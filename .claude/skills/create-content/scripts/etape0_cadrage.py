"""Étape 0 de /create-content : cadrer l'intention.

Usage : etape0_cadrage.py "<expression>"
Écrit la section « Cadrage » de rag_data/content/work/<slug>/plan.md (texte de l'étape :
prompts/etape0.md, copié depuis SKILL.md). Codes de sortie : 0 OK, 2 échec.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
_sys.path.insert(0, str(_Path(__file__).resolve().parent / "lib"))

import json
import re

from commun import EtapeError, call_claude, content_dir, deja_fait, entete_expression, load_prompt, pop_force, read_section, replace_section, statut
from etape1_facettes import fold
from gather import slugify_expression

FAMILLES = {
    "définition", "décision", "procédure", "comparaison", "causalité",
    "optimisation / diagnostic", "panorama",
}
CHAMPS = ("intention", "question_lecteur", "promesse", "hors_perimetre")

CONTRAT_SORTIE = (
    "\n\nFORMAT DE RÉPONSE (imposé par le script, ne rien ajouter) : réponds UNIQUEMENT avec un "
    "objet JSON {\"intention\": ..., \"question_lecteur\": ..., \"promesse\": ..., \"hors_perimetre\": ...}, "
    "sans texte ni balise autour. `intention` = exactement une des familles listées ci-dessus "
    "(définition, décision, procédure, comparaison, causalité, optimisation / diagnostic, panorama)."
)


def parse(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON invalide : {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("réponse JSON qui n'est pas un objet")
    for champ in CHAMPS:
        if not str(data.get(champ, "")).strip():
            raise ValueError(f"champ vide ou absent : {champ}")
    intention = str(data["intention"]).strip().lower()
    if intention not in FAMILLES:
        raise ValueError(f"famille d'intention inconnue : {data['intention']!r}")
    data["intention"] = intention
    return data


def exemples_intention() -> dict[str, str]:
    """Exemple de question donné par le texte de l'étape pour chaque famille (« qu'est-ce que… »)."""
    texte = load_prompt("etape0.md")
    return {fold(m.group(1)): " ".join(m.group(2).split())
            for m in re.finditer(r"\*([^*]+)\*\s+\(([^)]*)\)", texte)}


def render(expression: str, slug: str, data: dict) -> str:
    exemple = exemples_intention().get(fold(data["intention"]))
    intention = f"*{data['intention']}*" + (f" ({exemple})" if exemple else "")
    return (
        f"- **Expression** : « {expression} »\n"
        f"- **Slug** : `{slug}`\n"
        f"- **Intention** : {intention}.\n"
        f"- **Question du lecteur** : {data['question_lecteur'].strip()}\n"
        f"- **Promesse** : {data['promesse'].strip()}\n"
        f"- **Hors périmètre** : {data['hors_perimetre'].strip()}"
    )


def main() -> int:
    force = pop_force()
    if len(_sys.argv) != 2:
        print("usage : etape0_cadrage.py \"<expression>\"", file=_sys.stderr)
        return 2
    expression = _sys.argv[1].strip()
    slug = slugify_expression(expression)
    if deja_fait("0", slug, force):
        return 0
    folder = content_dir(slug)
    folder.mkdir(parents=True, exist_ok=True)
    plan_path = folder / "plan.md"
    texte = plan_path.read_text(encoding="utf-8") if plan_path.exists() else f"# Plan — {expression}\n"

    try:
        data = call_claude(
            slug, "etape0_cadrage",
            system=entete_expression(expression, slug) + load_prompt("etape0.md") + CONTRAT_SORTIE,
            user_text="Applique les consignes ci-dessus à l'expression indiquée.",
            parse=parse,
        )
    except EtapeError as exc:
        statut("0", False, f"cadrage non produit : {exc}")
        return 2

    plan_path.write_text(replace_section(texte, "Cadrage", render(expression, slug, data)), encoding="utf-8")
    statut("0", True, f"intention « {data['intention']} » écrite dans plan.md (section Cadrage)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
