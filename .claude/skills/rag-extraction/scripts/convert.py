"""Conversion d'un PDF (cours condensé) vers le format pivot (Markdown) +
extraction native des illustrations.

Approche : extraction programmatique via PyMuPDF plutôt que par vision LLM
page par page — rapide, gratuite, et suffisante pour la majorité des PDF
numériques. Les images sont récupérées comme objets natifs du PDF (jamais un
rendu écran rasterisé), ce qui préserve leur résolution d'origine. Le gate
qualité (`quality.py`) repère ensuite les pages où cette extraction échoue
(texte quasi absent, image basse résolution...) pour un traitement différencié
plutôt que de tout faire passer par un LLM.

Détection code vs prose : sur les PDF issus de LaTeX, les polices sont
souvent des Type3 sans programme de police exploitable (pas de nom ni de
flags fiables) — le nom de la police ne peut donc pas servir de critère. On
détecte à la place les polices à chasse fixe par la régularité de l'avance
horizontale entre caractères consécutifs (coefficient de variation bas),
un critère indépendant du nom de la police.
"""

from __future__ import annotations

import itertools
import re
import shutil
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import pymupdf

import difflib

from spellchecker import SpellChecker

from tex_source import extract_display_segments, extract_illustration_basenames

_MAX_HEADING_LEVELS = 4
_CODE_FONT_MIN_SAMPLES = 20
_CODE_FONT_MAX_CV = 0.20
_CODE_LINE_MIN_RATIO = 0.9
# Une vraie ligne de code (une fois un eventuel numero de ligne retire) est
# quasi-integralement en police(s) a chasse fixe — verifie empiriquement,
# ratio 1.0 dans tous les cas observes. Un ratio bas plus permissif (0.6)
# laissait passer un faux positif : la fin d'une phrase de prose repliee sur
# sa propre ligne PDF, contenant un seul mot en `\texttt{...}` assez long
# pour depasser 60% des caracteres de cette ligne precise (verifie
# empiriquement : "l'aide de Html2TextTransformer :", ratio 0.636), fusionnee
# a tort avec le bloc de code reel qui suivait (_merge_contiguous_code_runs
# ne s'arrete qu'aux elements non-code). Une marge large (0.9) reste
# confortable sous le ratio 1.0 du vrai code tout en rejetant ce cas.


# pdflatex (sans le package `upquote`) substitue les apostrophes/guillemets
# droits du code source par leurs equivalents typographiques courbes a la
# compilation — un artefact de rendu du PDF lui-meme (verifie empiriquement :
# `course.tex`, la verite terrain, a bien des guillemets droits), jamais du
# contenu reel du code. Jamais syntaxiquement valides en Python, ces
# caracteres ne peuvent apparaitre QUE comme cet artefact dans du code
# classifie — la substitution inverse est donc sans risque, contrairement a
# une substitution generale qui alterait aussi les guillemets typographiques
# voulus de la prose.
_CURLY_QUOTES = {
    "‘": "'", "’": "'",
    "“": '"', "”": '"',
}
_LINE_NUMBER_RE = re.compile(r"^\d{1,4}$")

# Dictionnaire français hors ligne, même mécanisme que
# `ligature_repair.py` — utilisé pour distinguer une césure automatique de
# fin de ligne pdflatex (trait d'union purement typographique, à retirer)
# d'un vrai mot composé qui tomberait par coïncidence sur la même frontière
# de ligne (trait d'union à conserver), voir `_join_wrapped_lines`.
_spell = SpellChecker(language="fr")
_LETTERS_RE = r"[^\W\d_]"
_HYPHENATION_BREAK_RE = re.compile(rf"({_LETTERS_RE}+)-$")
_WORD_START_RE = re.compile(rf"^({_LETTERS_RE}+)")

# Espaces/tabulations/retours a la ligne ASCII usuels — jamais `str.strip()`
# nu, ni `\s` dans une regex sur du texte issu du PDF : Python (et le module
# `re`) traite aussi U+001C-U+001F ("separateurs d'information" ASCII) comme
# des espaces (propriete Unicode White_Space, heritee de leur usage
# historique), alors que ce sont exactement des codes de police candidats a
# etre une ligature cassee (ff/fi/fl/ffi/ffl — voir ligature_repair.py, le
# code de controle depend de la police, aucun standard fixe). Un `.strip()`
# nu en tete/fin d'un titre, d'une ligne de code ou de prose supprimerait
# alors silencieusement la ligature avant meme d'atteindre la reparation —
# verifie empiriquement (un titre de chapitre commencant par la ligature
# "fi" perdait purement et simplement son premier caractere : "fidélité" ->
# "délité"). Utiliser cette liste explicite plutot que `\s`/`.strip()` nu
# partout ou du texte extrait du PDF est manipule.
_STRIP_CHARS = " \t\n\r"

# Polices mathématiques standard de la distribution TeX (Computer Modern),
# utilisées par pdflatex par défaut avec amsmath/amssymb/amsfonts — le
# préambule fixe de /cours-condense (appliquer_preambule_etape4.py) charge
# ces packages sans package de police alternatif, donc TOUT PDF de
# corpuscondense/ (sortie de /cours-condense) utilise ces noms de police
# pour les maths, quel que soit le livre source. CMMI/CMSY/CMEX ne servent
# JAMAIS à autre chose qu'à du contenu mathématique dans une compilation
# LaTeX standard — un signal fiable, pas une heuristique statistique.
_MATH_FONT_PREFIXES = ("CMMI", "CMSY", "CMEX")
# CMR (romain) apparaît aussi dans les formules (chiffres, parenthèses,
# "="), mais aussi potentiellement dans du texte de corps — il ne compte
# comme "compatible formule" que si la ligne contient par ailleurs au moins
# une police strictement mathématique (_MATH_FONT_PREFIXES) : une ligne de
# prose mélange toujours une police de corps de texte (jamais seulement
# CM*) avec une éventuelle variable isolée en CMMI, alors qu'une ligne de
# formule pure n'utilise jamais que ces 4 familles de police.
_MATH_ADJACENT_PREFIXES = _MATH_FONT_PREFIXES + ("CMR",)
_FORMULA_MERGE_PAD = 6.0  # points ; fusionne les fragments d'une meme formule
_FORMULA_RENDER_ZOOM = 6.0  # facteur de zoom pour la rasterisation (~432 dpi)
_FORMULA_RECT_MARGIN = 3.0  # points ; marge anti-clipping fine autour du rectangle
# Marge verticale genereuse (haut ET bas), en plus de _FORMULA_RECT_MARGIN :
# un denominateur de fraction ou la limite d'un grand operateur (somme,
# union, intersection) peut ne jamais etre classifie "formule" lui-meme
# (ex. melange avec du texte en \text{...}) tout en faisant visuellement
# partie de la meme expression juste au-dessus/en-dessous — verifie
# empiriquement (troncature du denominateur "card(Omega)" d'une fraction).
# Plutot que de fiabiliser parfaitement la classification de chaque
# sous-element (fragile), on elargit simplement le rendu d'environ une
# hauteur de ligne de chaque cote ; le garde-fou de largeur ci-dessous reste
# la protection principale contre une capture trop large.
_FORMULA_VERTICAL_PAD = 14.0
# Largeur max plausible pour UNE formule isolee (points PDF) : au-dela, le
# rectangle fusionne (union des bbox du run) s'etend presque sur toute la
# largeur utile de la page — signe que le run contient des mentions eparses
# de part et d'autre d'une meme ligne (pres de la marge gauche et de la
# marge droite a la fois), donc que le rectangle englobe forcement du texte
# de prose intercale entre elles, meme si cette prose n'a jamais ete
# classifiee "formule" (verifie empiriquement : une image de 488pt de large
# capturait deux phrases completes autour de mentions Cp isolees). Au-dela
# de ce seuil, on renonce a l'image et on garde le texte brut du run.
_FORMULA_MAX_WIDTH = 300.0


@dataclass
class ExtractedImage:
    path: Path
    page_number: int
    width: int
    height: int
    ext: str


@dataclass
class PageExtraction:
    page_number: int
    markdown: str
    images: list[ExtractedImage] = field(default_factory=list)
    char_count: int = 0


@dataclass
class ExtractionResult:
    source_path: Path
    pages: list[PageExtraction]
    markdown: str
    images: list[ExtractedImage]
    n_formula_regions: int = 0
    # Parmi `n_formula_regions`, nombre de zones effectivement appariees a
    # une formule de course.tex (voir match_formula_runs_to_tex) et donc
    # recopiees telles quelles (verite terrain) plutot que rasterisees + a
    # OCRiser plus tard par /rag-nottext. Les zones non appariees (course.tex
    # absent, ou contexte de prose voisin ne correspondant a aucune formule
    # source — souvent un faux positif du detecteur PDF, voir SKILL.md) sont
    # rasterisees individuellement, sans jamais degrader les zones deja
    # correctement appariees.
    n_formula_matches_from_tex: int = 0
    # Meme principe pour les illustrations : vrai si les images natives du
    # PDF ont ete remplacees par une copie directe des PNG d'origine
    # (illustrations/*.png, noms semantiques) plutot que par l'extraction
    # brute (page_NNN_img_NN.ext).
    illustrations_from_source: bool = False


@dataclass
class _Element:
    y: float
    kind: str  # "heading" | "code" | "prose" | "image" | "formula"
    group_id: object
    text: str
    heading_level: int | None = None
    bbox: tuple[float, float, float, float] | None = None  # formule uniquement


def _span_text(span: dict) -> str:
    """Reconstruit le texte d'un span issu de `get_text("rawdict")`, qui n'a
    pas de clé "text" directe (contrairement à `get_text("dict")`) — juste
    une liste `"chars"` par caractère."""
    return "".join(ch["c"] for ch in span.get("chars", []))


def _heading_level_map(pages_raw: dict[int, dict]) -> dict[float, int]:
    """Construit un mapping taille de police -> niveau de titre, en déduisant
    la taille du corps de texte comme la taille la plus fréquente du document."""
    sizes: list[float] = []
    for raw in pages_raw.values():
        for block in raw["blocks"]:
            if block["type"] != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    if _span_text(span).strip(_STRIP_CHARS):
                        sizes.append(round(span["size"], 1))

    if not sizes:
        return {}

    body_size = statistics.mode(sizes)
    distinct_larger = sorted({s for s in sizes if s > body_size + 0.5}, reverse=True)
    return {size: i + 1 for i, size in enumerate(distinct_larger[:_MAX_HEADING_LEVELS])}


def _classify_code_fonts(
    pages_raw: dict[int, dict],
    *,
    min_samples: int = _CODE_FONT_MIN_SAMPLES,
    max_cv: float = _CODE_FONT_MAX_CV,
) -> set[str]:
    """Repère les polices à chasse fixe par la régularité de l'avance
    horizontale entre caractères consécutifs (rapportée à la taille de
    police), sans dépendre du nom de la police ni de son programme embarqué
    (souvent absent sur les polices Type3 issues de LaTeX)."""
    advances: dict[str, list[float]] = {}

    for raw in pages_raw.values():
        for block in raw["blocks"]:
            if block["type"] != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    font = span["font"]
                    size = span["size"] or 1.0
                    prev_x0: float | None = None
                    for ch in span.get("chars", []):
                        x0 = ch["bbox"][0]
                        if prev_x0 is not None:
                            advance = (x0 - prev_x0) / size
                            if 0 < advance < 3:
                                advances.setdefault(font, []).append(advance)
                        prev_x0 = x0

    code_fonts: set[str] = set()
    for font, values in advances.items():
        if len(values) < min_samples:
            continue
        mean = statistics.fmean(values)
        if mean <= 0:
            continue
        cv = statistics.pstdev(values) / mean
        if cv <= max_cv:
            code_fonts.add(font)
    return code_fonts


def _estimate_code_char_width(pages_raw: dict[int, dict], code_fonts: set[str]) -> float:
    """Largeur moyenne (en points) d'un caractère des polices de code —
    nécessaire pour convertir un décalage horizontal de ligne en nombre
    d'espaces d'indentation (voir `_reconstruct_code_indentation`) : le
    package `listings` rend l'indentation Python par un décalage de
    position du curseur, jamais par de vrais caractères espace en tête de
    ligne (vérifié empiriquement — voir `_line_to_element`)."""
    advances: list[float] = []
    for raw in pages_raw.values():
        for block in raw["blocks"]:
            if block["type"] != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    if span["font"] not in code_fonts:
                        continue
                    size = span["size"] or 1.0
                    prev_x0: float | None = None
                    for ch in span.get("chars", []):
                        x0 = ch["bbox"][0]
                        if prev_x0 is not None:
                            advance = x0 - prev_x0
                            if 0 < advance < size * 3:
                                advances.append(advance)
                        prev_x0 = x0
    if not advances:
        return 0.0
    return statistics.median(advances)


def _reconstruct_code_indentation(elements: list[_Element], char_width: float) -> list[_Element]:
    """Reconstruit l'indentation Python perdue (voir
    `_estimate_code_char_width`) en préfixant chaque ligne de code d'un
    nombre d'espaces proportionnel à son décalage horizontal par rapport à
    la ligne la MOINS indentée du même bloc — jamais une référence globale
    au document entier : deux `lstlisting` distincts peuvent démarrer leur
    contenu à des profondeurs sémantiques différentes (un extrait montrant
    directement un corps de fonction, par exemple), donc seule la ligne la
    moins indentée DU MEME BLOC sert de référence fiable pour ce bloc.

    Appelée après `_merge_contiguous_code_runs` (mêmes group_id fusionnés),
    jamais avant : la référence de moindre indentation doit être calculée
    sur le bloc déjà complet, pas sur un fragment encore éclaté."""
    if char_width <= 0:
        return elements
    result: list[_Element] = []
    i = 0
    n = len(elements)
    while i < n:
        if elements[i].kind != "code":
            result.append(elements[i])
            i += 1
            continue
        gid = elements[i].group_id
        j = i
        run: list[_Element] = []
        while j < n and elements[j].kind == "code" and elements[j].group_id == gid:
            run.append(elements[j])
            j += 1

        base_x0 = min(e.bbox[0] for e in run if e.bbox)
        for e in run:
            if not e.bbox:
                result.append(e)
                continue
            indent = max(0, round((e.bbox[0] - base_x0) / char_width))
            new_text = (" " * indent + e.text) if indent else e.text
            result.append(_Element(
                y=e.y, kind=e.kind, group_id=e.group_id, text=new_text,
                heading_level=e.heading_level, bbox=e.bbox,
            ))
        i = j
    return result


def _insert_blank_code_lines(elements: list[_Element]) -> list[_Element]:
    """Réinsère les lignes vides internes à un bloc de code (séparateurs de
    lisibilité entre fonctions/sections, convention PEP8) — perdues car une
    ligne sans aucun caractère n'a rien à extraire du flux PDF (aucun span,
    donc `_line_to_element` ne produit jamais d'élément pour elle).

    Détecte un écart vertical anormalement grand entre deux lignes de code
    consécutives DU MÊME BLOC (un multiple de l'écart typique observé DANS
    CE BLOC — jamais une valeur fixe globale, la taille de police pouvant
    varier) comme la signature d'une ou plusieurs lignes vides sautées, et
    insère un élément de texte vide par tranche d'écart supplémentaire.

    Appelée après `_reconstruct_code_indentation` (même regroupement par
    `group_id` déjà fusionné) — l'ordre entre les deux n'a pas d'importance
    l'une envers l'autre (aucune ne dépend du texte déjà indenté ou non),
    mais toutes deux dépendent de `_merge_contiguous_code_runs`."""
    result: list[_Element] = []
    i = 0
    n = len(elements)
    while i < n:
        if elements[i].kind != "code":
            result.append(elements[i])
            i += 1
            continue
        gid = elements[i].group_id
        j = i
        run: list[_Element] = []
        while j < n and elements[j].kind == "code" and elements[j].group_id == gid:
            run.append(elements[j])
            j += 1

        gaps = [run[k + 1].y - run[k].y for k in range(len(run) - 1)]
        typical_gap = statistics.median(gaps) if gaps else None

        for k, e in enumerate(run):
            result.append(e)
            if typical_gap and typical_gap > 0 and k + 1 < len(run):
                gap = run[k + 1].y - e.y
                extra_lines = round(gap / typical_gap) - 1
                for _ in range(max(0, extra_lines)):
                    result.append(_Element(y=e.y, kind="code", group_id=gid, text=""))
        i = j
    return result


def _classify_line_number_fonts(
    pages_raw: dict[int, dict],
    code_fonts: set[str],
    *,
    min_samples: int = 5,
    size_ratio_max: float = 0.75,
) -> set[str]:
    """Repère la ou les polices utilisées exclusivement pour les numéros de
    ligne du package `listings` (`numberstyle=\\tiny\\color{gray}`, voir
    `cours-condense/scripts/appliquer_preambule_etape4.py`) : une telle
    police ne rend jamais que des chiffres, à une taille nettement plus
    petite que celle du code (`basicstyle=\\small`) — un signal typographique
    fiable, contrairement à une détection basée sur la VALEUR des chiffres
    (ambiguë : une vraie sortie de code peut coïncider avec une suite
    croissante plausible, vérifié empiriquement — `list[2]` puis `len(list)`
    affichant `6` puis `11` a été pris à tort pour une numérotation de ligne
    lors d'une première tentative de correction par heuristique de valeur).

    Sans cette distinction par police, un numéro de ligne qui atterrit sur
    sa propre ligne PDF (au lieu de rester en préfixe de la ligne de code
    qu'il numérote) se glisse comme fragment de code à part entière —
    vérifié empiriquement (`self.greetings = [...,\\n8\\n"hola", "yo"]`, où
    `8` est un numéro de ligne, pas du code)."""
    digits_only: dict[str, bool] = {}
    sizes_by_font: dict[str, list[float]] = {}

    for raw in pages_raw.values():
        for block in raw["blocks"]:
            if block["type"] != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    text = _span_text(span).strip(_STRIP_CHARS)
                    if not text:
                        continue
                    font = span["font"]
                    digits_only.setdefault(font, True)
                    sizes_by_font.setdefault(font, []).append(span["size"])
                    if not text.isdigit():
                        digits_only[font] = False

    code_sizes = [
        size
        for font, sizes in sizes_by_font.items()
        if font in code_fonts
        for size in sizes
    ]
    if not code_sizes:
        return set()
    typical_code_size = statistics.median(code_sizes)

    result: set[str] = set()
    for font, sizes in sizes_by_font.items():
        if len(sizes) < min_samples or not digits_only.get(font, False):
            continue
        if statistics.fmean(sizes) <= typical_code_size * size_ratio_max:
            result.add(font)
    return result


def _is_formula_line(non_space_spans: list[dict]) -> bool:
    """Une ligne est une formule "pure" (isolée, pas une mention inline dans
    une phrase) si TOUTES ses polices appartiennent à la famille TeX Computer
    Modern mathématique (CMMI/CMSY/CMEX/CMR) ET qu'au moins une est
    strictement mathématique (CMMI/CMSY/CMEX, jamais utilisée pour du texte
    de corps). Une ligne de prose contenant une variable isolée (ex. "Soit E
    un ensemble") mélange toujours sa police de corps de texte avec la
    police CMMI de la variable — elle ne passe donc jamais ce test, qui ne
    retient que les lignes exclusivement composées de police(s) de la
    famille CM."""
    fonts = {s["font"] for s in non_space_spans}
    if not fonts:
        return False
    if not all(f.startswith(_MATH_ADJACENT_PREFIXES) for f in fonts):
        return False
    return any(f.startswith(_MATH_FONT_PREFIXES) for f in fonts)


def _line_to_element(
    line: dict,
    block_id: int,
    heading_map: dict[float, int],
    code_fonts: set[str],
    line_number_fonts: set[str] = frozenset(),
) -> _Element | None:
    # Normalise les spans "rawdict" (qui n'ont que "chars") en leur donnant
    # une clé "text", pour que tout le reste de cette fonction — inchangé —
    # fonctionne identiquement à quand elle recevait des spans "dict".
    spans = [{**s, "text": _span_text(s)} for s in line["spans"]]
    spans = [s for s in spans if s["text"]]
    # Retire les spans de numérotation de ligne (voir
    # _classify_line_number_fonts) AVANT toute classification : qu'un tel
    # numéro forme sa propre ligne PDF (ligne entièrement supprimée si plus
    # rien ne reste ensuite) ou préfixe une ligne de code (span retiré, le
    # reste de la ligne traité normalement), il ne doit jamais apparaître
    # comme contenu — ni code, ni prose.
    if line_number_fonts:
        removed_leading_number = bool(spans) and spans[0]["font"] in line_number_fonts
        spans = [s for s in spans if s["font"] not in line_number_fonts]
        # Le numéro retiré laisse derrière lui le span-séparateur (un espace
        # seul) qui le séparait du code — sans ce retrait, chaque ligne de
        # code ainsi préfixée garde un espace en tête, ce qui suffit à faire
        # chuter un score de similarité à peine sous un seuil strict alors
        # que le code lui-même est identique (vérifié empiriquement, voir
        # `fidelity_check.py` : un bloc pourtant identique scorait 57%).
        if removed_leading_number and spans and spans[0]["text"].strip(_STRIP_CHARS) == "":
            spans = spans[1:]
    if not any(s["text"].strip(_STRIP_CHARS) for s in spans):
        return None

    y = line["bbox"][1]
    non_space_spans = [s for s in spans if s["text"].strip(_STRIP_CHARS)]
    max_size = max((round(s["size"], 1) for s in non_space_spans), default=0.0)
    heading_level = heading_map.get(max_size)

    code_chars = sum(len(s["text"]) for s in non_space_spans if s["font"] in code_fonts)
    total_chars = sum(len(s["text"]) for s in non_space_spans)
    is_code_line = not heading_level and total_chars > 0 and code_chars / total_chars > _CODE_LINE_MIN_RATIO

    if not heading_level and not is_code_line and _is_formula_line(non_space_spans):
        text = "".join(s["text"] for s in spans).strip(_STRIP_CHARS)
        if not text:
            return None
        # group_id unique par ligne (pas block_id) : une meme formule peut
        # etre fragmentee par pdflatex sur plusieurs blocs PDF differents
        # (verifie empiriquement) — le regroupement final se fait par
        # proximite spatiale des bbox (_cluster_formula_elements), pas par
        # bloc.
        return _Element(y=y, kind="formula", group_id=id(line), text=text, bbox=tuple(line["bbox"]))

    if is_code_line:
        code_spans = list(spans)
        # Retire un éventuel numéro de ligne isolé en tête (artefact du
        # package `listings`/`minted`, pas du code réel — déjà filtré par
        # police plus haut si `line_number_fonts` était fourni ; ce test
        # texte reste une seconde barrière pour l'appelant historique qui
        # n'en fournirait pas), ainsi que le séparateur qui le sépare du code.
        if len(code_spans) > 1 and _LINE_NUMBER_RE.match(code_spans[0]["text"].strip(_STRIP_CHARS)):
            code_spans = code_spans[1:]
            if code_spans and code_spans[0]["text"].strip(_STRIP_CHARS) == "":
                code_spans = code_spans[1:]
        # rstrip() seulement : ne jamais tronquer le contenu. L'indentation
        # PYTHON N'EST PAS faite de vrais caractères espace en tête de ligne
        # dans le flux PDF — `listings` la rend par un simple décalage
        # horizontal du curseur (vérifié empiriquement : la ligne "yield num"
        # d'un corps de boucle n'a AUCUN span espace avant "yield", seulement
        # un bbox démarrant plus à droite que la ligne englobante). Cette
        # fonction ne reconstruit donc PAS l'indentation elle-même — elle se
        # contente de fournir la position x du contenu réel (hors numéro de
        # ligne retiré) via `bbox`, que `_reconstruct_code_indentation`
        # (appelée après le regroupement des blocs contigus, une fois la
        # référence d'indentation du bloc entier connue) utilise pour la
        # reconstruire a posteriori.
        text = "".join(s["text"] for s in code_spans).rstrip(_STRIP_CHARS)
        for curly, straight in _CURLY_QUOTES.items():
            text = text.replace(curly, straight)
        if not text:
            return None
        content_x0 = line["bbox"][0]
        for s in code_spans:
            chars = s.get("chars")
            if chars:
                content_x0 = chars[0]["bbox"][0]
                break
        bbox = (content_x0,) + tuple(line["bbox"])[1:]
        return _Element(y=y, kind="code", group_id=block_id, text=text, bbox=bbox)

    if heading_level:
        text = "".join(s["text"] for s in spans).strip(_STRIP_CHARS)
        if not text:
            return None
        # group_id = block_id (pas un id unique par ligne) pour qu'un titre
        # étalé sur deux lignes PyMuPDF (numéro de section puis intitulé) se
        # recompose en une seule ligne Markdown. Le préfixe "#" est ajouté au
        # rendu du groupe (_render_group), pas ici, pour ne pas le dupliquer.
        return _Element(y=y, kind="heading", group_id=block_id, text=text, heading_level=heading_level, bbox=tuple(line["bbox"]))

    # Prose, avec les segments en police à chasse fixe enveloppés en code
    # inline. Les spans consécutifs de même nature (code ou non) sont
    # fusionnés avant d'ajouter les barres, pour éviter des découpes
    # artificielles du type `zip``()` au lieu de `zip()`. Les espaces sont
    # toujours émis comme séparateurs neutres, jamais fusionnés dans un
    # buffer, pour ne pas les perdre lors de l'enveloppement en code inline.
    parts: list[str] = []
    buffer = ""
    buffer_is_code = False

    def _flush() -> None:
        nonlocal buffer
        if buffer:
            parts.append(f"`{buffer}`" if buffer_is_code else buffer)
            buffer = ""

    for s in spans:
        t = s["text"]
        if not t:
            continue
        if t.strip(_STRIP_CHARS) == "":
            _flush()
            parts.append(t)
            continue
        is_code_span = s["font"] in code_fonts
        if buffer and is_code_span != buffer_is_code:
            _flush()
        buffer += t
        buffer_is_code = is_code_span
    _flush()

    text = "".join(parts).strip(_STRIP_CHARS)
    if not text:
        return None
    return _Element(y=y, kind="prose", group_id=block_id, text=text, bbox=tuple(line["bbox"]))


_CODE_TRAILING_COMMENT_Y_TOLERANCE = 3.0
# points ; un commentaire de fin de ligne (`code  # commentaire`) rendu par
# `listings` dans une police/couleur differente du code qu'il commente peut
# atterrir, cote PyMuPDF, comme une "ligne" separee de celle du code — avec
# un ecart vertical quasi nul (0 a 1.8pt observes empiriquement sur un
# changement de police Roman -> Oblique) — tres inferieur a l'ecart normal
# entre deux vraies lignes de code consecutives (~9-10pt observes sur le
# meme document). Sans fusion, ce commentaire atterrit sur sa propre ligne
# Markdown au lieu de rester a la fin de celle qu'il commente (verifie
# empiriquement : `chunk_overlap=200,` puis `# nombre de caracteres...` sur
# deux lignes separees du bloc de code rendu).


def _merge_code_trailing_comments(elements: list[_Element]) -> list[_Element]:
    """Fusionne un element `code` avec le precedent quand les deux sont a la
    meme hauteur (a la tolerance pres) et du meme groupe : signe qu'il
    s'agit d'un commentaire de fin de ligne separe a tort en une "ligne"
    PyMuPDF distincte, jamais une vraie ligne de code suivante (voir
    constante ci-dessus). Opere sur les elements dans leur ordre de
    construction (ordre de lecture du bloc PDF d'origine), avant tout
    tri/regroupement ulterieur — necessaire car ce doublon de hauteur
    fausserait sinon l'ecart vertical typique dont depend la detection des
    lignes vides internes (`_insert_blank_code_lines`)."""
    merged: list[_Element] = []
    for el in elements:
        if (
            el.kind == "code"
            and merged
            and merged[-1].kind == "code"
            and merged[-1].group_id == el.group_id
            and abs(el.y - merged[-1].y) <= _CODE_TRAILING_COMMENT_Y_TOLERANCE
        ):
            prev = merged[-1]
            merged[-1] = _Element(
                y=prev.y, kind="code", group_id=prev.group_id,
                text=prev.text + "  " + el.text, bbox=prev.bbox,
            )
            continue
        merged.append(el)
    return merged


def _join_wrapped_lines(texts: list[str]) -> str:
    """Joint les fragments de lignes PDF d'un même titre/paragraphe avec un
    espace — sauf quand le fragment courant se termine par un mot suivi d'un
    trait d'union et que recoller les deux fragments SANS le trait d'union
    ni l'espace forme un mot français valide (dictionnaire hors ligne, même
    mécanisme que `ligature_repair.py`) : signe d'une césure automatique de
    pdflatex en fin de ligne (le trait d'union n'est alors qu'un artefact de
    mise en page, jamais un vrai trait d'union du mot) — vérifié
    empiriquement ("exploration" coupé en "ex-" / "ploration" par un retour
    à la ligne). Un vrai mot composé (ex. "auto-encodeur") qui tomberait par
    coïncidence sur la même frontière de ligne reste inchangé, puisque la
    forme fusionnée sans trait d'union n'est alors pas un mot du
    dictionnaire."""
    if not texts:
        return ""
    result = texts[0]
    for nxt in texts[1:]:
        m_end = _HYPHENATION_BREAK_RE.search(result)
        m_start = _WORD_START_RE.match(nxt)
        if m_end and m_start:
            merged_word = m_end.group(1) + m_start.group(1)
            if merged_word.lower() in _spell:
                result = result[: m_end.start(1)] + merged_word + nxt[m_start.end(1):]
                continue
        result = result + " " + nxt
    return result


def _render_group(kind: str, elements: list[_Element]) -> str:
    texts = [e.text for e in elements]
    if kind == "code":
        return "```\n" + "\n".join(texts) + "\n```"
    if kind == "heading":
        level = elements[0].heading_level or 1
        return f"{'#' * level} " + _join_wrapped_lines(texts)
    if kind == "prose":
        return _join_wrapped_lines(texts)
    return "\n".join(texts)  # image : une entrée par ligne


def _drop_orphan_line_numbers(elements: list[_Element]) -> list[_Element]:
    """Retire les lignes qui ne contiennent qu'un numéro isolé collé à un
    bloc de code : sur certains PDF, ce numéro (artefact du package
    `listings`/`minted`) atterrit sur sa propre ligne PyMuPDF plutôt que sur
    la même ligne que le code qu'il précède, et échappe donc au nettoyage
    fait dans `_line_to_element`."""
    result = []
    n = len(elements)
    for i, el in enumerate(elements):
        if el.kind == "prose" and _LINE_NUMBER_RE.match(el.text):
            prev_kind = elements[i - 1].kind if i > 0 else None
            next_kind = elements[i + 1].kind if i + 1 < n else None
            if prev_kind == "code" or next_kind == "code":
                continue
        result.append(el)
    return result


@dataclass
class _FormulaRun:
    members: list[_Element]
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1
    # Texte brut (non normalise) de l'element non-formule immediatement
    # avant/apres ce run dans l'ordre de lecture DE LA PAGE — vide si le run
    # est en tout debut/fin de page (voisin potentiellement sur la page
    # adjacente, non recherche : cas rare, le run reste alors sans ancrage
    # et retombe simplement sur la rasterisation, voir extract_native_pdf).
    # Sert a localiser, via extract_display_segments()/course.tex, quelle
    # formule source ce run represente reellement — necessaire car un
    # fragment d'indice/exposant d'une formule INLINE peut se faire
    # classifier, a tort, comme un run "isole" au meme titre qu'une vraie
    # formule d'affichage (verifie empiriquement, voir SKILL.md/discussion :
    # page contenant une identite binomiale citee en pleine phrase).
    preceding_text: str = ""
    following_text: str = ""


def _group_formula_runs(elements: list[_Element]) -> tuple[list[_Element], list[_FormulaRun]]:
    """Regroupe les éléments `kind="formula"` en runs contigus dans l'ordre
    de lecture (triés par `y`) : une même formule peut être fragmentée par
    pdflatex sur plusieurs lignes à des hauteurs légèrement différentes
    (fraction, exposant, indice — vérifié empiriquement), et ces fragments
    se suivent alors SANS aucun élément non-formule intercalé entre eux.

    Important : le regroupement s'arrête dès qu'un élément d'un autre type
    (prose, code...) s'intercale dans l'ordre de lecture — deux mentions
    isolées d'une même variable dans deux phrases différentes ne sont
    JAMAIS fusionnées entre elles, même si leurs coordonnées sont proches
    sur la page (vérifié empiriquement : une pure proximité spatiale sans
    cette contrainte de contiguïté fusionne à tort des mentions séparées
    par de la prose en un charabia unique).

    Ne décide pas comment matérialiser chaque run (image rasterisée ou texte
    LaTeX repris de `course.tex`) — c'est le rôle de
    `_materialize_formula_runs_as_images`/`_as_text` ci-dessous, appelé une
    fois que le nombre total de runs sur tout le document est connu (voir
    `extract_native_pdf`). Un run dont le rectangle englobant est trop large
    pour être une formule isolée crédible est résolu immédiatement ici en
    élément `prose` (fallback texte brut) : ce cas ne dépend d'aucune
    décision globale, il est écarté avant même de compter les runs valides.

    Retourne les éléments non-formule inchangés (fallbacks "trop larges"
    inclus) et la liste des runs valides, dans l'ordre de lecture de la
    page."""
    if not any(e.kind == "formula" for e in elements):
        return elements, []

    # Tri par (y, x) et non par y seul : deux éléments sur la même ligne
    # visuelle (même y à quelques points près) doivent être départagés par
    # leur position horizontale, sinon un fragment de formule et le mot de
    # prose juste à côté sur la même ligne peuvent se retrouver dans un
    # ordre arbitraire (dépendant de l'ordre d'itération des blocs PDF, pas
    # de l'ordre de lecture réel) — ce qui a fait fusionner à tort, lors des
    # tests, des fragments de formule séparés par de la prose sur la même
    # ligne en une seule image incluant du texte de corps.
    ordered = sorted(elements, key=lambda e: (round(e.y / 6.0), e.bbox[0] if e.bbox else 0.0))
    other_elements: list[_Element] = []
    runs: list[_FormulaRun] = []

    i = 0
    while i < len(ordered):
        if ordered[i].kind != "formula":
            other_elements.append(ordered[i])
            i += 1
            continue

        preceding_el = ordered[i - 1] if i > 0 else None
        run = [ordered[i]]
        j = i + 1
        while j < len(ordered):
            candidate = ordered[j]
            if candidate.kind == "formula":
                # Meme au sein d'un run contigu, un ecart vertical
                # anormalement grand signale deux formules distinctes plutot
                # qu'une seule fragmentee.
                if candidate.bbox[1] - run[-1].bbox[3] > _FORMULA_MERGE_PAD * 3:
                    break
                run.append(candidate)
                j += 1
                continue
            # Un element non-formule tres court (ex. un "=" isole, mal
            # classifie a cause d'une police ambigue a cet endroit precis —
            # verifie empiriquement : ce cas precis coupait en deux une
            # formule "P(...) = somme(...)" pourtant continue) n'interrompt
            # le run que s'il n'est PAS suivi de nouveau par du formule —
            # sinon on le traverse sans l'inclure dans le contenu. Un element
            # non-formule plus long (de la vraie prose) interrompt toujours,
            # sans exception : c'est cette meme regle qui evite de fusionner
            # a tort des mentions separees par une phrase complete.
            if (
                len(candidate.text.strip(_STRIP_CHARS)) <= 3
                and j + 1 < len(ordered)
                and ordered[j + 1].kind == "formula"
            ):
                j += 1
                continue
            break
        following_el = ordered[j] if j < len(ordered) else None
        i = j

        x0 = min(e.bbox[0] for e in run)
        y0 = min(e.bbox[1] for e in run)
        x1 = max(e.bbox[2] for e in run)
        y1 = max(e.bbox[3] for e in run)

        if x1 - x0 > _FORMULA_MAX_WIDTH:
            # Rectangle trop large pour etre une formule isolee credible —
            # capturerait a coup sur de la prose intercalee (voir constante
            # ci-dessus). Abandonne l'image, garde le texte brut du run
            # (mal ordonne mais jamais perdu), comme avant cette fonctionnalite.
            fallback_text = " ".join(e.text for e in sorted(run, key=lambda e: (round(e.bbox[1]), e.bbox[0])))
            other_elements.append(_Element(y=y0, kind="prose", group_id=("formula_fallback", id(run[0])), text=fallback_text))
            continue

        runs.append(_FormulaRun(
            members=run, bbox=(x0, y0, x1, y1),
            preceding_text=preceding_el.text if preceding_el is not None else "",
            following_text=following_el.text if following_el is not None else "",
        ))

    return other_elements, runs


def _materialize_formula_runs_as_images(
    runs: list[_FormulaRun],
    page: "pymupdf.Page",
    page_number: int,
    images_dir: Path,
    output_dir: Path,
) -> tuple[list[_Element], list[ExtractedImage]]:
    """Rasterise chaque run et le sauvegarde comme image native, exactement
    comme les illustrations déjà intégrées au PDF : c'est `/rag-nottext`
    (description + OCR/pix2tex, avec ses propres garde-fous) qui la traite
    ensuite. Chemin de repli utilisé quand `course.tex` est absent, ou que le
    nombre de formules qui y sont détectées ne correspond pas au nombre de
    runs détectés dans le PDF (voir `extract_native_pdf`) — dans ce dernier
    cas, tenter une transcription pix2tex directe sur la zone se serait déjà
    avéré peu fiable sur les formules complexes à plusieurs fragments
    (hallucinations vérifiées empiriquement), d'où le choix de rasteriser
    plutôt que de deviner."""
    merged: list[_Element] = []
    new_images: list[ExtractedImage] = []
    formula_index = 0

    for run in runs:
        x0, y0, x1, y1 = run.bbox
        img_path = images_dir / f"page_{page_number:03d}_formula_{formula_index:02d}.png"
        formula_index += 1
        rect = pymupdf.Rect(
            x0 - _FORMULA_RECT_MARGIN, y0 - _FORMULA_VERTICAL_PAD,
            x1 + _FORMULA_RECT_MARGIN, y1 + _FORMULA_VERTICAL_PAD,
        ) & page.rect  # jamais deborder de la page (formule pres d'un bord)
        pix = page.get_pixmap(clip=rect, matrix=pymupdf.Matrix(_FORMULA_RENDER_ZOOM, _FORMULA_RENDER_ZOOM))
        img_path.write_bytes(pix.tobytes("png"))

        extracted = ExtractedImage(path=img_path, page_number=page_number, width=pix.width, height=pix.height, ext="png")
        new_images.append(extracted)

        rel_path = img_path.relative_to(output_dir).as_posix()
        merged.append(_Element(y=y0, kind="image", group_id=("formula_image", id(run.members[0])), text=f"![Illustration]({rel_path})"))

    return merged, new_images


_ANCHOR_SIMILARITY_THRESHOLD = 0.5


def _normalize_pdf_text(text: str) -> str:
    """Normalisation minimale du texte deja extrait du PDF (deja du texte
    brut, sans commande LaTeX) pour comparaison approximative avec
    `extract_display_segments()` (course.tex) : espaces/casse seulement — les
    artefacts de ligature mal encodee (caracteres de controle, voir
    `/rag-extraction`) restent en l'etat, la comparaison par similarite
    (`difflib`) les tolere sans qu'il soit necessaire de les reparer ici.
    Classe explicite `[ \\t\\n\\r]` plutot que `\\s` : ce dernier traite
    aussi U+001C-U+001F comme des espaces (voir `_STRIP_CHARS`) et les
    aurait fait disparaitre ici, contrairement a ce que dit le paragraphe
    ci-dessus."""
    return re.sub(r"[ \t\n\r]+", " ", text).strip(_STRIP_CHARS).lower()


def _anchor_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def match_formula_runs_to_tex(
    runs_in_order: list[_FormulaRun],
    tex_segments: list[dict],
    *,
    threshold: float = _ANCHOR_SIMILARITY_THRESHOLD,
) -> dict[int, str]:
    """Apparie chaque run detecte dans le PDF a la formule de `course.tex`
    dont le contexte de prose voisin (avant/apres, voir
    `extract_display_segments`) lui ressemble le plus.

    Appariement GLOBAL (chaque run cherche sa meilleure correspondance parmi
    TOUTES les formules source non encore utilisees), plutot qu'un pointeur
    sequentiel meme a fenetre glissante — meme principe que
    `fidelity_check.check_code_blocks` (voir son commentaire dedie sur ce
    choix). Necessaire car les deux sequences (runs PDF, formules source)
    partagent le meme ordre de lecture mais PAS le meme nombre d'elements,
    dans les deux sens :
    - un fragment d'indice/exposant d'une formule INLINE peut se faire
      classifier a tort comme un run "isole" cote PDF (verifie
      empiriquement), sans aucun equivalent cote source (plus de runs que de
      formules) ;
    - une formule source peut n'avoir AUCUN run correspondant du tout cote
      PDF (ex. ecartee par `_FORMULA_MAX_WIDTH` avant meme d'atteindre cette
      fonction — plus de formules que de runs exploitables a cet endroit).

    Une version anterieure utilisait un pointeur a fenetre glissante de
    taille fixe (regarder N formules en avance) : bug trouve en revue de
    code — au-dela de N formules source consecutives sans run correspondant,
    le pointeur restait bloque et desynchronisait tout le reste du document,
    exactement comme la version a pointeur strict qu'il visait a corriger,
    juste avec un seuil plus haut avant de se declencher. La recherche
    globale n'a structurellement pas ce mode d'echec : chaque run trouve sa
    vraie meilleure correspondance ou aucune, quel que soit le nombre de
    formules sans run intercalees, sans jamais dependre d'une taille de
    fenetre arbitraire.

    Retourne `{id(run): latex_de_la_formule}` — un run absent de ce dict n'a
    trouve aucune correspondance fiable et doit etre traite individuellement
    par l'appelant (voir `extract_native_pdf`), jamais comme une erreur
    bloquante."""
    matches: dict[int, str] = {}
    used = [False] * len(tex_segments)
    for run in runs_in_order:
        preceding = _normalize_pdf_text(run.preceding_text)
        following = _normalize_pdf_text(run.following_text)

        best_idx, best_score = None, 0.0
        for idx, seg in enumerate(tex_segments):
            if used[idx]:
                continue
            score = max(
                _anchor_similarity(seg["prefix_tail"], preceding),
                _anchor_similarity(seg["suffix_head"], following),
            )
            if score >= threshold and score > best_score:
                best_score, best_idx = score, idx

        if best_idx is not None:
            used[best_idx] = True
            matches[id(run)] = tex_segments[best_idx]["formula"]
        # sinon : ce run ne correspond a aucune formule source disponible
        # (probablement un fragment d'indice/exposant isole a tort) — on le
        # laisse sans correspondance, sans consequence sur les runs suivants.
    return matches


def _materialize_formula_runs_as_prose_fallback(runs: list[_FormulaRun]) -> list[_Element]:
    """Renvoie chaque run comme simple texte de prose (ordre de lecture
    reconstruit du mieux possible, jamais rasterisé) — utilisé uniquement
    quand `course.tex` est disponible ET que le run n'a trouvé aucune
    correspondance fiable (`match_formula_runs_to_tex`) : dans ce cas, le
    run est, avec une confiance élevée, un faux positif du détecteur par
    police (fragment d'indice/exposant d'une formule INLINE, jamais une
    vraie formule d'affichage isolée — vérifié empiriquement, voir
    SKILL.md), donc une image rasterisée n'apporterait qu'un crop trompeur
    mordant sur le texte voisin, jamais un contenu exploitable de plus que
    le texte déjà disponible. Même traitement que le fallback "trop large"
    de `_group_formula_runs` (même limite : ordre de lecture reconstruit
    par tri, pas garanti fidèle pour un empilement complexe, mais jamais
    pire qu'une image mal cadrée)."""
    merged: list[_Element] = []
    for run in runs:
        y0 = run.bbox[1]
        fallback_text = " ".join(e.text for e in sorted(run.members, key=lambda e: (round(e.bbox[1]), e.bbox[0])))
        merged.append(_Element(y=y0, kind="prose", group_id=("formula_unmatched", id(run.members[0])), text=fallback_text))
    return merged


def _materialize_formula_runs_as_text(runs: list[_FormulaRun], matches: dict[int, str]) -> list[_Element]:
    """Remplace chaque run APPARIE (voir `match_formula_runs_to_tex`) par le
    LaTeX source exact repris de `course.tex` (verite terrain). Un run
    absent de `matches` n'est PAS traite ici — l'appelant doit le rasteriser
    individuellement (`_materialize_formula_runs_as_images`), jamais le
    perdre silencieusement."""
    merged: list[_Element] = []
    for run in runs:
        formula_tex = matches.get(id(run))
        if formula_tex is None:
            continue
        y0 = run.bbox[1]
        merged.append(_Element(y=y0, kind="formula_tex", group_id=("formula_tex", id(run.members[0])), text=formula_tex))
    return merged


def _merge_contiguous_code_runs(ordered: list[_Element]) -> list[_Element]:
    """Fusionne les éléments `kind="code"` consécutifs (aucun élément
    non-code intercalé) sous un `group_id` commun, quel que soit leur
    `group_id` d'origine (`block_id` PyMuPDF).

    Nécessaire car pdflatex/`listings` peut découper un même bloc de code
    source en de très nombreux blocs PDF distincts — jusqu'à un par ligne,
    voire par mot recoloré syntaxiquement — sans que cela corresponde à une
    quelconque limite dans le `lstlisting` d'origine : sans cette fusion, un
    seul bloc de code source explose en autant de fences Markdown
    indépendants (vérifié empiriquement sur un livre à forte densité de
    code : 74 `lstlisting` dans `course.tex` produisaient 1562 fences dans
    `pivot.md`). Le regroupement par contiguïté (même principe que pour les
    formules, voir `_group_formula_runs`) est robuste à ce découpage
    arbitraire côté PDF : deux lignes de code sans rien d'autre entre elles
    appartiennent forcément au même bloc source."""
    result: list[_Element] = []
    i = 0
    n = len(ordered)
    while i < n:
        el = ordered[i]
        if el.kind != "code":
            result.append(el)
            i += 1
            continue
        shared_group_id = el.group_id
        result.append(el)
        i += 1
        while i < n and ordered[i].kind == "code":
            result.append(_Element(
                y=ordered[i].y, kind="code", group_id=shared_group_id,
                text=ordered[i].text, heading_level=ordered[i].heading_level,
                bbox=ordered[i].bbox,
            ))
            i += 1
    return result


def _render_page_markdown(elements: list[_Element], code_char_width: float = 0.0) -> str:
    ordered = _drop_orphan_line_numbers(sorted(elements, key=lambda e: e.y))
    ordered = _merge_contiguous_code_runs(ordered)
    ordered = _reconstruct_code_indentation(ordered, code_char_width)
    ordered = _insert_blank_code_lines(ordered)
    groups = []
    for (kind, _group_id), group_iter in itertools.groupby(ordered, key=lambda e: (e.kind, e.group_id)):
        group = list(group_iter)
        groups.append(_render_group(kind, group))
    return "\n\n".join(groups)


_FENCE = "```"
_IMAGE_LINE_RE = re.compile(r"^!\[Illustration\]\(.+\)$")
_FIGURE_CAPTION_RE = re.compile(r"^(?:Figure|Listing|Table|Tableau)\b")
_PAGE_NUMBER_LINE_RE = re.compile(r"^\d{1,4}$")


def _looks_like_page_furniture(text: str) -> bool:
    """Vrai si `text` ne contient QUE des éléments typiques de mise en page
    LaTeX susceptibles de s'intercaler entre deux moitiés d'un même bloc de
    code coupé par un saut de page — une illustration flottante (jamais
    fixée à un endroit précis par LaTeX, peut atterrir n'importe où selon
    l'algorithme de placement, y compris sans rapport avec le sujet du
    texte voisin — vérifié empiriquement), sa légende, un numéro de page
    isolé. Faux dès qu'une ligne ne correspond à aucun de ces motifs — un
    vrai paragraphe de prose substantiel indique deux blocs de code
    réellement distincts séparés par une explication, jamais une simple
    coupure de page, et ne doit jamais être avalé par erreur."""
    for raw_line in text.split("\n"):
        line = raw_line.strip(_STRIP_CHARS)
        if not line:
            continue
        if _IMAGE_LINE_RE.match(line) or _FIGURE_CAPTION_RE.match(line) or _PAGE_NUMBER_LINE_RE.match(line):
            continue
        return False
    return True


def _merge_cross_page_code_fences(page_markdowns: list[str]) -> list[str]:
    """Fusionne un bloc de code qui se termine tout en bas d'une page et
    reprend tout en haut de la suivante — un `lstlisting` plus long qu'une
    page pleine, que pdflatex laisse à cheval sur deux pages (voir
    `SKILL.md` étape 4 règle 5 : coupure "inévitable, pas un défaut" côté
    mise en page, mais qui n'a aucune raison de rester deux fences Markdown
    distincts côté extraction — la frontière de page n'a aucune
    signification pour le contenu du bloc de code lui-même).

    Gère aussi le cas où du "mobilier de page" (voir
    `_looks_like_page_furniture`) s'intercale entre la fin de la première
    moitié et la reprise de la seconde — sur une ou PLUSIEURS pages
    consécutives entièrement composées de ce mobilier (vérifié
    empiriquement : une illustration flottante D'UN AUTRE CHAPITRE occupait
    à elle seule toute une page intermédiaire, avec sa légende et son
    numéro de page — aucun rapport thématique avec le code environnant,
    placée là par l'algorithme de mise en page de LaTeX). Sans cette
    traversée multi-pages, la fusion échouait (la page suivant
    immédiatement la coupure ne commençait pas par un fence) et le contrôle
    de fidélité tombait à 88% de similarité (seuil 95%) au lieu de
    reconnaître le même bloc source. Le mobilier traversé est préservé tel
    quel juste après le bloc fusionné, jamais perdu — seulement déplacé hors
    du milieu du code.

    Traite `page_markdowns` comme une file (pas un index figé) : le reliquat
    d'une page après une fusion (`rest_of_page`, tout ce qui suit la
    fermeture du fence repris) est réinjecté en tête de la file plutôt
    qu'ajouté tel quel au résultat final — nécessaire pour DEUX coupures de
    page consécutives (vérifié empiriquement : une classe Python coupée
    page N/N+1, immédiatement suivie d'un paragraphe puis d'un second bloc
    de code coupé page N+1/N+2 via une page de mobilier). Un index figé
    traiterait `rest_of_page` comme définitif sans jamais remarquer qu'il se
    termine lui-même par un fence ouvert nécessitant une fusion avec la
    suite — la seconde coupure passait alors inaperçue silencieusement.

    Opère au niveau chaîne (pas `_Element`) car chaque page est déjà rendue
    indépendamment par `_render_page_markdown` à ce stade — plus simple et
    plus sûr qu'une refonte du rendu par page pour un cas qui ne concerne
    jamais que la toute première/dernière ligne de chaque page concernée."""
    remaining = list(page_markdowns)
    merged: list[str] = []
    while remaining:
        current = remaining.pop(0)
        current_stripped = current.rstrip(_STRIP_CHARS)
        if not current_stripped.endswith(_FENCE):
            merged.append(current)
            continue

        # Traverse zero ou plusieurs pages ENTIEREMENT composees de
        # mobilier (voir _looks_like_page_furniture) a la recherche de la
        # reprise du fence — jamais au-dela d'une vraie page de contenu.
        furniture_chunks: list[str] = []
        idx = 0
        resumed = False
        fence_idx = -1
        while idx < len(remaining):
            candidate = remaining[idx].lstrip(_STRIP_CHARS)
            fence_idx = candidate.find(_FENCE)
            before_fence = candidate if fence_idx == -1 else candidate[:fence_idx]
            if not _looks_like_page_furniture(before_fence):
                break
            stripped_before = before_fence.strip(_STRIP_CHARS)
            if stripped_before:
                furniture_chunks.append(stripped_before)
            if fence_idx != -1:
                resumed = True
                break
            idx += 1

        if not resumed:
            merged.append(current)
            continue

        resumed_page = remaining[idx]
        del remaining[: idx + 1]

        after_furniture = resumed_page.lstrip(_STRIP_CHARS)
        after_open = after_furniture[fence_idx + len(_FENCE):]
        if after_open.startswith("\n"):
            after_open = after_open[1:]
        close_idx = after_open.find("\n" + _FENCE)
        if close_idx == -1:
            # toute la page ne contient que ce fence (rare : page entierement
            # de code) — rien d'autre a preserver derriere.
            first_fence_body, rest_of_page = after_open, ""
        else:
            first_fence_body = after_open[:close_idx]
            rest_of_page = after_open[close_idx + 1 + len(_FENCE):]

        prev_body = current_stripped[: -len(_FENCE)]
        merged.append(prev_body + "\n" + first_fence_body + "\n" + _FENCE)
        merged.extend(furniture_chunks)
        if rest_of_page.strip(_STRIP_CHARS):
            remaining.insert(0, rest_of_page.lstrip("\n"))
    return merged


def extract_native_pdf(
    pdf_path: Path,
    output_dir: Path,
    page_range: tuple[int, int] | None = None,
    course_tex_path: Path | None = None,
    illustrations_dir: Path | None = None,
) -> ExtractionResult:
    """Extrait le texte structuré (avec distinction code/prose) et les images
    natives de `pdf_path`.

    `page_range` (début inclus, fin exclue) limite le traitement à un
    sous-ensemble de pages — utile pour tester sur un extrait avant de lancer
    un document complet.

    `course_tex_path`/`illustrations_dir` (dossier de travail de
    /cours-condense pour ce même document, quand il existe) permettent de
    court-circuiter deux points fragiles de l'extraction PDF :

    - les illustrations : copiées depuis leur PNG d'origine (nom sémantique
      conservé) plutôt que ré-extraites du PDF sous un nom opaque — activé
      seulement si le compte correspond exactement à ce que le PDF donne à
      extraire par ailleurs (comptage fiable ici : une image native est une
      unité bien définie des deux côtés, sans ambiguïté de fragmentation) ;
    - les formules mathématiques : reprises telles quelles depuis
      `course.tex` (vérité terrain) plutôt que rasterisées + à OCRiser plus
      tard — mais PAS via un simple comptage global (un fragment d'indice/
      exposant d'une formule INLINE se fait parfois classifier, à tort,
      comme une zone "isolée" côté PDF au même titre qu'une vraie formule
      d'affichage, ce qui invaliderait tout comptage strict). L'appariement
      se fait individuellement, run par run, par similarité de contexte de
      prose voisin (voir `match_formula_runs_to_tex`) : un run non apparié
      est rasterisé isolément (comportement legacy, inchangé pour ce run
      précis), jamais un abandon global de l'optimisation pour tout le
      document.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    images_dir = output_dir / "images"
    images_dir.mkdir(exist_ok=True)

    doc = pymupdf.open(pdf_path)
    start, end = page_range if page_range else (0, doc.page_count)
    page_numbers = list(range(start, min(end, doc.page_count)))

    # Une seule extraction "rawdict" par page (superset de "dict" + les bbox
    # par caractère), réutilisée pour les 3 usages qui feraient sinon chacun
    # leur propre passe complète du document : fil d'ariane des titres,
    # classification des polices à chasse fixe, puis assemblage du texte.
    pages_raw = {pno: doc[pno].get_text("rawdict") for pno in page_numbers}

    heading_map = _heading_level_map(pages_raw)
    code_fonts = _classify_code_fonts(pages_raw)
    line_number_fonts = _classify_line_number_fonts(pages_raw, code_fonts)
    code_char_width = _estimate_code_char_width(pages_raw, code_fonts)

    # --- Passe 1 : elements + regroupement des runs de formule par page,
    # SANS encore decider comment les materialiser (image ou texte source) —
    # cette decision depend du compte total sur tout le document, connu
    # seulement une fois cette passe terminee.
    per_page_other_elements: dict[int, list[_Element]] = {}
    per_page_formula_runs: dict[int, list[_FormulaRun]] = {}
    total_formula_runs = 0
    total_native_images = 0

    for pno in page_numbers:
        page_dict = pages_raw[pno]
        elements: list[_Element] = []
        for block_id, block in enumerate(page_dict["blocks"]):
            if block["type"] != 0:
                continue
            for line in block["lines"]:
                el = _line_to_element(line, block_id, heading_map, code_fonts, line_number_fonts)
                if el:
                    elements.append(el)

        elements = _merge_code_trailing_comments(elements)
        other_elements, runs = _group_formula_runs(elements)
        per_page_other_elements[pno] = other_elements
        per_page_formula_runs[pno] = runs
        total_formula_runs += len(runs)
        total_native_images += len(doc[pno].get_images(full=True))

    # --- Decision illustrations : une seule fois pour tout le document
    # (comptage fiable, voir docstring). Decision formules : voir plus bas,
    # par run individuel (match_formula_runs_to_tex), pas par comptage.
    #
    # `course.tex` n'est JAMAIS utilise comme verite terrain quand
    # `page_range` restreint le traitement a un sous-ensemble de pages : les
    # formules/illustrations qu'on y extrait couvrent le LIVRE ENTIER, sans
    # aucun moyen de savoir laquelle de ses portions correspond a la plage
    # de pages traitee ici. Les utiliser quand meme desynchroniserait tout
    # l'appariement (bug trouve en revue de code : le pointeur de
    # match_formula_runs_to_tex demarre a la premiere formule du LIVRE, pas
    # de la plage) — `--pages` etant documente comme reserve au test/debug
    # (voir SKILL.md), retomber sur le comportement legacy (rasterisation/
    # extraction native) y est strictement plus sur qu'un appariement
    # errone silencieux.
    tex_segments: list[dict] = []
    tex_illustration_names: list[str] = []
    if page_range is None and course_tex_path is not None and course_tex_path.exists():
        tex_text = course_tex_path.read_text(encoding="utf-8")
        tex_segments = extract_display_segments(tex_text)
        tex_illustration_names = extract_illustration_basenames(tex_text)

    use_source_illustrations = (
        illustrations_dir is not None
        and illustrations_dir.exists()
        and total_native_images > 0
        and len(tex_illustration_names) == total_native_images
    )
    illustration_names_iter = iter(tex_illustration_names) if use_source_illustrations else None

    # Appariement individuel des runs de formule, dans l'ordre de lecture du
    # DOCUMENT ENTIER (pas seulement de la page) — necessaire car le
    # contexte de prose voisin d'un run pres d'un bord de page peut manquer
    # localement sans que cela invalide l'appariement des runs suivants.
    all_runs_in_order = [run for pno in page_numbers for run in per_page_formula_runs[pno]]
    formula_matches = match_formula_runs_to_tex(all_runs_in_order, tex_segments) if tex_segments else {}

    # --- Passe 2 : materialisation (formules + images natives) et rendu
    # Markdown page par page, dans l'ordre.
    pages: list[PageExtraction] = []
    all_images: list[ExtractedImage] = []
    n_formula_regions_total = 0
    n_formula_matches_total = 0

    for pno in page_numbers:
        page = doc[pno]
        other_elements = per_page_other_elements[pno]
        runs = per_page_formula_runs[pno]
        n_formula_regions_total += len(runs)

        matched_runs = [r for r in runs if id(r) in formula_matches]
        unmatched_runs = [r for r in runs if id(r) not in formula_matches]
        n_formula_matches_total += len(matched_runs)

        formula_elements = _materialize_formula_runs_as_text(matched_runs, formula_matches)
        if tex_segments:
            # course.tex disponible : un run non apparie est, avec une
            # confiance elevee, un faux positif (fragment de formule
            # INLINE) plutot qu'une vraie formule d'affichage qui aurait
            # echoue a s'apparier (verifie empiriquement, voir SKILL.md) —
            # redescend en texte plutot que de rasteriser une image trompeuse.
            formula_elements += _materialize_formula_runs_as_prose_fallback(unmatched_runs)
            formula_images: list[ExtractedImage] = []
        else:
            raster_elements, formula_images = _materialize_formula_runs_as_images(unmatched_runs, page, pno, images_dir, output_dir)
            formula_elements += raster_elements

        elements = other_elements + formula_elements

        page_images: list[ExtractedImage] = list(formula_images)
        all_images.extend(formula_images)
        for img_index, img in enumerate(page.get_images(full=True)):
            xref = img[0]
            try:
                base_image = doc.extract_image(xref)
            except Exception:
                continue

            ext = base_image["ext"]
            if use_source_illustrations:
                name = next(illustration_names_iter)
                img_path = images_dir / name
                shutil.copy2(illustrations_dir / name, img_path)
            else:
                img_path = images_dir / f"page_{pno:03d}_img_{img_index:02d}.{ext}"
                img_path.write_bytes(base_image["image"])

            extracted = ExtractedImage(
                path=img_path,
                page_number=pno,
                width=base_image.get("width", 0),
                height=base_image.get("height", 0),
                ext=ext,
            )
            page_images.append(extracted)
            all_images.append(extracted)

            rects = page.get_image_rects(xref)
            y_pos = rects[0].y0 if rects else 1e9
            rel_path = img_path.relative_to(output_dir).as_posix()
            elements.append(_Element(y=y_pos, kind="image", group_id=("image", img_index), text=f"![Illustration]({rel_path})"))

        page_markdown = _render_page_markdown(elements, code_char_width)
        char_count = sum(1 for c in page_markdown if not c.isspace())

        pages.append(
            PageExtraction(
                page_number=pno,
                markdown=page_markdown,
                images=page_images,
                char_count=char_count,
            )
        )

    full_markdown = "\n\n".join(_merge_cross_page_code_fences([p.markdown for p in pages]))
    doc.close()

    return ExtractionResult(
        source_path=pdf_path,
        pages=pages,
        markdown=full_markdown,
        images=all_images,
        n_formula_regions=n_formula_regions_total,
        n_formula_matches_from_tex=n_formula_matches_total,
        illustrations_from_source=use_source_illustrations,
    )
