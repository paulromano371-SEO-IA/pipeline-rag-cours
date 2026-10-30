"""Étape 1 de /cours-condense : cartographie du pdf + plan condensé.

Un seul appel `claude -p` avec le prompt complet `prompts/prompt_etape1.md`.
Usage : python Etape1.py <chemin_du_livre.pdf>
"""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR / "lib"))
sys.path.insert(0, str(SCRIPTS_DIR))

from commun import RACINE_PROJET, EtapeError, call_claude, charger_plan, load_prompt, statut  # noqa: E402
from initialiser_arborescence import slugifier  # noqa: E402

NUMERO = "1"


def valider_plan(dossier: Path):
    """Renvoie le plan si `plan_cours.json` existe et est un objet avec une liste `chapitres` non vide ; sinon lève ValueError (nouvel essai)."""
    if not (dossier / "plan_cours.json").exists():
        raise ValueError("plan_cours.json absent")
    try:
        plan = charger_plan(dossier)
    except ValueError as exc:
        raise ValueError(f"plan_cours.json illisible : {exc}") from exc
    if not isinstance(plan, dict) or not isinstance(plan.get("chapitres"), list) or not plan["chapitres"]:
        raise ValueError("plan_cours.json doit être un objet contenant une liste 'chapitres' non vide")
    return plan


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage : Etape1.py <chemin_du_livre.pdf>")
        return 2
    pdf = Path(sys.argv[1]).resolve()
    if not pdf.is_file():
        statut(NUMERO, False, f"fichier introuvable : {pdf}")
        return 2

    slug = slugifier(pdf.stem)
    dossier = RACINE_PROJET / "rag_data" / "courscondense" / slug
    user_text = (
        f"Livre à condenser : {pdf}\nDossier de travail : {dossier}\n\n"
        "Tu dois aller jusqu'au bout : lire tous les lots de pages, puis écrire `plan_cours.json` avec l'outil Write. "
        "Ne t'arrête pas en cours de lecture et ne demande aucune validation : une réponse sans `plan_cours.json` écrit est un échec."
    )

    try:
        call_claude(dossier, f"etape{NUMERO}", load_prompt("prompt_etape1.md"), user_text, lambda raw: valider_plan(dossier),
                    renvoyer_erreur=True)
    except EtapeError as exc:
        statut(NUMERO, False, str(exc))
        return 1

    statut(NUMERO, True, f"plan écrit : {dossier / 'plan_cours.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
