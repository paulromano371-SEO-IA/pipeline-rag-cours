"""Rapport Markdown final de /rag-extraction, construit à partir des fichiers JSON
des étapes 1 et 2 (format fixe : l'agent relaie ce texte tel quel)."""
from __future__ import annotations


def construire_rapport(
    *, document_name: str, document_id: str, work_dir, etape1: dict, etape2: dict, page_range,
) -> str:
    lines: list[str] = []
    qualite = etape1["quality_issues"]
    fidelite = etape2["issues"]

    lines.append(f"## Extraction terminée — `{document_name}`")
    lines.append("")
    lines.append(
        f"{etape1['pages']} pages, {etape1['images']} images "
        f"(dont {etape1['n_formula_regions']} zone(s) de formule détectée(s) en texte), "
        f"{len(qualite)} problème(s) qualité, {etape1['ligature_repairs']} ligature(s) réparée(s), "
        f"{etape1['symbol_pairs_repaired']} guillemet(s) réparé(s), "
        f"{etape1['singleton_symbols_removed']} symbole(s) isolé(s) supprimé(s)."
    )
    lines.append(
        f"Formules reprises depuis `course.tex` : {etape1['n_formula_matches_from_tex']}/{etape1['n_formula_regions']} "
        f"(le reste, si non nul : rastérisé + à OCRiser par `/rag-nottext` si `course.tex` est absent, "
        f"sinon redescendu en prose approximative — jamais une image)."
    )
    lines.append(
        f"Illustrations depuis fichiers source : "
        f"{'oui' if etape1['illustrations_from_source'] else 'non (extraction native du PDF)'}."
    )
    lines.append("")
    lines.append(f"- `document_id` : `{document_id}`")
    lines.append(f"- dossier de travail : `{work_dir}`")

    if qualite:
        lines.append("")
        lines.append("### Problèmes qualité")
        lines.append("")
        lines.append("| Page | Type | Sévérité | Détail |")
        lines.append("|---|---|---|---|")
        for issue in qualite:
            sev = "BLOQUANT" if issue["blocking"] else "indicatif"
            lines.append(f"| {issue['page_number']} | `{issue['kind']}` | {sev} | {issue['detail']} |")
        n_block = sum(1 for i in qualite if i["blocking"])
        lines.append("")
        lines.append(f"**Total** : {n_block} bloquant(s), {len(qualite) - n_block} indicatif(s).")
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
        f"{etape2['n_code_blocks_syntax_valid']}/{etape2['n_code_blocks_syntax_checked']} blocs de code valides"
    )

    lines.append("")
    if etape2["skipped"]:
        raison = (
            "--pages utilisé (comparaison à course.tex non fiable sur un sous-ensemble de pages)"
            if page_range is not None else "dossier de travail /cours-condense introuvable"
        )
        lines.append(f"### Contrôle de fidélité (vs course.tex) : sauté ({raison})")
    else:
        lines.append("### Contrôle de fidélité (vs course.tex)")
        lines.append("")
        lines.append(
            f"illustrations {etape2['n_illustrations_verified']}/{etape2['n_illustrations_expected']}, "
            f"code {etape2['n_code_blocks_verified']}/{etape2['n_code_blocks_expected']}, "
            f"formules exactes {etape2['n_formulas_verified_exact']}/{etape2['n_formulas_expected']}, "
            f"titres {etape2['n_headings_verified']}/{etape2['n_headings_expected']}, "
            f"figures complètes {etape2['n_figures_complete']}/{etape2['n_figures_expected']}."
        )
        if etape2["corrections"]:
            lines.append("")
            lines.append(f"Titres corrigés automatiquement d'après `course.tex` : {len(etape2['corrections'])}.")
        if fidelite:
            lines.append("")
            lines.append("| Sévérité | Catégorie | Détail |")
            lines.append("|---|---|---|")
            for issue in fidelite:
                sev = "BLOQUANT" if issue["blocking"] else "indicatif"
                lines.append(f"| {sev} | {issue['category']} | {issue['detail']} |")
            n_block = sum(1 for i in fidelite if i["blocking"])
            lines.append("")
            lines.append(f"**Total** : {n_block} bloquant(s), {len(fidelite) - n_block} indicatif(s).")
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
