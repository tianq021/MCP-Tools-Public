#!/usr/bin/env python3
"""W0: static RA2/YR map parser (offline, text INI only).

Parses `.map` / `.mpr` (text INI) into structured JSON. Does NOT decode packed
sections (IsoMapPack5/OverlayPack/OverlayDataPack/PreviewPack); those are
recorded as metadata only (decoder=UNKNOWN).

Verified encodings used here:
* Waypoints: cell = (value % 1000, value // 1000)  -> X + 1000*Y
  (source: scripts/dsh_episode/runner.py:152-164 public_start_cells)
* Terrain keys: same packed formula (INFERRED by DSH 2026-11-01, validated by
  bounds statistics; NOT yet cross-checked against bridge map_data).
* Structures values: comma fields; observed schema
  owner,type,strength,cell_x,cell_y,facing,tag,... (INFERRED + raw kept).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
import time
from collections import Counter, OrderedDict

ROOT = pathlib.Path(r'E:\RA2MCP')
DEFAULT_ROOTS = [ROOT / 'test-game' / 'Maps']
TEXT_EXT = {'.map', '.mpr'}
PACKED_SECTIONS = ('IsoMapPack5', 'OverlayPack', 'OverlayDataPack', 'PreviewPack')
COUNTRY_SECTIONS = ('Americans', 'Alliance', 'French', 'Germans', 'British',
                    'Africans', 'Arabs', 'Confederation', 'Russians',
                    'YuriCountry', 'GDI', 'Nod', 'Neutral', 'Special')
KNOWN_SECTIONS = (
    'Header', 'SpecialFlags', 'Ranking', 'Basic', 'Lighting', 'Preview',
    'Map', 'Waypoints', 'Terrain', 'Structures', 'AITriggerTypesEnable',
    'Triggers', 'Events', 'Actions', 'Tags', 'Digest', 'Houses',
) + PACKED_SECTIONS + COUNTRY_SECTIONS


# ---------------------------------------------------------------------------
# low level
# ---------------------------------------------------------------------------
def read_map_text(path):
    data = pathlib.Path(path).read_bytes()
    for encoding in ('utf-8-sig', 'gbk', 'latin-1'):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode('latin-1', errors='replace'), 'latin-1'


def parse_ini(text):
    """Return (sections: OrderedDict[str, list[(key, value)]], raw_section_order)."""
    sections = OrderedDict()
    order = []
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(';') or line.startswith('#'):
            continue
        match = re.match(r'^\[(.+?)\]\s*$', line)
        if match:
            current = match.group(1).strip()
            if current not in sections:
                sections[current] = []
                order.append(current)
            continue
        if current is None or '=' not in line:
            continue
        key, _, value = line.partition('=')
        sections[current].append((key.strip(), value.strip()))
    return sections, order


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def decode_packed_cell(value):
    """X + 1000*Y (waypoint-verified formula). Returns None when not an int."""
    try:
        encoded = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return encoded % 1000, encoded // 1000


def _to_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# section parsers
# ---------------------------------------------------------------------------
def parse_header(section):
    out = {}
    for key, value in section:
        out[key] = value
    header = {'raw': out}
    start = None
    if 'StartX' in out and 'StartY' in out:
        start = (_to_int(out.get('StartX')), _to_int(out.get('StartY')))
    header['start'] = start
    header['width'] = _to_int(out.get('Width'))
    header['height'] = _to_int(out.get('Height'))
    return header


def parse_map_info(section):
    info = {'raw': dict(section)}
    size = info['raw'].get('Size')
    if size:
        parts = [part.strip() for part in size.split(',')]
        if len(parts) == 4:
            numbers = [_to_int(part) for part in parts]
            if all(number is not None for number in numbers):
                info['size'] = {'x': numbers[0], 'y': numbers[1],
                                'width': numbers[2], 'height': numbers[3]}
    local = info['raw'].get('LocalSize')
    if local:
        parts = [part.strip() for part in local.split(',')]
        if len(parts) == 4:
            numbers = [_to_int(part) for part in parts]
            if all(number is not None for number in numbers):
                info['local_size'] = {'x': numbers[0], 'y': numbers[1],
                                      'width': numbers[2], 'height': numbers[3]}
    return info


def parse_waypoints(section):
    waypoints = OrderedDict()
    invalid = []
    for key, value in section:
        index = _to_int(key)
        cell = decode_packed_cell(value)
        if index is None or cell is None:
            invalid.append({'key': key, 'value': value})
            continue
        waypoints[index] = {'raw': value, 'x': cell[0], 'y': cell[1]}
    starts = [waypoints[index] for index in sorted(waypoints) if index < 8]
    return {'count': len(waypoints), 'items': waypoints,
            'starts_0_7': starts, 'invalid': invalid,
            'max_index': max(waypoints) if waypoints else None}


def parse_terrain(section):
    items = []
    invalid = []
    for key, value in section:
        cell = decode_packed_cell(key)
        record = {'raw_key': key, 'type': value, 'cell': None,
                  'x': None, 'y': None}
        if cell is None:
            invalid.append({'key': key, 'value': value})
        else:
            record['x'], record['y'] = cell
            record['cell'] = [cell[0], cell[1]]
        items.append(record)
    inside = sum(1 for item in items if item['x'] is not None
                 and 0 <= item['x'] < 512 and 0 <= item['y'] < 512)
    return {'count': len(items), 'items': items, 'invalid': invalid,
            'bounds_check': {'within_0_511': inside, 'outside': len(items) - inside},
            'types': dict(Counter(item['type'] for item in items).most_common(20))}


def parse_structures(section):
    items = []
    invalid = []
    for key, value in section:
        fields = [field.strip() for field in value.split(',')]
        record = {'raw_key': key, 'raw_value': value, 'fields': fields,
                  'owner': fields[0] if fields else None,
                  'type': fields[1] if len(fields) > 1 else None,
                  'cell': None, 'x': None, 'y': None,
                  'schema': 'owner,type,strength,x,y,facing,tag,... (INFERRED)'}
        if len(fields) >= 5:
            x, y = _to_int(fields[3]), _to_int(fields[4])
            if x is not None and y is not None and 0 <= x < 512 and 0 <= y < 512:
                record['x'], record['y'], record['cell'] = x, y, [x, y]
        if record['x'] is None:
            invalid.append({'key': key, 'value': value})
        items.append(record)
    return {'count': len(items), 'items': items,
            'cell_resolved': sum(1 for item in items if item['x'] is not None),
            'unresolved_cell': invalid,
            'types': dict(Counter(item['type'] for item in items).most_common(20))}


def parse_trigger_system(sections):
    triggers = OrderedDict()
    for key, value in sections.get('Triggers') or []:
        fields = [field.strip() for field in value.split(',')]
        triggers[key] = {'raw': value, 'fields': fields,
                         'house': fields[0] if fields else None,
                         'name': fields[2] if len(fields) > 2 else None}
    events = OrderedDict()
    for key, value in sections.get('Events') or []:
        fields = [field.strip() for field in value.split(',')]
        events.setdefault(key, []).append({'raw': value, 'fields': fields,
                                           'event_type': _to_int(fields[0]) if fields else None})
    actions = OrderedDict()
    for key, value in sections.get('Actions') or []:
        fields = [field.strip() for field in value.split(',')]
        actions.setdefault(key, []).append({'raw': value, 'fields': fields,
                                            'action_type': _to_int(fields[0]) if fields else None})
    tags = OrderedDict()
    for key, value in sections.get('Tags') or []:
        fields = [field.strip() for field in value.split(',')]
        tag = {'raw': value, 'fields': fields,
               'repeating': fields[0] if fields else None,
               'tag_name': fields[1] if len(fields) > 1 else None,
               'trigger_id': fields[2] if len(fields) > 2 else None}
        tags[key] = tag
    # linkage: tag -> trigger -> events/actions
    linkage = []
    for tag_key, tag in tags.items():
        trigger_id = tag.get('trigger_id')
        linkage.append({
            'tag_id': tag_key, 'tag_name': tag.get('tag_name'),
            'trigger_id': trigger_id,
            'trigger': triggers.get(trigger_id),
            'events': events.get(trigger_id) or [],
            'actions': actions.get(trigger_id) or [],
        })
    orphan_triggers = [tid for tid in triggers if tid not in
                       {tag.get('trigger_id') for tag in tags.values()}]
    return {'triggers': triggers, 'events': events, 'actions': actions,
            'tags': tags, 'linkage': linkage,
            'orphan_triggers': orphan_triggers,
            'counts': {'triggers': len(triggers), 'events': sum(len(v) for v in events.values()),
                       'actions': sum(len(v) for v in actions.values()), 'tags': len(tags)}}


def parse_aitrigger_enable(section):
    enabled = {}
    for key, value in section:
        enabled[key] = str(value).strip().lower() in ('yes', 'true', '1')
    return {'count': len(enabled), 'enabled_count': sum(1 for value in enabled.values() if value),
            'items': enabled}


def parse_packed(sections):
    packed = {}
    for name in PACKED_SECTIONS:
        entries = sections.get(name) or []
        payload_chars = sum(len(value) for _, value in entries)
        packed[name] = {
            'entries': len(entries), 'payload_chars': payload_chars,
            'decoder': 'UNKNOWN',
            'first_entry_prefix': (entries[0][1][:60] if entries else None),
        }
    return packed


def parse_houses(sections):
    houses = {}
    for key, value in sections.get('Houses') or []:
        houses[key] = value
    countries = {name: dict(sections.get(name) or []) for name in COUNTRY_SECTIONS
                 if name in sections}
    return {'houses': houses, 'country_sections': countries}


# ---------------------------------------------------------------------------
# top level
# ---------------------------------------------------------------------------
def parse_static_map(path):
    path = pathlib.Path(path)
    text, encoding = read_map_text(path)
    sections, order = parse_ini(text)
    warnings = []
    result = {
        'schema': 'dsh.map_intel.static_map/1',
        'path': str(path),
        'name': path.name,
        'stem': path.stem,
        'size': path.stat().st_size,
        'sha256': sha256_file(path),
        'encoding': encoding,
        'sections': order,
        'counts': {name: len(entries) for name, entries in sections.items()},
    }
    result['header'] = parse_header(sections.get('Header') or [])
    result['map'] = parse_map_info(sections.get('Map') or [])
    result['basic'] = dict(sections.get('Basic') or [])
    result['special_flags'] = dict(sections.get('SpecialFlags') or [])
    result['lighting'] = dict(sections.get('Lighting') or [])
    result['houses'] = parse_houses(sections)
    result['waypoints'] = parse_waypoints(sections.get('Waypoints') or [])
    result['terrain'] = parse_terrain(sections.get('Terrain') or [])
    result['structures'] = parse_structures(sections.get('Structures') or [])
    result['trigger_system'] = parse_trigger_system(sections)
    result['ai_triggers'] = parse_aitrigger_enable(sections.get('AITriggerTypesEnable') or [])
    result['packed'] = parse_packed(sections)

    # map-embedded rule/AI sections (stock maps can override BuildingTypes/TeamTypes/...)
    embedded = {}
    for name in order:
        if name in KNOWN_SECTIONS:
            continue
        embedded[name] = dict(sections[name])
    result['embedded_sections'] = embedded

    # coordinate bounds summary (cell formula validation input)
    def _coords(items):
        iterable = items.values() if isinstance(items, dict) else items
        return [(item['x'], item['y']) for item in iterable
                if item.get('x') is not None and item.get('y') is not None]

    bounds = {}
    for group in ('waypoints', 'terrain', 'structures'):
        coords = _coords(result[group]['items'])
        bounds[group] = {
            'count': len(coords),
            'max_x': max((c[0] for c in coords), default=None),
            'max_y': max((c[1] for c in coords), default=None),
            'ge_512': sum(1 for c in coords if c[0] >= 512 or c[1] >= 512),
            'gt_182': sum(1 for c in coords if c[0] > 182 or c[1] > 182),
        }
    result['coord_bounds'] = bounds

    # warnings / data quality
    if result['structures']['unresolved_cell']:
        warnings.append(f"{len(result['structures']['unresolved_cell'])} structure cells unresolved")
    suspicious = sum(group['ge_512'] for group in bounds.values())
    if suspicious:
        warnings.append(f'{suspicious} coordinates >=512 (suspicious decode/extreme map size)')
    if result['terrain']['invalid']:
        warnings.append(f"{len(result['terrain']['invalid'])} terrain keys undecodable")
    result['warnings'] = warnings
    return result


def summarize(parsed):
    """Compact index row (no per-item arrays)."""
    return {
        'path': parsed['path'], 'name': parsed['name'], 'stem': parsed['stem'],
        'size': parsed['size'], 'sha256': parsed['sha256'],
        'encoding': parsed['encoding'],
        'sections': parsed['sections'],
        'counts': parsed['counts'],
        'header': {'width': parsed['header'].get('width'),
                   'height': parsed['header'].get('height'),
                   'start': parsed['header'].get('start')},
        'map': parsed['map'],
        'theater': parsed['map']['raw'].get('Theater'),
        'waypoints': parsed['waypoints']['count'],
        'starts_0_7': parsed['waypoints']['starts_0_7'],
        'terrain': parsed['terrain']['count'],
        'structures': parsed['structures']['count'],
        'structures_cell_resolved': parsed['structures']['cell_resolved'],
        'triggers': parsed['trigger_system']['counts']['triggers'],
        'events': parsed['trigger_system']['counts']['events'],
        'actions': parsed['trigger_system']['counts']['actions'],
        'tags': parsed['trigger_system']['counts']['tags'],
        'ai_triggers_enabled': parsed['ai_triggers']['enabled_count'],
        'packed': parsed['packed'],
        'coord_bounds': parsed['coord_bounds'],
        'embedded_sections': {name: len(entries)
                              for name, entries in parsed['embedded_sections'].items()},
        'warnings': parsed['warnings'],
    }


def iter_map_files(roots):
    for root in roots:
        root = pathlib.Path(root)
        if not root.exists():
            continue
        for path in sorted(root.rglob('*')):
            if path.is_file() and path.suffix.lower() in TEXT_EXT:
                yield path


def build_index(roots, out_dir, full_dumps=(), verbose=False):
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    maps_dir = out_dir / 'maps'
    maps_dir.mkdir(exist_ok=True)
    rows, failed = [], []
    full_set = {name.lower() for name in full_dumps}
    files = list(iter_map_files(roots))
    for path in files:
        try:
            parsed = parse_static_map(path)
            row = summarize(parsed)
            rows.append(row)
            if path.stem.lower() in full_set or path.name.lower() in full_set:
                target = maps_dir / f'{path.stem}.static.json'
                target.write_text(json.dumps(parsed, ensure_ascii=False, indent=1),
                                  encoding='utf-8')
        except Exception as exc:  # noqa: BLE001
            failed.append({'path': str(path), 'error': repr(exc)[:300]})
            if verbose:
                print('FAILED', path, repr(exc)[:200])
    index = {
        'schema': 'dsh.map_intel.maps_index/1',
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'roots': [str(root) for root in roots],
        'map_count': len(files),
        'parsed': len(rows),
        'failed': failed,
        'section_coverage': dict(Counter(section for row in rows for section in row['sections'])),
        'theater_counts': dict(Counter(row.get('theater') for row in rows)),
        'totals': {
            'waypoints': sum(row['waypoints'] for row in rows),
            'terrain': sum(row['terrain'] for row in rows),
            'structures': sum(row['structures'] for row in rows),
            'triggers': sum(row['triggers'] for row in rows),
            'events': sum(row['events'] for row in rows),
            'actions': sum(row['actions'] for row in rows),
            'tags': sum(row['tags'] for row in rows),
            'ai_triggers_enabled': sum(row['ai_triggers_enabled'] for row in rows),
        },
        'warnings_count': sum(len(row['warnings']) for row in rows),
        'maps': rows,
    }
    index_path = out_dir / 'maps_index.json'
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding='utf-8')
    return index, index_path


def main(argv=None):
    parser = argparse.ArgumentParser(description='W0 static map parser (offline)')
    parser.add_argument('--root', action='append', default=None,
                        help='map root directory (repeatable); default test-game/Maps')
    parser.add_argument('--out', default=str(ROOT / 'reports' / 'map-data-2026-11-01'))
    parser.add_argument('--full', action='append', default=[],
                        help='map stem/name to dump full static JSON (repeatable)')
    parser.add_argument('--map', default=None, help='parse a single map to stdout')
    args = parser.parse_args(argv)
    if args.map:
        parsed = parse_static_map(args.map)
        print(json.dumps(parsed, ensure_ascii=False, indent=1))
        return 0
    roots = [pathlib.Path(item) for item in (args.root or DEFAULT_ROOTS)]
    index, index_path = build_index(roots, args.out, full_dumps=args.full, verbose=True)
    print(json.dumps({key: index[key] for key in
                      ('map_count', 'parsed', 'failed', 'theater_counts',
                       'totals', 'warnings_count')}, ensure_ascii=False, indent=1))
    print('index:', index_path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
