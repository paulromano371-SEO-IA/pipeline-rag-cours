"""Client `claude -p` à session persistante pour /cours-condense.

Même mécanique que `_rag_lib/_claude_code_client_systemfile.py` (sans outils ni MCP, system
prompt par fichier, sortie JSON), avec en plus :
- la lecture de l'identifiant de session renvoyé par `claude -p` ;
- `--resume <id>` : l'appel continue une session existante (tous les messages précédents sont
  en contexte, mot pour mot) ;
- `--fork-session` : la reprise crée une NOUVELLE session copie de l'ancienne, l'ancienne n'est
  jamais modifiée (reprise propre après une réponse invalide).

Constat de test (voir la section « Sessions » du README) : le system prompt est fixé à la création
de la session, un autre system prompt passé à la reprise est ignoré. Les consignes propres à chaque
appel vont donc dans le message utilisateur.

Le client partagé `_rag_lib` n'est pas modifié : les étapes RAG ne sont pas concernées.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from _claude_code_client_systemfile import ClaudeCodeCallError, strip_ansi_codes


def appeler_session(
    system_prompt: str,
    input_text: str,
    *,
    workdir: Path,
    model: str | None = None,
    effort: str | None = None,
    timeout: int = 600,
    resume: str | None = None,
    fork: bool = False,
) -> dict:
    """Un appel `claude -p`. Retourne {result, session_id, contexte_tokens, fenetre, meta}.

    `workdir` : dossier de travail du processus `claude`. Les transcriptions de session y sont
    rangées (sous ~/.claude/projects/), à part de celles du projet. `resume` : identifiant d'une
    session à continuer ; `fork` : crée une nouvelle session à partir d'elle.
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "claude", "-p",
        "--tools", "",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--output-format", "json",
    ]
    chemin_system = None
    if resume:
        cmd += ["--resume", resume]
        if fork:
            cmd += ["--fork-session"]
    else:
        # System prompt dans un fichier (limite de ligne de commande Windows) ; fixé à la création.
        descripteur, chemin_system = tempfile.mkstemp(suffix=".txt", prefix="system_")
        with os.fdopen(descripteur, "w", encoding="utf-8") as f:
            f.write(system_prompt)
        cmd += ["--system-prompt-file", chemin_system]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]

    try:
        proc = subprocess.run(
            cmd, input=input_text, capture_output=True, text=True, encoding="utf-8",
            timeout=timeout, cwd=str(workdir),
        )
    except subprocess.TimeoutExpired as exc:
        raise ClaudeCodeCallError(f"claude -p a dépassé le délai ({timeout}s)") from exc
    finally:
        if chemin_system:
            os.remove(chemin_system)

    if proc.returncode != 0:
        raise ClaudeCodeCallError(f"claude -p a échoué (code {proc.returncode}): {strip_ansi_codes(proc.stderr).strip()}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeCodeCallError(f"réponse non-JSON de claude -p : {proc.stdout[:2000]!r}") from exc
    if payload.get("is_error"):
        raise ClaudeCodeCallError(f"claude -p a retourné une erreur: {payload}")
    if "result" not in payload or not payload.get("session_id"):
        raise ClaudeCodeCallError(f"réponse claude -p sans result ou session_id : {str(payload)[:500]}")

    usage = payload.get("usage") or {}
    contexte = sum(usage.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    modeles = payload.get("modelUsage") or {}
    fenetre = max((m.get("contextWindow") or 0 for m in modeles.values()), default=0) or 200000
    return {
        "result": payload["result"],
        "session_id": payload["session_id"],
        "contexte_tokens": contexte,
        "fenetre": fenetre,
        "meta": {
            "total_cost_usd": payload.get("total_cost_usd"),
            "usage": payload.get("usage"),
            "model_usage": payload.get("modelUsage"),
            "duration_ms": payload.get("duration_ms"),
        },
    }
