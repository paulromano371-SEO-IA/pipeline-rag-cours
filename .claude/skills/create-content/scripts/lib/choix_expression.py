"""Choix d'une expression recommandée pour /create-content lancé sans argument.

Lit `rag_data/expressions/EXPRESSIONS.md` en lecture seule. Garde les lignes dont la colonne
« Contenu consistant probable ? » vaut « Opportunité » ou « Très probable », et propose leur
colonne « Question » (doublons retirés dans chaque catégorie). Les colonnes sont repérées par
leur nom dans l'en-tête, jamais par leur position.

Usage :
  choix_expression.py            affiche les deux listes numérotées (numérotation continue)
  choix_expression.py <numéro>   affiche la question correspondante, à passer comme expression
  choix_expression.py --serie début,fin
                                 affiche les questions à traiter à la suite, de début à fin inclus,
                                 avec leur numéro de la liste complète
Codes de sortie : 0 OK, 2 usage ou numéro hors plage.
"""
from __future__ import annotations

import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

from slugs import slugify_expression

RACINE = next(p for p in Path(__file__).resolve().parents if (p / ".claude").is_dir())
EXPRESSIONS_MD = RACINE / "rag_data" / "expressions" / "EXPRESSIONS.md"
CONTENT_WORK = RACINE / "rag_data" / "content" / "work"

COL_QUESTION = "Question"
COL_PROBA = "Contenu consistant probable ?"
CATEGORIES = ("Opportunité", "Très probable")  # ordre d'affichage
SEUIL_SIMILARITE = 0.9  # ratio difflib entre le slug de la question et un slug existant : au-dessus, déjà traitée

_SEPARATEUR_CELLULES = re.compile(r"(?<!\\)\|")  # un « | » échappé « \| » fait partie de la cellule


def slugs_existants() -> list[str]:
    """Noms des dossiers de rag_data/content/work/ (un par contenu déjà créé)."""
    if not CONTENT_WORK.is_dir():
        return []
    return sorted(d.name for d in CONTENT_WORK.iterdir() if d.is_dir())


def deja_traitee(question: str, slugs: list[str]) -> str | None:
    """Slug existant très proche de la question (même découpage que /create-content), ou None."""
    slug_question = slugify_expression(question)
    for slug in slugs:
        if SequenceMatcher(None, slug_question, slug).ratio() >= SEUIL_SIMILARITE:
            return slug
    return None


def _cellules(ligne: str) -> list[str]:
    """Découpe une ligne de tableau markdown en cellules ; « \\| » redevient « | »."""
    morceaux = _SEPARATEUR_CELLULES.split(ligne.strip())[1:-1]
    return [m.replace("\\|", "|").strip() for m in morceaux]


def lire_recommandees() -> list[tuple[str, list[str]]]:
    """[(catégorie, questions uniques dans l'ordre du fichier)], une entrée par catégorie de CATEGORIES."""
    lignes = EXPRESSIONS_MD.read_text(encoding="utf-8").splitlines()
    entete = next((_cellules(l) for l in lignes if l.startswith("| Date |")), None)
    if entete is None or COL_QUESTION not in entete or COL_PROBA not in entete:
        raise SystemExit(f"colonnes « {COL_QUESTION} » ou « {COL_PROBA} » introuvables dans {EXPRESSIONS_MD.name}")
    i_question, i_proba = entete.index(COL_QUESTION), entete.index(COL_PROBA)

    groupes: dict[str, list[str]] = {c: [] for c in CATEGORIES}
    ignorees = 0
    for ligne in lignes:
        if not re.match(r"^\| \d{4}-\d{2}-\d{2} ", ligne):  # lignes de données : date AAAA-MM-JJ
            continue
        cellules = _cellules(ligne)
        if len(cellules) != len(entete):
            ignorees += 1
            continue
        proba, question = cellules[i_proba], cellules[i_question]
        if proba in groupes and question and question not in groupes[proba]:
            groupes[proba].append(question)
    if ignorees:
        print(f"avertissement : {ignorees} ligne(s) ignorée(s), nombre de cellules inattendu", file=sys.stderr)

    # Écarte les questions déjà traitées par un contenu existant (slug très proche).
    slugs = slugs_existants()
    ecartees = 0
    for categorie in CATEGORIES:
        restantes = [q for q in groupes[categorie] if not deja_traitee(q, slugs)]
        ecartees += len(groupes[categorie]) - len(restantes)
        groupes[categorie] = restantes
    if ecartees:
        print(f"{ecartees} question(s) écartée(s) : contenu déjà existant dans rag_data/content/work", file=sys.stderr)
    return [(c, groupes[c]) for c in CATEGORIES]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows : sans cela, les accents sortent en cp1252
    groupes = lire_recommandees()
    questions = [q for _, qs in groupes for q in qs]

    if len(sys.argv) == 1:
        n = 0
        for categorie, qs in groupes:
            print(f"## {categorie}")
            if not qs:
                print("_Aucune question_")
            for q in qs:
                n += 1
                print(f"{n}. {q}")
            print()
        print("Quel numéro voulez-vous traiter, ou quelle série (--serie A,B) ?")
        return 0

    if sys.argv[1] == "--serie":
        # --serie A,B : de A à B inclus.
        plage = sys.argv[2] if len(sys.argv) == 3 else ""
        if not re.fullmatch(r"\d+,\d+", plage):
            print("usage : choix_expression.py --serie début,fin", file=sys.stderr)
            return 2
        depart, fin = (int(b) for b in plage.split(","))
        if not 1 <= depart <= fin <= len(questions):
            print(f"numéros hors plage (1 à {len(questions)}, début ≤ fin)", file=sys.stderr)
            return 2
        # Liste figée à cet instant : chaque question est traitée telle quelle, même si la liste
        # se recalcule plus tard (un article créé entre-temps peut faire disparaître une entrée).
        print(f"## Série : {fin - depart + 1} question(s), de {depart} à {fin}")
        for numero in range(depart, fin + 1):
            print(f"{numero}. {questions[numero - 1]}")
        return 0

    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        print("usage : choix_expression.py [numéro | --serie [numéro]]", file=sys.stderr)
        return 2
    choix = int(sys.argv[1])
    if not 1 <= choix <= len(questions):
        print(f"numéro hors plage (1 à {len(questions)})", file=sys.stderr)
        return 2
    print(questions[choix - 1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
