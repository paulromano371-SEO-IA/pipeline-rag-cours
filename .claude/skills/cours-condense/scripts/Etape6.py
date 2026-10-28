"""Étape 6 de /cours-condense : compilation, vérification, décision et livraison.

Un seul appel `claude -p` avec le prompt complet `prompts/prompt_etape6.md`.
Usage : python Etape6.py <chemin_du_livre.pdf>
"""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR / "lib"))
sys.path.insert(0, str(SCRIPTS_DIR))

from commun import RACINE_PROJET, EtapeError, call_claude, load_prompt, statut  # noqa: E402
from initialiser_arborescence import slugifier  # noqa: E402

NUMERO = "6"


def valider_livraison(dossier: Path, slug: str):
    """Vérifie que le PDF compilé existe dans `out/` et qu'il a bien été publié dans `corpuscondense/<slug>.pdf`.

    Sinon lève ValueError (nouvel essai).
    """
    pdfs = sorted((dossier / "out").glob("*.pdf"), key=lambda p: p.stat().st_mtime)
    if not pdfs:
        raise ValueError("aucun PDF dans out/ : compiler.py n'a pas produit de PDF")

    publie = RACINE_PROJET / "corpuscondense" / f"{slug}.pdf"
    if not publie.exists():
        raise ValueError(f"PDF non publié : {publie} absent (publish_condense.py non exécuté ou en échec)")
    return publie


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage : Etape6.py <chemin_du_livre.pdf>")
        return 2
    pdf = Path(sys.argv[1]).resolve()
    if not pdf.is_file():
        statut(NUMERO, False, f"fichier introuvable : {pdf}")
        return 2

    slug = slugifier(pdf.stem)
    dossier = RACINE_PROJET / "rag_data" / "courscondense" / slug
    if not (dossier / "course.tex").exists():
        statut(NUMERO, False, "course.tex absent : lancer d'abord les étapes 1 à 5")
        return 2

    user_text = f"Livre à condenser : {pdf}\nDossier de travail : {dossier}\nSlug : {slug}"

    try:
        publie = call_claude(dossier, f"etape{NUMERO}", load_prompt("prompt_etape6.md"), user_text, lambda raw: valider_livraison(dossier, slug),max_attempts=6, renvoyer_erreur=True)
    except EtapeError as exc:
        statut(NUMERO, False, str(exc))
        return 1

    statut(NUMERO, True, f"cours compilé et publié : {publie}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
