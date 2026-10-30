"""Étape 2a de /create-content : repérer les voisins, puis les consigner dans plan.md.

Usage : etape2a_voisins.py "<expression>" [--out-dir <dossier>]
1. Lance `scan_existing.py` (écrit voisins.json).
2. S'il y a des voisins, lit chacun en entier et consigne dans plan.md la section
   « Voisins » (appel claude -p). Sans voisin, note « aucun voisin » dans plan.md.
Texte de l'étape : prompts/etape2a.md, copié depuis SKILL.md. Codes de sortie : 0 OK, 2 échec.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
_sys.path.insert(0, str(_Path(__file__).resolve().parent / "lib"))

import json
import re
import subprocess
import sys
from pathlib import Path

from commun import EtapeError, bloc_voisins, call_claude, content_dir, deja_fait, load_prompt, pop_force, replace_section, statut
from gather import slugify_expression

SCAN = Path(__file__).resolve().parent / "lib" / "scan_existing.py"

CONTRAT_SORTIE = (
    "\n\nFORMAT DE RÉPONSE (imposé par le script, ne rien ajouter) : réponds UNIQUEMENT avec une puce Markdown "
    "par voisin de l'entrée, dans l'ordre de VOISINS, sans titre, sans sous-puce, sans texte avant ou après. "
    "Chaque puce commence exactement par « - **<slug du voisin>** : » suivi, dans la même puce, de son "
    "intention et de son angle, de son plan (H2) résumé, de ses arguments clés, de ses tournures d'accroche "
    "et de conclusion."
)


def scanner(expression: str, out_dir: str | None) -> tuple[bool, int | None]:
    """Lance scan_existing.py. Retourne (succès, nombre d'articles examinés affiché par le scan)."""
    cmd = [sys.executable, str(SCAN), expression]
    if out_dir:
        cmd += ["--out-dir", out_dir]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    m = re.search(r"Articles existants examines : (\d+)", proc.stdout)
    return proc.returncode == 0, int(m.group(1)) if m else None


def lire_voisins(slug: str, out_dir: str | None) -> list[dict]:
    chemin = (Path(out_dir) if out_dir else content_dir(slug)) / "voisins.json"
    data = json.loads(chemin.read_text(encoding="utf-8"))
    voisins = []
    for n in data.get("neighbors", []):
        texte = Path(n["article_path"]).read_text(encoding="utf-8")
        voisins.append({"slug": n["slug"], "titre": n.get("title", ""), "texte": texte})
    return voisins


def make_parser(slugs: list[str]):
    def parse(raw: str) -> str:
        lignes = [l for l in raw.strip().splitlines() if l.strip()]
        if len(lignes) != len(slugs):
            raise ValueError(f"{len(lignes)} puce(s) pour {len(slugs)} voisin(s)")
        for ligne, slug in zip(lignes, slugs):
            if not ligne.startswith(f"- **{slug}** : "):
                raise ValueError(f"puce attendue pour « {slug} » : {ligne[:60]!r}")
        return "\n".join(lignes)
    return parse


def main() -> int:
    force = pop_force()
    args = sys.argv[1:]
    out_dir = None
    if "--out-dir" in args:
        k = args.index("--out-dir")
        out_dir = args[k + 1]
        del args[k:k + 2]
    if len(args) != 1:
        print("usage : etape2a_voisins.py \"<expression>\" [--out-dir <dossier>]", file=sys.stderr)
        return 2
    expression = args[0].strip()
    slug = slugify_expression(expression)
    if not out_dir and deja_fait("2a", slug, force):
        return 0
    dossier = Path(out_dir) if out_dir else content_dir(slug)
    dossier.mkdir(parents=True, exist_ok=True)

    ok_scan, nb_examines = scanner(expression, out_dir)
    if not ok_scan:
        statut("2a", False, "scan_existing.py en échec : voisins.json non produit")
        return 2

    plan_path = dossier / "plan.md"
    texte = plan_path.read_text(encoding="utf-8") if plan_path.exists() else f"# Plan — {expression}\n"

    try:
        voisins = lire_voisins(slug, out_dir)
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        statut("2a", False, f"voisins.json illisible : {exc}")
        return 2

    if not voisins:
        plan_path.write_text(replace_section(texte, "Voisins", "Aucun voisin : page blanche, rien à éviter."), encoding="utf-8")
        statut("2a", True, "aucun voisin : noté dans plan.md")
        return 0

    try:
        section = call_claude(
            slug, "etape2a_voisins",
            system=bloc_voisins(voisins),
            user_text=load_prompt("etape2a.md") + CONTRAT_SORTIE + f"\n\nEXPRESSION : {expression}",
            parse=make_parser([v["slug"] for v in voisins]),
            dossier=dossier,
        )
    except EtapeError as exc:
        statut("2a", False, f"consignation des voisins non produite : {exc}")
        return 2

    intro = f"Lus en entier (étape 2a), {len(voisins)} voisin(s) retenu(s) sur {nb_examines} article(s) :"
    plan_path.write_text(replace_section(texte, "Voisins", f"{intro}\n\n{section}"), encoding="utf-8")
    statut("2a", True, f"{len(voisins)} voisin(s) lu(s) en entier et consignés dans plan.md (section Voisins)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
