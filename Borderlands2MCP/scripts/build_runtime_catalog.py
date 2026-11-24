"""Build the offline definition catalog consumed by the standalone in-game mod."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime_client import item_definition


def main():
    catalog = {}
    skipped = []
    for index, row in enumerate(json.loads((ROOT/'data/special_weapons.json').read_text(encoding='utf-8'))):
        try:
            catalog['{} · {} [{}]'.format(row.get('rarity', ''), row['name'], index+1)] = item_definition(row['code'], 1)
        except (ValueError, KeyError) as exc:
            skipped.append({'name':row.get('name'),'reason':str(exc)})
    root = ROOT/'launcher-assets/qol'
    (root/'SessionInspector/equipment_catalog.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    manifest_path = root/'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    for name in ('__init__.py','runtime_actions.py','equipment_catalog.json'):
        manifest['SessionInspector'][name] = hashlib.sha256((root/'SessionInspector'/name).read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'included':len(catalog),'skipped':skipped},ensure_ascii=False))


if __name__ == '__main__':
    main()
