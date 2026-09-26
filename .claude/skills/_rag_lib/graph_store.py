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

import json
import math
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import kuzu
import numpy as np

_SCHEMA_STATEMENTS = [
    "CREATE NODE TABLE Concept(id STRING, canonical_form STRING, type STRING, "
    # `aliases` est une chaine JSON (liste de textes), PAS un STRING[] : sur Kuzu 0.11.3
    # les colonnes STRING[] relues apres reouverture remplacaient les alias des concepts
    # crees dans le lot precedent par ceux des tout premiers concepts (bug reproduit,
    # deterministe ; les colonnes STRING et DOUBLE[] n'etaient pas touchees).
    "aliases STRING, embedding DOUBLE[], sense STRING, PRIMARY KEY(id))",
    "CREATE NODE TABLE Chunk(id STRING, document_id STRING, chunk_index INT64, PRIMARY KEY(id))",
    "CREATE REL TABLE MENTIONS(FROM Chunk TO Concept)",
    # Base creee avant l'ajout de `sense` : ajoute la colonne (ignore si deja presente).
    "ALTER TABLE Concept ADD sense STRING DEFAULT ''",
]


def dump_aliases(aliases: list[str]) -> str:
    """Liste d'alias -> chaine JSON stockee dans la colonne `aliases` (STRING)."""
    return json.dumps(list(aliases), ensure_ascii=False)


def load_aliases(raw) -> list[str]:
    """Colonne `aliases` -> liste. Tolere une base a l'ancien schema (STRING[])."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        return list(json.loads(raw))
    return list(raw)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def cosine_similarities(query: list[float], candidates: list[list[float]]) -> list[float]:
    """Similarite cosinus de `query` contre TOUS les `candidates` en un seul
    calcul vectorise (numpy), dans le MEME ordre que `candidates` -- a
    utiliser a la place d'une boucle appelant `cosine_similarity` candidat
    par candidat des qu'il y en a plusieurs. Mesure empirique (voir
    `entity_resolution.resolve_concept`, le seul appelant) : 22x plus rapide
    que la boucle Python pure sur 500 candidats (126ms -> 5.8ms) -- la
    resolution d'entites compare chaque mention a TOUS les concepts deja
    connus du graphe, potentiellement des centaines, pour chacune des
    mentions d'un document (jusqu'a plusieurs milliers).

    Repli sur `cosine_similarity` candidat par candidat si les dimensions ne
    sont pas toutes identiques (ex. embeddings issus de modeles differents
    melanges dans le graphe) -- jamais un crash silencieux sur une
    incompatibilite de forme numpy."""
    if not candidates:
        return []
    dim = len(query)
    if any(len(c) != dim for c in candidates):
        return [cosine_similarity(query, c) for c in candidates]

    q = np.asarray(query, dtype=np.float64)
    q_norm = np.linalg.norm(q)
    if q_norm == 0:
        return [0.0] * len(candidates)

    C = np.asarray(candidates, dtype=np.float64)
    c_norms = np.linalg.norm(C, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        sims = (C @ q) / (c_norms * q_norm)
    return [0.0 if c_norms[i] == 0 or not np.isfinite(sims[i]) else float(sims[i]) for i in range(len(candidates))]


class GraphIntegrityError(RuntimeError):
    """Ce que Kuzu contient ne correspond pas a ce que le programme vient d'y
    ecrire (ou a l'etat qu'il tient en memoire). Arret immediat voulu : ne
    jamais continuer sur une base dont on ne peut plus se fier aux alias."""


@dataclass
class ConceptRecord:
    id: str
    canonical_form: str
    type: str
    aliases: list[str]
    embedding: list[float]
    # Definition courte du sens du concept (voir `concepts.ConceptMention.sense`)
    # et documents qui le mentionnent : renseignes par `all_concepts()` seulement,
    # utilises par la resolution d'entites pour ne pas fusionner deux homonymes.
    sense: str = ""
    documents: set[str] = field(default_factory=set)


class ConceptGraph:
    def __init__(self, db_path: Path):
        self._db = kuzu.Database(str(db_path))
        self._conn = kuzu.Connection(self._db)
        self._ensure_schema()

    def close(self) -> None:
        """Arret propre : force un CHECKPOINT (ecrit le journal dans les fichiers de
        la base) puis ferme la connexion et la base. Sans cela, un processus qui
        se termine laisse Kuzu fermer implicitement, et une reouverture a relu des
        listes d'alias remplacees par celles des premiers concepts (voir
        `check_integrity`). Idempotent ; une erreur est signalee, jamais avalee."""
        if getattr(self, "_closed", False):
            return
        self._closed = True
        try:
            self._conn.execute("CHECKPOINT")
            self._conn.close()
            self._db.close()
        except Exception as exc:  # signale : une fermeture ratee ne doit pas passer inapercue
            import sys
            print(f"ATTENTION : fermeture propre du graphe Kuzu echouee ({type(exc).__name__}: {exc})", file=sys.stderr)

    def _ensure_schema(self) -> None:
        for statement in _SCHEMA_STATEMENTS:
            try:
                self._conn.execute(statement)
            except RuntimeError as exc:
                if "already exists" not in str(exc).lower() and "already has property" not in str(exc).lower():
                    raise

    def clear(self) -> None:
        """Supprime tous les noeuds et relations (Concept, Chunk, MENTIONS),
        sans supprimer le schema lui-meme -- repart d'une base vide sans
        avoir a supprimer le dossier de fichiers Kuzu a la main (utile apres
        un changement du prompt d'extraction de concepts ou d'un seuil de
        resolution d'entites, pour ne pas melanger anciennes et nouvelles
        formes de concepts).

        N'agit QUE sur le graphe -- ne touche jamais `status.json` des
        documents qui y avaient deja contribue. Un document dont le stage
        "graphe" est marque "done" resterait donc a tort marque comme tel
        apres un `clear()` : a l'appelant de reinitialiser ce stage pour les
        documents concernes (voir `status.mark_stage` / suppression manuelle
        de l'entree "graphe" dans leur `status.json`) si une reconstruction
        complete est voulue."""
        self._conn.execute("MATCH ()-[r:MENTIONS]->() DELETE r")
        self._conn.execute("MATCH (c:Concept) DELETE c")
        self._conn.execute("MATCH (ch:Chunk) DELETE ch")

    def remove_document(self, document_id: str) -> None:
        """Retire la contribution d'UN SEUL document au graphe partage : ses
        `Chunk` et les relations `MENTIONS` depuis ces chunks, puis les
        `Concept` devenus orphelins (plus AUCUNE mention, d'aucun document).
        Un concept encore mentionne par un autre document est conserve tel
        quel -- c'est precisement la valeur du GraphRAG (voir
        `shared_concepts()`), donc jamais supprime tant qu'un autre document
        s'appuie dessus.

        Contrairement a `clear()`, n'affecte aucun autre document du corpus
        -- a utiliser pour reconstruire proprement un seul document (ex.
        apres un changement du prompt d'extraction de concepts) sans perdre
        le travail deja fait sur les autres."""
        result = self._conn.execute(
            "MATCH (ch:Chunk {document_id: $doc})-[:MENTIONS]->(c:Concept) RETURN DISTINCT c.id",
            {"doc": document_id},
        )
        candidate_concept_ids = []
        while result.has_next():
            candidate_concept_ids.append(result.get_next()[0])

        self._conn.execute(
            "MATCH (ch:Chunk {document_id: $doc})-[r:MENTIONS]->(:Concept) DELETE r",
            {"doc": document_id},
        )
        self._conn.execute("MATCH (ch:Chunk {document_id: $doc}) DELETE ch", {"doc": document_id})

        for concept_id in candidate_concept_ids:
            remaining = self._conn.execute(
                "MATCH (:Chunk)-[:MENTIONS]->(c:Concept {id: $id}) RETURN count(*)",
                {"id": concept_id},
            )
            count = remaining.get_next()[0] if remaining.has_next() else 0
            if count == 0:
                self._conn.execute("MATCH (c:Concept {id: $id}) DELETE c", {"id": concept_id})

    def add_chunk(self, chunk_id: str, document_id: str, chunk_index: int) -> None:
        self._conn.execute(
            "MERGE (c:Chunk {id: $id}) ON CREATE SET c.document_id = $document_id, c.chunk_index = $chunk_index",
            {"id": chunk_id, "document_id": document_id, "chunk_index": chunk_index},
        )

    def create_concept(self, canonical_form: str, type_: str, alias: str, embedding: list[float], sense: str = "") -> str:
        concept_id = str(uuid.uuid4())
        self._conn.execute(
            "CREATE (c:Concept {id: $id, canonical_form: $canonical_form, type: $type, "
            "aliases: $aliases, embedding: $embedding, sense: $sense})",
            {
                "id": concept_id,
                "canonical_form": canonical_form,
                "type": type_,
                "aliases": dump_aliases([alias]),
                "embedding": embedding,
                "sense": sense,
            },
        )
        self._verify_written(concept_id, canonical_form, [alias], "create_concept")
        return concept_id

    def add_alias(self, concept_id: str, alias: str) -> None:
        result = self._conn.execute(
            "MATCH (c:Concept {id: $id}) RETURN c.canonical_form, c.aliases", {"id": concept_id}
        )
        if not result.has_next():
            return
        canonical_form, raw_aliases = result.get_next()
        aliases = load_aliases(raw_aliases)
        if alias not in aliases:
            self._conn.execute(
                "MATCH (c:Concept {id: $id}) SET c.aliases = $aliases",
                {"id": concept_id, "aliases": dump_aliases(aliases + [alias])},
            )
            self._verify_written(concept_id, canonical_form, aliases + [alias], f"add_alias({alias!r})")

    def _verify_written(self, concept_id: str, canonical_form: str, expected_aliases: list[str], operation: str) -> None:
        """Relit le concept qui vient d'etre ecrit et le compare a ce qu'on y a
        mis. Un ecart signifie que Kuzu a stocke autre chose que la requete
        envoyee -- on s'arrete au premier, avec de quoi le localiser."""
        result = self._conn.execute(
            "MATCH (c:Concept {id: $id}) RETURN c.canonical_form, c.aliases", {"id": concept_id}
        )
        if not result.has_next():
            raise GraphIntegrityError(
                f"{operation} : concept {canonical_form!r} (id {concept_id}) introuvable juste apres son ecriture"
            )
        found_canonical, raw_found = result.get_next()
        found_aliases = load_aliases(raw_found)
        if found_canonical != canonical_form or list(found_aliases) != list(expected_aliases):
            raise GraphIntegrityError(
                f"{operation} : Kuzu a stocke autre chose que ce qui a ete ecrit.\n"
                f"    concept (id {concept_id}) : forme canonique attendue {canonical_form!r}, lue {found_canonical!r}\n"
                f"    alias attendus {list(expected_aliases)!r}\n"
                f"    alias lus      {list(found_aliases)!r}"
            )

    def check_integrity(self, expected: list["ConceptRecord"] | None = None, limit: int = 10) -> list[str]:
        """Relit TOUTE la base et renvoie la liste des anomalies (vide = saine) :
        - un concept dont la forme canonique n'est pas dans ses propres alias
          (invariant garanti par create_concept/add_alias : impossible sans
          corruption du stockage) ;
        - si `expected` (l'etat tenu en memoire par l'appelant) est fourni,
          tout concept absent, en trop, ou dont les alias different.
        Au plus `limit` anomalies detaillees, plus un total."""
        problems: list[str] = []
        found = {c.id: c for c in self.all_concepts()}
        for c in found.values():
            if c.canonical_form not in c.aliases:
                problems.append(
                    f"concept {c.canonical_form!r} (id {c.id}) : sa forme canonique n'est pas dans ses alias {list(c.aliases)!r}"
                )
        if expected is not None:
            for e in expected:
                c = found.get(e.id)
                if c is None:
                    problems.append(f"concept {e.canonical_form!r} (id {e.id}) present en memoire mais absent de la base")
                elif list(c.aliases) != list(e.aliases):
                    problems.append(
                        f"concept {e.canonical_form!r} (id {e.id}) : alias en memoire {list(e.aliases)!r}, dans la base {list(c.aliases)!r}"
                    )
            known_ids = {e.id for e in expected}
            extra = [c for i, c in found.items() if i not in known_ids]
            if extra:
                problems.append(f"{len(extra)} concept(s) dans la base absent(s) de la memoire, ex. {extra[0].canonical_form!r}")
        if len(problems) > limit:
            problems = problems[:limit] + [f"... et {len(problems) - limit} autre(s) anomalie(s)"]
        return problems

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

    def rename_concept(self, concept_id: str, new_canonical_form: str) -> None:
        """Change la forme canonique d'un concept. `new_canonical_form` doit deja
        etre un de ses alias (l'invariant "la forme canonique est dans les
        alias" reste ainsi vrai) ; alias et embedding ne sont pas modifies."""
        concept = self.get_concept(concept_id)
        if concept is None or new_canonical_form not in concept.aliases:
            raise GraphIntegrityError(
                f"rename_concept : {new_canonical_form!r} n'est pas un alias du concept {concept_id}"
            )
        self._conn.execute(
            "MATCH (c:Concept {id: $id}) SET c.canonical_form = $name",
            {"id": concept_id, "name": new_canonical_form},
        )
        self._verify_written(concept_id, new_canonical_form, concept.aliases, f"rename_concept({new_canonical_form!r})")

    def stats(self) -> dict:
        """Contenu actuel du graphe partagé : {"concepts": n, "mentions": n,
        "chunks_by_document": {document_id: n}} — sert à annoncer l'impact
        d'un `clear()` avant de l'exécuter."""
        def _scalar(query: str) -> int:
            result = self._conn.execute(query)
            return result.get_next()[0] if result.has_next() else 0

        by_document: dict[str, int] = {}
        result = self._conn.execute("MATCH (ch:Chunk) RETURN ch.document_id, count(*)")
        while result.has_next():
            doc, n = result.get_next()
            by_document[doc] = n
        return {
            "concepts": _scalar("MATCH (c:Concept) RETURN count(*)"),
            "mentions": _scalar("MATCH ()-[r:MENTIONS]->() RETURN count(r)"),
            "chunks_by_document": by_document,
        }

    def count_chunks(self, document_id: str) -> int:
        """Nombre de noeuds `Chunk` de ce document (contrôle de sortie de /rag-graphe)."""
        result = self._conn.execute(
            "MATCH (ch:Chunk {document_id: $doc}) RETURN count(*)", {"doc": document_id}
        )
        return result.get_next()[0] if result.has_next() else 0

    def count_mentions(self, document_id: str) -> int:
        """Nombre de relations `MENTIONS` issues des chunks de ce document."""
        result = self._conn.execute(
            "MATCH (ch:Chunk {document_id: $doc})-[r:MENTIONS]->(:Concept) RETURN count(r)",
            {"doc": document_id},
        )
        return result.get_next()[0] if result.has_next() else 0

    def all_concepts(self) -> list[ConceptRecord]:
        """Tous les concepts, avec leur `sense` et l'ensemble des documents qui
        les mentionnent (une requete supplementaire pour tous les concepts, pas
        une par concept)."""
        documents: dict[str, set[str]] = {}
        result = self._conn.execute(
            "MATCH (ch:Chunk)-[:MENTIONS]->(c:Concept) RETURN DISTINCT c.id, ch.document_id"
        )
        while result.has_next():
            concept_id, document_id = result.get_next()
            documents.setdefault(concept_id, set()).add(document_id)

        result = self._conn.execute(
            "MATCH (c:Concept) RETURN c.id, c.canonical_form, c.type, c.aliases, c.embedding, c.sense"
        )
        records = []
        while result.has_next():
            row = result.get_next()
            records.append(ConceptRecord(
                id=row[0], canonical_form=row[1], type=row[2], aliases=load_aliases(row[3]), embedding=row[4],
                sense=row[5] or "", documents=documents.get(row[0], set()),
            ))
        return records

    def get_concept(self, concept_id: str) -> ConceptRecord | None:
        result = self._conn.execute(
            "MATCH (c:Concept {id: $id}) RETURN c.id, c.canonical_form, c.type, c.aliases, c.embedding",
            {"id": concept_id},
        )
        if not result.has_next():
            return None
        row = result.get_next()
        return ConceptRecord(id=row[0], canonical_form=row[1], type=row[2], aliases=load_aliases(row[3]), embedding=row[4])

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
            records.append(ConceptRecord(id=row[0], canonical_form=row[1], type=row[2], aliases=load_aliases(row[3]), embedding=row[4]))
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
            record = ConceptRecord(id=row[0], canonical_form=row[1], type=row[2], aliases=load_aliases(row[3]), embedding=row[4])
            mentions = self.mentions_of(record.id)
            count_a = sum(1 for _, doc, _ in mentions if doc == document_id_a)
            count_b = sum(1 for _, doc, _ in mentions if doc == document_id_b)
            records.append((record, count_a, count_b))
        return records
