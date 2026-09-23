"""Lance UN lot de /rag-nottext et termine TOUJOURS par une ligne de bilan.

Usage :
    python .claude/skills/rag-nottext/scripts/nottext_lot.py <document_id | pdf | dossier> [--batch-size 10] [--force] [--model NAME]

`--force` (a n'utiliser que sur le tout premier lot) est transmis tel quel a
/rag-nottext. Sans lui, la commande reprend le lot suivant.

Ce script n'enchaine volontairement PAS plusieurs lots : un lot dure de
quelques dizaines de secondes a plusieurs minutes, et une commande de plus de
10 minutes est passee en arriere-plan par l'application (ce que le pipeline
interdit). Un lot par appel ; la derniere ligne de sortie dit quoi faire.

Sortie : la sortie brute complete de /rag-nottext (stdout et stderr
fusionnes, en UTF-8), puis UNE ligne de bilan commencant par [LOT n/N], [FIN],
[BLOQUANT] ou [ERREUR].

Code de sortie DE CE SCRIPT (celui de /rag-nottext, lui, reste 3 sur un lot
partiel -- convention du pipeline, inchangee dans run.py) :
    0  lot termine : partiel ([LOT n/N]) OU etape finie ([FIN]) -- c'est la
       ligne de bilan qui dit lequel ; un lot partiel n'est pas une erreur
    1  erreur technique ou prerequis non satisfait ([ERREUR])
    2  BLOQUANT ([BLOQUANT]) : element(s) en echec, sans description...
"""
from __future__ import annotations

import argparse
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
RUN = Path(__file__).resolve().parent / "run.py"
PYTHON = ROOT / ".venv-rag" / "Scripts" / "python.exe"


def summarize(out: str, returncode: int, batch_size: int, seconds: float) -> tuple[str, int]:
    """(ligne de bilan, code de sortie du script) a partir de la sortie de
    /rag-nottext et de son code de retour. Separee de main() pour etre testable."""
    progress = re.findall(r">>> Progression : (\d+)/(\d+) traites", out)
    done, total = (int(progress[-1][0]), int(progress[-1][1])) if progress else (0, 0)
    duration = f"{seconds:.0f} s"

    if returncode == 3:
        # Le nombre d'elements restants est celui de la ligne FINALE ("Lot de
        # N element(s) traite(s), R restant(s)"), pas celui de la ligne de
        # debut de lot ("Lot en cours : N element(s) sur T restant(s)").
        final = re.search(r"traite\(s\), (\d+) restant\(s\)", out)
        left = final.group(1) if final else str(max(total - done, 0))
        n_lot = math.ceil(done / batch_size) if done else 0
        n_total = math.ceil(total / batch_size) if total else 0
        return (
            f"[LOT {n_lot}/{n_total}] termine en {duration} : {done}/{total} elements, {left} restant(s), "
            "controles d'integrite OK -> relancer la meme commande (sans --force).",
            0,
        )
    if returncode == 0:
        ok = re.search(r"(\d+) element\(s\) traite\(s\) : ", out)
        detail = f"{ok.group(1)} element(s) traite(s)" if ok else "termine"
        state = "deja fait" if "deja fait" in out else "termine"
        return f"[FIN] /rag-nottext {state} en {duration} : {detail} ({done}/{total} elements).", 0
    if returncode == 2:
        return f"[BLOQUANT] /rag-nottext arrete en {duration} apres {done}/{total} elements -- voir le detail ci-dessus. NE PAS enchainer.", 2
    return f"[ERREUR] /rag-nottext a echoue (code {returncode}) en {duration} -- voir le detail ci-dessus.", 1


def main() -> int:
    # Sortie en UTF-8 : sinon Windows ecrit en cp1252 et l'application, qui lit
    # de l'UTF-8, affiche des "?" a la place des accents et des blocs Unicode.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("document", help="document_id, PDF condense ou dossier de travail")
    parser.add_argument("--batch-size", type=int, default=10, help="elements par lot (defaut 10)")
    parser.add_argument("--force", action="store_true", help="premier lot seulement : retraite le document entierement")
    parser.add_argument("--model", type=str, default=None, help="modele Claude pour les descriptions (defaut : installation locale)")
    args = parser.parse_args()

    cmd = [str(PYTHON), str(RUN), args.document, "--batch-size", str(args.batch_size)]
    if args.force:
        cmd.append("--force")
    if args.model:
        cmd += ["--model", args.model]

    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    start = time.time()
    # stderr fusionne dans stdout : l'ordre des lignes est conserve et rien ne
    # s'affiche en rouge simplement parce que la bibliotheque ecrit sur stderr.
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
