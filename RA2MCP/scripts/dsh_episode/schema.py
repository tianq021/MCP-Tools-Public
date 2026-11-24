"""Episode schema v2 for the RA2 learner: raw observations, raw actions, outcomes.

Design rules (see reports/dsh-episode-schema.md):
  * JSONL, UTF-8 without BOM, one record per line, `\\n` newlines
  * line 1 = meta, last line = result, middle lines = observations/actions/events
  * an observation keeps the *raw* state pieces (full own objects, visible enemies,
    factories, economy) so downstream feature versions can be re-derived without
    re-running the game
  * hidden enemies are never stored per-object (only an aggregate count), so no
    replay can leak map-hack information into training
  * actions keep their full parameters (target object id, coordinates, production
    type, placement cell), not just a class label
"""
import json
import os
import pathlib
import threading
import time
from dsh_episode.hostility import is_opponent

SCHEMA_VERSION = "episode-v2"
# "stalled"/"partial" are explicit non-normal endings: an incomplete episode must be
# labelled and excluded from learning instead of masquerading as a clean finish
# (RUN02 run-protection requirement; see reports/g0l1-run-protection-2026-11-02/).
RESULT_KINDS = ("running", "defeat", "win", "timeout", "crash", "game_end",
                "interrupted", "manual", "stalled", "partial")

# required fields for each raw action type (checked by validate_action)
ACTION_REQUIRED = {
    "DEPLOY": ("object_addresses",),
    "MOVE": ("object_addresses", "coordinates"),
    "ATTACK": ("object_addresses", "target_address"),
    "STOP": ("object_addresses",),
    "PRODUCE": ("production_type", "production_action"),
    "PLACE": ("building_address", "coordinates"),
}


class SchemaError(Exception):
    pass


def now_wall():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def make_meta(episode_id, policy_name, policy_params, env, model=None):
    return {
        "schema_version": SCHEMA_VERSION,
        "episode_id": episode_id,
        "started_at": now_wall(),
        "wall_timezone": time.tzname[0] if time.tzname else "local",
        "policy": {"name": policy_name, "params": policy_params},
        "model": model,
        "environment": env,
        "features": {"observation": "raw-v2", "action": "raw-v2"},
        "notes": [],
    }


def make_observation(frame, wall, stage, money, houses, own_objects,
                     visible_enemies, factories, hidden_enemy_count,
                     map_summary, game_frame_present=True):
    return {
        "type": "observation",
        "frame": int(frame),
        "wall": wall,
        "stage": stage,
        "money": int(money),
        "houses": houses,
        "own_objects": own_objects,
        "visible_enemies": visible_enemies,
        "factories": factories,
        "hidden_enemy_count": int(hidden_enemy_count),
        "map": map_summary,
        "game_frame_present": bool(game_frame_present),
    }


def make_action(action, object_addresses, ack_ms=None, error=None,
                coordinates=None, target_address=None, target_type=None,
                production_type=None, production_action=None,
                building_address=None, extra=None):
    rec = {
        "type": "action",
        "action": action,
        "object_addresses": [int(a) for a in object_addresses],
        "ack_ms": None if ack_ms is None else float(ack_ms),
        "error": error,
    }
    if coordinates is not None:
        rec["coordinates"] = [int(coordinates[0]), int(coordinates[1])]
    if target_address is not None:
        rec["target_address"] = int(target_address)
    if target_type is not None:
        rec["target_type"] = target_type
    if production_type is not None:
        rec["production_type"] = production_type
    if production_action is not None:
        rec["production_action"] = production_action
    if building_address is not None:
        rec["building_address"] = int(building_address)
    if extra:
        rec["extra"] = extra
    return rec


def make_event(kind, **data):
    return {"type": "event", "event": kind, **data}


def validate_action(rec):
    errs = []
    name = rec.get("action")
    if name not in ACTION_REQUIRED:
        errs.append(f"unknown action {name!r}")
        return errs
    for key in ACTION_REQUIRED[name]:
        if rec.get(key) in (None, []):
            errs.append(f"{name} missing {key}")
    if name == "MOVE" and rec.get("coordinates") is not None:
        c = rec["coordinates"]
        if not (isinstance(c, list) and len(c) == 2 and all(isinstance(v, int) for v in c)):
            errs.append("MOVE coordinates must be [int, int]")
    return errs


def validate_observation(rec):
    errs = []
    for key in ("frame", "wall", "stage", "money", "own_objects",
                "visible_enemies", "factories", "hidden_enemy_count", "map"):
        if key not in rec:
            errs.append(f"observation missing {key}")
    for i, obj in enumerate(rec.get("own_objects") or []):
        for key in ("addr", "type_name", "abstract", "health", "cell"):
            if key not in obj:
                errs.append(f"own_objects[{i}] missing {key}")
                break
    for i, obj in enumerate(rec.get("visible_enemies") or []):
        for key in ("addr", "type_name", "owner", "health", "cell",
                    "cell_shrouded", "cloak_state"):
            if key not in obj:
                errs.append(f"visible_enemies[{i}] missing {key}")
                break
        if obj.get("cell_shrouded") is not False:
            errs.append(f"visible_enemies[{i}] is not visible (cell_shrouded != False)")
    return errs


def classify_outcome(stage_name=None, human_defeated=False, human_is_loser=False,
                     human_is_winner=False, enemies_all_defeated=False,
                     human_is_game_over=False,
                     proc_exited=False, exit_code=None, elapsed_s=0.0,
                     max_seconds=None, interrupted=False, manual=False):
    """Return {'kind', 'detail', 'is_win', 'is_crash'}.

    Rules (a crash is NEVER reported as win):
      1. interrupted -> interrupted
      2. manual -> manual
      3. human defeated / loser -> defeat (even if the process then exited)
      4. process exited:
           - clean exit (code 0/None) while stage is EXIT_GAME/NONE:
               * winner flag / all enemies defeated -> win
               * otherwise -> game_end
           - a win observed at the time budget -> win (checked first)
           - exit at the configured time budget -> timeout (own teardown)
           - anything else (nonzero code, no clean stage) -> crash
      5. human_is_winner / all enemies defeated while still ingame -> win
         (checked before timeout so a victory in the final budget second wins)
      6. elapsed >= max_seconds - 1 -> timeout
      7. game-over flag without a confirmed winner -> game_end (unknown result)
      8. stage != INGAME -> game_end
      9. elapsed > max_seconds -> timeout
     10. else -> running
    """
    out = {"kind": "running", "detail": "", "is_win": False, "is_crash": False}
    if interrupted:
        out.update(kind="interrupted", detail="runner interrupted")
        return out
    if manual:
        out.update(kind="manual", detail="stopped by user")
        return out
    if human_defeated or human_is_loser:
        out.update(kind="defeat", detail="human player defeated")
        return out

    def _budget_reached():
        return max_seconds is not None and elapsed_s >= max_seconds - 1.0

    if proc_exited:
        clean_exit = exit_code in (0, None)
        # only an explicit EXIT_GAME/NONE stage counts as a clean ending; an
        # unknown stage after process exit is treated as a crash (never as win)
        clean_stage = stage_name in ("STAGE_EXIT_GAME", "STAGE_NONE")
        if clean_exit and clean_stage and (human_is_winner or enemies_all_defeated):
            out.update(kind="win", detail="clean exit after victory", is_win=True)
            return out
        if not clean_exit:
            out.update(kind="crash",
                       detail=f"game process exited (code={exit_code}, stage={stage_name}) "
                              "without a clean result",
                       is_crash=True)
            return out
        # Reaching the configured episode time budget ends the episode and the
        # runner then shuts the game down; a process exit at that point is our
        # own teardown, not a crash (regression: 240s combat episodes were all
        # classified as crashes because the ExitGameLoop exit arrived first).
        if _budget_reached():
            out.update(kind="timeout",
                       detail=f"elapsed {elapsed_s:.1f}s >= {max_seconds:.1f}s budget")
            return out
        if clean_exit and clean_stage:
            out.update(kind="game_end",
                       detail=f"clean exit without result (stage={stage_name})")
            return out
        out.update(kind="crash",
                   detail=f"game process exited (code={exit_code}, stage={stage_name}) "
                          "without a clean result",
                   is_crash=True)
        return out
    # Victory takes precedence over the time budget: a win inside the final
    # second of the episode must never be reported as a timeout.
    if enemies_all_defeated or human_is_winner:
        out.update(kind="win", detail="victory condition met", is_win=True)
        return out
    if _budget_reached():
        out.update(kind="timeout",
                   detail=f"elapsed {elapsed_s:.1f}s >= {max_seconds:.1f}s budget")
        return out
    if human_is_game_over:
        out.update(kind="game_end", detail="game over without a confirmed winner")
        return out
    if stage_name is not None and stage_name != "STAGE_INGAME":
        out.update(kind="game_end", detail=f"stage={stage_name}")
        return out
    if max_seconds is not None and elapsed_s > max_seconds:
        out.update(kind="timeout", detail=f"elapsed {elapsed_s:.1f}s > {max_seconds:.1f}s")
        return out
    return out


def derive_metrics(rows):
    """Aggregate cheap metrics from recorded observation/event rows."""
    damage_dealt = 0
    neutral_hp_lost = 0
    own_hp_lost = 0
    buildings_peak = 0
    units_peak = 0
    first_damage_at = None
    last_hp = {}
    for row in rows:
        if row.get("type") == "observation":
            own = row.get("own_objects") or []
            b = sum(1 for o in own if o.get("abstract") == "ABSTRACT_TYPE_BUILDING")
            u = sum(1 for o in own if o.get("abstract") in
                    ("ABSTRACT_TYPE_UNIT", "ABSTRACT_TYPE_INFANTRY",
                     "ABSTRACT_TYPE_AIRCRAFT"))
            buildings_peak = max(buildings_peak, b)
            units_peak = max(units_peak, u)
            hp_now = {}
            for o in own:
                hp_now[o["addr"]] = (o["health"], str(o.get("type_name")), "own")
            for o in row.get("visible_enemies") or []:
                side = "enemy" if is_opponent(o) else "neutral"
                hp_now[o["addr"]] = (o["health"], str(o.get("type_name")), side)
            for addr, (hp, type_name, side) in hp_now.items():
                if addr in last_hp:
                    prev, prev_type, prev_side = last_hp[addr]
                    same_identity = (type_name == prev_type and side == prev_side)
                    if same_identity and hp < prev:
                        delta = prev - hp
                        if side == "own":
                            own_hp_lost += delta
                        elif side == "enemy":
                            damage_dealt += delta
                            if first_damage_at is None:
                                first_damage_at = row.get("t", row.get("frame"))
                        else:
                            neutral_hp_lost += delta
            # drop addresses that disappeared this tick: native addresses are
            # reused by newly created objects (e.g. MCV 1000hp -> CY 750hp), and
            # an identity change must never be counted as HP loss
            # (regression: ep-0004/0006 reported own_hp_lost=250 instead of 0)
            last_hp = dict(hp_now)
    events = {}
    for row in rows:
        for ev in row.get("events") or []:
            events[ev.get("event")] = events.get(ev.get("event"), 0) + 1
    return {"damage_dealt": damage_dealt, "neutral_hp_lost": neutral_hp_lost,
            "metrics_version": "hostile-v1", "own_hp_lost": own_hp_lost,
            "buildings_peak": buildings_peak, "units_peak": units_peak,
            "first_damage_at_s": first_damage_at, "event_counts": events}


class EpisodeWriter:
    """Append-only JSONL writer with checkpoint + resume + atomic, idempotent finalize.

    RUN02 run-protection hardening:
    * finalize is guarded by a re-entrant lock and is idempotent: a repeat call (normal
      shutdown after a stall handler already finalized) returns the finished path
      instead of raising/overwriting;
    * every published file gets a ``<id>.jsonl.partial-manifest.json`` sidecar with its
      SHA-256 and an explicit ``COMPLETE``/``STALLED`` status so an incomplete episode
      can never be mistaken for a normal completion;
    * ``finalize_stalled`` records the stall evidence and marks ``learning_excluded``.
    """

    def __init__(self, episodes_dir, episode_id, meta, resume=False,
                 flush_every=1, checkpoint_every=10):
        self.dir = pathlib.Path(episodes_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.episode_id = episode_id
        self.part_path = self.dir / f"{episode_id}.jsonl.part"
        self.final_path = self.dir / f"{episode_id}.jsonl"
        self.ckpt_path = self.dir / f"{episode_id}.ckpt.json"
        self.manifest_path = self.dir / f"{episode_id}.jsonl.partial-manifest.json"
        self.flush_every = flush_every
        self.checkpoint_every = checkpoint_every
        self.row_count = 0
        self.meta = meta
        self.rows = []          # kept in memory for metrics derivation
        self._since_flush = 0
        self._lock = threading.RLock()
        self.finalized = False
        self.finalize_count = 0
        self.finalize_history = []
        self.finalize_error = None
        if self.final_path.exists():
            raise SchemaError(f"episode already finalized: {self.final_path}")
        if self.part_path.exists() and not resume:
            raise SchemaError(f"unfinished episode exists; archive or resume: {self.part_path}")
        if resume and not (self.part_path.exists() and self.ckpt_path.exists()):
            raise SchemaError("cannot resume: recording or checkpoint missing")
        if resume and self.part_path.exists() and self.ckpt_path.exists():
            ckpt = json.loads(self.ckpt_path.read_text(encoding="utf-8"))
            if ckpt.get("schema_version") != SCHEMA_VERSION:
                raise SchemaError("cannot resume: schema version changed")
            self.meta = ckpt.get("meta", meta)
            existing = read_episode(self.part_path)
            if existing["result"] is not None:
                raise SchemaError("cannot resume: recording already contains a result")
            self.rows = existing["rows"]
            self.row_count = len(self.rows)
            self._fh = self.part_path.open("a", encoding="utf-8", newline="\n")
            self._resumed = True
        else:
            self._fh = self.part_path.open("w", encoding="utf-8", newline="\n")
            self._write_line({"type": "meta", "meta": meta})
            self._resumed = False
            self._save_ckpt()

    # ---- internals ----
    def _write_line(self, rec):
        line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
        self._fh.write(line + "\n")
        self._since_flush += 1
        if self._since_flush >= self.flush_every:
            self._fh.flush()
            self._since_flush = 0

    def _save_ckpt(self):
        self.ckpt_path.write_text(json.dumps({
            "schema_version": SCHEMA_VERSION,
            "episode_id": self.episode_id,
            "row_count": self.row_count,
            "meta": self.meta,
            "updated_at": now_wall(),
        }, ensure_ascii=False), encoding="utf-8")

    # ---- public API ----
    def add_row(self, rec):
        with self._lock:
            if rec.get("type") not in ("observation", "action", "event", "note"):
                raise SchemaError(f"bad row type {rec.get('type')!r}")
            if rec.get("type") == "action":
                errs = validate_action(rec)
                if errs:
                    raise SchemaError(f"invalid action record: {errs}")
            if rec.get("type") == "observation":
                errs = validate_observation(rec)
                if errs:
                    raise SchemaError(f"invalid observation: {errs}")
            self._write_line(rec)
            self.row_count += 1
            self.rows.append(rec)
            if self.row_count % self.checkpoint_every == 0:
                self._fh.flush()
                self._save_ckpt()
            return self.row_count

    def add_rows(self, recs):
        for rec in recs:
            self.add_row(rec)

    def finalize(self, result):
        """Atomic idempotent finalize; repeats return the existing path unchanged."""
        with self._lock:
            self.finalize_count += 1
            if self.finalized:
                self.finalize_history.append({"event": "duplicate_finalize_ignored",
                                              "kind": result.get("kind"),
                                              "at": now_wall()})
                return self.final_path
            if result.get("kind") not in RESULT_KINDS:
                raise SchemaError(f"bad result kind {result.get('kind')!r}")
            incomplete = result.get("kind") in ("stalled", "interrupted", "crash",
                                                "partial")
            rec = {"type": "result", "result": result,
                   "rows": self.row_count, "finished_at": now_wall()}
            self._write_line(rec)
            self._fh.flush()
            try:
                os.fsync(self._fh.fileno())
            except OSError:
                pass
            try:
                self._fh.close()
            except OSError:
                pass
            if self.final_path.exists():
                raise SchemaError(f"refusing to overwrite {self.final_path}")
            os.replace(self.part_path, self.final_path)
            self.ckpt_path.write_text(json.dumps({
                "schema_version": SCHEMA_VERSION, "episode_id": self.episode_id,
                "row_count": self.row_count, "done": True,
                "result_kind": result.get("kind"), "updated_at": now_wall(),
            }, ensure_ascii=False), encoding="utf-8")
            # SHA-256 + explicit status sidecar (partial finalize / stall evidence)
            try:
                import hashlib
                digest = hashlib.sha256(self.final_path.read_bytes()).hexdigest()
                manifest = {"schema": "partial-finalize-v1/1",
                            "episode_id": self.episode_id,
                            "status": "STALLED" if result.get("kind") == "stalled"
                            else ("PARTIAL" if incomplete else "COMPLETE"),
                            "complete": not incomplete,
                            "learning_excluded": incomplete,
                            "result_kind": result.get("kind"),
                            "rows": self.row_count,
                            "final_sha256": digest,
                            "final_size": self.final_path.stat().st_size,
                            "finished_at": now_wall(),
                            "stalled_evidence": result.get("stalled_evidence"),
                            "finalize_count": self.finalize_count}
                self.manifest_path.write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=1),
                    encoding="utf-8")
            except OSError:
                pass
            self.finalized = True
            return self.final_path

    def finalize_stalled(self, evidence=None, *, detail=None):
        """Finalize an incomplete episode explicitly as STALLED (never a normal end)."""
        result = {"episode_id": self.episode_id, "schema_version": SCHEMA_VERSION,
                  "kind": "stalled",
                  "detail": detail or "run-protection stalled partial finalize",
                  "is_win": False, "is_crash": False,
                  "learning_excluded": True, "complete": False,
                  "outcome_confirmed": False, "rows": self.row_count,
                  "stalled_evidence": evidence}
        return self.finalize(result)

    def abort(self):
        """Flush and close without finalizing (keeps .part for --resume)."""
        with self._lock:
            try:
                self._fh.flush()
            except Exception:  # noqa: BLE001
                pass
            try:
                self._fh.close()
            except Exception:  # noqa: BLE001
                pass
            self._save_ckpt()


def read_episode(path):
    """Load an episode file (works for both finalized .jsonl and .part)."""
    path = pathlib.Path(path)
    meta, result, rows = None, None, []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            t = rec.get("type")
            if t == "meta":
                meta = rec["meta"]
            elif t == "result":
                result = rec["result"]
            else:
                rows.append(rec)
    return {"path": str(path), "meta": meta, "rows": rows, "result": result}


def validate_episode_file(path, require_final=True):
    """Return list of validation errors (empty = valid)."""
    errors = []
    try:
        ep = read_episode(path)
    except Exception as e:  # noqa: BLE001
        return [f"unreadable: {e}"]
    if not ep["meta"]:
        errors.append("missing meta line")
    if require_final and not ep["result"]:
        errors.append("missing result line (episode not finalized)")
    if ep["meta"] and ep["meta"].get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version mismatch: {ep['meta'].get('schema_version')}")
    for i, rec in enumerate(ep["rows"]):
        if rec.get("type") == "observation":
            errors.extend(f"row {i}: {e}" for e in validate_observation(rec))
        elif rec.get("type") == "action":
            errors.extend(f"row {i}: {e}" for e in validate_action(rec))
    if ep["result"] and ep["result"].get("kind") not in RESULT_KINDS:
        errors.append(f"bad result kind {ep['result'].get('kind')}")
    return errors
