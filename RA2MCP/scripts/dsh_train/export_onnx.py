"""Optional: export an exported model dir (.torch checkpoint) to ONNX.

Not required for the default numpy runtime (infer.py). Use when a faster /
cross-language runtime is needed:
  python scripts/dsh_train/export_onnx.py --model reports/dsh-learning/models/bc-demo

Requires torch (training env); onnx/onnxruntime are optional for validation.
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="reports/dsh-learning/models/bc-demo")
    a = ap.parse_args()
    mdir = pathlib.Path(a.model)
    try:
        import torch
        import model as M
        from spec import FEATURE_VERSION
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"skipped": True, "reason": f"torch/model import failed: {e}"}))
        return
    norm = json.loads((mdir / "norm.json").read_text(encoding="utf-8"))
    meta = json.loads((mdir / "metrics.json").read_text(encoding="utf-8"))
    net = M.TorchMLP(norm["obs_dim"], meta["hidden"], norm["n_actions"])
    if not (mdir / "model.pt").exists():
        print(json.dumps({"skipped": True,
                          "reason": "model.pt missing (train with --backend torch)"}))
        return
    net.load_state_dict(torch.load(mdir / "model.pt", map_location="cpu"))
    net.eval()
    dummy = torch.zeros(1, norm["obs_dim"], dtype=torch.float32)
    out = mdir / "model.onnx"
    try:
        torch.onnx.export(net, dummy, str(out), input_names=["obs"],
                          output_names=["logits"], opset_version=17,
                          dynamic_axes={"obs": {0: "batch"}, "logits": {0: "batch"}})
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"skipped": True, "reason": f"onnx export failed: {e}"}))
        return
    res = {"onnx": str(out), "feature_version": FEATURE_VERSION,
           "obs_dim": norm["obs_dim"]}
    try:
        import onnxruntime as ort
        sess = ort.InferenceSession(str(out))
        import numpy as np
        y = sess.run(None, {"obs": np.zeros((1, norm["obs_dim"]), dtype=np.float32)})
        res["onnxruntime_ok"] = True
        res["sample_logits"] = y[0][0].tolist()
    except Exception as e:  # noqa: BLE001
        res["onnxruntime_ok"] = False
        res["onnxruntime_note"] = str(e)[:200]
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
