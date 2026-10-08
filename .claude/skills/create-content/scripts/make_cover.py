"""Genere l'image de couverture (image mise en avant WordPress) d'un article.

    make_cover.py <slug>

Lit le titre, le premier mot-cle et la `category` (optionnelle : slug de
categorie WordPress, ex. "rag" ; sa teinte est alors reprise) du frontmatter de
rag_data/content/<slug>/article.md et ecrit rag_data/content/<slug>/<slug>.png
(1200x630, ratio Open Graph). Aucune IA : la composition est un fond en
degrade + formes geometriques dont la teinte et la disposition sont derivees
du slug (meme slug -> meme image, articles differents -> images differentes).
Ne mentionne jamais le livre source.

<slug>.png (nom unique dans la mediatheque WP) est volontairement HORS de images/ : ce dossier ne contient que les
images citees dans le corps de l'article. /publish-content l'uploade comme
image mise en avant.
"""
from __future__ import annotations
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(next(p for p in _Path(__file__).resolve().parents if (p / ".claude" / "skills" / "_rag_lib").is_dir()) / ".claude" / "skills" / "_rag_lib"))
import venv_guard  # noqa: F401

import colorsys
import hashlib
import random
import re
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import yaml
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[4]
CONTENT_DIR = ROOT / "rag_data" / "content"
W, H = 1200, 630
MARGIN = 80

FONT_BOLD = ["C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/calibrib.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
FONT_REG = ["C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/calibri.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]


def load_font(candidates: list[str], size: int) -> ImageFont.ImageFont:
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def hsl(h: float, s: float, l: float) -> tuple[int, int, int]:
    r, g, b = colorsys.hls_to_rgb(h % 1.0, l, s)
    return int(r * 255), int(g * 255), int(b * 255)


def slug_hue(slug: str, category: str | None = None) -> float:
    """Teinte de la couverture. Avec une categorie connue (cf. CATEGORIES de
    make_category_cover.py), reprend sa teinte a +-0.025 pres (derive du slug) :
    l'article reste de la famille de couleur de sa categorie sans etre identique
    a ses voisins. Sinon : teinte issue du hash du slug."""
    h = int(hashlib.sha256(slug.encode("utf-8")).hexdigest(), 16)
    if category:
        from make_category_cover import CATEGORIES
        if category in CATEGORIES:
            return CATEGORIES[category][1] + ((h % 51) - 25) / 1000
    return (h % 360) / 360


def wp_fields(slug: str, fm: dict) -> dict:
    """Texte alternatif / Legende / Description de la mediatheque WP pour la
    couverture. Les trois champs sont toujours remplis, sans mention de livre."""
    title = str(fm.get("title", slug)).strip()
    desc = str(fm.get("description", "")).strip()
    return {
        "alt": title,
        "caption": title,
        "description": desc or title,
    }


def read_frontmatter(slug: str) -> dict:
    path = CONTENT_DIR / slug / "article.md"
    if not path.exists():
        sys.exit(f"ERREUR : {path} introuvable.")
    raw = path.read_text(encoding="utf-8")
    end = raw.find("\n---", 3)
    if not raw.startswith("---") or end == -1:
        sys.exit("ERREUR : article.md sans frontmatter YAML valide.")
    return yaml.safe_load(raw[3:end])


def wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int) -> list[str]:
    lines, cur = [], ""
    # Espace insecable avant la ponctuation haute : jamais un « : » en debut de ligne.
    text = re.sub(r" ([:;!?»])", " \\1", text)
    for word in text.split(" "):
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def fit_title(draw: ImageDraw.ImageDraw, title: str, max_w: int, max_h: int):
    for size in range(76, 35, -2):
        font = load_font(FONT_BOLD, size)
        lines = wrap(draw, title, font, max_w)
        line_h = int(size * 1.22)
        if len(lines) * line_h <= max_h and len(lines) <= 4:
            return font, lines, line_h
    font = load_font(FONT_BOLD, 36)
    return font, wrap(draw, title, font, max_w)[:4], 44


def render(slug: str, title: str, tag: str | None, category: str | None = None) -> Image.Image:
    seed = int(hashlib.sha256(slug.encode("utf-8")).hexdigest(), 16)
    rng = random.Random(seed)
    hue = slug_hue(slug, category)

    # Fond : degrade diagonal sombre, deux teintes voisines.
    c1, c2 = hsl(hue, 0.55, 0.16), hsl(hue + 0.08, 0.60, 0.30)
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        for x in range(W):
            t = (x / W * 0.6 + y / H * 0.4)
            px[x, y] = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))

    # Formes decoratives translucides (cercles + anneaux), cote droit surtout.
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _ in range(7):
        r = rng.randint(70, 260)
        cx, cy = rng.randint(int(W * 0.45), W + 80), rng.randint(-60, H + 60)
        col = hsl(hue + rng.uniform(-0.05, 0.12), 0.65, rng.uniform(0.45, 0.7))
        if rng.random() < 0.5:
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col + (rng.randint(35, 70),))
        else:
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col + (130,), width=rng.randint(3, 8))
    img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")

    draw = ImageDraw.Draw(img)
    accent = hsl(hue + 0.02, 0.85, 0.65)

    # Etiquette thematique (premier mot-cle) + filet d'accent.
    y = MARGIN
    if tag:
        tag_font = load_font(FONT_REG, 28)
        draw.text((MARGIN, y), tag.upper(), font=tag_font, fill=accent)
        y += 44
    draw.rectangle([MARGIN, y, MARGIN + 90, y + 6], fill=accent)
    y += 40

    # Titre : plus grande taille qui tient dans la zone (largeur ~70 %).
    font, lines, line_h = fit_title(draw, title, int(W * 0.68), H - y - MARGIN)
    for line in lines:
        draw.text((MARGIN, y), line, font=font, fill=(255, 255, 255))
        y += line_h
    return img


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    slug = sys.argv[1]
    fm = read_frontmatter(slug)
    keywords = fm.get("keywords") or []
    tag = str(keywords[0]) if keywords else None
    category = fm.get("category")
    img = render(slug, str(fm.get("title", slug)), tag, str(category) if category else None)
    out = CONTENT_DIR / slug / f"{slug}.png"
    img.save(out, optimize=True)
    print(f"Couverture : {out} ({W}x{H}, {out.stat().st_size // 1024} Ko)")


if __name__ == "__main__":
    main()
