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
# Garde-fou : force l'interpreteur du projet (.venv-rag), voir _rag_lib/venv_guard.py
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(next(p for p in _Path(__file__).resolve().parents if (p / ".claude" / "skills" / "_rag_lib").is_dir()) / ".claude" / "skills" / "_rag_lib"))
import venv_guard  # noqa: F401

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_rag_lib"))  # paths/status/quality (partages entre etapes)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # convert/tex_source/fidelity_check/ligature_repair (propres a /rag-extraction) — insere EN DERNIER pour rester prioritaire (index 0) en cas de collision de nom future avec _rag_lib

from convert import extract_native_pdf
from quality import assess_extraction_quality, count_control_chars, QualityIssue
from ligature_repair import auto_repair_ligatures
from fidelity_check import run_fidelity_check
import checks
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
        # Meme logique de code de sortie que l'execution normale (voir plus
        # bas) : reconstruite a partir des compteurs deja persistes, pour ne
        # pas reintroduire la limite documentee dans SKILL.md ("ne peut etre
        # revrifie qu'en relancant avec --force") main tenant que ces deux
        # compteurs sont bien dans status.json.
        metadata = status_lib.load_status(work_dir).get("extraction", {}).get("metadata", {})
        has_blocking = bool(metadata.get("quality_blocking_issues")) or bool(metadata.get("fidelity_blocking_issues"))
        return 2 if has_blocking else 0

    pre = checks.check_extraction_input(pdf_path)
    print(pre.report())
    if not pre.ok:
        return 1

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
    symbol_pairs_repaired = 0
    singleton_symbols_removed = 0
    if any(issue.kind == "control_char_ligature" for issue in quality_report.issues):
        repair_result = auto_repair_ligatures(markdown)
        markdown = repair_result.text
        ligature_repairs = len(repair_result.ligature_mapping)
        symbol_pairs_repaired = len(repair_result.symbol_pair_mapping)
        singleton_symbols_removed = len(repair_result.singleton_codes_removed)

        # Reliquat APRES les trois reparations ci-dessus (ligatures en plein
        # mot jamais reconnues par le dictionnaire, ou symbole de police sans
        # paire ouvrant/fermant valide) : jamais suppose absent, toujours
        # revérifié sur le texte final — entre dans le controle qualite au
        # meme titre que control_char_ligature (voir quality.py,
        # _NEVER_BLOCKING_KINDS), jamais bloquant, jamais une perte
        # silencieuse. page_number=-1 : ce compte porte sur le document
        # fusionne dans son ensemble, plus sur une page PyMuPDF individuelle
        # (la fusion inter-pages de convert.py a deja recompose le texte a
        # ce stade).
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

    fidelity_report = run_fidelity_check(
        pivot_path, work_dir / "images", courscondense_dir,
        illustrations_from_source=result.illustrations_from_source,
        page_range_active=page_range is not None,
    )

    post = checks.check_output(
        "extraction", work_dir,
        quality_blocking=len(quality_report.blocking_issues),
        fidelity_blocking=len(fidelity_report.blocking_issues),
    )
    print(post.report())

    status_lib.mark_stage(
        work_dir, "extraction", "done",
        verdict=post.verdict, verdict_reasons=post.blocking,
        pages=len(result.pages), images=len(result.images),
        quality_issues=len(quality_report.issues), ligature_repairs=ligature_repairs,
        symbol_pairs_repaired=symbol_pairs_repaired,
        singleton_symbols_removed=singleton_symbols_removed,
        formula_regions=result.n_formula_regions,
        formula_matches_from_tex_source=result.n_formula_matches_from_tex,
        illustrations_from_source=result.illustrations_from_source,
        # Symetrique a fidelity_blocking_issues ci-dessous : sans ce compte
        # separe, savoir si une execution passee avait un probleme QUALITE
        # bloquant (voir quality.py:_is_blocking_by_default — tout sauf
        # control_char_ligature peut l'etre) exigeait de relancer le script
        # avec --force rien que pour le revoir (limite documentee dans
        # SKILL.md, "Critere de sortie exploitable").
        quality_blocking_issues=len(quality_report.blocking_issues),
        fidelity_skipped=fidelity_report.skipped,
        fidelity_blocking_issues=len(fidelity_report.blocking_issues),
        fidelity_indicative_issues=len(fidelity_report.issues) - len(fidelity_report.blocking_issues),
    )

    print(_build_report(
        document_name=pdf_path.stem, document_id=document_id, work_dir=work_dir,
        result=result, quality_report=quality_report, ligature_repairs=ligature_repairs,
        symbol_pairs_repaired=symbol_pairs_repaired, singleton_symbols_removed=singleton_symbols_removed,
        fidelity_report=fidelity_report, page_range=page_range,
    ))
    # Code de sortie 2 : extraction reussie (status.json reste "done", ce
    # n'est jamais un echec du script) mais au moins un probleme BLOQUANT
    # (qualite ou fidelite) figure dans le rapport ci-dessus — permet de
    # detecter cet etat sans reparser/recompter le tableau Markdown (voir
    # SKILL.md, "Apres execution", decision 1). Ne remplace pas la lecture
    # du rapport : lequel des deux volets est bloquant, et le detail par
    # ligne, restent uniquement dans le texte affiche.
    has_blocking = bool(quality_report.blocking_issues) or bool(fidelity_report.blocking_issues)
    return 2 if (has_blocking or not post.ok) else 0


def _build_report(
    *, document_name: str, document_id: str, work_dir: Path, result, quality_report,
    ligature_repairs: int, symbol_pairs_repaired: int, singleton_symbols_removed: int,
    fidelity_report, page_range: tuple[int, int] | None,
) -> str:
    """Rapport Markdown complet, deja pret a etre relaye tel quel — le format
    (titre, ordre des sections, distinction bloquant/indicatif) est fixe,
    sans aucune variation d'une execution a l'autre : c'est donc ce script,
    pas l'agent qui l'invoque, qui doit le produire (voir SKILL.md, section
    "Apres execution" — l'agent relaie ce texte, il ne le reconstruit pas)."""
    lines: list[str] = []
    lines.append(f"## Extraction terminée — `{document_name}`")
    lines.append("")
    lines.append(
        f"{len(result.pages)} pages, {len(result.images)} images "
        f"(dont {result.n_formula_regions} zone(s) de formule détectée(s) en texte), "
        f"{len(quality_report.issues)} problème(s) qualité, {ligature_repairs} ligature(s) réparée(s), "
        f"{symbol_pairs_repaired} guillemet(s) réparé(s), {singleton_symbols_removed} symbole(s) isolé(s) supprimé(s)."
    )
    lines.append(
        f"Formules reprises depuis `course.tex` : {result.n_formula_matches_from_tex}/{result.n_formula_regions} "
        f"(le reste, si non nul : rastérisé + à OCRiser par `/rag-nottext` si `course.tex` est absent, "
        f"sinon redescendu en prose approximative — jamais une image)."
    )
    lines.append(
        f"Illustrations depuis fichiers source : "
        f"{'oui' if result.illustrations_from_source else 'non (extraction native du PDF)'}."
    )
    lines.append("")
    lines.append(f"- `document_id` : `{document_id}`")
    lines.append(f"- dossier de travail : `{work_dir}`")

    if quality_report.issues:
        lines.append("")
        lines.append("### Problèmes qualité")
        lines.append("")
        lines.append("| Page | Type | Sévérité | Détail |")
        lines.append("|---|---|---|---|")
        for issue in quality_report.issues:
            sev = "BLOQUANT" if issue.blocking else "indicatif"
            lines.append(f"| {issue.page_number} | `{issue.kind}` | {sev} | {issue.detail} |")
        n_block = len(quality_report.blocking_issues)
        n_indic = len(quality_report.indicative_issues)
        lines.append("")
        lines.append(f"**Total** : {n_block} bloquant(s), {n_indic} indicatif(s).")
        if n_block:
            lines.append("")
            lines.append(
                "**Problème(s) bloquant(s) détecté(s) — arrête-toi avant de proposer d'enchaîner sur "
                "`/rag-nottext`**, sauf exception justifiée explicitement dans le compte-rendu. "
                "N'invente jamais de contenu pour combler une page mal extraite."
            )

    lines.append("")
    lines.append(
        f"### Contrôle de fidélité (syntaxe Python) : "
        f"{fidelity_report.n_code_blocks_syntax_valid}/{fidelity_report.n_code_blocks_syntax_checked} "
        f"blocs de code valides"
    )

    lines.append("")
    if fidelity_report.skipped:
        raison = (
            "--pages utilisé (comparaison à course.tex non fiable sur un sous-ensemble de pages)"
            if page_range is not None else "dossier de travail /cours-condense introuvable"
        )
        lines.append(f"### Contrôle de fidélité (vs course.tex) : sauté ({raison})")
    else:
        lines.append("### Contrôle de fidélité (vs course.tex)")
        lines.append("")
        lines.append(
            f"illustrations {fidelity_report.n_illustrations_verified}/{fidelity_report.n_illustrations_expected}, "
            f"code {fidelity_report.n_code_blocks_verified}/{fidelity_report.n_code_blocks_expected}, "
            f"formules exactes {fidelity_report.n_formulas_verified_exact}/{fidelity_report.n_formulas_expected}, "
            f"titres {fidelity_report.n_headings_verified}/{fidelity_report.n_headings_expected}, "
            f"figures complètes {fidelity_report.n_figures_complete}/{fidelity_report.n_figures_expected}."
        )
        if fidelity_report.issues:
            lines.append("")
            lines.append("| Sévérité | Catégorie | Détail |")
            lines.append("|---|---|---|")
            for issue in fidelity_report.issues:
                sev = "BLOQUANT" if issue.blocking else "indicatif"
                lines.append(f"| {sev} | {issue.category} | {issue.detail} |")
            n_block = len(fidelity_report.blocking_issues)
            n_indic = len(fidelity_report.issues) - n_block
            lines.append("")
            lines.append(f"**Total** : {n_block} bloquant(s), {n_indic} indicatif(s).")
            if n_block:
                lines.append("")
                lines.append(
                    "**Anomalie(s) de fidélité bloquante(s) détectée(s) — arrête-toi avant de proposer "
                    "d'enchaîner sur `/rag-nottext`**, exactement comme un problème qualité bloquant. "
                    "Nuance : si l'anomalie est une illustration présente dans `illustrations/` mais absente "
                    "de `course.tex` lui-même (jamais insérée par `/cours-condense`), ce n'est pas un défaut "
                    "de cette étape-ci — signale-le comme tel plutôt que de chercher une correction côté "
                    "`/rag-extraction`."
                )

    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
