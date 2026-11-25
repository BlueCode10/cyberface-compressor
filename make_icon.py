"""Draw icon.ico for the Cyberface Compressor: the tools' orange-red skewed tile with a white "compress" glyph
(two arrows squeezing toward a bar). Run: python make_icon.py  (needs pillow)"""
import os
from PIL import Image, ImageDraw

S = 1024                       # drawn large, then downsampled for each icon size


def tile():
    # gradient (top-left #ff4e27 -> bottom-right #b91d18), rounded square, skewed like the UI's .mark
    g = Image.new('RGBA', (S, S))
    px = g.load()
    a, b = (0xff, 0x4e, 0x27), (0xb9, 0x1d, 0x18)
    for y in range(S):
        for x in range(0, S, 1):
            t = (x + y) / (2 * S - 2)
            px[x, y] = tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3)) + (255,)
    m = Image.new('L', (S, S), 0)
    pad = 90
    ImageDraw.Draw(m).rounded_rectangle((pad + 40, pad, S - pad - 40, S - pad), radius=150, fill=255)
    m = m.transform((S, S), Image.AFFINE, (1, 0.12, -0.12 * S / 2, 0, 1, 0), resample=Image.BICUBIC)
    out = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    out.paste(g, (0, 0), m)
    return out


def glyph(im, bold=False):
    d = ImageDraw.Draw(im)
    W = (255, 255, 255, 255)
    cx, cy = S // 2, S // 2
    bar_w, bar_h = (560, 120) if bold else (470, 74)
    d.rounded_rectangle((cx - bar_w // 2, cy - bar_h // 2, cx + bar_w // 2, cy + bar_h // 2), radius=bar_h // 2, fill=W)
    stem_w, head_w, head_h, gap = (150, 470, 190, 60) if bold else (92, 330, 150, 46)
    for sgn in (-1, 1):        # top arrow points down, bottom arrow points up
        tip = cy + sgn * (bar_h // 2 + gap)
        base = tip + sgn * head_h
        d.polygon([(cx, tip), (cx - head_w // 2, base), (cx + head_w // 2, base)], fill=W)
        end = cy + sgn * (360 if bold else 330)
        y0, y1 = sorted((base - sgn * 4, end))
        d.rectangle((cx - stem_w // 2, y0, cx + stem_w // 2, y1), fill=W)
    return im


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    big, bold = glyph(tile()), glyph(tile(), bold=True)      # bolder glyph for the small sizes (<= 32)
    imgs = [(bold if s <= 32 else big).resize((s, s), Image.LANCZOS) for s in (256, 128, 64, 48, 32, 24, 16)]
    imgs[0].save(os.path.join(here, 'icon.ico'), sizes=[im.size for im in imgs], append_images=imgs[1:])
    print('icon.ico written')


if __name__ == '__main__':
    main()
