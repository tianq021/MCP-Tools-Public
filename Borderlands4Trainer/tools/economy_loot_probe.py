"""Read-only authoritative currency / reward / loot structure probe."""
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools')]
from process_reader import Reader, game_pids, modules
from reflection import Reflection
from feature_probe import locate_local_player, safe_name, outer_chain, sha256
from code_patches import VERIFIED_SHA256


def main():
    config = json.loads((ROOT / 'trainer_config.json').read_text())
    if sha256(config['expected_executable']) != VERIFIED_SHA256:
        raise RuntimeError('Version mismatch')
    pids = game_pids()
    if len(pids) != 1:
        raise RuntimeError(f'Expected one game, found {len(pids)}')
    signatures = json.loads((ROOT / 'research/reflection_signatures.local.json').read_text())
    with Reader(pids[0], config['expected_executable']) as reader:
        main_module = next(m for m in modules(reader.pid) if m['path'].casefold() == reader.path.casefold())
        base = main_module['base']
        r = Reflection(reader, base, signatures)
        local = locate_local_player(r)
        pc = local['controller']
        holder = r.u64(pc + 0x9b0 + 0x280)
        getter = r.u64(r.u64(holder) + 0x2a8)
        currency = dict(holder=holder, holder_name=safe_name(r, holder),
                        holder_class=safe_name(r, r.u64(holder + 16)),
                        holder_properties=list(r.props(r.u64(holder + 16))),
                        getter_rva=getter-base, getter_bytes=reader.read(getter, 128).hex(),
                        holder_raw=reader.read(holder, 4096).hex())
        schemas, instances = [], []
        words = ('itempool', 'lootpool', 'lootlist', 'lootsource', 'droprate', 'rarity',
                 'lootable', 'lootspawn', 'lootrng', 'lootluck', 'currencyholder',
                 'experiencemultiplier', 'experiencestate', 'lootquality', 'lootdrop', 'itemweight')
        for index, obj in r.objects_iter():
            try:
                name = safe_name(r, obj)
                cls = safe_name(r, r.u64(obj + 16))
                if not any(word in (name + ' ' + cls).casefold() for word in words):
                    continue
                if cls in ('Class', 'ScriptStruct', 'Function'):
                    row = dict(name=name, cls=cls, address=obj, properties=list(r.props(obj)), outer=outer_chain(r, obj))
                    if cls == 'Function':
                        native = r.u64(obj + 0xd8)
                        row.update(native_rva=native-base, native_bytes=reader.read(native, 128).hex())
                    schemas.append(row)
                elif len(instances) < 1500:
                    instances.append(dict(name=name, cls=cls, address=obj))
            except (OSError, ValueError, UnicodeError):
                continue
        result = dict(observed_at=datetime.now(timezone.utc).isoformat(), read_only=True,
                      process=reader.identity(), base=base, local=local,
                      currency=currency, schemas=schemas, instances=instances)
    destination = ROOT / 'research/economy_loot_probe.local.json'
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(output=str(destination), getter_rva=hex(currency['getter_rva']),
                          holder_class=currency['holder_class'], schemas=len(schemas), instances=len(instances))))


if __name__ == '__main__':
    main()
