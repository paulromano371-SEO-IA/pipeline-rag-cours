"""Rapport de qualité du graphe de concepts, hors pipeline, LECTURE SEULE.

Adapté de l'ancien `tools/graph_quality_report.py` (déplacé ici) : cette
étape reste, comme lui, entièrement déterministe (aucun appel `claude -p`) —
c'est `/rag-integrity` (voir SKILL.md) qui enchaîne ensuite `audit_lot.py`,
lequel utilise Claude pour juger les cas ambigus que CE script se contente
de repérer et de documenter avec des preuves (extraits de chunks réels).

Le graphe Kuzu (`rag_data/db/graph/`) est ouvert en mode `read_only=True` :
aucune écriture possible, ni sur le graphe, ni sur `rag_data/work/`. Si un
autre processus (ex. /rag-graphe en cours) tient la base, l'ouverture échoue
proprement — relancer une fois celui-ci terminé.

Il n'existe pas de vérité de référence pour un graphe extrait automatiquement :
ce rapport ne rend donc PAS de note. Il produit (1) des métriques structurelles
qui repèrent des anomalies, et (2) des échantillons à auditer — par Claude
(voir audit_lot.py), avec les chunks comme preuve — pour estimer la qualité de
la résolution d'entités, le point qui décide de la valeur inter-livres du graphe.

Bilan par livre
  Vue consolidée, un livre par ligne, juste avant "Signaux à examiner" : reprend
  des chiffres déjà détaillés dans les parties 1 et 2 (chunks, concepts, part
  partagée avec le corpus, hubs, fusions manquées, doublons internes), pour
  lire l'état d'un livre précis sans parcourir les tableaux entiers.

Partie 1 — Métriques structurelles
  1.1 Volumes par livre (chunks, liens, concepts, chunks sans concept)
  1.2 Concepts : types, longue traîne (mentions uniques), partage entre livres,
      alias, cohérence des dimensions d'embedding, concepts orphelins
  1.3 Concepts "hubs" (mentionnés par une grande part des chunks d'un livre)

Partie 2 — Audit de la résolution d'entités
  2.1 Concepts partagés entre livres : tous listés (jusqu'à --max-shared) avec
      leurs alias et un extrait de chunk par livre. Risque : FUSION À TORT
      (deux notions différentes fusionnées), la pire erreur car elle crée de
      faux liens entre livres.
  2.2 Fusions internes suspectes : concepts à plusieurs alias, triés par
      dissemblance lexicale alias/forme canonique (les plus suspects d'abord)
      + un tirage aléatoire (--sample) pour estimer la précision sans biais.
  2.3 Fusions MANQUÉES entre livres : paires de concepts de livres différents,
      jamais fusionnées, dont l'embedding est proche (>= --near-threshold).
  2.4 Doublons probables au sein d'un même livre (>= --intra-threshold).
  2.5 Alias portés par plusieurs concepts distincts (résolution incohérente).

Chaque tableau d'audit porte une colonne "Verdict" vide dans CE rapport —
`/rag-integrity` la remplit ensuite via `audit_lot.py`, qui produit un
rapport séparé (`<stem>_audit.md`) plutôt que de réécrire celui-ci.

Évolution depuis le dernier rapport
  Chaque exécution enregistre un instantané chiffré (pas le rapport entier)
  dans --history (par défaut rag_data/audit/historique.json). Au run suivant,
  ce fichier permet d'afficher un delta : le corpus s'est-il amélioré ou
  dégradé depuis la dernière fois, et pourquoi (quel(s) livre(s) ajouté(s),
  quels compteurs ont bougé). --no-history désactive la lecture/écriture.

Sortie machine
  En plus du Markdown, écrit toujours un `.json` à côté de --out (même nom,
  extension json) avec les métriques et la liste `audit_items` : exactement
  les lignes auditables affichées dans les tableaux ci-dessus (mêmes bornes
  --max-shared/--sample/--max-pairs), chacune avec le contexte nécessaire à
  un jugement (nom, type, alias, extraits de chunks réels) — c'est l'entrée
  de `audit_lot.py`, qui ne relit jamais le graphe Kuzu lui-même.

  --max-items (défaut 300, 0 = illimité) plafonne le nombre TOTAL d'items,
  toutes catégories confondues — indépendant de --max-shared/--sample/
  --max-pairs, qui ne bornent que l'affichage (et les items) de CHAQUE
  section prise séparément. Sans ce plafond global, un corpus à beaucoup de
  livres accumule des items sans limite, donc sans limite sur le coût total
  en appels `claude -p` de l'étape suivante. Voir cap_items().

Usage:
    python report.py [--out rapport.md] [--sample 25] [--seed 0]
        (par défaut, --out vaut rag_data/audit/rapport_graphe_<AAAAMMJJ_HHMMSS>.md)
        [--max-shared 60] [--max-pairs 40] [--near-threshold 0.80]
        [--intra-threshold 0.88] [--hub-share 0.10] [--max-items 300]
        [--history rag_data/audit/historique.json] [--no-history]
"""
from __future__ import annotations
# Garde-fou : force l'interpreteur du projet (.venv-rag), voir _rag_lib/venv_guard.py
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(next(p for p in _Path(__file__).resolve().parents if (p / ".claude" / "skills" / "_rag_lib").is_dir()) / ".claude" / "skills" / "_rag_lib"))
import venv_guard  # noqa: F401

import argparse
import difflib
import json
import random
import re
import statistics
import sys
from datetime import datetime, timezone
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_rag_lib"))

import numpy as np

import paths
import decisions
from checks import MAX_MISSING_SENSE_RATE

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


# ---------------------------------------------------------------------------
# Chargement (lecture seule)
# ---------------------------------------------------------------------------

def _resolution_thresholds() -> tuple[float, float]:
    """Seuils de `entity_resolution.py` lus dans son source (sans l'importer :
    il tire sentence-transformers/torch) pour rester synchronisés avec lui."""
    entity_resolution_path = (
        Path(__file__).resolve().parents[2] / "rag-graphe" / "scripts" / "entity_resolution.py"
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
    # colonne `sense` (definition courte, voir concepts.ConceptMention.sense) :
    # absente d'une base creee avant son ajout et jamais migree (ouverture en
    # lecture seule ici, donc pas d'ALTER TABLE) -> repli sans elle.
    try:
        rows = list(_rows(conn, "MATCH (c:Concept) RETURN c.id, c.canonical_form, c.type, c.aliases, c.embedding, c.sense"))
    except RuntimeError:
        rows = [(*r, "") for r in _rows(conn, "MATCH (c:Concept) RETURN c.id, c.canonical_form, c.type, c.aliases, c.embedding")]
    concepts = {
        r[0]: {"id": r[0], "canonical": r[1], "type": r[2],
               # colonne `aliases` : chaîne JSON (nouveau schéma) ou liste (ancienne base)
               "aliases": json.loads(r[3]) if isinstance(r[3], str) and r[3] else list(r[3] or []),
               "embedding": r[4], "sense": (r[5] or "").strip()}
        for r in rows
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


_chunk_meta_cache: dict[str, dict[int, dict]] = {}


def _chunk_meta(doc: str, index: int) -> dict:
    """Enregistrement complet du chunk (chunks.json), pour ses champs
    `has_code`/`has_formula` — jamais mutuellement exclusifs ni exhaustifs
    avec le "reste du texte" (un chunk peut avoir les deux a la fois)."""
    if doc not in _chunk_meta_cache:
        path = paths.WORK_DIR / doc / "chunks.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            _chunk_meta_cache[doc] = {c["index"]: c for c in data}
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            _chunk_meta_cache[doc] = {}
    return _chunk_meta_cache[doc].get(index, {})


def chunk_type_breakdown(chunk_infos: list[dict]) -> dict:
    """Repartition, parmi des chunks (dicts {"doc","index"}), de ceux qui
    contiennent du code, de la formule, ou ni l'un ni l'autre — donnee comme
    preuve supplementaire a Claude en 1.3, PAS comme nouveau seuil : la
    consigne (audit_run.py) lui laisse interpreter cette repartition a la
    lumiere du type du concept (une methode/un outil precis dans du
    code/formule = signe de precision, un concept general disperse sans
    lien = suspect) plutot que d'imposer une regle fixe ici."""
    avec_code = sum(1 for c in chunk_infos if _chunk_meta(c["doc"], c["index"]).get("has_code"))
    avec_formule = sum(1 for c in chunk_infos if _chunk_meta(c["doc"], c["index"]).get("has_formula"))
    texte_seul = sum(
        1 for c in chunk_infos
        if not _chunk_meta(c["doc"], c["index"]).get("has_code") and not _chunk_meta(c["doc"], c["index"]).get("has_formula")
    )
    return {"avec_code": avec_code, "avec_formule": avec_formule, "texte_seul": texte_seul}


# Fenêtre d'extrait donnée au juge (l'affichage Markdown garde 170 : lisible en
# tableau). Alignée sur l'arbitrage de /rag-graphe (400 caractères).
EVIDENCE_WIDTH = 400
# Plafond d'un chunk COMPLET fourni à la 2e passe d'audit (voir audit_run.py).
FULL_CHUNK_MAX = 2500


def _term_match(text: str, terms: list[str]):
    """Première occurrence (mot entier, insensible à la casse) d'un des termes."""
    for term in sorted({t for t in terms if len(t) >= 2}, key=len, reverse=True):
        match = re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.IGNORECASE)
        if match:
            return match
    return None


def _fuzzy_match(text: str, terms: list[str]):
    """Repli quand AUCUN terme n'apparaît tel quel : premier mot significatif
    (>= 6 lettres) d'un des termes, cherché par son radical (les 2 dernières
    lettres retirées au-delà de 7 lettres : "spécifique" retrouve "spécifiques").
    Preuve plus faible qu'une occurrence exacte — signalée comme telle au juge
    ("terme": "approché") — mais bien meilleure que le début du chunk, qui n'a
    souvent aucun rapport avec le concept (ex. "gabarit cypher" absent tel quel
    d'un chunk qui dit "un gabarit de requête codée en dur")."""
    words = sorted({w for t in terms for w in re.findall(r"\w+", t.lower()) if len(w) >= 6}, key=len, reverse=True)
    for word in words:
        stem = word[:-2] if len(word) >= 7 else word
        match = re.search(r"(?<!\w)" + re.escape(stem), text, re.IGNORECASE)
        if match:
            return match
    return None


def chunk_excerpt_info(doc: str, index: int, *, terms: list[str] | None = None, width: int = 170) -> tuple[str, str]:
    """(extrait, nature de la preuve). Extrait d'un chunk (chunks.json du
    dossier de travail), sur une seule ligne, commentaires HTML retirés, centré
    sur la première occurrence d'un des `terms`. Nature : "exact" (un terme
    apparaît tel quel), "approché" (seul un radical de mot apparaît) ou "absent"
    (aucun terme trouvé : l'extrait est alors le DÉBUT du chunk et ne prouve
    rien — le juge doit en tenir compte). Extrait vide si chunk indisponible."""
    text = _chunk_text(doc, index)
    kind = "exact"
    match = _term_match(text, terms or [])
    if not match:
        match, kind = _fuzzy_match(text, terms or []), "approché"
    if not match:
        return ((text[:width] + "...") if len(text) > width else text), "absent"
    start = max(0, match.start() - width // 2)
    end = min(len(text), start + width)
    return ("..." if start else "") + text[start:end] + ("..." if end < len(text) else ""), kind


def chunk_excerpt(doc: str, index: int, *, terms: list[str] | None = None, width: int = 170) -> str:
    return chunk_excerpt_info(doc, index, terms=terms, width=width)[0]


def has_term(doc: str, index: int, terms: list[str], *, skip_toc: bool = False, fuzzy: bool = False) -> bool:
    text = _chunk_text(doc, index)
    if skip_toc and text.count(". . .") >= 3:
        return False  # table des matières : cite le terme sans en prouver le sens
    return (_fuzzy_match(text, terms) if fuzzy else _term_match(text, terms)) is not None


def _best_chunks(concept: dict, chunk_ids, chunks: dict, docs_filter: list[str] | None) -> tuple[list[str], list[tuple[str, dict]]]:
    """(termes du concept, [(livre, chunk le plus probant)] — un par livre).
    Ordre de préférence : occurrence exacte hors table des matières, exacte,
    approchée, à défaut le premier chunk du livre."""
    terms = [concept["canonical"], *concept["aliases"]]
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for ch in chunk_ids:
        if ch not in chunks:
            continue
        info = chunks[ch]
        if docs_filter is not None and info["doc"] not in docs_filter:
            continue
        by_doc[info["doc"]].append(info)
    best_per_doc = []
    for doc, infos in sorted(by_doc.items()):
        infos = sorted(infos, key=lambda i: i["index"])
        best = (
            next((i for i in infos if has_term(doc, i["index"], terms, skip_toc=True)), None)
            or next((i for i in infos if has_term(doc, i["index"], terms)), None)
            or next((i for i in infos if has_term(doc, i["index"], terms, fuzzy=True)), None)
            or infos[0]
        )
        best_per_doc.append((doc, best))
    return terms, best_per_doc


def concept_excerpts(concept: dict, chunk_ids, chunks: dict, *, docs_filter: list[str] | None = None,
                     width: int = 170, with_kind: bool = False) -> list[dict]:
    """Un extrait par livre (restreint à `docs_filter` si fourni), centré sur
    la première occurrence prouvée du concept — utilisé à la fois pour
    l'affichage Markdown (2.1, 1.3) et comme preuve donnée à Claude dans
    `audit_items` : le jugement de audit_lot.py ne doit jamais se baser sur
    le seul nom du concept. `with_kind` ajoute "terme" (exact/approché/absent,
    voir chunk_excerpt_info) : sans lui, le juge ne peut pas distinguer un
    extrait qui PROUVE la mention d'un simple début de chunk."""
    terms, best_per_doc = _best_chunks(concept, chunk_ids, chunks, docs_filter)
    results = []
    for doc, info in best_per_doc:
        text, kind = chunk_excerpt_info(doc, info["index"], terms=terms, width=width)
        row = {"livre": short_doc(doc), "chunk": info["index"], "extrait": text}
        if with_kind:
            row["terme"] = kind
        results.append(row)
    return results


def _full_text(doc: str, index: int) -> str:
    text = _chunk_text(doc, index)
    return text if len(text) <= FULL_CHUNK_MAX else text[:FULL_CHUNK_MAX] + "..."


def concept_full_chunks(concept: dict, chunk_ids, chunks: dict, *, docs_filter: list[str] | None = None) -> list[dict]:
    """Chunks COMPLETS (les mêmes que ceux des extraits, un par livre) : preuve
    de la 2e passe d'audit, qui ne confirme un signalement que sur le texte
    entier, jamais sur une fenêtre de 400 caractères."""
    _, best_per_doc = _best_chunks(concept, chunk_ids, chunks, docs_filter)
    return [{"concept": concept["canonical"], "livre": short_doc(doc), "chunk": info["index"], "texte": _full_text(doc, info["index"])}
            for doc, info in best_per_doc]


def _unique_chunks(chunk_list: list[dict]) -> list[dict]:
    """Un même chunk peut servir de preuve à plusieurs titres (chunk du concept
    ET origine de l'alias, ou partagé par deux concepts) : ne l'envoyer qu'une fois."""
    seen, out = set(), []
    for ch in chunk_list:
        key = (ch["livre"], ch["chunk"])
        if key not in seen:
            seen.add(key)
            out.append(ch)
    return out


_doc_mentions_cache: dict[str, list[dict]] = {}


def _doc_mentions(doc: str) -> list[dict]:
    """concepts.json d'un livre ([{chunk_index, mentions:[{name, canonical_form,
    type, sense}]}]) : seule trace, après résolution, du chunk d'où vient chaque
    alias (le graphe Kuzu ne le garde pas)."""
    if doc not in _doc_mentions_cache:
        try:
            data = json.loads((paths.WORK_DIR / doc / "concepts.json").read_text(encoding="utf-8"))
            _doc_mentions_cache[doc] = data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            _doc_mentions_cache[doc] = []
    return _doc_mentions_cache[doc]


def alias_origins(docs, alias: str, chunk_ids, limit: int = 2) -> tuple[list[dict], list[dict]]:
    """(preuves pour le juge, chunks complets pour la 2e passe) de l'endroit où
    `alias` a été EXTRAIT (mention dont la forme canonique vaut l'alias, et
    donc rattachée au concept par la résolution) : la vraie preuve d'une fusion
    suspecte, avec le sens propre de la mention, plutôt qu'un chunk quelconque du
    concept. Privilégie les chunks liés au concept ; vide si concepts.json est
    absent (ex. work/ vidé)."""
    target = _norm(alias)
    linked = set(chunk_ids)
    hits: list[tuple[str, int, dict, bool]] = []
    for doc in sorted(docs):
        for entry in _doc_mentions(doc):
            for m in entry.get("mentions", []):
                if _norm(str(m.get("canonical_form", ""))) == target:
                    hits.append((doc, entry["chunk_index"], m, f"{doc}::{entry['chunk_index']}" in linked))
    hits.sort(key=lambda h: not h[3])  # tri stable : chunks liés au concept d'abord
    evidence, full = [], []
    seen: set[tuple[str, int]] = set()
    for doc, index, m, _ in hits:
        if len(evidence) >= limit:
            break
        if (doc, index) in seen:  # plusieurs mentions du même chunk : une seule preuve suffit
            continue
        seen.add((doc, index))
        text, kind = chunk_excerpt_info(doc, index, terms=[str(m.get("name", "")), alias], width=EVIDENCE_WIDTH)
        evidence.append({"livre": short_doc(doc), "chunk": index, "mention_dans_le_texte": m.get("name", ""),
                         "sens_de_la_mention": m.get("sense", ""), "terme": kind, "extrait": text})
        full.append({"concept": f"(origine de l'alias {alias!r})", "livre": short_doc(doc), "chunk": index, "texte": _full_text(doc, index)})
    return evidence, full


def _excerpts_line(excerpts: list[dict]) -> str:
    return " // ".join(f"[{e['livre']} #{e['chunk']}] {e['extrait']}" for e in excerpts)


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
# Partie 1 : métriques structurelles
# ---------------------------------------------------------------------------

def part1(graph: dict, hub_share: float) -> tuple[list[str], list[str], list[dict]]:
    concepts, chunks, mentions = graph["concepts"], graph["chunks"], graph["mentions"]
    docs = sorted({c["doc"] for c in chunks.values()})
    chunks_of_concept: dict[str, set[str]] = defaultdict(set)
    concepts_of_chunk: dict[str, set[str]] = defaultdict(set)
    for chunk_id, concept_id in mentions:
        chunks_of_concept[concept_id].add(chunk_id)
        concepts_of_chunk[chunk_id].add(concept_id)
    docs_of_concept = {cid: {chunks[ch]["doc"] for ch in chs if ch in chunks} for cid, chs in chunks_of_concept.items()}

    signals: list[str] = []
    items: list[dict] = []
    out: list[str] = ["## Partie 1 — Métriques structurelles", "", "### 1.1 Volumes par livre", ""]

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
    out += table(["Livre", "Chunks", "Liens", "Concepts distincts", "Chunks sans concept", "Concepts/chunk moy / méd / max"], rows)

    out += ["", "### 1.2 Concepts", ""]
    n = len(concepts)
    single = sum(1 for cid in concepts if len(chunks_of_concept.get(cid, ())) == 1)
    orphans = [cid for cid in concepts if not chunks_of_concept.get(cid)]
    n_docs = Counter(len(docs_of_concept.get(cid, ())) for cid in concepts)
    alias_counts = [len({a.lower() for a in c["aliases"]} | {c["canonical"].lower()}) for c in concepts.values()]
    dims = Counter(len(c["embedding"] or []) for c in concepts.values())
    no_sense = sum(1 for c in concepts.values() if not c["sense"])

    out += table(["Indicateur", "Valeur"], [
        ["Concepts au total", n],
        ["Mentionnés par un seul chunk (longue traîne)", f"{single} ({pct(single, n)})"],
        ["Présents dans 1 seul livre", f"{n_docs.get(1, 0)} ({pct(n_docs.get(1, 0), n)})"],
        ["Partagés entre >= 2 livres", f"{sum(v for k, v in n_docs.items() if k >= 2)} ({pct(sum(v for k, v in n_docs.items() if k >= 2), n)})"],
        ["Concepts orphelins (aucun lien)", len(orphans)],
        ["Formes distinctes par concept (moy / max)", f"{statistics.mean(alias_counts):.2f} / {max(alias_counts)}" if alias_counts else "n/a"],
        ["Concepts sans définition (`sense`)", f"{no_sense} ({pct(no_sense, n)})"],
        ["Dimensions d'embedding", ", ".join(f"{d} ({c})" for d, c in sorted(dims.items())) or "n/a"],
    ])
    if n and single / n > 0.75:
        signals.append(f"{pct(single, n)} des concepts ne sont mentionnés que par un chunk : formes canoniques peut-être trop fragmentées")
    if orphans:
        signals.append(f"{len(orphans)} concept(s) orphelin(s) : incohérence du graphe (nettoyage de remove_document ?)")
    if n and no_sense / n > MAX_MISSING_SENSE_RATE:
        signals.append(
            f"{no_sense}/{n} concept(s) sans définition (`sense`, {pct(no_sense, n)} > {MAX_MISSING_SENSE_RATE:.0%}) : "
            "concepts.json extrait avant l'ajout de `sense` ? (le juge de l'étape 2 travaille alors sans cette aide)"
        )
    if len(dims) > 1:
        signals.append(f"embeddings de dimensions différentes ({dict(dims)}) : plusieurs modèles mélangés, similarités non comparables")

    types = Counter(c["type"] for c in concepts.values())
    out += ["", "Répartition par type :", ""]
    out += table(["Type", "Concepts", "Part"], [[t, c, pct(c, n)] for t, c in types.most_common()])

    out += ["", f"### 1.3 Concepts \"hubs\" (mentionnés par >= {hub_share:.0%} des chunks d'un livre)", ""]
    chunks_per_doc = Counter(c["doc"] for c in chunks.values())
    hubs = []
    for cid, chs in chunks_of_concept.items():
        per_doc = Counter(chunks[ch]["doc"] for ch in chs if ch in chunks)
        doc_shares = {d: cnt / chunks_per_doc[d] for d, cnt in per_doc.items()}
        share = max(doc_shares.values(), default=0.0)
        if share >= hub_share:
            # Le livre où extraire l'exemple doit être celui qui justifie le
            # statut de hub (la plus grande PART, "Part max" affichée) — jamais
            # celui qui a le plus de mentions BRUTES, qui peut être un livre
            # différent et bien plus gros où le concept n'est pas remarquable.
            top_doc = max(doc_shares, key=doc_shares.get)
            hubs.append({
                "id": cid, "canonical": concepts[cid]["canonical"], "type": concepts[cid]["type"],
                "chunks": len(chs), "n_docs": len(per_doc), "share": share, "top_doc": top_doc,
                "per_doc": per_doc, "doc_shares": doc_shares,
                "detail": ", ".join(f"{short_doc(d)}:{k}" for d, k in per_doc.most_common()),
            })
    hubs.sort(key=lambda h: -h["chunks"])
    shown_hubs = hubs[:15]
    out += table(
        ["Concept", "Type", "Chunks", "Livres", "Part max", "Détail"],
        [[h["canonical"], h["type"], h["chunks"], h["n_docs"], f"{h['share']:.0%}", h["detail"]] for h in shown_hubs],
    )
    if hubs:
        out += ["", "Un hub très générique (ex. \"modèle\", \"données\") relie tout à tout : bruit pour l'expansion par concept."]
        signals.append(f"{len(hubs)} concept(s) hub(s) >= {hub_share:.0%} : vérifier qu'ils ne sont pas trop génériques (1.3)")
    # Item d'audit PAR (concept, livre) où le seuil est franchi, pas par
    # concept seul : un concept peut être hub dans plusieurs livres à la fois
    # (ex. "llm" à 49% dans un livre ET 27% dans un autre) avec potentiellement
    # une généricité différente dans chacun — un seul verdict global, basé sur
    # un seul extrait d'un seul livre, masquerait cette différence. Le tableau
    # 1.3 ci-dessus reste lui agrégé (une ligne par concept) : seul l'audit se
    # décompose par livre.
    for h in shown_hubs:
        hub_docs = sorted(d for d, s in h["doc_shares"].items() if s >= hub_share)
        for doc in hub_docs:
            excerpts = concept_excerpts(concepts[h["id"]], chunks_of_concept.get(h["id"], ()), chunks, docs_filter=[doc])
            mentioning_chunks = [chunks[ch] for ch in chunks_of_concept.get(h["id"], ()) if ch in chunks and chunks[ch]["doc"] == doc]
            items.append({
                "id": f"hub:{h['id']}:{doc}", "category": "1.3", "subject": f"{h['canonical']} (dans {short_doc(doc)})",
                "type": h["type"],
                "context": {
                    "livre": short_doc(doc), "sens": concepts[h["id"]]["sense"], "part_dans_ce_livre": f"{h['doc_shares'][doc]:.0%}",
                    "chunks_du_concept_dans_ce_livre": h["per_doc"][doc], "chunks_total_du_livre": chunks_per_doc[doc],
                    "repartition_chunks": chunk_type_breakdown(mentioning_chunks),
                    "extraits": excerpts,
                },
            })
    return out, signals, items


# ---------------------------------------------------------------------------
# Partie 2 : audit de la résolution d'entités
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def alias_dissimilarity(concept: dict) -> tuple[float, str]:
    """(plus faible ressemblance lexicale alias/forme canonique, alias en cause).
    Bas = alias très différent de la forme canonique = fusion suspecte."""
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
    """Paires de concepts distincts proches par embedding, jamais fusionnées.
    (paires inter-livres, paires intra-livre), triées par similarité décroissante."""
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


# ---------------------------------------------------------------------------
# Historique et delta depuis le dernier rapport
# ---------------------------------------------------------------------------

def load_history(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_history(path: Path, history: list[dict], *, keep_last: int = 500) -> None:
    path.write_text(json.dumps(history[-keep_last:], ensure_ascii=False, indent=2), encoding="utf-8")


def compute_metrics(graph: dict, args, merge_threshold: float, ambiguous_threshold: float, docs: list[str]) -> dict:
    """Instantané chiffré du graphe, indépendant de la mise en forme Markdown
    des parties 1/2 — c'est ce qui est écrit dans --history pour permettre un
    delta au prochain run. Recalcule volontairement les mêmes agrégats que
    part1/part2 (docs_of_concept, near_pairs) plutôt que de les leur emprunter :
    garde ce calcul autonome et lisible indépendamment de la mise en forme."""
    concepts, chunks, mentions = graph["concepts"], graph["chunks"], graph["mentions"]
    chunks_of_concept: dict[str, set[str]] = defaultdict(set)
    concepts_of_chunk: dict[str, set[str]] = defaultdict(set)
    for chunk_id, concept_id in mentions:
        chunks_of_concept[concept_id].add(chunk_id)
        concepts_of_chunk[chunk_id].add(concept_id)
    docs_of_concept = {cid: {chunks[ch]["doc"] for ch in chs if ch in chunks} for cid, chs in chunks_of_concept.items()}

    orphans = [cid for cid in concepts if not chunks_of_concept.get(cid)]
    short_alias = {
        f"{c['canonical']} <- {a}" for c in concepts.values() for a in c["aliases"]
        if _norm(a) != _norm(c["canonical"]) and len(_norm(a)) <= 3
    }
    owners: dict[str, list[dict]] = defaultdict(list)
    for c in concepts.values():
        for form in {_norm(a) for a in c["aliases"]} | {_norm(c["canonical"])}:
            owners[form].append(c)
    collisions = [form for form, cs in owners.items() if len(cs) >= 2]
    shared = [c for cid, c in concepts.items() if len(docs_of_concept.get(cid, ())) >= 2]
    cross, same = near_pairs(graph, docs_of_concept, (args.near_threshold, args.intra_threshold))
    missed = [(sim, a, b) for sim, a, b in cross if sim >= merge_threshold]

    # mentions par (livre, concept) : sert à déterminer, pour CE livre précisément,
    # si un concept y atteint le seuil de hub (même critère que 1.3, mais localisé)
    doc_concept_mentions: dict[tuple[str, str], int] = defaultdict(int)
    for chunk_id, concept_id in mentions:
        ch = chunks.get(chunk_id)
        if ch:
            doc_concept_mentions[(ch["doc"], concept_id)] += 1

    per_doc = {}
    for doc in docs:
        doc_chunks = [c for c in chunks.values() if c["doc"] == doc]
        no_concept = [c for c in doc_chunks if not concepts_of_chunk.get(c["id"])]
        doc_concepts = {cid for cid, ds in docs_of_concept.items() if doc in ds}
        doc_shared = {cid for cid in doc_concepts if len(docs_of_concept.get(cid, ())) >= 2}
        doc_missed = [
            t for t in missed
            if doc in docs_of_concept.get(t[1]["id"], ()) or doc in docs_of_concept.get(t[2]["id"], ())
        ]
        doc_intra_dupes = [
            t for t in same
            if doc in (docs_of_concept.get(t[1]["id"], set()) & docs_of_concept.get(t[2]["id"], set()))
        ]
        doc_hubs = sum(
            1 for cid in doc_concepts
            if doc_chunks and doc_concept_mentions.get((doc, cid), 0) / len(doc_chunks) >= args.hub_share
        )
        per_doc[doc] = {
            "chunks": len(doc_chunks),
            "chunks_no_concept_pct": round(100 * len(no_concept) / len(doc_chunks), 1) if doc_chunks else 0.0,
            "concepts_distinct": len(doc_concepts),
            "shared_concepts": len(doc_shared),
            "hubs": doc_hubs,
            "missed_merges": len(doc_missed),
            "intra_duplicates": len(doc_intra_dupes),
        }

    return {
        "docs": docs,
        "global": {
            "concepts": len(concepts),
            "chunks": len(chunks),
            "mentions": len(mentions),
            "orphans": len(orphans),
            "short_alias_count": len(short_alias),
            "collisions_count": len(collisions),
            "shared_concepts_count": len(shared),
            "missed_merges_count": len(missed),
            "intra_duplicates_count": len(same),
        },
        "per_doc": per_doc,
    }


# métrique -> "up" (une hausse est une amélioration) ou "down" (une hausse est une dégradation)
_GLOBAL_DIRECTION = {
    "shared_concepts_count": ("up", "concepts partagés entre livres (recoupement du corpus, 2.1)"),
    "missed_merges_count": ("down", "fusions manquées >= seuil de fusion auto, jamais fusionnées (2.3)"),
    "intra_duplicates_count": ("down", "doublons probables au sein d'un même livre (2.4)"),
    "orphans": ("down", "concepts orphelins, aucun lien (1.2)"),
    "short_alias_count": ("down", "alias de <= 3 caractères, fusions à tort probables (2.2)"),
    "collisions_count": ("down", "formes de surface portées par plusieurs concepts (2.5)"),
}


def _delta_line(label: str, prev_v: int, cur_v: int, direction: str) -> str:
    diff = cur_v - prev_v
    sign = f"+{diff}" if diff > 0 else str(diff)
    if diff == 0:
        verdict = "stable"
    elif (diff > 0) == (direction == "up"):
        verdict = "AMÉLIORATION"
    else:
        verdict = "DÉGRADATION"
    return f"- {label} : {prev_v} -> {cur_v} ({sign}) — {verdict}"


def render_delta_section(history: list[dict], current: dict) -> list[str]:
    out = ["## Évolution depuis le dernier rapport", ""]
    if not history:
        out += [
            "Premier rapport enregistré dans l'historique : pas de comparaison possible. "
            "Le prochain rapport (après ingestion d'un nouveau livre, ou relance sur le même corpus) "
            "affichera le delta depuis celui-ci.",
        ]
        return out

    prev = history[-1]
    prev_docs, cur_docs = set(prev.get("docs", [])), set(current["docs"])
    new_docs = sorted(cur_docs - prev_docs)
    removed_docs = sorted(prev_docs - cur_docs)

    if new_docs:
        out.append(f"Nouveau(x) livre(s) depuis le dernier rapport ({prev.get('date', '?')}) : " + ", ".join(f"`{short_doc(d)}`" for d in new_docs))
        out.append("")
        out.append("**Qualité du/des livre(s) ajouté(s)**, mesurée sur ce seul livre (indicateurs de la partie 2 rattachés à lui) :")
        out.append("")
        out += table(
            ["Livre", "Chunks", "Chunks sans concept", "Concepts partagés avec le corpus", "Fusions manquées (2.3)", "Doublons internes (2.4)"],
            [
                [short_doc(d), current["per_doc"][d]["chunks"], f"{current['per_doc'][d]['chunks_no_concept_pct']} %",
                 current["per_doc"][d]["shared_concepts"], current["per_doc"][d]["missed_merges"], current["per_doc"][d]["intra_duplicates"]]
                for d in new_docs if d in current["per_doc"]
            ],
        )
        out += [
            "", "Lecture : `Concepts partagés avec le corpus` élevé = ce livre se connecte bien au reste (bon signe, "
            "valeur ajoutée du GraphRAG). `Fusions manquées`/`Doublons internes` > 0 = points à auditer en priorité "
            "pour CE livre précisément (sections 2.3/2.4 du rapport, filtrer sur son nom).",
        ]
    elif removed_docs:
        out.append(f"Aucun nouveau livre, mais {len(removed_docs)} retiré(s) depuis le dernier rapport : " + ", ".join(f"`{short_doc(d)}`" for d in removed_docs))
    else:
        out.append("Aucun nouveau livre depuis le dernier rapport (même corpus — relance après reconstruction, changement de seuils, etc.).")

    out += ["", "**Delta global du graphe** (tout le corpus, pas seulement le(s) livre(s) ajouté(s)) :", ""]
    out.append(
        f"Volume : {prev['global']['concepts']} -> {current['global']['concepts']} concepts, "
        f"{prev['global']['chunks']} -> {current['global']['chunks']} chunks, "
        f"{prev['global']['mentions']} -> {current['global']['mentions']} liens."
    )
    out.append("")
    for key, (direction, label) in _GLOBAL_DIRECTION.items():
        out.append(_delta_line(label, prev["global"][key], current["global"][key], direction))
    degradations = sum(
        1 for key, (direction, _) in _GLOBAL_DIRECTION.items()
        if (current["global"][key] - prev["global"][key]) != 0 and (current["global"][key] - prev["global"][key] > 0) != (direction == "up")
    )
    out += ["", f"=> {degradations} indicateur(s) en dégradation depuis le dernier rapport." if degradations else "=> Aucun indicateur en dégradation depuis le dernier rapport."]
    return out


def render_per_book_section(current: dict) -> list[str]:
    """Vue consolidée, un livre par ligne : reprend des chiffres déjà détaillés
    dans les parties 1 et 2, rassemblés ici pour une lecture rapide par livre —
    sans avoir à parcourir les tableaux entiers de 2.1/2.3/2.4 pour un seul livre."""
    docs = current["docs"]
    shares = {
        doc: (100 * current["per_doc"][doc]["shared_concepts"] / current["per_doc"][doc]["concepts_distinct"])
        if current["per_doc"][doc]["concepts_distinct"] else 0.0
        for doc in docs
    }
    avg_share = statistics.mean(shares.values()) if shares else 0.0

    out = ["## Bilan par livre", "", (
        "Lecture : `Partagés` = part des concepts DE CE LIVRE aussi mentionnés par au moins un autre livre "
        "(concepts partagés / concepts distincts de 2.1) — comparable d'un livre à l'autre malgré des tailles "
        f"différentes, contrairement à un simple compte. Moyenne du corpus : {avg_share:.1f} %. Nettement en dessous "
        "= ce livre recoupe peu les autres (sujet à part, ou fusions possiblement manquées, voir 2.3). "
        "`Hubs` très élevé peut signaler des concepts trop génériques (voir 1.3). `Fusions manquées`/`Doublons "
        "internes` > 0 = à auditer en priorité pour CE livre (sections 2.3/2.4, filtrer sur son nom)."
    ), ""]
    rows = []
    for doc in docs:
        m = current["per_doc"][doc]
        flags = []
        if m["chunks_no_concept_pct"] > 5:
            flags.append("chunks sans concept > 5%")
        if m["missed_merges"] > 0:
            flags.append("fusions manquées")
        if m["intra_duplicates"] > 0:
            flags.append("doublons internes")
        rows.append([
            short_doc(doc), m["chunks"], m["concepts_distinct"], f"{m['chunks_no_concept_pct']} %",
            f"{shares[doc]:.1f} % ({m['shared_concepts']})", m["hubs"], m["missed_merges"], m["intra_duplicates"],
            ", ".join(flags) if flags else "-",
        ])
    out += table(
        ["Livre", "Chunks", "Concepts distincts", "Sans concept", "Partagés (2.1)", "Hubs (1.3)",
         "Fusions manquées (2.3)", "Doublons internes (2.4)", "À vérifier"],
        rows,
    )
    return out


def part2(graph: dict, args, merge_threshold: float, ambiguous_threshold: float) -> tuple[list[str], list[str], list[dict]]:
    concepts, chunks, mentions = graph["concepts"], graph["chunks"], graph["mentions"]
    rng = random.Random(args.seed)
    chunks_of_concept: dict[str, list[str]] = defaultdict(list)
    for chunk_id, concept_id in mentions:
        chunks_of_concept[concept_id].append(chunk_id)
    docs_of_concept = {cid: {chunks[ch]["doc"] for ch in chs if ch in chunks} for cid, chs in chunks_of_concept.items()}

    def ev(c: dict) -> list[dict]:
        return concept_excerpts(c, chunks_of_concept.get(c["id"], ()), chunks, width=EVIDENCE_WIDTH, with_kind=True)

    def full(*cs: dict) -> dict:
        """Chunks complets de la 2e passe (audit_run.py), hors `context` : ne
        servent qu'à confirmer un signalement et ne rentrent pas dans la
        comparaison de reprise des verdicts entre rapports."""
        return {"chunks": _unique_chunks([ch for c in cs for ch in concept_full_chunks(c, chunks_of_concept.get(c["id"], ()), chunks)])}

    def alias_item(c: dict, alias: str, extra: dict) -> dict:
        origins, origin_chunks = alias_origins(docs_of_concept.get(c["id"], ()), alias, chunks_of_concept.get(c["id"], ()))
        confirmation = full(c)
        confirmation["chunks"] = _unique_chunks(confirmation["chunks"] + origin_chunks)
        return {
            "id": f"alias:{c['id']}", "category": "2.2", "subject": c["canonical"], "type": c["type"],
            "context": {"sens": c["sense"], "alias_suspect": alias, **extra, "tous_les_alias": sorted({a for a in c["aliases"]}),
                        "alias_origine": origins, "extraits": ev(c)},
            "confirmation": confirmation,
        }

    items: list[dict] = []
    signals: list[str] = []
    out = [
        "## Partie 2 — Audit de la résolution d'entités", "",
        f"Seuils du pipeline : fusion automatique >= {merge_threshold}, arbitrage LLM entre {ambiguous_threshold} et {merge_threshold}, "
        f"nouveau concept en dessous. La colonne \"Verdict\" ci-dessous reste VOLONTAIREMENT vide dans ce fichier : "
        f"elle n'est jamais remplie à la main ici. `/rag-integrity` (audit_lot.py) juge chaque ligne auditable "
        f"séparément et écrit ses verdicts dans un rapport dédié (`<nom-de-ce-rapport>_audit.md`, à côté de "
        f"celui-ci) — c'est ce fichier-là qu'il faut consulter pour les verdicts, jamais cette colonne.", "",
        "### 2.1 Concepts partagés entre livres (risque : fusion à tort)", "",
    ]
    shared = [c for cid, c in concepts.items() if len(docs_of_concept.get(cid, ())) >= 2]
    shared.sort(key=lambda c: -len(chunks_of_concept[c["id"]]))
    shown_shared = shared[:args.max_shared]
    out.append(f"{len(shared)} concept(s) partagé(s) ; {len(shown_shared)} listé(s) (les plus mentionnés d'abord).")
    out.append("")
    shared_excerpts = {c["id"]: concept_excerpts(c, chunks_of_concept.get(c["id"], ()), chunks) for c in shown_shared}
    out += table(
        ["Concept", "Type", "Alias", "Chunks", "Extraits (un par livre)", "Verdict"],
        [[c["canonical"], c["type"], ", ".join(sorted({a for a in c["aliases"] if _norm(a) != _norm(c["canonical"])})[:6]) or "-",
          len(chunks_of_concept[c["id"]]), _excerpts_line(shared_excerpts[c["id"]]), ""] for c in shown_shared],
    )
    if not shared:
        signals.append("aucun concept partagé entre livres : soit les livres ne se recoupent pas, soit des fusions sont manquées (voir 2.3)")
    for c in shown_shared:
        items.append({
            "id": f"shared:{c['id']}", "category": "2.1", "subject": c["canonical"], "type": c["type"],
            "context": {"sens": c["sense"], "alias": sorted({a for a in c["aliases"] if _norm(a) != _norm(c["canonical"])}),
                        "livres": sorted(short_doc(d) for d in docs_of_concept.get(c["id"], ())),
                        "extraits": ev(c)},
            "confirmation": full(c),
        })

    out += ["", "### 2.2 Fusions internes suspectes", ""]
    merged = [c for c in concepts.values() if len({_norm(a) for a in c["aliases"]} - {_norm(c["canonical"])}) >= 1]
    scored = sorted(((alias_dissimilarity(c), c) for c in merged), key=lambda t: t[0][0])
    out.append(
        f"{len(merged)} concept(s) portent au moins un alias différent de leur forme canonique. "
        f"Ressemblance lexicale alias/forme canonique : faible = fusion à vérifier (ex. deux notions voisines)."
    )
    out += ["", f"**Les {min(args.sample, len(scored))} plus dissemblables :**", ""]
    dissimilar_shown = scored[:args.sample]
    out += table(["Concept", "Type", "Alias le plus éloigné", "Ressemblance", "Tous les alias", "Verdict"],
                 [[c["canonical"], c["type"], worst_alias, f"{ratio:.2f}", ", ".join(sorted({a for a in c["aliases"]})[:8]), ""]
                  for (ratio, worst_alias), c in dissimilar_shown])
    for (ratio, worst_alias), c in dissimilar_shown:
        items.append(alias_item(c, worst_alias, {"ressemblance": round(ratio, 2)}))
    short_alias = sorted(
        {f"{c['canonical']} <- {a}" for c in concepts.values() for a in c["aliases"] if _norm(a) != _norm(c["canonical"]) and len(_norm(a)) <= 3}
    )
    if short_alias:
        out += ["", f"**Alias de 3 caractères ou moins ({len(short_alias)}) — l'embedding d'une chaîne aussi courte est peu fiable, "
                    f"risque de fusion à tort :** " + ", ".join(f"`{s}`" for s in short_alias[:20])]
        signals.append(f"{len(short_alias)} alias de <= 3 caractères (ex. {short_alias[0]}) : fusions à tort probables, à auditer en 2.2")
    rest = [c for _, c in scored[args.sample:]]
    draw = rng.sample(rest, min(args.sample, len(rest)))
    out += ["", f"**Tirage aléatoire de {len(draw)} (graine {args.seed}) — sert à estimer la précision sans biais :**", ""]
    out += table(["Concept", "Type", "Tous les alias", "Livres", "Verdict"],
                 [[c["canonical"], c["type"], ", ".join(sorted({a for a in c["aliases"]})[:8]),
                   ", ".join(sorted(short_doc(d) for d in docs_of_concept.get(c["id"], ()))), ""] for c in draw])
    for c in draw:
        _, worst_alias = alias_dissimilarity(c)
        items.append(alias_item(c, worst_alias, {}))

    cross, same = near_pairs(graph, docs_of_concept, (args.near_threshold, args.intra_threshold))
    out += ["", f"### 2.3 Fusions MANQUÉES entre livres (similarité >= {args.near_threshold}, livres disjoints)", ""]
    shown_cross = cross[:args.max_pairs]
    out.append(f"{len(cross)} paire(s) ; {len(shown_cross)} listée(s), les plus proches d'abord. "
               f"Au-dessus de {merge_threshold}, une fusion automatique aurait dû avoir lieu.")
    out.append("")
    out += table(["Sim.", "Concept A", "Livre A", "Concept B", "Livre B", "Verdict"],
                 [[f"{sim:.3f}" + (" (>= fusion auto)" if sim >= merge_threshold else ""), a["canonical"],
                   ", ".join(sorted(short_doc(d) for d in docs_of_concept.get(a["id"], ()))), b["canonical"],
                   ", ".join(sorted(short_doc(d) for d in docs_of_concept.get(b["id"], ()))), ""] for sim, a, b in shown_cross])
    above = sum(1 for sim, _, _ in cross if sim >= merge_threshold)
    if above:
        signals.append(f"{above} paire(s) inter-livres >= {merge_threshold} jamais fusionnée(s) : vérifier l'ordre de traitement/arbitrage (2.3)")
    for sim, a, b in shown_cross:
        items.append({
            "id": f"missed:{a['id']}:{b['id']}", "category": "2.3", "subject": f"{a['canonical']} <-> {b['canonical']}",
            "context": {
                "similarite": round(sim, 3),
                "concept_a": {"nom": a["canonical"], "type": a["type"], "sens": a["sense"], "livres": sorted(short_doc(d) for d in docs_of_concept.get(a["id"], ())),
                              "extraits": ev(a)},
                "concept_b": {"nom": b["canonical"], "type": b["type"], "sens": b["sense"], "livres": sorted(short_doc(d) for d in docs_of_concept.get(b["id"], ())),
                              "extraits": ev(b)},
            },
            "confirmation": full(a, b),
        })

    out += ["", f"### 2.4 Doublons probables au sein d'un même livre (similarité >= {args.intra_threshold})", ""]
    shown_same = same[:args.max_pairs]
    out.append(f"{len(same)} paire(s) ; {len(shown_same)} listée(s).")
    out.append("")
    out += table(["Sim.", "Concept A", "Concept B", "Livres communs", "Verdict"],
                 [[f"{sim:.3f}", a["canonical"], b["canonical"],
                   ", ".join(sorted(short_doc(d) for d in docs_of_concept.get(a["id"], set()) & docs_of_concept.get(b["id"], set()))), ""]
                  for sim, a, b in shown_same])
    for sim, a, b in shown_same:
        items.append({
            "id": f"dup:{a['id']}:{b['id']}", "category": "2.4", "subject": f"{a['canonical']} <-> {b['canonical']}",
            "context": {
                "similarite": round(sim, 3),
                "concept_a": {"nom": a["canonical"], "type": a["type"], "sens": a["sense"], "extraits": ev(a)},
                "concept_b": {"nom": b["canonical"], "type": b["type"], "sens": b["sense"], "extraits": ev(b)},
            },
            "confirmation": full(a, b),
        })

    out += ["", "### 2.5 Alias portés par plusieurs concepts distincts", ""]
    owners: dict[str, list[dict]] = defaultdict(list)
    for c in concepts.values():
        for form in {_norm(a) for a in c["aliases"]} | {_norm(c["canonical"])}:
            owners[form].append(c)
    collisions = sorted(((form, cs) for form, cs in owners.items() if len(cs) >= 2), key=lambda t: t[0])
    shown_collisions = collisions[:args.max_pairs]
    out.append(f"{len(collisions)} forme(s) de surface apparaissent dans plusieurs concepts : un même mot résolu vers deux entités "
               f"signale une résolution incohérente (l'un des deux est probablement faux).")
    out.append("")
    out += table(["Forme", "Concepts qui la portent", "Verdict"],
                 [[form, " / ".join(f"{c['canonical']} ({c['type']})" for c in cs), ""] for form, cs in shown_collisions])
    if collisions:
        signals.append(f"{len(collisions)} forme(s) de surface portée(s) par plusieurs concepts (2.5)")
    for form, cs in shown_collisions:
        items.append({
            "id": f"collision:{form}", "category": "2.5", "subject": form,
            "context": {"concepts": [
                {"nom": c["canonical"], "type": c["type"], "sens": c["sense"], "extraits": ev(c)}
                for c in cs
            ]},
            "confirmation": full(*cs[:6]),  # borne la taille : une forme comme "biais" est portée par 9 concepts
        })
    return out, signals, items


def cap_items(items: list[dict], max_items: int | None) -> tuple[list[dict], int]:
    """Plafonne le nombre TOTAL d'items d'audit, toutes categories confondues
    — independant de --max-shared/--sample/--max-pairs, qui ne bornent que
    l'affichage (et donc les items) DE CHAQUE section prise separement. Sans
    ca, un corpus a beaucoup de livres accumule des items sans plafond global
    (deja 199-205 sur 5 livres), donc sans limite sur le cout total en appels
    `claude -p`. Retire, categorie par categorie en commencant par la plus
    grande a chaque fois (repartition equilibree plutot que de vider une
    seule categorie), les items les MOINS prioritaires (derniers de leur
    categorie : chaque categorie est deja triee par pertinence avant appel).
    Retourne (items gardes, nombre retire)."""
    if max_items is None or len(items) <= max_items:
        return items, 0
    counts = Counter(it["category"] for it in items)
    while sum(counts.values()) > max_items:
        biggest = max(counts, key=counts.get)
        counts[biggest] -= 1
    remaining = dict(counts)
    kept = []
    for it in items:
        cat = it["category"]
        if remaining.get(cat, 0) > 0:
            kept.append(it)
            remaining[cat] -= 1
    return kept, len(items) - len(kept)


# ---------------------------------------------------------------------------

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # console Windows cp1252
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-path", type=Path, default=paths.GRAPH_DB_PATH)
    parser.add_argument("--out", type=Path, default=None, help="écrit le rapport (Markdown, UTF-8) + un .json à côté (défaut rag_data/audit/rapport_graphe_<AAAAMMJJ_HHMMSS>.md, nom unique à chaque lancement)")
    parser.add_argument("--sample", type=int, default=25, help="taille des échantillons d'audit de 2.2 (défaut 25)")
    parser.add_argument("--seed", type=int, default=0, help="graine du tirage aléatoire (défaut 0, reproductible)")
    parser.add_argument("--max-shared", type=int, default=60, help="concepts partagés listés en 2.1 (défaut 60)")
    parser.add_argument("--max-pairs", type=int, default=40, help="paires listées en 2.3 et 2.4 (défaut 40)")
    parser.add_argument("--near-threshold", type=float, default=None, help="seuil de 2.3 (défaut : seuil d'arbitrage du pipeline)")
    parser.add_argument("--intra-threshold", type=float, default=0.88, help="seuil de 2.4 (défaut 0.88)")
    parser.add_argument("--hub-share", type=float, default=0.10, help="part de chunks d'un livre à partir de laquelle un concept est un hub (défaut 0.10)")
    parser.add_argument("--history", type=Path, default=paths.AUDIT_DIR / "historique.json", help="fichier d'historique pour le delta entre rapports (défaut rag_data/audit/historique.json)")
    parser.add_argument("--no-history", action="store_true", help="ne pas lire ni écrire le fichier d'historique")
    parser.add_argument("--max-items", type=int, default=300, help="plafond TOTAL d'items d'audit toutes catégories confondues, indépendant de --max-shared/--sample/--max-pairs (défaut 300, 0 = illimité)")
    args = parser.parse_args()
    paths.AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    if args.out is None:
        args.out = paths.AUDIT_DIR / f"rapport_graphe_{datetime.now():%Y%m%d_%H%M%S}.md"
    json_out = args.out.with_suffix(".json")

    merge_threshold, ambiguous_threshold = _resolution_thresholds()
    if args.near_threshold is None:
        args.near_threshold = ambiguous_threshold

    if not args.db_path.exists():
        print(f"ERREUR: graphe introuvable : {args.db_path}", file=sys.stderr)
        return 1
    try:
        graph = load_graph(args.db_path)
    except Exception as exc:  # base verrouillée par un autre processus, format inattendu...
        print(f"ERREUR: ouverture en lecture seule impossible ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 1
    if not graph["concepts"]:
        print("Le graphe est vide : rien à évaluer.")
        return 0

    docs = sorted({c["doc"] for c in graph["chunks"].values()})
    head = [
        "# Rapport de qualité du graphe de concepts", "",
        f"Graphe : `{args.db_path}` (lecture seule). {len(docs)} livre(s) : " + ", ".join(f"`{d}`" for d in docs) + ".",
        f"{len(graph['concepts'])} concepts, {len(graph['chunks'])} chunks, {len(graph['mentions'])} liens.", "",
    ]
    if len(docs) < 2:
        head += ["> Un seul livre dans le graphe : les sections inter-livres (2.1, 2.3) seront vides.", ""]

    current_metrics = compute_metrics(graph, args, merge_threshold, ambiguous_threshold, docs)
    history = [] if args.no_history else load_history(args.history)
    delta = render_delta_section(history, current_metrics)
    bilan = render_per_book_section(current_metrics)

    p1, s1, items1 = part1(graph, args.hub_share)
    p2, s2, items2 = part2(graph, args, merge_threshold, ambiguous_threshold)
    signals = s1 + s2
    max_items = args.max_items if args.max_items > 0 else None
    # Hubs (1.3) : métrique du rapport seulement, plus jugés par le LLM.
    items1 = [it for it in items1 if it["category"] not in decisions.NOT_AUDITED]
    # Registre des décisions : seuls les items nouveaux ou dont le contexte a changé
    # sont à juger ; les signalements encore ouverts sont listés à part.
    registry = decisions.load()
    to_judge, open_flags, n_settled_ok = decisions.split_items(items1 + items2, registry)
    audit_items, n_capped = cap_items(to_judge, max_items)
    if n_capped:
        signals.append(
            f"{n_capped} item(s) d'audit retiré(s) par --max-items ({args.max_items}) : "
            f"{len(audit_items)}/{len(to_judge)} conservé(s), répartis équitablement entre catégories"
        )
    summary = ["## Signaux à examiner", ""] + ([f"- {s}" for s in signals] if signals else ["- Aucun signal automatique. Les audits de la partie 2 restent à faire."])
    audit_state = [
        "## État de l'audit", "",
        f"{len(audit_items)} item(s) à juger (nouveaux ou au contexte modifié), {n_settled_ok} déjà validé(s) et non rejugé(s), "
        f"{len(open_flags)} signalement(s) encore ouvert(s). Hubs (1.3) : métrique seulement, non jugés.", "",
    ]
    if open_flags:
        audit_state += ["Signalements ouverts (déjà jugés, à corriger dans le graphe, ou à écarter avec `audit_accept.py <id>`) :", ""]
        audit_state += table(["Id", "Catégorie", "Sujet", "Verdict", "Raison"],
                             [[f["id"], f["category"], f["subject"], f["verdict"], f["reason"]] for f in open_flags])
        audit_state.append("")
    report = "\n".join(head + delta + [""] + bilan + [""] + summary + [""] + audit_state + p1 + [""] + p2) + "\n"

    print(report)
    args.out.write_text(report, encoding="utf-8")
    print(f"(rapport écrit dans {args.out})", file=sys.stderr)

    json_out.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "db_path": str(args.db_path),
        "docs": docs,
        "merge_threshold": merge_threshold, "ambiguous_threshold": ambiguous_threshold,
        "current_metrics": current_metrics,
        "audit_items": audit_items,
        "open_flags": open_flags,
        "n_settled_ok": n_settled_ok,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    cap_note = f" (plafonné par --max-items {args.max_items}, {n_capped} retiré(s))" if n_capped else ""
    print(f"(données d'audit écrites dans {json_out} — {len(audit_items)} item(s) pour audit_lot.py{cap_note})", file=sys.stderr)

    if not args.no_history:
        history.append({"date": datetime.now(timezone.utc).isoformat(timespec="seconds"), **current_metrics})
        save_history(args.history, history)
        print(f"(historique mis à jour dans {args.history})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
