"""Etape /rag-chunking : pivot.md -> chunks embeddables (chunks.json).

Usage:
    python run.py <pdf_condense | document_id | chemin_dossier_travail | pivot.md> [--force] [--target-tokens N] [--overlap-blocks N]

Ecrit dans <racine_projet>/rag_data/work/<document_id>/ :
    chunks.json   liste des chunks (index, texte, texte embeddé, fil d'ariane,
                  tokens, has_code)
    status.json   suivi de l'etape "chunking"
"""
from __future__ import annotations
# Garde-fou : force l'interpreteur du projet (.venv-rag), voir _rag_lib/venv_guard.py
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(next(p for p in _Path(__file__).resolve().parents if (p / ".claude" / "skills" / "_rag_lib").is_dir()) / ".claude" / "skills" / "_rag_lib"))
import venv_guard  # noqa: F401

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_rag_lib"))

from chunk import (
    chunk_markdown, drop_structural_sections, split_into_blocks,
    DEFAULT_TARGET_TOKENS, DEFAULT_OVERLAP_BLOCKS,
)
import checks
import paths
import status as status_lib


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=str, help="PDF condense, document_id, dossier de travail, ou pivot.md")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--target-tokens", type=int, default=DEFAULT_TARGET_TOKENS)
    parser.add_argument("--overlap-blocks", type=int, default=DEFAULT_OVERLAP_BLOCKS)
    parser.add_argument(
        "--keep-structural", action="store_true",
        help="conserve les sections structurelles (table des matieres, index...) au lieu de les exclure — "
        "a reserver a un livre dont une section de ce nom porte du vrai contenu",
    )
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

    pre = checks.check_input("chunking", work_dir)
    print(pre.report())
    if not pre.ok:
        return 1

    markdown = pivot_path.read_text(encoding="utf-8")
    excluded: list[tuple[str, int]] = []
    if not args.keep_structural:
        _, excluded = drop_structural_sections(split_into_blocks(markdown))
    if excluded:
        print(
            "Sections structurelles exclues (aucun chunk, donc rien d'indexe ni de relie au graphe) : "
            + ", ".join(f"« {title} » ({n} bloc(s))" for title, n in excluded)
        )
    chunks = chunk_markdown(
        markdown, target_tokens=args.target_tokens, overlap_blocks=args.overlap_blocks,
        exclude_structural=not args.keep_structural,
    )

    if not chunks:
        status_lib.mark_stage(work_dir, "chunking", "failed", detail="aucun chunk produit", verdict="bloquant", verdict_reasons=["aucun chunk produit"])
        print("ERREUR: aucun chunk produit", file=sys.stderr)
        return 2

    chunks_path.write_text(json.dumps([asdict(c) for c in chunks], ensure_ascii=False), encoding="utf-8")

    post = checks.check_output("chunking", work_dir, n_chunks=len(chunks), keep_structural=args.keep_structural)
    print(post.report())
    status_lib.mark_stage(
        work_dir, "chunking", "done" if post.ok else "failed", detail=post.detail(),
        n_chunks=len(chunks), verdict=post.verdict, verdict_reasons=post.blocking,
        keep_structural=args.keep_structural,
        structural_sections_excluded=[title for title, _ in excluded],
        structural_blocks_excluded=sum(n for _, n in excluded),
    )

    print(f"{'OK' if post.ok else 'BLOQUANT'}: {len(chunks)} chunks -> {chunks_path}")
    return 0 if post.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
