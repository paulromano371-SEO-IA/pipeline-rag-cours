"""PreToolUse hook (matcher: Bash) — bloque l'exécution en arrière-plan des
scripts des skills du projet.

Deux motifs, selon le skill (détail : /ragpipeline, section « Pourquoi le
foreground ») :
- contention de `claude -p` : /cours-condense, /rag-nottext, /rag-concepts,
  /rag-graphe et l'audit de /rag-integrity font des appels `claude -p`
  headless synchrones qui, en arrière-plan ou en parallèle d'une autre étape,
  entrent en contention avec la session appelante et expirent au bout de 120s ;
- écriture concurrente : /rag-extraction, /rag-chunking, /rag-index,
  /create-content et /publish-content écrivent sur des fichiers ou bases
  partagés.

Ce hook ne bloque QUE l'exécution en arrière-plan (tool_input.run_in_background
== true) d'une commande référençant un dossier .claude/skills/<skill>/ (tous
les skills du projet, sans liste de noms) — la même commande en foreground
reste autorisée normalement.
"""

import json
import re
import sys

# Aucun skill n'est liste par nom : toute commande referencant un dossier
# .claude/skills/<skill>/ est couverte, y compris un skill ajoute plus tard.
_RAG_PATH_RE = re.compile(r"\.claude/skills/[\w.\-]+/", re.IGNORECASE)


def _is_rag_pipeline_command(command: str) -> bool:
    normalized = command.replace("\\", "/")
    return bool(_RAG_PATH_RE.search(normalized))


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
        "Bloque : ce script d'un skill du projet doit toujours tourner en "
        "foreground, jamais via run_in_background. Selon l'etape, la raison est "
        "la contention des appels internes 'claude -p' (timeout a 120s si la "
        "commande tourne en arriere-plan ou en parallele d'une autre etape) ou "
        "l'ecriture concurrente sur des fichiers/bases partages. Voir la "
        "section Execution du SKILL.md de l'etape concernee. "
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
