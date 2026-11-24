#!/usr/bin/env python3
"""Validate the W0 static map dataset and produce a quality report (offline)."""
import collections
import json
import pathlib
import statistics
import sys

ROOT = pathlib.Path(r'E:\RA2MCP')
sys.path.insert(0, str(ROOT / 'scripts'))
from map_intel import static_map_parser as P  # noqa: E402

DATA = ROOT / 'reports' / 'map-data-2026-11-01'
index = json.loads((DATA / 'maps_index.json').read_text(encoding='utf-8'))

report = {
    'schema': 'dsh.map_intel.validation/1',
    'index_generated_at': index.get('generated_at'),
    'map_count': index['map_count'],
    'parsed': index['parsed'],
}
# re-parse everything for deep stats
terra_out_512 = 0
terra_in_182 = 0
terra_total = 0
struct_resolved = 0
struct_total = 0
wp_out_512 = 0
wp_total = 0
warn_counter = collections.Counter()
maxcoord = []
header_vs_max = []
map_size_vs_max = []
terra_types = collections.Counter()
struct_owners = collections.Counter()
linkage_quality = {'tags': 0, 'tags_with_trigger': 0, 'orphan_triggers': 0}
for row in index['maps']:
    path = pathlib.Path(row['path'])
    parsed = P.parse_static_map(path)
    for warning in row.get('warnings') or []:
        key = warning.split(':')[0][:40]
        for token in ('waypoints outside header', 'terrain keys undecodable',
                      'structure cells unresolved', 'unknown sections'):
            if token in warning:
                key = token
                break
        warn_counter[key] += 1
    for item in parsed['terrain']['items']:
        if item['x'] is None:
            continue
        terra_total += 1
        if item['x'] > 511 or item['y'] > 511:
            terra_out_512 += 1
        if item['x'] < 182 and item['y'] < 182:
            terra_in_182 += 1
    terra_types.update(parsed['terrain']['types'])
    for item in parsed['structures']['items']:
        struct_total += 1
        if item['x'] is not None:
            struct_resolved += 1
            struct_owners[item['owner']] += 1
    for item in parsed['waypoints']['items'].values():
        wp_total += 1
        if item['x'] > 511 or item['y'] > 511:
            wp_out_512 += 1
    coords = []
    for group in ('waypoints', 'terrain', 'structures'):
        items = (parsed[group]['items'].values() if isinstance(parsed[group]['items'], dict)
                 else parsed[group]['items'])
        for item in items:
            if item.get('x') is not None and item.get('y') is not None:
                coords.append((item['x'], item['y']))
    if coords:
        max_x = max(item[0] for item in coords)
        max_y = max(item[1] for item in coords)
        maxcoord.append((row['name'], max_x, max_y))
        header = parsed['header'] or {}
        if header.get('width') and header.get('height'):
            header_vs_max.append((row['name'], header['width'], header['height'], max_x, max_y))
        size = (parsed['map'] or {}).get('size')
        if size:
            map_size_vs_max.append((row['name'], size['width'], size['height'], max_x, max_y))
    ts = parsed['trigger_system']
    linkage_quality['tags'] += len(ts['tags'])
    linkage_quality['tags_with_trigger'] += sum(
        1 for tag in ts['tags'].values() if tag.get('trigger_id') in ts['triggers'])
    linkage_quality['orphan_triggers'] += len(ts['orphan_triggers'])

report['terrain'] = {
    'total': terra_total,
    'out_of_0_511': terra_out_512,
    'within_182x182': terra_in_182,
    'within_182_ratio': round(terra_in_182 / terra_total, 4) if terra_total else None,
    'top_types': terra_types.most_common(15),
}
report['structures'] = {
    'total': struct_total, 'cell_resolved': struct_resolved,
    'resolved_ratio': round(struct_resolved / struct_total, 4) if struct_total else None,
    'owners_top': struct_owners.most_common(10),
}
report['waypoints'] = {'total': wp_total, 'out_of_0_511': wp_out_512}
report['warnings'] = warn_counter.most_common()
report['trigger_linkage'] = linkage_quality
report['section_coverage'] = index.get('section_coverage')
# header/size vs observed max coordinate
if header_vs_max:
    header_over = sum(1 for _, w, h, mx, my in header_vs_max if mx >= w or my >= h)
    report['header_vs_observed_max'] = {
        'maps_compared': len(header_vs_max),
        'maps_where_observed_max_exceeds_header': header_over,
        'examples': header_vs_max[:8],
    }
if map_size_vs_max:
    size_over = sum(1 for _, w, h, mx, my in map_size_vs_max if mx >= w or my >= h)
    report['map_size_vs_observed_max'] = {
        'maps_compared': len(map_size_vs_max),
        'maps_where_observed_max_exceeds_size': size_over,
        'examples': map_size_vs_max[:8],
    }
report['observed_max_coord_range'] = {
    'max_x_max': max(item[1] for item in maxcoord) if maxcoord else None,
    'max_y_max': max(item[2] for item in maxcoord) if maxcoord else None,
    'example': maxcoord[:5],
}
# 2peaks cross-check vs pre-existing probe counts
full_2peaks = DATA / 'maps' / '2peaks.static.json'
if full_2peaks.exists():
    payload = json.loads(full_2peaks.read_text(encoding='utf-8'))
    report['2peaks_crosscheck'] = {
        'waypoints': payload['waypoints']['count'],
        'terrain': payload['terrain']['count'],
        'structures': payload['structures']['count'],
        'structures_cell_resolved': payload['structures']['cell_resolved'],
        'triggers': payload['trigger_system']['counts']['triggers'],
        'events': payload['trigger_system']['counts']['events'],
        'actions': payload['trigger_system']['counts']['actions'],
        'tags': payload['trigger_system']['counts']['tags'],
        'ai_triggers_enabled': payload['ai_triggers']['enabled_count'],
        'sha256': payload['sha256'],
        'sections': payload['sections'],
        'expected': {'waypoints': 81, 'terrain': 104, 'structures': 10,
                     'triggers': 7, 'events': 7, 'actions': 7, 'tags': 7,
                     'ai_triggers_enabled': 163},
    }
    exp = report['2peaks_crosscheck']['expected']
    report['2peaks_crosscheck']['all_match'] = all(
        report['2peaks_crosscheck'][key] == value for key, value in exp.items())

(DATA / 'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                     encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=1)[:6000])
