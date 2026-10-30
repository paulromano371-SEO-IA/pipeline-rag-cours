"""Étape 1, pour UN lot : décider les mentions en parallèle, puis écrire dans Kuzu depuis le parent.

1. `decider_lot` : un thread par mention, lecture seule (voir lib/resolution.py `decider`).
2. `ecrire_lot` : le processus parent, seul, applique les décisions dans l'ordre des mentions.
   Il est le seul à écrire dans Kuzu. Les nouveaux concepts du lot sont comparés entre eux avant
   création : au-delà de MERGE_THRESHOLD, le second devient un alias du premier.

Un arrêt d'intégrité (`ArretIntegrite`) remonte à l'appelant, qui le transforme en code 2 (BLOQUANT).
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))  # scripts/ : commun.py

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import commun  # noqa: F401  (ajoute _rag_lib au chemin d'import)
from concepts import ConceptMention
from graph_store import ConceptGraph, ConceptRecord, GraphIntegrityError, cosine_similarities
from resolution import MERGE_THRESHOLD, Decision, decider
import status as status_lib


class ArretIntegrite(RuntimeError):
    """Incohérence entre Kuzu et ce que le programme y a écrit : arrêt immédiat."""

    def __init__(self, where: str, lines: list[str]):
        super().__init__(where)
        self.where = where
        self.lines = lines


def arret_integrite(work_dir: Path, where: str, lines: list[str]) -> int:
    """Affiche l'écart, marque l'étape `failed` (BLOQUANT) et renvoie le code 2.

    Le pipeline ne doit jamais enchaîner sur une base dont les alias ne sont plus fiables."""
    print("=== INTEGRITE DU GRAPHE : ECART DETECTE — arret de /rag-graphe ===", file=sys.stderr)
    print(f"Moment : {where}", file=sys.stderr)
    for line in lines:
        print(f"  - {line}", file=sys.stderr)
    print(
        "Le graphe contient deja l'ecart : relancer sans le retirer le laisserait en place. "
        "Reconstruire ce livre avec `--reset --batch-size 10` (ou `--clear-graph` si un autre livre est touche).",
        file=sys.stderr,
    )
    status_lib.mark_stage(
        work_dir, "graphe", "failed", detail=f"integrite du graphe : ecart detecte ({where})",
        verdict="bloquant", verdict_reasons=[f"integrite du graphe : {where}"],
    )
    return 2


def charger_textes_chunks(work_dir: Path) -> dict[int, str]:
    """Texte des chunks, transmis à l'arbitrage comme extrait de contexte."""
    chunks_path = work_dir / "chunks.json"
    if not chunks_path.exists():
        return {}
    return {c["index"]: c["text"] for c in json.loads(chunks_path.read_text(encoding="utf-8"))}


def decider_lot(
    entries: list[dict],
    document_id: str,
    chunk_texts: dict[int, str],
    known_concepts: list[ConceptRecord],
    embeddings: list[list[float]],
) -> list[tuple[int, Decision]]:
    """Décide chaque mention du lot, un thread par mention. Les threads lisent `known_concepts` sans le modifier.

    Retourne [(chunk_index, Decision)] dans l'ordre des chunks puis des mentions."""
    taches = []
    embedding_iter = iter(embeddings)
    for entry in entries:
        idx = entry["chunk_index"]
        for m in entry["mentions"]:
            mention = ConceptMention(name=m["name"], canonical_form=m["canonical_form"], type=m["type"], sense=m.get("sense", ""))
            taches.append((idx, mention, next(embedding_iter)))
    if not taches:
        return []

    with ThreadPoolExecutor(max_workers=len(taches)) as pool:
        futurs = [
            pool.submit(decider, mention, emb, known_concepts, document_id, chunk_texts.get(idx, ""))
            for idx, mention, emb in taches
        ]
        return [(idx, futur.result()) for (idx, _, _), futur in zip(taches, futurs)]


def _nouveau_concept(nouveaux: list[dict], d: Decision) -> dict:
    """Concept planifié pour une mention « nouveau » : soit un concept déjà planifié par une mention
    précédente du lot (similarité >= MERGE_THRESHOLD, alias ajouté), soit un nouveau concept planifié."""
    forme = d.mention.canonical_form
    if nouveaux:
        sims = cosine_similarities(d.embedding, [n["embedding"] for n in nouveaux])
        best = max(range(len(nouveaux)), key=lambda i: sims[i])
        if sims[best] >= MERGE_THRESHOLD:
            if forme not in nouveaux[best]["aliases"]:
                nouveaux[best]["aliases"].append(forme)
            return nouveaux[best]
    planifie = {
        "form": forme, "type": d.mention.type, "sense": d.mention.sense,
        "embedding": d.embedding, "aliases": [forme], "id": None,
    }
    nouveaux.append(planifie)
    return planifie


def ecrire_lot(
    graph: ConceptGraph,
    document_id: str,
    entries: list[dict],
    decisions: list[tuple[int, Decision]],
    known_concepts: list[ConceptRecord],
) -> dict[int, tuple[int, int]]:
    """Applique les décisions du lot. Seul écrivain Kuzu. Retourne {chunk_index: (mentions liées, mentions ignorées)}.

    Ordre : chunks, nouveaux concepts, alias sur concepts existants, liens. La mémoire `known_concepts`
    est mise à jour à la fin pour que le contrôle d'intégrité post-lot compare base et mémoire."""
    compteurs: dict[int, list[int]] = {e["chunk_index"]: [0, 0] for e in entries}
    par_id = {c.id: c for c in known_concepts}
    nouveaux: list[dict] = []
    alias_existants: list[tuple[str, str]] = []
    fusions_existantes: set[str] = set()
    liens: list[tuple[int, str | dict]] = []

    for chunk_index, d in decisions:
        forme = d.mention.canonical_form
        if d.kind == "ignoree":
            print(
                f"  chunk {chunk_index}: mention {forme!r} ignoree (arbitrage claude -p echoue : {d.erreur})",
                file=sys.stderr,
            )
            compteurs[chunk_index][1] += 1
            continue
        if d.kind == "fusion":
            cible: str | dict = d.concept_id
            fusions_existantes.add(d.concept_id)
            if forme not in par_id[d.concept_id].aliases and (d.concept_id, forme) not in alias_existants:
                alias_existants.append((d.concept_id, forme))
        else:
            cible = _nouveau_concept(nouveaux, d)
        liens.append((chunk_index, cible))
        compteurs[chunk_index][0] += 1

    try:
        for entry in entries:
            idx = entry["chunk_index"]
            graph.add_chunk(f"{document_id}::{idx}", document_id, idx)
        for n in nouveaux:
            n["id"] = graph.create_concept(n["form"], n["type"], n["form"], n["embedding"], sense=n["sense"])
            for alias in n["aliases"][1:]:
                graph.add_alias(n["id"], alias)
        for concept_id, forme in alias_existants:
            graph.add_alias(concept_id, forme)
        for chunk_index, cible in liens:
            concept_id = cible["id"] if isinstance(cible, dict) else cible
            graph.link_mention(f"{document_id}::{chunk_index}", concept_id)
    except GraphIntegrityError as exc:
        raise ArretIntegrite("pendant l'ecriture du lot", str(exc).splitlines()) from exc

    # Mémoire : même état que la base après les écritures.
    for n in nouveaux:
        known_concepts.append(ConceptRecord(
            id=n["id"], canonical_form=n["form"], type=n["type"], aliases=list(n["aliases"]),
            embedding=n["embedding"], sense=n["sense"], documents={document_id},
        ))
    for concept_id, forme in alias_existants:
        par_id[concept_id].aliases.append(forme)
    for concept_id in fusions_existantes:
        par_id[concept_id].documents.add(document_id)

    return {idx: (v[0], v[1]) for idx, v in compteurs.items()}
