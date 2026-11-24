"""One bounded local vision/input session, accessible from Codex or DSH MCP."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
import uuid
import tempfile
import threading

import cv2

from engine import (CAPTURES, LOGS, PROFILES, box, identifier, image_read, image_write,
                    integer, load_rules, summary)
from native import Guard, Windows
from actions import execute_steps, validate_steps, number


class Capture:
    def __init__(self, region):
        import dxcam
        self.camera = dxcam.create(output_color="BGR", processor_backend="cv2")
        self.region = tuple(region)

    def grab(self):
        # Fresh DXGI frame only. A stale frame must not count as another stable detection.
        return self.camera.grab(region=self.region, new_frame_only=True)

    def close(self):
        self.camera.release()


def get_frame(camera, guard, timeout=2, fresh=True):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        guard.check()
        frame = camera.grab() if fresh else camera.camera.grab(region=camera.region, new_frame_only=False)
        if frame is not None:
            guard.check()
            return frame
        time.sleep(0.005)
    raise RuntimeError("未取得新的游戏画面。")


def capture_save(frame, session, name, guard):
    path = CAPTURES / (session + "_" + name + ".png")
    image_write(path, frame)
    return str(path)


def teach(request):
    """Extract an agent-reviewed visual cue from an already saved game snapshot."""
    name = identifier(request["profile"])
    snapshot = identifier(request["snapshot"])
    metadata = json.loads((CAPTURES / (snapshot + ".json")).read_text(encoding="utf-8"))
    frame = image_read(CAPTURES / (snapshot + "_snapshot.png"))
    height, width = frame.shape[:2]
    if metadata["client_size"] != [width, height]:
        raise ValueError("截图元数据不匹配。")
    source_rules = request["rules"]
    if not isinstance(source_rules, list) or not 1 <= len(source_rules) <= 8:
        raise ValueError("需要 1 到 8 条规则。")
    folder = PROFILES / name
    if folder.exists():
        raise ValueError("配置已存在，请使用新名称，避免覆盖已有规则。")
    prepared = []
    for row in source_rules:
        rule_name = identifier(row["name"])
        l, t, r, b = box(row["crop"], width, height)
        region = list(box(row.get("region", [max(0, l - 15), max(0, t - 15),
                                             min(width, r + 15), min(height, b + 15)]), width, height))
        prepared.append((rule_name, frame[t:b, l:r].copy(), {
            "name": rule_name, "region": region, "threshold": row.get("threshold", 0.93),
            "stable_frames": row.get("stable_frames", 2), "action": row["action"],
            "cooldown_s": row.get("cooldown_s", 1),
        }))
    if len({row[0] for row in prepared}) != len(prepared):
        raise ValueError("规则名称不能重复。")
    config = {"target_executable": metadata.get("target_executable", metadata.get("game_executable")), "client_size": [width, height],
              "source_snapshot": snapshot, "rules": [row[2] for row in prepared]}
    PROFILES.mkdir(parents=True, exist_ok=True)
    # Publish only validated profiles; bad rules must not leave a broken named profile.
    with tempfile.TemporaryDirectory(prefix="teach-", dir=PROFILES) as staging:
        pending = Path(staging) / name
        pending.mkdir()
        for rule_name, template, _ in prepared:
            image_write(pending / (rule_name + ".png"), template)
        (pending / "profile.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        load_rules(name, Path(staging))
        pending.rename(folder)
    return {"ok": True, "applied": False, "profile": name, "rules": len(prepared)}


def run(request, cancel=None):
    operation = request.get("operation", "snapshot")
    if operation == "teach":
        return teach(request)
    if operation not in ("snapshot", "watch", "sequence"):
        raise ValueError("operation 必须是 snapshot、teach、watch 或 sequence。")
    seconds = request.get("duration_s", 10)
    number(seconds, 0.5, 20)
    fps = integer(request.get("fps", 30), 5, 60)
    limit = integer(request.get("max_actions", 1), 1, 20)
    dry = request.get("dry_run", True)
    if type(dry) is not bool:
        raise ValueError("dry_run 必须是布尔值。")
    config, rules = load_rules(request["profile"]) if operation == "watch" else (None, [])
    target = request.get("target_executable")
    if config is not None:
        bound = config.get("target_executable", config.get("game_executable"))
        if not bound or target and Path(bound).resolve() != Path(target).resolve():
            raise ValueError("配置未绑定当前目标程序。")
        target = bound
    if operation == "sequence":
        if not isinstance(target, str) or not Path(target).is_absolute():
            raise ValueError("连续输入需要绑定目标程序的绝对路径。")
        validate_steps(request.get("steps"))
    win = Windows()
    session = "s" + uuid.uuid4().hex
    metrics = {"capture": [], "match": [], "capture_to_input_complete": []}
    events, frames, reason = [], 0, "duration_elapsed"
    result = {"ok": False, "applied": False, "session": session, "events": events}
    guard, camera = Guard(win, request["observation_id"], target, cancel), None
    try:
        with guard:
            l, t, r, b = guard.client_rect
            size = [r - l, b - t]
            if config is not None and config["client_size"] != size:
                raise ValueError("游戏分辨率与模板不同，请重新采样。")
            if operation == "sequence":
                validate_steps(request["steps"], *size)
            camera = Capture(guard.client_rect)
            frame = get_frame(camera, guard)
            if operation == "snapshot":
                path = capture_save(frame, session, "snapshot", guard)
                metadata = {"session": session, "client_size": size, "client_rect": guard.client_rect,
                            "target_executable": str(guard.expected_exe), "created_utc": datetime.now(timezone.utc).isoformat()}
                (CAPTURES / (session + ".json")).write_text(json.dumps(metadata), encoding="utf-8")
                result.update(ok=True, applied=False, **metadata, observation_id=guard.renew(),
                              image={"path": path, "width": size[0], "height": size[1]},
                              coordinates="client_pixels")
                return result
            guard.consume()
            initial = capture_save(frame, session, "before", guard)
            if operation == "sequence":
                started = time.perf_counter()
                if not dry:
                    execute_steps(guard, request["steps"])
                guard.release_button()
                guard.release_keys()
                guard.wait(0.12)
                after = get_frame(camera, guard, fresh=False)
                final = capture_save(after, session, "after", guard)
                result.update(ok=True, applied=guard.applied, dry_run=dry,
                              steps=len(request["steps"]), elapsed_s=round(time.perf_counter()-started, 4),
                              observation_id=guard.renew(), target_executable=str(guard.expected_exe),
                              coordinates="client_pixels", client_size=size, before_image=initial,
                              image={"path": final, "width": size[0], "height": size[1]})
                return result
            start = time.perf_counter()
            deadline = start + seconds
            session_deadline = guard.deadline
            guard.deadline = min(guard.deadline, deadline)
            next_tick = start
            misses = 0
            while time.perf_counter() < deadline:
                guard.check()
                tick = time.perf_counter()
                if tick < next_tick:
                    time.sleep(min(0.005, next_tick - tick))
                    continue
                next_tick = tick + 1 / fps
                capture_start = time.perf_counter()
                current = camera.grab()
                captured = time.perf_counter()
                if current is None:
                    misses += 1
                    continue
                guard.check()
                frames += 1
                metrics["capture"].append((captured - capture_start) * 1000)
                matches = [(rule, *rule.match(current)) for rule in rules]
                matched = time.perf_counter()
                metrics["match"].append((matched - captured) * 1000)
                for rule, score, location in matches:
                    if not rule.update(score, captured):
                        continue
                    guard.check()
                    action = rule.action
                    before_input = time.perf_counter()
                    if not dry:
                        if action["type"] == "click_match":
                            guard.click(*location)
                        elif action["type"] == "click":
                            guard.click(action["x"], action["y"])
                        elif action["type"] == "key":
                            guard.key(action["key"])
                        elif action["type"] == "sequence":
                            execute_steps(guard, action["steps"])
                            guard.release_button()
                            guard.release_keys()
                    completed = time.perf_counter()
                    rule.fired(completed)
                    metrics["capture_to_input_complete"].append((completed - capture_start) * 1000)
                    events.append({"rule": rule.name, "score": round(score, 5), "action": action,
                                   "dry_run": dry, "location_client_px": location,
                                   "at_s": round(completed - start, 4),
                                   "first_seen_to_input_complete_ms": round((completed - rule.first_seen) * 1000, 3),
                                   "match_to_input_start_ms": round((before_input - matched) * 1000, 3),
                                   "capture_to_input_complete_ms": round((completed - capture_start) * 1000, 3)})
                    # An action may change the UI: never apply another rule to this old frame.
                    break
                if len(events) >= limit:
                    reason = "action_limit"
                    break
            elapsed = time.perf_counter() - start
            guard.deadline = session_deadline
            guard.wait(0.15)
            after = get_frame(camera, guard, fresh=False)
            final = capture_save(after, session, "after", guard)
            result.update(ok=True, applied=guard.applied, reason=reason, dry_run=dry,
                          frames=frames, empty_polls=misses, elapsed_s=round(elapsed, 4),
                          observed_fps=round(frames / elapsed, 2),
                          metrics={key: summary(value) for key, value in metrics.items()},
                          observation_id=guard.renew(), target_executable=str(guard.expected_exe),
                          coordinates="client_pixels", client_size=size,
                          before_image=initial, image={"path": final, "width": size[0], "height": size[1]})
    except Exception as error:
        result.update(ok=False, applied=guard.applied, message=str(error),
                      error_type=type(error).__name__)
    finally:
        if camera is not None:
            try:
                camera.close()
            except Exception as error:
                result.update(ok=False, message="采集资源释放失败：" + str(error))
        result["keys_released"] = not guard.keys and not guard.buttons
        LOGS.mkdir(parents=True, exist_ok=True)
        (LOGS / (session + ".json")).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-file")
    args = parser.parse_args()
    cancel = threading.Event()
    try:
        raw = Path(args.request_file).read_text(encoding="utf-8-sig") if args.request_file else sys.stdin.readline(65537)
        if len(raw) > 65536:
            raise ValueError("请求过长。")
        if not args.request_file:
            # A closed MCP parent or explicit cancellation releases held input cooperatively.
            def watch_parent():
                sys.stdin.readline()
                cancel.set()
            threading.Thread(target=watch_parent, daemon=True).start()
        result = run(json.loads(raw), cancel)
    except Exception as error:
        result = {"ok": False, "applied": False, "message": str(error), "error_type": type(error).__name__}
    print(json.dumps(result, ensure_ascii=True), flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
