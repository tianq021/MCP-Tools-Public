"""Bounded, read-only inventory reflection snapshot; no game function calls."""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools')]
from process_reader import Reader, game_pids, modules
from reflection import Reflection
from feature_probe import sha256, safe_name, outer_chain
from code_patches import VERIFIED_SHA256

config = json.loads((ROOT / 'trainer_config.json').read_text())
assert sha256(config['expected_executable']) == VERIFIED_SHA256, 'Executable version mismatch'
pids = game_pids()
assert len(pids) == 1, pids
signatures = json.loads((ROOT / 'research/reflection_signatures.local.json').read_text())
schemas, instances, errors = [], [], []
with Reader(pids[0], config['expected_executable']) as reader:
    base = next(m['base'] for m in modules(reader.pid) if m['path'].casefold() == reader.path.casefold())
    r = Reflection(reader, base, signatures)
    classes = {}
    for index, obj, flags, cls in r.object_headers():
        try:
            if cls not in classes:
                classes[cls] = safe_name(r, cls)
            kind = classes[cls]
            is_schema = kind in ('Class', 'ScriptStruct', 'Function')
            if not is_schema and not any(w in kind.casefold() for w in ('inventory', 'weapon')):
                continue
            name = safe_name(r, obj)
            if is_schema:
                if not any(w in name.casefold() for w in ('inventory', 'serial', 'weaponpart', 'equippedweapon')):
                    continue
                row = dict(name=name, kind=kind, address=obj, properties=list(r.props(obj)), outer=outer_chain(r,obj))
                if kind == 'Function':
                    native = r.u64(obj+0xd8)
                    row['native_rva'] = hex(native-base)
                schemas.append(row)
            elif len(instances)<2500:
                instances.append(dict(name=name, kind=kind, address=obj, cls=cls, outer=outer_chain(r,obj)))
        except (OSError, ValueError, UnicodeError) as exc:
            if len(errors)<20: errors.append(str(exc))
    result = dict(observed_at=datetime.now(timezone.utc).isoformat(), read_only=True, process=reader.identity(), base=base, schemas=schemas, instances=instances, errors=errors)
dest=ROOT/'research/current_inventory_runtime.json'
dest.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(dict(output=str(dest),schemas=len(schemas),instances=len(instances),errors=errors)))
