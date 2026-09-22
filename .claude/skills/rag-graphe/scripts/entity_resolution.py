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
import unicodedata
from dataclasses import dataclass
from typing import Callable

from graph_store import ConceptGraph, ConceptRecord, cosine_similarities
from vector_store import embed_texts
from _claude_code_client import ClaudeCodeCallError, run_claude_code, strip_code_fence
from concepts import ConceptMention

MERGE_THRESHOLD = 0.92
AMBIGUOUS_THRESHOLD = 0.80


def _normalize_for_containment(text: str) -> str:
    stripped = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return stripped.lower().strip()


def _lexically_related(a: str, b: str) -> bool:
    """Inclusion de chaine (accents/casse ignores) entre deux formes
    canoniques -- filet de securite complementaire a la similarite
    d'embedding pour la resolution d'entites. Necessaire empiriquement : sur
    des formes courtes (2-3 mots, sans phrase autour), l'embedding est plus
    bruite que sur un chunk entier -- des variantes clairement apparentees
    ("ridge" / "regularisation ridge", "ridge" / "ridge/lasso") tombaient
    sous AMBIGUOUS_THRESHOLD (0.72-0.77 mesures, contre un seuil a 0.80),
    court-circuitant l'arbitrage LLM sans meme lui donner sa chance -- voir
    tools/ (investigation ridge/lasso). Une inclusion de chaine simple
    (au lieu d'abaisser le seuil globalement) cible precisement ce cas sans
    envoyer en arbitrage des paires de concepts sans rapport lexical
    ailleurs dans le graphe."""
    na, nb = _normalize_for_containment(a), _normalize_for_containment(b)
    if not na or not nb:
        return False
    return na in nb or nb in na

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
    matched_via: str  # "similarity" | "arbitration" | "arbitration_lexical" | "new"
    similarity_score: float


def resolve_concept(
    mention: ConceptMention,
    graph: ConceptGraph,
    *,
    model: str | None = None,
    embed_fn: EmbedFn | None = None,
    arbitrate_fn: ArbitrateFn | None = None,
    known_concepts: list[ConceptRecord] | None = None,
    embedding: list[float] | None = None,
) -> ResolutionOutcome:
    """Résout `mention` vers un concept existant ou en crée un nouveau dans
    `graph`. `embed_fn`/`arbitrate_fn` sont injectables pour les tests
    hors-ligne ; par défaut, embeddings réels (sentence-transformers) et arbitrage réel
    (`claude -p`).

    `embedding` : precalcule, evite un appel `embed_fn` individuel par
    mention (mesure empirique : 404ms par appel isole contre 32ms/texte en
    lot de 20, soit 12x plus lent en individuel -- un appelant qui traite
    plusieurs mentions doit TOUJOURS les embedder en un seul appel groupe en
    amont, voir `rag-graphe/scripts/run.py`, plutot que de laisser cette
    fonction embedder mention par mention). Si omis, retombe sur un appel
    individuel via `embed_fn` (utile hors-ligne / pour une mention isolee).

    `known_concepts` évite de recharger tout le graphe (`graph.all_concepts()`,
    un scan complet de Kuzu avec désérialisation des embeddings) à chaque
    mention : l'appelant peut le charger une seule fois par document et le
    passer ici — cette fonction le tient à jour en y ajoutant tout nouveau
    concept qu'elle crée, pour que les mentions suivantes du même document
    puissent s'y résoudre sans requête supplémentaire. Si omis, retombe sur
    un rechargement complet à chaque appel."""
    embed = embed_fn or embed_texts
    arbitrate = arbitrate_fn or (lambda a, b: _arbitrate_via_claude_code(a, b, model=model))

    if embedding is None:
        embedding = embed([mention.canonical_form])[0]
    existing = graph.all_concepts() if known_concepts is None else known_concepts

    # Comparaison vectorisee (numpy) a TOUS les concepts connus en un seul
    # calcul, reutilisee plus bas pour le filet lexical -- 22x plus rapide
    # qu'une boucle Python comparant candidat par candidat (mesure
    # empirique, voir `graph_store.cosine_similarities`).
    sims = cosine_similarities(embedding, [c.embedding for c in existing])

    best = None
    best_score = -1.0
    if existing:
        best_idx = max(range(len(existing)), key=lambda i: sims[i])
        best, best_score = existing[best_idx], sims[best_idx]

    if best is not None and best_score >= MERGE_THRESHOLD:
        graph.add_alias(best.id, mention.canonical_form)
        if mention.canonical_form not in best.aliases:
            best.aliases.append(mention.canonical_form)
        return ResolutionOutcome(concept_id=best.id, created_new=False, matched_via="similarity", similarity_score=best_score)

    already_arbitrated_best = False
    if best is not None and best_score >= AMBIGUOUS_THRESHOLD:
        already_arbitrated_best = True
        if arbitrate(mention.canonical_form, best.canonical_form):
            graph.add_alias(best.id, mention.canonical_form)
            if mention.canonical_form not in best.aliases:
                best.aliases.append(mention.canonical_form)
            return ResolutionOutcome(concept_id=best.id, created_new=False, matched_via="arbitration", similarity_score=best_score)

    # Filet de securite lexical : une forme candidate clairement apparentee
    # par inclusion de chaine (ex. "ridge" / "regularisation ridge") mais
    # dont la similarite d'embedding tombe sous AMBIGUOUS_THRESHOLD (formes
    # courtes, embedding plus bruite qu'un chunk entier -- voir
    # _lexically_related) merite quand meme un arbitrage, plutot qu'une
    # creation automatique sans aucune verification. Ne re-arbitre jamais la
    # meme paire deux fois (si `best` est deja ce candidat et a deja ete
    # arbitre juste au-dessus).
    lexical_candidate = None
    lexical_score = -1.0
    lexical_indices = [i for i, c in enumerate(existing) if _lexically_related(mention.canonical_form, c.canonical_form)]
    if lexical_indices:
        best_lexical_idx = max(lexical_indices, key=lambda i: sims[i])
        lexical_candidate, lexical_score = existing[best_lexical_idx], sims[best_lexical_idx]

    if lexical_candidate is not None and not (lexical_candidate is best and already_arbitrated_best):
        if arbitrate(mention.canonical_form, lexical_candidate.canonical_form):
            graph.add_alias(lexical_candidate.id, mention.canonical_form)
            if mention.canonical_form not in lexical_candidate.aliases:
                lexical_candidate.aliases.append(mention.canonical_form)
            return ResolutionOutcome(
                concept_id=lexical_candidate.id,
                created_new=False,
                matched_via="arbitration_lexical",
                similarity_score=lexical_score,
            )

    concept_id = graph.create_concept(mention.canonical_form, mention.type, mention.canonical_form, embedding)
    if known_concepts is not None:
        known_concepts.append(
            ConceptRecord(id=concept_id, canonical_form=mention.canonical_form, type=mention.type, aliases=[mention.canonical_form], embedding=embedding)
        )
    return ResolutionOutcome(concept_id=concept_id, created_new=True, matched_via="new", similarity_score=best_score)
