"""Graphe de concepts : stockage Kuzu embarqué.

Nœuds `Concept` (forme canonique, type, alias, embedding) reliés aux `Chunk`
qui les mentionnent (`MENTIONS`). Ce module se limite au stockage et aux
requêtes de similarité brute-force sur les embeddings ; la logique de
résolution (fusion vs nouveau concept, arbitrage LLM) vit dans
`entity_resolution.py`.

Kuzu plutôt qu'un serveur de graphe : embarqué (un simple dossier de fichiers
locaux), cohérent avec Chroma pour le vecteur.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from pathlib import Path

import kuzu

_SCHEMA_STATEMENTS = [
    "CREATE NODE TABLE Concept(id STRING, canonical_form STRING, type STRING, "
    "aliases STRING[], embedding DOUBLE[], PRIMARY KEY(id))",
    "CREATE NODE TABLE Chunk(id STRING, document_id STRING, chunk_index INT64, PRIMARY KEY(id))",
    "CREATE REL TABLE MENTIONS(FROM Chunk TO Concept)",
]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


@dataclass
class ConceptRecord:
    id: str
    canonical_form: str
    type: str
    aliases: list[str]
    embedding: list[float]


class ConceptGraph:
    def __init__(self, db_path: Path):
        self._db = kuzu.Database(str(db_path))
        self._conn = kuzu.Connection(self._db)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        for statement in _SCHEMA_STATEMENTS:
            try:
                self._conn.execute(statement)
            except RuntimeError as exc:
                if "already exists" not in str(exc).lower():
                    raise

    def add_chunk(self, chunk_id: str, document_id: str, chunk_index: int) -> None:
        self._conn.execute(
            "MERGE (c:Chunk {id: $id}) ON CREATE SET c.document_id = $document_id, c.chunk_index = $chunk_index",
            {"id": chunk_id, "document_id": document_id, "chunk_index": chunk_index},
        )

    def create_concept(self, canonical_form: str, type_: str, alias: str, embedding: list[float]) -> str:
        concept_id = str(uuid.uuid4())
        self._conn.execute(
            "CREATE (c:Concept {id: $id, canonical_form: $canonical_form, type: $type, "
            "aliases: $aliases, embedding: $embedding})",
            {
                "id": concept_id,
                "canonical_form": canonical_form,
                "type": type_,
                "aliases": [alias],
                "embedding": embedding,
            },
        )
        return concept_id

    def add_alias(self, concept_id: str, alias: str) -> None:
        result = self._conn.execute("MATCH (c:Concept {id: $id}) RETURN c.aliases", {"id": concept_id})
        if not result.has_next():
            return
        aliases = result.get_next()[0]
        if alias not in aliases:
            self._conn.execute(
                "MATCH (c:Concept {id: $id}) SET c.aliases = $aliases",
                {"id": concept_id, "aliases": aliases + [alias]},
            )

    def link_mention(self, chunk_id: str, concept_id: str) -> None:
        exists = self._conn.execute(
            "MATCH (ch:Chunk {id: $chunk_id})-[:MENTIONS]->(c:Concept {id: $concept_id}) RETURN count(*)",
            {"chunk_id": chunk_id, "concept_id": concept_id},
        )
        if exists.has_next() and exists.get_next()[0] > 0:
            return
        self._conn.execute(
            "MATCH (ch:Chunk {id: $chunk_id}), (c:Concept {id: $concept_id}) CREATE (ch)-[:MENTIONS]->(c)",
            {"chunk_id": chunk_id, "concept_id": concept_id},
        )

    def all_concepts(self) -> list[ConceptRecord]:
        result = self._conn.execute(
            "MATCH (c:Concept) RETURN c.id, c.canonical_form, c.type, c.aliases, c.embedding"
        )
        records = []
        while result.has_next():
            row = result.get_next()
            records.append(ConceptRecord(id=row[0], canonical_form=row[1], type=row[2], aliases=row[3], embedding=row[4]))
        return records

    def get_concept(self, concept_id: str) -> ConceptRecord | None:
        result = self._conn.execute(
            "MATCH (c:Concept {id: $id}) RETURN c.id, c.canonical_form, c.type, c.aliases, c.embedding",
            {"id": concept_id},
        )
        if not result.has_next():
            return None
        row = result.get_next()
        return ConceptRecord(id=row[0], canonical_form=row[1], type=row[2], aliases=row[3], embedding=row[4])

    def mentions_of(self, concept_id: str) -> list[tuple[str, str, int]]:
        result = self._conn.execute(
            "MATCH (ch:Chunk)-[:MENTIONS]->(c:Concept {id: $id}) RETURN ch.id, ch.document_id, ch.chunk_index",
            {"id": concept_id},
        )
        rows = []
        while result.has_next():
            rows.append(tuple(result.get_next()))
        return rows

    def concepts_of_chunk(self, chunk_id: str) -> list[ConceptRecord]:
        result = self._conn.execute(
            "MATCH (ch:Chunk {id: $id})-[:MENTIONS]->(c:Concept) "
            "RETURN c.id, c.canonical_form, c.type, c.aliases, c.embedding",
            {"id": chunk_id},
        )
        records = []
        while result.has_next():
            row = result.get_next()
            records.append(ConceptRecord(id=row[0], canonical_form=row[1], type=row[2], aliases=row[3], embedding=row[4]))
        return records

    def shared_concepts(self, document_id_a: str, document_id_b: str) -> list[tuple[ConceptRecord, int, int]]:
        """Concepts mentionnés à la fois par des chunks de deux documents
        différents — le lien inter-sources qui fait la valeur du GraphRAG."""
        result = self._conn.execute(
            "MATCH (ca:Chunk {document_id: $doc_a})-[:MENTIONS]->(c:Concept)"
            "<-[:MENTIONS]-(cb:Chunk {document_id: $doc_b}) "
            "RETURN DISTINCT c.id, c.canonical_form, c.type, c.aliases, c.embedding",
            {"doc_a": document_id_a, "doc_b": document_id_b},
        )
        records = []
        while result.has_next():
            row = result.get_next()
            record = ConceptRecord(id=row[0], canonical_form=row[1], type=row[2], aliases=row[3], embedding=row[4])
            mentions = self.mentions_of(record.id)
            count_a = sum(1 for _, doc, _ in mentions if doc == document_id_a)
            count_b = sum(1 for _, doc, _ in mentions if doc == document_id_b)
            records.append((record, count_a, count_b))
        return records
