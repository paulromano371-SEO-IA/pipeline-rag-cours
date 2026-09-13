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

    if status_lib.is_done(work_dir, "extraction_concepts") and not args.force and concepts_path.exists():
        print(f"deja fait (extraction_concepts): {concepts_path}")
        return 0

    raw_chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    chunks = [Chunk(**d) for d in raw_chunks]

    results = []
    n_skipped = 0
    n_concepts = 0
    for chunk in chunks:
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

    concepts_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    status_lib.mark_stage(work_dir, "extraction_concepts", "done", n_concepts=n_concepts, chunks_ignores=n_skipped)

    print(f"OK: {n_concepts} mention(s) de concept sur {len(chunks) - n_skipped} chunk(s) ({n_skipped} ignore(s))")
    print(f"concepts: {concepts_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
