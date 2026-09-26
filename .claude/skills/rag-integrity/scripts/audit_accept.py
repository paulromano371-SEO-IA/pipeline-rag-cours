"""Écarte un signalement de /rag-integrity qu'on juge faux ou acceptable, ou liste les signalements ouverts.

Usage :
    python .claude/skills/rag-integrity/scripts/audit_accept.py --list
    python .claude/skills/rag-integrity/scripts/audit_accept.py <id> [<id> ...]

Passe l'état de l'item de "signale" à "ok" dans rag_data/audit/decisions.json :
il n'est plus remonté, tant que son contexte (extraits, alias, sens) et la
consigne du juge ne changent pas. Ne modifie jamais le graphe. Un problème
réellement corrigé dans le graphe n'a pas besoin de cette commande : l'item
disparaît de lui-même du rapport suivant.
"""
from __future__ import annotations
# Garde-fou : force l'interpreteur du projet (.venv-rag), voir _rag_lib/venv_guard.py
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(next(p for p in _Path(__file__).resolve().parents if (p / ".claude" / "skills" / "_rag_lib").is_dir()) / ".claude" / "skills" / "_rag_lib"))
import venv_guard  # noqa: F401

import argparse
import sys
from datetime import datetime, timezone

import decisions


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ids", nargs="*", help="id(s) d'item (ex. alias:12, shared:40, collision:classe)")
    parser.add_argument("--list", action="store_true", help="liste les signalements ouverts et s'arrête")
    args = parser.parse_args()

    registry = decisions.load()
    open_entries = {i: e for i, e in registry.items() if e.get("state") == "signale"}
    if args.list or not args.ids:
        print(f"{len(open_entries)} signalement(s) ouvert(s) :")
        for item_id, e in sorted(open_entries.items(), key=lambda kv: (kv[1]["category"], kv[0])):
            print(f"  {item_id}  [{e['category']}] {e['subject']} — {e['verdict']} : {e.get('reason', '')}")
        return 0

    missing = [i for i in args.ids if i not in registry]
    if missing:
        print(f"ERREUR: id inconnu du registre : {', '.join(missing)} (voir --list)", file=sys.stderr)
        return 1
    for item_id in args.ids:
        entry = registry[item_id]
        if entry["state"] == "ok":
            print(f"{item_id} : déjà 'ok', rien à faire")
            continue
        entry["state"] = "ok"
        entry["accepte_le"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        print(f"{item_id} : signalement écarté ({entry['subject']})")
    decisions.save(registry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
