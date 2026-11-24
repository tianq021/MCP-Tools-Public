from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from code_patches import SKILL_NO_SPEND, XP_KILL_MULTIPLIER, identify_variant


class PatchDefinitionsTests(unittest.TestCase):
    def test_patch_lengths(self):
        assert len(SKILL_NO_SPEND.original) == 3
        assert all(len(x) == 3 for x in SKILL_NO_SPEND.variants.values())
        assert len(XP_KILL_MULTIPLIER.original) == 7
        assert all(len(x) == 7 for x in XP_KILL_MULTIPLIER.variants.values())
    
    
    def test_variants(self):
        assert identify_variant(SKILL_NO_SPEND, SKILL_NO_SPEND.variants["on"]) == "on"
        for n in (1,2,5,10):
            assert identify_variant(XP_KILL_MULTIPLIER, XP_KILL_MULTIPLIER.variants[n]) == n
