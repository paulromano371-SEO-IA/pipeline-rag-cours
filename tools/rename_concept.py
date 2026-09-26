"""Renomme la forme canonique d'un concept du graphe partage (`rag_data/db/graph/`).

Ne change QUE le champ `canonical_form` : alias, embedding, definition, type et
liens vers les chunks restent identiques. Le nouveau nom doit deja etre un des
alias du concept (invariant "la forme canonique est dans les alias") ; sinon
`ConceptGraph.rename_concept` refuse.

Usage :
    python tools/rename_concept.py <id> "<canonical_form>"
    python tools/rename_concept.py <id> "<canonical_form>" --dry-run

`<id>` : identifiant complet ou prefixe unique (ex. les 8 premiers caracteres).
`--dry-run` : affiche ce qui serait fait, sans rien ecrire.

Apres ecriture : verification que alias, type et nombre de mentions sont
inchanges, puis controle d'integrite de toute la base. Code de sortie 0 si tout
est bon, 1 sinon. A ne pas lancer pendant qu'une etape du pipeline RAG tourne
(Kuzu n'autorise qu'un processus a la fois).

Usage :

cd "chemin du projet"

.venv-rag\Scripts\activate.bat

python tools/rename_concept.py 67336fe9 "one-versus-one" --dry-run

"""
from __future__ import annotations
# Garde-fou : force l'interpreteur du projet (.venv-rag), voir _rag_lib/venv_guard.py
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(next(p for p in _Path(__file__).resolve().parents if (p / ".claude" / "skills" / "_rag_lib").is_dir()) / ".claude" / "skills" / "_rag_lib"))
import venv_guard  # noqa: F401

import argparse
import sys
from pathlib import Path

_RAG_LIB = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "_rag_lib"
sys.path.insert(0, str(_RAG_LIB))

import paths  # noqa: E402
from graph_store import ConceptGraph, GraphIntegrityError  # noqa: E402


def _resolve(graph: ConceptGraph, concept_id: str):
    matches = [c for c in graph.all_concepts() if c.id == concept_id or c.id.startswith(concept_id)]
    exact = [c for c in matches if c.id == concept_id]
    if exact:
        return exact[0]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SystemExit(f"ERREUR : aucun concept dont l'id commence par {concept_id!r}.")
    ids = ", ".join(f"{c.id[:12]} ({c.canonical_form})" for c in matches[:10])
    raise SystemExit(f"ERREUR : prefixe {concept_id!r} ambigu ({len(matches)} concepts) : {ids}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("id", help="identifiant du concept (complet ou prefixe unique)")
    parser.add_argument("canonical_form", help="nouveau nom canonique (doit deja etre un alias du concept)")
    parser.add_argument("--dry-run", action="store_true", help="n'ecrit rien, affiche seulement le changement")
    args = parser.parse_args()

    graph = ConceptGraph(paths.GRAPH_DB_PATH)
    try:
        concept = _resolve(graph, args.id)
        print(f"Concept {concept.id}")
        print(f"  type     : {concept.type}")
        print(f"  sens     : {concept.sense}")
        print(f"  alias    : {concept.aliases}")
        print(f"  nom      : {concept.canonical_form!r} -> {args.canonical_form!r}")

        if args.canonical_form == concept.canonical_form:
            print("Rien a faire : c'est deja le nom canonique.")
            return 0
        if args.canonical_form not in concept.aliases:
            print(f"ERREUR : {args.canonical_form!r} n'est pas un alias de ce concept (alias : {concept.aliases}).", file=sys.stderr)
            return 1
        if args.dry_run:
            print("[DRY-RUN] rien n'a ete ecrit.")
            return 0

        mentions_before = len(graph.mentions_of(concept.id))
        try:
            graph.rename_concept(concept.id, args.canonical_form)
        except GraphIntegrityError as exc:
            print(f"ERREUR : {exc}", file=sys.stderr)
            return 1

        after = next(c for c in graph.all_concepts() if c.id == concept.id)
        problems = []
        if after.canonical_form != args.canonical_form:
            problems.append(f"nom relu : {after.canonical_form!r}")
        if list(after.aliases) != list(concept.aliases):
            problems.append("alias modifies")
        if after.type != concept.type or after.sense != concept.sense:
            problems.append("type ou definition modifies")
        if len(graph.mentions_of(concept.id)) != mentions_before:
            problems.append("nombre de mentions modifie")
        problems += graph.check_integrity()
        if problems:
            print("ANOMALIES apres renommage :", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            return 1
        print(f"OK : renomme en {args.canonical_form!r} ; alias, type, definition et {mentions_before} mention(s) inchanges ; base integre.")
        return 0
    finally:
        graph.close()


if __name__ == "__main__":
    sys.exit(main())
