"""Extraction de concepts par chunk (prépare la construction du graphe).

Même mécanisme headless qu'`entity_resolution.py` : un appel `claude -p` sans
outils ni MCP, pour rester cohérent avec le choix d'éviter une clé API
facturée à l'usage.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from _claude_code_client import ClaudeCodeCallError, run_claude_code, strip_code_fence

_CONCEPT_TYPES = {"concept", "method", "tool", "metric", "person", "other"}

_SYSTEM_PROMPT = (
    "Tu extrais les concepts clés d'un extrait technique en français, dans le "
    "domaine de l'intelligence artificielle / du machine learning. Pour "
    "chaque concept notable (pas les mots génériques), donne : le nom tel "
    'qu\'il apparaît dans le texte ("name"), une forme canonique courte et '
    'normalisée ("canonical_form", en minuscules, sans article), et un type '
    'parmi concept, method, tool, metric, person, other ("type"). '
    "La forme canonique doit toujours être ATOMIQUE, jamais composée : si le "
    "texte mentionne deux méthodes ou concepts ensemble (ex. \"ridge et "
    "lasso\", \"ridge/lasso\"), donne DEUX entrées séparées (\"ridge\", "
    "\"lasso\"), jamais une forme combinée (\"ridge/lasso\"). Retire aussi "
    "tout préfixe générique qui n'apporte rien à l'identification du concept "
    '(ex. "régularisation ridge" -> "ridge", "méthode du bootstrap" -> '
    '"bootstrap") : la forme canonique doit être celle, la plus courte et la '
    "plus stable, qu'on retrouverait à l'identique dans n'importe quel autre "
    "passage mentionnant ce même concept — nécessaire pour que deux mentions "
    "du même concept dans des passages différents partagent exactement la "
    "même forme canonique, condition pour que la résolution d'entités en "
    "aval (comparaison d'embeddings sur cette seule forme courte) les "
    "reconnaisse comme identiques. Limite-toi aux 3-8 concepts les plus "
    "significatifs du passage, ignore les mots communs. Réponds UNIQUEMENT "
    "avec un tableau JSON d'objets "
    '{"name": ..., "canonical_form": ..., "type": ...}, sans texte ni balise autour.'
)


class ConceptExtractionError(RuntimeError):
    pass


@dataclass
class ConceptMention:
    name: str
    canonical_form: str
    type: str


def _parse_concepts(raw_json: str) -> list[ConceptMention]:
    """Parse et valide la réponse JSON du LLM. Isolé de l'appel réseau pour
    pouvoir être testé hors-ligne."""
    try:
        items = json.loads(strip_code_fence(raw_json))
    except json.JSONDecodeError as exc:
        raise ConceptExtractionError(f"réponse non-JSON de claude -p : {raw_json!r}") from exc

    if not isinstance(items, list):
        raise ConceptExtractionError(f"réponse JSON inattendue (pas une liste) : {items!r}")

    mentions: list[ConceptMention] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        canonical = str(item.get("canonical_form", "")).strip().lower()
        type_ = item.get("type") if item.get("type") in _CONCEPT_TYPES else "other"
        if name and canonical:
            mentions.append(ConceptMention(name=name, canonical_form=canonical, type=type_))
    return mentions


def extract_concepts(chunk_text: str, *, model: str | None = None, timeout: int = 120) -> list[ConceptMention]:
    try:
        result = run_claude_code(_SYSTEM_PROMPT, chunk_text, model=model, timeout=timeout)
    except ClaudeCodeCallError as exc:
        raise ConceptExtractionError(str(exc)) from exc

    return _parse_concepts(result)
