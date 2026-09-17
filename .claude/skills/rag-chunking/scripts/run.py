"""Etape /rag-chunking : pivot.md -> chunks embeddables (chunks.json).

Usage:
    python run.py <pdf_condense | document_id | chemin_dossier_travail | pivot.md> [--force] [--target-tokens N] [--overlap-blocks N]

Ecrit dans <racine_projet>/rag_data/work/<document_id>/ :
    chunks.json   liste des chunks (index, texte, texte embeddé, fil d'ariane,
                  tokens, has_code)
    status.json   suivi de l'etape "chunking"
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_rag_lib"))

from chunk import chunk_markdown, DEFAULT_TARGET_TOKENS, DEFAULT_OVERLAP_BLOCKS
import paths
import status as status_lib


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=str, help="PDF condense, document_id, dossier de travail, ou pivot.md")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--target-tokens", type=int, default=DEFAULT_TARGET_TOKENS)
    parser.add_argument("--overlap-blocks", type=int, default=DEFAULT_OVERLAP_BLOCKS)
    args = parser.parse_args()

    try:
        work_dir = paths.resolve_work_dir(args.path)
    except ValueError as exc:
        print(f"ERREUR: {exc}", file=sys.stderr)
        return 1

    pivot_path = work_dir / "pivot.md"
    chunks_path = work_dir / "chunks.json"

    if not pivot_path.exists():
        print(f"ERREUR: pivot introuvable: {pivot_path} (lancez d'abord /rag-extraction)", file=sys.stderr)
        return 1

    if status_lib.is_done(work_dir, "chunking") and not args.force and chunks_path.exists():
        print(f"deja fait (chunking): {chunks_path}")
        return 0

    markdown = pivot_path.read_text(encoding="utf-8")
    chunks = chunk_markdown(markdown, target_tokens=args.target_tokens, overlap_blocks=args.overlap_blocks)

    if not chunks:
        status_lib.mark_stage(work_dir, "chunking", "failed", detail="aucun chunk produit")
        print("ERREUR: aucun chunk produit", file=sys.stderr)
        return 1

    chunks_path.write_text(json.dumps([asdict(c) for c in chunks], ensure_ascii=False), encoding="utf-8")
    status_lib.mark_stage(work_dir, "chunking", "done", n_chunks=len(chunks))

    print(f"OK: {len(chunks)} chunks -> {chunks_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
