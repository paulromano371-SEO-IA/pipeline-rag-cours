"""Embedding et indexation vectorielle locale.

Modèle d'embedding : `BAAI/bge-m3` via `sentence-transformers` (local, pas de
clé API) — multilingue, fenetre native de 8192 tokens. Remplace
`paraphrase-multilingual-MiniLM-L12-v2` (128 tokens reels) utilise
initialement via `fastembed` : mesure empirique sur le corpus reel (voir
`tools/analyze_embedder_candidates.py`) montrant que ce dernier tronquait
silencieusement ~98% des chunks assembles par `/rag-chunking`, contre 0%
avec bge-m3 aux memes tailles de chunk. `fastembed` ne propose pas de
version ONNX de bge-m3 (liste fermee de modeles) ; `sentence-transformers`
tourne sur `torch`/`transformers`, deja presents dans ce venv pour
`pix2tex` (OCR de formules) — pas de nouvelle dependance lourde.

Stockage : Chroma en mode embarqué (`PersistentClient`), un simple dossier de
fichiers locaux — pas de serveur à faire tourner.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import chromadb
from sentence_transformers import SentenceTransformer

from chunk import Chunk

DEFAULT_MODEL_NAME = "BAAI/bge-m3"
DEFAULT_COLLECTION_NAME = "documents"

_model_cache: dict[str, SentenceTransformer] = {}


def _get_model(model_name: str = DEFAULT_MODEL_NAME) -> SentenceTransformer:
    if model_name not in _model_cache:
        _model_cache[model_name] = SentenceTransformer(model_name)
    return _model_cache[model_name]


def embedding_text(chunk: Chunk) -> str:
    """Texte réellement embeddé : préfixé du fil d'ariane des titres, pour
    donner du contexte à un extrait qui, isolé, serait sinon ambigu.

    Utilise `chunk.embed_text` plutôt que `chunk.text` quand il est présent :
    pour un bloc code/image/formule enrichi par `/rag-nottext`, ce champ ne
    contient QUE sa description en langage naturel, jamais le verbatim
    (LaTeX/code/légende d'image) — mélanger les deux dilue la similarité
    d'embedding avec une question en français (vérifié empiriquement, voir
    `chunk.py`). Le contenu verbatim reste intact dans `chunk.text`, stocké
    et restitué tel quel à la citation — seul le texte embeddé change."""
    body = chunk.embed_text or chunk.text
    if chunk.heading_trail:
        breadcrumb = " > ".join(chunk.heading_trail)
        return f"{breadcrumb}\n\n{body}"
    return body


def embed_texts(texts: list[str], *, model_name: str = DEFAULT_MODEL_NAME) -> list[list[float]]:
    """Embedde une liste de textes bruts avec le même modèle que les chunks —
    réutilisé par la résolution d'entités pour embedder les formes
    canoniques de concepts, sans dupliquer le chargement du modèle."""
    if not texts:
        return []
    model = _get_model(model_name)
    return model.encode(texts, convert_to_numpy=True).tolist()


def embed_chunks(chunks: list[Chunk], *, model_name: str = DEFAULT_MODEL_NAME) -> list[list[float]]:
    if not chunks:
        return []
    return embed_texts([embedding_text(c) for c in chunks], model_name=model_name)


@dataclass
class DocumentMetadata:
    document_id: str
    source_path: str


def get_collection(db_path: Path, collection_name: str = DEFAULT_COLLECTION_NAME):
    client = chromadb.PersistentClient(path=str(db_path))
    return client.get_or_create_collection(collection_name)


def index_chunks(
    chunks: list[Chunk],
    document: DocumentMetadata,
    *,
    db_path: Path,
    collection_name: str = DEFAULT_COLLECTION_NAME,
    model_name: str = DEFAULT_MODEL_NAME,
) -> int:
    """Embedde et indexe `chunks` dans la collection Chroma persistée sous
    `db_path`. Un ré-import du même document (même `document_id`) remplace
    entièrement ses chunks existants : les anciennes entrées de ce document
    sont d'abord supprimées, puis les nouvelles insérées — un simple
    `upsert` par id ne retire jamais les ids qui ne sont plus fournis, ce qui
    laisserait des chunks périmés dans la base si le document est réindexé
    avec moins de chunks qu'auparavant (vérifié empiriquement)."""
    collection = get_collection(db_path, collection_name)

    existing = collection.get(where={"document_id": document.document_id})
    if existing["ids"]:
        collection.delete(ids=existing["ids"])

    if not chunks:
        return 0

    embeddings = embed_chunks(chunks, model_name=model_name)

    ids = [f"{document.document_id}::{c.index}" for c in chunks]
    metadatas = [
        {
            "document_id": document.document_id,
            "source_path": document.source_path,
            "chunk_index": c.index,
            "heading_trail": " > ".join(c.heading_trail),
            "token_count": c.token_count,
            "has_code": c.has_code,
        }
        for c in chunks
    ]
    documents = [c.text for c in chunks]

    collection.upsert(ids=ids, embeddings=embeddings, metadatas=metadatas, documents=documents)
    return len(chunks)


def search(
    query: str,
    *,
    db_path: Path,
    collection_name: str = DEFAULT_COLLECTION_NAME,
    model_name: str = DEFAULT_MODEL_NAME,
    top_k: int = 5,
):
    model = _get_model(model_name)
    query_embedding = model.encode([query], convert_to_numpy=True)[0].tolist()
    collection = get_collection(db_path, collection_name)
    return collection.query(query_embeddings=[query_embedding], n_results=top_k)
