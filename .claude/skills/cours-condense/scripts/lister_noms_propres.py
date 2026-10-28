"""
Etape 5 / point 2 bis : liste les mots accentues en majuscule initiale de la
prose de course.tex (candidats noms propres, outils, marques) et indique si
le texte anglais source les ecrit sans accent.

Ce script ne corrige rien : il produit un rapport JSON que Claude juge au cas
par cas (voir prompts/prompt_etape5.md, point 2 bis).

Usage:
    python lister_noms_propres.py <course.tex> <dossier_du_livre>

Sortie: rapport JSON sur stdout. Champ `forme_source` :
  - forme sans accent trouvee dans extraction/pages/ -> probablement a restaurer ;
  - null -> absente de la source, a juger par le contexte.
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from controle_qualite import _masquer, ligne_de  # noqa: E402

MOT_CAPITALISE_ACCENTUE = re.compile(r"\b[A-ZÀ-Ý][A-Za-zÀ-ÿ]{2,}\b")
MARQUEUR_CORPS = r"\begin{document}"


def sans_accent(mot):
    decompose = unicodedata.normalize("NFKD", mot)
    return "".join(c for c in decompose if not unicodedata.combining(c))


def charger_source(dossier):
    """Tous les mots du texte anglais source (extraction/pages/*.txt)."""
    mots = set()
    for page in (dossier / "extraction" / "pages").glob("*.txt"):
        mots.update(re.findall(r"[A-Za-zÀ-ÿ]+", page.read_text(encoding="utf-8", errors="replace")))
    return mots


def lister(texte, mots_source):
    # seule la prose du corps compte : le preambule LaTeX n'est pas lu par le lecteur
    corps = texte.split(MARQUEUR_CORPS, 1)[-1]
    prose = _masquer(corps)
    candidats = []
    for m in MOT_CAPITALISE_ACCENTUE.finditer(prose):
        mot = m.group()
        base = sans_accent(mot)
        if base == mot:
            continue  # pas d'accent : hors champ de ce point
        if mot in mots_source:
            continue  # la source ecrit deja le mot avec ses accents
        forme_source = base if base in mots_source else None
        if forme_source is None:
            continue  # sans preuve dans la source anglaise, le mot n'est pas signale
        decalage = len(texte) - len(corps)  # position du corps dans le fichier complet
        position = decalage + m.start()
        candidats.append({
            "ligne": ligne_de(texte, position),
            "mot": mot,
            "forme_source": forme_source,
            "contexte": texte[max(0, position - 50):position + 50].replace("\n", " "),
        })
    return candidats


def main():
    if len(sys.argv) != 3:
        print("Usage: python lister_noms_propres.py <course.tex> <dossier_du_livre>")
        sys.exit(1)
    course = Path(sys.argv[1])
    dossier = Path(sys.argv[2])
    texte = course.read_text(encoding="utf-8")
    candidats = lister(texte, charger_source(dossier))
    rapport = {"nb_candidats": len(candidats), "candidats": candidats}
    print(json.dumps(rapport, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
