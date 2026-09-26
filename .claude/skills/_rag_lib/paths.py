"""Emplacements standardises du pipeline RAG, centralises a la racine du
projet — jamais de donnees generees eparpillees a cote de chaque livre
source.

    <racine_projet>/
      corpusdedepart/          PDF sources (langue d'origine) UNIQUEMENT, entree
                                de /cours-condense — jamais de dossier de travail ici
      corpuscondense/          cours condenses en francais (PDF finaux) UNIQUEMENT,
                                sortie de /cours-condense, entree de /rag-extraction —
                                jamais de dossier de travail ici non plus
      rag_data/                TOUTES les donnees generees, centralisees, JAMAIS
                                supprime automatiquement (meme par un script) :
        courscondense/<slug>/  dossier de travail de /cours-condense (extraction/,
                                illustrations/, scripts/, course.tex, glossaire.json,
                                plan_cours.json, out/) — redirige ici par instruction
                                d'invocation, cours-condense lui-meme n'est pas modifie
        work/<document_id>/    fichiers intermediaires PAR DOCUMENT du reste du
                                pipeline (pivot.md, images/, chunks.json, concepts.json,
                                status.json, meta.json)
        db/vector/              base Chroma UNIQUE, commune a tout le corpus
        db/graph/                graphe Kuzu UNIQUE, commun a tout le corpus

`document_id` est derive du nom + du contenu du PDF condense (hash) : un
fichier modifie obtient un nouveau `document_id` et repart de zero
naturellement, sans logique d'invalidation manuelle a maintenir.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]

CORPUS_SOURCE_DIR = PROJECT_ROOT / "corpusdedepart"
CORPUS_CONDENSE_DIR = PROJECT_ROOT / "corpuscondense"

RAG_DATA_DIR = PROJECT_ROOT / "rag_data"
COURSCONDENSE_WORK_DIR = RAG_DATA_DIR / "courscondense"
WORK_DIR = RAG_DATA_DIR / "work"
VECTOR_DB_PATH = RAG_DATA_DIR / "db" / "vector"
GRAPH_DB_PATH = RAG_DATA_DIR / "db" / "graph"
# Paires de concepts deja jugees par la consolidation du graphe (/rag-graphe, consolidate.py).
CONSOLIDATION_DECISIONS_PATH = RAG_DATA_DIR / "db" / "consolidation_decisions.json"


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
    return slug[:50] or "document"


def document_id_from_pdf(pdf_path: Path) -> str:
    """Identifiant stable derive du contenu du PDF condense (pas seulement
    de son nom) : un fichier modifie obtient un nouveau `document_id`."""
    digest = hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest()[:8]
    return f"{slugify(Path(pdf_path).stem)}__{digest}"


def work_dir_for(document_id: str) -> Path:
    return WORK_DIR / document_id


def _slugify_livre(name: str) -> str:
    """Meme algorithme, caractere pour caractere, que slugifier() dans
    .claude/skills/cours-condense/scripts/initialiser_arborescence.py (qui
    cree reellement le dossier de travail sur disque) : normalise les
    accents (NFKD) avant substitution, contrairement a slugify() ci-dessus.
    Distinct de slugify() expres -- slugify() est aussi utilise par
    document_id_from_pdf() pour tout le reste du pipeline RAG (rag-extraction
    et suivants) ; le changer casserait la continuite des document_id deja
    calcules pour des documents dont le nom contient un accent."""
    sans_accents = unicodedata.normalize("NFKD", name)
    sans_accents = "".join(c for c in sans_accents if not unicodedata.combining(c))
    slug = re.sub(r"[^a-z0-9]+", "_", sans_accents.lower())
    return re.sub(r"_+", "_", slug).strip("_")


def courscondense_work_dir_for(source_pdf_path: Path) -> Path:
    """Dossier de travail de /cours-condense pour un PDF source donne,
    sous rag_data/courscondense/<slug>/ — jamais a la racine du projet."""
    return COURSCONDENSE_WORK_DIR / _slugify_livre(Path(source_pdf_path).stem)


def courscondense_dir_for_condense_pdf(condense_pdf_path: Path) -> Path:
    """Dossier de travail de /cours-condense correspondant a un PDF DEJA
    publie dans corpuscondense/ (sortie de /cours-condense, entree de
    /rag-extraction) — pas le PDF source original.

    `publish_condense.py` nomme ce fichier `<slug>.pdf` ou `<slug>` est
    exactement le nom du dossier de travail (`work_dir.name`, voir
    `publish_condense.py:publish()`) : pas besoin de reslugifier quoi que ce
    soit ici, contrairement a `courscondense_work_dir_for()` ci-dessus qui
    part du titre du PDF SOURCE et doit donc recalculer le meme slug que
    `initialiser_arborescence.py`. Peut ne pas exister (PDF condense produit
    autrement que par ce pipeline, ou dossier de travail nettoye depuis) —
    a l'appelant de verifier `.exists()` avant usage."""
    return COURSCONDENSE_WORK_DIR / Path(condense_pdf_path).stem


def resolve_work_dir(raw: str) -> Path:
    """Resout n'importe laquelle des formes d'entree acceptees par les
    scripts du pipeline vers le dossier de travail centralise du document :

    - un chemin de PDF condense (typiquement dans `corpuscondense/`) -> le
      `document_id` est calcule a partir de son contenu ;
    - un `document_id` deja connu (nom de dossier sous `rag_data/work/`) ;
    - le dossier de travail lui-meme, ou un fichier a l'interieur
      (`pivot.md`, `chunks.json`, `concepts.json`, `status.json`).
    """
    p = Path(raw)
    resolved = p.resolve() if p.exists() else None

    if resolved is not None:
        if resolved.is_dir() and resolved.parent == WORK_DIR:
            return resolved
        if resolved.is_file() and resolved.parent.parent == WORK_DIR:
            return resolved.parent
        if resolved.is_file() and resolved.suffix.lower() == ".pdf":
            return work_dir_for(document_id_from_pdf(resolved))
        if resolved.is_dir():
            # dossier quelconque contenant un pivot.md/chunks.json (ancienne
            # convention "a cote du PDF") : on le prend tel quel plutot que
            # d'echouer, mais ce n'est pas l'emplacement recommande.
            if (resolved / "pivot.md").exists() or (resolved / "chunks.json").exists():
                return resolved

    candidate = WORK_DIR / raw
    if candidate.exists():
        return candidate

    raise ValueError(
        f"impossible de resoudre {raw!r} vers un dossier de travail du pipeline RAG "
        f"(attendu : un PDF condense, un document_id existant sous {WORK_DIR}, "
        "ou un chemin vers pivot.md/chunks.json/concepts.json)"
    )
