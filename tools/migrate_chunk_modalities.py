"""Migration ponctuelle : ajoute `has_image` a chaque chunk de `chunks.json`
et copie `has_formula` / `has_image` dans les metadonnees Chroma, SANS
re-embedder, ni re-chunker, ni toucher au graphe.

Pourquoi c'est sur : `chunk_markdown` est deterministe, et l'index d'un
chunk ne depend pas des drapeaux `has_*`. Le script le PROUVE pour chaque
livre avant d'ecrire quoi que ce soit : il re-decoupe `pivot.md` et exige
que index, texte, fil d'ariane, tokens, embed_text, has_code et has_formula
soient identiques a `chunks.json`. Au moindre ecart, ce livre est ignore
(rien n'est ecrit pour lui) -- les identifiants `document::index` sur
lesquels reposent Chroma et le graphe ne peuvent donc pas devenir faux.

Integrite de Chroma : seules les metadonnees des ids existants sont
mises a jour (`collection.update(ids, metadatas)`). Ni documents, ni
embeddings, ni ids ne sont touches. Apres ecriture, le script compare
l'etat avant/apres (ids, documents, embeddings octet a octet, anciennes
metadonnees conservees a l'identique, resultats de requete identiques) et
N'ECRASE LA BASE D'ORIGINE QUE SI TOUT EST IDENTIQUE.

Methode : la base d'origine n'est jamais modifiee en place. Le script
travaille sur une COPIE, la verifie, puis la substitue a l'original par
simple renommage (l'ancienne base devient la sauvegarde
`vector.backup_<horodatage>`). En cas d'ecart, l'original n'a jamais ete
touche : il n'y a donc rien a restaurer (une restauration par suppression
serait dangereuse sous Windows, ou le client Chroma verrouille ses
fichiers -- constate en test).

Usage (simulation par defaut, n'ecrit rien) :
    python migrate_chunk_modalities.py [--work-root ...] [--db-path ...]
    python migrate_chunk_modalities.py --apply
"""
from __future__ import annotations
# Garde-fou : force l'interpreteur du projet (.venv-rag), voir _rag_lib/venv_guard.py
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(next(p for p in _Path(__file__).resolve().parents if (p / ".claude" / "skills" / "_rag_lib").is_dir()) / ".claude" / "skills" / "_rag_lib"))
import venv_guard  # noqa: F401

import argparse
import subprocess
import json
import os
import shutil
import sys
import time
from dataclasses import asdict
from pathlib import Path

_RAG_LIB = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "_rag_lib"
sys.path.insert(0, str(_RAG_LIB))

import numpy as np  # noqa: E402
import paths  # noqa: E402
from chunk import chunk_markdown  # noqa: E402
from vector_store import DEFAULT_COLLECTION_NAME, get_collection  # noqa: E402

_COMPARED_KEYS = ("index", "text", "heading_trail", "token_count", "embed_text", "has_code", "has_formula")
_NEW_KEYS = ("has_formula", "has_image")


def _plan_document(work_dir: Path) -> tuple[list[dict] | None, str]:
    """(nouveaux chunks en dict, "") si le re-decoupage reproduit chunks.json
    a l'identique, sinon (None, raison)."""
    pivot, chunks_path = work_dir / "pivot.md", work_dir / "chunks.json"
    if not pivot.exists() or not chunks_path.exists():
        return None, "pivot.md ou chunks.json absent"
    old = json.loads(chunks_path.read_text(encoding="utf-8"))
    new = [asdict(c) for c in chunk_markdown(pivot.read_text(encoding="utf-8"))]
    if len(old) != len(new):
        return None, f"nombre de chunks different ({len(old)} vs {len(new)})"
    for a, b in zip(old, new):
        for k in _COMPARED_KEYS:
            if a.get(k) != b.get(k):
                return None, f"chunk {a['index']} : champ '{k}' different"
    return new, ""


def _snapshot(collection) -> dict:
    got = collection.get(include=["documents", "metadatas", "embeddings"])
    order = sorted(range(len(got["ids"])), key=lambda i: got["ids"][i])
    ids = [got["ids"][i] for i in order]
    emb = np.array([got["embeddings"][i] for i in order])
    return {
        "ids": ids,
        "documents": [got["documents"][i] for i in order],
        "metadatas": [got["metadatas"][i] for i in order],
        "embeddings": emb,
    }


def _queries(before: dict, n: int = 15) -> list[list[float]]:
    step = max(1, len(before["ids"]) // n)
    return [before["embeddings"][i].tolist() for i in range(0, len(before["ids"]), step)][:n]


def _query_ids(collection, queries: list[list[float]]) -> list[list[str]]:
    return collection.query(query_embeddings=queries, n_results=10, include=[])["ids"]


def _verify(before: dict, after: dict, q_before, q_after, expected_new: dict[str, dict]) -> list[str]:
    errors = []
    if before["ids"] != after["ids"]:
        errors.append("ids differents")
    if before["documents"] != after["documents"]:
        errors.append("documents differents")
    if before["embeddings"].shape != after["embeddings"].shape or not np.array_equal(
        before["embeddings"], after["embeddings"]
    ):
        errors.append("embeddings differents")
    for id_, mb, ma in zip(before["ids"], before["metadatas"], after["metadatas"]):
        for k, v in mb.items():
            if ma.get(k) != v:
                errors.append(f"{id_} : metadonnee '{k}' alteree ({v!r} -> {ma.get(k)!r})")
        for k, v in expected_new.get(id_, {}).items():
            if ma.get(k) != v:
                errors.append(f"{id_} : '{k}' attendu {v!r}, obtenu {ma.get(k)!r}")
        extra = set(ma) - set(mb) - set(_NEW_KEYS)
        if extra:
            errors.append(f"{id_} : cles inattendues {sorted(extra)}")
    if q_before != q_after:
        errors.append("resultats de recherche differents")
    return errors[:20]


def _worker(expected_file: Path, db_path: Path, work_copy: Path | None) -> int:
    """Travail Chroma, dans un processus a part : a sa sortie, tous les
    verrous de fichiers sont liberes (impossible de les liberer proprement
    depuis le processus qui a ouvert la base, sous Windows), ce qui permet
    au processus parent de renommer les dossiers ensuite."""
    expected: dict[str, dict] = json.loads(expected_file.read_text(encoding="utf-8"))
    collection = get_collection(db_path, DEFAULT_COLLECTION_NAME)
    before = _snapshot(collection)
    in_db = [i for i in before["ids"] if i in expected]
    print(f"Chroma : {len(before['ids'])} chunks, {len(in_db)} a mettre a jour, "
          f"{len(before['ids']) - len(in_db)} non couverts (laisses intacts)")
    if work_copy is None or not in_db:
        return 0

    q_before = _query_ids(collection, _queries(before))
    del collection
    shutil.copytree(db_path, work_copy)
    coll_copy = get_collection(work_copy, DEFAULT_COLLECTION_NAME)
    coll_copy.update(ids=in_db, metadatas=[expected[i] for i in in_db])
    after = _snapshot(coll_copy)
    errors = _verify(before, after, q_before, _query_ids(coll_copy, _queries(before)), expected)
    if os.environ.get("MIGRATE_TEST_FORCE_FAIL"):  # crochet de test uniquement
        errors = ["ECHEC SIMULE"]
    if errors:
        print("ECHEC de la verification d'integrite. La base d'origine n'a PAS ete modifiee.")
        for e in errors:
            print("  -", e)
        print(f"Copie de travail laissee pour analyse : {work_copy}")
        return 2
    print(f"Verification OK sur la copie : {len(before['ids'])} ids, documents, embeddings, "
          f"metadonnees anterieures et resultats de recherche identiques.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work-root", type=Path, default=paths.WORK_DIR)
    ap.add_argument("--db-path", type=Path, default=paths.VECTOR_DB_PATH)
    ap.add_argument("--apply", action="store_true", help="ecrit reellement (sinon simulation)")
    ap.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    ap.add_argument("--work-copy", type=Path, help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.worker:
        return _worker(args.worker, args.db_path, args.work_copy)

    plans: dict[Path, list[dict]] = {}
    for work_dir in sorted(p for p in args.work_root.iterdir() if p.is_dir()):
        new, reason = _plan_document(work_dir)
        if new is None:
            print(f"IGNORE  {work_dir.name} : {reason}")
        else:
            n_img = sum(c["has_image"] for c in new)
            print(f"OK      {work_dir.name} : {len(new)} chunks reproduits a l'identique, {n_img} avec image")
            plans[work_dir] = new
    expected = {
        f"{wd.name}::{c['index']}": {"has_formula": bool(c["has_formula"]), "has_image": bool(c["has_image"])}
        for wd, new in plans.items() for c in new
    }

    stamp = time.strftime("%Y%m%d_%H%M%S")
    work_copy = args.db_path.parent / f"{args.db_path.name}.migrating_{stamp}"
    backup = args.db_path.parent / f"{args.db_path.name}.backup_{stamp}"
    expected_file = args.db_path.parent / f"migration_expected_{stamp}.json"
    expected_file.write_text(json.dumps(expected), encoding="utf-8")
    try:
        rc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--worker", str(expected_file),
             "--db-path", str(args.db_path)] + (["--work-copy", str(work_copy)] if args.apply else []),
        ).returncode
    finally:
        expected_file.unlink(missing_ok=True)
    if rc != 0:
        return rc
    if not args.apply:
        print("SIMULATION : rien n'a ete ecrit. Relancer avec --apply.")
        return 0
    if not work_copy.exists():
        print("Rien a faire.")
        return 0

    os.replace(args.db_path, backup)
    try:
        os.replace(work_copy, args.db_path)
    except OSError:
        os.replace(backup, args.db_path)  # l'original reprend sa place
        raise
    for work_dir, new in plans.items():
        shutil.copy2(work_dir / "chunks.json", work_dir / f"chunks.json.backup_{stamp}")
        tmp = work_dir / "chunks.json.tmp"
        tmp.write_text(json.dumps(new, ensure_ascii=False), encoding="utf-8")
        tmp.replace(work_dir / "chunks.json")
    print(f"OK : base substituee, ancienne base conservee dans {backup} ; {len(plans)} chunks.json mis a jour "
          f"(sauvegardes chunks.json.backup_{stamp}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
