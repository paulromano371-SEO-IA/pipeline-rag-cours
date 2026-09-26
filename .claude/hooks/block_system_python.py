"""PreToolUse hook (matcher: Bash|PowerShell) — impose l'interpreteur du projet
(.venv-rag) pour tout script du pipeline RAG.

Les scripts sous .claude/skills/rag-*/, .claude/skills/create-content/ et
tools/ dependent de paquets installes uniquement dans <racine>/.venv-rag
(pytesseract, chromadb, kuzu...). Les lancer avec `python`/`py` (Python
systeme) echoue en ModuleNotFoundError ou, pire, tourne dans un mauvais
environnement. Ce hook refuse ces commandes et donne la commande correcte.
Complement du garde-fou `_rag_lib/venv_guard.py` (relance automatique).
"""

import json
import re
import sys

_SCRIPT_RE = re.compile(
    r"(?:\.claude/skills/(?:rag-[a-z]+|create-content)/scripts/|(?:^|[\s\"'/])tools/)"
    r"[\w.\-]+\.py",
    re.IGNORECASE,
)
_VENV_RE = re.compile(r"\.venv-rag/Scripts/python(?:\.exe)?", re.IGNORECASE)
_SYSTEM_PY_RE = re.compile(r"(?:^|[;&|(]\s*|&\s+)[\"']?(?:python3?|py)(?:\.exe)?[\"']?\s", re.IGNORECASE)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    if payload.get("tool_name") not in ("Bash", "PowerShell"):
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    normalized = command.replace("\\", "/")
    if not _SCRIPT_RE.search(normalized):
        return 0
    # Chaque segment de commande lancant un script RAG doit passer par le venv.
    for segment in re.split(r"&&|\|\||;|\n", normalized):
        if _SCRIPT_RE.search(segment) and not _VENV_RE.search(segment) \
                and _SYSTEM_PY_RE.search(segment.strip() + " "):
            reason = (
                "Bloque : ce script du pipeline RAG doit tourner avec l'interpreteur "
                "du projet, pas le Python systeme. Relance la meme commande en "
                "remplacant `python` par "
                "\"<racine_projet>/.venv-rag/Scripts/python.exe\" "
                "(voir le SKILL.md de l'etape)."
            )
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                },
                "systemMessage": reason,
            }))
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
