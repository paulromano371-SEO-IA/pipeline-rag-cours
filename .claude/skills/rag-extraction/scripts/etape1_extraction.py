"""Étape 1 de /rag-extraction : PDF condensé -> pivot.md, images/, meta.json.

Usage:
    etape1_extraction.py <chemin_vers_pdf_condense> [--force] [--pages DEBUT:FIN]

Affiche "[étape 1] OK — ..." ou "[étape 1] ÉCHEC — ...".
Écrit etape1_extraction.json (compteurs et problèmes qualité), lu par l'étape 2.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from commun import ETAPE1_JSON, ecrire_json, resoudre  # noqa: E402

import argparse  # noqa: E402
import json  # noqa: E402

from convert import extract_native_pdf  # noqa: E402
from recollage_code import recoller_blocs_code  # noqa: E402
from ligature_repair import auto_repair_ligatures  # noqa: E402
from quality import assess_extraction_quality, count_control_chars, QualityIssue  # noqa: E402
import checks  # noqa: E402
import status as status_lib  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_path")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--pages", type=str, default=None, help="DEBUT:FIN (fin exclue)")
    args = parser.parse_args()

    pdf_path, document_id, work_dir, courscondense_dir = resoudre(args.pdf_path)
    pivot_path = work_dir / "pivot.md"

    if not pdf_path.exists():
        print(f"[étape 1] ÉCHEC — fichier introuvable : {pdf_path}")
        return 1

    verdict = status_lib.load_status(work_dir).get("extraction", {}).get("metadata", {}).get("verdict")
    if status_lib.is_done(work_dir, "extraction") and verdict == "ok" and not args.force and pivot_path.exists():
        print(f"[étape 1] OK — déjà fait : {pivot_path} (relancer avec --force pour refaire)")
        return 0

    pre = checks.check_extraction_input(pdf_path)
    print(pre.report())
    if not pre.ok:
        print("[étape 1] ÉCHEC — entrée invalide (voir ci-dessus)")
        return 1

    page_range = None
    if args.pages:
        start_s, end_s = args.pages.split(":")
        page_range = (int(start_s), int(end_s))

    # Dossier de travail de /cours-condense, s'il existe encore : permet de
    # reprendre formules et illustrations depuis leur source exacte.
    course_tex_path = courscondense_dir / "course.tex"
    illustrations_dir = courscondense_dir / "illustrations"
    result = extract_native_pdf(
        pdf_path, work_dir, page_range=page_range,
        course_tex_path=course_tex_path if course_tex_path.exists() else None,
        illustrations_dir=illustrations_dir if illustrations_dir.exists() else None,
    )
    quality_report = assess_extraction_quality(result)
    markdown = result.markdown

    # Blocs de code coupés aux sauts de page : recollés depuis course.tex (voir recollage_code.py).
    blocs_recolles = 0
    if course_tex_path.exists():
        markdown, recollage = recoller_blocs_code(markdown, course_tex_path.read_text(encoding="utf-8"))
        blocs_recolles = len(recollage.recolles)

    ligature_repairs = 0
    symbol_pairs_repaired = 0
    singleton_symbols_removed = 0
    if any(issue.kind == "control_char_ligature" for issue in quality_report.issues):
        repair_result = auto_repair_ligatures(markdown)
        markdown = repair_result.text
        ligature_repairs = len(repair_result.ligature_mapping)
        symbol_pairs_repaired = len(repair_result.symbol_pair_mapping)
        singleton_symbols_removed = len(repair_result.singleton_codes_removed)

        remaining = count_control_chars(markdown)
        if remaining > 0:
            quality_report.issues.append(QualityIssue(
                -1, "unresolved_control_char",
                f"{remaining} caractère(s) de contrôle restant(s) après réparation automatique "
                f"(ligature en plein mot non reconnue par le dictionnaire, ou symbole de police sans "
                f"paire ouvrant/fermant valide) — jamais deviné, jamais supprimé sans le signaler ici",
            ))

    work_dir.mkdir(parents=True, exist_ok=True)
    pivot_path.write_text(markdown, encoding="utf-8")
    (work_dir / "meta.json").write_text(
        json.dumps({"document_id": document_id, "source_pdf": str(pdf_path)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    ecrire_json(work_dir / ETAPE1_JSON, {
        "document_id": document_id,
        "page_range": list(page_range) if page_range else None,
        "pages": len(result.pages),
        "images": len(result.images),
        "n_formula_regions": result.n_formula_regions,
        "n_formula_matches_from_tex": result.n_formula_matches_from_tex,
        "illustrations_from_source": result.illustrations_from_source,
        "ligature_repairs": ligature_repairs,
        "symbol_pairs_repaired": symbol_pairs_repaired,
        "singleton_symbols_removed": singleton_symbols_removed,
        "blocs_code_recolles": blocs_recolles,
        "quality_issues": [
            {"page_number": i.page_number, "kind": i.kind, "detail": i.detail, "blocking": i.blocking}
            for i in quality_report.issues
        ],
    })

    print(f"[étape 1] OK — pivot.md écrit ({len(result.pages)} pages, {len(result.images)} images)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
