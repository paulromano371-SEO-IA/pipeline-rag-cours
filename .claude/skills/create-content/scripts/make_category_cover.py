"""Genere la banniere (image d'en-tete) des pages categories du blog.

    make_category_cover.py [slug ...]     # sans argument : toutes les categories
    make_category_cover.py --list

Ecrit rag_data/categories/<slug>.png (1600x400). Aucune IA : fond en degrade
dont la teinte est FIXE par categorie (CATEGORIES ci-dessous) + un motif
dessine qui illustre le theme (graphe documents pour RAG, couches de neurones
pour Deep Learning, frontiere de decision pour Machine Learning, etc.).
Rendu en 2x puis reduit (bords lisses). Meme categorie -> meme image.

Pour qu'une categorie et ses articles partagent la meme famille de couleur,
`hue_for(slug)` expose la teinte d'une categorie.
"""
from __future__ import annotations
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(next(p for p in _Path(__file__).resolve().parents if (p / ".claude" / "skills" / "_rag_lib").is_dir()) / ".claude" / "skills" / "_rag_lib"))
import venv_guard  # noqa: F401

import math
import random
import sys
from pathlib import Path

_sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_cover import FONT_BOLD, FONT_REG, hsl, load_font, wrap  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[4]
OUT_DIR = ROOT / "rag_data" / "categories"
W, H = 1600, 400
S = 2  # facteur de sur-echantillonnage
MARGIN = 90

# slug de categorie WordPress -> (nom affiche, teinte 0..1, motif)
CATEGORIES: dict[str, tuple[str, float, str]] = {
    "rag": ("RAG", 0.97, "rag"),
    "deep-learning": ("Deep Learning", 0.75, "deep"),
    "machine-learning": ("Machine Learning", 0.58, "ml"),
    "agentic": ("Agentic", 0.07, "agentic"),
    "maths": ("Maths", 0.48, "maths"),
    "python": ("Python", 0.14, "python"),
    "mlops": ("MLOps", 0.32, "mlops"),
    "inference": ("Inference", 0.87, "inference"),
    # Pas une categorie WP : banniere de la page d'accueil du blog (index.php).
    "accueil": ("Intelligence Artificielle", 0.56, "home"),
    # Banniere generique des categories encore vides (archive.php).
    "en-preparation": ("En cours de préparation", 0.56, "soon"),
    # Banniere generique des pages de recherche (search.php).
    "recherche": ("Recherche", 0.56, "search"),
    # Banniere generique des archives mensuelles (archive.php, is_month()).
    "archives": ("Archives", 0.56, "calendar"),
    # Banniere generique de la page 404 (404.php).
    "404": ("Page introuvable", 0.56, "notfound"),
}


def hue_for(slug: str) -> float:
    return CATEGORIES[slug][1]


# --- primitives (coordonnees deja multipliees par S) -----------------------

def arrow(d, x1, y1, x2, y2, col, w, head):
    d.line([x1, y1, x2, y2], fill=col, width=w)
    a = math.atan2(y2 - y1, x2 - x1)
    pts = [(x2, y2),
           (x2 - head * math.cos(a - 0.45), y2 - head * math.sin(a - 0.45)),
           (x2 - head * math.cos(a + 0.45), y2 - head * math.sin(a + 0.45))]
    d.polygon(pts, fill=col)


def dot(d, cx, cy, r, fill, outline=None, w=0):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill, outline=outline, width=w)


# --- motifs : (draw, x0, y0, x1, y1, accent, rng) --------------------------

def motif_rag(d, x0, y0, x1, y1, acc, rng):
    u = S
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    white = (255, 255, 255)
    dot(d, cx, cy, 150 * u, None, white + (35,), 2 * u)
    docs = []
    for i in range(7):
        a = -math.pi / 2 + i * 2 * math.pi / 7
        docs.append((cx + 250 * u * math.cos(a), cy + 125 * u * math.sin(a)))
    hit = {0, 2, 5}
    for i, (dx, dy) in enumerate(docs):
        col = acc + (200,) if i in hit else white + (60,)
        d.line([cx, cy, dx, dy], fill=col, width=(4 if i in hit else 2) * u)
    for i, (dx, dy) in enumerate(docs):
        on = i in hit
        d.rounded_rectangle([dx - 30 * u, dy - 38 * u, dx + 30 * u, dy + 38 * u], radius=6 * u,
                            fill=(255, 255, 255, 40 if not on else 70),
                            outline=(acc if on else white) + (230 if on else 90,), width=3 * u)
        for k in range(4):
            wbar = (44 if k < 3 else 26) * u
            yy = dy - 22 * u + k * 14 * u
            d.rounded_rectangle([dx - 22 * u, yy, dx - 22 * u + wbar, yy + 6 * u], radius=3 * u,
                                fill=(acc if on else white) + (230 if on else 90,))
    dot(d, cx, cy, 40 * u, acc + (255,))
    dot(d, cx, cy, 40 * u, None, white + (255,), 4 * u)
    dot(d, cx, cy, 14 * u, white + (255,))


def motif_deep(d, x0, y0, x1, y1, acc, rng):
    u = S
    layers = [3, 5, 6, 5, 3]
    xs = [x0 + 40 * u + i * (x1 - x0 - 80 * u) / (len(layers) - 1) for i in range(len(layers))]
    pos = []
    for n, x in zip(layers, xs):
        gap = 52 * u
        top = (y0 + y1) / 2 - gap * (n - 1) / 2
        pos.append([(x, top + k * gap) for k in range(n)])
    for a, b in zip(pos, pos[1:]):
        for (xa, ya) in a:
            for (xb, yb) in b:
                d.line([xa, ya, xb, yb], fill=(255, 255, 255, 38), width=2 * u)
    lit = {(1, 2), (2, 3), (3, 1), (0, 1), (4, 1)}
    for i in range(len(pos) - 1):
        for j, (xa, ya) in enumerate(pos[i]):
            for k, (xb, yb) in enumerate(pos[i + 1]):
                if (i, j) in lit and (i + 1, k) in lit:
                    d.line([xa, ya, xb, yb], fill=acc + (230,), width=4 * u)
    for i, col in enumerate(pos):
        for j, (x, y) in enumerate(col):
            on = (i, j) in lit
            dot(d, x, y, 17 * u, (acc + (255,)) if on else (255, 255, 255, 45),
                (255, 255, 255, 230 if on else 130), 3 * u)


def motif_ml(d, x0, y0, x1, y1, acc, rng):
    u = S
    w, h = x1 - x0, y1 - y0
    for k in range(1, 5):
        d.line([x0, y0 + k * h / 5, x1, y0 + k * h / 5], fill=(255, 255, 255, 22), width=2 * u)
    d.line([x0, y1 - 10 * u, x1, y1 - 10 * u], fill=(255, 255, 255, 90), width=3 * u)
    d.line([x0 + 10 * u, y0, x0 + 10 * u, y1], fill=(255, 255, 255, 90), width=3 * u)
    # frontiere : S allonge
    pts = [(x0 + w * t, y0 + h * (0.5 + 0.22 * math.sin(t * 5.2 + 0.6) - 0.3 * (t - 0.5)))
           for t in [i / 60 for i in range(61)]]
    for _ in range(46):
        t = rng.random()
        bx, by = x0 + w * t, y0 + h * (0.5 + 0.22 * math.sin(t * 5.2 + 0.6) - 0.3 * (t - 0.5))
        side = rng.choice((-1, 1))
        off = side * (rng.uniform(22, 120) * u)
        py = min(max(by + off, y0 + 14 * u), y1 - 22 * u)
        if side < 0:
            dot(d, bx, py, 9 * u, acc + (240,), (255, 255, 255, 255), 2 * u)
        else:
            dot(d, bx, py, 9 * u, (255, 255, 255, 110), (255, 255, 255, 220), 2 * u)
    d.line(pts, fill=(255, 255, 255, 255), width=5 * u, joint="curve")


def motif_agentic(d, x0, y0, x1, y1, acc, rng):
    u = S
    cx, cy, r = (x0 + x1) / 2, (y0 + y1) / 2, 125 * u
    white = (255, 255, 255)
    dot(d, cx, cy, r + 55 * u, None, white + (30,), 2 * u)
    angles = [-90, 0, 90, 180]
    for i, a in enumerate(angles):
        a0, a1 = a + 22, a + 90 - 22
        d.arc([cx - r, cy - r, cx + r, cy + r], a0, a1, fill=acc + (230,), width=6 * u)
        ar = math.radians(a1)
        ex, ey = cx + r * math.cos(ar), cy + r * math.sin(ar)
        tx, ty = -math.sin(ar), math.cos(ar)
        d.polygon([(ex + tx * 18 * u, ey + ty * 18 * u),
                   (ex - tx * 4 * u + math.cos(ar) * 14 * u, ey - ty * 4 * u + math.sin(ar) * 14 * u),
                   (ex - tx * 4 * u - math.cos(ar) * 14 * u, ey - ty * 4 * u - math.sin(ar) * 14 * u)],
                  fill=acc + (230,))
    for i, a in enumerate(angles):
        ar = math.radians(a)
        nx, ny = cx + r * math.cos(ar), cy + r * math.sin(ar)
        dot(d, nx, ny, 34 * u, (255, 255, 255, 50), white + (255,), 4 * u)
        dot(d, nx, ny, 12 * u, white + (255,) if i % 2 else acc + (255,))
    dot(d, cx, cy, 30 * u, acc + (255,), white + (255,), 4 * u)
    for k in range(3):
        a = k * 2 * math.pi / 3 + 0.5
        d.line([cx, cy, cx + 60 * u * math.cos(a), cy + 60 * u * math.sin(a)], fill=white + (180,), width=3 * u)


def motif_maths(d, x0, y0, x1, y1, acc, rng):
    u = S
    white = (255, 255, 255)
    w, h = x1 - x0, y1 - y0
    step = 40 * u
    x = x0
    while x <= x1:
        d.line([x, y0, x, y1], fill=white + (20,), width=2 * u)
        x += step
    y = y0
    while y <= y1:
        d.line([x0, y, x1, y], fill=white + (20,), width=2 * u)
        y += step
    ay = y0 + h * 0.62
    d.line([x0, ay, x1, ay], fill=white + (130,), width=3 * u)
    d.line([x0 + w * 0.12, y0, x0 + w * 0.12, y1], fill=white + (130,), width=3 * u)
    # gaussienne + aire
    gx = [x0 + w * t / 100 for t in range(101)]
    gy = [ay - 190 * u * math.exp(-((t - 62) / 17) ** 2) for t in range(101)]
    d.polygon(list(zip(gx, gy)) + [(gx[-1], ay), (gx[0], ay)], fill=acc + (60,))
    d.line(list(zip(gx, gy)), fill=acc + (255,), width=6 * u, joint="curve")
    sx = [x0 + w * t / 100 for t in range(101)]
    sy = [ay - 70 * u * math.sin(t / 100 * 4 * math.pi) for t in range(101)]
    d.line(list(zip(sx, sy)), fill=white + (230,), width=4 * u, joint="curve")
    px, py = gx[62], gy[62]
    d.line([px, py, px, ay], fill=white + (200,), width=2 * u)
    dot(d, px, py, 11 * u, white + (255,), acc + (255,), 4 * u)


def motif_python(d, x0, y0, x1, y1, acc, rng):
    u = S
    white = (255, 255, 255)
    cx = (x0 + x1) / 2
    bx0, bx1 = cx - 230 * u, cx + 230 * u
    by0, by1 = y0 + 20 * u, y1 - 20 * u
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=18 * u, fill=(0, 0, 0, 70), outline=white + (110,), width=3 * u)
    d.line([bx0, by0 + 40 * u, bx1, by0 + 40 * u], fill=white + (60,), width=2 * u)
    for k, c in enumerate((acc, white, white)):
        dot(d, bx0 + (28 + k * 26) * u, by0 + 20 * u, 8 * u, c + (230 if k == 0 else 120,))
    spec = [(0, 120, acc), (1, 150, white), (1, 90, white), (2, 190, acc), (2, 130, white),
            (1, 70, white), (0, 100, acc), (1, 170, white), (1, 110, white), (2, 60, acc)]
    y = by0 + 62 * u
    for ind, ln, c in spec:
        if y + 12 * u > by1 - 10 * u:
            break
        x = bx0 + (28 + ind * 34) * u
        d.rounded_rectangle([x, y, x + ln * u, y + 12 * u], radius=6 * u, fill=c + (235 if c == acc else 150,))
        if ind == 0:
            d.rounded_rectangle([x + (ln + 12) * u, y, x + (ln + 52) * u, y + 12 * u], radius=6 * u, fill=white + (110,))
        y += 28 * u


def motif_mlops(d, x0, y0, x1, y1, acc, rng):
    u = S
    white = (255, 255, 255)
    n, bw = 5, 74 * u
    gap = ((x1 - x0) - n * bw) / (n - 1)
    cy = (y0 + y1) / 2 - 25 * u
    xs = [x0 + i * (bw + gap) for i in range(n)]
    for i in range(n - 1):
        arrow(d, xs[i] + bw + 6 * u, cy, xs[i + 1] - 8 * u, cy, white + (200,), 4 * u, 16 * u)
    for i, x in enumerate(xs):
        last = i == n - 1
        d.rounded_rectangle([x, cy - bw / 2, x + bw, cy + bw / 2], radius=14 * u,
                            fill=(acc + (255,)) if last else (255, 255, 255, 40), outline=white + (230,), width=4 * u)
        mx, my = x + bw / 2, cy
        if i == 0:
            for k in range(3):
                d.rounded_rectangle([mx - 20 * u, my - 20 * u + k * 15 * u, mx + 20 * u, my - 12 * u + k * 15 * u], radius=4 * u, fill=white + (220,))
        elif i == 1:
            for k in range(3):
                d.line([mx - 20 * u + k * 20 * u, my + 18 * u, mx - 12 * u + k * 20 * u, my - 18 * u + k * 10 * u], fill=white + (220,), width=5 * u)
        elif i == 2:
            dot(d, mx, my, 21 * u, None, white + (230,), 5 * u)
            dot(d, mx, my, 6 * u, white + (230,))
        elif i == 3:
            for k, hh in enumerate((16, 34, 24)):
                d.rectangle([mx - 22 * u + k * 17 * u, my + 18 * u - hh * u, mx - 12 * u + k * 17 * u, my + 18 * u], fill=white + (220,))
        else:
            d.polygon([(mx - 14 * u, my - 20 * u), (mx + 22 * u, my), (mx - 14 * u, my + 20 * u)], fill=white + (255,))
    ry = cy + bw / 2 + 50 * u
    pts = [(xs[-1] + bw / 2, cy + bw / 2 + 6 * u), (xs[-1] + bw / 2, ry), (xs[0] + bw / 2, ry)]
    d.line(pts, fill=acc + (230,), width=5 * u, joint="curve")
    arrow(d, xs[0] + bw / 2, ry, xs[0] + bw / 2, cy + bw / 2 + 8 * u, acc + (230,), 5 * u, 18 * u)


def motif_inference(d, x0, y0, x1, y1, acc, rng):
    u = S
    white = (255, 255, 255)
    cy = (y0 + y1) / 2
    mx0, mx1 = x0 + (x1 - x0) * 0.40, x0 + (x1 - x0) * 0.62
    ins = [(x0 + rng.uniform(0, 90) * u, cy + (k - 5.5) * 24 * u) for k in range(12)]
    for (x, y) in ins:
        d.line([x, y, mx0, cy + (y - cy) * 0.35], fill=white + (60,), width=2 * u)
    for (x, y) in ins:
        dot(d, x, y, 7 * u, white + (230,))
    d.rounded_rectangle([mx0, cy - 100 * u, mx1, cy + 100 * u], radius=22 * u,
                        fill=(255, 255, 255, 40), outline=white + (240,), width=4 * u)
    for k in range(3):
        yy = cy - 60 * u + k * 60 * u
        for j in range(3):
            xx = mx0 + (mx1 - mx0) * (j + 1) / 4
            dot(d, xx, yy, 9 * u, acc + (255,) if (j + k) % 2 == 0 else white + (230,))
    arrow(d, mx1 + 8 * u, cy, mx1 + 70 * u, cy, white + (230,), 5 * u, 20 * u)
    bx = mx1 + 100 * u
    heights = [60, 120, 40, 20]
    for k, hh in enumerate(heights):
        top = cy + 90 * u - hh * 1.4 * u
        d.rounded_rectangle([bx + k * 46 * u, top, bx + k * 46 * u + 32 * u, cy + 90 * u], radius=8 * u,
                            fill=(acc + (255,)) if k == 1 else (255, 255, 255, 120))


def motif_home(d, x0, y0, x1, y1, acc, rng):
    """Hub : une idee centrale reliee aux 8 categories, chacune dans sa teinte."""
    u = S
    white = (255, 255, 255)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    sats = [k for k, v in CATEGORIES.items() if v[2] not in ("home", "soon", "search", "calendar")]
    dot(d, cx, cy, 160 * u, None, white + (30,), 2 * u)
    pts = []
    for i, k in enumerate(sats):
        a = -math.pi / 2 + i * 2 * math.pi / len(sats)
        pts.append((k, cx + 260 * u * math.cos(a), cy + 130 * u * math.sin(a)))
    for k, x, y in pts:
        d.line([cx, cy, x, y], fill=hsl(CATEGORIES[k][1], 0.85, 0.65) + (170,), width=3 * u)
    for k, x, y in pts:
        c = hsl(CATEGORIES[k][1], 0.85, 0.62)
        dot(d, x, y, 24 * u, c + (255,), white + (255,), 4 * u)
    dot(d, cx, cy, 46 * u, acc + (255,), white + (255,), 5 * u)
    dot(d, cx, cy, 16 * u, white + (255,))


def dashed_rect(d, x0, y0, x1, y1, col, w, dash=14, gap=10):
    for (ax, ay, bx, by) in ((x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)):
        n = max(abs(bx - ax), abs(by - ay))
        pos = 0
        while pos < n:
            e = min(pos + dash, n)
            d.line([ax + (bx - ax) * pos / n, ay + (by - ay) * pos / n,
                    ax + (bx - ax) * e / n, ay + (by - ay) * e / n], fill=col, width=w)
            pos += dash + gap


def motif_soon(d, x0, y0, x1, y1, acc, rng):
    """Trois fiches d'articles a venir (pointilles) + une horloge."""
    u = S
    white = (255, 255, 255)
    cw, ch, gap = 150 * u, 190 * u, 34 * u
    total = 3 * cw + 2 * gap
    sx = (x0 + x1) / 2 - total / 2
    top = (y0 + y1) / 2 - ch / 2 + 34 * u
    for i in range(3):
        cx0 = sx + i * (cw + gap)
        on = i == 1
        col = (acc if on else white) + (230 if on else 110,)
        dashed_rect(d, cx0, top, cx0 + cw, top + ch, col, 4 * u, 16 * u, 11 * u)
        d.rounded_rectangle([cx0 + 16 * u, top + 16 * u, cx0 + cw - 16 * u, top + 86 * u], radius=8 * u,
                            fill=(acc if on else white) + (70 if on else 35,))
        for k, wbar in enumerate((118, 96, 60)):
            yy = top + 108 * u + k * 22 * u
            d.rounded_rectangle([cx0 + 16 * u, yy, cx0 + 16 * u + wbar * u * 0.98, yy + 9 * u], radius=4 * u,
                                fill=(acc if on else white) + (200 if on else 80,))
    mx = sx + cw + gap + cw / 2
    my = top - 52 * u
    dot(d, mx, my, 34 * u, (255, 255, 255, 30), acc + (255,), 5 * u)
    d.line([mx, my, mx, my - 20 * u], fill=white + (255,), width=5 * u)
    d.line([mx, my, mx + 15 * u, my + 8 * u], fill=white + (255,), width=5 * u)
    dot(d, mx, my, 4 * u, acc + (255,))


def motif_search(d, x0, y0, x1, y1, acc, rng):
    """Liste de resultats (la premiere en evidence) + une loupe."""
    u = S
    white = (255, 255, 255)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    lx0, rw, rh, gap = cx - 120 * u, 330 * u, 78 * u, 22 * u
    top = cy - (3 * rh + 2 * gap) / 2
    for i in range(3):
        ry = top + i * (rh + gap)
        on = i == 0
        c = (acc if on else white)
        d.rounded_rectangle([lx0, ry, lx0 + rw, ry + rh], radius=12 * u, fill=white + (28,),
                            outline=c + (230 if on else 100,), width=3 * u)
        d.rounded_rectangle([lx0 + 14 * u, ry + 14 * u, lx0 + 64 * u, ry + rh - 14 * u], radius=8 * u,
                            fill=c + (110 if on else 55,))
        for k, wbar in enumerate((190, 120)):
            yy = ry + 22 * u + k * 22 * u
            d.rounded_rectangle([lx0 + 82 * u, yy, lx0 + (82 + wbar) * u, yy + 10 * u], radius=5 * u,
                                fill=c + (230 if (on or k == 0) else 90,))
    mx, my, r = lx0 - 34 * u, top + rh / 2 - 4 * u, 58 * u
    a = math.radians(45)
    d.line([mx + r * math.cos(a), my + r * math.sin(a), mx + (r + 52 * u) * math.cos(a), my + (r + 52 * u) * math.sin(a)],
           fill=white + (255,), width=16 * u)
    dot(d, mx, my, r, (20, 40, 90, 150), white + (255,), 10 * u)
    dot(d, mx, my, r - 18 * u, None, acc + (150,), 3 * u)


def motif_calendar(d, x0, y0, x1, y1, acc, rng):
    """Calendrier (quelques jours en evidence) devant une pile de feuilles."""
    u = S
    white = (255, 255, 255)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    cw, ch = 380 * u, 300 * u
    bx0, by0 = cx - cw / 2, cy - ch / 2 + 6 * u
    for off in (28, 14):
        d.rounded_rectangle([bx0 + off * u, by0 - off * u, bx0 + cw + off * u, by0 + ch - off * u],
                            radius=18 * u, fill=white + (18,), outline=white + (70,), width=3 * u)
    d.rounded_rectangle([bx0, by0, bx0 + cw, by0 + ch], radius=18 * u, fill=(15, 30, 80, 150),
                        outline=white + (230,), width=4 * u)
    d.rounded_rectangle([bx0, by0, bx0 + cw, by0 + 58 * u], radius=18 * u, fill=acc + (230,))
    d.rectangle([bx0, by0 + 34 * u, bx0 + cw, by0 + 58 * u], fill=acc + (230,))
    for k in (0.25, 0.75):
        rx = bx0 + cw * k
        d.rounded_rectangle([rx - 6 * u, by0 - 18 * u, rx + 6 * u, by0 + 22 * u], radius=6 * u, fill=white + (255,))
    cols, rows = 7, 4
    gx0, gy0 = bx0 + 28 * u, by0 + 82 * u
    cell = (cw - 56 * u) / cols
    lit = {1, 4, 9, 10, 15, 19, 22, 26}
    for r in range(rows):
        for c in range(cols):
            i = r * cols + c
            x, y = gx0 + c * cell, gy0 + r * (cell * 0.92)
            on = i in lit
            d.rounded_rectangle([x + 4 * u, y + 4 * u, x + cell - 4 * u, y + cell * 0.92 - 4 * u], radius=6 * u,
                                fill=(acc + (255,)) if on else white + (45,))


def motif_notfound(d, x0, y0, x1, y1, acc, rng):
    """Feuille au coin corne portant un grand « 404 », avec une loupe barree."""
    u = S
    white = (255, 255, 255)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    pw, ph, fold = 300 * u, 320 * u, 70 * u
    px0, py0 = cx - pw / 2 - 30 * u, cy - ph / 2
    px1, py1 = px0 + pw, py0 + ph
    d.polygon([(px0, py0), (px1 - fold, py0), (px1, py0 + fold), (px1, py1), (px0, py1)],
              fill=(15, 30, 80, 150), outline=white + (230,), width=4 * u)
    d.polygon([(px1 - fold, py0), (px1 - fold, py0 + fold), (px1, py0 + fold)],
              fill=acc + (230,), outline=white + (230,), width=3 * u)
    font = load_font(FONT_BOLD, 112 * u)
    d.text((px0 + pw / 2, py0 + 150 * u), "404", font=font, fill=white + (255,), anchor="mm")
    for k, wbar in enumerate((190, 120)):
        yy = py0 + 232 * u + k * 26 * u
        d.rounded_rectangle([px0 + 34 * u, yy, px0 + (34 + wbar) * u, yy + 10 * u], radius=5 * u,
                            fill=(acc if k == 0 else white) + (200 if k == 0 else 80,))
    mx, my, r = px1 + 10 * u, py1 - 40 * u, 52 * u
    a = math.radians(45)
    d.line([mx + r * math.cos(a), my + r * math.sin(a), mx + (r + 48 * u) * math.cos(a), my + (r + 48 * u) * math.sin(a)],
           fill=white + (255,), width=15 * u)
    dot(d, mx, my, r, (20, 40, 90, 170), white + (255,), 9 * u)
    d.line([mx - 22 * u, my - 22 * u, mx + 22 * u, my + 22 * u], fill=acc + (255,), width=8 * u)
    d.line([mx - 22 * u, my + 22 * u, mx + 22 * u, my - 22 * u], fill=acc + (255,), width=8 * u)


MOTIFS = {"rag": motif_rag, "deep": motif_deep, "ml": motif_ml, "agentic": motif_agentic,
          "maths": motif_maths, "python": motif_python, "mlops": motif_mlops, "inference": motif_inference, "home": motif_home, "soon": motif_soon, "search": motif_search, "calendar": motif_calendar, "notfound": motif_notfound}


# --- rendu -----------------------------------------------------------------

def render(slug: str) -> Image.Image:
    name, hue, motif = CATEGORIES[slug]
    w, h = W * S, H * S
    c1, c2 = hsl(hue, 0.55, 0.14), hsl(hue + 0.08, 0.62, 0.30)
    bg = Image.new("RGB", (w, h))
    bd = ImageDraw.Draw(bg)
    for x in range(w):
        t = x / (w - 1)
        bd.line([x, 0, x, h], fill=tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3)))
    img = bg.convert("RGBA")

    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow, "RGBA")
    gcol = hsl(hue + 0.03, 0.8, 0.55)
    gx, gy = int(w * 0.78), int(h * 0.45)
    for r in range(900 * S // 2, 0, -12 * S):
        gd.ellipse([gx - r, gy - r, gx + r, gy + r], fill=gcol + (max(1, int(34 * (1 - r / (450 * S)) ** 1.5)),))
    img = Image.alpha_composite(img, glow)

    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer, "RGBA")
    accent = hsl(hue + 0.02, 0.9, 0.68)
    rng = random.Random(slug)
    MOTIFS[motif](d, int(w * 0.52), 30 * S, w - 70 * S, h - 30 * S, accent, rng)
    img = Image.alpha_composite(img, layer)

    d = ImageDraw.Draw(img)
    label = load_font(FONT_REG, 26 * S)
    d.text((MARGIN * S, 110 * S), "COURS & TUTORIELS", font=label, fill=accent)
    d.rectangle([MARGIN * S, 158 * S, (MARGIN + 90) * S, 164 * S], fill=accent)
    max_w = int(w * 0.46)
    for size in range(110, 59, -4):  # une seule ligne si possible
        font = load_font(FONT_BOLD, size * S)
        lines = wrap(d, name, font, max_w)
        if len(lines) == 1:
            break
    y = 186 * S
    for line in lines:
        d.text((MARGIN * S, y), line, font=font, fill=(255, 255, 255))
        y += int(size * 1.15 * S)
    return img.convert("RGB").resize((W, H), Image.LANCZOS)


def main() -> None:
    args = sys.argv[1:]
    if args == ["--list"]:
        for slug, (name, hue, _) in CATEGORIES.items():
            print(f"{slug:18} {name:18} teinte={hue}")
        return
    slugs = args or list(CATEGORIES)
    unknown = [s for s in slugs if s not in CATEGORIES]
    if unknown:
        sys.exit(f"ERREUR : categorie(s) inconnue(s) {unknown}. Connues : {', '.join(CATEGORIES)}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for slug in slugs:
        out = OUT_DIR / f"{slug}.png"
        render(slug).save(out, optimize=True)
        print(f"Banniere : {out} ({W}x{H}, {out.stat().st_size // 1024} Ko)")


if __name__ == "__main__":
    main()
