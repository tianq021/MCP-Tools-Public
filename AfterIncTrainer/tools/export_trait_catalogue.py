"""Export Chinese trait descriptions and the matching game component's selection rules."""
import argparse
import csv
import html
import io
import json
import re
from pathlib import Path

import UnityPy


def export(game_data, live_catalogue, destination):
    env = UnityPy.load(str(game_data / "globalgamemanagers"), str(game_data / "resources.assets"))
    manager = next(o.read() for o in env.objects if o.type.name == "ResourceManager")
    assets = dict(manager.m_Container)

    def rows(asset):
        return csv.DictReader(io.StringIO(assets["generated/" + asset].read().m_Script))

    chinese = {r["key"].strip(): r["Chinese Simplified"].strip()
               for r in rows("localization - after inc. - default")}
    mods = {row["ident"]: row for row in rows("new sapphire data - modifiers")}
    catalogue = json.loads(live_catalogue.read_text(encoding="utf-8-sig"))
    for ident, trait in catalogue.items():
        row = mods[ident]
        trait["title"] = chinese[row["title"].strip()]
        desc = chinese[row["description"].strip()].replace("{goal}", chinese["Goal"])
        desc = html.unescape(re.sub(r"</?b>", "", desc))
        if not desc or re.search(r"<[^>]+>|\{[^}]+\}", desc):
            raise ValueError(f"Missing or unresolved description for {ident}: {desc}")
        trait["description"] = desc
    destination.write_text(json.dumps(catalogue, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("game_data", "live_catalogue", "destination"):
        parser.add_argument(name, type=Path)
    export(**vars(parser.parse_args()))
