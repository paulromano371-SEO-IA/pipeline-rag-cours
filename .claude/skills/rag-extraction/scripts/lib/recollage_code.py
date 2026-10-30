"""Recollage des blocs de code fragmentés dans pivot.md, à partir de course.tex.

Le PDF coupe parfois un bloc de code aux sauts de page : pivot.md en contient
alors plusieurs morceaux (plusieurs clôtures ```), avec parfois une phrase
orpheline au milieu et une indentation perdue. Pour chaque bloc de course.tex
qui se retrouve ainsi fragmenté, ce module remplace la zone correspondante du
pivot par le texte exact de course.tex.

Garde-fous : on n'agit que si la zone est délimitée par une clôture ouvrante
et une clôture fermante, si la similarité est au seuil, et si les lignes non
reconnues sont très peu nombreuses et courtes. Sinon le bloc est ignoré et le
texte reste intact. La prose hors blocs de code n'est jamais touchée.

Fonction pure : prend le texte du pivot et celui de course.tex, rend le
nouveau texte et un rapport. Rien n'est écrit sur le disque ici.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from fidelity_check import _normalize_code
from tex_source import extract_code_blocks

SEUIL_SIMILARITE = 0.95
MAX_LIGNES_ORPHELINES = 3
MAX_CARACTERES_ORPHELINS = 40
CLOTURE = "```"


@dataclass
class RecollageReport:
    """Ce qui a été recollé (numéros 1-based des blocs de course.tex) et ce qui a été ignoré."""

    recolles: list[int] = field(default_factory=list)
    ignores: list[int] = field(default_factory=list)


def _est_cloture(ligne: str) -> bool:
    return ligne.strip().startswith(CLOTURE)


def _sans_espaces(texte: str) -> str:
    return re.sub(r"\s+", "", texte)


def _zone_valide(lignes: list[str], debut: int, fin: int, bloc_sans_espaces: str) -> bool:
    """Vrai si chaque ligne non reconnue de la zone est un fragment du bloc (retour à la ligne,
    fin de ligne coupée), et non de la prose : rien à perdre en remplaçant la zone."""
    orphelines = [
        _sans_espaces(ligne)
        for ligne in lignes[debut:fin + 1]
        if not _est_cloture(ligne) and _sans_espaces(ligne) and _sans_espaces(ligne) not in bloc_sans_espaces
    ]
    return (
        len(orphelines) <= MAX_LIGNES_ORPHELINES
        and sum(len(texte) for texte in orphelines) <= MAX_CARACTERES_ORPHELINS
    )


def _meilleure_zone(
    lignes: list[str], bloc_attendu: str, premiere: str, derniere: str, longueur_max: int
) -> tuple[float, int, int] | None:
    """Cherche la zone (début, fin) du pivot la plus proche de `bloc_attendu`."""
    meilleur: tuple[float, int, int] | None = None
    for debut, ligne in enumerate(lignes):
        if ligne.strip() != premiere:
            continue
        for fin in range(debut + 1, min(len(lignes), debut + longueur_max)):
            if lignes[fin].strip() != derniere:
                continue
            zone = "\n".join(l for l in lignes[debut:fin + 1] if not _est_cloture(l))
            score = difflib.SequenceMatcher(None, bloc_attendu, _normalize_code(zone)).ratio()
            if meilleur is None or score > meilleur[0]:
                meilleur = (score, debut, fin)
    return meilleur


def recoller_blocs_code(pivot_text: str, course_tex: str) -> tuple[str, RecollageReport]:
    """Retourne (pivot corrigé, rapport). Le pivot est renvoyé inchangé si rien n'est sûr."""
    lignes = pivot_text.split("\n")
    rapport = RecollageReport()

    for numero, bloc in enumerate(extract_code_blocks(course_tex), start=1):
        contenu = bloc.strip("\n").split("\n")
        premiere, derniere = contenu[0].strip(), contenu[-1].strip()
        if not premiere or not derniere:
            rapport.ignores.append(numero)
            continue

        attendu = _normalize_code(bloc)
        bloc_sans_espaces = _sans_espaces(bloc)
        trouve = _meilleure_zone(lignes, attendu, premiere, derniere, 3 * len(contenu) + 50)
        if trouve is None or trouve[0] < SEUIL_SIMILARITE:
            rapport.ignores.append(numero)
            continue

        _, debut, fin = trouve
        # Une phrase de prose peut être remontée juste après la clôture ouvrante, avant la
        # première ligne du bloc : la zone du bloc paraît propre, mais la clôture ne l'est pas.
        ouverture = debut - 1
        while ouverture >= 0 and debut - ouverture <= MAX_LIGNES_ORPHELINES and not _est_cloture(lignes[ouverture]):
            ouverture -= 1
        avec_prefixe = (
            0 <= ouverture < debut - 1
            and _est_cloture(lignes[ouverture])
            and sum(_est_cloture(l) for l in lignes[:ouverture]) % 2 == 0
        )
        if avec_prefixe:
            fermeture_ok = fin + 1 < len(lignes) and _est_cloture(lignes[fin + 1])
            zone_sans_cloture = not any(_est_cloture(l) for l in lignes[ouverture + 1:fin + 1])
            if not (fermeture_ok and zone_sans_cloture and _zone_valide(lignes, ouverture + 1, fin, bloc_sans_espaces)):
                rapport.ignores.append(numero)
                continue
            # Les lignes de prose sortent du bloc : elles sont placées avant la clôture ouvrante.
            prose_remontee = lignes[ouverture + 1:debut]
            lignes[ouverture:fin + 1] = [*prose_remontee, CLOTURE, *contenu]
            rapport.recolles.append(numero)
            continue

        clotures_zone = sum(_est_cloture(l) for l in lignes[debut:fin + 1])
        deja_propre = clotures_zone == 0 and _normalize_code("\n".join(lignes[debut:fin + 1])) == attendu
        if deja_propre:
            continue

        # La zone doit être entre une clôture ouvrante (juste avant) et une fermante (juste après),
        # et contenir un nombre pair de clôtures intermédiaires : on ne touche ainsi jamais à la prose.
        ouverture_ok = debut > 0 and _est_cloture(lignes[debut - 1])
        fermeture_ok = fin + 1 < len(lignes) and _est_cloture(lignes[fin + 1])
        if not (ouverture_ok and fermeture_ok and clotures_zone % 2 == 0):
            rapport.ignores.append(numero)
            continue
        if not _zone_valide(lignes, debut, fin, bloc_sans_espaces):
            rapport.ignores.append(numero)
            continue

        lignes[debut:fin + 1] = contenu
        rapport.recolles.append(numero)

    return "\n".join(lignes), rapport
