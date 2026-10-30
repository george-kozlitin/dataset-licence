"""The home page's large number: the dataset count cut through the paper, silver cells behind it."""

from __future__ import annotations

import math
import random

from markupsafe import Markup, escape

W, H = 560, 300
# Josefin Sans 700, letter-spacing -2: the widest 7-digit total ("8 888 888") measures 0.467 em per
# character in Chromium; 0.48 leaves a margin for other renderers.
EM_PER_CHAR = 0.48
MAX_FONT = 192
TEXT_WIDTH = 540


def group_digits(n: int) -> str:
    """28261 -> '28 261': thousands grouped with a space."""
    return f"{n:,}".replace(",", " ")


def font_size(text: str) -> int:
    """Font size at which text fits inside the viewBox width."""
    return min(MAX_FONT, int(TEXT_WIDTH / (EM_PER_CHAR * max(1, len(text)))))


def _hexpts(cx: float, cy: float, r: float) -> str:
    return " ".join(
        f"{cx + r * math.cos(math.pi / 3 * i):.1f},{cy + r * math.sin(math.pi / 3 * i):.1f}" for i in range(6)
    )


def _cells() -> str:
    rnd = random.Random(7)  # the same cloud in every build
    r = 15
    out = []
    for c in range(-1, 26):
        for row in range(-1, 14):
            cx = c * 1.5 * r + 8
            cy = row * math.sqrt(3) * r + (math.sqrt(3) * r / 2 if c % 2 else 0)
            # A loose cloud, densest low on the right.
            d = math.hypot((cx - 380) / 230, (cy - 230) / 150)
            if rnd.random() < max(0, 0.95 - d * 0.8):
                # Cells rise in from the bottom right, the way light reaches into the cut.
                out.append(
                    f'<polygon class="cell" style="--d:{0.35 + (W - cx + cy) / 1400:.2f}s" '
                    f'points="{_hexpts(cx, cy, r - 2.2)}" fill="url(#cut-silver)" filter="url(#cut-lift)"/>'
                )
    return '<g class="cells">' + "".join(out) + "</g>"


def svg(total: int) -> Markup:
    num = group_digits(total)
    fs = font_size(num)
    y = round(165 + 0.35 * fs)
    txt = (
        f'<text x="{W // 2}" y="{y}" text-anchor="middle" font-family="Josefin Sans, sans-serif" font-weight="700" '
        f'font-size="{fs}" letter-spacing="-2"{{fill}}>{escape(num)}</text>'
    )
    plain, dark = txt.format(fill=""), txt.format(fill=' fill="#000"')
    label = escape(f"{total:,} datasets checked")
    return Markup(
        f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{label}"><defs>'
        f'<clipPath id="cut-num">{plain}</clipPath>'
        '<radialGradient id="cut-room" cx="65%" cy="70%" r="80%"><stop offset="0" stop-color="#3a3640"/>'
        '<stop offset="1" stop-color="#17151a"/></radialGradient>'
        '<linearGradient id="cut-silver" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#e9e5dc"/>'
        '<stop offset=".55" stop-color="#b6b0a4"/><stop offset="1" stop-color="#6f6a61"/></linearGradient>'
        '<filter id="cut-lift" x="-40%" y="-40%" width="180%" height="180%"><feDropShadow dx="2.5" dy="3.5" '
        'stdDeviation="2.2" flood-color="#000" flood-opacity=".55"/></filter>'
        '<filter id="cut-soft"><feGaussianBlur stdDeviation="7"/></filter>'
        f'<mask id="cut-edge"><rect width="{W}" height="{H}" fill="#fff"/><g filter="url(#cut-soft)">'
        f'<g transform="translate(9 12)">{dark}</g></g></mask></defs>'
        f'<g transform="translate(1.6 2.2)" fill="#ffffff" opacity=".9">{plain}</g>'
        f'<g clip-path="url(#cut-num)"><rect width="{W}" height="{H}" fill="url(#cut-room)"/>{_cells()}'
        f'<rect width="{W}" height="{H}" fill="#08070a" opacity=".78" mask="url(#cut-edge)"/></g></svg>'
    )
