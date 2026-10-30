"""Étape 3 de /create-content : lire le dossier en entier, puis arrêter le plan.

Usage : etape3_plan.py "<expression>"
Lit rag_data/content/work/<slug>/dossier.md en entier, avec la section Cadrage, Facettes et
Voisins de plan.md, et écrit la section « Plan » de plan.md (tableau H2 : facette,
question du lecteur, chunks Sxxx, image/formule/code). Texte de l'étape : prompts/etape3.md,
copié depuis SKILL.md. Codes de sortie : 0 OK, 2 échec.
"""
from __future__ import annotations

import re
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
_sys.path.insert(0, str(_Path(__file__).resolve().parent / "lib"))

import sys
from pathlib import Path

from commun import EtapeError, call_claude, content_dir, deja_fait, load_prompt, pop_force, read_section, replace_section, statut, systeme_dossier
from etape1_facettes import fold
from gather import slugify_expression

ENTETE = "| H2 | Facette | Question du lecteur | Chunks | Image / formule / code |"
CHUNK_RE = re.compile(r"S\d{3}")
ID_DOSSIER_RE = re.compile(r"^--- \[(S\d{3})\]", re.MULTILINE)

CONTRAT_SORTIE = (
    "\n\nFORMAT DE RÉPONSE (imposé par le script, ne rien ajouter) : réponds UNIQUEMENT avec le tableau Markdown "
    f"de la section « Plan », en-tête exact « {ENTETE} », ligne de séparation, puis une ligne par H2 "
    "(l'accroche et les blocs fixes ne sont pas à lister ici). Colonne « Chunks » : identifiants Sxxx séparés "
    "par des virgules, tous présents dans DOSSIER, ou « — ». Colonne « Facette » : le numéro de la facette "
    "dans la liste NOMS DE FACETTE (ex. « 3 »), jamais son nom. Aucun texte avant ni après le tableau."
)


def nom_facette(libelle: str) -> str:
    """Nom de la facette sans son annotation entre parenthèses : « x (facette ajoutée) » → « x »."""
    return libelle.split(" (")[0].strip()


def make_parser(noms: list[str], identifiants: set[str]):
    par_cle = {fold(nom): nom for nom in noms}

    def parse(raw: str) -> str:
        lignes = [l.strip() for l in raw.splitlines() if l.strip().startswith("|")]
        if not lignes or lignes[0] != ENTETE:
            raise ValueError("en-tête du tableau absent ou différent")
        if len(lignes) < 3 or "---" not in lignes[1]:
            raise ValueError("tableau sans ligne de séparation ni données")
        sortie = [lignes[0], lignes[1]]
        for ligne in lignes[2:]:
            cellules = [c.strip() for c in ligne.strip("|").split("|")]
            if len(cellules) != 5:
                raise ValueError(f"ligne à 5 colonnes attendue : {ligne[:80]!r}")
            h2, facette, question, chunks, visuel = cellules
            if not h2 or not question or not visuel:
                raise ValueError(f"cellule vide : {ligne[:80]!r}")
            # Le numéro (1..N) désigne la facette ; le nom exact est alors celui de la liste, jamais celui du modèle.
            if facette.isdigit() and 1 <= int(facette) <= len(noms):
                nom = noms[int(facette) - 1]
            elif fold(nom_facette(facette)) in par_cle:
                nom = par_cle[fold(nom_facette(facette))]
            else:
                raise ValueError(f"facette inconnue : {facette!r}")
            sortie.append(f"| {h2} | {nom} | {question} | {chunks} | {visuel} |")
            if chunks != "—":
                cites = CHUNK_RE.findall(chunks)
                inconnus = [c for c in cites if c not in identifiants]
                if not cites or inconnus:
                    raise ValueError(f"chunks absents du dossier pour « {h2} » : {inconnus or chunks!r}")
        return "\n".join(sortie)
    return parse


def main() -> int:
    force = pop_force()
    if len(sys.argv) != 2:
        print("usage : etape3_plan.py \"<expression>\"", file=sys.stderr)
        return 2
    expression = sys.argv[1].strip()
    slug = slugify_expression(expression)
    if deja_fait("3", slug, force):
        return 0
    dossier = content_dir(slug)
    plan_path = dossier / "plan.md"
    dossier_path = dossier / "dossier.md"
    if not plan_path.exists() or not dossier_path.exists():
        statut("3", False, "plan.md ou dossier.md absent : lancer les étapes précédentes d'abord")
        return 2

    texte = plan_path.read_text(encoding="utf-8")
    cadrage = read_section(texte, "Cadrage")
    facettes_section = read_section(texte, "Facettes")
    voisins_section = read_section(texte, "Voisins")
    if not cadrage or not facettes_section:
        statut("3", False, "sections Cadrage ou Facettes absentes de plan.md")
        return 2

    dossier_texte = dossier_path.read_text(encoding="utf-8")
    identifiants = set(ID_DOSSIER_RE.findall(dossier_texte))
    if not identifiants:
        statut("3", False, "aucun identifiant [Sxxx] dans dossier.md")
        return 2

    lignes_facettes = [l for l in facettes_section.splitlines() if l.strip().startswith("|")][2:]
    noms_facettes = [nom_facette(l.strip("|").split("|")[0].strip()) for l in lignes_facettes]

    try:
        plan = call_claude(
            slug, "etape3_plan",
            system=systeme_dossier(dossier_texte),
            user_text=(
                load_prompt("etape3.md") + CONTRAT_SORTIE + "\n\n"
                "NOMS DE FACETTE (numéro : nom) : "
                + " ; ".join(f"{n}. {nom}" for n, nom in enumerate(noms_facettes, 1)) + "\n\n"
                f"EXPRESSION : {expression}\n\nSECTION CADRAGE :\n{cadrage}\n\n"
                f"SECTION FACETTES :\n{facettes_section}\n\n"
                f"SECTION VOISINS :\n{voisins_section or 'Aucun voisin.'}"
            ),
            parse=make_parser(noms_facettes, identifiants),
            dossier=dossier,
        )
    except EtapeError as exc:
        statut("3", False, f"plan non produit : {exc}")
        return 2

    plan_path.write_text(replace_section(texte, "Plan", plan), encoding="utf-8")
    h2 = len(plan.splitlines()) - 2
    statut("3", True, f"plan écrit dans plan.md : {h2} ligne(s) de H2 dans la section Plan")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
