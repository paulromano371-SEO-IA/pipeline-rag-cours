"""Étape 3 de /rag-extraction : verdict, status.json et rapport final.

Usage:
    etape3_statut.py <chemin_vers_pdf_condense>

Lit etape1_extraction.json et etape2_verification.json. Affiche le rapport, puis :
    "[étape 3] OK"                      : rien de bloquant
    "[étape 3] BLOQUANT — ..." (code 2) : au moins un problème bloquant (voir le rapport)
    "[étape 3] ÉCHEC — ..." (code 1)    : sortie invalide
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from commun import ETAPE1_JSON, ETAPE2_JSON, lire_json, resoudre  # noqa: E402

import argparse  # noqa: E402

from rapport import construire_rapport  # noqa: E402
import checks  # noqa: E402
import status as status_lib  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_path")
    args = parser.parse_args()

    pdf_path, document_id, work_dir, _ = resoudre(args.pdf_path)
    etape1_path = work_dir / ETAPE1_JSON
    etape2_path = work_dir / ETAPE2_JSON
    if not etape1_path.exists() or not etape2_path.exists():
        print("[étape 3] ÉCHEC — résultats des étapes 1 et 2 absents")
        return 1

    etape1 = lire_json(etape1_path)
    etape2 = lire_json(etape2_path)
    quality_blocking = sum(1 for i in etape1["quality_issues"] if i["blocking"])
    fidelity_blocking = sum(1 for i in etape2["issues"] if i["blocking"])

    post = checks.check_output(
        "extraction", work_dir,
        quality_blocking=quality_blocking,
        fidelity_blocking=fidelity_blocking,
    )
    print(post.report())

    status_lib.mark_stage(
        work_dir, "extraction", "done",
        verdict=post.verdict, verdict_reasons=post.blocking,
        pages=etape1["pages"], images=etape1["images"],
        quality_issues=len(etape1["quality_issues"]),
        ligature_repairs=etape1["ligature_repairs"],
        symbol_pairs_repaired=etape1["symbol_pairs_repaired"],
        singleton_symbols_removed=etape1["singleton_symbols_removed"],
        formula_regions=etape1["n_formula_regions"],
        formula_matches_from_tex_source=etape1["n_formula_matches_from_tex"],
        illustrations_from_source=etape1["illustrations_from_source"],
        quality_blocking_issues=quality_blocking,
        fidelity_skipped=etape2["skipped"],
        fidelity_blocking_issues=fidelity_blocking,
        fidelity_indicative_issues=len(etape2["issues"]) - fidelity_blocking,
        heading_corrections=len(etape2["corrections"]),
    )

    print(construire_rapport(
        document_name=pdf_path.stem, document_id=document_id, work_dir=work_dir,
        etape1=etape1, etape2=etape2, page_range=etape1["page_range"],
    ))

    if quality_blocking or fidelity_blocking:
        print(f"[étape 3] BLOQUANT — {quality_blocking} qualité, {fidelity_blocking} fidélité (voir le rapport)")
        return 2
    if not post.ok:
        print("[étape 3] ÉCHEC — sortie invalide (voir ci-dessus)")
        return 1
    print("[étape 3] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
