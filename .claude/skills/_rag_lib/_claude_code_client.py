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
