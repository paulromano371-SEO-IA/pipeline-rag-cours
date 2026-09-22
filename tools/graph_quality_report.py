"""Rapport de qualite du graphe de concepts, hors pipeline, LECTURE SEULE.

Le graphe Kuzu (`rag_data/db/graph/`) est ouvert en mode `read_only=True` :
aucune ecriture possible, ni sur le graphe, ni sur `rag_data/work/`. Si un
autre processus (ex. /rag-graphe en cours) tient la base, l'ouverture echoue
proprement — relancer une fois celui-ci termine.

Il n'existe pas de verite de reference pour un graphe extrait automatiquement :
ce rapport ne rend donc PAS de note. Il produit (1) des metriques structurelles
qui reperent des anomalies, et (2) des echantillons a auditer a la main (ou par
Claude, avec les chunks comme preuve) pour estimer la qualite de la resolution
d'entites — le point qui decide de la valeur inter-livres du graphe.

Partie 1 — Metriques structurelles
  1.1 Volumes par livre (chunks, liens, concepts, chunks sans concept)
  1.2 Concepts : types, longue traine (mentions uniques), partage entre livres,
      alias, coherence des dimensions d'embedding, concepts orphelins
  1.3 Concepts "hubs" (mentionnes par une grande part des chunks d'un livre)

Partie 2 — Audit de la resolution d'entites
  2.1 Concepts partages entre livres : tous listes (jusqu'a --max-shared) avec
      leurs alias et un extrait de chunk par livre. Risque : FUSION A TORT
      (deux notions differentes fusionnees), la pire erreur car elle cree de
      faux liens entre livres.
  2.2 Fusions internes suspectes : concepts a plusieurs alias, tries par
      dissemblance lexicale alias/forme canonique (les plus suspects d'abord)
      + un tirage aleatoire (--sample) pour estimer la precision sans biais.
  2.3 Fusions MANQUEES entre livres : paires de concepts de livres differents,
      jamais fusionnees, dont l'embedding est proche (>= --near-threshold).
  2.4 Doublons probables au sein d'un meme livre (>= --intra-threshold).
  2.5 Alias portes par plusieurs concepts distincts (resolution incoherente).

Chaque tableau d'audit porte une colonne "Verdict" vide a remplir. Precision
estimee = (lignes correctes) / (lignes auditees) sur l'echantillon : c'est une
estimation, pas une mesure exacte.

Usage:
    python tools/graph_quality_report.py [--out rapport.md] [--sample 25] [--seed 0]
        [--max-shared 60] [--max-pairs 40] [--near-threshold 0.80]
        [--intra-threshold 0.88] [--hub-share 0.10]
"""
from __future__ import annotations

import argparse
import difflib
import json
import random
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

_LIB = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "_rag_lib"
sys.path.insert(0, str(_LIB))

import numpy as np

import paths

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


# ---------------------------------------------------------------------------
# Chargement (lecture seule)
# ---------------------------------------------------------------------------

def _resolution_thresholds() -> tuple[float, float]:
    """Seuils de `entity_resolution.py` lus dans son source (sans l'importer :
    il tire sentence-transformers/torch) pour rester synchronises avec lui."""
    entity_resolution_path = (
        Path(__file__).resolve().parents[1]
        / ".claude" / "skills" / "rag-graphe" / "scripts" / "entity_resolution.py"
    )
    text = entity_resolution_path.read_text(encoding="utf-8")
    merge = re.search(r"^MERGE_THRESHOLD\s*=\s*([0-9.]+)", text, re.MULTILINE)
    ambiguous = re.search(r"^AMBIGUOUS_THRESHOLD\s*=\s*([0-9.]+)", text, re.MULTILINE)
    return (float(merge.group(1)) if merge else 0.92, float(ambiguous.group(1)) if ambiguous else 0.80)


def _rows(conn, query: str):
    result = conn.execute(query)
    while result.has_next():
        yield result.get_next()


def load_graph(db_path: Path) -> dict:
    import kuzu

    db = kuzu.Database(str(db_path), read_only=True)
    conn = kuzu.Connection(db)
    concepts = {
        r[0]: {"id": r[0], "canonical": r[1], "type": r[2],
               # colonne `aliases` : chaine JSON (nouveau schema) ou liste (ancienne base)
               "aliases": json.loads(r[3]) if isinstance(r[3], str) and r[3] else list(r[3] or []),
               "embedding": r[4]}
        for r in _rows(conn, "MATCH (c:Concept) RETURN c.id, c.canonical_form, c.type, c.aliases, c.embedding")
    }
    chunks = {r[0]: {"id": r[0], "doc": r[1], "index": r[2]} for r in _rows(conn, "MATCH (ch:Chunk) RETURN ch.id, ch.document_id, ch.chunk_index")}
    mentions = [(r[0], r[1]) for r in _rows(conn, "MATCH (ch:Chunk)-[:MENTIONS]->(c:Concept) RETURN ch.id, c.id")]
    return {"concepts": concepts, "chunks": chunks, "mentions": mentions}


_chunk_text_cache: dict[str, dict[int, str]] = {}


def _chunk_text(doc: str, index: int) -> str:
    if doc not in _chunk_text_cache:
        path = paths.WORK_DIR / doc / "chunks.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            _chunk_text_cache[doc] = {c["index"]: c["text"] for c in data}
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            _chunk_text_cache[doc] = {}
    text = _chunk_text_cache[doc].get(index, "")
    return re.sub(r"\s+", " ", _HTML_COMMENT_RE.sub(" ", text)).strip()


def _term_match(text: str, terms: list[str]):
    """Premiere occurrence (mot entier, insensible a la casse) d'un des termes."""
    for term in sorted({t for t in terms if len(t) >= 2}, key=len, reverse=True):
        match = re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.IGNORECASE)
        if match:
            return match
    return None


def chunk_excerpt(doc: str, index: int, *, terms: list[str] | None = None, width: int = 170) -> str:
    """Extrait d'un chunk (chunks.json du dossier de travail), sur une seule
    ligne, commentaires HTML du pipeline retires. Centre sur la premiere
    occurrence d'un des `terms` s'il y en a une (c'est ce qui prouve qu'un
    concept est bien mentionne la), sinon debut du chunk. Vide si indisponible."""
    text = _chunk_text(doc, index)
    match = _term_match(text, terms or [])
    if match:
        start = max(0, match.start() - width // 2)
        end = min(len(text), start + width)
        return ("..." if start else "") + text[start:end] + ("..." if end < len(text) else "")
    return (text[:width] + "...") if len(text) > width else text


def has_term(doc: str, index: int, terms: list[str], *, skip_toc: bool = False) -> bool:
    text = _chunk_text(doc, index)
    if skip_toc and text.count(". . .") >= 3:
        return False  # table des matieres : cite le terme sans en prouver le sens
    return _term_match(text, terms) is not None


# ---------------------------------------------------------------------------
# Mise en forme Markdown
# ---------------------------------------------------------------------------

def _cell(value) -> str:
    return str(value).replace("|", "/").replace("\n", " ")


def table(headers: list[str], rows: list[list]) -> list[str]:
    if not rows:
        return ["_(aucune ligne)_"]
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return out


def short_doc(doc: str) -> str:
    return re.sub(r"__[0-9a-f]{8}$", "", doc)[:32]


def pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f} %" if whole else "n/a"


# ---------------------------------------------------------------------------
# Partie 1 : metriques structurelles
# ---------------------------------------------------------------------------

def part1(graph: dict, hub_share: float) -> tuple[list[str], list[str]]:
    concepts, chunks, mentions = graph["concepts"], graph["chunks"], graph["mentions"]
    docs = sorted({c["doc"] for c in chunks.values()})
    chunks_of_concept: dict[str, set[str]] = defaultdict(set)
    concepts_of_chunk: dict[str, set[str]] = defaultdict(set)
    for chunk_id, concept_id in mentions:
        chunks_of_concept[concept_id].add(chunk_id)
        concepts_of_chunk[chunk_id].add(concept_id)
    docs_of_concept = {cid: {chunks[ch]["doc"] for ch in chs if ch in chunks} for cid, chs in chunks_of_concept.items()}

    signals: list[str] = []
    out: list[str] = ["## Partie 1 — Metriques structurelles", "", "### 1.1 Volumes par livre", ""]

    rows = []
    for doc in docs:
        doc_chunks = [c for c in chunks.values() if c["doc"] == doc]
        no_concept = [c for c in doc_chunks if not concepts_of_chunk.get(c["id"])]
        links = sum(len(concepts_of_chunk.get(c["id"], ())) for c in doc_chunks)
        doc_concepts = {cid for cid, ds in docs_of_concept.items() if doc in ds}
        per_chunk = [len(concepts_of_chunk.get(c["id"], ())) for c in doc_chunks]
        rows.append([
            short_doc(doc), len(doc_chunks), links, len(doc_concepts),
            f"{len(no_concept)} ({pct(len(no_concept), len(doc_chunks))})",
            f"{statistics.mean(per_chunk):.1f} / {statistics.median(per_chunk):.0f} / {max(per_chunk)}" if per_chunk else "n/a",
        ])
        if doc_chunks and len(no_concept) / len(doc_chunks) > 0.05:
            signals.append(f"{short_doc(doc)} : {pct(len(no_concept), len(doc_chunks))} des chunks du graphe n'ont aucun concept (> 5 %)")
    out += table(["Livre", "Chunks", "Liens", "Concepts distincts", "Chunks sans concept", "Concepts/chunk moy / med / max"], rows)

    out += ["", "### 1.2 Concepts", ""]
    n = len(concepts)
    single = sum(1 for cid in concepts if len(chunks_of_concept.get(cid, ())) == 1)
    orphans = [cid for cid in concepts if not chunks_of_concept.get(cid)]
    n_docs = Counter(len(docs_of_concept.get(cid, ())) for cid in concepts)
    alias_counts = [len({a.lower() for a in c["aliases"]} | {c["canonical"].lower()}) for c in concepts.values()]
    dims = Counter(len(c["embedding"] or []) for c in concepts.values())

    out += table(["Indicateur", "Valeur"], [
        ["Concepts au total", n],
        ["Mentionnes par un seul chunk (longue traine)", f"{single} ({pct(single, n)})"],
        ["Presents dans 1 seul livre", f"{n_docs.get(1, 0)} ({pct(n_docs.get(1, 0), n)})"],
        ["Partages entre >= 2 livres", f"{sum(v for k, v in n_docs.items() if k >= 2)} ({pct(sum(v for k, v in n_docs.items() if k >= 2), n)})"],
        ["Concepts orphelins (aucun lien)", len(orphans)],
        ["Formes distinctes par concept (moy / max)", f"{statistics.mean(alias_counts):.2f} / {max(alias_counts)}" if alias_counts else "n/a"],
        ["Dimensions d'embedding", ", ".join(f"{d} ({c})" for d, c in sorted(dims.items())) or "n/a"],
    ])
    if n and single / n > 0.75:
        signals.append(f"{pct(single, n)} des concepts ne sont mentionnes que par un chunk : formes canoniques peut-etre trop fragmentees")
    if orphans:
        signals.append(f"{len(orphans)} concept(s) orphelin(s) : incoherence du graphe (nettoyage de remove_document ?)")
    if len(dims) > 1:
        signals.append(f"embeddings de dimensions differentes ({dict(dims)}) : plusieurs modeles melanges, similarites non comparables")

    types = Counter(c["type"] for c in concepts.values())
    out += ["", "Repartition par type :", ""]
    out += table(["Type", "Concepts", "Part"], [[t, c, pct(c, n)] for t, c in types.most_common()])

    out += ["", f"### 1.3 Concepts \"hubs\" (mentionnes par >= {hub_share:.0%} des chunks d'un livre)", ""]
    chunks_per_doc = Counter(c["doc"] for c in chunks.values())
    hubs = []
    for cid, chs in chunks_of_concept.items():
        per_doc = Counter(chunks[ch]["doc"] for ch in chs if ch in chunks)
        share = max((cnt / chunks_per_doc[d] for d, cnt in per_doc.items()), default=0.0)
        if share >= hub_share:
            c = concepts[cid]
            hubs.append([c["canonical"], c["type"], len(chs), len(per_doc), f"{share:.0%}",
                         ", ".join(f"{short_doc(d)}:{k}" for d, k in per_doc.most_common())])
    hubs.sort(key=lambda r: -r[2])
    out += table(["Concept", "Type", "Chunks", "Livres", "Part max", "Detail"], hubs[:15])
    if hubs:
        out += ["", "Un hub tres generique (ex. \"modele\", \"donnees\") relie tout a tout : bruit pour l'expansion par concept."]
        signals.append(f"{len(hubs)} concept(s) hub(s) >= {hub_share:.0%} : verifier qu'ils ne sont pas trop generiques (1.3)")
    return out, signals


# ---------------------------------------------------------------------------
# Partie 2 : audit de la resolution d'entites
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def alias_dissimilarity(concept: dict) -> tuple[float, str]:
    """(plus faible ressemblance lexicale alias/forme canonique, alias en cause).
    Bas = alias tres different de la forme canonique = fusion suspecte."""
    canonical = _norm(concept["canonical"])
    worst, worst_alias = 1.0, ""
    for alias in concept["aliases"]:
        a = _norm(alias)
        if a == canonical:
            continue
        ratio = difflib.SequenceMatcher(None, a, canonical).ratio()
        if ratio < worst:
            worst, worst_alias = ratio, alias
    return worst, worst_alias


def near_pairs(graph: dict, docs_of_concept: dict, thresholds: tuple[float, float]) -> tuple[list, list]:
    """Paires de concepts distincts proches par embedding, jamais fusionnees.
    (paires inter-livres, paires intra-livre), triees par similarite decroissante."""
    near, intra = thresholds
    concepts = list(graph["concepts"].values())
    if len(concepts) < 2 or len({len(c["embedding"] or []) for c in concepts}) != 1:
        return [], []
    E = np.asarray([c["embedding"] for c in concepts], dtype=np.float32)
    norms = np.linalg.norm(E, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    E /= norms
    low = min(near, intra)
    cross, same = [], []
    block = 512
    for i0 in range(0, len(concepts), block):
        S = E[i0:i0 + block] @ E.T
        for r, c in np.argwhere(S >= low):
            i, j = i0 + int(r), int(c)
            if j <= i:
                continue
            sim = float(S[r, c])
            di, dj = docs_of_concept.get(concepts[i]["id"], set()), docs_of_concept.get(concepts[j]["id"], set())
            if di and dj and not (di & dj):
                if sim >= near:
                    cross.append((sim, concepts[i], concepts[j]))
            elif sim >= intra:
                same.append((sim, concepts[i], concepts[j]))
    cross.sort(key=lambda t: -t[0])
    same.sort(key=lambda t: -t[0])
    return cross, same


def part2(graph: dict, args, merge_threshold: float, ambiguous_threshold: float) -> tuple[list[str], list[str]]:
    concepts, chunks, mentions = graph["concepts"], graph["chunks"], graph["mentions"]
    rng = random.Random(args.seed)
    chunks_of_concept: dict[str, list[str]] = defaultdict(list)
    for chunk_id, concept_id in mentions:
        chunks_of_concept[concept_id].append(chunk_id)
    docs_of_concept = {cid: {chunks[ch]["doc"] for ch in chs if ch in chunks} for cid, chs in chunks_of_concept.items()}

    def excerpt_lines(concept_id: str) -> str:
        """Un extrait par livre qui mentionne ce concept : le premier chunk qui
        contient effectivement l'un de ses noms (extrait centre dessus), a
        defaut le premier chunk lie."""
        concept = concepts[concept_id]
        terms = [concept["canonical"], *concept["aliases"]]
        by_doc: dict[str, list[dict]] = defaultdict(list)
        for ch in sorted(chunks_of_concept.get(concept_id, ()), key=lambda c: chunks[c]["index"] if c in chunks else 0):
            if ch in chunks:
                by_doc[chunks[ch]["doc"]].append(chunks[ch])
        parts = []
        for doc, infos in by_doc.items():
            best = next(
                (i for i in infos if has_term(doc, i["index"], terms, skip_toc=True)),
                next((i for i in infos if has_term(doc, i["index"], terms)), infos[0]),
            )
            parts.append(f"[{short_doc(doc)} #{best['index']}] {chunk_excerpt(doc, best['index'], terms=terms)}")
        return " // ".join(parts)

    signals: list[str] = []
    out = [
        "## Partie 2 — Audit de la resolution d'entites", "",
        f"Seuils du pipeline : fusion automatique >= {merge_threshold}, arbitrage LLM entre {ambiguous_threshold} et {merge_threshold}, "
        f"nouveau concept en dessous. Colonne \"Verdict\" a remplir : `ok` / `fusion a tort` / `doublon`.", "",
        "### 2.1 Concepts partages entre livres (risque : fusion a tort)", "",
    ]
    shared = [c for cid, c in concepts.items() if len(docs_of_concept.get(cid, ())) >= 2]
    shared.sort(key=lambda c: -len(chunks_of_concept[c["id"]]))
    out.append(f"{len(shared)} concept(s) partage(s) ; {min(len(shared), args.max_shared)} liste(s) (les plus mentionnes d'abord).")
    out.append("")
    out += table(
        ["Concept", "Type", "Alias", "Chunks", "Extraits (un par livre)", "Verdict"],
        [[c["canonical"], c["type"], ", ".join(sorted({a for a in c["aliases"] if _norm(a) != _norm(c["canonical"])})[:6]) or "-",
          len(chunks_of_concept[c["id"]]), excerpt_lines(c["id"]), ""] for c in shared[:args.max_shared]],
    )
    if not shared:
        signals.append("aucun concept partage entre livres : soit les livres ne se recoupent pas, soit des fusions sont manquees (voir 2.3)")

    out += ["", "### 2.2 Fusions internes suspectes", ""]
    merged = [c for c in concepts.values() if len({_norm(a) for a in c["aliases"]} - {_norm(c["canonical"])}) >= 1]
    scored = sorted(((alias_dissimilarity(c), c) for c in merged), key=lambda t: t[0][0])
    out.append(
        f"{len(merged)} concept(s) portent au moins un alias different de leur forme canonique. "
        f"Ressemblance lexicale alias/forme canonique : faible = fusion a verifier (ex. deux notions voisines)."
    )
    out += ["", f"**Les {min(args.sample, len(scored))} plus dissemblables :**", ""]
    out += table(["Concept", "Type", "Alias le plus eloigne", "Ressemblance", "Tous les alias", "Verdict"],
                 [[c["canonical"], c["type"], worst_alias, f"{ratio:.2f}", ", ".join(sorted({a for a in c["aliases"]})[:8]), ""]
                  for (ratio, worst_alias), c in scored[:args.sample]])
    short_alias = sorted(
        {f"{c['canonical']} <- {a}" for c in concepts.values() for a in c["aliases"] if _norm(a) != _norm(c["canonical"]) and len(_norm(a)) <= 3}
    )
    if short_alias:
        out += ["", f"**Alias de 3 caracteres ou moins ({len(short_alias)}) — l'embedding d'une chaine aussi courte est peu fiable, "
                    f"risque de fusion a tort :** " + ", ".join(f"`{s}`" for s in short_alias[:20])]
        signals.append(f"{len(short_alias)} alias de <= 3 caracteres (ex. {short_alias[0]}) : fusions a tort probables, a auditer en 2.2")
    rest = [c for _, c in scored[args.sample:]]
    draw = rng.sample(rest, min(args.sample, len(rest)))
    out += ["", f"**Tirage aleatoire de {len(draw)} (graine {args.seed}) — sert a estimer la precision sans biais :**", ""]
    out += table(["Concept", "Type", "Tous les alias", "Livres", "Verdict"],
                 [[c["canonical"], c["type"], ", ".join(sorted({a for a in c["aliases"]})[:8]),
                   ", ".join(sorted(short_doc(d) for d in docs_of_concept.get(c["id"], ()))), ""] for c in draw])

    cross, same = near_pairs(graph, docs_of_concept, (args.near_threshold, args.intra_threshold))
    out += ["", f"### 2.3 Fusions MANQUEES entre livres (similarite >= {args.near_threshold}, livres disjoints)", ""]
    out.append(f"{len(cross)} paire(s) ; {min(len(cross), args.max_pairs)} listee(s), les plus proches d'abord. "
               f"Au-dessus de {merge_threshold}, une fusion automatique aurait du avoir lieu.")
    out.append("")
    out += table(["Sim.", "Concept A", "Livre A", "Concept B", "Livre B", "Verdict"],
                 [[f"{sim:.3f}" + (" (>= fusion auto)" if sim >= merge_threshold else ""), a["canonical"],
                   ", ".join(sorted(short_doc(d) for d in docs_of_concept.get(a["id"], ()))), b["canonical"],
                   ", ".join(sorted(short_doc(d) for d in docs_of_concept.get(b["id"], ()))), ""] for sim, a, b in cross[:args.max_pairs]])
    above = sum(1 for sim, _, _ in cross if sim >= merge_threshold)
    if above:
        signals.append(f"{above} paire(s) inter-livres >= {merge_threshold} jamais fusionnee(s) : verifier l'ordre de traitement/arbitrage (2.3)")

    out += ["", f"### 2.4 Doublons probables au sein d'un meme livre (similarite >= {args.intra_threshold})", ""]
    out.append(f"{len(same)} paire(s) ; {min(len(same), args.max_pairs)} listee(s).")
    out.append("")
    out += table(["Sim.", "Concept A", "Concept B", "Livres communs", "Verdict"],
                 [[f"{sim:.3f}", a["canonical"], b["canonical"],
                   ", ".join(sorted(short_doc(d) for d in docs_of_concept.get(a["id"], set()) & docs_of_concept.get(b["id"], set()))), ""]
                  for sim, a, b in same[:args.max_pairs]])

    out += ["", "### 2.5 Alias portes par plusieurs concepts distincts", ""]
    owners: dict[str, list[dict]] = defaultdict(list)
    for c in concepts.values():
        for form in {_norm(a) for a in c["aliases"]} | {_norm(c["canonical"])}:
            owners[form].append(c)
    collisions = sorted(((form, cs) for form, cs in owners.items() if len(cs) >= 2), key=lambda t: t[0])
    out.append(f"{len(collisions)} forme(s) de surface apparaissent dans plusieurs concepts : un meme mot resolu vers deux entites "
               f"signale une resolution incoherente (l'un des deux est probablement faux).")
    out.append("")
    out += table(["Forme", "Concepts qui la portent", "Verdict"],
                 [[form, " / ".join(f"{c['canonical']} ({c['type']})" for c in cs), ""] for form, cs in collisions[:args.max_pairs]])
    if collisions:
        signals.append(f"{len(collisions)} forme(s) de surface portee(s) par plusieurs concepts (2.5)")
    return out, signals


# ---------------------------------------------------------------------------

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # console Windows cp1252
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-path", type=Path, default=paths.GRAPH_DB_PATH)
    parser.add_argument("--out", type=Path, default=Path("rapport_graphe.md"), help="ecrit aussi le rapport (Markdown, UTF-8) dans ce fichier (defaut rapport_graphe.md)")
    parser.add_argument("--sample", type=int, default=25, help="taille des echantillons d'audit de 2.2 (defaut 25)")
    parser.add_argument("--seed", type=int, default=0, help="graine du tirage aleatoire (defaut 0, reproductible)")
    parser.add_argument("--max-shared", type=int, default=60, help="concepts partages listes en 2.1 (defaut 60)")
    parser.add_argument("--max-pairs", type=int, default=40, help="paires listees en 2.3 et 2.4 (defaut 40)")
    parser.add_argument("--near-threshold", type=float, default=None, help="seuil de 2.3 (defaut : seuil d'arbitrage du pipeline)")
    parser.add_argument("--intra-threshold", type=float, default=0.88, help="seuil de 2.4 (defaut 0.88)")
    parser.add_argument("--hub-share", type=float, default=0.10, help="part de chunks d'un livre a partir de laquelle un concept est un hub (defaut 0.10)")
    args = parser.parse_args()

    merge_threshold, ambiguous_threshold = _resolution_thresholds()
    if args.near_threshold is None:
        args.near_threshold = ambiguous_threshold

    if not args.db_path.exists():
        print(f"ERREUR: graphe introuvable : {args.db_path}", file=sys.stderr)
        return 1
    try:
        graph = load_graph(args.db_path)
    except Exception as exc:  # base verrouillee par un autre processus, format inattendu...
        print(f"ERREUR: ouverture en lecture seule impossible ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 1
    if not graph["concepts"]:
        print("Le graphe est vide : rien a evaluer.")
        return 0

    docs = sorted({c["doc"] for c in graph["chunks"].values()})
    head = [
        "# Rapport de qualite du graphe de concepts", "",
        f"Graphe : `{args.db_path}` (lecture seule). {len(docs)} livre(s) : " + ", ".join(f"`{d}`" for d in docs) + ".",
        f"{len(graph['concepts'])} concepts, {len(graph['chunks'])} chunks, {len(graph['mentions'])} liens.", "",
    ]
    if len(docs) < 2:
        head += ["> Un seul livre dans le graphe : les sections inter-livres (2.1, 2.3) seront vides.", ""]

    p1, s1 = part1(graph, args.hub_share)
    p2, s2 = part2(graph, args, merge_threshold, ambiguous_threshold)
    signals = s1 + s2
    summary = ["## Signaux a examiner", ""] + ([f"- {s}" for s in signals] if signals else ["- Aucun signal automatique. Les audits de la partie 2 restent a faire."])
    report = "\n".join(head + summary + [""] + p1 + [""] + p2) + "\n"

    print(report)
    if args.out:
        args.out.write_text(report, encoding="utf-8")
        print(f"(rapport ecrit dans {args.out})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
