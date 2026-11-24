"""Default-off read-only advice sidecar for the existing live environment.

Enable only in an authorized run with DSH_WDZS_ADVICE=1 and an explicitly chosen
DSH_WDZS_ADVICE_BUNDLE directory. No control/RPC/model-input calls occur here.
"""
from __future__ import annotations

import json
import os
import pathlib

from .wdzs_advisor import WdzsAdvisor


def capture_advice(env):
    if os.environ.get("DSH_WDZS_ADVICE") != "1":
        return None
    try:
        sink = getattr(env, "_wdzs_advice_sink", None)
        if sink is None:
            bundle_name = os.environ.get("DSH_WDZS_ADVICE_BUNDLE")
            if not bundle_name:
                raise ValueError("explicit WDZS advice bundle required")
            bundle = pathlib.Path(bundle_name)
            library = json.loads((bundle / "compiled_ai.json").read_text(encoding="utf-8"))
            rules = json.loads((bundle / "rules_reference.json").read_text(encoding="utf-8"))
            provenance = json.loads((bundle / "SOURCE_PROVENANCE.json").read_text(encoding="utf-8"))
            if library["source_sha256"] != provenance["candidate"]["sha256"]:
                raise ValueError("compiled library/provenance SHA conflict")
            episode_id = str(env.writer.episode_id)
            if pathlib.Path(episode_id).name != episode_id or "/" in episode_id or "\\" in episode_id:
                raise ValueError("invalid episode identifier")
            trace = pathlib.Path(env.out_dir) / (episode_id + ".wdzs-advice.jsonl")
            trace.parent.mkdir(parents=True, exist_ok=True)
            sink = (WdzsAdvisor(library, rules), trace)
            env._wdzs_advice_sink = sink
        advisor, trace = sink
        advice = advisor.suggest_from_env(env)
        advice["input_kind"] = "HOST_OBSERVATION_SIDECAR_NOT_TRAINING_LABEL"
        with trace.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(advice, ensure_ascii=False) + "\n")
        env._wdzs_latest_advice = advice
        env._wdzs_advice_error = None
        return advice
    except Exception as exc:  # diagnostics must never stop the game loop
        env._wdzs_advice_error = f"{type(exc).__name__}: {exc}"
        env._wdzs_latest_advice = None
        return None
