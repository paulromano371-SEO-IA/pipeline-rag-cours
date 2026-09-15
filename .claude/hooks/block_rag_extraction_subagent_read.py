"""PreToolUse hook (matcher: Agent) — bloque toute délégation à un
sous-agent de la lecture/vérification de `pivot.md` (sortie de
/rag-extraction) ou de son rapport qualité/fidélité.

Voir le SKILL.md de /rag-extraction, section "Execution" : "Interdiction de
déléguer à un sous-agent (outil Agent) toute lecture ou vérification de
pivot.md ou du rapport qualité — cette lecture doit être faite directement,
dans le même tour de conversation, jamais confiée à un sous-agent 'pour
économiser du contexte' : préserver le déterminisme et éviter toute
contention entre appels imbriqués."

Ce hook ne bloque QUE les appels `Agent` dont le prompt référence
explicitement `pivot.md` ou le dossier de travail du pipeline
(`rag_data/work/...`) — tout autre usage de l'outil Agent reste autorisé
normalement.
"""

import json
import re
import sys

_PIVOT_REF_RE = re.compile(r"pivot\.md|rag_data[/\\]work[/\\]", re.IGNORECASE)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    if payload.get("tool_name") != "Agent":
        return 0

    tool_input = payload.get("tool_input") or {}
    haystack = " ".join(
        str(tool_input.get(key) or "") for key in ("prompt", "description")
    )
    if not _PIVOT_REF_RE.search(haystack):
        return 0

    reason = (
        "Bloque : la lecture ou la verification de pivot.md (sortie de "
        "/rag-extraction) et de son rapport qualite/fidelite ne doit jamais "
        "etre deleguee a un sous-agent, meme 'pour economiser du contexte' — "
        "voir le SKILL.md de /rag-extraction, section Execution. Cette "
        "lecture doit se faire directement, dans le meme tour de "
        "conversation : meme raison que l'interdiction de run_in_background, "
        "preserver le determinisme et eviter toute contention entre appels "
        "imbriques. Lis le fichier toi-meme (Read/Bash), sans passer par "
        "l'outil Agent."
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
