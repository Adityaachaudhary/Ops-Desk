"""Tiny server-side SVG charts (no JS library, no CDN -> works offline)."""
BAND = [(0, 12, "#f8d4d4"), (12, 15, "#fbefc9"), (15, 30, "#dff1e3"), (30, 33, "#fbefc9"), (33, 100, "#d6e6f8")]
DOT = {"Red": "#c62828", "Yellow": "#e0a100", "Green": "#2e7d32", "Blue": "#1565c0", "Grey": "#78909c"}


def cm2_chart(days: list[dict], w: int = 560, h: int = 130) -> str:
    pts = [(i, d["cm2_pct"], d["colour"], d["day"]) for i, d in enumerate(days) if d["cm2_pct"] is not None]
    if len(pts) < 2:
        return ""
    vals = [p[1] for p in pts]
    lo, hi = min(8, min(vals) - 1), max(36, max(vals) + 1)
    pad_l, pad_r, pad_t, pad_b = 34, 8, 8, 16
    X = lambda i: pad_l + i * (w - pad_l - pad_r) / max(1, len(days) - 1)
    Y = lambda v: pad_t + (hi - v) * (h - pad_t - pad_b) / (hi - lo)
    s = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="CM2 percent by day">']
    for a, b, c in BAND:
        a2, b2 = max(a, lo), min(b, hi)
        if a2 < b2:
            s.append(f'<rect x="{pad_l}" y="{Y(b2):.1f}" width="{w - pad_l - pad_r}" height="{Y(a2) - Y(b2):.1f}" fill="{c}"/>')
    for v in (12, 15, 30, 33):
        if lo < v < hi:
            s.append(f'<text x="{pad_l - 4}" y="{Y(v) + 3:.1f}" font-size="9" text-anchor="end" fill="#55666b">{v}%</text>')
    line = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v, _, _ in pts)
    s.append(f'<polyline points="{line}" fill="none" stroke="#12353a" stroke-width="1.6"/>')
    for i, v, c, day in pts:
        s.append(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="2.8" fill="{DOT[c]}"><title>{day}: CM2 {v:.1f}%</title></circle>')
    s.append("</svg>")
    return "".join(s)
