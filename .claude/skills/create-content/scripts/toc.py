"""Sommaire cliquable d'un article /create-content -- fonction partagee entre
l'ecriture (`toc.py <slug>`), `verify.py` (controle) et la publication WordPress
(ajout d'un `id` a chaque H2, voir publish-content).

Format impose, place JUSTE AVANT `## L'essentiel en 30 secondes` :

    **Sommaire**

    - [Titre du H2](#id-du-h2)
    - ...

(Markdown pur : le convertisseur WordPress ne pose pas d'`id` sur les titres,
c'est lui qui les ajoute a la publication avec `heading_id()`, la meme fonction.)

Usage:
    python toc.py <slug_ou_dossier> [--article article.md] [--check]
"""
from __future__ import annotations
import re
import sys
import unicodedata
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
CONTENT_DIR = _PROJECT_ROOT / "rag_data" / "content"

ESSENTIEL_TITLE = "L'essentiel en 30 secondes"
_H2_RE = re.compile(r"^## (?!#)(.+?)\s*$", re.MULTILINE)
_FENCE_RE = re.compile(r"^```[^\n]*\n.*?^```[ \t]*$", re.MULTILINE | re.DOTALL)
_TOC_RE = re.compile(r"\*\*Sommaire\*\*\s*\n\n(?:- \[[^\]]*\]\(#[^)]*\)\s*\n?)+", re.MULTILINE)
_LINK_RE = re.compile(r"- \[([^\]]*)\]\(#([^)]*)\)")


def norm_title(title: str) -> str:
    """Titre compare sans typographie : apostrophes droites, minuscules."""
    return title.replace("’", "'").strip().lower()


def is_essentiel(title: str) -> bool:
    return norm_title(title) == norm_title(ESSENTIEL_TITLE)


def heading_id(title: str) -> str:
    """Identifiant d'ancre ASCII : accents retires, tout hors [a-z0-9] -> '-'."""
    s = unicodedata.normalize("NFKD", title.lower().replace("’", "'"))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "section"


def plain_title(title: str) -> str:
    """Titre sans ponctuation Markdown inline (`code`, **gras**, *italique*)."""
    return re.sub(r"[`*_]", "", title).strip()


def h2_titles(body: str) -> list[str]:
    return [plain_title(m.group(1)) for m in _H2_RE.finditer(_FENCE_RE.sub("", body))]


def unique_ids(titles: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for t in titles:
        base = heading_id(t)
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.append(base if n == 0 else f"{base}-{n + 1}")
    return out


def build_toc(titles: list[str]) -> str:
    ids = unique_ids(titles)
    lines = ["**Sommaire**", ""] + [f"- [{t}](#{i})" for t, i in zip(titles, ids)]
    return "\n".join(lines) + "\n\n"


def parse_toc(body: str) -> list[tuple[str, str]] | None:
    """Entrees (titre, ancre) du sommaire de l'article, ou None s'il n'y en a pas."""
    m = _TOC_RE.search(body)
    if not m:
        return None
    return _LINK_RE.findall(m.group(0))


def apply_toc(text: str) -> tuple[str, str]:
    """Retourne (texte_mis_a_jour, message). Remplace le sommaire existant ou
    l'insere juste avant le H2 `L'essentiel en 30 secondes`."""
    titles = h2_titles(text)
    if not titles:
        return text, "ECHEC : aucun H2 dans l'article."
    essentiel = next((m for m in _H2_RE.finditer(text) if is_essentiel(plain_title(m.group(1)))), None)
    if essentiel is None:
        return text, f"ECHEC : le H2 '## {ESSENTIEL_TITLE}' est absent -- il est obligatoire."
    text_wo = _TOC_RE.sub("", text)
    essentiel = next(m for m in _H2_RE.finditer(text_wo) if is_essentiel(plain_title(m.group(1))))
    toc = build_toc(titles)
    updated = text_wo[:essentiel.start()] + toc + text_wo[essentiel.start():]
    return updated, f"Sommaire ecrit : {len(titles)} entree(s)."


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="slug (sous rag_data/content/) ou chemin du dossier de contenu")
    ap.add_argument("--article", default="article.md")
    ap.add_argument("--check", action="store_true", help="n'ecrit rien, signale seulement un sommaire absent ou obsolete")
    args = ap.parse_args()
    content_dir = Path(args.target) if Path(args.target).is_dir() else CONTENT_DIR / args.target
    path = content_dir / args.article
    if not path.exists():
        print(f"ECHEC : fichier introuvable : {path}")
        return 1
    text = path.read_text(encoding="utf-8")
    updated, msg = apply_toc(text)
    if msg.startswith("ECHEC"):
        print(msg)
        return 2
    if args.check:
        print("Sommaire a jour." if updated == text else "Sommaire absent ou obsolete.")
        return 0 if updated == text else 2
    if updated != text:
        path.write_text(updated, encoding="utf-8")
    print(msg + (" (article mis a jour)" if updated != text else " (deja a jour)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
