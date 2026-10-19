#!/usr/bin/env python3
"""Render the app icon (1024x1024 PNG) matching the in-app SwiftUI LogoMark:
an open book framed by a camera viewfinder, on the indigo accent gradient.

Run:  .venv/bin/python scripts/make_icon.py
Writes ios/LibrarySweep/Assets.xcassets/AppIcon.appiconset/icon-1024.png
"""
from __future__ import annotations

import math
import pathlib

from PIL import Image, ImageDraw

S = 1024
OUT = pathlib.Path(__file__).resolve().parent.parent / "ios/LibrarySweep/Assets.xcassets/AppIcon.appiconset"

# Accent gradient (matches Theme.accentGradient: indigo -> violet).
TOP = (99, 102, 241)      # #6366F1
BOT = (139, 92, 246)      # #8B5CF6


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def main() -> None:
    img = Image.new("RGB", (S, S), TOP)
    px = img.load()
    for y in range(S):  # vertical gradient
        c = _lerp(TOP, BOT, y / (S - 1))
        for x in range(S):
            px[x, y] = c

    d = ImageDraw.Draw(img)
    cx = S / 2

    # --- Open book: two pages fanning from a center spine ---
    bw, bh = S * 0.46, S * 0.34
    left, right = cx - bw / 2, cx + bw / 2
    top, bot = S * 0.36, S * 0.36 + bh
    spine_top = (cx, top + bh * 0.06)
    spine_bot = (cx, bot)
    white = (255, 255, 255)

    # Quadratic-ish curves approximated with many segments.
    def quad(p0, p1, p2, n=40):
        pts = []
        for i in range(n + 1):
            t = i / n
            x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0]
            y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]
            pts.append((x, y))
        return pts

    left_page = [spine_top]
    left_page += quad(spine_top, (cx - bw * 0.28, top), (left, top + bh * 0.04))
    left_page += [(left, bot - bh * 0.06)]
    left_page += quad((left, bot - bh * 0.06), (cx - bw * 0.26, bot - bh * 0.02), spine_bot)
    d.polygon(left_page, fill=white)

    right_page = [spine_top]
    right_page += quad(spine_top, (cx + bw * 0.28, top), (right, top + bh * 0.04))
    right_page += [(right, bot - bh * 0.06)]
    right_page += quad((right, bot - bh * 0.06), (cx + bw * 0.26, bot - bh * 0.02), spine_bot)
    d.polygon(right_page, fill=white)

    # Spine shadow line.
    d.line([spine_top, spine_bot], fill=_lerp(TOP, BOT, 0.5), width=int(S * 0.012))

    # Text lines hinted on each page.
    ink = (203, 213, 225)
    for i in range(4):
        y = top + bh * (0.24 + i * 0.16)
        d.line([(left + bw * 0.08, y), (cx - bw * 0.07, y - bh * 0.02)], fill=ink, width=int(S * 0.012))
        d.line([(cx + bw * 0.07, y - bh * 0.02), (right - bw * 0.08, y)], fill=ink, width=int(S * 0.012))

    # --- Viewfinder brackets ---
    lw = int(S * 0.055)
    inset = S * 0.17
    corner = S * 0.14
    r = (inset, inset, S - inset, S - inset)

    def bracket(p_corner, p_a, p_b):
        d.line([p_a, p_corner], fill=white, width=lw, joint="curve")
        d.line([p_corner, p_b], fill=white, width=lw, joint="curve")
        rr = lw / 2
        for p in (p_corner, p_a, p_b):
            d.ellipse([p[0] - rr, p[1] - rr, p[0] + rr, p[1] + rr], fill=white)

    bracket((r[0], r[1]), (r[0], r[1] + corner), (r[0] + corner, r[1]))        # TL
    bracket((r[2], r[1]), (r[2] - corner, r[1]), (r[2], r[1] + corner))        # TR
    bracket((r[2], r[3]), (r[2], r[3] - corner), (r[2] - corner, r[3]))        # BR
    bracket((r[0], r[3]), (r[0] + corner, r[3]), (r[0], r[3] - corner))        # BL

    OUT.mkdir(parents=True, exist_ok=True)
    img.save(OUT / "icon-1024.png")

    # Minimal single-size asset catalog entry (Xcode 14+ accepts one 1024 icon).
    (OUT / "Contents.json").write_text(
        '{\n  "images" : [\n    {\n      "filename" : "icon-1024.png",\n'
        '      "idiom" : "universal",\n      "platform" : "ios",\n      "size" : "1024x1024"\n'
        '    }\n  ],\n  "info" : { "author" : "xcode", "version" : 1 }\n}\n')
    print("wrote", OUT / "icon-1024.png")


if __name__ == "__main__":
    main()
