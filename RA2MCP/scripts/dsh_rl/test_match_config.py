import pathlib
import tempfile
import unittest

import match_config as M

SAMPLE = """\ufeff; header comment
[Settings]
Name=DSH-Probe
Side=0
; human color comment
Color=2
AIPlayers=1
PlayerCount=2
AIDifficulty=1
Credits=10000
Scenario=spawnmap.ini
UIMapName=[2] Twin Peaks

[HouseColors]
Multi1=2
Multi2=2

[HouseCountries]
Multi1=0
Multi2=8

[HouseHandicaps]
Multi1=0
Multi2=0
"""


def write_map(path, name, starts):
    lines = ['[Header]', 'Width=80', 'Height=80', '', '[Basic]', f'Name={name}', '', '[Waypoints]']
    for start in starts:
        lines.append(f'{start}=59092')
    path.write_text('\r\n'.join(lines) + '\r\n', encoding='utf-8')


def make_maps_dir(root):
    maps = root / 'maps'
    maps.mkdir()
    write_map(maps / '2peaks.map', '[2] Twin Peaks', [0, 1])
    write_map(maps / '4_divide_and_conquer.map', '[4] Divide and Conquer', [0, 1, 2, 3])
    return maps


class TestMatchConfig(unittest.TestCase):
    def test_parse_reads_sections_and_skips_comments(self):
        values = M.parse(SAMPLE)
        self.assertEqual(values[('Settings', 'Color')], '2')
        self.assertEqual(values[('Settings', 'Name')], 'DSH-Probe')
        self.assertEqual(values[('HouseColors', 'Multi1')], '2')

    def test_set_values_keeps_comments_and_other_lines(self):
        text = M.set_values(SAMPLE, {('Settings', 'Color'): '3', ('HouseColors', 'Multi1'): '1'})
        self.assertIn('; human color comment', text)
        self.assertIn('Color=3', text)
        self.assertIn('Multi1=1', text)
        self.assertIn('Name=DSH-Probe', text)

    def test_set_values_appends_missing_keys(self):
        text = M.set_values(SAMPLE, {('Settings', 'Seed'): '20261028'})
        self.assertIn('Seed=20261028', text)
        self.assertGreater(text.index('Seed=20261028'), text.index('[Settings]'))
        self.assertLess(text.index('Seed=20261028'), text.index('[HouseColors]'))

    def test_normalize_bool_and_numbers(self):
        spec_bool = dict(section='Settings', keys=['Crates'], label='箱子', kind='bool')
        self.assertEqual(M.normalize(spec_bool, 'yes'), 'Yes')
        self.assertEqual(M.normalize(spec_bool, '0'), 'No')
        spec_int = dict(section='Settings', keys=['Credits'], label='资金', kind='int',
                        minimum=0, maximum=10000)
        self.assertEqual(M.normalize(spec_int, '2500'), '2500')
        with self.assertRaises(ValueError):
            M.normalize(spec_int, '99999')
        spec_enum = dict(section='Settings', keys=['Color'], label='颜色', kind='enum',
                         options=M.COLORS)
        self.assertEqual(M.normalize(spec_enum, '7'), '7')
        with self.assertRaises(ValueError):
            M.normalize(spec_enum, '9')

    def test_validate_rejects_unknown_keys(self):
        with self.assertRaises(ValueError):
            M.validate_updates({('Settings', 'NotASetting'): '1'})

    def test_baseline_is_valid_and_covers_every_editable_key(self):
        normalized = M.validate_updates(M.BASELINE)
        expected = {(spec['section'], key) for spec in M.SPECS for key in spec['keys']}
        self.assertEqual(set(normalized), expected)

    def test_locked_keys_are_not_editable(self):
        for spec in M.LOCKED:
            for key in spec['keys']:
                with self.assertRaises(ValueError):
                    M.validate_updates({(spec['section'], key): '1'})
        for key in ('Scenario', 'UIMapName'):
            with self.assertRaises(ValueError):
                M.validate_updates({('Settings', key): 'x'})

    def test_read_map_info(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            maps = make_maps_dir(root)
            info = M.read_map_info(maps / '4_divide_and_conquer.map')
            self.assertEqual(info['players'], 4)
            self.assertEqual(info['name'], '[4] Divide and Conquer')
            self.assertEqual((info['width'], info['height']), (80, 80))

    def test_save_writes_config_copy_history_and_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            maps = make_maps_dir(root)
            config = root / 'spawn.ini'
            copy = root / 'test-game' / 'spawn.ini'
            copy.parent.mkdir()
            config.write_text(SAMPLE, encoding='utf-8')
            copy.write_text(SAMPLE, encoding='utf-8')
            new_rev, old_rev = M.save(
                {('Settings', 'Credits'): '2500', ('HouseColors', 'Multi1'): '1'},
                path=config, copy_path=copy, history_dir=root / 'history',
                maps_dir=maps, map_meta_path=root / 'map.json')
            text = config.read_text(encoding='utf-8')
            self.assertIn('Credits=2500', text)
            self.assertEqual(text, copy.read_text(encoding='utf-8'))
            self.assertTrue((root / 'history' / (old_rev + '.ini')).exists())
            self.assertEqual(new_rev, M.file_revision(config))
            # 1 AI keeps the 2-player map and matching player count.
            self.assertIn('PlayerCount=2', text)
            self.assertIn('AIPlayers=1', text)
            self.assertIn('UIMapName=[2] Twin Peaks', text)
            self.assertEqual((copy.parent / 'spawnmap.ini').read_text(encoding='utf-8'),
                             (maps / '2peaks.map').read_text(encoding='utf-8'))

    def test_save_two_ais_switches_to_four_player_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            maps = make_maps_dir(root)
            config = root / 'spawn.ini'
            copy = root / 'test-game' / 'spawn.ini'
            copy.parent.mkdir()
            config.write_text(SAMPLE, encoding='utf-8')
            copy.write_text(SAMPLE, encoding='utf-8')
            M.save({('Settings', 'AIPlayers'): '2', ('HouseColors', 'Multi1'): '1'},
                   path=config, copy_path=copy, history_dir=root / 'history',
                   maps_dir=maps, map_meta_path=root / 'map.json')
            text = config.read_text(encoding='utf-8')
            self.assertIn('AIPlayers=2', text)
            self.assertIn('PlayerCount=3', text)
            self.assertIn('UIMapName=[4] Divide and Conquer', text)
            self.assertEqual((copy.parent / 'spawnmap.ini').read_text(encoding='utf-8'),
                             (maps / '4_divide_and_conquer.map').read_text(encoding='utf-8'))
            self.assertEqual(M.current_map(maps, root / 'map.json')['players'], 4)

    def test_save_rejects_ai_count_without_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            maps = root / 'maps'
            maps.mkdir()
            write_map(maps / '2peaks.map', '[2] Twin Peaks', [0, 1])
            config = root / 'spawn.ini'
            config.write_text(SAMPLE, encoding='utf-8')
            with self.assertRaises(ValueError):
                M.save({('Settings', 'AIPlayers'): '2'}, path=config, copy_path=None,
                       history_dir=root / 'history', maps_dir=maps,
                       map_meta_path=root / 'map.json')

    def test_active_status_returns_dict(self):
        status = M.active_status()
        self.assertIn('saved', status)
        self.assertIn('message', status)
        self.assertIn('map', status)

    def test_countries_cover_three_sides(self):
        sides = {country[4] for country in M.COUNTRIES}
        self.assertEqual(sides, {'盟军', '苏联', '尤里'})
        self.assertEqual([c[0] for c in M.COUNTRIES], list(range(10)))

    def test_every_manual_option_has_detailed_help(self):
        for spec in M.SPECS:
            if spec.get('hidden'):
                continue
            self.assertIn('help', spec, spec['label'])
            self.assertGreaterEqual(len(spec['help']), 30, spec['label'])

    def test_load_match_state_reads_saved_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            config = root / 'spawn.ini'
            text = SAMPLE.replace('Side=0', 'Side=8')
            text = text.replace('Multi2=8', 'Multi2=5')
            text += '\n[HouseCountries]\nMulti3=0\n'
            config.write_text(text, encoding='utf-8')
            teams = root / 'teams.json'
            teams.write_text('{"player": 1, "ai": [2, 2, 0]}', encoding='utf-8')
            state = M.load_match_state(config, teams)
            self.assertEqual(state['player_country'], 8)
            self.assertEqual(state['ai_countries'][:2], [5, 0])
            self.assertEqual(state['teams'], {'player': 1, 'ai': [2, 2, 0]})

    def test_validate_match_state_rejects_bad_values(self):
        good = {'player_country': 0, 'ai_countries': [8, 5, 0],
                'teams': {'player': 1, 'ai': [2, 2, 0]}}
        self.assertEqual(M.validate_match_state(good, 2)['player_team'], 1)
        with self.assertRaises(ValueError):
            M.validate_match_state(dict(good, player_country=99), 2)
        with self.assertRaises(ValueError):
            M.validate_match_state(dict(good, teams={'player': 9, 'ai': [0, 0, 0]}), 2)

    def test_apply_alliances_writes_team_blocks_and_replaces_old(self):
        text = SAMPLE + '\n[Multi2_Alliances]\nHouseAllyOne=0\n'
        new_text = M.apply_alliances(text, 1, [2, 2], ending='\n')
        self.assertEqual(new_text.count('[Multi2_Alliances]'), 1)
        self.assertIn('[Multi2_Alliances]\nHouseAllyOne=2', new_text)
        self.assertIn('[Multi3_Alliances]\nHouseAllyOne=1', new_text)
        self.assertNotIn('[Multi1_Alliances]', new_text)  # team 1 has no allies
        # Teammates 1 and 2 share a team, so both blocks reference each other.
        no_teams = M.apply_alliances(new_text, 0, [0, 0], ending='\n')
        self.assertNotIn('_Alliances', no_teams)

    def test_save_with_match_state_writes_countries_and_teams(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            maps = make_maps_dir(root)
            config = root / 'spawn.ini'
            copy = root / 'test-game' / 'spawn.ini'
            copy.parent.mkdir()
            config.write_text(SAMPLE, encoding='utf-8')
            copy.write_text(SAMPLE, encoding='utf-8')
            match = {'player_country': 8, 'ai_countries': [0, 5, 0],
                     'teams': {'player': 1, 'ai': [2, 2, 0]}}
            M.save({('Settings', 'AIPlayers'): '2', ('HouseColors', 'Multi1'): '1'},
                   path=config, copy_path=copy, history_dir=root / 'history',
                   maps_dir=maps, map_meta_path=root / 'map.json',
                   match=match, teams_path=root / 'teams.json')
            text = config.read_text(encoding='utf-8')
            self.assertIn('Side=8', text)
            self.assertIn('Multi2=0', text)   # AI 1 country
            self.assertIn('Multi3=5', text)   # AI 2 country (African Empire)
            self.assertIn('[Multi2_Alliances]', text)
            self.assertIn('[Multi3_Alliances]', text)
            self.assertTrue((root / 'teams.json').exists())
            state = M.load_match_state(config, root / 'teams.json')
            self.assertEqual(state['player_country'], 8)
            self.assertEqual(state['teams']['ai'][:2], [2, 2])


if __name__ == '__main__':
    unittest.main()
