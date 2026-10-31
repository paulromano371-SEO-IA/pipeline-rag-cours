"""Etape 1 de /create-content : reperer, parmi les contenus deja ecrits sous
`rag_data/content/work/`, ceux qui sont PROCHES du sujet du nouveau contenu (les
« voisins »), pour ne jamais redire ce qu'ils disent deja.

Lecture seule, aucun appel LLM. Ne modifie ni ne supprime aucun article
existant : n'ecrit que `rag_data/content/work/<slug>/voisins.json`.

Proximite d'un ancien article avec l'expression, mesuree de trois facons :
  - chunks : part des chunks que le RAG remonte pour l'expression qui ont deja
    ete CITES (`<!--src:Sxxx-->`) par l'ancien article ;
  - sections : meme mesure, au niveau (pdf, section) -- plus robuste quand
    deux articles piochent des chunks voisins d'une meme section ;
  - semantique : similarite cosinus entre l'expression et le titre + description
    + mots-cles + H2 de l'ancien article (meme modele d'embedding que le RAG).
Un article est « voisin » si l'une des trois mesures passe son seuil. Un
article sur un autre sujet (ex. RAG quand on traite d'inference) ressort a ~0
partout et est ignore.

AUCUN VOISIN n'est PAS une erreur : le script le dit, ecrit un `voisins.json`
vide et sort en code 0 -- le nouveau contenu part alors d'une page blanche.

Usage:
    python scan_existing.py "<expression>" [--slug S] [--threshold-chunks F]
                            [--threshold-sections F] [--threshold-semantic F]
                            [--max-neighbors N]
"""
from __future__ import annotations
# Garde-fou : force l'interpreteur du projet (.venv-rag), voir _rag_lib/venv_guard.py
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(next(p for p in _Path(__file__).resolve().parents if (p / ".claude" / "skills" / "_rag_lib").is_dir()) / ".claude" / "skills" / "_rag_lib"))
import venv_guard  # noqa: F401

import argparse
import json
import re
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

_PROJECT_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(_PROJECT_ROOT / ".claude" / "skills" / "_rag_lib"))
sys.path.insert(0, str(_PROJECT_ROOT / "tools"))

import paths  # noqa: E402
from graph_store import cosine_similarities  # noqa: E402
from retrieval import (  # noqa: E402
    DEFAULT_MAX_SECTIONS,
    DEFAULT_RELEVANCE_MARGIN,
    DEFAULT_RESULT_CAP,
    DEFAULT_SEARCH_TOP_K,
    retrieve,
)
from vector_store import embed_texts  # noqa: E402
from gather import slugify_expression  # noqa: E402

CONTENT_DIR = paths.RAG_DATA_DIR / "content" / "work"

_SRC_RE = re.compile(r"<!--\s*src:\s*([^>]*?)\s*-->")
_FENCE_RE = re.compile(r"^```[^\n]*\n.*?^```[ \t]*$", re.MULTILINE | re.DOTALL)
_H2_RE = re.compile(r"^## (?!#)(.+?)\s*$", re.MULTILINE)
_H3_RE = re.compile(r"^### (?!#)(.+?)\s*$", re.MULTILINE)

# Seuils par defaut : a recalibrer au besoin avec les chiffres affiches.
DEFAULT_THRESHOLD_CHUNKS = 0.10
DEFAULT_THRESHOLD_SECTIONS = 0.25
DEFAULT_THRESHOLD_SEMANTIC = 0.55
DEFAULT_MAX_NEIGHBORS = 6


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}, text
    fm: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "-")):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip("\"'")
    return fm, text[m.end():]


def load_article(slug_dir: Path) -> dict | None:
    """Lit un ancien contenu : titre, mots-cles, plan, chunks cites. Ne plante
    jamais sur un dossier incomplet (retourne None s'il n'y a pas d'article)."""
    article_path = slug_dir / "article.md"
    if not article_path.exists():
        return None
    try:
        raw = article_path.read_text(encoding="utf-8")
    except OSError:
        return None
    fm, body = parse_frontmatter(raw)
    body_nocode = _FENCE_RE.sub("", body)
    h2 = [t.strip() for t in _H2_RE.findall(body_nocode)]
    h3 = [t.strip() for t in _H3_RE.findall(body_nocode)]

    cited_ids: set[str] = set()
    for m in _SRC_RE.finditer(body):
        cited_ids.update(s for s in re.split(r"[,\s]+", m.group(1).strip()) if s)

    cited_chunks: set[tuple[str, int]] = set()
    cited_sections: set[tuple[str, str]] = set()
    dossier_path = slug_dir / "dossier.json"
    if dossier_path.exists():
        try:
            items = json.loads(dossier_path.read_text(encoding="utf-8")).get("items", [])
        except (OSError, json.JSONDecodeError):
            items = []
        for it in items:
            if it.get("id") in cited_ids:
                cited_chunks.add((it["document_id"], it["chunk_index"]))
                cited_sections.add((it["document_id"], it.get("section", "")))

    return {
        "slug": slug_dir.name,
        "title": fm.get("title", slug_dir.name),
        "description": fm.get("description", ""),
        "keywords": fm.get("keywords", ""),
        "h2": h2,
        "h3": h3,
        "cited_chunks": cited_chunks,
        "cited_sections": cited_sections,
        "article_path": str(article_path),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("expression", type=str)
    ap.add_argument("--slug", type=str, default=None, help="slug du nouveau contenu (defaut : derive de l'expression) ; il est exclu du scan")
    ap.add_argument("--threshold-chunks", type=float, default=DEFAULT_THRESHOLD_CHUNKS)
    ap.add_argument("--threshold-sections", type=float, default=DEFAULT_THRESHOLD_SECTIONS)
    ap.add_argument("--threshold-semantic", type=float, default=DEFAULT_THRESHOLD_SEMANTIC)
    ap.add_argument("--max-neighbors", type=int, default=DEFAULT_MAX_NEIGHBORS)
    ap.add_argument("--db-path", type=str, default=str(paths.VECTOR_DB_PATH))
    ap.add_argument("--graph-db-path", type=str, default=str(paths.GRAPH_DB_PATH))
    ap.add_argument("--out-dir", type=str, default=None, help="defaut : rag_data/content/work/<slug>/")
    args = ap.parse_args()

    slug = args.slug or slugify_expression(args.expression)
    out_dir = Path(args.out_dir) if args.out_dir else CONTENT_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    articles = []
    if CONTENT_DIR.is_dir():
        for d in sorted(p for p in CONTENT_DIR.iterdir() if p.is_dir() and p.name != slug):
            a = load_article(d)
            if a is not None:
                articles.append(a)

    def write_result(neighbors: list[dict]) -> None:
        (out_dir / "voisins.json").write_text(
            json.dumps({"expression": args.expression, "slug": slug, "neighbors": neighbors},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(f"Scan des contenus existants pour {args.expression!r} (slug {slug})")
    print(f"  Articles existants examines : {len(articles)}")
    if not articles:
        write_result([])
        print("  AUCUN VOISIN : aucun contenu existant sous rag_data/content/work/ -- page blanche, ce n'est pas bloquant.")
        return 0

    # --- sonde : ce que le RAG remonte pour l'expression ---------------------
    probe = retrieve(
        args.expression,
        db_path=Path(args.db_path),
        graph_db_path=Path(args.graph_db_path),
        read_only=True,
        search_top_k=DEFAULT_SEARCH_TOP_K,
        max_sections=DEFAULT_MAX_SECTIONS,
        result_cap=DEFAULT_RESULT_CAP,
        relevance_margin=DEFAULT_RELEVANCE_MARGIN,
    )
    probe_chunks = {(r.document_id, r.chunk_index) for r in probe}
    probe_sections = {(r.document_id, " > ".join(r.heading_trail)) for r in probe}

    # --- semantique : expression contre l'identite de chaque article ---------
    texts = [args.expression] + [
        f"{a['title']}. {a['description']} {a['keywords']} " + " ; ".join(a["h2"]) for a in articles
    ]
    embeddings = embed_texts(texts)
    sems = cosine_similarities(embeddings[0], embeddings[1:]) if len(embeddings) > 1 else []

    scored = []
    for a, sem in zip(articles, sems):
        chunk_ov = len(probe_chunks & a["cited_chunks"]) / len(probe_chunks) if probe_chunks else 0.0
        sect_ov = len(probe_sections & a["cited_sections"]) / len(probe_sections) if probe_sections else 0.0
        reasons = []
        if chunk_ov >= args.threshold_chunks:
            reasons.append("chunks")
        if sect_ov >= args.threshold_sections:
            reasons.append("sections")
        if float(sem) >= args.threshold_semantic:
            reasons.append("semantique")
        scored.append({
            "slug": a["slug"],
            "title": a["title"],
            "article_path": a["article_path"],
            "h2": a["h2"],
            "h3": a["h3"],
            "keywords": a["keywords"],
            "cited_chunks": sorted(f"{d}::{i}" for d, i in a["cited_chunks"]),
            "chunk_overlap": round(chunk_ov, 3),
            "section_overlap": round(sect_ov, 3),
            "semantic": round(float(sem), 3),
            "reasons": reasons,
        })

    scored.sort(key=lambda s: (-len(s["reasons"]), -max(s["chunk_overlap"], s["section_overlap"], s["semantic"])))
    neighbors = [s for s in scored if s["reasons"]][: args.max_neighbors]
    ignored = [s for s in scored if s not in neighbors]

    write_result(neighbors)

    print(f"  Chunks remontes par la sonde : {len(probe_chunks)} dans {len(probe_sections)} section(s)")
    print(f"  Seuils : chunks >= {args.threshold_chunks}, sections >= {args.threshold_sections}, semantique >= {args.threshold_semantic}")
    if neighbors:
        print(f"  VOISINS ({len(neighbors)}) -- a lire en entier avant de planifier :")
        for n in neighbors:
            print(f"    - {n['slug']}  [{', '.join(n['reasons'])}]  chunks={n['chunk_overlap']:.2f} "
                  f"sections={n['section_overlap']:.2f} semantique={n['semantic']:.2f}")
            print(f"        titre : {n['title']}")
            print(f"        fichier : {n['article_path']}")
    else:
        print("  AUCUN VOISIN : aucun contenu existant n'est proche de ce sujet -- page blanche, ce n'est pas bloquant.")
    if ignored:
        print(f"  Ignores car hors sujet ({len(ignored)}) :")
        for s in ignored:
            print(f"    - {s['slug']}  chunks={s['chunk_overlap']:.2f} sections={s['section_overlap']:.2f} semantique={s['semantic']:.2f}")
    print(f"  Ecrit : {out_dir / 'voisins.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
