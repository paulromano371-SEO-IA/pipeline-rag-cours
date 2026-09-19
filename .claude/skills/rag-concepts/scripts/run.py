"""Etape /rag-concepts : chunks.json -> concepts.json (concepts candidats par chunk).

Appelle `claude -p` headless une fois par chunk indexable (pas de cle API,
reutilise l'abonnement Claude Code). Un chunk dont l'extraction echoue
(bruit non detecte, reponse LLM inexploitable) est ignore et journalise,
sans interrompre le traitement des autres.

Usage:
    python run.py <pdf_condense | document_id | chemin_dossier_travail | chunks.json> [--force]

Ecrit dans <document>/ :
    concepts.json   {"chunk_index": N, "mentions": [{"name","canonical_form","type"}, ...]}
    status.json     suivi de l'etape "extraction_concepts"
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_rag_lib"))

from chunk import Chunk
from quality import is_noise_text
from concepts import extract_concepts, ConceptExtractionError
import paths
import status as status_lib


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=str, help="PDF condense, document_id, dossier de travail, ou chunks.json")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Supprime concepts.json et l'entree extraction_concepts de status.json avant de regenerer entierement (ex. apres un changement du prompt d'extraction).",
    )
    args = parser.parse_args()

    try:
        work_dir = paths.resolve_work_dir(args.path)
    except ValueError as exc:
        print(f"ERREUR: {exc}", file=sys.stderr)
        return 1

    chunks_path = work_dir / "chunks.json"
    concepts_path = work_dir / "concepts.json"

    if not chunks_path.exists():
        print(f"ERREUR: chunks introuvables: {chunks_path} (lancez d'abord /rag-chunking)", file=sys.stderr)
        return 1

    if args.reset:
        concepts_path.unlink(missing_ok=True)
        status = status_lib.load_status(work_dir)
        status.pop("extraction_concepts", None)
        status_lib.save_status(work_dir, status)
        print(f"reset : {concepts_path} supprime (si present), extraction_concepts retire de status.json")

    if status_lib.is_done(work_dir, "extraction_concepts") and not args.force and concepts_path.exists():
        print(f"deja fait (extraction_concepts): {concepts_path}")
        return 0

    raw_chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    chunks = [Chunk(**d) for d in raw_chunks]

    # Reprise apres interruption (Ctrl+C, crash, timeout) : si concepts.json
    # contient deja des resultats partiels d'un run precedent (status pas
    # "done", sinon on serait deja sorti ci-dessus) et que --force n'est pas
    # passe, on repart de ces resultats plutot que de tout refaire -- chaque
    # chunk deja present dans le fichier est saute. Un chunk qui avait
    # echoue (bruit ou extraction ratee) n'est PAS enregistre dans le
    # fichier : il sera naturellement retente ici, ce qui est le
    # comportement voulu pour un echec transitoire (reseau, LLM). Avec
    # --force, on repart entierement de zero comme avant.
    results = []
    processed_indices: set[int] = set()
    if concepts_path.exists() and not args.force:
        try:
            results = json.loads(concepts_path.read_text(encoding="utf-8"))
            processed_indices = {r["chunk_index"] for r in results}
        except (json.JSONDecodeError, KeyError, TypeError):
            results = []
            processed_indices = set()
        if processed_indices:
            print(f"reprise : {len(processed_indices)} chunk(s) deja traite(s), ignore(s)")

    n_skipped = 0
    n_concepts = sum(len(r["mentions"]) for r in results)
    for chunk in chunks:
        if chunk.index in processed_indices:
            continue
        if is_noise_text(chunk.text):
            n_skipped += 1
            continue
        try:
            mentions = extract_concepts(chunk.text)
        except ConceptExtractionError as exc:
            print(f"  chunk {chunk.index}: extraction ignoree ({exc})", file=sys.stderr)
            n_skipped += 1
            continue
        n_concepts += len(mentions)
        results.append({"chunk_index": chunk.index, "mentions": [asdict(m) for m in mentions]})
        # Checkpoint apres CHAQUE chunk traite avec succes (pas seulement a
        # la fin) : une interruption ne perd jamais plus d'un chunk de
        # progression, et la reprise ci-dessus s'appuie exactement sur cet
        # etat intermediaire.
        concepts_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    status_lib.mark_stage(work_dir, "extraction_concepts", "done", n_concepts=n_concepts, chunks_ignores=n_skipped)

    print(f"OK: {n_concepts} mention(s) de concept sur {len(chunks) - n_skipped} chunk(s) ({n_skipped} ignore(s))")
    print(f"concepts: {concepts_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
