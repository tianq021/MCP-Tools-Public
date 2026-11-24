"""Apply the "God's Toy" AI buffs to the isolated wdzs mod copy.

The mode file mpgodstoymd.ini only overrides rules values; a local single-player
skirmish cannot select that CnCNet game mode, so we apply the same overrides
directly to the copy's plain RulesMD.ini (the original stays in E:\\hj).

Overrides taken from mpgodstoymd.ini:
  [General] MultiplayerAICM=4000,3000,2000   (AI money coefficient: 40x/30x/20x)
  [General] DisableAIParallelProduction=no
  AIBasePlanningCount for war factories / barracks / yards / airforce / radar
  the six extra MCV deploy AITriggers
"""
import pathlib
import re
import shutil

ROOT = pathlib.Path(r"E:\RA2MCP")
GAME = ROOT / "test-game-wdzs"
RULES = GAME / "RulesMD.ini"
BACKUP = ROOT / "backups" / "test-game-wdzs" / "RulesMD.ini.original"

PLANNING = {
    "GAWEAP": 6, "NAWEAP": 6, "YAWEAP": 6,
    "GAPILE": 3, "NAHAND": 3, "YABRCK": 3,
    "GAYARD": 6, "NAYARD": 6, "YAYARD": 6,
    "GAAIRC": 3, "AMRADR": 3,
}
MODE_TRIGGERS = [
    "06000100-G=Allied MCV(M),09000112-G,<all>,10,1,GACNST,0300000000000000000000000000000000000000000000000000000000000000,5000.000000,5000.000000,5000.000000,1,0,1,0,<none>,0,1,1",
    "06000101-G=Soviet MCV(M),09000113-G,<all>,10,1,NACNST,0300000000000000000000000000000000000000000000000000000000000000,5000.000000,5000.000000,5000.000000,1,0,2,0,<none>,0,1,1",
    "06000102-G=Yuri MCV(M),09000114-G,<all>,10,1,YACNST,0300000000000000000000000000000000000000000000000000000000000000,5000.000000,5000.000000,5000.000000,1,0,3,0,<none>,0,1,1",
    "06000288-G=Allied MCV(H),09000112-G,<all>,10,1,GACNST,0500000000000000000000000000000000000000000000000000000000000000,500.000000,500.000000,500.000000,1,0,1,0,<none>,0,0,1",
    "06000289-G=Soviet MCV(H),09000113-G,<all>,10,1,NACNST,0500000000000000000000000000000000000000000000000000000000000000,500.000000,500.000000,500.000000,1,0,2,0,<none>,0,0,1",
    "06000290-G=Yuri MCV(H),09000114-G,<all>,10,1,YACNST,0500000000000000000000000000000000000000000000000000000000000000,500.000000,500.000000,500.000000,1,0,3,0,<none>,0,0,1",
]


def main():
    if not RULES.exists():
        raise SystemExit(f"missing {RULES}")
    BACKUP.parent.mkdir(parents=True, exist_ok=True)
    if not BACKUP.exists():
        shutil.copy2(RULES, BACKUP)
        print("backup ->", BACKUP)

    text = RULES.read_text(encoding="utf-8", errors="replace")
    before = text

    text = re.sub(r"(?mi)^MultiplayerAICM\s*=.*$",
                  "MultiplayerAICM=4000,3000,2000 ;God's Toy AI money coefficient",
                  text, count=1)
    text = re.sub(r"(?mi)^DisableAIParallelProduction\s*=.*$",
                  "DisableAIParallelProduction=no ;God's Toy: AI may clone units",
                  text, count=1)
    text = re.sub(r"(?mi)^EnableAIBuildLimitation\s*=.*$",
                  "EnableAIBuildLimitation=yes",
                  text, count=1)

    # Update AIBasePlanningCount in place for each affected building section.
    for name, count in PLANNING.items():
        m = re.search(rf"(?mi)^\[{name}\]\s*$", text)
        if not m:
            print("warning: section missing", name)
            continue
        start = m.end()
        nxt = re.search(r"(?m)^\[", text[start:])
        end = start + (nxt.start() if nxt else len(text) - start)
        block = text[start:end]
        if re.search(r"(?mi)^AIBasePlanningCount\s*=", block):
            new_block = re.sub(r"(?mi)^AIBasePlanningCount\s*=.*$",
                               f"AIBasePlanningCount={count}", block, count=1)
        else:
            new_block = f"\nAIBasePlanningCount={count}" + block
        text = text[:start] + new_block + text[end:]

    # Append the mode's extra MCV deploy triggers into [AITriggerTypes].
    m = re.search(r"(?mi)^\[AITriggerTypes\]\s*$", text)
    if m:
        start = m.end()
        nxt = re.search(r"(?m)^\[", text[start:])
        end = start + (nxt.start() if nxt else len(text) - start)
        existing = text[start:end].rstrip("\n")
        added = "\n".join(MODE_TRIGGERS)
        text = text[:start] + existing + "\n" + added + "\n" + text[end:]

    RULES.write_text(text, encoding="utf-8")
    print("rules patched:", RULES, "changed:", text != before)


if __name__ == "__main__":
    main()
