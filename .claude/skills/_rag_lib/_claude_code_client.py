"""Client partagé pour les appels `claude -p` headless (sans outils ni MCP).

Utilisé par `concepts.py` et `entity_resolution.py`, pour ne pas dupliquer la
construction de commande, la gestion d'erreur du sous-processus et le
parsing JSON dans chacun. (`ligature_repair.py` utilisait aussi ce client,
mais résout désormais les ligatures par dictionnaire français hors ligne —
déterministe et sans le risque de contention/timeout d'un appel `claude -p`
imbriqué, voir sa docstring.)
"""

from __future__ import annotations

import json
import re
import subprocess

DEFAULT_TIMEOUT = 180

# Nombre de tentatives SUPPLEMENTAIRES (donc 1 + ce nombre au total) que
# `describe_code`/`describe_formula`/`describe_image` s'autorisent quand la
# reponse ne respecte pas le format demande (JSON invalide, champ manquant,
# description multi-paragraphe) — verifie empiriquement : ce genre d'echec
# est majoritairement NON-DETERMINISTE (le meme contenu, redemande a
# l'identique, reussit la plupart du temps) plutot qu'un defaut structurel
# du contenu source. Ne s'applique JAMAIS a un echec d'INFRASTRUCTURE
# (process, timeout, reponse d'erreur explicite de `claude -p` lui-meme,
# voir `run_claude_code`) : retenter un timeout avec le meme budget de temps
# n'a pas la meme justification empirique et coute cher en pratique.
MAX_FORMAT_RETRIES = 2

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


class ClaudeCodeCallError(RuntimeError):
    pass


def strip_ansi_codes(text: str) -> str:
    """Retire les codes ANSI de mise en forme terminal (couleurs, etc.) —
    présents dans `stderr`/`stdout` de `claude -p` (ex. un avertissement en
    jaune), lisibles dans un vrai terminal mais affichés en brut
    (`\\x1b[33m...\\x1b[39m`) une fois stockés tels quels ailleurs."""
    return _ANSI_ESCAPE_RE.sub("", text)


def run_claude_code(
    system_prompt: str,
    input_text: str,
    *,
    model: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Envoie `input_text` à un appel `claude -p` headless (sans outils ni
    MCP) avec `system_prompt`, et retourne le champ `result` de la réponse.

    Lève `ClaudeCodeCallError` sur tout échec (process, délai dépassé, JSON
    invalide, réponse sans le champ attendu) plutôt que de laisser remonter
    une exception générique (`KeyError`, `JSONDecodeError`, `TimeoutExpired`)
    qui empêcherait l'appelant de marquer proprement l'étape comme "failed".
    """
    cmd = [
        "claude", "-p",
        "--tools", "",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--output-format", "json",
        "--system-prompt", system_prompt,
    ]
    if model:
        cmd += ["--model", model]

    try:
        proc = subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ClaudeCodeCallError(f"claude -p a dépassé le délai ({timeout}s)") from exc

    if proc.returncode != 0:
        raise ClaudeCodeCallError(f"claude -p a échoué (code {proc.returncode}): {strip_ansi_codes(proc.stderr).strip()}")

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeCodeCallError(f"réponse non-JSON de claude -p : {proc.stdout[:2000]!r}") from exc

    if payload.get("is_error"):
        raise ClaudeCodeCallError(f"claude -p a retourné une erreur: {payload}")

    if "result" not in payload:
        raise ClaudeCodeCallError(f"réponse claude -p sans champ 'result' : {payload}")

    return payload["result"]


def strip_code_fence(text: str) -> str:
    """Retire un éventuel bloc ```json ... ``` (ou variante) autour d'une
    réponse LLM censée être du JSON brut."""
    return re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()


def clean_plain_text_response(text: str) -> str:
    """Nettoie une réponse LLM censée être du texte brut (jamais du JSON —
    voir `code_description.py`/`formula_description.py`/`image_vision.py` :
    demander une valeur unique en JSON expose à un risque de syntaxe cassée
    que du texte brut n'a structurellement pas). Retire un éventuel bloc de
    code (```...```) autour, puis un éventuel couple de guillemets doubles
    englobant tout le texte — réflexe fréquent d'un modèle habitué aux
    conventions JSON malgré la consigne de texte brut — jamais un guillemet
    interne, seulement le couple qui engloberait la réponse entière."""
    text = strip_code_fence(text)
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1]
    return text.strip()


# Taille maximale d'un groupe pour describe_code_batch/describe_formula_batch
# (voir code_description.py/formula_description.py) — jamais pour les
# images (une par appel, chacune nécessitant son propre `Read`). Choisie par
# l'utilisateur pour réduire le nombre d'allers-retours séquentiels
# `claude -p`, le vrai goulot d'étranglement de `/rag-nottext` sur un
# document à beaucoup de blocs de code/formules (vérifié empiriquement).
MAX_GROUP_SIZE = 5

_BATCH_MARKER_RE = re.compile(r"^###(\d+)###[ \t]*$", re.MULTILINE)


def build_batch_prompt(items: list[str]) -> str:
    """Concatène `items` (blocs de code ou formules) avec un marqueur
    `###k###` (k à partir de 1) avant chacun — format texte brut, jamais un
    tableau JSON (même raison que `clean_plain_text_response` : pas de
    syntaxe à casser)."""
    parts = [f"###{idx}###\n{item}" for idx, item in enumerate(items, start=1)]
    return "\n\n".join(parts)


def parse_batch_response(text: str, expected: int) -> list[str] | None:
    """Extrait les `expected` descriptions d'une réponse groupée, une par
    marqueur `###k###` rencontré dans l'ordre, sur sa propre ligne, suivi du
    texte jusqu'au marqueur suivant (ou la fin). Retourne `None` — jamais un
    appariement partiel ou approximatif — si le nombre de marqueurs trouvés
    diffère de `expected`, ou si leur numérotation n'est pas exactement
    1, 2, ..., `expected` dans cet ordre : un doute sur la correspondance
    bloc <-> description ne doit jamais se résoudre par une supposition."""
    matches = list(_BATCH_MARKER_RE.finditer(text))
    if len(matches) != expected:
        return None
    descriptions = []
    for i, m in enumerate(matches):
        if int(m.group(1)) != i + 1:
            return None
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        descriptions.append(text[start:end].strip())
    return descriptions


_BLANK_LINE_RE = re.compile(r"\n\s*\n")


def is_single_paragraph(text: str) -> bool:
    """Vrai si `text` ne contient aucune ligne vide interne.

    Utilisé pour valider les descriptions générées par `image_vision.py`,
    `code_description.py` et `formula_description.py` : `chunk.py`
    (`_merge_description_blocks`) suppose qu'une description tient dans un
    seul paragraphe Markdown pour rester rattachée au marqueur
    `DESCRIPTION_MARKER` et fusionnée avec le bloc code/image/formule
    qu'elle décrit — une description sur plusieurs paragraphes casserait
    silencieusement cette fusion (seul le premier paragraphe resterait
    utilisé pour l'embedding), d'où le rejet explicite en amont plutôt que
    de laisser passer une dégradation invisible."""
    return not _BLANK_LINE_RE.search(text.strip())
