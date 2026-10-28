"""Correction déterministe des titres de pivot.md, d'après course.tex.

Un titre de pivot.md n'est réparé que si, après deux retouches simples
(retrait des numéros de section 4.1.1 collés dans le titre, puis recollage d'un
mot coupé par un tiret « entre- prises »), il correspond à un titre de
course.tex selon la même normalisation que fidelity_check. Aucun texte n'est
inventé : le titre de remplacement existe déjà dans course.tex.
"""
from __future__ import annotations

import re

from fidelity_check import _normalize_heading
from tex_source import extract_headings

_TITRE_MD = re.compile(r"^(#{1,6})\s+(.*)$")
_NUMERO_SECTION = re.compile(r"\s+\d+(?:\.\d+)+(?=\s|$)")
_MOT_COUPE = re.compile(r"(\w)- (\w)")


def corriger_titres(pivot_text: str, course_tex: str) -> tuple[str, list[tuple[str, str]]]:
    """Retourne (pivot corrigé, liste des (ancien titre, nouveau titre))."""
    attendus = {_normalize_heading(t) for t in extract_headings(course_tex)}
    attendus.discard("")

    lignes = pivot_text.split("\n")
    corrections: list[tuple[str, str]] = []
    for idx, ligne in enumerate(lignes):
        match = _TITRE_MD.match(ligne)
        if not match:
            continue
        titre = match.group(2)
        if _normalize_heading(titre) in attendus:
            continue
        sans_numero = _NUMERO_SECTION.sub("", titre).strip()
        for candidat in (sans_numero, _MOT_COUPE.sub(r"\1\2", sans_numero)):
            if _normalize_heading(candidat) in attendus:
                lignes[idx] = f"{match.group(1)} {candidat}"
                corrections.append((titre, candidat))
                break
    return "\n".join(lignes), corrections
