"""Opt-in, replayable evidence for deterministic one-player decisions."""
from contextlib import ExitStack
import gzip
import hashlib
import json
import math
from pathlib import Path

import torch
from scripts.v2_ops.contract import FAMILIES, mask
from .model import load


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else _stream_hash(stream)


def _stream_hash(stream):
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1 << 20), b""):
        digest.update(block)
    return digest.hexdigest()


def _ranking(logits, legal, names):
    indices = [i for i, allowed in enumerate(legal) if allowed]
    if not indices:
        return []
    probabilities = torch.softmax(logits[indices], dim=0).tolist()
    ranked = sorted(zip(indices, probabilities), key=lambda pair: -pair[1])
    return [{"index": i, "name": names[i], "p": p} for i, p in ranked]


class DecisionRecorder:
    """Use decide in place of an inference call. Only the primary pass supplies h_next."""

    def __init__(self, path, checkpoint, source):
        self.path = Path(path)
        self.model, _, package = load(checkpoint)
        self.header = {
            "schema": "v2-attention-decision-trace/1", "checkpoint": str(Path(checkpoint).resolve()),
            "checkpoint_sha256": file_hash(checkpoint), "input_profile": self.model.input_profile,
            "architecture": package["board_attention"], "source": source, "deterministic": True,
            "torch_version": str(torch.__version__),
            "weights_definition": "Per-head probabilities recomputed from actual per-layer Q/K for the listed queries.",
            "ablation_definition": "Same public input and same incoming hidden state; omit only the new board attention values this step.",
        }

    def __enter__(self):
        self.stream = gzip.open(self.path, "xt", encoding="utf-8")
        self._write({"header": self.header})
        self.count = 0
        return self

    def __exit__(self, kind, exc, traceback):
        try:
            if kind is None:
                self._write({"end": {"decisions": self.count}})
        finally:
            self.stream.close()

    def _write(self, row):
        self.stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")

    @torch.inference_mode()
    def decide(self, snapshot, features, current, hidden=None, *, annotation=None):
        model = self.model
        board = model.control.board_attention
        if not board.use_board_context or not model.control.attention_enabled:
            raise ValueError("trace requires the active attention policy")
        inputs = ([snapshot], [features], [current], hidden)
        projections, encodings = [], []
        def capture_layer(module, args, kwargs):
            projections.append((module, args, kwargs["key_padding_mask"]))
        def capture_board(module, args, result):
            encodings.append(result)
        with ExitStack() as hooks:
            for layer in board.layers:
                hooks.enter_context(layer.register_forward_pre_hook(capture_layer, with_kwargs=True))
            hooks.enter_context(board.register_forward_hook(capture_board))
            out = model(*inputs, deterministic=True)
        core, products = encodings[0]
        family = _ranking(model.control.family_head(core[0]), mask(snapshot, "family", {}), FAMILIES)
        action = out["actions"][0]
        # These are conditional product choices, even on a frame where the family is WAIT.
        production = []
        if any(item["index"] == 2 for item in family):
            query = core[0] + model.control.family_embedding.weight[2]
            scores = products[0] @ model.control.query["production"](query) / math.sqrt(model.control.hidden)
            production = _ranking(scores, mask(snapshot, "production", {"family": 2}),
                                  [x["name"] for x in snapshot["production"]])
        assert action["family"] == family[0]["index"]
        if action["family"] == 2:
            assert action["production"] == production[0]["index"]
        queries = [("decision", 0)] + [(f"produce:{entry['name']}", 1 + entry["index"]) for entry in production[:3]]
        objects = [{"kind": "global_state"}] + [
            {"kind": e["object"]["type_name"], "friendly": e["friendly"],
             "track_id": e["track_id"], "cell": e["object"]["cell"]} for e in snapshot["entities"]]
        weights = []
        for layer, (q, k, v), key_mask in projections:
            _, probs = layer(q[:, [i for _, i in queries]], k, v, key_padding_mask=key_mask,
                             need_weights=True, average_attn_weights=False)
            # [query][head][public token]; token identities are stored separately in the same row.
            weights.append(probs[0].transpose(0, 1).tolist())
        board.use_board_context = False
        try:
            off = model(*inputs, deterministic=True)
            # Force the original action only to measure its probability in the ablated policy.
            off_original = (off if off["actions"][0] == action else model(*inputs, actions=[action]))
        finally:
            board.use_board_context = True
        record = {
            "id": f"{snapshot['episode']}:{snapshot['frame']}", "snapshot": snapshot,
            "features": torch.as_tensor(features).tolist(), "current": torch.as_tensor(current).tolist(),
            "hidden_before": None if hidden is None else hidden.detach().cpu().tolist(),
            "hidden_after": out["h_next"].tolist(), "action": action, "log_prob": float(out["log_prob"][0]),
            "family_ranking": family, "conditional_production_ranking": production,
            "weight_tokens": objects, "weight_queries": [name for name, _ in queries],
            "weights_by_layer": weights,
            "context_off": {"action": off["actions"][0], "original_action_log_prob": float(off_original["log_prob"][0])},
            "annotation": annotation,
        }
        self._write(record)
        self.count += 1
        return out
