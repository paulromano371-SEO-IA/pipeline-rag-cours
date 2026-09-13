"""Réparation des ligatures typographiques mal encodées (ff, fi, fl, ffi, ffl).

Certains PDF (typiquement issus de LaTeX) ont une police dont les glyphes de
ligature n'ont pas de correspondance Unicode correcte. PyMuPDF les extrait
alors comme un caractère de contrôle invisible en plein milieu d'un mot
("figées" -> "\\x1cgées"), sans jamais produire de U+FFFD — c'est le gate
qualité (`quality.py`) qui les repère.

Le code de contrôle utilisé pour chaque ligature dépend de la police du
document (pas de standard universel), donc pas de table de correspondance
fixe possible. On montre le contexte de chaque code à un appel Claude Code
headless (même mécanisme que l'extraction de concepts) pour déduire, à
partir du mot français reconstitué, quelle ligature il représente, puis on
applique un remplacement déterministe.
"""

from __future__ import annotations

import json
import unicodedata

from _claude_code_client import ClaudeCodeCallError, run_claude_code, strip_code_fence

_ALLOWED_CONTROL_CHARS = {"\n", "\t", "\r"}
_CANDIDATE_LIGATURES = {"ff", "fi", "fl", "ffi", "ffl"}
_MAX_CONTEXTS_PER_CODE = 5

_SYSTEM_PROMPT = (
    "Tu analyses des extraits de texte français issus d'un PDF dont certaines "
    "ligatures typographiques (ff, fi, fl, ffi, ffl) ont été mal extraites et "
    "remplacées par un caractère de contrôle invisible, représenté ici par le "
    "symbole █. On te donne, pour un ou plusieurs codes, plusieurs extraits où "
    "chacun apparaît. Déduis quelle ligature (ff, fi, fl, ffi ou ffl) chaque "
    "code représente, à partir du mot français que ça reconstitue une fois "
    "remplacé. Réponds UNIQUEMENT avec un objet JSON de la forme "
    '{"<code>": "<ligature>"}, sans aucun texte ni balise de code autour.'
)


class LigatureRepairError(RuntimeError):
    pass


def _char_from_code(code: str) -> str:
    return chr(int(code.removeprefix("U+"), 16))


def find_control_chars(text: str) -> dict[str, list[str]]:
    """Recense chaque caractère de contrôle non standard et jusqu'à
    `_MAX_CONTEXTS_PER_CODE` extraits de contexte où il apparaît."""
    contexts: dict[str, list[str]] = {}
    for idx, c in enumerate(text):
        if c in _ALLOWED_CONTROL_CHARS:
            continue
        if unicodedata.category(c) != "Cc":
            continue
        code = f"U+{ord(c):04X}"
        start, end = max(0, idx - 10), min(len(text), idx + 11)
        snippet = text[start:end].replace(c, "█")
        bucket = contexts.setdefault(code, [])
        if len(bucket) < _MAX_CONTEXTS_PER_CODE and snippet not in bucket:
            bucket.append(snippet)
    return contexts


def infer_ligature_mapping(
    contexts: dict[str, list[str]], *, model: str | None = None, timeout: int = 120
) -> dict[str, str]:
    """Interroge Claude Code (headless, sans outils ni MCP) pour déduire la
    ligature représentée par chaque code de contrôle."""
    if not contexts:
        return {}

    user_message = "\n".join(f"{code}: " + " | ".join(snippets) for code, snippets in contexts.items())

    try:
        result = run_claude_code(_SYSTEM_PROMPT, user_message, model=model, timeout=timeout)
    except ClaudeCodeCallError as exc:
        raise LigatureRepairError(str(exc)) from exc

    raw = strip_code_fence(result)
    try:
        mapping: dict[str, str] = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LigatureRepairError(f"réponse non-JSON de claude -p : {raw!r}") from exc

    # Ne garde que les réponses parmi les 5 ligatures latines valides : une
    # réponse hors de cette liste indique un doute qu'on préfère laisser
    # visible (caractère de contrôle non remplacé) plutôt que d'introduire du
    # texte inventé.
    return {code: ligature for code, ligature in mapping.items() if ligature in _CANDIDATE_LIGATURES}


def repair_ligatures(text: str, mapping: dict[str, str]) -> str:
    """Remplace chaque caractère de contrôle par la ligature déduite."""
    result = text
    for code, ligature in mapping.items():
        result = result.replace(_char_from_code(code), ligature)
    return result


def auto_repair_ligatures(text: str, *, model: str | None = None) -> tuple[str, dict[str, str]]:
    """Enchaîne détection, inférence et remplacement. Retourne le texte
    réparé (inchangé si aucun code résolu) et la correspondance utilisée."""
    contexts = find_control_chars(text)
    mapping = infer_ligature_mapping(contexts, model=model)
    return repair_ligatures(text, mapping), mapping
