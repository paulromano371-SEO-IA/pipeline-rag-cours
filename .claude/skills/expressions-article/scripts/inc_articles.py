"""Lecture seule des articles de rag_data/content/work/<slug>/article.md.

Module partagé par expressions_article.py, inc_extraction.py et inc_couverture.py.
Aucune écriture : les articles existants ne sont jamais modifiés.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import paths

CONTENT_DIR = paths.RAG_DATA_DIR / "content" / "work"
PUBLISHED_DIR = paths.RAG_DATA_DIR / "content" / "published"  # trace published/<slug>.json = article publié

_SRC_MARKER_RE = re.compile(r"<!--src:[^>]*-->")
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?", re.S)
_KEY_RE = re.compile(r"^(\w+):\s*(.*)$")


def fold(text: str) -> str:
    """Minuscules sans accents, espaces normalisés (même règle que check_gap.py)."""
    s = unicodedata.normalize("NFKD", text.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip()


def word_in(needle_folded: str, haystack_folded: str) -> bool:
    """Vrai si `needle` apparaît comme mot ou groupe de mots entier dans `haystack`
    (les deux déjà passés par `fold`)."""
    if not needle_folded:
        return False
    pattern = r"(?<!\w)" + re.escape(needle_folded) + r"(?!\w)"
    return re.search(pattern, haystack_folded) is not None


@dataclass
class Article:
    slug: str
    title: str
    keywords: list[str]
    related: list[str]
    body: str  # sans frontmatter ni marqueurs <!--src:...-->


def _list_value(raw: str) -> list[str]:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        return [item.strip().strip("\"'") for item in raw[1:-1].split(",") if item.strip()]
    return [raw.strip("\"'")] if raw else []


def parse_article(slug: str, raw: str) -> Article:
    meta: dict[str, str] = {}
    body = raw
    match = _FRONTMATTER_RE.match(raw)
    if match:
        for line in match.group(1).splitlines():
            kv = _KEY_RE.match(line)
            if kv:
                meta[kv.group(1)] = kv.group(2).strip()
        body = raw[match.end():]
    body = _SRC_MARKER_RE.sub("", body)
    return Article(
        slug=slug,
        title=meta.get("title", "").strip("\"'"),
        keywords=_list_value(meta.get("keywords", "")),
        related=_list_value(meta.get("related", "")),
        body=body,
    )


def article_path(slug: str) -> Path:
    return CONTENT_DIR / slug / "article.md"


def load_article(slug: str) -> Article:
    return parse_article(slug, article_path(slug).read_text(encoding="utf-8"))


def list_slugs() -> list[str]:
    """Slugs (noms de dossier) des articles, ordre alphabétique : même numérotation que /maillage."""
    if not CONTENT_DIR.is_dir():
        return []
    return sorted(d.name for d in CONTENT_DIR.iterdir() if (d / "article.md").is_file())


def load_all() -> list[Article]:
    return [load_article(slug) for slug in list_slugs()]


def is_published(slug: str) -> bool:
    """Publié = trace rag_data/content/published/<slug>.json présente (lecture directe, sans importer /publish-content)."""
    return (PUBLISHED_DIR / f"{slug}.json").is_file()


def catalogue(articles: list[Article]) -> str:
    """Liste des articles existants (slug et titre) : ce que le LLM consulte pour savoir si un sujet est déjà traité."""
    return "\n".join(f"- {a.slug} — {a.title}" for a in articles)
