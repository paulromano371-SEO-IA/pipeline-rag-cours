"""Recuperation complete pour une requete donnee : combine recherche
vectorielle (`vector_store`), expansion par section (`heading_trail`) et
expansion par concept (`graph_store`), pour rassembler tout le contenu
pertinent (texte, formule, image, code) plutot qu'un simple top-k de chunks
isoles -- voir `tools/analyze_retrieval_coverage.py` pour la mesure
empirique qui a motive ce design : une recherche par similarite seule ne
garantit pas de retrouver, pour une section donnee, sa formule/image
associee (souvent hors du top-5 par defaut) ni son exemple de code (dans
une section SOEUR distincte, jamais dans le meme `heading_trail`).

Principe de l'expansion par concept (l'etape la plus delicate) : ne JAMAIS
juger un concept lui-meme -- ni par son nombre de mentions dans le corpus,
ni par la similarite de son propre embedding a la requete, les deux se sont
reveles peu fiables empiriquement (un concept tres cite n'est pas forcement
pertinent ; un concept tres specifique et utile peut avoir une similarite
textuelle faible a la requete si celle-ci ne le cite pas mot pour mot). A la
place, le graphe sert de simple GENERATEUR DE CANDIDATS (des chunks qu'une
recherche vectorielle seule n'aurait pas surfaces, parce que leur
formulation de surface differe trop de la requete -- ex. du code Python vs
une question en francais) ; chaque candidat est ensuite juge
INDIVIDUELLEMENT avec la MEME mesure de pertinence qu'a l'etape 1
(similarite cosinus a la requete), jamais un critere different.

Le score utilise pour ce filtre est TOUJOURS recalcule ici (jamais la
"distance" renvoyee par Chroma) : la collection n'a pas d'espace de
distance explicitement configure (`metadata: None`, verifie empiriquement),
donc rien ne garantit qu'elle mesure une similarite cosinus -- recalculer
soi-meme, de facon identique pour tous les candidats (recherche, section,
concept), est la seule facon de garantir une echelle de comparaison
coherente dans tout ce module.

Filtre anti-faux-ancrage : un chunk structurel (table des matieres,
sommaire...) peut obtenir un bon score de similarite par pur recouvrement de
mots (verifie empiriquement sur "Table des matieres", qui contient
litteralement les mots de la requete) sans porter de contenu reel -- ecarte
systematiquement via son fil d'ariane, independamment de tout score.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from graph_store import ConceptGraph, cosine_similarities
from vector_store import DEFAULT_COLLECTION_NAME, DEFAULT_MODEL_NAME, embed_texts, get_collection

import paths

DEFAULT_SEARCH_TOP_K = 15
DEFAULT_MAX_SECTIONS = 3
DEFAULT_RESULT_CAP = 30
# Calibre empiriquement (voir la conversation de conception) : a 0.15, le
# meilleur candidat non-structurel d'un cas reel testait a 0.556 contre un
# plancher a 0.564 -- ratait de justesse un chunk clairement pertinent
# (section SVM faisant explicitement le lien avec la regression logistique).
# A 0.20, ce candidat et 6 autres du meme calibre passent ; au-dela, le
# plafond `DEFAULT_RESULT_CAP` est deja atteint sans rien ramener de plus --
# point d'arret naturel, pas un choix arbitraire.
DEFAULT_RELEVANCE_MARGIN = 0.20

# Titres de sections structurelles, sans contenu reel, a ne jamais retenir
# comme resultat -- comparaison apres normalisation (accents/casse ignores).
_STRUCTURAL_HEADINGS = {
    "table des matieres", "sommaire", "index", "bibliographie",
    "annexe", "annexes", "glossaire", "table of contents", "contents",
    "references", "remerciements",
}


def _normalize(text: str) -> str:
    stripped = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return stripped.lower().strip()


def _is_structural(heading_trail: list[str]) -> bool:
    return any(_normalize(h) in _STRUCTURAL_HEADINGS for h in heading_trail)


@dataclass
class RetrievedChunk:
    document_id: str
    chunk_index: int
    text: str
    heading_trail: list[str]
    has_code: bool
    has_formula: bool
    has_image: bool
    score: float | None  # None pour un chunk inclus par expansion de section (pas de filtre de pertinence individuel, voir docstring du module)
    reason: str  # "search" | "section" | "concept:<canonical_form>"


# Cache par document_id -- un module de recuperation repond a plusieurs
# requetes successives (voir la discussion sur un modele garde en memoire) ;
# eviter de relire chunks.json a chaque appel pour un document deja vu.
_chunks_cache: dict[str, dict[int, dict]] = {}


def _load_chunks(document_id: str) -> dict[int, dict]:
    if document_id not in _chunks_cache:
        work_dir = paths.work_dir_for(document_id)
        raw = json.loads((work_dir / "chunks.json").read_text(encoding="utf-8"))
        _chunks_cache[document_id] = {c["index"]: c for c in raw}
    return _chunks_cache[document_id]


def _to_retrieved(document_id: str, chunk_index: int, score: float | None, reason: str) -> RetrievedChunk | None:
    raw = _load_chunks(document_id).get(chunk_index)
    if raw is None:
        return None
    return RetrievedChunk(
        document_id=document_id,
        chunk_index=chunk_index,
        text=raw["text"],
        heading_trail=raw["heading_trail"],
        has_code=bool(raw.get("has_code")),
        has_formula=bool(raw.get("has_formula")),
        has_image="![" in raw["text"],
        score=score,
        reason=reason,
    )


def retrieve(
    query: str,
    *,
    db_path: Path,
    graph_db_path: Path,
    collection_name: str = DEFAULT_COLLECTION_NAME,
    model_name: str = DEFAULT_MODEL_NAME,
    search_top_k: int = DEFAULT_SEARCH_TOP_K,
    max_sections: int = DEFAULT_MAX_SECTIONS,
    result_cap: int = DEFAULT_RESULT_CAP,
    relevance_margin: float = DEFAULT_RELEVANCE_MARGIN,
) -> list[RetrievedChunk]:
    """Recupere un ensemble complet de chunks pertinents pour `query` --
    texte, formule, image et code, potentiellement de plusieurs documents --
    en combinant recherche vectorielle, expansion par section et expansion
    par concept (voir docstring du module).

    `result_cap` est un plafond de securite sur la TAILLE TOTALE du resultat
    (pas un seuil par concept) : les chunks de recherche/section sont
    toujours inclus tels quels (leur pertinence a deja ete etablie par leur
    position dans le top-k ou leur appartenance a une section retenue) ;
    seuls les chunks amenes par expansion de concept sont individuellement
    filtres par pertinence (`relevance_margin` sous le meilleur score de
    recherche) puis tronques pour respecter ce plafond."""
    query_embedding = embed_texts([query], model_name=model_name)[0]
    collection = get_collection(db_path, collection_name)

    raw_results = collection.query(
        query_embeddings=[query_embedding],
        n_results=search_top_k,
        include=["metadatas", "embeddings"],
    )
    metadatas = raw_results["metadatas"][0]
    embeddings = raw_results["embeddings"][0]

    included: dict[tuple[str, int], RetrievedChunk] = {}
    seed_sections: list[tuple[str, str]] = []  # (document_id, heading_trail), dans l'ordre de pertinence

    search_scores = cosine_similarities(query_embedding, list(embeddings)) if len(embeddings) else []
    for meta, score in zip(metadatas, search_scores):
        doc_id = meta["document_id"]
        idx = meta["chunk_index"]
        trail = meta["heading_trail"]
        retrieved = _to_retrieved(doc_id, idx, score, "search")
        if retrieved is None or _is_structural(retrieved.heading_trail):
            continue
        key = (doc_id, idx)
        if key not in included:
            included[key] = retrieved
        section_key = (doc_id, trail)
        if section_key not in seed_sections and len(seed_sections) < max_sections:
            seed_sections.append(section_key)

    top_search_score = max((c.score for c in included.values() if c.score is not None), default=None)

    # --- expansion par section : tous les chunks des sections retenues ---
    for doc_id, trail in seed_sections:
        for raw_chunk in _load_chunks(doc_id).values():
            if " > ".join(raw_chunk["heading_trail"]) != trail or _is_structural(raw_chunk["heading_trail"]):
                continue
            key = (doc_id, raw_chunk["index"])
            if key not in included:
                included[key] = _to_retrieved(doc_id, raw_chunk["index"], None, "section")

    # --- expansion par concept : candidats generes par le graphe, juges individuellement ---
    graph = ConceptGraph(graph_db_path)
    seen_concept_ids: set[str] = set()
    candidate_reason: dict[tuple[str, int], str] = {}

    for doc_id, trail in seed_sections:
        for raw_chunk in _load_chunks(doc_id).values():
            if " > ".join(raw_chunk["heading_trail"]) != trail:
                continue
            chunk_id = f"{doc_id}::{raw_chunk['index']}"
            for concept in graph.concepts_of_chunk(chunk_id):
                if concept.id in seen_concept_ids:
                    continue
                seen_concept_ids.add(concept.id)
                for _mention_chunk_id, mention_doc_id, mention_idx in graph.mentions_of(concept.id):
                    key = (mention_doc_id, mention_idx)
                    if key in included or key in candidate_reason:
                        continue
                    candidate_reason[key] = f"concept:{concept.canonical_form}"

    if candidate_reason and top_search_score is not None:
        candidate_keys = list(candidate_reason)
        ids = [f"{doc}::{idx}" for doc, idx in candidate_keys]
        fetched = collection.get(ids=ids, include=["embeddings"])
        id_to_embedding = dict(zip(fetched["ids"], fetched["embeddings"]))

        scored: list[tuple[tuple[str, int], float]] = []
        for key in candidate_keys:
            doc_id, idx = key
            emb = id_to_embedding.get(f"{doc_id}::{idx}")
            raw_chunk = _load_chunks(doc_id).get(idx)
            if emb is None or raw_chunk is None or _is_structural(raw_chunk["heading_trail"]):
                continue
            scored.append((key, cosine_similarities(query_embedding, [emb])[0]))

        # Tries par score decroissant : on s'arrete des qu'on tombe sous le
        # plancher de pertinence (relatif au meilleur score de recherche,
        # pas un seuil absolu arbitraire) OU des qu'on atteint le plafond --
        # les candidats restants, moins bons, sont abandonnes.
        scored.sort(key=lambda kv: -kv[1])
        floor = top_search_score - relevance_margin
        for key, sim in scored:
            if sim < floor:
                break
            if len(included) >= result_cap:
                break
            doc_id, idx = key
            included[key] = _to_retrieved(doc_id, idx, sim, candidate_reason[key])

    return list(included.values())[:result_cap]
