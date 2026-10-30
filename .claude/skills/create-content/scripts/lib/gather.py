"""Etape 1 de /create-content : constitue le DOSSIER de matiere premiere d'un
contenu web a partir du RAG -- lecture seule, aucun appel LLM, aucune ecriture
dans les bases ni dans `rag_data/work/`.

Pour une expression (ex. "comment fonctionne une regression lineaire ?") et
une liste de sous-requetes (`--query`, repetable : definition, formule, code,
hypotheses, limites...), appelle `tools/retrieval.retrieve()` une fois par
requete, fusionne et dedoublonne les chunks, puis ecrit sous
`rag_data/content/work/<slug>/` :

    dossier.md    ce que le redacteur lit : chunks regroupes par pdf puis par
                  section, chacun avec un identifiant stable [S012], son type
                  (texte/code/formule/image), et pour une image son chemin web
    dossier.json  la meme chose, structuree : entree de verify.py
    images/       copie des images referencees (noms uniques par pdf), pour
                  que l'article final soit autonome et publiable tel quel

Chaque sous-requete porte la FACETTE qu'elle sert, sous la forme
`--query "nature::qu'est-ce que ..."` (facette = une ligne du catalogue de
l'intention, voir SKILL.md etape 2). Le compte rendu donne la couverture par
facette ; une requete sans `::` est signalee (facette non etiquetee).

Le dossier est la SEULE source de faits de l'article : verify.py controle que
code, formules et images de l'article en proviennent.

Usage:
    python gather.py "<expression>" --query "<facette>::<sous-requete>" [--query ...]
                     [--search-top-k N] [--max-sections N] [--result-cap N]
                     [--relevance-margin F] [--out-dir ...]
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
import shutil
import sys
import unicodedata
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

_PROJECT_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(_PROJECT_ROOT / ".claude" / "skills" / "_rag_lib"))
sys.path.insert(0, str(_PROJECT_ROOT / "tools"))

import paths  # noqa: E402
from slugs import slugify_expression  # noqa: E402  (réexporté : etape0_cadrage importe depuis gather)
from retrieval import (  # noqa: E402
    DEFAULT_MAX_SECTIONS,
    DEFAULT_RELEVANCE_MARGIN,
    DEFAULT_RESULT_CAP,
    DEFAULT_SEARCH_TOP_K,
    RetrievedChunk,
    retrieve,
)

CONTENT_DIR = paths.RAG_DATA_DIR / "content" / "work"

DESCRIPTION_MARKER = "<!-- rag-nottext:description -->"
OCR_MARKER = "<!-- rag-nottext:ocr -->"
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_FENCE_RE = re.compile(r"^```", re.MULTILINE)

# --- signaux de matiere distinctive (deterministe, purement heuristique) ---
# Objectif : reperer les chunks qui portent un chiffre precis, un benchmark,
# une entite nommee ou un cas concret -- ce que les pdf source (souvent en
# anglais, non indexes par les moteurs de recherche) apportent et qu'un savoir
# generique deja disponible partout n'apporte pas. Un simple signal pour aider
# le redacteur a prioriser, jamais un jugement : un chunk sans signal peut
# rester tres pertinent (definition necessaire, code, etc.).
# Note : (?!\s+[A-ZÀ-Ü]) ecarte les numeros de section/listing ("5.11
# Polymorphisme", "Listing 5.12 FoodPreferences.py") qui matcheraient sinon
# le motif decimal sans porter aucun chiffre distinctif.
_NUM_SIGNAL_RE = re.compile(r"\b\d+[.,]\d+\b(?!\s+[A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜŸÇ])|\b\d+\s?%|\b(?:19|20)\d{2}\b")
_TWO_CAP_RE = re.compile(
    r"\b([A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜŸÇ][a-zà-öø-ÿ]+)\s+([A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜŸÇ][a-zà-öø-ÿ]+)\b"
)
_ACRONYM_RE = re.compile(r"\b[A-Z]{3,}\b")
_STOPWORDS_CAP = {
    "le", "la", "les", "un", "une", "des", "ce", "cette", "ces", "cet", "il", "elle",
    "on", "nous", "vous", "ils", "elles", "dans", "sur", "pour", "avec", "sans",
    "mais", "donc", "or", "ni", "car", "chaque", "tout", "tous", "toute", "toutes",
    "ainsi", "cela", "son", "sa", "ses", "notre", "votre", "leur", "leurs", "du",
    "de", "au", "aux", "et", "que", "qui", "dont", "où", "the", "a", "an", "listing",
    "figure", "chapitre",
}
_GENERIC_ACRONYMS = {
    "RAG", "LLM", "POO", "API", "PDF", "URL", "HTML", "JSON", "CSV", "SQL", "IA",
    "ACP", "OOP", "CPU", "GPU", "RAM", "IDE", "CLI", "SDK", "MRO",
}


def has_number_signal(text: str) -> bool:
    return bool(_NUM_SIGNAL_RE.search(text))


def has_named_entity_signal(text: str) -> bool:
    for w1, w2 in _TWO_CAP_RE.findall(text):
        if w1.lower() not in _STOPWORDS_CAP and w2.lower() not in _STOPWORDS_CAP:
            return True
    return any(a not in _GENERIC_ACRONYMS for a in _ACRONYM_RE.findall(text))


def document_title(document_id: str) -> str:
    """Titre lisible du pdf, si `meta.json` en fournit un ; sinon le
    `document_id` sans son suffixe de hash. Ne plante jamais."""
    try:
        meta = json.loads((paths.work_dir_for(document_id) / "meta.json").read_text(encoding="utf-8"))
        for key in ("title", "titre", "source_title"):
            if isinstance(meta.get(key), str) and meta[key].strip():
                return meta[key].strip()
    except Exception:  # noqa: BLE001 -- meta.json est facultatif ici
        pass
    return re.sub(r"__[0-9a-f]{8}$", "", document_id).replace("_", " ")


def clean_text(text: str) -> str:
    """Rend le texte d'un chunk lisible par le redacteur : les marqueurs HTML
    internes de /rag-nottext deviennent des etiquettes explicites."""
    text = text.replace(DESCRIPTION_MARKER, "\n[DESCRIPTION DU PIPELINE]")
    text = text.replace(OCR_MARKER, "\n[TRANSCRIPTION OCR]")
    return text.strip()


def content_type(r: RetrievedChunk) -> str:
    tags = [t for t, on in (("code", r.has_code), ("formule", r.has_formula), ("image", r.has_image)) if on]
    return "+".join(tags) if tags else "texte"


def fence_balanced(text: str) -> bool:
    return len(_FENCE_RE.findall(text)) % 2 == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("expression", type=str)
    ap.add_argument("--query", action="append", default=[], help="'<facette>::<sous-requete>' (repetable) ; l'expression elle-meme est toujours interrogee en premier")
    ap.add_argument("--search-top-k", type=int, default=DEFAULT_SEARCH_TOP_K)
    ap.add_argument("--max-sections", type=int, default=DEFAULT_MAX_SECTIONS)
    ap.add_argument("--result-cap", type=int, default=DEFAULT_RESULT_CAP)
    ap.add_argument("--relevance-margin", type=float, default=DEFAULT_RELEVANCE_MARGIN)
    ap.add_argument("--db-path", type=str, default=str(paths.VECTOR_DB_PATH))
    ap.add_argument("--graph-db-path", type=str, default=str(paths.GRAPH_DB_PATH))
    ap.add_argument("--out-dir", type=str, default=None, help="defaut : rag_data/content/work/<slug>/")
    ap.add_argument("--voisins", type=str, default=None, help="voisins.json produit par scan_existing.py (defaut : <out-dir>/voisins.json s'il existe)")
    args = ap.parse_args()

    slug = slugify_expression(args.expression)
    out_dir = Path(args.out_dir) if args.out_dir else CONTENT_DIR / slug
    # sous-requetes etiquetees par facette : "facette::requete" (sans "::" = non etiquetee)
    query_facet: dict[str, str | None] = {args.expression: None}
    for raw_q in args.query:
        facet, sep, text = raw_q.partition("::")
        if not sep:
            facet, text = None, raw_q
        text = text.strip()
        if text and text != args.expression and text not in query_facet:
            query_facet[text] = facet.strip() if facet and facet.strip() else None
    queries = list(query_facet)
    untagged = [q for q in queries[1:] if query_facet[q] is None]

    # --- voisins : chunks deja cites par des contenus proches (lecture seule) --
    # Absence de voisins.json, ou liste vide : cas normal (page blanche), jamais bloquant.
    consumed: dict[tuple[str, int], list[str]] = {}
    neighbor_slugs: list[str] = []
    voisins_path = Path(args.voisins) if args.voisins else out_dir / "voisins.json"
    if voisins_path.exists():
        try:
            for nb in json.loads(voisins_path.read_text(encoding="utf-8")).get("neighbors", []):
                neighbor_slugs.append(nb["slug"])
                for ref in nb.get("cited_chunks", []):
                    doc_id, _, idx = ref.rpartition("::")
                    consumed.setdefault((doc_id, int(idx)), []).append(nb["slug"])
        except (OSError, ValueError, KeyError):
            print(f"  ATTENTION : {voisins_path} illisible -- dossier constitue sans marquage des voisins.")
            consumed, neighbor_slugs = {}, []

    # --- recuperation multi-requetes, fusion + dedoublonnage ---------------
    merged: dict[tuple[str, int], dict] = {}
    per_query_counts: dict[str, int] = {}
    for q in queries:
        results = retrieve(
            q,
            db_path=Path(args.db_path),
            graph_db_path=Path(args.graph_db_path),
            search_top_k=args.search_top_k,
            max_sections=args.max_sections,
            result_cap=args.result_cap,
            relevance_margin=args.relevance_margin,
        )
        per_query_counts[q] = (len(results), sum(1 for r in results if (r.document_id, r.chunk_index) not in consumed))
        for r in results:
            key = (r.document_id, r.chunk_index)
            entry = merged.get(key)
            if entry is None:
                merged[key] = {"chunk": r, "queries": [q], "best": r.score, "reasons": {r.reason}}
            else:
                if q not in entry["queries"]:
                    entry["queries"].append(q)
                entry["reasons"].add(r.reason)
                if r.score is not None and (entry["best"] is None or r.score > entry["best"]):
                    entry["best"] = r.score

    if not merged:
        print(f"ECHEC : aucun chunk trouve pour {args.expression!r} -- le corpus ne couvre pas ce sujet "
              "(ou la base est vide / en cours d'ingestion).")
        return 3

    # --- regroupement (pdf, section), sections classees par pertinence ---
    sections: dict[tuple[str, str], list[dict]] = {}
    for entry in merged.values():
        c = entry["chunk"]
        sections.setdefault((c.document_id, " > ".join(c.heading_trail)), []).append(entry)

    def section_rank(item: tuple[tuple[str, str], list[dict]]) -> tuple[int, float]:
        entries = item[1]
        hits = max(len(e["queries"]) for e in entries)
        best = max((e["best"] for e in entries if e["best"] is not None), default=0.0)
        return (-hits, -best)

    ordered_sections = sorted(sections.items(), key=section_rank)

    # --- numerotation stable + copie des images ----------------------------
    (out_dir / "images").mkdir(parents=True, exist_ok=True)
    doc_short: dict[str, str] = {}
    items: list[dict] = []
    next_id = 1
    counts = {"texte": 0, "code": 0, "formule": 0, "image": 0}
    truncated_fences: list[str] = []

    for (doc_id, trail), entries in ordered_sections:
        short = doc_short.setdefault(doc_id, f"b{len(doc_short) + 1}")
        for entry in sorted(entries, key=lambda e: e["chunk"].chunk_index):
            c: RetrievedChunk = entry["chunk"]
            text = clean_text(c.text)
            images = []
            work_dir = paths.work_dir_for(doc_id)

            def _rewrite(m: re.Match) -> str:
                alt, rel = m.group(1), m.group(2)
                src = work_dir / rel
                if not src.exists():
                    images.append({"src": rel, "web_path": None, "alt": alt, "missing": True})
                    return m.group(0)
                web_name = f"{short}_{Path(rel).name}"
                shutil.copy2(src, out_dir / "images" / web_name)
                images.append({"src": rel, "web_path": f"images/{web_name}", "alt": alt, "missing": False})
                return f"![{alt}](images/{web_name})"

            text = _IMAGE_RE.sub(_rewrite, text)
            ctype = content_type(c)
            for t in ("code", "formule", "image"):
                if t in ctype:
                    counts[t] += 1
            if ctype == "texte":
                counts["texte"] += 1
            if not fence_balanced(text):
                truncated_fences.append(f"S{next_id:03d}")
            signals = {
                "has_number": has_number_signal(text),
                "has_named_entity": has_named_entity_signal(text),
            }
            items.append({
                "id": f"S{next_id:03d}",
                "document_id": doc_id,
                "book": document_title(doc_id),
                "section": trail,
                "chunk_index": c.chunk_index,
                "type": ctype,
                "has_code": c.has_code,
                "has_formula": c.has_formula,
                "has_image": c.has_image,
                "score": None if entry["best"] is None else round(entry["best"], 3),
                "found_by": sorted(entry["reasons"]),
                "queries": entry["queries"],
                "facets": sorted({query_facet[q] for q in entry["queries"] if query_facet.get(q)}),
                "images": images,
                "text": text,
                "distinctive_signals": signals,
                "distinctive": signals["has_number"] or signals["has_named_entity"],
                "consumed_by": sorted(set(consumed.get((doc_id, c.chunk_index), []))),
            })
            next_id += 1

    related_concepts = sorted({
        r.split(":", 1)[1] for e in merged.values() for r in e["reasons"] if r.startswith("concept:")
    })
    books = sorted({(it["document_id"], it["book"]) for it in items})

    # --- signaux de diversite et de matiere distinctive (voir plus haut) ----
    # book_coverage : pdf retrouves par recherche semantique directe (pas
    # seulement rattaches via section/graphe de concepts) -- un sujet couvert
    # par un seul pdf est un signal de risque "commodity" (savoir standard,
    # difficile a differencier) ; couvert par plusieurs, un signal positif.
    searched_books = {it["document_id"] for it in items if any(r == "search" for r in it["found_by"])}
    book_coverage = len(searched_books)
    distinctive_count = sum(1 for it in items if it["distinctive"])

    (out_dir / "dossier.json").write_text(
        json.dumps({
            "expression": args.expression,
            "slug": slug,
            "queries": queries,
            "query_facets": query_facet,
            "neighbors": neighbor_slugs,
            "books": [{"document_id": d, "title": t} for d, t in books],
            "related_concepts": related_concepts,
            "counts": counts,
            "book_coverage": book_coverage,
            "distinctive_count": distinctive_count,
            "items": items,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # --- dossier.md : ce que lit le redacteur -------------------------------
    lines: list[str] = [
        f"# Dossier -- {args.expression}",
        "",
        f"- Sous-requetes interrogees : {len(queries)} -- facettes : "
        + (", ".join(sorted({f for f in query_facet.values() if f})) or "(aucune etiquetee)"),
        f"- Chunks uniques : {len(items)} -- texte={counts['texte']}, code={counts['code']}, "
        f"formule={counts['formule']}, image={counts['image']}",
        f"- Livres : {', '.join(t for _, t in books)}",
    ]
    if related_concepts:
        lines.append(f"- Concepts voisins (via le graphe, pistes de liens internes) : {', '.join(related_concepts)}")
    lines += ["", "Chaque bloc porte un identifiant [Sxxx] a citer dans l'article "
              "(`<!--src:Sxxx-->`). Le texte entre `[DESCRIPTION DU PIPELINE]` est une description "
              "en langage naturel generee par le pipeline : ce n'est pas du verbatim de livre, "
              "c'est une aide de lecture -- le bloc de code/formule/image qui la precede fait foi.",
              "Un chunk marque `⚠ DÉJÀ EXPLOITÉ par : <slug>` a deja servi a un contenu voisin : "
              "son TEXTE (phrases, arguments, exemples) ne se redit pas ici. Les images, formules et "
              "blocs de code restent librement reutilisables (ils peuvent servir un autre argument).", ""]

    current: tuple[str, str] | None = None
    for it in items:
        key = (it["book"], it["section"])
        if key != current:
            lines += ["", "=" * 90, f"### {it['book']} -- {it['section'] or '(sans titre)'}", "=" * 90]
            current = key
        score = "-" if it["score"] is None else f"{it['score']:.3f}"
        marker = " | ★ signal distinctif" if it["distinctive"] else ""
        if it["facets"]:
            marker += f" | facette : {', '.join(it['facets'])}"
        if it["consumed_by"]:
            marker += f" | ⚠ DÉJÀ EXPLOITÉ par : {', '.join(it['consumed_by'])}"
        lines += ["", f"--- [{it['id']}] {it['type']} | score {score} | {', '.join(it['found_by'])}{marker} ---", it["text"]]
        for img in it["images"]:
            if img["missing"]:
                lines.append(f"(IMAGE INTROUVABLE : {img['src']} -- ne pas l'utiliser)")
            else:
                lines.append(f"(image disponible pour l'article : {img['web_path']})")
    (out_dir / "dossier.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # --- compte rendu (a relayer tel quel a l'utilisateur) ------------------
    print(f"Dossier constitue : {out_dir}")
    print(f"  Requetes : {len(queries)}")
    for q, (n, fresh) in per_query_counts.items():
        suffix = f", dont {fresh} neuf(s) (non exploites par un voisin)" if neighbor_slugs else ""
        tag = f"[{query_facet[q]}] " if query_facet.get(q) else ""
        print(f"    - {tag}{q!r} -> {n} chunk(s){suffix}")
    facet_names = list(dict.fromkeys(f for f in query_facet.values() if f))
    if facet_names:
        print("  Couverture par facette (chunks distincts trouves / dont neufs vs voisins) :")
        for fn in facet_names:
            keys = {(it["document_id"], it["chunk_index"]) for it in items if fn in it["facets"]}
            fresh_keys = {k for k in keys if k not in consumed}
            flag = "  <-- AUCUN CHUNK : reformule la requete avec le vocabulaire de la facette" if not keys else ""
            print(f"    - {fn} : {len(keys)} / {len(fresh_keys)}{flag}")
    if untagged:
        print(f"  ATTENTION : {len(untagged)} sous-requete(s) sans facette ('<facette>::<requete>') : {untagged} -- "
              "une requete doit servir une facette du catalogue de l'intention (SKILL.md, etape 1).")
    print(f"  Chunks uniques : {len(items)}  (texte={counts['texte']}, code={counts['code']}, "
          f"formule={counts['formule']}, image={counts['image']})")
    print(f"  Livres : {len(books)}  Sections : {len(ordered_sections)}")
    print(f"  Concepts voisins : {len(related_concepts)}")

    # --- recouvrement avec les contenus voisins (information, jamais bloquant) --
    if neighbor_slugs:
        text_items = [it for it in items if it["type"] == "texte"]
        n_text = len(text_items) or 1
        print(f"  Voisins pris en compte : {', '.join(neighbor_slugs)}")
        for nb in neighbor_slugs:
            shared = sum(1 for it in text_items if nb in it["consumed_by"])
            print(f"    - {nb} : {shared}/{len(text_items)} chunk(s) de texte du dossier deja exploites ({shared / n_text:.0%})")
        fresh_text = sum(1 for it in text_items if not it["consumed_by"])
        print(f"  Chunks de texte neufs : {fresh_text}/{len(text_items)}")
        if len(text_items) and fresh_text / n_text < 0.4:
            print("  RECOUVREMENT ELEVE : la plupart des chunks de texte ont deja servi a un voisin -- "
                  "reprends tes sous-requetes sur des facettes que les voisins ne couvrent pas (voir cadrage), "
                  "ou accepte un article plus court qui renvoie vers eux. Information, pas un blocage.")
    else:
        print("  Aucun voisin : page blanche (rien a eviter), ce n'est pas bloquant.")

    # --- diagnostic diversite / matiere distinctive (voir Regles d'or) ------
    print(f"  Livres pertinents (trouves par recherche directe) : {book_coverage}/{len(books)}")
    if book_coverage <= 1:
        print("  RISQUE COMMODITY : un seul livre couvre vraiment ce sujet -- le contenu s'appuiera "
              "sur un savoir standard, difficile a differencier aux yeux de Google.")
    elif book_coverage >= 3:
        print(f"  SIGNAL POSITIF : {book_coverage} livres apportent chacun un angle propre -- "
              "bonne matiere a contenu non-commodity.")
    print(f"  Chunks distinctifs (chiffre precis / entite nommee, marques ★ dans dossier.md) : "
          f"{distinctive_count}/{len(items)}")
    if book_coverage >= 3 and len(items) > 80:
        themes = sorted({it["section"].split(">")[0].strip() for it in items if it["section"]})
        print(f"  SUGGESTION : dossier tres riche ({len(items)} chunks, {len(themes)} sous-theme(s) "
              "repere(s)) -- envisager un article principal + des articles satellites plutot qu'un "
              "seul article-fleuve.")
        print(f"    Sous-themes reperes : {', '.join(themes[:12])}" + (" ..." if len(themes) > 12 else ""))

    missing = [i["src"] for it in items for i in it["images"] if i["missing"]]
    if missing:
        print(f"  ATTENTION : {len(missing)} image(s) referencee(s) introuvable(s) : {missing}")
    if truncated_fences:
        print(f"  ATTENTION : bloc de code non ferme (chunk tronque) dans {truncated_fences} -- "
              "ne pas reproduire ce code sans l'avoir complete depuis un autre chunk.")
    for label, n in (("code", counts["code"]), ("formule", counts["formule"]), ("image", counts["image"])):
        if n == 0:
            print(f"  LACUNE : aucun chunk de type {label} -- l'article ne devra pas en inventer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
