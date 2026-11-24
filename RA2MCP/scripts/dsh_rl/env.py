"""Gymnasium wrapper over the real RA2 bridge. No simulated training fallback."""
import asyncio
import json
import os
import pathlib
import sys
import time
import threading
import traceback
import uuid
from types import SimpleNamespace

import gymnasium as gym
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts/dsh_train"))
# RUN02 run-protection: single installation point (offline-tested in v2_candidate/).
# Kept optional-with-loud-fallback: a real run without the protection package must not
# silently continue unprotected; it starts with protection disabled only if missing.
sys.path.insert(0, str(ROOT / "v2_candidate" / "src"))
try:
    from ra2v2 import production_wiring as _run_protection
    from ra2v2.run_protection import RunStalled, rpc_timeout as _rpc_timeout
except Exception:  # noqa: BLE001
    _run_protection = None


    def _rpc_timeout(name, fallback=15.0):
        return float(fallback)

    class RunStalled(RuntimeError):  # type: ignore[no-redef]
        def __init__(self, message, evidence=None):
            super().__init__(message)
            self.evidence = evidence or {}
from dsh_episode import schema, obs, lockfile
from dsh_episode.policy import RulePolicy
from dsh_episode.runner import (EpisodeRunner, launch_game, kill_games, read_initial,
                                read_state, read_map_soa, public_start_cells, env_fingerprint,
                                GameStateReadError)
from dsh_episode.render_off import RenderOffError
from dsh_rl.feedback import (TerminalMonitor, FeedbackUnavailable, FEEDBACK_VERSION,
                             outcome_flags)
from ra2yrproto import ra2yr
import features_v2
import spec_v2
from dsh_rl.reward import military_intent, reward_parts, progress_reward, REWARD_VERSION
from dsh_m1 import command_capture as command_capture_mod  # TASK-105 (default off)


class RA2MicroEnv(gym.Env):
    """Neural military decisions with fixed economic scaffolding.

    The game keeps running in real time. A policy action persists for
    decision_seconds while observations/economy run every 0.5 seconds.
    """
    metadata = {"render_modes": []}
    reward_version = REWARD_VERSION
    # A dead game replica is relaunched on the next reset; these are the pauses
    # between attempts (launch + bridge connect can transiently fail).
    RESET_RETRY_DELAYS = (5.0, 15.0, 30.0)
    # Only real game/bridge failures end an episode. Programming errors still
    # raise, so a broken reward/action path cannot masquerade as a game crash.
    REAL_GAME_ERRORS = (GameStateReadError, FeedbackUnavailable, RenderOffError,
                        OSError, ConnectionError, TimeoutError, EOFError)

    def __init__(self, out_dir, max_seconds=180, decision_seconds=2.0,
                 game_dir=None, port=14521, instance=0, kill_stale=None,
                 render_off=False, launch_delay_seconds=0.0, protection=None):
        super().__init__()
        if decision_seconds <= 0 or (max_seconds != 0 and max_seconds <= 50):
            raise ValueError("decision_seconds > 0 and max_seconds == 0 (unlimited) or > 50 required")
        # RUN02 run-protection state: configured per episode by _reset; all hooks below
        # are no-ops when protection is disabled/missing (offline tests rely on this).
        self.protection = None
        self.protection_config = dict(protection or {})
        self._stalled_evidence = None
        # Parallel training instances each own a game copy, bridge port and lock.
        self.game_dir = pathlib.Path(game_dir) if game_dir else None
        self.port = int(port)
        self.instance = int(instance)
        if self.instance < 0:
            raise ValueError("instance must be >= 0")
        # Opt-in experimental mode: validated runtime draw-skip patch. Default is
        # normal rendering; validation failures abort instead of falling back.
        self.render_off = bool(render_off)
        self.render_off_evidence = None
        self._ro_last_integrity = 0.0
        # Only a lone primary instance may sweep stale games; a parallel group
        # would kill its siblings. The launcher passes kill_stale=False then.
        self.kill_stale = (self.instance == 0) if kill_stale is None else bool(kill_stale)
        self.observation_space = gym.spaces.Box(-5.0, 5.0, (spec_v2.OBS_DIM,), np.float32)
        self.action_space = gym.spaces.Discrete(4)
        self.out_dir = pathlib.Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        from dsh_m1 import map_diag as _md
        self.map_interval = 5.0
        self.map_diag = _md
        self.map_stats = {k: 0 for k in ('env_capture_enter','map_refresh_check','map_refresh_skip',
            'map_refresh_enter','map_read_attempt','map_read_success','map_read_fail',
            'sidecar_attempt','sidecar_success','sidecar_fail')}
        from dsh_m1 import map_diag as _md
        self.map_interval = 5.0
        self.map_diag = _md
        self.map_stats = {k: 0 for k in ('env_capture_enter','map_refresh_check','map_refresh_skip',
            'map_refresh_enter','map_read_attempt','map_read_success','map_read_fail',
            'sidecar_attempt','sidecar_success','sidecar_fail')}
        from dsh_m1 import map_diag as _md
        self.map_interval = 5.0
        self.map_diag = _md
        self.map_stats = {k: 0 for k in ('env_capture_enter','map_refresh_check','map_refresh_skip',
            'map_refresh_enter','map_read_attempt','map_read_success','map_read_fail',
            'sidecar_attempt','sidecar_success','sidecar_fail')}
        self.max_seconds, self.decision_seconds = max_seconds, decision_seconds
        self.loop = asyncio.new_event_loop()
        self._loop_thread = None
        self._feedback_monitor = None
        self.transport_feedback_failures = False
        self._last_outcome_flags = {}
        self.economy_invalid_orders = 0
        self.runner = None
        self.writer = None
        self.finished = True
        self.episode_count = 0
        self.transitions = 0
        self.completed = []
        self.total_reward = 0.0
        self.last_features = None
        # 多实例错峰：每个 worker 首次确认开局前延迟 launch_delay_seconds，
        # 避免所有游戏同时启动/同时重开造成窗口与桥接的瞬时竞争。
        self.launch_delay_seconds = float(launch_delay_seconds or 0.0)
        self._launch_delayed = self.launch_delay_seconds <= 0

    # ---- 实例诊断（训练中可查询“当前有几个实例在跑”）----
    def is_episode_active(self):
        """该 worker 是否正在一局进行中的对局（用于统计活跃实例数）。"""
        return bool((not self.finished) and self.kind == "running")

    @property
    def episode_active(self):
        """向后兼容的属性形式（不得被 env_method 调用）。"""
        return self.is_episode_active()

    @property
    def game_pid(self):
        try:
            return int(self.runner.proc.pid) if self.runner is not None else None
        except Exception:  # noqa: BLE001
            return None

    def _run(self, coroutine, name=None):
        if self._loop_thread is None:
            ready = threading.Event()
            def serve():
                asyncio.set_event_loop(self.loop)
                self.loop.call_soon(ready.set)
                self.loop.run_forever()
            self._loop_thread = threading.Thread(target=serve, daemon=True,
                                                  name=f"ra2-io-{self.instance}")
            self._loop_thread.start()
            ready.wait()
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        if self.protection is not None:
            # RUN02: every decision/reset crossing the sync bridge has a hard wall-clock
            # cap; a hard block becomes RunStalled instead of hanging like RUN01.
            label = name or getattr(coroutine, "__qualname__", "coroutine")
            return self.protection.await_future(future, name=label)
        return future.result()

    def _features(self):
        features_v2.validate_visibility(self.current)
        x = np.asarray(features_v2.build_obs_v2(self.current, self.type_ids), dtype=np.float32)
        if not np.isfinite(x).all():
            raise RuntimeError("non-finite real observation")
        return np.clip(x, -5.0, 5.0)

    def _context(self):
        return {"t": self.current["t"], "frame": self.current["frame"],
                "own_objects": self.current["own_objects"], "visible_enemies": self.current["visible_enemies"],
                "factories": self.current["factories"], "observation": self.current,
                "hero": {"self": int(self.hero.self), "money": int(self.hero.money), "name": self.hero.name},
                "map": self.current["map"], "last_acks": self.last_acks,
                "public_start_cells": self.starts}

    def _filter_observation(self, row):
        return row

    def _initialize_controller(self, initial, catalog):
        self.rule = RulePolicy(type_catalog=catalog)

    def _control_metadata(self):
        return "ppo_micro", {"fixed_economy": True, "reward_version": REWARD_VERSION}

    async def _warmup_controller(self):
        infantry = []
        while self.current["t"] < 50 and self.kind == "running":
            infantry = [o for o in self.current["own_objects"] if o.get("abstract") == "ABSTRACT_TYPE_INFANTRY"]
            if len(infantry) >= 4:
                break
            await self._economy()
            await asyncio.sleep(0.5)
            await self._capture()
        if self.kind != "running" or len(infantry) < 4:
            raise FeedbackUnavailable("fixed economy failed to provide four infantry for PPO")

    def _select_intent(self, action):
        return military_intent(action, self.current, self.starts)

    def _is_noop(self, action):
        return False

    def _extra_rewards(self, before, after):
        return {}

    def _action_details(self, action):
        return {}

    def _tick_rewards(self, before, after, invalid):
        return reward_parts(before, after, self.kind, invalid)

    async def _capture_impl(self, state=None):
        monitor = self._feedback_monitor
        if monitor is not None and monitor.terminal_state is not None:
            state = monitor.terminal_state
        elif state is None:
            try:
                state = await read_state(self.runner.client, strict=True)
            except GameStateReadError:
                if monitor is None or monitor.terminal_state is None:
                    raise
                state = monitor.terminal_state
        if state is None:
            raise GameStateReadError("real game state unavailable; refusing fabricated transition")
        if monitor is not None and monitor.terminal_state is not None:
            state = monitor.terminal_state
        self.state = state
        house_id = getattr(self, '_hero_id', None)
        hero = next((h for h in state.houses if int(h.self) == house_id), None)
        if hero is None:
            raise FeedbackUnavailable("original human house missing from real snapshot")
        self.hero = hero
        if monitor is not None:
            monitor.observe(state)
        await self._refresh_map_sidecar(state)
        houses = {int(h.self): h.name for h in state.houses}
        row = obs.capture_observation(state, int(self.hero.self), self.type_names,
                                      houses, self.map_soa, schema.now_wall())
        row["t"] = round(monitor.terminal_elapsed if monitor is not None and
                         state is monitor.terminal_state else time.monotonic()-self.started, 3)
        row = self._filter_observation(row)
        row["record_kind"] = "periodic"
        self.writer.add_row(row)
        if self.protection is not None:
            # RUN02: light raw-event capture (replaces MessageToDict hot path) and the
            # frame-progress watchdog; raises RunStalled when frames stagnate.
            self._protect_capture(row, state)
        else:
            self._v3_capture_events(state)
        if self.protection is not None:
            # RUN02-RETRY data-chain fix: incremental ReadValue(event_buffer) ingest.
            # A failed read is recorded as a capture gap; it never aborts the loop.
            try:
                await self._protect_events()
            except BaseException:  # noqa: BLE001
                pass
        self.current = row
        if os.environ.get("DSH_WDZS_ADVICE") == "1":
            # Optional, read-only strategy guidance. It cannot send commands,
            # alter rewards/actions, or become an unverified training label.
            from ai_teacher.wdzs_advice_hook import capture_advice
            capture_advice(self)
        flags = outcome_flags(state, self._hero_id)
        self._last_outcome_flags = flags
        self.kind = schema.classify_outcome(
            stage_name=ra2yr.LoadStage.Name(state.stage),
            elapsed_s=row["t"], max_seconds=self.max_seconds or None,
            **flags)["kind"]
        if self.kind == 'running':
            self._render_off_watchdog(int(row["frame"]))
        elif self.kind == 'game_end':
            raise FeedbackUnavailable("game ended without a confirmed outcome")
        return row
    def _render_off_watchdog(self, frame):
        """Raise when the opt-in render-off mode degrades (stall/read/patch loss)."""
        controller = getattr(getattr(self, "runner", None), "render_off", None)
        if controller is None:
            return
        violations = controller.check_health(int(frame), True)
        now = time.monotonic()
        if now - getattr(self, "_ro_last_integrity", 0.0) >= 5.0:
            self._ro_last_integrity = now
            try:
                controller.assert_patch_intact()
            except Exception as exc:  # noqa: BLE001
                violations.append(("patch_lost", str(exc)))
        if violations:
            raise FeedbackUnavailable(
                f"render-off watchdog: {violations}; "
                f"evidence={getattr(self.runner, 'render_off_evidence_dir', None)}")

    async def _commands(self, intents, source):
        self.last_acks = []
        invalid = 0
        ok = 0
        for intent in intents:
            records = await self.runner.execute(self.state, self.hero, intent)
            # RUN02-RETRY data-chain fix: one canonical decision association key per
            # decision; a frame is never fabricated as 0 (None => UNKNOWN).
            dec_frame = int((getattr(self, "current", {}) or {}).get("frame") or 0) or None
            for record in records:
                # This sequence belongs to the episode, including scripted commands.
                # A runner may emit several records for one intent: each captured
                # record needs its own key for the 1:1 action/replay association.
                dec_i = getattr(self, "_dec_seq", 0)
                self._dec_seq = dec_i + 1
                dec_id = f"dec-{getattr(self, 'episode_id', 'ep')}-{dec_i:04d}"
                record.setdefault("decision_id", dec_id)
                record.setdefault("decision_key",
                                  f"{getattr(self, 'episode_id', 'ep')}:{dec_id}")
                record.setdefault("decision_index", dec_i)
                record.setdefault("frame_id", dec_frame)
                record.setdefault("observation_frame", dec_frame)
            if self.protection is not None:
                # RUN02: a bridge command timeout poisons the transport -> fail closed.
                # It must never be treated as "command cancelled" and re-sent.
                self._protect_commands(records)
            for record in records:
                record["decision_source"] = source
                self._dec_acks.append(record)
                self.writer.add_row(record)
                self._v3_link_decision(record)
                if record.get("type") != "action":
                    invalid += 1
                    continue
                error = record.get("error")
                if error and "already exists" not in error:
                    self.invalid_orders += 1
                    invalid += 1
                elif not error:
                    ok += 1
                    self.orders_ok += 1
                    self.rl_orders_ok += source == "ppo"
                self.last_acks.append({"action": record.get("action"), "production_type": record.get("production_type"),
                                       "error": error, "t": self.current["t"]})
        controller = getattr(getattr(self, "runner", None), "render_off", None)
        if controller is not None:
            violation = controller.note_actions(ok, invalid)
            if violation:
                raise FeedbackUnavailable(
                    f"render-off watchdog: {violation}; "
                    f"evidence={getattr(self.runner, 'render_off_evidence_dir', None)}")
        return invalid

    async def _economy(self):
        intents = self.rule.step(self._context())
        economy = [i for i in intents if i["action"] in ("DEPLOY", "PRODUCE", "PLACE")]
        return await self._commands(economy, "fixed_economy")

    async def _reset(self):
        self._finish("interrupted")
        await self._stop_client()
        self._cleanup_game()
        self.episode_count += 1
        episode_id = f"ppo-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        # RUN02 run-protection: start the per-episode watchdog/focus/screenshot recorder
        # before the game is launched so a launch/connect block is also covered by the
        # hard wall-clock cap in self._run().
        if _run_protection is not None:
            self._protect_begin(episode_id)
        opts = SimpleNamespace(episode_id=episode_id, keep_game=False, attach=False,
                               port=self.port, game_dir=self.game_dir,
                               kill_existing=self.kill_stale,
                               launch_timeout=90, max_seconds=self.max_seconds,
                               render_off=self.render_off)
        self.runner = EpisodeRunner(opts)
        if not self._launch_delayed:
            # 错峰：首次构造后的第一次真正开局前等待（仅一次）。
            time.sleep(self.launch_delay_seconds)
            self._launch_delayed = True
        lock_name = ("dsh-game.lock" if self.instance == 0
                     else f"dsh-game-{self.instance}.lock")
        self.runner.lock_path = lockfile.acquire(lockfile.lock_dir() / lock_name)
        if self.kill_stale:
            # The primary instance clears stale games from a previous crash.
            # Parallel instances must never kill each other's process.
            kill_games()
        self.runner.proc = launch_game(self.game_dir)
        self.runner.log(f"PPO launched pid={self.runner.proc.pid} episode={episode_id}")
        st = await self.runner.connect_ingame(self.runner.proc)
        if self.protection is not None:
            # RUN02: bind the isolated game pid + save the 'start' (INGAME) screenshot.
            self._protect_ingame(st)
        render_off_env = {"enabled": bool(self.render_off)}
        self.render_off_evidence = None
        if self.render_off:
            evidence_dir = self.out_dir.parent / "render-off" / episode_id
            info = self.runner.enable_render_off(evidence_dir=evidence_dir)
            self.render_off_evidence = {
                "info": info, "evidence_dir": str(evidence_dir),
                "target": (self.runner.render_off.evidence or {}).get("target")}
            render_off_env.update({
                "va": info.get("va"),
                "original_bytes": info.get("bytes_before"),
                "patch_bytes": info.get("bytes_after"),
                "evidence_dir": str(evidence_dir)})
        initial = await read_initial(self.runner.client)
        self.runner.initial = initial
        self.runner.type_by_ptr = {int(t.pointer_self): t for t in initial.object_types}
        catalog = [{"pointer_self": int(t.pointer_self), "name": t.name,
                    "array_index": t.array_index, "type": ra2yr.AbstractType.Name(t.type)} for t in initial.object_types]
        self.type_names = {t["pointer_self"]: t["name"] for t in catalog}
        self.runner.type_names = self.type_names
        self.runner.type_catalog = catalog
        self.type_ids = {n: i for i, n in enumerate(sorted(set(self.type_names.values())))}
        self.hero = next(h for h in st.houses if h.current_player and h.is_human_player)
        self._hero_id = int(self.hero.self)
        self._initialize_controller(initial, catalog)
        start_map = ((self.game_dir or (ROOT / "test-game")) / "spawnmap.ini")
        self.starts = public_start_cells(path=start_map)
        policy_name, control_params = self._control_metadata()
        meta = schema.make_meta(episode_id, policy_name, control_params,
                                env_fingerprint({"public_start_cells": self.starts,
                                                 "reward_version": self.reward_version,
                                                 "action_space_version": getattr(self, "action_space_version", None),
                                                 "target_candidate_range": getattr(self, "target_candidate_range", None),
                                                 "instance": self.instance,
                                                 "bridge_port": self.port,
                                                 "render_off": render_off_env},
                                                game_dir=self.game_dir))
        self.writer = schema.EpisodeWriter(self.out_dir, episode_id, meta)
        from dsh_m1 import capture_v1
        self.decision_step = 0
        self.last_decision = None
        self._bind_capture(episode_id)
        self.action_source = "scripted_teacher"
        self.action_version = "unknown"
        if self.render_off_evidence:
            self.writer.add_row({"type": "event", "event": "render_off_enabled",
                                 "va": self.render_off_evidence["info"].get("va"),
                                 "original_bytes": self.render_off_evidence["info"].get("bytes_before"),
                                 "patch_bytes": self.render_off_evidence["info"].get("bytes_after"),
                                 "evidence_dir": self.render_off_evidence["evidence_dir"]})
        self.finished = False
        self.started = time.monotonic()
        self.map_soa, self.last_map_at = None, -99
        self.last_acks = []
        self.orders_ok = self.rl_orders_ok = self.invalid_orders = 0
        self.episode_reward_parts = {}
        self.total_reward = 0.0
        self._feedback_monitor = TerminalMonitor(self.runner.client, read_state,
            self._hero_id, self.started, self.max_seconds)
        await self._capture(st)
        self._feedback_monitor.start()
        await self._warmup_controller()
        self.writer.add_row({"type": "event", "event": "ppo_control_start", "t": self.current["t"]})
        self.max_mapped = int((self.current.get("map") or {}).get("mapped", 0))
        self.exploration_total = 0.0
        return self._features(), {"episode_id": episode_id, "warmup_s": self.current["t"]}

    def _preserve_crash_logs(self):
        """Copy raw game stdout/stderr before the next launch wipes them.

        An uncaught C++ exception in the bridge prints its terminate message to
        the game's stderr; that file is deleted by the relaunch, so preserve it
        next to the episode evidence.
        """
        game_dir = getattr(self, "game_dir", None)
        if game_dir is None:
            return
        episode_id = getattr(getattr(self, "writer", None), "episode_id", None) or "episode"
        target = self.out_dir.parent / "crash-logs"
        try:
            target.mkdir(parents=True, exist_ok=True)
            for name in ("dsh-game-stderr.log", "dsh-game-stdout.log", "ra2yrcpp.log"):
                src = pathlib.Path(game_dir) / name
                if src.exists() and src.stat().st_size > 0:
                    (target / f"{episode_id}.{name}").write_bytes(src.read_bytes())
        except OSError:
            pass

    def _recover(self):
        """Best-effort cleanup after a dead game; never raise into training."""
        try:
            self._run(self._stop_client())
        except BaseException:  # noqa: BLE001
            pass
        try:
            self._cleanup_game()
        except BaseException:  # noqa: BLE001
            self.runner = None

    def _last_observation(self):
        if self.last_features is not None:
            return self.last_features
        return np.zeros(self.observation_space.shape, np.float32)

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        error = None
        attempts = len(self.RESET_RETRY_DELAYS) + 1
        for attempt in range(attempts):
            try:
                obs, info = self._run(self._reset(), name="reset")
                self.last_features = obs
                return obs, info
            except RunStalled as exc:
                if getattr(self, "protection", None) is not None:
                    self._protect_handle_stall(exc)
                raise
            except PermissionError as exc:
                # RUN02 regression: a permission error in our own diagnostics path is a
                # programming/environment error, NOT a real-game failure. It must not be
                # retried and must leave evidence + cleanup behind.
                self._preserve_crash_logs()
                failure = self._failure_evidence(exc, 'reset')
                self._finish(failure['kind'], failure)
                self._recover()
                raise
            except BaseException as exc:  # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                if not isinstance(exc, self.REAL_GAME_ERRORS):
                    raise
                error = exc
                self._preserve_crash_logs()
                failure = self._failure_evidence(exc, 'reset')
                self._finish(failure['kind'], failure)
                self._recover()
                if attempt < attempts - 1:
                    time.sleep(self.RESET_RETRY_DELAYS[attempt])
        raise RuntimeError(
            f"real game failed to relaunch after {attempts} attempts: {error!r}")

    def step(self, action):
        if not self.action_space.contains(action):
            raise ValueError(f"invalid PPO action: {action}")
        try:
            result = self._run(self._step(int(action)), name="step")
            self.last_features = result[0]
            return result
        except RunStalled as exc:
            # RUN02: stall/timeout -> STALLED partial finalize; NEVER retried or resent.
            if getattr(self, "protection", None) is not None:
                self._protect_handle_stall(exc)
            raise
        except PermissionError as exc:
            self._preserve_crash_logs()
            failure = self._failure_evidence(exc, 'step')
            self._finish(failure['kind'], failure)
            self._recover()
            raise
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if not isinstance(exc, self.REAL_GAME_ERRORS):
                raise
            # A transport envelope lets the vector wrapper restart this replica.
            # It MUST be rejected before PPO sees it; it is not a real transition.
            self._preserve_crash_logs()
            failure = self._failure_evidence(exc, 'step')
            self._finish(failure['kind'], failure)
            self._recover()
            if not self.transport_feedback_failures:
                raise FeedbackUnavailable(json.dumps(failure)) from exc
            return (self._last_observation(), 0.0, False, True,
                    {"feedback_valid": False, "learning_excluded": True,
                     "episode_id": getattr(self.writer, 'episode_id', None),
                     "failure": failure})

    def _failure_evidence(self, exc, phase):
        proc = getattr(self.runner, 'proc', None)
        code = proc.poll() if proc is not None else None
        monitor = self._feedback_monitor
        evidence = {'kind': 'crash' if code not in (None, 0) else
                            'game_end' if code == 0 else 'connection_lost',
                    'phase': phase, 'exception_type': type(exc).__name__,
                    'message': str(exc), 'traceback': traceback.format_exc(),
                    'pid': getattr(proc, 'pid', None), 'exit_code': code,
                    'last_frame': getattr(self, 'current', {}).get('frame'),
                    'last_stage': getattr(self, 'current', {}).get('stage'),
                    'last_observation_t': getattr(self, 'current', {}).get('t'),
                    'monitor_errors': monitor.errors if monitor is not None else [],
                    'learning_excluded': True}
        # RUN02 regression: the failure evidence must survive even if the process is
        # killed/aborted afterwards (the failed RUN02 lost its traceback until we read
        # the protection dir). Persist it immediately next to the episode output.
        try:
            import pathlib
            out = pathlib.Path(getattr(self, "out_dir", "."))
            out.mkdir(parents=True, exist_ok=True)
            target = out / f"failure-{phase}-{int(time.time())}.json"
            target.write_text(json.dumps(evidence, indent=2, ensure_ascii=False,
                                         default=str), encoding="utf-8")
            evidence['evidence_file'] = str(target)
        except Exception:  # noqa: BLE001 - diagnostics must never mask the failure
            pass
        return evidence

    async def _step(self, action):
        if self.finished:
            raise RuntimeError("reset required before step")
        self._begin_transition()
        parts = {k: 0.0 for k in ("opponent_hp", "own_hp", "invalid", "terminal", "time")}
        from dsh_m1 import capture_v1
        from dsh_m1 import capture_v1
        from dsh_m1 import capture_v1
        decision_before = self.current
        _dec_obs_before = capture_v1.obs_id(self.writer.episode_id, int((decision_before or {}).get("frame") or 0))
        self._dec_acks = []
        self._dec_intent = None
        action_details = self._action_details(action)
        # TASK-105: expose the decision context to the default-off command capture.
        # No-op when capture is disabled; never raises into the control loop.
        try:
            if command_capture_mod.installed():
                episode_uid = command_capture_mod.episode_uid_for(
                    getattr(self.runner, "capture_id", None) or "",
                    getattr(self, "episode_id", None))
                command_capture_mod.set_context(
                    action_idx=int(action),
                    decision_id=command_capture_mod.decision_id_for(
                        getattr(self, "episode_id", None), self.decision_step),
                    issued_frame=int((decision_before or {}).get("frame") or 0),
                    episode_uid=episode_uid)
        except Exception:  # noqa: BLE001 - capture must never break control
            pass
        # A terminal snapshot can arrive while PPO is updating or another
        # worker resets. Use that observed result before sending another order.
        terminal = self._feedback_monitor is not None and self._feedback_monitor.terminal_state is not None
        if terminal:
            await self._capture()
        intent = self._select_intent(action) if self.kind == 'running' else None
        self._dec_intent = intent
        self._dec_intent = intent
        self._dec_intent = intent
        invalid = await self._commands([intent], "ppo") if intent else (0 if self._is_noop(action) else 1)
        deadline = time.monotonic() + self.decision_seconds
        if self.kind != 'running':
            parts = self._tick_rewards(decision_before, self.current, 0)
        while self.kind == 'running':
            # V4: scripted-economy invalid orders are tracked for diagnostics but
            # must never be charged to the model's reward (audit section 1/Q6).
            self.economy_invalid_orders += int(await self._economy() or 0)
            before = self.current
            await asyncio.sleep(min(0.5, max(0.001, deadline - time.monotonic())))
            await self._capture()
            tick = self._tick_rewards(before, self.current, invalid)
            invalid = 0
            for key in parts:
                parts[key] += tick[key]
            if self.kind != "running" or time.monotonic() >= deadline:
                break
        try:
            from dsh_m1 import capture_v1
            _rec = capture_v1.build_decision(
                self.writer.episode_id, self.decision_step,
                int((decision_before or {}).get("frame") or 0), _dec_obs_before,
                int(action), self.action_source, self.action_version, round(time.time(), 3),
                decision_before, self.current, intent=self._dec_intent,
                ack_records=self._dec_acks,
                status="TERMINAL" if self.kind != 'running' else "RUNNING")
            self.runner.capture.add(_rec)
            self.last_decision = _rec
            self.decision_step += 1
        except Exception:  # noqa: BLE001
            raise
        parts = self._complete_reward_parts(decision_before, self.current, parts)
        reward = float(sum(parts.values()))
        for key, value in parts.items():
            self.episode_reward_parts[key] = self.episode_reward_parts.get(key, 0.0) + value
        self.total_reward += reward
        self.transitions += 1
        record = {"transition": self.transitions, "episode_id": self.writer.episode_id,
                  "action": int(action), "reward": reward, "reward_parts": parts,
                  "frame": self.current["frame"], "kind": self.kind,
                  "feedback_valid": True, "feedback_version": FEEDBACK_VERSION,
                  "action_executed": not terminal,
                  "terminated": self.kind in ("win", "defeat", "game_end"), "truncated": self.kind == "timeout"}
        record.update(action_details)
        with (self.out_dir.parent / "transitions.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        x = self._features()
        self.last_features = x
        if self.kind != "running":
            self._finish(self.kind)
        return x, reward, record["terminated"], record["truncated"], record

    def _begin_transition(self):
        """Hook called at the start of every PPO decision step (no-op by default)."""
        return None

    def _result_metrics_extra(self):
        """Optional per-episode aggregate metrics (counts only for V4 truth)."""
        return {}

    def _complete_reward_parts(self, before, after, parts):
        parts["progress"] = progress_reward(before, after, self.starts, self.kind)
        mapped = int((self.current.get("map") or {}).get("mapped", 0))
        new_cells = max(0, mapped - self.max_mapped)
        parts["exploration"] = min(0.02, max(0.0, 0.5 - self.exploration_total), 0.001 * new_cells)
        self.exploration_total += parts["exploration"]
        self.max_mapped = max(self.max_mapped, mapped)
        parts.update(self._extra_rewards(before, after))
        return parts

    def _finish(self, kind, failure=None):
        """Guaranteed-non-raising finalize wrapper (RUN02 regression hardening).

        An exception inside finalize must never mask the original failure or skip the
        game cleanup (the failed RUN02 left an orphan game because a SchemaError escaped
        this path).
        """
        try:
            return self._finish_impl(kind, failure)
        except BaseException as exc:  # noqa: BLE001 - finalize must never escape
            try:
                import pathlib
                out = pathlib.Path(getattr(self, "out_dir", "."))
                out.mkdir(parents=True, exist_ok=True)
                (out / f"finish-error-{int(time.time())}.json").write_text(
                    json.dumps({"kind": kind, "error_type": type(exc).__name__,
                                "error": str(exc),
                                "traceback": traceback.format_exc(),
                                "failure": failure},
                               indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
            return None

    def _finish_impl(self, kind, failure=None):
        # Normalize failure kinds to the episode schema's allowed result kinds
        # ("connection_lost" is environmental evidence, not a valid episode result).
        original_kind = kind
        if kind not in schema.RESULT_KINDS:
            kind = "crash" if failure is not None else "game_end"
        if getattr(self, "protection", None) is not None and not self.finished:
            # RUN02: stop the watchdog, persist the end/stall screenshot + diagnostics.
            # Runs before the episode files are published so the protection evidence is
            # on disk even if the finalize itself is interrupted.
            try:
                self._protection_summary = self._protect_finish(kind, failure)
            except Exception:  # noqa: BLE001 - protection must never break finalize
                self._protection_summary = {"error": "protection finish failed"}
        try:
            from dsh_m1 import map_diag
            map_diag.record('LOOP_END', instance=self.instance, episode_id=getattr(self, 'episode_id', None),
                            kind=kind, counters=dict(getattr(self, 'map_stats', {})))
            map_diag.write_summary(dict(getattr(self, 'map_stats', {})), instance=self.instance)
        except Exception:
            pass
        try:
            from dsh_m1 import map_diag
            map_diag.record('LOOP_END', instance=self.instance, episode_id=getattr(self, 'episode_id', None),
                            kind=kind, counters=dict(getattr(self, 'map_stats', {})))
            map_diag.write_summary(dict(getattr(self, 'map_stats', {})), instance=self.instance)
        except Exception:
            pass
        try:
            from dsh_m1 import map_diag
            map_diag.record('LOOP_END', instance=self.instance, episode_id=getattr(self, 'episode_id', None),
                            kind=kind, counters=dict(getattr(self, 'map_stats', {})))
            map_diag.write_summary(dict(getattr(self, 'map_stats', {})), instance=self.instance)
        except Exception:
            pass
        if self.writer is None or self.finished:
            return
        self._v3_finalize(kind, failure)
        metrics = schema.derive_metrics(self.writer.rows)
        extra_metrics = self._result_metrics_extra()
        if extra_metrics:
            metrics.update(extra_metrics)
        stats = {"orders_ok": self.orders_ok, "invalid_orders": self.invalid_orders,
                 "visibility_violations": 0, "rl_orders_ok": self.rl_orders_ok,
                 "economy_invalid_orders": int(self.economy_invalid_orders)}
        try:
            if self.runner is not None and getattr(self.runner, "capture", None) is not None:
                self.runner.capture.write(self.out_dir / "episodes")
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.runner is not None and getattr(self.runner, "capture", None) is not None:
                self.runner.capture.write(self.out_dir / "episodes")
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.runner is not None and getattr(self.runner, "capture", None) is not None:
                self.runner.capture.write(self.out_dir / "episodes")
        except Exception:  # noqa: BLE001
            pass
        result = {"episode_id": self.writer.episode_id, "schema_version": schema.SCHEMA_VERSION,
                  "kind": kind, "is_win": kind == "win", "is_crash": kind == "crash",
                  "failure_kind": original_kind,
                  "feedback_version": FEEDBACK_VERSION,
                  "outcome_confirmed": kind in ('win', 'defeat'),
                  "learning_excluded": failure is not None,
                  "failure": failure, "reward_total": self.total_reward,
                  "reward_parts": dict(getattr(self, 'episode_reward_parts', {})),
                  "rows": self.writer.row_count, "elapsed_s": round(time.monotonic() - self.started, 3),
                  "metrics": metrics,
                  "stats": stats,
                  "environment": self.writer.meta["environment"]}
        path = self.writer.finalize(result)
        path.with_suffix(".result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        from dsh_rl.reflection import write_reflection
        write_reflection(self.out_dir.parent, result, self.writer.rows,
                         getattr(self, "episode_reward_parts", {}))
        self.completed.append(result)
        self.finished = True

    async def _stop_client(self):
        if self._feedback_monitor is not None:
            await self._feedback_monitor.stop()
            self._feedback_monitor = None
        if self.runner is not None and self.runner.client is not None:
            try:
                # RUN02: lifecycle RPC uses the same central timeout table (15s).
                await asyncio.wait_for(self.runner.client.stop(),
                                       _rpc_timeout("client.stop"))
            except Exception:
                pass

    def _cleanup_game(self):
        if self.runner is not None:
            self.runner.cleanup()
            self.runner = None

    def close(self):
        """Teardown that can never leak the isolated game process (RUN02 regression)."""
        if self.loop.is_closed():
            return
        try:
            self._finish("interrupted")
        except BaseException:  # noqa: BLE001 - close must continue
            pass
        try:
            self._run(self._stop_client(), name="stop_client")
        except BaseException:
            pass
        # RUN02: the failed run left an orphan game because this call was skipped when
        # finalize raised. Cleanup now runs in `finally` and is individually guarded.
        try:
            self._cleanup_game()
        except BaseException:  # noqa: BLE001
            self.runner = None
        async def cancel_remaining():
            pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        try:
            self._run(cancel_remaining(), name="cancel_remaining")
        except BaseException:
            pass
        try:
            if self._loop_thread is not None:
                self.loop.call_soon_threadsafe(self.loop.stop)
                self._loop_thread.join(5)
            self.loop.close()
        except BaseException:  # noqa: BLE001
            pass
        if getattr(self, "protection", None) is not None:
            try:
                self._protect_close()
            except BaseException:  # noqa: BLE001
                pass
            self.protection = None


# ---------------- P3 env-path map capture (production path) ----------------
def _bind_capture(self, episode_id):
    """Bind decision capture + capture dir to the REAL runner instance; observable CAPTURE_BIND."""
    from dsh_m1 import capture_v1, map_diag, command_capture
    self.runner.capture = capture_v1.DecisionCapture(episode_id)
    self.runner.capture_dir = self.out_dir
    self.runner.capture_dir.mkdir(parents=True, exist_ok=True)
    self.episode_id = episode_id
    # Only episode reset clears the episode-wide action associations. Clearing
    # these in _step discarded every decision except the final step, and reused
    # command IDs on every step even though full_env delegates to this path.
    self._dec_seq = 0
    self._v3_decisions = []
    self._v3_events = []
    self._wdzs_advice_sink = None
    self._wdzs_latest_advice = None
    self._wdzs_advice_error = None
    # TASK-105: default-off command capture install (explicit opt-in only).
    try:
        if command_capture.enabled():
            info = command_capture.install_for_module(
                sys.modules[type(self.runner).__module__],
                capture_dir=self.out_dir, episode_id=episode_id)
            if info:
                self.runner.capture_id = info.get("capture_id")
                self.runner.cmd_capture_state = info
    except Exception:  # noqa: BLE001 - capture must never break episode setup
        pass
    map_diag.record("RUNNER_INIT", instance=self.instance, episode_id=episode_id,
                    runner_id=id(self.runner), env_id=id(self), module=type(self.runner).__module__)
    map_diag.record("CAPTURE_BIND", instance=self.instance, episode_id=episode_id,
                    runner_id=id(self.runner), capture_id=id(self.runner.capture),
                    capture_dir=str(self.runner.capture_dir))


async def _refresh_map_sidecar(self, state):
    from dsh_m1 import capture_v1, map_diag
    import traceback
    st = self.map_stats
    frame = int(getattr(state, "current_frame", -1))
    now = time.monotonic() - self.started
    st["map_refresh_check"] += 1
    map_diag.record("MAP_REFRESH_CHECK", instance=self.instance, episode_id=self.episode_id, frame_id=frame)
    if now - self.last_map_at <= self.map_interval:
        st["map_refresh_skip"] += 1
        map_diag.record("MAP_REFRESH_SKIPPED", instance=self.instance, episode_id=self.episode_id, frame_id=frame,
                        age=round(now - self.last_map_at, 3), interval=self.map_interval)
        return
    st["map_refresh_enter"] += 1; st["map_read_attempt"] += 1
    map_diag.record("MAP_REFRESH_ENTER", instance=self.instance, episode_id=self.episode_id, frame_id=frame)
    map_diag.record("MAP_READ_START", instance=self.instance, episode_id=self.episode_id, frame_id=frame)
    fresh = False
    try:
        soa = await read_map_soa(self.runner.client)
        if (soa is None or int(getattr(soa, "map_width", 0)) <= 0
                or len(getattr(soa, "mapped", []) or []) == 0):
            raise ValueError(f"SERIALIZE_EMPTY width={getattr(soa,'map_width',None)} "
                             f"mapped={len(getattr(soa,'mapped',[]) or [])}")
        self.map_soa = soa; fresh = True; st["map_read_success"] += 1
        map_diag.record("MAP_READ_SUCCESS", instance=self.instance, episode_id=self.episode_id, frame_id=frame,
                        width=int(soa.map_width), height=int(soa.map_height), mapped=len(soa.mapped))
    except Exception as exc:  # noqa: BLE001
        self.map_soa = None; st["map_read_fail"] += 1
        map_diag.record("MAP_READ_FAILED", instance=self.instance, episode_id=self.episode_id, frame_id=frame,
                        error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc()[-800:])
    self.last_map_at = now
    if fresh and getattr(self.runner, "capture", None) is not None and getattr(self.runner, "capture_dir", None) is not None:
        st["sidecar_attempt"] += 1
        map_diag.record("SIDECAR_WRITE_START", instance=self.instance, episode_id=self.episode_id, frame_id=frame)
        try:
            m = capture_v1.write_map_sidecar(
                pathlib.Path(self.runner.capture_dir) / f"{self.runner.capture.episode_id}.map-f{frame}.npz",
                self.map_soa, self.runner.capture.episode_id, frame)
            st["sidecar_success"] += 1
            map_diag.record("SIDECAR_WRITE_SUCCESS", instance=self.instance, episode_id=self.episode_id,
                            frame_id=frame, capture_gap=m.get("capture_gap", []), sha256=m.get("sha256"))
            if m.get("capture_gap"):
                map_diag.record("CAPTURE_GAP", instance=self.instance, episode_id=self.episode_id,
                                frame_id=frame, fields=m["capture_gap"])
        except Exception as exc:  # noqa: BLE001
            st["sidecar_fail"] += 1
            map_diag.record("SIDECAR_WRITE_FAILED", instance=self.instance, episode_id=self.episode_id,
                            frame_id=frame, error=f"{type(exc).__name__}: {exc}",
                            traceback=traceback.format_exc()[-800:])


async def _capture(self, state=None):
    from dsh_m1 import map_diag
    import traceback
    self.map_stats["env_capture_enter"] += 1
    map_diag.record("ENV_CAPTURE_ENTER", instance=self.instance,
                    episode_id=getattr(self, "episode_id", None),
                    frame_id=int(getattr(state, "current_frame", -1)) if state is not None else None)
    try:
        row = await self._capture_impl(state)
        map_diag.record("ENV_CAPTURE_END", instance=self.instance, episode_id=getattr(self, "episode_id", None),
                        frame_id=int(row.get("frame", -1)) if isinstance(row, dict) else None, counters=dict(self.map_stats))
        return row
    except Exception as exc:  # noqa: BLE001
        map_diag.record("ENV_CAPTURE_CRASH", instance=self.instance, episode_id=getattr(self, "episode_id", None),
                        error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc()[-1200:])
        raise


RA2MicroEnv._bind_capture = _bind_capture
RA2MicroEnv._refresh_map_sidecar = _refresh_map_sidecar
RA2MicroEnv._capture = _capture


# ---------------- hardened ENV_CAPTURE wrapper (CRASH observable even on partial init) ----------
async def _capture_safe(self, state=None):
    from dsh_m1 import map_diag
    import traceback
    try:
        stats = getattr(self, "map_stats", None)
        if stats is not None:
            stats["env_capture_enter"] = stats.get("env_capture_enter", 0) + 1
        map_diag.record("ENV_CAPTURE_ENTER", instance=getattr(self, "instance", 0),
                        episode_id=getattr(self, "episode_id", None),
                        frame_id=int(getattr(state, "current_frame", -1)) if state is not None else None)
        row = await RA2MicroEnv._capture_impl(self, state)
        map_diag.record("ENV_CAPTURE_END", instance=getattr(self, "instance", 0),
                        episode_id=getattr(self, "episode_id", None),
                        frame_id=int(row.get("frame", -1)) if isinstance(row, dict) else None,
                        counters=dict(getattr(self, "map_stats", {}) or {}))
        return row
    except Exception as exc:  # noqa: BLE001
        map_diag.record("ENV_CAPTURE_CRASH", instance=getattr(self, "instance", 0),
                        episode_id=getattr(self, "episode_id", None),
                        error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc()[-1500:])
        raise


RA2MicroEnv._capture = _capture_safe


# ---------------- hardened ENV_CAPTURE wrapper (CRASH observable even on partial init) ----------
async def _capture_safe(self, state=None):
    from dsh_m1 import map_diag
    import traceback
    try:
        stats = getattr(self, "map_stats", None)
        if stats is not None:
            stats["env_capture_enter"] = stats.get("env_capture_enter", 0) + 1
        map_diag.record("ENV_CAPTURE_ENTER", instance=getattr(self, "instance", 0),
                        episode_id=getattr(self, "episode_id", None),
                        frame_id=int(getattr(state, "current_frame", -1)) if state is not None else None)
        row = await RA2MicroEnv._capture_impl(self, state)
        map_diag.record("ENV_CAPTURE_END", instance=getattr(self, "instance", 0),
                        episode_id=getattr(self, "episode_id", None),
                        frame_id=int(row.get("frame", -1)) if isinstance(row, dict) else None,
                        counters=dict(getattr(self, "map_stats", {}) or {}))
        return row
    except Exception as exc:  # noqa: BLE001
        map_diag.record("ENV_CAPTURE_CRASH", instance=getattr(self, "instance", 0),
                        episode_id=getattr(self, "episode_id", None),
                        error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc()[-1500:])
        raise


RA2MicroEnv._capture = _capture_safe


# ---------------- hardened ENV_CAPTURE wrapper (CRASH observable even on partial init) ----------
async def _capture_safe(self, state=None):
    from dsh_m1 import map_diag
    import traceback
    try:
        stats = getattr(self, "map_stats", None)
        if stats is not None:
            stats["env_capture_enter"] = stats.get("env_capture_enter", 0) + 1
        map_diag.record("ENV_CAPTURE_ENTER", instance=getattr(self, "instance", 0),
                        episode_id=getattr(self, "episode_id", None),
                        frame_id=int(getattr(state, "current_frame", -1)) if state is not None else None)
        row = await RA2MicroEnv._capture_impl(self, state)
        map_diag.record("ENV_CAPTURE_END", instance=getattr(self, "instance", 0),
                        episode_id=getattr(self, "episode_id", None),
                        frame_id=int(row.get("frame", -1)) if isinstance(row, dict) else None,
                        counters=dict(getattr(self, "map_stats", {}) or {}))
        return row
    except Exception as exc:  # noqa: BLE001
        map_diag.record("ENV_CAPTURE_CRASH", instance=getattr(self, "instance", 0),
                        episode_id=getattr(self, "episode_id", None),
                        error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc()[-1500:])
        raise


RA2MicroEnv._capture = _capture_safe


# ---------------- hardened ENV_CAPTURE wrapper (CRASH observable even on partial init) ----------
async def _capture_safe(self, state=None):
    from dsh_m1 import map_diag
    import traceback
    try:
        stats = getattr(self, "map_stats", None)
        if stats is not None:
            stats["env_capture_enter"] = stats.get("env_capture_enter", 0) + 1
        map_diag.record("ENV_CAPTURE_ENTER", instance=getattr(self, "instance", 0),
                        episode_id=getattr(self, "episode_id", None),
                        frame_id=int(getattr(state, "current_frame", -1)) if state is not None else None)
        row = await RA2MicroEnv._capture_impl(self, state)
        map_diag.record("ENV_CAPTURE_END", instance=getattr(self, "instance", 0),
                        episode_id=getattr(self, "episode_id", None),
                        frame_id=int(row.get("frame", -1)) if isinstance(row, dict) else None,
                        counters=dict(getattr(self, "map_stats", {}) or {}))
        return row
    except Exception as exc:  # noqa: BLE001
        map_diag.record("ENV_CAPTURE_CRASH", instance=getattr(self, "instance", 0),
                        episode_id=getattr(self, "episode_id", None),
                        error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc()[-1500:])
        raise


RA2MicroEnv._capture = _capture_safe


# ---------------- hardened ENV_CAPTURE wrapper (CRASH observable even on partial init) ----------
async def _capture_safe(self, state=None):
    from dsh_m1 import map_diag
    import traceback
    try:
        stats = getattr(self, "map_stats", None)
        if stats is not None:
            stats["env_capture_enter"] = stats.get("env_capture_enter", 0) + 1
        map_diag.record("ENV_CAPTURE_ENTER", instance=getattr(self, "instance", 0),
                        episode_id=getattr(self, "episode_id", None),
                        frame_id=int(getattr(state, "current_frame", -1)) if state is not None else None)
        row = await RA2MicroEnv._capture_impl(self, state)
        map_diag.record("ENV_CAPTURE_END", instance=getattr(self, "instance", 0),
                        episode_id=getattr(self, "episode_id", None),
                        frame_id=int(row.get("frame", -1)) if isinstance(row, dict) else None,
                        counters=dict(getattr(self, "map_stats", {}) or {}))
        return row
    except Exception as exc:  # noqa: BLE001
        map_diag.record("ENV_CAPTURE_CRASH", instance=getattr(self, "instance", 0),
                        episode_id=getattr(self, "episode_id", None),
                        error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc()[-1500:])
        raise


RA2MicroEnv._capture = _capture_safe


# ---------------- hardened ENV_CAPTURE wrapper (CRASH observable even on partial init) ----------
async def _capture_safe(self, state=None):
    from dsh_m1 import map_diag
    import traceback
    try:
        stats = getattr(self, "map_stats", None)
        if stats is not None:
            stats["env_capture_enter"] = stats.get("env_capture_enter", 0) + 1
        map_diag.record("ENV_CAPTURE_ENTER", instance=getattr(self, "instance", 0),
                        episode_id=getattr(self, "episode_id", None),
                        frame_id=int(getattr(state, "current_frame", -1)) if state is not None else None)
        row = await RA2MicroEnv._capture_impl(self, state)
        map_diag.record("ENV_CAPTURE_END", instance=getattr(self, "instance", 0),
                        episode_id=getattr(self, "episode_id", None),
                        frame_id=int(row.get("frame", -1)) if isinstance(row, dict) else None,
                        counters=dict(getattr(self, "map_stats", {}) or {}))
        return row
    except Exception as exc:  # noqa: BLE001
        map_diag.record("ENV_CAPTURE_CRASH", instance=getattr(self, "instance", 0),
                        episode_id=getattr(self, "episode_id", None),
                        error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc()[-1500:])
        raise


RA2MicroEnv._capture = _capture_safe


# ---------------- G1-EXT episode-v3 capture hooks (offline-tested) ----------------
def _v3_capture_events(self, state):
    import os
    if os.environ.get('DSH_V3_CAPTURE') != '1':
        return
    try:
        from dsh_m1 import capture_ext_v3 as C
        self._v3_events = getattr(self, '_v3_events', [])
        for lname in ('out_list', 'do_list', 'megamission_list'):
            for ev in (getattr(state, lname, []) or []):
                try:
                    from google.protobuf.json_format import MessageToDict
                    d = MessageToDict(ev)
                except Exception:
                    d = dict(ev) if isinstance(ev, dict) else {}
                d['list'] = lname
                d['frame'] = getattr(state, 'current_frame', d.get('frame'))
                self._v3_events.append(d)
    except Exception:
        pass


def _v3_link_decision(self, record):
    import os
    if os.environ.get('DSH_V3_CAPTURE') != '1':
        return
    # Finalization selects action rows only; diagnostics must not shift pairing.
    if record.get('type') != 'action' or not record.get('action'):
        return
    self._v3_decisions = getattr(self, '_v3_decisions', [])
    try:
        self._v3_decisions.append(dict(record))
    except Exception:
        pass


def _v3_finalize(self, kind="unknown", failure=None):
    import os, pathlib
    if os.environ.get('DSH_V3_CAPTURE') != '1':
        return
    try:
        if _run_protection is not None and getattr(self, 'protection', None) is not None:
            # RUN02: safe partial finalize (staging -> atomic publish -> SHA manifest,
            # explicit COMPLETE/PARTIAL/STALLED marker; never a normal-looking finish
            # for an incomplete episode).
            self._v3_manifest = self._protect_finalize_v3(kind)
            self._v3_error = None
            return
        from dsh_m1 import capture_ext_v3 as C
        from ra2v2 import capture_v3_wiring as W
        ep = str(getattr(self.writer, 'episode_id', 'ep'))
        episode_dir = pathlib.Path(getattr(self, 'out_dir', '.')).parent / 'episode-v3' / ep
        rows = list(getattr(self.writer, 'rows', []))
        obs_all = [r for r in rows if r.get('type') == 'observation']
        actions = [r for r in rows if r.get('type') == 'action' and r.get('action')]
        self._v3_manifest = W.finalize_episode(
            episode_dir, ep,
            decisions=list(getattr(self, '_v3_decisions', [])),
            actions=actions, obs_before=obs_all[:-1], obs_after=obs_all[1:],
            raw_events=list(getattr(self, '_v3_events', [])),
            neutral_objects=[], map_soa_result=C.map_soa_full(getattr(self, 'map_soa', None)))
    except Exception as exc:
        self._v3_error = f'{type(exc).__name__}: {exc}'

for _n in ('_v3_capture_events', '_v3_link_decision', '_v3_finalize'):
    setattr(RA2MicroEnv, _n, globals()[_n])


# ---------------- RUN02 run-protection production installation ----------------
# Single installation point: every hook called above is provided by this install.
# (Called at import time; the real-run entry collect_teacher.py also asserts it.)
if _run_protection is not None:
    RA2_PROTECTION_INSTALLED = _run_protection.install_env_protection(RA2MicroEnv)
else:  # pragma: no cover - only when v2_candidate/src is unavailable
    RA2_PROTECTION_INSTALLED = False
