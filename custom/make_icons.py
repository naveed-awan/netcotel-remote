#!/usr/bin/env python3
"""Render every icon/logo file the client needs from custom/brands/<brand>/brand-src/.

Output goes to custom/brands/<brand>/overlay/, which mirrors the repo layout; brand.py
copies it over the sources at build time. Run on macOS (needs Pillow and iconutil):

    python3 custom/make_icons.py netcotel

icon.png is either a mark on a transparent background or an opaque square tile
(mark already on its background color). logo.png has dark text on transparent; if
it stacks mark, wordmark and slogan, only the wordmark band is used.
"""
import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent


def load_cropped(path):
    im = Image.open(path).convert("RGBA")
    return im.crop(im.getchannel("A").getbbox())


def is_tile(im):
    return im.getchannel("A").getextrema()[0] == 255


def rounded(im, radius=0.2237):
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, im.width - 1, im.height - 1), round(im.width * radius), fill=255)
    out = im.copy()
    out.putalpha(mask)
    return out


def glyph(tile):
    """The mark inside a tile, as opaque pixels on transparent (alpha from distance to the background)."""
    bg = tile.getpixel((tile.width // 2, 2))[:3]
    alpha = Image.new("L", tile.size)
    alpha.putdata([min(255, max(abs(c - b) for c, b in zip(p[:3], bg)) * 2) if p[3] else 0
                   for p in tile.getdata()])
    out = tile.copy()
    out.putalpha(alpha)
    return out.crop(alpha.getbbox())


def scaled(im, size):
    # premultiplied resize, so transparent (white) pixels don't bleed into the edges
    return im.convert("RGBa").resize(size, Image.LANCZOS).convert("RGBA")


def fit(im, box_w, box_h):
    k = min(box_w / im.width, box_h / im.height)
    return scaled(im, (max(1, round(im.width * k)), max(1, round(im.height * k))))


def boost_alpha(im, gain):
    r, g, b, a = im.split()
    return Image.merge("RGBA", (r, g, b, a.point(lambda v: min(255, int(v * gain)))))


def square(mark, size, pad=0.04):
    """Mark centered on a transparent square; thin strokes get strengthened at small sizes."""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    inner = round(size * (1 - 2 * pad))
    m = fit(mark, inner, inner)
    if size <= 64:
        m = boost_alpha(m, 2.2 if size <= 32 else 1.6)
    canvas.alpha_composite(m, ((size - m.width) // 2, (size - m.height) // 2))
    return canvas


def mac_tile(mark, size=1024):
    """macOS style icon: white rounded tile with the mark inside."""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    margin, radius = round(size * 0.098), round(size * 0.18)
    ImageDraw.Draw(canvas).rounded_rectangle(
        (margin, margin, size - margin, size - margin), radius, fill=(255, 255, 255, 255))
    m = fit(mark, round(size * 0.64), round(size * 0.64))
    canvas.alpha_composite(m, ((size - m.width) // 2, (size - m.height) // 2))
    return canvas


def mac_framed(tile, size=1024):
    """macOS style icon from a tile that already has its background: just the standard margin."""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    t = fit(tile, size - 2 * round(size * 0.098), size - 2 * round(size * 0.098))
    canvas.alpha_composite(t, ((size - t.width) // 2, (size - t.height) // 2))
    return canvas


def silhouette(mark, size, color):
    sq = boost_alpha(square(mark, size, pad=0.03), 2.2)
    flat = Image.new("RGBA", sq.size, color + (0,))
    flat.putalpha(sq.getchannel("A"))
    return flat


def bands(im):
    """Split the stacked logo into its horizontal bands (mark, wordmark, slogan)."""
    alpha = im.getchannel("A")
    rows = [alpha.crop((0, y, im.width, y + 1)).getbbox() is not None for y in range(im.height)]
    out, start = [], None
    for y, filled in enumerate(rows + [False]):
        if filled and start is None:
            start = y
        elif not filled and start is not None:
            if y - start > im.height * 0.02:
                out.append((start, y))
            start = None
    return out


def wordmark_logo(mark, logo, dark):
    """Horizontal logo for the home screen (shown at max 300x60, rendered at 2x)."""
    spans = bands(logo)
    stacked = len(spans) == 3
    top, bottom = spans[1] if stacked else (0, logo.height)
    word = logo.crop((0, top, logo.width, bottom))
    word = word.crop(word.getchannel("A").getbbox())
    if dark:
        px = word.load()
        for y in range(word.height):
            for x in range(word.width):
                r, g, b, a = px[x, y]
                if a and max(r, g, b) < 90:
                    px[x, y] = (255, 255, 255, a)
    height = 120
    m = fit(mark, 10 ** 6, height)
    # a whole logo (wordmark plus its own subline) needs more height than a single line
    w = fit(word, 10 ** 6, round(height * (0.42 if stacked else 0.75)))
    gap = 28
    canvas = Image.new("RGBA", (m.width + gap + w.width, height), (0, 0, 0, 0))
    canvas.alpha_composite(m, (0, 0))
    canvas.alpha_composite(w, (m.width + gap, (height - w.height) // 2))
    return fit(canvas, 600, 120)


def save(im, rel, **kw):
    path = OUT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, **kw)
    print("  ", rel)


def main():
    global OUT
    brands = sorted(p.name for p in (HERE / "brands").iterdir() if (p / "brand-src").is_dir())
    parser = argparse.ArgumentParser()
    parser.add_argument("brand", choices=brands)
    brand_dir = HERE / "brands" / parser.parse_args().brand
    OUT = brand_dir / "overlay"

    mark = load_cropped(brand_dir / "brand-src/icon.png")
    tile = is_tile(mark)
    if tile:
        mark = rounded(mark)
    logo = Image.open(brand_dir / "brand-src/logo.png").convert("RGBA")
    if OUT.exists():
        shutil.rmtree(OUT)

    big = square(mark, 1024)
    ico_sizes = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256)]
    ico_frames = [square(mark, s) for s, _ in ico_sizes]
    for rel in ("res/icon.ico", "flutter/windows/runner/resources/app_icon.ico"):
        save(ico_frames[-1], rel, sizes=ico_sizes, append_images=ico_frames[:-1])
    tray = [square(mark, s) for s in (16, 32, 48)]
    save(tray[-1], "res/tray-icon.ico", sizes=[(16, 16), (32, 32), (48, 48)], append_images=tray[:-1])

    save(big, "res/icon.png")
    for name, s in (("32x32", 32), ("64x64", 64), ("128x128", 128), ("128x128@2x", 256)):
        save(square(mark, s), f"res/{name}.png")
    save(square(mark, 256), "flutter/assets/icon.png")

    mac = mac_framed(mark) if tile else mac_tile(mark)
    save(mac, "res/mac-icon.png")
    shape = glyph(mark) if tile else mark
    save(silhouette(shape, 60, (0, 0, 0)), "res/mac-tray-dark-x2.png")
    save(silhouette(shape, 48, (255, 255, 255)), "res/mac-tray-light-x2.png")
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        for s in (16, 32, 128, 256, 512):
            scaled(mac, (s, s)).save(iconset / f"icon_{s}x{s}.png")
            scaled(mac, (s * 2, s * 2)).save(iconset / f"icon_{s}x{s}@2x.png")
        icns = OUT / "flutter/macos/Runner/AppIcon.icns"
        icns.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)], check=True)
        print("  ", icns.relative_to(OUT))

    light = wordmark_logo(mark, logo, dark=False)
    save(light, "flutter/assets/logo.png")
    save(light, "flutter/assets/logo_light.png")
    save(wordmark_logo(mark, logo, dark=True), "flutter/assets/logo_dark.png")


if __name__ == "__main__":
    main()
