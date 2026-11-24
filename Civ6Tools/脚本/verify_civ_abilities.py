r"""Offline verification of the get_civ_abilities chain.

Proves three things WITHOUT Civilization VI running:

1. The generated Lua chunk is syntactically valid (compiled by a real Lua VM).
2. The parser turns pipe-delimited output into typed objects.
3. The narrator renders them readably for an LLM.

Run:  E:\Civ6\civ6-mcp\.venv\Scripts\python.exe E:\Civ6\脚本\verify_civ_abilities.py
"""

import os
import sys

os.environ.setdefault("PYTHONUTF8", "1")

sys.path.insert(0, r"E:\Civ6\civ6-mcp\src")

import lupa  # noqa: E402

from civ_mcp import lua as lq  # noqa: E402
from civ_mcp.narrate import narrate_civ_abilities  # noqa: E402

FAIL = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAIL.append(label)


# --- 1. Lua syntax -----------------------------------------------------------
code = lq.build_civ_abilities_query()
try:
    lupa.LuaRuntime().compile(code)
    ok = True
    err = ""
except Exception as e:  # noqa: BLE001
    ok = False
    err = str(e)
check("Lua chunk compiles", ok, err or f"{len(code)} chars")

check("emits SENTINEL", lq.SENTINEL in code)
check("reads CivilizationTraits", "GameInfo.CivilizationTraits" in code)
check("reads LeaderTraits", "GameInfo.LeaderTraits" in code)
check("reads Traits table", "GameInfo.Traits[" in code)
check("reads Units by TraitType", "GameInfo.Units()" in code)
check("sanitises pipe separator", 'gsub("|", "/")' in code)

# --- 2 + 3. Parse + narrate synthetic Byzantium output -----------------------
SYNTHETIC = [
    "CIV|CIVILIZATION_BYZANTIUM|Byzantium|LEADER_BASIL_II|Basil II",
    "TRAIT|CIV|TRAIT_CIVILIZATION_BYZANTIUM|Taxis|+3 Combat Strength for each Holy City following your religion",
    "TRAIT|LEADER|TRAIT_LEADER_BASIL_II|Divine Providence|Killing an enemy unit converts its city",
    "UNIT|UNIT_TAGMA|Tagma|48|0|Horseback Riding||220",
    "DISTRICT|DISTRICT_BASILIKOI_PAIDES|Basilikoi Paides|0|0|Military Training||150",
    "BUILDING|BUILDING_HIPPODROME|Hippodrome|0|0|||120",
    "IMPROVEMENT|IMPROVEMENT_BYZANTINE_SPEARMAN|Spearman|0|0|||0",
    "DIAG|CIVTRAITS|1",
    "DIAG|LEADERTRAITS|1",
    "DIAG|TRAITS|2",
    "DIAG|UNITS_WITH_TRAITTYPE|87",
    "DIAG|OUR_UNITS|1",
    "DIAG|OUR_DISTRICTS|1",
    "DIAG|OUR_BUILDINGS|1",
    "DIAG|OUR_IMPROVEMENTS|1",
    lq.SENTINEL,
]

ab = lq.parse_civ_abilities_response(SYNTHETIC)

check("civ name parsed", ab.civ_name == "Byzantium", ab.civ_name)
check("leader parsed", ab.leader_name == "Basil II", ab.leader_name)
check("civ_type parsed", ab.civ_type == "CIVILIZATION_BYZANTIUM", ab.civ_type)
check("traits count == 2", len(ab.traits) == 2, str(len(ab.traits)))
check("trait scope kept", ab.traits[0].scope == "CIV", ab.traits[0].scope)
check("trait text kept", "Holy City" in ab.traits[0].description)
check("uniques count == 4", len(ab.uniques) == 4, str(len(ab.uniques)))
tagma = next((u for u in ab.uniques if u.kind == "UNIT"), None)
check("unit combat parsed", tagma is not None and tagma.combat == 48,
      str(tagma.combat if tagma else None))
check("unit prereq tech parsed",
      tagma is not None and tagma.prereq_tech == "Horseback Riding",
      tagma.prereq_tech if tagma else "")
check("unit cost parsed", tagma is not None and tagma.cost == 220,
      str(tagma.cost if tagma else None))
check("diagnostics parsed", ab.diagnostics.get("TRAITS") == 2, str(ab.diagnostics))

# Empty-description robustness
ab_empty = lq.parse_civ_abilities_response(["TRAIT|CIV|TRAIT_X|Name|", lq.SENTINEL])
check("empty description tolerated", len(ab_empty.traits) == 1,
      str(ab_empty.traits[0].description if ab_empty.traits else "none"))

# Zero-traits case must warn, not silently look fine
ab_zero = lq.parse_civ_abilities_response(["CIV|CIVILIZATION_X|X|LEADER_X|X", lq.SENTINEL])
text_zero = narrate_civ_abilities(ab_zero)
check("zero-trait path warns", "NO TRAITS RETURNED" in text_zero)

text = narrate_civ_abilities(ab)
check("narration has identity", "Byzantium" in text)
check("narration has trait text", "Holy City" in text)
check("narration has unique unit", "Tagma" in text)
check("narration has CS 48", "CS 48" in text)
check("narration has prereq", "Horseback Riding" in text)

print("\n--- rendered narration (what the model would receive) ---\n")
print(text)

print("\n" + ("ALL CHECKS PASSED" if not FAIL else f"FAILURES: {FAIL}"))
sys.exit(0 if not FAIL else 1)
