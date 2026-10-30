"""`--clear-graph` de l'étape 1 : vider le graphe partagé, puis remettre à zéro l'étape 'graphe'
de chaque document. DESTRUCTIF pour tout le corpus ; ne reconstruit rien.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))  # scripts/ : commun.py

import json
import sys
from pathlib import Path

from commun import progress_path
from graph_store import ConceptGraph
import paths
import status as status_lib


def dossiers_avec_etat_graphe() -> list[Path]:
    """Dossiers de travail (tous documents) dont l'etape 'graphe' est renseignee dans status.json
    ou qui portent un fichier de reprise : ce que `--clear-graph` doit remettre a zero."""
    found = []
    if not paths.WORK_DIR.exists():
        return found
    for d in sorted(p for p in paths.WORK_DIR.iterdir() if p.is_dir()):
        try:
            has_state = "graphe" in status_lib.load_status(d) or progress_path(d).exists()
        except (json.JSONDecodeError, OSError):
            print(f"  attention : status.json illisible dans {d.name}, ignore", file=sys.stderr)
            continue
        if has_state:
            found.append(d)
    return found


def vider(graph: ConceptGraph, dry_run: bool) -> int:
    stats = graph.stats()
    other_dirs = dossiers_avec_etat_graphe()
    print("=== --clear-graph : impact sur le graphe PARTAGE ===")
    print(f"Supprime : {stats['concepts']} concept(s), {stats['mentions']} lien(s), "
          f"{sum(stats['chunks_by_document'].values())} chunk(s) de {len(stats['chunks_by_document'])} document(s) :")
    for doc, n in sorted(stats["chunks_by_document"].items()):
        known = "" if (paths.WORK_DIR / doc).exists() else "  [AUCUN dossier de travail : non reconstructible]"
        print(f"  - {doc} ({n} chunk(s)){known}")
    print(f"Etape 'graphe' remise a zero dans status.json pour {len(other_dirs)} document(s) : "
          + (", ".join(d.name for d in other_dirs) or "aucun"))
    print("Aucun document n'est reconstruit par cette commande.")
    if dry_run:
        print("--dry-run : rien n'a ete modifie.")
        return 0
    graph.clear()
    for d in other_dirs:
        progress_path(d).unlink(missing_ok=True)
        state = status_lib.load_status(d)
        state.pop("graphe", None)
        status_lib.save_status(d, state)
    print("graphe vide ; etape 'graphe' remise a zero pour les documents ci-dessus.")
    print("A reconstruire ensuite, document par document : /rag-graphe <document> --reset --batch-size 10")
    return 0
