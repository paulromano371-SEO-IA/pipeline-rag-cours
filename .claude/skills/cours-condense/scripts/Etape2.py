"""Étape 2 de /cours-condense : génération des illustrations.

Un seul appel `claude -p` avec le prompt complet `prompts/prompt_etape2.md`.
Usage : python Etape2.py <chemin_du_livre.pdf>
"""
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR / "lib"))
sys.path.insert(0, str(SCRIPTS_DIR))

from commun import RACINE_PROJET, EtapeError, call_claude, load_prompt, statut  # noqa: E402
from initialiser_arborescence import slugifier  # noqa: E402

NUMERO = "2"


def valider_illustrations(dossier: Path, plan: Path):
    """Relance `verifier_illustrations.py` et exige `"pret": true` ; sinon lève ValueError (nouvel essai)."""
    verif = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "verifier_illustrations.py"), str(dossier), str(plan)],
        capture_output=True, text=True, encoding="utf-8",
    )
    try:
        rapport = json.loads(verif.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"verifier_illustrations.py n'a pas renvoyé de JSON (code {verif.returncode}) : {verif.stderr.strip()}") from exc
    if not rapport.get("pret"):
        raise ValueError(f"illustrations non prêtes : {json.dumps(rapport.get('anomalies', []), ensure_ascii=False)}")
    return rapport


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage : Etape2.py <chemin_du_livre.pdf>")
        return 2
    pdf = Path(sys.argv[1]).resolve()
    if not pdf.is_file():
        statut(NUMERO, False, f"fichier introuvable : {pdf}")
        return 2

    slug = slugifier(pdf.stem)
    dossier = RACINE_PROJET / "rag_data" / "courscondense" / slug
    plan = dossier / "plan_cours.json"
    if not plan.exists():
        statut(NUMERO, False, "plan_cours.json absent : lancer d'abord l'étape 1")
        return 2

    user_text = f"Livre à condenser : {pdf}\nDossier de travail : {dossier}\nPlan : {plan}"

    try:
        call_claude(dossier, f"etape{NUMERO}", load_prompt("prompt_etape2.md"), user_text, lambda raw: valider_illustrations(dossier, plan),max_attempts=6, renvoyer_erreur=True)
    except EtapeError as exc:
        statut(NUMERO, False, str(exc))
        return 1

    statut(NUMERO, True, f"illustrations validées dans {dossier / 'illustrations'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
