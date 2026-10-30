"""Étape 4 de /cours-condense : mise en forme LaTeX de course.tex.

Un seul appel `claude -p` avec le prompt complet `prompts/prompt_etape4.md`.
Usage : python Etape4.py <chemin_du_livre.pdf>
"""
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR / "lib"))
sys.path.insert(0, str(SCRIPTS_DIR))

from commun import RACINE_PROJET, EtapeError, call_claude, load_prompt, statut  # noqa: E402
from initialiser_arborescence import slugifier  # noqa: E402

NUMERO = "4"
MARQUEUR_PREAMBULE = r"\lstdefinestyle{pythonstyle}"


def lancer_script(script: str, dossier: Path) -> subprocess.CompletedProcess:
    """Lance un script de l'étape 4 sur le dossier du pdf (ces scripts sont idempotents)."""
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script), str(dossier)],
        capture_output=True, text=True, encoding="utf-8",
    )


def valider_mise_en_forme(dossier: Path):
    """Vérifie que le préambule fixe est présent, que les `\\Needspace` sont insérés, et que le document est complet.

    Relance `inserer_needspace_listings.py` (idempotent) pour garantir l'état final. Sinon lève ValueError (nouvel essai).
    """
    course = dossier / "course.tex"
    texte = course.read_text(encoding="utf-8")
    if MARQUEUR_PREAMBULE not in texte:
        raise ValueError("bloc de mise en forme absent de course.tex (appliquer_preambule_etape4.py non exécuté)")
    if r"\begin{document}" not in texte or r"\end{document}" not in texte:
        raise ValueError("course.tex incomplet : \\begin{document} ou \\end{document} manquant")

    normalisation = lancer_script("normaliser_listings_etape4.py", dossier)
    if normalisation.returncode != 0:
        raise ValueError(f"normaliser_listings_etape4.py en échec (code {normalisation.returncode}) : {normalisation.stderr.strip()}")

    needspace = lancer_script("inserer_needspace_listings.py", dossier)
    if needspace.returncode != 0:
        raise ValueError(f"inserer_needspace_listings.py en échec (code {needspace.returncode}) : {needspace.stderr.strip()}")


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage : Etape4.py <chemin_du_livre.pdf>")
        return 2
    pdf = Path(sys.argv[1]).resolve()
    if not pdf.is_file():
        statut(NUMERO, False, f"fichier introuvable : {pdf}")
        return 2

    slug = slugifier(pdf.stem)
    dossier = RACINE_PROJET / "rag_data" / "courscondense" / slug
    if not (dossier / "course.tex").exists():
        statut(NUMERO, False, "course.tex absent : lancer d'abord les étapes 1 à 3")
        return 2

    user_text = f"Livre à condenser : {pdf}\nDossier de travail : {dossier}"

    try:
        call_claude(dossier, f"etape{NUMERO}", load_prompt("prompt_etape4.md"), user_text,
                    lambda raw: valider_mise_en_forme(dossier),max_attempts=6, renvoyer_erreur=True)
    except EtapeError as exc:
        statut(NUMERO, False, str(exc))
        return 1

    statut(NUMERO, True, f"mise en forme appliquée dans {dossier / 'course.tex'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
