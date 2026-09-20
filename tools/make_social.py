#!/usr/bin/env python3
"""Draws src/social.png (1200 x 630), the picture shown when the site is shared on Facebook, WhatsApp and so on.
Needs Pillow (pip install pillow). Run it again if you rename the site or change the tagline.

    python3 tools/make_social.py
"""
import json
import os
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANDIDATES_BOLD = [
    "/usr/share/fonts/truetype/liberation/LiberationSansNarrow-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialnb.ttf", "C:/Windows/Fonts/arialbd.ttf",
    "/Library/Fonts/Arial Narrow Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]
CANDIDATES_REG = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def font(paths, size):
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


cfg = json.load(open(os.path.join(ROOT, "site.json")))
W, H = 1200, 630
im = Image.new("RGB", (W, H), (0, 0, 0))
d = ImageDraw.Draw(im)
# the ladder, at level 3
cols = [(143, 203, 234), (79, 163, 214), (245, 179, 1)]
base, bw, gap = 520, 64, 22
for i in range(5):
    h = 110 + i * 62
    x = 90 + i * (bw + gap)
    if i < 3:
        d.rectangle([x, base - h, x + bw, base], fill=cols[i])
    else:
        d.rectangle([x, base - h, x + bw, base], outline=(154, 164, 170), width=5)
d.text((560, 120), cfg["name"].upper(), font=font(CANDIDATES_BOLD, 190), fill=(242, 244, 245))
d.text((566, 340), cfg.get("tagline", ""), font=font(CANDIDATES_REG, 48), fill=(242, 244, 245))
d.text((566, 410), cfg.get("description", ""), font=font(CANDIDATES_REG, 30), fill=(154, 164, 170))
d.text((566, 540), "No cookies. No ads. No tracking.", font=font(CANDIDATES_REG, 30), fill=(143, 203, 234))
im.save(os.path.join(ROOT, "src", "social.png"))
print("wrote src/social.png")
