"""Lance UN lot de audit_run.py et termine TOUJOURS par une ligne de bilan.

Usage :
    python .claude/skills/rag-integrity/scripts/audit_lot.py [rapport_graphe_<ts>.json] [--batch-size 6] [--force]

Sans argument, prend le rapport le plus récent sous rag_data/audit/ (celui
que report.py vient d'écrire). `--force` (à n'utiliser que sur le tout
premier lot) rejuge tous les items ; sans lui, la commande reprend le lot
suivant.

Ce script n'enchaîne volontairement PAS plusieurs lots : un lot dure de
quelques secondes à quelques minutes, et une commande de plus de 10 minutes
est passée en arrière-plan par l'application (ce que /rag-integrity
interdit, même raison que les autres étapes par lots du pipeline RAG). Un
lot par appel ; la dernière ligne de sortie dit quoi faire.

Sortie : la sortie brute complète de audit_run.py (stdout et stderr
fusionnés, en UTF-8), puis UNE ligne de bilan commençant par [LOT n/N], [FIN],
[BLOQUANT] ou [ERREUR].

Code de sortie DE CE SCRIPT (celui de audit_run.py, lui, reste 3 sur un lot
partiel -- même convention que le reste du pipeline) :
    0  lot terminé : partiel ([LOT n/N]) OU étape finie ([FIN])
    1  erreur technique ou prérequis non satisfait ([ERREUR])
    2  BLOQUANT ([BLOQUANT]) : > 5 % d'items jamais jugés
"""
from __future__ import annotations
# Garde-fou : force l'interpreteur du projet (.venv-rag), voir _rag_lib/venv_guard.py
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(next(p for p in _Path(__file__).resolve().parents if (p / ".claude" / "skills" / "_rag_lib").is_dir()) / ".claude" / "skills" / "_rag_lib"))
import venv_guard  # noqa: F401

import argparse
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
RUN = Path(__file__).resolve().parent / "audit_run.py"
PYTHON = ROOT / ".venv-rag" / "Scripts" / "python.exe"


def summarize(out: str, returncode: int, batch_size: int, seconds: float) -> tuple[str, int]:
    """(ligne de bilan, code de sortie du script) à partir de la sortie de
    audit_run.py et de son code de retour. Séparée de main() pour être testable."""
    progress = re.findall(r">>> Progression : (\d+)/(\d+)", out)
    done, total = (int(progress[-1][0]), int(progress[-1][1])) if progress else (0, 0)
    duration = f"{seconds:.0f} s"

    if returncode == 3:
        # La ligne "Lot en cours" (avant traitement) contient elle aussi
        # "restant(s)" : prendre la DERNIÈRE occurrence (ligne finale "Lot de
        # N item(s) traité(s)..., R restant(s)"), jamais la première.
        matches = re.findall(r"(\d+) restant\(s\)", out)
        left = matches[-1] if matches else str(max(total - done, 0))
        n_lot = math.ceil(done / batch_size) if done else 0
        n_total = math.ceil(total / batch_size) if total else 0
        return (
            f"[LOT {n_lot}/{n_total}] terminé en {duration} : {done}/{total} items, {left} restant(s) -> "
            "relancer la même commande (sans --force).",
            0,
        )
    if returncode == 0:
        ok = re.search(r"OK: (\d+)/(\d+) item", out)
        detail = f"{ok.group(1)}/{ok.group(2)} item(s) jugés" if ok else "terminé"
        return f"[FIN] audit terminé en {duration} : {detail}.", 0
    if returncode == 2:
        return f"[BLOQUANT] audit arrêté en {duration} après {done}/{total} items -- voir le détail ci-dessus. NE PAS enchaîner.", 2
    return f"[ERREUR] audit a échoué (code {returncode}) en {duration} -- voir le détail ci-dessus.", 1


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("report", nargs="?", type=str, help="rapport_graphe_<ts>.json (défaut : le plus récent sous rag_data/audit/)")
    parser.add_argument("--batch-size", type=int, default=6, help="items par lot (défaut 6)")
    parser.add_argument("--force", action="store_true", help="premier lot seulement : rejuge tous les items")
    parser.add_argument("--model", type=str, default=None)
    args = parser.parse_args()

    cmd = [str(PYTHON), str(RUN)]
    if args.report:
        cmd.append(args.report)
    cmd += ["--batch-size", str(args.batch_size)]
    if args.force:
        cmd.append("--force")
    if args.model:
        cmd += ["--model", args.model]

    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    start = time.time()
    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", env=env,
    )
    seconds = time.time() - start

    out = proc.stdout or ""
    print(out, end="" if out.endswith("\n") else "\n")
    line, code = summarize(out, proc.returncode, args.batch_size, seconds)
    print(line)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
