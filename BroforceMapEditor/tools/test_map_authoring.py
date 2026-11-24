import copy
import unittest
from map_authoring import new_expedition, validate, project_path

class PlacementTests(unittest.TestCase):
    def test_expedition(self):
        self.assertTrue(validate(new_expedition())['valid'])

    def test_rejects_double_height_offset(self):
        p = new_expedition()
        p['doodads'][4]['y'] += 3
        with self.assertRaises(ValueError): validate(p)

    def test_rejects_pit_under_enemy(self):
        p = new_expedition()
        d = p['doodads'][4]
        row = p['height'] - d['y']
        line = list(p['tiles'][row])
        line[d['x']] = '.'
        p['tiles'][row] = ''.join(line)
        with self.assertRaises(ValueError): validate(p)

    def test_rejects_multiline_protocol_input(self):
        p = new_expedition()
        p['name'] = 'map\nop=load'
        with self.assertRaises(ValueError): validate(p)

    def test_rejects_path_escape(self):
        with self.assertRaises(ValueError): project_path('../other')

if __name__ == '__main__': unittest.main()
