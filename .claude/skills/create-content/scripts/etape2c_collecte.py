"""Étape 2c de /create-content : collecte des sous-requêtes, vérification des lacunes, relance.

Usage : etape2c_collecte.py "<expression>"
1. Lit les sous-requêtes « facette::requête » de la section « Facettes » de plan.md.
2. Lance gather.py avec ces requêtes (code 3 = corpus sans rien sur le sujet : arrêt).
3. Appel claude -p : décide des vérifications de lacune (check_gap.py) et de la relance.
4. Lance check_gap.py ; un terme PRESENT rattaché au sujet devient une requête de relance.
5. Une seule relance de gather.py (« une seule relance de rattrapage »).
Ne modifie pas plan.md : la sortie de l'étape est le dossier produit par gather.py.
Texte de l'étape : prompts/etape2c.md, copié depuis SKILL.md. Codes de sortie : 0 OK, 2 échec, 3 corpus vide.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
_sys.path.insert(0, str(_Path(__file__).resolve().parent / "lib"))

import json
import subprocess
import sys
from pathlib import Path

from commun import EtapeError, call_claude, content_dir, deja_fait, load_prompt, pop_force, read_section, statut
from gather import slugify_expression

SCRIPTS = Path(__file__).resolve().parent / "lib"
GATHER = SCRIPTS / "gather.py"
CHECK_GAP = SCRIPTS / "check_gap.py"

CONTRAT_DECISION = (
    "\n\nFORMAT DE RÉPONSE (imposé par le script, ne rien ajouter) : réponds UNIQUEMENT avec un objet JSON "
    "{\"verifications\": [{\"facette\": ..., \"sujet\": ..., \"termes\": [...]}], \"relances\": [\"facette::requête\", ...], "
    "\"result_cap\": entier ou null}. "
    "« verifications » : un élément par lacune à vérifier avec check_gap.py (vide si aucune). "
    "« relances » : requêtes « facette::requête » à ajouter à la collecte (vide si aucune). "
    "« result_cap » : valeur de --result-cap pour la relance si la skill le demande, sinon null. Aucun texte autour."
)


def sous_requetes(section: str) -> list[str]:
    lignes = [l.strip() for l in section.splitlines() if l.strip().startswith("|")]
    requetes = []
    for ligne in lignes[2:]:
        cellules = [c.strip() for c in ligne.strip("|").split("|")]
        if len(cellules) < 2 or "::" not in cellules[1]:
            raise ValueError(f"sous-requête sans « facette::requête » : {cellules[:2]!r}")
        requetes.append(cellules[1])
    if not requetes:
        raise ValueError("aucune sous-requête dans la section Facettes")
    return requetes


def gather(expression: str, requetes: list[str], result_cap: int | None) -> tuple[int, str]:
    cmd = [sys.executable, str(GATHER), expression]
    for r in requetes:
        cmd += ["--query", r]
    if result_cap:
        cmd += ["--result-cap", str(result_cap)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return proc.returncode, proc.stdout


def check_gap(sujet: str, termes: list[str]) -> str:
    proc = subprocess.run([sys.executable, str(CHECK_GAP), sujet, *termes], capture_output=True, text=True, encoding="utf-8")
    return proc.stdout


def parse_decision(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON invalide : {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("verifications"), list) or not isinstance(data.get("relances"), list):
        raise ValueError("objet JSON avec « verifications » et « relances » attendu")
    for r in data["relances"]:
        if "::" not in str(r):
            raise ValueError(f"relance sans « facette:: » : {r!r}")
    cap = data.get("result_cap")
    if cap is not None and not isinstance(cap, int):
        raise ValueError(f"result_cap non entier : {cap!r}")
    return data


def main() -> int:
    force = pop_force()
    if len(sys.argv) != 2:
        print("usage : etape2c_collecte.py \"<expression>\"", file=sys.stderr)
        return 2
    expression = sys.argv[1].strip()
    slug = slugify_expression(expression)
    if deja_fait("2c", slug, force):
        return 0
    plan_path = content_dir(slug) / "plan.md"
    if not plan_path.exists():
        statut("2c", False, "plan.md absent : lancer les étapes précédentes d'abord")
        return 2
    facettes_section = read_section(plan_path.read_text(encoding="utf-8"), "Facettes")
    if not facettes_section:
        statut("2c", False, "section Facettes absente de plan.md : lancer l'étape 1 d'abord")
        return 2

    try:
        requetes = sous_requetes(facettes_section)
    except ValueError as exc:
        statut("2c", False, str(exc))
        return 2

    code, rapport = gather(expression, requetes, None)
    if code == 3:
        statut("2c", False, "corpus sans rien sur ce sujet : arrêt")
        return 3
    if code != 0:
        statut("2c", False, f"gather.py en échec (code {code})")
        return 2

    try:
        decision = call_claude(
            slug, "etape2c_decision",
            system=load_prompt("etape2c.md") + CONTRAT_DECISION,
            user_text=f"EXPRESSION : {expression}\n\nSECTION FACETTES :\n{facettes_section}\n\nRAPPORT DE COLLECTE :\n{rapport}",
            parse=parse_decision,
        )
    except EtapeError as exc:
        statut("2c", False, f"décision de relance non produite : {exc}")
        return 2

    gap_sorties = []
    relances_gap = []
    for v in decision["verifications"]:
        sortie = check_gap(str(v.get("sujet", "")), [str(t) for t in v.get("termes", [])])
        gap_sorties.append(sortie)
        facette = str(v.get("facette", "")).strip()
        if "PRESENT et rattache" in sortie and facette and v.get("termes"):
            relances_gap.append(f"{facette}::{' '.join(str(t) for t in v['termes'])}")

    relances = list(dict.fromkeys([str(r) for r in decision["relances"]] + relances_gap))
    if relances:
        requetes_finales = requetes + [r for r in relances if r not in requetes]
        code, rapport = gather(expression, requetes_finales, decision.get("result_cap"))
        if code != 0:
            statut("2c", False, f"relance gather.py en échec (code {code})")
            return 2

    statut("2c", True, f"{len(requetes)} sous-requête(s) collectée(s), {len(relances)} relance(s), dossier produit par gather.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
