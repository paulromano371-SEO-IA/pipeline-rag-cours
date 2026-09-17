"""Embedding et indexation vectorielle locale.

Modèle d'embedding : `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
via fastembed (ONNX, ~220 Mo, local, pas de clé API) — multilingue, ce qui
compte puisque le corpus est en français mais les concepts techniques
restent parfois mélangés à de l'anglais. Choisi comme compromis léger plutôt
que des modèles plus lourds (multilingual-e5-large, bge-m3) : à réévaluer si
la qualité de retrieval s'avère insuffisante en pratique.

Stockage : Chroma en mode embarqué (`PersistentClient`), un simple dossier de
fichiers locaux — pas de serveur à faire tourner.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import chromadb
from fastembed import TextEmbedding

from chunk import Chunk

DEFAULT_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_COLLECTION_NAME = "documents"

_model_cache: dict[str, TextEmbedding] = {}


def _get_model(model_name: str = DEFAULT_MODEL_NAME) -> TextEmbedding:
    if model_name not in _model_cache:
        _model_cache[model_name] = TextEmbedding(model_name=model_name)
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
    return [vec.tolist() for vec in model.embed(texts)]


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
    query_embedding = next(iter(model.embed([query]))).tolist()
    collection = get_collection(db_path, collection_name)
    return collection.query(query_embeddings=[query_embedding], n_results=top_k)
