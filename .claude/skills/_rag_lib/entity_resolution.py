"""Résolution d'entités : déduplique les concepts extraits par chunk
(`concepts.py`) dans le registre canonique partagé (`graph_store.py`).

Un concept nouvellement extrait est comparé par similarité d'embedding aux
concepts déjà connus :

- similarité haute (>= MERGE_THRESHOLD) : fusion automatique (ajout d'alias) —
  cas clair, pas besoin d'arbitrage.
- similarité basse (< AMBIGUOUS_THRESHOLD) : nouveau concept.
- entre les deux : arbitrage par un appel `claude -p` — la similarité seule
  ne suffit pas à distinguer par exemple "réseau de neurones" de "réseau de
  neurones convolutif", deux concepts proches mais différents.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from graph_store import ConceptGraph, ConceptRecord, cosine_similarity
from vector_store import embed_texts
from _claude_code_client import ClaudeCodeCallError, run_claude_code, strip_code_fence
from concepts import ConceptMention

MERGE_THRESHOLD = 0.92
AMBIGUOUS_THRESHOLD = 0.80

_ARBITRATION_SYSTEM_PROMPT = (
    "Tu compares deux désignations de concepts techniques en IA/Machine "
    "Learning pour décider s'il s'agit du MÊME concept ou de deux concepts "
    "DIFFÉRENTS (même proches ou liés). Réponds UNIQUEMENT avec un objet "
    'JSON {"same": true} ou {"same": false}, sans texte ni balise autour.'
)

EmbedFn = Callable[[list[str]], list[list[float]]]
ArbitrateFn = Callable[[str, str], bool]


class ArbitrationError(RuntimeError):
    pass


def _arbitrate_via_claude_code(name_a: str, name_b: str, *, model: str | None = None, timeout: int = 60) -> bool:
    prompt = f'Concept A: "{name_a}"\nConcept B: "{name_b}"'
    try:
        result = run_claude_code(_ARBITRATION_SYSTEM_PROMPT, prompt, model=model, timeout=timeout)
    except ClaudeCodeCallError as exc:
        raise ArbitrationError(str(exc)) from exc

    raw = strip_code_fence(result)
    try:
        decision = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ArbitrationError(f"réponse non-JSON de claude -p : {raw!r}") from exc
    return bool(decision.get("same", False))


@dataclass
class ResolutionOutcome:
    concept_id: str
    created_new: bool
    matched_via: str  # "similarity" | "arbitration" | "new"
    similarity_score: float


def resolve_concept(
    mention: ConceptMention,
    graph: ConceptGraph,
    *,
    model: str | None = None,
    embed_fn: EmbedFn | None = None,
    arbitrate_fn: ArbitrateFn | None = None,
    known_concepts: list[ConceptRecord] | None = None,
) -> ResolutionOutcome:
    """Résout `mention` vers un concept existant ou en crée un nouveau dans
    `graph`. `embed_fn`/`arbitrate_fn` sont injectables pour les tests
    hors-ligne ; par défaut, embeddings réels (fastembed) et arbitrage réel
    (`claude -p`).

    `known_concepts` évite de recharger tout le graphe (`graph.all_concepts()`,
    un scan complet de Kuzu avec désérialisation des embeddings) à chaque
    mention : l'appelant peut le charger une seule fois par document et le
    passer ici — cette fonction le tient à jour en y ajoutant tout nouveau
    concept qu'elle crée, pour que les mentions suivantes du même document
    puissent s'y résoudre sans requête supplémentaire. Si omis, retombe sur
    un rechargement complet à chaque appel."""
    embed = embed_fn or embed_texts
    arbitrate = arbitrate_fn or (lambda a, b: _arbitrate_via_claude_code(a, b, model=model))

    embedding = embed([mention.canonical_form])[0]
    existing = graph.all_concepts() if known_concepts is None else known_concepts

    best = None
    best_score = -1.0
    for candidate in existing:
        score = cosine_similarity(embedding, candidate.embedding)
        if score > best_score:
            best, best_score = candidate, score

    if best is not None and best_score >= MERGE_THRESHOLD:
        graph.add_alias(best.id, mention.canonical_form)
        if mention.canonical_form not in best.aliases:
            best.aliases.append(mention.canonical_form)
        return ResolutionOutcome(concept_id=best.id, created_new=False, matched_via="similarity", similarity_score=best_score)

    if best is not None and best_score >= AMBIGUOUS_THRESHOLD:
        if arbitrate(mention.canonical_form, best.canonical_form):
            graph.add_alias(best.id, mention.canonical_form)
            if mention.canonical_form not in best.aliases:
                best.aliases.append(mention.canonical_form)
            return ResolutionOutcome(concept_id=best.id, created_new=False, matched_via="arbitration", similarity_score=best_score)

    concept_id = graph.create_concept(mention.canonical_form, mention.type, mention.canonical_form, embedding)
    if known_concepts is not None:
        known_concepts.append(
            ConceptRecord(id=concept_id, canonical_form=mention.canonical_form, type=mention.type, aliases=[mention.canonical_form], embedding=embedding)
        )
    return ResolutionOutcome(concept_id=concept_id, created_new=True, matched_via="new", similarity_score=best_score)
