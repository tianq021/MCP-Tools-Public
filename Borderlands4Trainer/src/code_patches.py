"""Version-specific runtime code patches for the verified BL4 build.

Pure data/validation module.  It does not open or write another process.
All addresses are RVAs relative to Borderlands4.exe and must be guarded by the
recorded executable SHA-256 plus exact original-byte checks.
"""
from dataclasses import dataclass

VERIFIED_SHA256 = "9C3AFB7DC6A550A6C2E817846CD2C40FF11E066DC6AEFEB819F802E6A4C5C3E0"


@dataclass(frozen=True)
class BytePatch:
    name: str
    rva: int
    original: bytes
    variants: dict
    note: str


SKILL_NO_SPEND = BytePatch(
    name="skill_no_spend",
    rva=0x1212567,
    original=bytes.fromhex("41 89 F0"),             # mov r8d, esi
    variants={
        "on": bytes.fromhex("45 31 C0"),           # xor r8d, r8d
        "off": bytes.fromhex("41 89 F0"),
    },
    note=(
        "Server_SpendProgressPoints final path: keep the normal purchase flow, "
        "but pass a spend amount of zero to the final point-spend call. The "
        "earlier availability check is left intact, so at least the normal "
        "required number of points must still be available to buy a node."
    ),
)


XP_KILL_MULTIPLIER = BytePatch(
    name="kill_xp_multiplier",
    rva=0x1326A1C,
    original=bytes.fromhex("49 89 D0 49 C1 F8 3F"),  # mov r8,rdx ; sar r8,63
    variants={
        1: bytes.fromhex("49 89 D0 49 C1 F8 3F"),
        2: bytes.fromhex("48 6B D2 02 49 89 D0"),
        5: bytes.fromhex("48 6B D2 05 49 89 D0"),
        10: bytes.fromhex("48 6B D2 0A 49 89 D0"),
    },
    note=(
        "UNVALIDATED legacy bytes retained only for recognition/restoration. "
        "AwardKillExperienceToParty final path: after the game's own floating-"
        "point reward/modifier calculation is converted to a positive integer "
        "in RDX, multiply that integer by 2/5/10. This targets kill XP only."
    ),
)


ALL_PATCHES = (SKILL_NO_SPEND, XP_KILL_MULTIPLIER)


def identify_variant(patch, raw):
    matches = [key for key, value in patch.variants.items() if value == raw]
    if len(matches) == 1:
        return matches[0]
    if raw == patch.original:
        # Some patches intentionally have an explicit restore variant equal to
        # the original. Prefer that key when present.
        for key, value in patch.variants.items():
            if value == patch.original:
                return key
        return "original"
    return None


def validate_layout():
    for patch in ALL_PATCHES:
        if patch.rva <= 0:
            raise AssertionError(patch.name)
        sizes = {len(patch.original), *(len(v) for v in patch.variants.values())}
        if len(sizes) != 1:
            raise AssertionError(f"{patch.name}: variant size mismatch {sizes}")
        if len(patch.original) > 16:
            raise AssertionError(f"{patch.name}: unexpectedly large patch")


validate_layout()
