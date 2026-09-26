"""Résolution d'entités : déduplique les concepts extraits par chunk
(`concepts.py`) dans le registre canonique partagé (`graph_store.py`).

Un concept nouvellement extrait est comparé par similarité d'embedding aux
concepts déjà connus :

- similarité haute (>= MERGE_THRESHOLD) : fusion automatique (ajout d'alias) —
  cas clair, pas besoin d'arbitrage, SAUF si le concept connu n'a jamais été
  vu dans le document de la mention : le même mot peut désigner deux choses
  différentes d'un livre à l'autre ("lambda" en Python / en régularisation),
  donc ce cas va toujours à l'arbitrage, qui reçoit le sens et le contexte
  des deux côtés.
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
MAX_ARBITRATION_CANDIDATES = 3


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
    "Tu compares deux concepts extraits de livres techniques pour décider "
    "s'il s'agit du MÊME concept ou de deux concepts DIFFÉRENTS (même proches "
    "ou liés). Le corpus mêle des livres de statistiques, de Python et de "
    "RAG/graphes de connaissances : un même mot y a souvent des sens "
    'différents (ex. "lambda" = fonction Python ou paramètre de '
    'régularisation ; "biais" = biais statistique, social ou paramètre d\'un '
    'réseau ; "generator" = composant RAG ou générateur Python ; "classe" = '
    "classe de programmation ou catégorie à prédire). Deux noms identiques "
    "ou proches NE SUFFISENT PAS : compare ce que chaque concept désigne, "
    "d'après sa définition, son livre et son extrait. En cas de doute réel "
    "sur l'identité du sens, réponds false. Le type (concept, method, tool, "
    "metric...) est attribué à l'extraction de façon peu fiable, \"concept\" "
    "étant le type générique : deux types différents n'excluent pas à eux "
    "seuls un même concept, mais un outil logiciel et une méthode "
    "mathématique distincts ne sont pas le même concept. Réponds UNIQUEMENT avec un objet "
    'JSON {"same": true} ou {"same": false}, sans texte ni balise autour.'
)

_EXCERPT_MAX_CHARS = 400

EmbedFn = Callable[[list[str]], list[list[float]]]


@dataclass
class ArbitrationSide:
    """Ce qu'on sait d'un des deux concepts comparés."""
    name: str
    sense: str = ""
    documents: tuple[str, ...] = ()
    excerpt: str = ""
    type: str = ""


ArbitrateFn = Callable[[ArbitrationSide, ArbitrationSide], bool]


class ArbitrationError(RuntimeError):
    pass


def _format_side(label: str, side: ArbitrationSide) -> str:
    lines = [f'Concept {label}: "{side.name}"', f"  définition : {side.sense or '(non fournie)'}"]
    if side.type:
        lines.append(f"  type : {side.type}")
    if side.documents:
        lines.append(f"  livre(s) : {', '.join(side.documents)}")
    if side.excerpt:
        lines.append(f"  extrait : {side.excerpt[:_EXCERPT_MAX_CHARS]}")
    return "\n".join(lines)


def embedding_text(mention: ConceptMention) -> str:
    """Texte embeddé pour une mention : forme canonique + définition. Deux
    homonymes aux sens différents ne sont ainsi plus à similarité 1.0."""
    return f"{mention.canonical_form} : {mention.sense}" if mention.sense else mention.canonical_form


def _arbitrate_via_claude_code(a: ArbitrationSide, b: ArbitrationSide, *, model: str | None = None, timeout: int = 60) -> bool:
    prompt = _format_side("A", a) + "\n\n" + _format_side("B", b)
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
    document_id: str | None = None,
    excerpt: str = "",
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
    Ce vecteur doit etre celui de `embedding_text(mention)` (nom + sens).

    `document_id` : document de la mention. Un concept connu jamais vu dans
    ce document n'est jamais fusionne automatiquement (voir docstring du
    module) ; None desactive cette regle. `excerpt` : extrait du chunk de la
    mention, transmis a l'arbitrage.

    `known_concepts` évite de recharger tout le graphe (`graph.all_concepts()`,
    un scan complet de Kuzu avec désérialisation des embeddings) à chaque
    mention : l'appelant peut le charger une seule fois par document et le
    passer ici — cette fonction le tient à jour en y ajoutant tout nouveau
    concept qu'elle crée, pour que les mentions suivantes du même document
    puissent s'y résoudre sans requête supplémentaire. Si omis, retombe sur
    un rechargement complet à chaque appel."""
    embed = embed_fn or embed_texts
    arbitrate = arbitrate_fn or (lambda a, b: _arbitrate_via_claude_code(a, b, model=model))

    mention_side = ArbitrationSide(
        name=mention.canonical_form, sense=mention.sense,
        documents=(document_id,) if document_id else (), excerpt=excerpt, type=mention.type,
    )

    def _side_of(concept: ConceptRecord) -> ArbitrationSide:
        return ArbitrationSide(name=concept.canonical_form, sense=concept.sense, documents=tuple(sorted(concept.documents)), type=concept.type)

    def _attach(concept: ConceptRecord) -> None:
        graph.add_alias(concept.id, mention.canonical_form)
        if mention.canonical_form not in concept.aliases:
            concept.aliases.append(mention.canonical_form)
        if document_id:
            concept.documents.add(document_id)

    if embedding is None:
        embedding = embed([embedding_text(mention)])[0]
    existing = graph.all_concepts() if known_concepts is None else known_concepts

    # Comparaison vectorisee (numpy) a TOUS les concepts connus en un seul
    # calcul, reutilisee plus bas pour le filet lexical -- 22x plus rapide
    # qu'une boucle Python comparant candidat par candidat (mesure
    # empirique, voir `graph_store.cosine_similarities`).
    sims = cosine_similarities(embedding, [c.embedding for c in existing])

    best_score = max(sims) if sims else -1.0
    ranked = sorted(range(len(existing)), key=lambda i: sims[i], reverse=True)

    # Fusion automatique : concept tres proche ET deja vu dans CE document.
    for i in ranked:
        if sims[i] < MERGE_THRESHOLD:
            break
        if document_id is None or document_id in existing[i].documents:
            _attach(existing[i])
            return ResolutionOutcome(concept_id=existing[i].id, created_new=False, matched_via="similarity", similarity_score=sims[i])

    # Arbitrage des MAX_ARBITRATION_CANDIDATES concepts les plus proches (et
    # pas seulement du premier) : le bon concept n'est pas toujours le plus
    # ressemblant par embedding, et le comparer au seul premier laissait
    # passer des doublons (ex. "similarity search" / "recherche par
    # similarite vectorielle"). S'arrete au premier "meme concept".
    arbitrated_ids: set[str] = set()
    for i in ranked[:MAX_ARBITRATION_CANDIDATES]:
        if sims[i] < AMBIGUOUS_THRESHOLD:
            break
        arbitrated_ids.add(existing[i].id)
        if arbitrate(mention_side, _side_of(existing[i])):
            _attach(existing[i])
            return ResolutionOutcome(concept_id=existing[i].id, created_new=False, matched_via="arbitration", similarity_score=sims[i])

    # Filet de securite lexical : une forme candidate clairement apparentee
    # par inclusion de chaine (ex. "ridge" / "regularisation ridge") mais
    # dont la similarite d'embedding tombe sous AMBIGUOUS_THRESHOLD (formes
    # courtes, embedding plus bruite qu'un chunk entier -- voir
    # _lexically_related) merite quand meme un arbitrage, plutot qu'une
    # creation automatique sans aucune verification. Ne re-arbitre jamais un
    # concept deja arbitre juste au-dessus.
    lexical_indices = [
        i for i, c in enumerate(existing)
        if c.id not in arbitrated_ids and _lexically_related(mention.canonical_form, c.canonical_form)
    ]
    if lexical_indices:
        i = max(lexical_indices, key=lambda k: sims[k])
        if arbitrate(mention_side, _side_of(existing[i])):
            _attach(existing[i])
            return ResolutionOutcome(concept_id=existing[i].id, created_new=False, matched_via="arbitration_lexical", similarity_score=sims[i])

    concept_id = graph.create_concept(mention.canonical_form, mention.type, mention.canonical_form, embedding, sense=mention.sense)
    if known_concepts is not None:
        known_concepts.append(
            ConceptRecord(
                id=concept_id, canonical_form=mention.canonical_form, type=mention.type, aliases=[mention.canonical_form],
                embedding=embedding, sense=mention.sense, documents={document_id} if document_id else set(),
            )
        )
    return ResolutionOutcome(concept_id=concept_id, created_new=True, matched_via="new", similarity_score=best_score)
