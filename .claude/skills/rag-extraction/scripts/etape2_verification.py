"""Étape 2 de /rag-extraction : contrôle de fidélité contre course.tex.

Usage:
    etape2_verification.py <chemin_vers_pdf_condense>

Lit pivot.md et etape1_extraction.json. Si des titres sont bloquants, les corrige
d'après course.tex (sauvegarde préalable dans pivot.avant_correction.md), puis
revérifie. Écrit etape2_verification.json, lu par l'étape 3.
Affiche "[étape 2] OK — ..." ou "[étape 2] ÉCHEC — ...".
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from commun import ETAPE1_JSON, ETAPE2_JSON, ecrire_json, lire_json, resoudre  # noqa: E402

import argparse  # noqa: E402

from correction_titres import corriger_titres  # noqa: E402
from fidelity_check import run_fidelity_check  # noqa: E402


def _serialiser(rapport, corrections: list[tuple[str, str]]) -> dict:
    return {
        "skipped": rapport.skipped,
        "n_illustrations_expected": rapport.n_illustrations_expected,
        "n_illustrations_verified": rapport.n_illustrations_verified,
        "n_code_blocks_expected": rapport.n_code_blocks_expected,
        "n_code_blocks_verified": rapport.n_code_blocks_verified,
        "n_formulas_expected": rapport.n_formulas_expected,
        "n_formulas_verified_exact": rapport.n_formulas_verified_exact,
        "n_headings_expected": rapport.n_headings_expected,
        "n_headings_verified": rapport.n_headings_verified,
        "n_code_blocks_syntax_checked": rapport.n_code_blocks_syntax_checked,
        "n_code_blocks_syntax_valid": rapport.n_code_blocks_syntax_valid,
        "n_figures_expected": rapport.n_figures_expected,
        "n_figures_complete": rapport.n_figures_complete,
        "issues": [
            {"category": i.category, "blocking": i.blocking, "detail": i.detail}
            for i in rapport.issues
        ],
        "corrections": [list(c) for c in corrections],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_path")
    args = parser.parse_args()

    _, _, work_dir, courscondense_dir = resoudre(args.pdf_path)
    pivot_path = work_dir / "pivot.md"
    etape1_path = work_dir / ETAPE1_JSON

    if not pivot_path.exists() or not etape1_path.exists():
        print("[étape 2] ÉCHEC — pivot.md ou etape1_extraction.json absent : relancer l'étape 1 avec --force")
        return 1

    etape1 = lire_json(etape1_path)
    images_dir = work_dir / "images"
    course_tex_path = courscondense_dir / "course.tex"

    def verifier():
        return run_fidelity_check(
            pivot_path, images_dir, courscondense_dir,
            illustrations_from_source=etape1["illustrations_from_source"],
            page_range_active=etape1["page_range"] is not None,
        )

    rapport = verifier()
    corrections: list[tuple[str, str]] = []
    if any(i.category == "heading" and i.blocking for i in rapport.issues) and course_tex_path.exists():
        texte = pivot_path.read_text(encoding="utf-8")
        nouveau, corrections = corriger_titres(texte, course_tex_path.read_text(encoding="utf-8"))
        if corrections:
            (work_dir / "pivot.avant_correction.md").write_text(texte, encoding="utf-8")
            pivot_path.write_text(nouveau, encoding="utf-8")
            rapport = verifier()

    ecrire_json(work_dir / ETAPE2_JSON, _serialiser(rapport, corrections))

    print(f"[étape 2] OK — fidélité vérifiée ({len(corrections)} titre(s) corrigé(s) d'après course.tex)")
    for ancien, nouveau_titre in corrections:
        print(f"  titre corrigé : « {ancien} » -> « {nouveau_titre} »")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
