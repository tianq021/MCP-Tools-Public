"""Export the build 26049821 trait artwork. Build-time dependencies: UnityPy and Pillow."""
import argparse
import csv
import io
import json
import re
import struct
from pathlib import Path

import UnityPy
from PIL import Image, ImageDraw, ImageFont, ImageOps


def glyphs(asset, texture):
    """Read named TMP 1.1.0 sprite characters and their Unity glyph rectangles."""
    raw = asset.get_raw_data()
    pos = raw.index(struct.pack("<II", 2, 0xfffe))
    count, = struct.unpack_from("<I", raw, pos - 4)
    names = {}
    for _ in range(count):
        kind, _, index, _, length = struct.unpack_from("<IIIfI", raw, pos)
        if kind != 2:
            raise ValueError("Unexpected TMP character type")
        names[raw[pos + 20:pos + 20 + length].decode()] = index
        pos = (pos + 20 + length + 3) & ~3
    num_rects, = struct.unpack_from("<I", raw, pos)
    pos += 4
    rects = {}
    for _ in range(num_rects):
        index, _, _, _, _, _, x, y, w, h = struct.unpack_from("<I5f4i", raw, pos)
        if not (0 <= x <= x + w <= texture.width and 0 <= y <= y + h <= texture.height):
            raise ValueError("TMP glyph extends outside its atlas")
        rects[index] = (x, texture.height - y - h, x + w, texture.height - y)
        pos += 64
    return {name: texture.crop(rects[index]) for name, index in names.items()}


def compose(row, sprites, badges):
    # The game places its corner sprite at lower right and its text sprites at lower left.
    icon = Image.new("RGBA", (128, 128))
    def place(image, x, y, w, h):
        scaled = ImageOps.contain(image, (w, h), Image.Resampling.LANCZOS)
        icon.alpha_composite(scaled, (x + (w-scaled.width)//2, y + (h-scaled.height)//2))

    place(sprites[row["icon"]], 5, 4, 118, 118)
    if row["icon_overlay"]:
        place(sprites[row["icon_overlay"]], 34, 32, 60, 60)
    if row["icon_corner_right"]:
        place(sprites[row["icon_corner_right"]], 64, 62, 62, 62)
    left = row["text_corner_left"]
    if left == "+1":
        draw = ImageDraw.Draw(icon)
        font = ImageFont.truetype("arialbd.ttf", 43)
        draw.text((2, 124), "+1", font=font, anchor="ls", fill="white", stroke_width=3, stroke_fill="#39352d")
    elif left:
        names = re.findall(r"<sprite name=([\w]+)>", left)
        if "".join(f"<sprite name={n}>" for n in names) != left:
            raise ValueError(f"Unknown corner badge: {left}")
        size = min(59, 88 // len(names))
        for i, name in enumerate(names):
            place(badges[name], i * size, 125 - size, size, size)
    return icon.resize((40, 40), Image.Resampling.LANCZOS)


def export(game_data, catalogue, destination):
    env = UnityPy.load(str(game_data / "globalgamemanagers"), str(game_data / "resources.assets"))
    manager = next(o.read() for o in env.objects if o.type.name == "ResourceManager")
    assets = {(path, ptr.type.name): ptr for path, ptr in manager.m_Container if ptr.m_PathID}
    table = assets["generated/new sapphire data - modifiers", "TextAsset"].read().m_Script
    rows = {r["ident"]: r for r in csv.DictReader(io.StringIO(table))}
    ids = json.loads(catalogue.read_text(encoding="utf-8-sig"))
    paths = {rows[i][field] for i in ids for field in ("icon", "icon_overlay", "icon_corner_right")}
    sprites = {p: assets[p, "Sprite"].read().image.convert("RGBA") for p in paths if p}
    atlas = assets["everywhere/fonts and materials/text_sprites", "Texture2D"].read().image.convert("RGBA")
    badges = glyphs(assets["pc/everywhere/fonts and materials/textsprites3d", "MonoBehaviour"].deref(), atlas)
    destination.mkdir(parents=True, exist_ok=True)
    for ident in ids:
        if not re.fullmatch(r"[\w-]+", ident):
            raise ValueError(f"Invalid trait ID: {ident}")
        icon = compose(rows[ident], sprites, badges)
        if not icon.getbbox():
            raise ValueError(f"Empty icon: {ident}")
        icon.save(destination / f"{ident}.png")
    print(f"Exported {len(ids)} trait icons from {len(sprites)} sprites and the game's TMP atlas.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("game_data", "catalogue", "destination"):
        parser.add_argument(arg, type=Path)
    export(**vars(parser.parse_args()))
