#!/usr/bin/env python3
"""Unit tests for scripts/map_intel/static_map_parser.py (offline only)."""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))

from map_intel import static_map_parser as P  # noqa: E402

MAP_2PEAKS = ROOT / 'test-game' / 'Maps' / "Yuri's Revenge" / '2peaks.map'
EXPECTED_2PEAKS = {
    'waypoints': 81, 'terrain': 104, 'structures': 10, 'triggers': 7,
    'events': 7, 'actions': 7, 'tags': 7, 'ai_triggers_enabled': 163,
}
SAMPLE_INI = """; test map
[Header]
StartX=218
StartY=47
Width=75

[Map]
Theater=TEMPERATE
Size=0,0,80,102
LocalSize=2,6,76,90

[Houses]
0=Americans
1=Alliance

[Waypoints]
0=59092
1=127090
78=75085

[Terrain]
92091=TIBTRE01
54099=TIBTRE01
bad=VALUE

[Structures]
1028885=Neutral,CAAIRP,256,80,96,0,None,0,0,1,0,0,None,None,None,0,0

[Triggers]
0AA0427C=Americans,<none>,SFX_Ocean01,0,1,1,1,0

[Events]
0AA0427C=1,13,0,0

[Actions]
0AA0427C=13,99,7,_Amb_OceanLight,0,0

[Tags]
0AA05CDC=0,SFX_Ocean01,0AA0427C

[AITriggerTypesEnable]
0CAD0DCC-G=yes
0CAD0C7C-G=no

[IsoMapPack5]
1=AbCdEf
2=GhIjKl

[SomeUnknownSection]
x=1
"""


class IniParsingTests(unittest.TestCase):
    def test_parse_ini_order_and_entries(self):
        sections, order = P.parse_ini(SAMPLE_INI)
        self.assertEqual(order[:3], ['Header', 'Map', 'Houses'])
        self.assertIn('Waypoints', sections)
        self.assertEqual(len(sections['Waypoints']), 3)
        self.assertEqual(sections['Header'][0], ('StartX', '218'))

    def test_decode_packed_cell(self):
        self.assertEqual(P.decode_packed_cell('59092'), (92, 59))
        self.assertEqual(P.decode_packed_cell('127090'), (90, 127))
        self.assertIsNone(P.decode_packed_cell('bad'))
        self.assertIsNone(P.decode_packed_cell(None))

    def test_read_text_encodings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'x.map'
            path.write_text('[Header]\nStartX=1\n', encoding='utf-8')
            text, encoding = P.read_map_text(path)
            self.assertIn('StartX=1', text)
            self.assertIn(encoding, ('utf-8-sig', 'utf-8'))


class SectionParserTests(unittest.TestCase):
    def setUp(self):
        self.sections, _ = P.parse_ini(SAMPLE_INI)

    def test_header_map(self):
        header = P.parse_header(self.sections['Header'])
        self.assertEqual(header['start'], (218, 47))
        self.assertEqual(header['width'], 75)
        info = P.parse_map_info(self.sections['Map'])
        self.assertEqual(info['size'], {'x': 0, 'y': 0, 'width': 80, 'height': 102})
        self.assertEqual(info['local_size']['width'], 76)

    def test_waypoints(self):
        waypoints = P.parse_waypoints(self.sections['Waypoints'])
        self.assertEqual(waypoints['count'], 3)
        self.assertEqual(waypoints['starts_0_7'][0]['x'], 92)
        self.assertEqual(waypoints['starts_0_7'][0]['y'], 59)
        self.assertEqual(len(waypoints['invalid']), 0)

    def test_terrain(self):
        terrain = P.parse_terrain(self.sections['Terrain'])
        self.assertEqual(terrain['count'], 3)
        self.assertEqual(terrain['items'][0]['cell'], [91, 92])
        self.assertEqual(len(terrain['invalid']), 1)

    def test_structures(self):
        structures = P.parse_structures(self.sections['Structures'])
        self.assertEqual(structures['count'], 1)
        self.assertEqual(structures['items'][0]['cell'], [80, 96])
        self.assertEqual(structures['items'][0]['owner'], 'Neutral')
        self.assertEqual(structures['items'][0]['type'], 'CAAIRP')

    def test_trigger_system_linkage(self):
        system = P.parse_trigger_system(self.sections)
        self.assertEqual(system['counts'], {'triggers': 1, 'events': 1,
                                            'actions': 1, 'tags': 1})
        linkage = system['linkage'][0]
        self.assertEqual(linkage['tag_id'], '0AA05CDC')
        self.assertEqual(linkage['trigger_id'], '0AA0427C')
        self.assertEqual(linkage['events'][0]['fields'][0], '1')
        self.assertEqual(system['orphan_triggers'], [])

    def test_ai_trigger_enable(self):
        enabled = P.parse_aitrigger_enable(self.sections['AITriggerTypesEnable'])
        self.assertEqual(enabled['count'], 2)
        self.assertEqual(enabled['enabled_count'], 1)

    def test_packed_metadata(self):
        packed = P.parse_packed(self.sections)
        self.assertEqual(packed['IsoMapPack5']['entries'], 2)
        self.assertEqual(packed['IsoMapPack5']['decoder'], 'UNKNOWN')
        self.assertEqual(packed['OverlayPack']['entries'], 0)


class RealMapTests(unittest.TestCase):
    @unittest.skipUnless(MAP_2PEAKS.exists(), '2peaks.map not found')
    def test_2peaks_counts(self):
        parsed = P.parse_static_map(MAP_2PEAKS)
        self.assertEqual(parsed['waypoints']['count'], EXPECTED_2PEAKS['waypoints'])
        self.assertEqual(parsed['terrain']['count'], EXPECTED_2PEAKS['terrain'])
        self.assertEqual(parsed['structures']['count'], EXPECTED_2PEAKS['structures'])
        self.assertEqual(parsed['trigger_system']['counts']['triggers'],
                         EXPECTED_2PEAKS['triggers'])
        self.assertEqual(parsed['ai_triggers']['enabled_count'],
                         EXPECTED_2PEAKS['ai_triggers_enabled'])
        starts = parsed['waypoints']['starts_0_7']
        self.assertGreaterEqual(len(starts), 2)
        self.assertLess(starts[0]['x'], 512)
        self.assertLess(starts[0]['y'], 512)
        # structures schema heuristic should resolve the 10 neutral structures
        self.assertEqual(parsed['structures']['cell_resolved'], 10)
        # packed metadata present but not decoded
        self.assertEqual(parsed['packed']['IsoMapPack5']['decoder'], 'UNKNOWN')

    @unittest.skipUnless(MAP_2PEAKS.exists(), '2peaks.map not found')
    def test_index_build_on_real_maps(self):
        with tempfile.TemporaryDirectory() as tmp:
            index, path = P.build_index([MAP_2PEAKS.parent], tmp,
                                        full_dumps=['2peaks'])
            self.assertGreater(index['map_count'], 0)
            self.assertEqual(index['failed'], [])
            self.assertTrue(path.exists())
            full = pathlib.Path(tmp) / 'maps' / '2peaks.static.json'
            self.assertTrue(full.exists())
            payload = json.loads(full.read_text(encoding='utf-8'))
            self.assertEqual(payload['waypoints']['count'], 81)


if __name__ == '__main__':
    unittest.main(verbosity=2)
