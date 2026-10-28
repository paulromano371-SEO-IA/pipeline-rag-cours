"""Étape 5 de /cours-condense : contrôle qualité + configuration pré-compilation.

Un seul appel `claude -p` avec le prompt complet `prompts/prompt_etape5.md`.
Usage : python Etape5.py <chemin_du_livre.pdf>
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

NUMERO = "5"
MARQUEUR_HYPERREF = r"\usepackage{cleveref}"


def valider_controle(dossier: Path):
    """Relance `controle_qualite.py` (il ne modifie que les doublons hyperref, idempotent) et exige `pret_a_compiler`.

    Vérifie aussi que le bloc hyperref/cleveref est présent. Sinon lève ValueError (nouvel essai).
    """
    course = dossier / "course.tex"
    controle = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "controle_qualite.py"), str(course)],
        capture_output=True, text=True, encoding="utf-8",
    )
    try:
        rapport = json.loads(controle.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"controle_qualite.py n'a pas renvoyé de JSON (code {controle.returncode}) : {controle.stderr.strip()}") from exc
    if not rapport.get("pret_a_compiler"):
        raise ValueError(f"contrôle qualité non passé : {json.dumps(rapport, ensure_ascii=False)}")

    if MARQUEUR_HYPERREF not in course.read_text(encoding="utf-8"):
        raise ValueError("bloc hyperref/cleveref absent de course.tex (appliquer_hyperref_etape5.py non exécuté)")
    return rapport


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage : Etape5.py <chemin_du_livre.pdf>")
        return 2
    pdf = Path(sys.argv[1]).resolve()
    if not pdf.is_file():
        statut(NUMERO, False, f"fichier introuvable : {pdf}")
        return 2

    slug = slugifier(pdf.stem)
    dossier = RACINE_PROJET / "rag_data" / "courscondense" / slug
    if not (dossier / "course.tex").exists():
        statut(NUMERO, False, "course.tex absent : lancer d'abord les étapes 1 à 4")
        return 2

    user_text = f"Livre à condenser : {pdf}\nDossier de travail : {dossier}"

    try:
        call_claude(dossier, f"etape{NUMERO}", load_prompt("prompt_etape5.md"), user_text, lambda raw: valider_controle(dossier),max_attempts=6, renvoyer_erreur=True)
    except EtapeError as exc:
        statut(NUMERO, False, str(exc))
        return 1

    statut(NUMERO, True, f"contrôle qualité passé, prêt à compiler : {dossier / 'course.tex'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
