"""PreToolUse hook (matcher: Bash) — bloque l'exécution en arrière-plan des
scripts du pipeline RAG.

Chaque étape du pipeline (/cours-condense, /rag-extraction, /rag-chunking,
/rag-index, /rag-concepts, /rag-graphe, /ragpipeline) fait en interne des
appels `claude -p` headless synchrones (réparation de ligatures, extraction
de concepts, résolution d'entités...). Lancer l'étape en arrière-plan (ou en
parallèle d'une autre étape) met ces appels imbriqués en contention avec la
session appelante et les fait expirer au bout de 120s — vu concrètement en
production (timeout systématique de ligature_repair.py). Voir le SKILL.md de
chaque étape : "Toujours en foreground, bloquant jusqu'à complétion — jamais
via run_in_background ni aucun mécanisme async."

Ce hook ne bloque QUE l'exécution en arrière-plan (tool_input.run_in_background
== true) d'une commande référençant un script du pipeline RAG — la même
commande en foreground reste autorisée normalement.
"""

import json
import re
import sys

_RAG_PATH_RE = re.compile(
    r"\.claude/skills/(?:"
    r"cours-condense|rag-extraction|rag-chunking|rag-index|"
    r"rag-concepts|rag-graphe|ragpipeline"
    r")/",
    re.IGNORECASE,
)
# Filet de sécurité générique, au cas où un chemin de script ne suivrait pas
# exactement un des noms de skill ci-dessus (ex. renommage futur) : tout
# run.py sous un dossier scripts/ d'un skill rag-* ou cours-condense.
_RAG_SCRIPT_RE = re.compile(
    r"\.claude/skills/(?:rag-[a-z]+|cours-condense)/scripts/run\.py",
    re.IGNORECASE,
)


def _is_rag_pipeline_command(command: str) -> bool:
    normalized = command.replace("\\", "/")
    return bool(_RAG_PATH_RE.search(normalized) or _RAG_SCRIPT_RE.search(normalized))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    if payload.get("tool_name") != "Bash":
        return 0

    tool_input = payload.get("tool_input") or {}
    if not tool_input.get("run_in_background"):
        return 0

    command = tool_input.get("command") or ""
    if not _is_rag_pipeline_command(command):
        return 0

    reason = (
        "Bloque : ce script du pipeline RAG doit toujours tourner en foreground, "
        "jamais via run_in_background. Les etapes du pipeline (/cours-condense, "
        "/rag-extraction, /rag-chunking, /rag-index, /rag-concepts, /rag-graphe, "
        "/ragpipeline) font des appels internes 'claude -p' synchrones (reparation "
        "de ligatures, extraction de concepts...) qui entrent en contention et "
        "timeoutent a 120s si la commande tourne en arriere-plan ou en parallele "
        "d'une autre etape. Voir le SKILL.md de l'etape concernee. "
        "Relance la meme commande sans run_in_background."
    )
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
        "systemMessage": reason,
    }
    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
