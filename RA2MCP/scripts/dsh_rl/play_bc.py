"""Train the action-level BC model on the collected data, then let it play a game.

The policy is learned only from previously recorded (state, action, mask) data;
no hand-written teacher rules are used at play time.

  F:\\conda\\envs\\python_mmx_LLM\\python.exe scripts\\dsh_rl\\play_bc.py --data <dataset.npz> --episode-seconds 200
"""
import argparse
import json
import pathlib
import sys
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from dsh_episode import lockfile  # noqa: E402
from dsh_rl.full_env import RA2FullEnv  # noqa: E402
from dsh_rl import full_spec as S  # noqa: E402
from dsh_rl.scripted_policy import intent_label  # noqa: E402


def load(data):
    d = np.load(data)
    return (d["obs"].astype(np.float32), d["actions"].astype(np.int64),
            d["masks"].astype(bool))


def train(obs, actions, masks, epochs, device, hidden=(512, 256)):
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    torch.manual_seed(0)
    layers = [nn.Linear(obs.shape[1], hidden[0]), nn.ReLU(), nn.Dropout(0.1),
              nn.Linear(hidden[0], hidden[1]), nn.ReLU(), nn.Linear(hidden[1], 301)]
    model = nn.Sequential(*layers).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    X = torch.tensor(obs, device=device)
    Y = torch.tensor(actions, device=device)
    M = torch.tensor(masks, device=device)
    NEG = torch.finfo(torch.float32).min / 2
    batch = 128
    for epoch in range(1, epochs + 1):
        model.train()
        perm = torch.randperm(len(X), device=device)
        total = 0.0
        for i in range(0, len(X), batch):
            idx = perm[i:i + batch]
            opt.zero_grad()
            logits = model(X[idx]).masked_fill(~M[idx], NEG)
            loss = F.cross_entropy(logits, Y[idx])
            loss.backward()
            opt.step()
            total += float(loss.item()) * len(idx)
        if epoch == 1 or epoch % 50 == 0:
            model.eval()
            with torch.no_grad():
                pred = model(X).masked_fill(~M, NEG).argmax(1)
                acc = float((pred == Y).float().mean().item())
            print(f"[train] epoch {epoch:3d} loss={total / max(1, len(X)):.4f} "
                  f"train_acc={acc:.3f}", flush=True)
    model.eval()
    return model


def stats(env):
    row = env.current
    own = [o for o in row.get("own_objects", []) if S.placed(o)]
    buildings = [o for o in own if S.is_building(o)]
    combat = [o for o in own if not S.is_building(o) and not S.is_mcv(o)]
    return {"t": round(float(row.get("t", 0)), 1), "money": int(row.get("money", 0)),
            "buildings": len(buildings), "units": len(combat),
            "building_types": sorted({o.get("type_name", "") for o in buildings}),
            "unit_types": sorted({o.get("type_name", "") for o in combat})}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", required=True)
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--episode-seconds", type=float, default=200)
    ap.add_argument("--mode", choices=("argmax", "sample"), default="sample")
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    data = pathlib.Path(a.data)
    out = pathlib.Path(a.out or (data.parent / f"play-bc-{time.strftime('%H%M%S')}"))
    out.mkdir(parents=True, exist_ok=True)

    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    obs, actions, masks = load(data)
    print(f"[play-bc] dataset samples={len(actions)} device={device}", flush=True)
    model = train(obs, actions, masks, a.epochs, device)
    torch.save(model.state_dict(), out / "bc_action_model.pt")

    held = lockfile.acquire(ROOT / "build" / "dsh-experiment.lock")
    env = None
    labels = {}
    try:
        env = RA2FullEnv(out / "episodes", max_seconds=a.episode_seconds,
                         decision_seconds=0.5, game_dir=None, port=14521,
                         instance=0, kill_stale=True)
        x, info = env.reset()
        done = False
        steps = 0
        cap = int(a.episode_seconds / 0.5) + 60
        while not done and steps < cap:
            mask = env.action_masks()
            with torch.no_grad():
                logits = model(torch.tensor(x, device=device).unsqueeze(0)).squeeze(0)
            legal = torch.tensor(mask, device=device)
            logits = logits.masked_fill(~legal, torch.finfo(torch.float32).min / 2)
            if a.mode == "argmax":
                action = int(logits.argmax().item())
            else:
                probs = torch.softmax(logits / max(1e-3, a.temperature), dim=0)
                action = int(torch.multinomial(probs, 1).item())
            labels[intent_label(action)] = labels.get(intent_label(action), 0) + 1
            x, reward, terminated, truncated, info = env.step(action)
            steps += 1
            done = terminated or truncated
            if steps % 50 == 0:
                st = stats(env)
                print(f"[play-bc] step{steps} t={st['t']}s money={st['money']} "
                      f"buildings={st['buildings']} units={st['units']}", flush=True)
        st = stats(env)
        last = dict(env.completed[-1]) if env.completed else {}
        report = {"data": str(data), "epochs": a.epochs, "mode": a.mode,
                  "episode_seconds": a.episode_seconds, "steps": steps,
                  "end_kind": last.get("kind"), "stats": st,
                  "action_intent_counts": labels,
                  "teacher_reference": {"buildings": 9, "units": 10}}
        (out / "play-bc.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
        print(f"[play-bc] end_kind={report['end_kind']} buildings={st['buildings']} "
              f"units={st['units']} intents={labels}", flush=True)
        print(f"[play-bc] wrote {out / 'play-bc.json'}", flush=True)
    finally:
        if env is not None:
            try:
                env.close()
            except BaseException:  # noqa: BLE001
                pass
        lockfile.release(held)


if __name__ == "__main__":
    main()
