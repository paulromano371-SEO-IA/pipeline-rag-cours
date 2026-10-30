"""Client `claude -p` headless AVEC outils, variante de `_claude_code_client_systemfile.py`.

Même fonctionnement que `_claude_code_client_systemfile.py` (system prompt écrit dans un
fichier temporaire, sortie JSON, erreurs regroupées dans `ClaudeCodeCallError`), à une
différence près : au lieu de `--tools ""` (aucun outil), Claude peut utiliser les outils
listés dans `ALLOWED_TOOLS`. C'est nécessaire quand le prompt demande à Claude de lancer
lui-même des commandes (ex. `/cours-condense`, étape 1).

Le client d'origine reste inchangé pour les autres skills.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile

DEFAULT_TIMEOUT = 600

# Outils que Claude peut utiliser pendant l'appel.
ALLOWED_TOOLS = "Bash,Read,Write"

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


class ClaudeCodeCallError(RuntimeError):
    pass


def strip_ansi_codes(text: str) -> str:
    """Retire les codes ANSI de mise en forme terminal présents dans `stderr`."""
    return _ANSI_ESCAPE_RE.sub("", text)


def run_claude_code(
    system_prompt: str,
    input_text: str,
    *,
    model: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    meta: dict | None = None,
    effort: str | None = None,
) -> str:
    """Envoie `input_text` à un appel `claude -p` headless (outils : `ALLOWED_TOOLS`, sans MCP)
    avec `system_prompt`, et retourne le champ `result` de la réponse.

    Lève `ClaudeCodeCallError` sur tout échec (process, délai dépassé, JSON invalide,
    réponse sans le champ attendu).
    """
    descripteur, chemin_system = tempfile.mkstemp(suffix=".txt", prefix="system_")
    try:
        with os.fdopen(descripteur, "w", encoding="utf-8") as f:
            f.write(system_prompt)
        cmd = [
            "claude", "-p",
            "--allowedTools", ALLOWED_TOOLS,
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--output-format", "json",
            "--system-prompt-file", chemin_system,
        ]
        if model:
            cmd += ["--model", model]
        if effort:
            cmd += ["--effort", effort]

        try:
            proc = subprocess.run(
                cmd,
                input=input_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=None,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClaudeCodeCallError(f"claude -p a dépassé le délai ({timeout}s)") from exc
    finally:
        os.remove(chemin_system)

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

    if meta is not None:
        meta.update({
            "total_cost_usd": payload.get("total_cost_usd"),
            "usage": payload.get("usage"),
            "model_usage": payload.get("modelUsage"),
            "duration_ms": payload.get("duration_ms"),
        })
    return payload["result"]
