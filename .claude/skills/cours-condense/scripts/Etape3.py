"""Étape 3 de /cours-condense : rédaction du cours condensé en français (contenu).

Un seul appel `claude -p` avec le prompt complet `prompts/prompt_etape3.md`.
Usage : python Etape3.py <chemin_du_livre.pdf>
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

NUMERO = "3"


def lancer_verifieur(script: str, *args: str) -> dict:
    """Lance un script de vérification et renvoie son rapport JSON ; lève ValueError si la sortie n'est pas du JSON."""
    verif = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    try:
        return json.loads(verif.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{script} n'a pas renvoyé de JSON (code {verif.returncode}) : {verif.stderr.strip()}") from exc


def valider_cours(dossier: Path, plan: Path):
    """Relance `verifier_glossaire.py` et `verifier_structure_course_tex.py` ; exige `"pret": true` pour les deux.

    Sinon lève ValueError (nouvel essai) avec les anomalies de celui qui échoue.
    """
    glossaire = lancer_verifieur("verifier_glossaire.py", str(dossier / "glossaire.json"), str(dossier / "course.tex"))
    if not glossaire.get("pret"):
        raise ValueError(f"glossaire non cohérent : {json.dumps(glossaire.get('anomalies', []), ensure_ascii=False)}")

    structure = lancer_verifieur("verifier_structure_course_tex.py", str(dossier / "course.tex"), str(plan))
    if not structure.get("pret"):
        raise ValueError(f"structure du cours non conforme : {json.dumps(structure.get('anomalies', []), ensure_ascii=False)}")
    return structure


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage : Etape3.py <chemin_du_livre.pdf>")
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
        call_claude(dossier, f"etape{NUMERO}", load_prompt("prompt_etape3.md"), user_text,
                    lambda raw: valider_cours(dossier, plan), max_attempts=6, renvoyer_erreur=True)
    except EtapeError as exc:
        statut(NUMERO, False, str(exc))
        return 1

    statut(NUMERO, True, f"cours rédigé et vérifié : {dossier / 'course.tex'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
