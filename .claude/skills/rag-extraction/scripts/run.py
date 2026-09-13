"""Etape /rag-extraction : PDF condense (corpuscondense/) -> markdown pivot + images natives.

Usage:
    python run.py <chemin_vers_pdf_condense> [--force] [--pages DEBUT:FIN]

Le PDF est normalement dans <racine_projet>/corpuscondense/ (sortie de
/cours-condense), mais n'importe quel chemin de PDF fonctionne.

Ecrit dans <racine_projet>/rag_data/work/<document_id>/ (document_id derive
du nom + du contenu du PDF condense — jamais un dossier cree a cote du PDF
source) :
    pivot.md      texte structure (titres/code/prose/images)
    images/       images natives extraites
    meta.json     {"document_id", "source_pdf"}
    status.json   suivi de l'etape "extraction"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_rag_lib"))  # paths/status/quality (partages entre etapes)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # convert/tex_source/fidelity_check/ligature_repair (propres a /rag-extraction) — insere EN DERNIER pour rester prioritaire (index 0) en cas de collision de nom future avec _rag_lib

from convert import extract_native_pdf
from quality import assess_extraction_quality
from ligature_repair import auto_repair_ligatures
from fidelity_check import run_fidelity_check
import paths
import status as status_lib


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_path", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--pages", type=str, default=None, help="DEBUT:FIN (fin exclue)")
    args = parser.parse_args()

    pdf_path = args.pdf_path.resolve()
    if not pdf_path.exists():
        print(f"ERREUR: fichier introuvable: {pdf_path}", file=sys.stderr)
        return 1

    document_id = paths.document_id_from_pdf(pdf_path)
    work_dir = paths.work_dir_for(document_id)
    pivot_path = work_dir / "pivot.md"

    if status_lib.is_done(work_dir, "extraction") and not args.force and pivot_path.exists():
        print(f"deja fait (extraction): {pivot_path}")
        print(f"document_id: {document_id}")
        return 0

    page_range = None
    if args.pages:
        start_s, end_s = args.pages.split(":")
        page_range = (int(start_s), int(end_s))

    # Dossier de travail de /cours-condense pour ce meme document, s'il
    # existe encore (voir paths.courscondense_dir_for_condense_pdf) : permet
    # de reprendre les formules et illustrations depuis leur source exacte
    # plutot que de les redetecter/rasteriser depuis le PDF compile. Absent
    # sans consequence (PDF condense produit autrement, ou dossier de
    # travail nettoye depuis) : extract_native_pdf retombe alors sur son
    # comportement precedent.
    courscondense_dir = paths.courscondense_dir_for_condense_pdf(pdf_path)
    course_tex_path = courscondense_dir / "course.tex"
    illustrations_dir = courscondense_dir / "illustrations"

    result = extract_native_pdf(
        pdf_path, work_dir, page_range=page_range,
        course_tex_path=course_tex_path if course_tex_path.exists() else None,
        illustrations_dir=illustrations_dir if illustrations_dir.exists() else None,
    )
    quality_report = assess_extraction_quality(result)
    markdown = result.markdown

    ligature_repairs = 0
    if any(issue.kind == "control_char_ligature" for issue in quality_report.issues):
        markdown, mapping = auto_repair_ligatures(markdown)
        ligature_repairs = len(mapping)

    work_dir.mkdir(parents=True, exist_ok=True)
    pivot_path.write_text(markdown, encoding="utf-8")
    (work_dir / "meta.json").write_text(
        json.dumps({"document_id": document_id, "source_pdf": str(pdf_path)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fidelity_report = run_fidelity_check(
        pivot_path, work_dir / "images", courscondense_dir,
        illustrations_from_source=result.illustrations_from_source,
    )

    status_lib.mark_stage(
        work_dir, "extraction", "done",
        pages=len(result.pages), images=len(result.images),
        quality_issues=len(quality_report.issues), ligature_repairs=ligature_repairs,
        formula_regions=result.n_formula_regions,
        formula_matches_from_tex_source=result.n_formula_matches_from_tex,
        illustrations_from_source=result.illustrations_from_source,
        fidelity_skipped=fidelity_report.skipped,
        fidelity_blocking_issues=len(fidelity_report.blocking_issues),
        fidelity_indicative_issues=len(fidelity_report.issues) - len(fidelity_report.blocking_issues),
    )

    print(f"OK: {len(result.pages)} pages, {len(result.images)} images "
          f"(dont {result.n_formula_regions} zone(s) de formule detectee(s) en texte), "
          f"{len(quality_report.issues)} probleme(s) qualite, {ligature_repairs} ligature(s) reparee(s)")
    print(f"formules reprises depuis course.tex: {result.n_formula_matches_from_tex}/{result.n_formula_regions} "
          f"(le reste, si non nul : rasterise + a OCRiser par /rag-images si course.tex est absent, "
          f"sinon redescendu en prose approximative — jamais une image)")
    print(f"illustrations depuis fichiers source: {'oui' if result.illustrations_from_source else 'non (extraction native du PDF)'}")
    print(f"document_id: {document_id}")
    print(f"dossier de travail: {work_dir}")
    if quality_report.issues:
        print("Problemes qualite restants:")
        for issue in quality_report.issues:
            print(f"  page {issue.page_number} [{issue.kind}]: {issue.detail}")

    print(
        f"Controle de fidelite (syntaxe Python) : {fidelity_report.n_code_blocks_syntax_valid}/"
        f"{fidelity_report.n_code_blocks_syntax_checked} blocs de code valides"
    )
    if fidelity_report.skipped:
        print("Controle de fidelite (vs course.tex): saute (dossier de travail /cours-condense introuvable)")
    else:
        print(
            f"Controle de fidelite (vs course.tex): illustrations {fidelity_report.n_illustrations_verified}/{fidelity_report.n_illustrations_expected}, "
            f"code {fidelity_report.n_code_blocks_verified}/{fidelity_report.n_code_blocks_expected}, "
            f"formules exactes {fidelity_report.n_formulas_verified_exact}/{fidelity_report.n_formulas_expected} "
            f"(+{fidelity_report.n_formulas_as_image} en image de repli), "
            f"titres {fidelity_report.n_headings_verified}/{fidelity_report.n_headings_expected}, "
            f"figures completes {fidelity_report.n_figures_complete}/{fidelity_report.n_figures_expected}"
        )
    if fidelity_report.issues:
        print("Anomalies de fidelite:")
        for issue in fidelity_report.issues:
            marque = "BLOQUANT" if issue.blocking else "indicatif"
            print(f"  [{marque}] [{issue.category}] {issue.detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
