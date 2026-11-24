"""Quick inspection of AI INI structures (aimd.ini / Extreme AI.ini)."""
import pathlib
import re
import sys

def read_sections(path):
    sections = {}
    order = []
    current = None
    for raw in path.read_text(encoding="gbk", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        m = re.match(r"^\[(.+?)\]\s*$", line)
        if m:
            current = m.group(1)
            if current not in sections:
                sections[current] = []
                order.append(current)
            continue
        if current is None or "=" not in line:
            continue
        k, _, v = line.partition("=")
        sections[current].append((k.strip(), v.strip()))
    return sections, order


def main():
    aimd = pathlib.Path(r"E:\hj\Utilities\aimd.ini")
    secs, order = read_sections(aimd)
    print("aimd sections:", len(order))
    print("first 10 section names:", order[:10])
    for name in ("TaskForces", "ScriptTypes", "TeamTypes", "AITriggerTypes", "AI"):
        print(f"\n=== [{name}] present={name in secs} ===")
        for k, v in secs.get(name, [])[:6]:
            print("  ", k, "=", v[:160])

    def dump(section_id, limit=12):
        print(f"\n--- [{section_id}] ---")
        for k, v in secs.get(section_id, [])[:limit]:
            print("  ", k, "=", v[:180])

    for name in ("TaskForces", "ScriptTypes", "TeamTypes"):
        entries = secs.get(name, [])
        if entries:
            sid = entries[0][1].split(",")[0].strip()
            dump(sid)

    ext = pathlib.Path(r"E:\hj\Yuris Revenge\Game\INI\Game Options\AI\Extreme AI.ini")
    if ext.exists():
        es, eorder = read_sections(ext)
        print("\nExtreme AI sections:", len(eorder))
        print("names:", eorder[:16])
        for name in ("AI", "General", "TaskForces", "ScriptTypes", "TeamTypes", "AITriggerTypes"):
            print(f"\n=== Extreme [{name}] present={name in es} ===")
            for k, v in es.get(name, [])[:6]:
                print("  ", k, "=", v[:160])
        for name in ("TaskForces", "ScriptTypes", "TeamTypes"):
            entries = es.get(name, [])
            if entries:
                sid = entries[0][1].split(",")[0].strip()
                print(f"\n--- Extreme [{name}] first id [{sid}] ---")
                for k, v in es.get(sid, [])[:10]:
                    print("  ", k, "=", v[:160])


if __name__ == "__main__":
    main()
