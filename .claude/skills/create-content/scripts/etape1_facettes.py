"""Étape 1 de /create-content : facettes déduites de l'intention.

Usage : etape1_facettes.py "<expression>"
Lit la section « Cadrage » de rag_data/content/work/<slug>/plan.md, écrit la section
« Facettes » (tableau complet). Texte de l'étape : prompts/etape1.md, copié depuis
SKILL.md. Codes de sortie : 0 OK, 2 échec.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
_sys.path.insert(0, str(_Path(__file__).resolve().parent / "lib"))

import re
import unicodedata

from commun import EtapeError, call_claude, content_dir, deja_fait, entete_expression, load_prompt, pop_force, read_section, replace_section, statut
from gather import slugify_expression

CONTRAT_SORTIE = (
    "\n\nFORMAT DE RÉPONSE (imposé par le script, ne rien ajouter) : réponds UNIQUEMENT avec le "
    "tableau Markdown de la section « Facettes » : en-tête "
    "« | Facette | Sous-requête | Voisin qui la couvre | Décision | », ligne de séparation, puis une ligne par facette. "
    "Laisse la colonne « Voisin qui la couvre » vide (« — ») : elle se remplit à l'étape 2. "
    "Dans la colonne « Sous-requête », écris « étiquette::mots-clés » : étiquette = un mot court de la facette "
    "(ex. nature, usage, composants, ecosysteme, exemple, idees), suivie de « :: », puis des mots-clés de recherche "
    "séparés par des espaces, sans point d'interrogation ni formule de question. "
    "Aucun texte avant ni après le tableau."
)


def fold(text: str) -> str:
    s = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in s if not unicodedata.combining(c)).strip()


def catalogue_pour(intention: str) -> list[str]:
    """Facettes du catalogue de l'étape 1 pour l'intention, lues dans le texte de l'étape (écriture d'origine)."""
    texte = load_prompt("etape1.md")
    cle = fold(intention).split()[0]
    for ligne in texte.splitlines():
        m = re.match(r"^\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$", ligne)
        if m and fold(m.group(1)).startswith(cle):
            return [f.strip() for f in m.group(2).split("·")]
    raise ValueError(f"catalogue introuvable pour l'intention « {intention} »")


def intention_du_cadrage(cadrage: str) -> str:
    m = re.search(r"\*\*Intention\*\* : \*([^*]+)\*", cadrage)
    if not m:
        raise ValueError("intention absente de la section Cadrage")
    return m.group(1).strip()


def make_parser(facettes_attendues: list[str]):
    def parse(raw: str) -> str:
        lignes = [l.strip() for l in raw.splitlines() if l.strip().startswith("|")]
        if len(lignes) < 3 or "---" not in lignes[1]:
            raise ValueError("tableau Markdown absent ou sans ligne de séparation")
        lignes_donnees = lignes[2:]
        for ligne in lignes_donnees:
            cellules = [c.strip() for c in ligne.strip("|").split("|")]
            if len(cellules) != 4:
                raise ValueError(f"ligne à 4 colonnes attendue : {ligne[:80]!r}")
        presentes = {fold(l.strip("|").split("|")[0]) for l in lignes_donnees}
        manquantes = [f for f in facettes_attendues if f not in presentes]
        if manquantes:
            raise ValueError(f"facettes du catalogue absentes du tableau : {manquantes}")
        for ligne in lignes_donnees:
            sous_requete = ligne.strip("|").split("|")[1].strip()
            etiquette, _, mots = sous_requete.partition("::")
            if not etiquette.strip() or not mots.strip():
                raise ValueError(f"sous-requête sans « étiquette::mots-clés » : {sous_requete[:60]!r}")
        return "\n".join(lignes)
    return parse


def main() -> int:
    force = pop_force()
    if len(_sys.argv) != 2:
        print("usage : etape1_facettes.py \"<expression>\"", file=_sys.stderr)
        return 2
    expression = _sys.argv[1].strip()
    slug = slugify_expression(expression)
    if deja_fait("1", slug, force):
        return 0
    plan_path = content_dir(slug) / "plan.md"
    if not plan_path.exists():
        statut("1", False, "plan.md absent : lancer l'étape 0 d'abord")
        return 2
    texte = plan_path.read_text(encoding="utf-8")
    cadrage = read_section(texte, "Cadrage")
    if not cadrage:
        statut("1", False, "section Cadrage absente de plan.md : lancer l'étape 0 d'abord")
        return 2

    try:
        intention = intention_du_cadrage(cadrage)
        facettes = catalogue_pour(intention)
        tableau = call_claude(
            slug, "etape1_facettes",
            system=entete_expression(expression, slug) + load_prompt("etape1.md") + CONTRAT_SORTIE,
            user_text=f"SECTION CADRAGE :\n{cadrage}",
            parse=make_parser([fold(f) for f in facettes]),
        )
    except (EtapeError, ValueError) as exc:
        statut("1", False, f"facettes non produites : {exc}")
        return 2

    ligne_catalogue = f"Catalogue « {intention.capitalize()} » : {' · '.join(facettes)}."
    plan_path.write_text(replace_section(texte, "Facettes", f"{ligne_catalogue}\n\n{tableau}"), encoding="utf-8")
    statut("1", True, f"{len(facettes)} facette(s) du catalogue « {intention} » écrites dans plan.md (section Facettes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
