"""Suggere des expressions candidates pour /create-content, hors pipeline,
LECTURE SEULE -- demarche INVERSE de gather.py (etape 1 de /create-content) :
celui-ci part d'une expression choisie a la main et mesure sa couverture
multi-livres et sa matiere distinctive ; ce script parcourt TOUT le graphe de
concepts et calcule les deux memes signaux pour CHAQUE concept, PUIS remonte
les intitules de section REELS du corpus qui portent ce concept, afin de
proposer des expressions complexes ("Pourquoi pas la regression lineaire ?",
"La regression lineaire par maximum de vraisemblance"...) et non de simples
noms de concepts deja connus -- sans jamais en INVENTER : chaque expression
proposee est un intitule ecrit par un auteur, relu tel quel dans `chunks.json`
(`heading_trail`), jamais genere ni reformule par ce script.

Les deux signaux de base sont EXACTEMENT ceux de gather.py (memes seuils,
memes regex, volontairement dupliques ici plutot qu'importes : importer
gather.py tirerait toute la chaine `retrieval` -> `vector_store` ->
chromadb/sentence-transformers, alors que ce script n'a besoin d'aucune
recherche vectorielle -- meme choix que `rag-integrity/scripts/report.py`
pour `entity_resolution.py`, voir son commentaire `_resolution_thresholds`) :

  - couverture multi-livres : nombre de documents distincts dont au moins un
    chunk mentionne le concept ;
  - chunks distinctifs : parmi les chunks qui mentionnent le concept, ceux
    portant un chiffre precis/pourcentage/annee ou une entite nommee
    (`has_number_signal` / `has_named_entity_signal`, voir gather.py).

Intitules complexes -- comment ils sont EXTRAITS (jamais inventes) :
  Pour chaque chunk qui mentionne un concept retenu, le dernier niveau de son
  `heading_trail` (le titre de section le plus specifique) est nettoye de sa
  numerotation ("8.9 Regression lineaire de bout en bout" -> "Regression
  lineaire de bout en bout") puis ecarte s'il est generique (Resume,
  Introduction, Exercices...). Chaque intitule retenu est marque :
    - question ou non (commence par Comment/Pourquoi/Quand/Qu'est-ce/Quelle...
      ou contient "?") -- ce sont les plus proches de "comment fonctionne X ?" ;
    - accompagne d'une formule et/ou de code (`has_formula`/`has_code` du
      chunk) -- un intitule qui explique ET prouve (formule/code) est un
      meilleur candidat qu'un intitule purement descriptif ;
    - nombre de chunks distinctifs qu'il porte.
  Les intitules d'un concept sont ensuite classes (question d'abord, puis
  formule/code, puis chunks distinctifs) : les premiers de la liste sont les
  candidats d'expression les plus riches deja ecrits par les auteurs.

Concepts fragmentes -- des concepts identiques (meme `canonical_form`, ex.
"regression lineaire" vu au moins 11 fois lors d'un run reel) peuvent exister
comme plusieurs noeuds distincts du graphe faute de fusion par /rag-graphe :
non corrige ici (ce script ne modifie jamais le graphe), mais FUSIONNE pour
l'affichage seulement (memes chunks, meme couverture agregee) -- sinon un
concept tres present serait sous-estime et invisible du rapport. Le nombre de
noeuds fusionnes est indique ; un nombre eleve est un signal a traiter avec
/rag-graphe (consolidation), independamment de ce rapport.

Le graphe ne sert que de GENERATEUR DE CANDIDATS (comme dans retrieval.py) :
"nombre de mentions", "couverture multi-livres" ou "intitule releve" ne sont
jamais un jugement de pertinence, seulement des indices que /create-content
devra confirmer par une vraie recherche vectorielle.

Un concept a couverture <2 livres est explicitement le cas "RISQUE COMMODITY"
que gather.py signale deja (book_coverage <= 1) : exclu par defaut
(--min-doc-coverage 1 pour le reafficher).

Sortie : rapport Markdown, par defaut sous
rag_data/audit/candidats_expressions_<AAAAMMJJ_HHMMSS>.md -- meme convention
de nommage que /rag-integrity (report.py).

Usage:
    python tools/suggest_expressions.py [--min-chunks 3] [--min-doc-coverage 2]
        [--top 60] [--max-headings 4] [--out ...] [--graph-db-path rag_data/db/graph]
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
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_RAG_LIB = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "_rag_lib"
sys.path.insert(0, str(_RAG_LIB))

import paths  # noqa: E402
from graph_store import load_aliases  # noqa: E402
from structural import is_structural_trail, normalize as normalize_heading  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# --- signaux de matiere distinctive -- copie exacte de gather.py (voir
# docstring du module pour pourquoi ce n'est pas un import) ---------------
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


def is_distinctive(text: str) -> bool:
    return has_number_signal(text) or has_named_entity_signal(text)


# --- intitules de section : nettoyage, filtrage du generique, detection de
# question -- REMONTES tels qu'ecrits, jamais reformules -------------------
_HEADING_NUMBERING_RE = re.compile(r"^\d+(?:\.\d+)*\.?\s+")
_GENERIC_HEADINGS = {
    "resume", "en resume", "pour resumer", "synthese", "points cles",
    "a retenir", "introduction", "conclusion", "objectifs", "objectif",
    "plan", "plan du chapitre", "prerequis", "prerequisites", "exercices",
    "exercice", "aller plus loin", "pour aller plus loin", "references",
    "notes", "avant-propos", "avertissement",
}
_QUESTION_STARTS = (
    "comment ", "pourquoi ", "quand ", "qu'est-ce", "qu est ce", "en quoi ",
    "a quoi sert", "quelle ", "quel ", "quels ", "quelles ", "est-ce ",
)


def clean_heading(raw: str) -> str:
    return _HEADING_NUMBERING_RE.sub("", raw or "").strip()


def is_generic_heading(cleaned: str) -> bool:
    return len(cleaned) < 4 or normalize_heading(cleaned) in _GENERIC_HEADINGS


def is_question_heading(cleaned: str) -> bool:
    if "?" in cleaned:
        return True
    norm = normalize_heading(cleaned)
    return norm.startswith(_QUESTION_STARTS)


# ---------------------------------------------------------------------------
# Chargement du graphe -- lecture seule, sans ConceptGraph (voir report.py :
# ConceptGraph._ensure_schema tente un CREATE TABLE au premier appel, qui
# echoue sur une base ouverte read_only pour toute autre raison qu'un "already
# exists" ; ouvrir Kuzu directement en read_only evite ce risque).
# ---------------------------------------------------------------------------

@dataclass
class HeadingCandidate:
    text: str
    document_id: str
    is_question: bool
    has_formula: bool = False
    has_code: bool = False
    distinctive_chunks: int = 0
    total_chunks: int = 0

    def sort_key(self):
        return (not self.is_question, not (self.has_formula or self.has_code),
                -self.distinctive_chunks, self.text.lower())


@dataclass
class ConceptCandidate:
    id: str
    canonical_form: str
    type: str
    sense: str
    aliases: list[str]
    documents: set[str] = field(default_factory=set)
    chunks: set[tuple[str, int]] = field(default_factory=set)
    distinctive_count: int = 0
    merged_from: int = 1
    headings: list[HeadingCandidate] = field(default_factory=list)

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)

    @property
    def doc_coverage(self) -> int:
        return len(self.documents)

    @property
    def distinctive_ratio(self) -> float:
        return self.distinctive_count / self.chunk_count if self.chunks else 0.0


def load_candidates(graph_db_path: Path) -> list[ConceptCandidate]:
    import kuzu

    db = kuzu.Database(str(graph_db_path), read_only=True)
    conn = kuzu.Connection(db)

    candidates: dict[str, ConceptCandidate] = {}
    result = conn.execute("MATCH (c:Concept) RETURN c.id, c.canonical_form, c.type, c.aliases, c.sense")
    while result.has_next():
        cid, canonical_form, type_, raw_aliases, sense = result.get_next()
        candidates[cid] = ConceptCandidate(
            id=cid, canonical_form=canonical_form, type=type_ or "",
            sense=(sense or "").strip(), aliases=load_aliases(raw_aliases),
        )

    result = conn.execute(
        "MATCH (ch:Chunk)-[:MENTIONS]->(c:Concept) RETURN c.id, ch.document_id, ch.chunk_index"
    )
    while result.has_next():
        cid, doc_id, chunk_idx = result.get_next()
        cand = candidates.get(cid)
        if cand is None:
            continue
        cand.documents.add(doc_id)
        cand.chunks.add((doc_id, chunk_idx))

    return list(candidates.values())


def merge_by_canonical_form(candidates: list[ConceptCandidate]) -> list[ConceptCandidate]:
    """Fusionne, pour l'AFFICHAGE seulement (le graphe n'est jamais ecrit),
    les noeuds distincts partageant exactement la meme `canonical_form` --
    voir docstring du module : la resolution d'entites peut laisser des
    doublons non fusionnes, qui sous-estimeraient sinon la couverture d'un
    concept tres present."""
    groups: dict[str, list[ConceptCandidate]] = {}
    for c in candidates:
        groups.setdefault(c.canonical_form, []).append(c)

    merged: list[ConceptCandidate] = []
    for canonical_form, group in groups.items():
        if len(group) == 1:
            merged.append(group[0])
            continue
        documents: set[str] = set()
        chunks: set[tuple[str, int]] = set()
        aliases: set[str] = set()
        types: Counter = Counter()
        sense = ""
        for g in group:
            documents |= g.documents
            chunks |= g.chunks
            aliases |= set(g.aliases)
            types[g.type] += 1
            if len(g.sense) > len(sense):
                sense = g.sense
        combined = ConceptCandidate(
            id=group[0].id, canonical_form=canonical_form,
            type=types.most_common(1)[0][0] if types else "",
            sense=sense, aliases=sorted(aliases),
            documents=documents, chunks=chunks, merged_from=len(group),
        )
        merged.append(combined)
    return merged


# --- chunks (texte + heading_trail), un chargement par livre, mis en cache --
# meme principe que retrieval._load_chunks ----------------------------------
_chunk_text_cache: dict[str, dict[int, dict]] = {}
_missing_chunks_json: set[str] = set()


def _load_chunk_texts(document_id: str) -> dict[int, dict]:
    if document_id in _chunk_text_cache:
        return _chunk_text_cache[document_id]
    chunks_path = paths.work_dir_for(document_id) / "chunks.json"
    if not chunks_path.exists():
        _missing_chunks_json.add(document_id)
        _chunk_text_cache[document_id] = {}
        return {}
    raw = json.loads(chunks_path.read_text(encoding="utf-8"))
    by_index = {c["index"]: c for c in raw}
    _chunk_text_cache[document_id] = by_index
    return by_index


_title_cache: dict[str, str] = {}


def document_title(document_id: str) -> str:
    """Titre lisible du livre -- meme logique que gather.py:document_title."""
    if document_id in _title_cache:
        return _title_cache[document_id]
    title = None
    try:
        meta = json.loads((paths.work_dir_for(document_id) / "meta.json").read_text(encoding="utf-8"))
        for key in ("title", "titre", "source_title"):
            if isinstance(meta.get(key), str) and meta[key].strip():
                title = meta[key].strip()
                break
    except Exception:  # noqa: BLE001 -- meta.json est facultatif ici
        pass
    if title is None:
        title = re.sub(r"__[0-9a-f]{8}$", "", document_id).replace("_", " ")
    _title_cache[document_id] = title
    return title


def analyze_chunks(candidates: list[ConceptCandidate]) -> None:
    """Remplit `distinctive_count` ET `headings` de chaque candidat en une
    seule passe sur ses chunks (mise en cache par livre). Les chunks a fil
    d'ariane structurel (table des matieres...) sont ignores partout dans le
    pipeline (voir structural.py) : ni comptes, ni juges distinctifs, ni
    source d'intitule."""
    for cand in candidates:
        kept: set[tuple[str, int]] = set()
        distinctive = 0
        headings: dict[tuple[str, str], HeadingCandidate] = {}
        for doc_id, chunk_idx in cand.chunks:
            chunk = _load_chunk_texts(doc_id).get(chunk_idx)
            if chunk is None or is_structural_trail(chunk.get("heading_trail", [])):
                continue
            kept.add((doc_id, chunk_idx))
            dist = is_distinctive(chunk["text"])
            if dist:
                distinctive += 1

            trail = chunk.get("heading_trail") or []
            if not trail:
                continue
            cleaned = clean_heading(trail[-1])
            if not cleaned or is_generic_heading(cleaned):
                continue
            key = (doc_id, cleaned)
            h = headings.get(key)
            if h is None:
                h = HeadingCandidate(text=cleaned, document_id=doc_id, is_question=is_question_heading(cleaned))
                headings[key] = h
            h.has_formula = h.has_formula or bool(chunk.get("has_formula"))
            h.has_code = h.has_code or bool(chunk.get("has_code"))
            h.total_chunks += 1
            if dist:
                h.distinctive_chunks += 1

        cand.chunks = kept
        cand.distinctive_count = distinctive
        cand.headings = sorted(headings.values(), key=HeadingCandidate.sort_key)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-chunks", type=int, default=3,
                     help="ecarte les concepts a trop peu de chunks pour porter un article (defaut 3)")
    ap.add_argument("--min-doc-coverage", type=int, default=2,
                     help="ecarte les concepts vus dans moins de N livres -- 1 est le cas "
                          "RISQUE COMMODITY deja signale par gather.py (defaut 2)")
    ap.add_argument("--top", type=int, default=60, help="nombre de candidats affiches/ecrits (defaut 60)")
    ap.add_argument("--max-headings", type=int, default=4,
                     help="nombre d'intitules reels affiches par concept (defaut 4)")
    ap.add_argument("--graph-db-path", type=str, default=str(paths.GRAPH_DB_PATH))
    ap.add_argument("--out", type=str, default=None,
                     help="defaut : rag_data/audit/candidats_expressions_<AAAAMMJJ_HHMMSS>.md")
    args = ap.parse_args()

    graph_db_path = Path(args.graph_db_path)
    if not graph_db_path.exists():
        print(f"ERREUR : graphe introuvable ({graph_db_path}) -- lancer /rag-graphe d'abord.", file=sys.stderr)
        return 1

    raw_candidates = load_candidates(graph_db_path)
    all_candidates = merge_by_canonical_form(raw_candidates)
    analyze_chunks(all_candidates)

    fragmented = sorted((c for c in all_candidates if c.merged_from > 1),
                         key=lambda c: -c.merged_from)

    scanned = len(raw_candidates)
    retained = [
        c for c in all_candidates
        if c.chunk_count >= args.min_chunks and c.doc_coverage >= args.min_doc_coverage
    ]
    retained.sort(key=lambda c: (-c.doc_coverage, -c.distinctive_ratio, -c.chunk_count, c.canonical_form.lower()))
    shown = retained[: args.top]

    strong = [c for c in shown if c.doc_coverage >= 3]
    borderline = [c for c in shown if c.doc_coverage == 2]

    if args.out:
        out_path = Path(args.out)
    else:
        timestamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
        out_path = paths.AUDIT_DIR / f"candidats_expressions_{timestamp}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def books_label(cand: ConceptCandidate, limit: int = 4) -> str:
        titles = sorted({document_title(d) for d in cand.documents})
        label = ", ".join(titles[:limit])
        if len(titles) > limit:
            label += f", +{len(titles) - limit}"
        return label

    def row(cand: ConceptCandidate) -> str:
        ratio = f"{cand.distinctive_ratio:.0%}"
        sense = cand.sense.replace("|", "/") or "-"
        frag = f" ({cand.merged_from} noeuds fusionnes)" if cand.merged_from > 1 else ""
        return (f"| {cand.canonical_form}{frag} | {cand.type or '-'} | {cand.doc_coverage} | {cand.chunk_count} | "
                f"{cand.distinctive_count} | {ratio} | {sense} | {books_label(cand)} |")

    def heading_line(h: HeadingCandidate) -> str:
        tags = []
        if h.is_question:
            tags.append("question")
        if h.has_formula:
            tags.append("formule")
        if h.has_code:
            tags.append("code")
        tag_str = f" [{', '.join(tags)}]" if tags else ""
        return (f"  - « {h.text} »{tag_str} -- {document_title(h.document_id)} "
                f"({h.distinctive_chunks}/{h.total_chunks} chunk(s) distinctif(s))")

    def heading_card(cand: ConceptCandidate) -> list[str]:
        card = [f"### {cand.canonical_form} [{cand.type or '-'}] -- {cand.doc_coverage} livres, "
                f"{cand.chunk_count} chunks, {cand.distinctive_count} distinctifs ({cand.distinctive_ratio:.0%})"]
        if cand.sense:
            card.append(f"Sens : {cand.sense}")
        card.append(f"Livres : {books_label(cand, limit=8)}")
        card.append("")
        top_headings = cand.headings[: args.max_headings]
        if top_headings:
            card.append("Intitules reels releves dans le corpus (question/formule/code en priorite) :")
            card += [heading_line(h) for h in top_headings]
        else:
            card.append("(aucun intitule de section exploitable -- concept mentionne hors de titres specifiques)")
        card.append("")
        return card

    lines: list[str] = [
        "# Candidats d'expressions pour /create-content",
        "",
        f"- Concepts scannes dans le graphe : {scanned} (regroupes en {len(all_candidates)} apres fusion "
        "par forme canonique -- voir 'Concepts fragmentes' ci-dessous)",
        f"- Retenus (>= {args.min_chunks} chunks, >= {args.min_doc_coverage} livre(s)) : {len(retained)}",
        f"- Affiches ci-dessous : {len(shown)}",
        "",
        "Ces signaux sont EXACTEMENT ceux que `gather.py` (etape 1 de /create-content) calcule pour une "
        "expression donnee -- ce rapport les precalcule pour tout le graphe afin d'aider a CHOISIR quelle "
        "expression tester. Les intitules listes plus bas sont extraits tels quels de `heading_trail` "
        "(jamais generes) : ce sont des candidats d'expression complexe deja ecrits par les auteurs. Le "
        "graphe reste un generateur de candidats : seule une vraie execution de /create-content (recherche "
        "vectorielle + expansion) confirme la couverture et la matiere reelles.",
        "",
    ]

    if fragmented:
        lines.append(f"## Concepts fragmentes ({len(fragmented)})")
        lines.append("")
        lines.append("Meme `canonical_form` portee par plusieurs noeuds du graphe non fusionnes -- fusionnes "
                      "ici pour l'affichage seulement (le graphe lui-meme n'est pas modifie). Un nombre eleve "
                      "de noeuds indique une resolution d'entites a revoir (/rag-graphe, consolidation).")
        lines.append("")
        for c in fragmented[:20]:
            lines.append(f"  - « {c.canonical_form} » : {c.merged_from} noeuds fusionnes")
        if len(fragmented) > 20:
            lines.append(f"  - ... et {len(fragmented) - 20} autre(s)")
        lines.append("")

    if _missing_chunks_json:
        lines.append(f"- ATTENTION : chunks.json introuvable pour {len(_missing_chunks_json)} document(s) "
                      f"(ignores dans le comptage) : {', '.join(sorted(_missing_chunks_json))}")
        lines.append("")

    header = "| Expression | Type | Livres | Chunks | Distinctifs | Ratio | Sens | Livres (titres) |"
    sep = "|---|---|---|---|---|---|---|---|"

    lines.append("## Vue d'ensemble -- fort potentiel (>= 3 livres)")
    lines.append("")
    if strong:
        lines += [header, sep] + [row(c) for c in strong]
    else:
        lines.append("(aucun)")
    lines.append("")

    lines.append("## Vue d'ensemble -- a verifier (2 livres)")
    lines.append("")
    if borderline:
        lines += [header, sep] + [row(c) for c in borderline]
    else:
        lines.append("(aucun)")
    lines.append("")

    lines.append("## Expressions complexes candidates (intitules reels du corpus)")
    lines.append("")
    lines.append("Un intitule marque `[question]` se lit deja comme une expression a tester telle quelle "
                  "(ex. « Pourquoi pas la regression lineaire ? »). Sans marque `[question]`, l'intitule "
                  "reste le sujet le plus specifique ecrit par l'auteur pour ce concept -- une bonne base "
                  "pour formuler la sous-requete a passer a /create-content (ex. --query \"<intitule>\").")
    lines.append("")
    for c in strong + borderline:
        lines += heading_card(c)

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # --- compte rendu (a relayer tel quel) ----------------------------------
    print(f"Rapport ecrit : {out_path}")
    print(f"Concepts scannes : {scanned}  (regroupes en {len(all_candidates)} apres fusion par forme canonique)")
    print(f"Retenus : {len(retained)}  Affiches : {len(shown)}")
    if fragmented:
        print(f"Concepts fragmentes (noeuds dupliques dans le graphe) : {len(fragmented)}")
        for c in fragmented[:10]:
            print(f"  - {c.canonical_form!r} : {c.merged_from} noeuds")
    if _missing_chunks_json:
        print(f"ATTENTION : chunks.json introuvable pour {len(_missing_chunks_json)} document(s) "
              f"(ignores) : {sorted(_missing_chunks_json)}")

    print(f"\nFort potentiel (>= 3 livres) : {len(strong)}")
    for c in strong:
        print(f"  - {c.canonical_form!r} [{c.type}] -- {c.doc_coverage} livres, "
              f"{c.chunk_count} chunks, {c.distinctive_count} distinctifs ({c.distinctive_ratio:.0%}) "
              f"-- {books_label(c)}")
        for h in c.headings[: args.max_headings]:
            print("   " + heading_line(h))

    print(f"\nA verifier (2 livres) : {len(borderline)}")
    for c in borderline:
        print(f"  - {c.canonical_form!r} [{c.type}] -- {c.chunk_count} chunks, "
              f"{c.distinctive_count} distinctifs ({c.distinctive_ratio:.0%}) -- {books_label(c)}")
        for h in c.headings[: args.max_headings]:
            print("   " + heading_line(h))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
