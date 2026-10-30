"""Étape 2b de /create-content : croiser chaque facette avec les voisins.

Usage : etape2b_croisement.py "<expression>"
Lit la section « Facettes » de rag_data/content/work/<slug>/plan.md et les voisins de
voisins.json (étape 2a, `scan_existing.py`), puis réécrit la section « Facettes » :
colonnes « Voisin qui la couvre » et « Décision » remplies, sous-requêtes ajustées.
Texte de l'étape : prompts/etape2b.md, copié depuis SKILL.md. Codes de sortie : 0 OK, 2 échec.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
_sys.path.insert(0, str(_Path(__file__).resolve().parent / "lib"))

import json
import sys
from pathlib import Path

from commun import EtapeError, bloc_voisins, call_claude, content_dir, deja_fait, load_prompt, pop_force, read_section, replace_section, statut
from etape1_facettes import fold
from gather import slugify_expression

CONTRAT_SORTIE = (
    "\n\nFORMAT DE RÉPONSE (imposé par le script, ne rien ajouter) : réponds UNIQUEMENT avec le "
    "tableau Markdown de la section « Facettes » mise à jour : en-tête "
    "« | Facette | Sous-requête | Voisin qui la couvre | Décision | », ligne de séparation, puis une ligne par facette "
    "de l'entrée, dans le même ordre, sans en retirer aucune. Dans « Voisin qui la couvre », écris « aucun » "
    "ou le slug d'un article voisin présent dans VOISINS, suivi si besoin d'une précision entre parenthèses "
    "(ex. « logistique (fonction de lien) »). Dans « Décision », écris une phrase qui dit ce qu'on fait "
    "de la facette (par exemple : angle neuf, traitée en H2 dédié, formule réutilisée, texte neuf), jamais un mot "
    "générique seul. Aucun texte avant ni après le tableau."
)

MOTS_GENERIQUES = {"retenue", "retenu", "à traiter", "a traiter", "oui", "non"}


def lire_voisins(slug: str) -> list[dict]:
    chemin = content_dir(slug) / "voisins.json"
    data = json.loads(chemin.read_text(encoding="utf-8"))
    voisins = []
    for n in data.get("neighbors", []):
        texte = Path(n["article_path"]).read_text(encoding="utf-8")
        voisins.append({"slug": n["slug"], "titre": n.get("title", ""), "texte": texte})
    return voisins


def make_parser(facettes: list[str], slugs_voisins: set[str]):
    def parse(raw: str) -> str:
        lignes = [l.strip() for l in raw.splitlines() if l.strip().startswith("|")]
        if len(lignes) < 3 or "---" not in lignes[1]:
            raise ValueError("tableau Markdown absent ou sans ligne de séparation")
        donnees = lignes[2:]
        cellules_par_ligne = []
        for ligne in donnees:
            cellules = [c.strip() for c in ligne.strip("|").split("|")]
            if len(cellules) != 4:
                raise ValueError(f"ligne à 4 colonnes attendue : {ligne[:80]!r}")
            cellules_par_ligne.append(cellules)
        presentes = [fold(c[0]) for c in cellules_par_ligne]
        if sorted(presentes) != sorted(facettes):
            raise ValueError(f"facettes différentes de l'entrée (attendu {len(facettes)}, obtenu {len(presentes)})")
        for c in cellules_par_ligne:
            voisin, decision = c[2], c[3]
            etiquette, sep, mots = c[1].partition("::")
            if not sep or not etiquette.strip() or not mots.strip():
                raise ValueError(f"sous-requête « étiquette::mots-clés » manquante pour « {c[0]} » : {c[1]!r}")
            slug_cite = voisin.split(" (")[0].strip()
            if slug_cite not in ("aucun", "—") and slug_cite not in slugs_voisins:
                raise ValueError(f"voisin inconnu pour « {c[0]} » : {voisin!r}")
            if len(decision.split()) < 3 or fold(decision) in {fold(m) for m in MOTS_GENERIQUES}:
                raise ValueError(f"décision non exploitable pour « {c[0]} » : {decision!r}")
        return "\n".join(lignes)
    return parse


def main() -> int:
    force = pop_force()
    if len(sys.argv) != 2:
        print("usage : etape2b_croisement.py \"<expression>\"", file=sys.stderr)
        return 2
    expression = sys.argv[1].strip()
    slug = slugify_expression(expression)
    if deja_fait("2b", slug, force):
        return 0
    plan_path = content_dir(slug) / "plan.md"
    if not plan_path.exists():
        statut("2b", False, "plan.md absent : lancer les étapes précédentes d'abord")
        return 2
    texte = plan_path.read_text(encoding="utf-8")
    facettes_section = read_section(texte, "Facettes")
    if not facettes_section:
        statut("2b", False, "section Facettes absente de plan.md : lancer l'étape 1 d'abord")
        return 2

    try:
        voisins = lire_voisins(slug)
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        statut("2b", False, f"voisins.json illisible : {exc}")
        return 2

    if not voisins:
        plan_path.write_text(replace_section(texte, "Facettes", facettes_section + "\n\nAucun voisin : page blanche, rien à éviter."), encoding="utf-8")
        statut("2b", True, "aucun voisin : tableau inchangé, noté dans plan.md")
        return 0

    facettes = [fold(l.strip("|").split("|")[0]) for l in facettes_section.splitlines()
                if l.strip().startswith("|") and "---" not in l and not fold(l).startswith("| facette")]
    try:
        tableau = call_claude(
            slug, "etape2b_croisement",
            system=bloc_voisins(voisins),
            user_text=load_prompt("etape2b.md") + CONTRAT_SORTIE + "\n\n"
                      f"EXPRESSION : {expression}\n\nSECTION FACETTES :\n{facettes_section}",
            parse=make_parser(facettes, {v["slug"] for v in voisins}),
        )
    except EtapeError as exc:
        statut("2b", False, f"croisement non produit : {exc}")
        return 2

    plan_path.write_text(replace_section(texte, "Facettes", tableau), encoding="utf-8")
    statut("2b", True, f"croisement écrit dans plan.md : {len(facettes)} facette(s), {len(voisins)} voisin(s) lu(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
