"""Décision d'une mention (étape 1) : fusion, nouveau concept, ou arbitrage par Claude.

`decider` est une fonction PURE : elle lit le snapshot du graphe en mémoire, n'écrit jamais dans
Kuzu et ne modifie pas la liste reçue. Elle peut donc tourner en parallèle, un thread par mention.
Les écritures sont faites ensuite par le processus parent (lib/graphe_document.py).

Règles, dans l'ordre :
- similarité >= MERGE_THRESHOLD et concept déjà vu dans CE pdf : fusion automatique ;
- similarité >= AMBIGUOUS_THRESHOLD : arbitrage Claude sur les MAX_ARBITRATION_CANDIDATES plus proches ;
- inclusion de texte (filet lexical) : arbitrage Claude sur ce candidat ;
- sinon : nouveau concept.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))  # scripts/ : commun.py

import unicodedata
from dataclasses import dataclass
from typing import Callable

from commun import load_prompt
from arbitrage import ArbitrationError, ArbitrationSide, arbitrer
from concepts import ConceptMention
from graph_store import ConceptRecord, cosine_similarities

MERGE_THRESHOLD = 0.92
AMBIGUOUS_THRESHOLD = 0.80
MAX_ARBITRATION_CANDIDATES = 3

ARBITRAGE_SYSTEM = load_prompt("etape1.md")

ArbitrateFn = Callable[[ArbitrationSide, ArbitrationSide], bool]


@dataclass
class Decision:
    """Ce qu'il faut faire d'une mention. Le parent l'appliquera, un thread ne l'écrit pas."""
    mention: ConceptMention
    embedding: list[float]
    kind: str                       # "fusion" | "nouveau" | "ignoree"
    concept_id: str | None = None   # "fusion" : concept existant visé
    erreur: str = ""                # "ignoree" : raison de l'échec d'arbitrage


def _arbitrer_etape1(a: ArbitrationSide, b: ArbitrationSide) -> bool:
    return arbitrer(a, b, ARBITRAGE_SYSTEM)


def _normalize_for_containment(text: str) -> str:
    stripped = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return stripped.lower().strip()


def _lexically_related(a: str, b: str) -> bool:
    """Inclusion de chaine (accents/casse ignores) entre deux formes canoniques.

    Filet de sécurité complémentaire à l'embedding : sur des formes courtes, l'embedding est plus
    bruité, et des variantes clairement apparentées ("ridge" / "regularisation ridge") tombent
    sous AMBIGUOUS_THRESHOLD. Une inclusion de chaîne cible précisément ce cas."""
    na, nb = _normalize_for_containment(a), _normalize_for_containment(b)
    if not na or not nb:
        return False
    return na in nb or nb in na


def embedding_text(mention: ConceptMention) -> str:
    """Texte embeddé pour une mention : forme canonique + définition. Deux
    homonymes aux sens différents ne sont ainsi plus à similarité 1.0."""
    return f"{mention.canonical_form} : {mention.sense}" if mention.sense else mention.canonical_form


def decider(
    mention: ConceptMention,
    embedding: list[float],
    existing: list[ConceptRecord],
    document_id: str | None,
    excerpt: str = "",
    arbitrate_fn: ArbitrateFn | None = None,
) -> Decision:
    """Décide le sort de `mention` à partir du snapshot `existing`. N'écrit rien, ne modifie pas `existing`.

    `document_id` : pdf de la mention. Un concept jamais vu dans ce pdf n'est jamais fusionné
    automatiquement (le même mot peut avoir un autre sens d'un pdf à l'autre). `excerpt` : extrait
    du chunk, transmis à l'arbitrage. `arbitrate_fn` : injectable pour les tests hors-ligne."""
    arbitrate = arbitrate_fn or _arbitrer_etape1

    mention_side = ArbitrationSide(
        name=mention.canonical_form, sense=mention.sense,
        documents=(document_id,) if document_id else (), excerpt=excerpt, type=mention.type,
    )

    def _side_of(concept: ConceptRecord) -> ArbitrationSide:
        return ArbitrationSide(name=concept.canonical_form, sense=concept.sense, documents=tuple(sorted(concept.documents)), type=concept.type)

    sims = cosine_similarities(embedding, [c.embedding for c in existing])
    ranked = sorted(range(len(existing)), key=lambda i: sims[i], reverse=True)

    try:
        # Fusion automatique : concept très proche ET déjà vu dans CE pdf.
        for i in ranked:
            if sims[i] < MERGE_THRESHOLD:
                break
            if document_id is None or document_id in existing[i].documents:
                return Decision(mention, embedding, "fusion", concept_id=existing[i].id)

        # Arbitrage des candidats les plus proches, jusqu'au premier « même concept ».
        arbitrated_ids: set[str] = set()
        for i in ranked[:MAX_ARBITRATION_CANDIDATES]:
            if sims[i] < AMBIGUOUS_THRESHOLD:
                break
            arbitrated_ids.add(existing[i].id)
            if arbitrate(mention_side, _side_of(existing[i])):
                return Decision(mention, embedding, "fusion", concept_id=existing[i].id)

        # Filet lexical : un candidat contenant la forme, non encore arbitré, va à l'arbitrage.
        lexical_indices = [
            i for i, c in enumerate(existing)
            if c.id not in arbitrated_ids and _lexically_related(mention.canonical_form, c.canonical_form)
        ]
        if lexical_indices:
            i = max(lexical_indices, key=lambda k: sims[k])
            if arbitrate(mention_side, _side_of(existing[i])):
                return Decision(mention, embedding, "fusion", concept_id=existing[i].id)
    except ArbitrationError as exc:
        return Decision(mention, embedding, "ignoree", erreur=str(exc))

    return Decision(mention, embedding, "nouveau")
