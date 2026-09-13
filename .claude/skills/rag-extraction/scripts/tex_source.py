"""Extraction de contenu directement depuis le `course.tex` produit par
/cours-condense (dossier de travail `rag_data/courscondense/<slug>/`), pour
court-circuiter deux points fragiles de `/rag-extraction` quand ce fichier
est disponible :

- les formules mathematiques : `convert.py` doit normalement les detecter par
  police (CMMI/CMSY/CMEX) dans le PDF compile, les rasteriser, puis les faire
  OCRiser par pix2tex a l'etape /rag-images (transcription indicative, pas
  garantie fidele sur les formules complexes). Le LaTeX source de la formule
  est pourtant deja disponible tel quel dans `course.tex` — aucune raison de
  repasser par une image + un OCR quand la verite terrain est a portee de
  lecture.
- les illustrations : `convert.py` extrait les images natives embarquees dans
  le PDF (`page_NNN_img_NN.ext`, nom opaque). Le fichier `illustrations/*.png`
  d'origine, avec son nom semantique (`ch01_union.png`...), est deja present
  dans le meme dossier de travail.

Ce module ne fait QUE du parsing de texte (aucun acces disque) : la
resolution de chemin (`rag_data/courscondense/<slug>/`) reste dans `paths.py`,
et la decision d'utiliser ou non ce contenu (selon que les comptes
correspondent a ce qui est detecte dans le PDF) reste dans `convert.py`.
"""

from __future__ import annotations

import re

_ENV_NAMES = r"equation\*?|align\*?|gather\*?|multline\*?|eqnarray\*?|flalign\*?"

# Ordre des alternatives sans consequence (une seule matche a une position
# donnee), mais on garde \[..\] et les environnements avant $...$ : un $...$
# non-greedy pourrait sinon matcher un fragment interne a un \[..\] contenant
# lui-meme un signe dollar litteral (rarissime mais possible dans du texte
# cite), meme si l'alternance regex essaie de toute facon chaque alternative
# a chaque position et retient la premiere qui reussit.
_FORMULA_RE = re.compile(
    r"\\\[.*?\\\]"
    r"|\$\$.*?\$\$"
    r"|\\begin\{(?P<env>" + _ENV_NAMES + r")\}.*?\\end\{(?P=env)\}"
    r"|\\\(.*?\\\)"
    r"|(?<!\$)\$(?:\\.|[^$\\])+?\$(?!\$)",
    re.DOTALL,
)

_LSTLISTING_RE = re.compile(r"\\begin\{lstlisting\}.*?\\end\{lstlisting\}", re.DOTALL)
_LSTINLINE_RE = re.compile(r"\\lstinline\{[^}]*\}")
_COMMENT_RE = re.compile(r"(?<!\\)%.*")
# `\$` est un dollar litteral (ex. montant en euros/dollars dans un enonce
# d'exercice), jamais un delimiteur de mode mathematique — sans ce
# neutralisage prealable, `(?<!\$)\$...\$(?!\$)` ci-dessous le confondrait
# avec une ouverture de formule inline et engloutirait tout le texte jusqu'au
# prochain `$` reel, potentiellement des paragraphes plus loin (verifie
# empiriquement). Remplace par un texte neutre plutot que de le supprimer :
# la longueur n'a pas besoin d'etre preservee (voir docstring ci-dessous),
# mais un simple retrait risquerait de recoller deux mots.
_ESCAPED_DOLLAR_RE = re.compile(r"\\\$")

_INCLUDEGRAPHICS_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")


def _strip_non_math(tex_source: str) -> str:
    """Retire le code (jamais du LaTeX mathematique, mais peut contenir des
    caracteres qui perturberaient la detection : `%`, `$` dans un commentaire
    Python par ex.), les commentaires LaTeX et les dollars litteraux
    echappes, avant la recherche de formules. Ne preserve pas la
    longueur/position d'origine — ce module ne rend jamais de positions,
    seulement une liste de formules dans l'ordre de lecture, donc aucune
    correspondance de decalage n'est necessaire."""
    sans_code = _LSTLISTING_RE.sub("", tex_source)
    sans_code = _LSTINLINE_RE.sub("", sans_code)
    sans_code = _COMMENT_RE.sub("", sans_code)
    return _ESCAPED_DOLLAR_RE.sub(" dollar ", sans_code)


def extract_formulas(tex_source: str) -> list[str]:
    """Retourne chaque formule mathematique de `tex_source`, delimiteurs
    d'origine inclus (`$...$`, `\\(...\\)`, `\\[...\\]`, environnements
    `align`/`equation`/...), dans l'ordre de lecture du fichier — le meme
    ordre que celui dans lequel pdflatex les fait apparaitre dans le PDF
    compile, ce qui permet un appariement position par position avec les
    zones de formule detectees par police dans le PDF (voir `convert.py`)."""
    cleaned = _strip_non_math(tex_source)
    return [m.group(0) for m in _FORMULA_RE.finditer(cleaned)]


_DISPLAY_RE = re.compile(
    r"\\\[.*?\\\]"
    r"|\$\$.*?\$\$"
    r"|\\begin\{(?P<denv>" + _ENV_NAMES + r")\}.*?\\end\{(?P=denv)\}",
    re.DOTALL,
)

_COMMAND_RE = re.compile(r"\\[a-zA-Z]+\*?")


def _normalize_prose(text: str) -> str:
    """Reduit un fragment de prose LaTeX (avant/apres une formule) a une
    forme comparable au texte brut extrait du PDF compile : retire les
    formules residuelles (maths inline notamment, jamais utiles comme
    ancrage puisqu'elles ne sont jamais isolees dans le PDF), les commandes
    LaTeX et les accolades, puis normalise espaces/casse. Ne vise pas une
    egalite stricte avec le texte PDF (qui peut porter des artefacts de
    ligature mal encodee, voir `/rag-extraction`) — seulement une base
    suffisamment proche pour une comparaison approximative (voir
    `convert.py`, `difflib.SequenceMatcher`)."""
    text = _FORMULA_RE.sub(" ", text)
    text = _COMMAND_RE.sub(" ", text)
    text = text.replace("{", " ").replace("}", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def extract_display_segments(tex_source: str, context_words: int = 12) -> list[dict]:
    """Retourne, pour chaque formule d'AFFICHAGE de `tex_source` (jamais les
    formules inline `$...$`/`\\(...\\)` : celles-ci ne sont jamais isolees
    par la detection par police du PDF compile, donc inutiles a apparier),
    un dict `{"formula": <LaTeX exact>, "prefix_tail": <fin de la prose
    precedente normalisee>, "suffix_head": <debut de la prose suivante
    normalisee>}`, dans l'ordre de lecture.

    Ces "ancres" de prose voisine servent a localiser, dans le PDF compile,
    quelle zone de police mathematique correspond a quelle formule source —
    de facon robuste a la fragmentation (une formule peut se retrouver
    coupee en plusieurs runs cote PDF) sans dependre d'un simple comptage
    global (voir `convert.py` — le comptage global se fait tromper des que
    des indices/exposants isoles d'une formule INLINE se retrouvent, a
    tort, classes "isoles" cote PDF)."""
    cleaned = _strip_non_math(tex_source)
    matches = list(_DISPLAY_RE.finditer(cleaned))
    segments = []
    prev_end = 0
    for idx, m in enumerate(matches):
        next_start = matches[idx + 1].start() if idx + 1 < len(matches) else len(cleaned)
        prefix_norm = _normalize_prose(cleaned[prev_end:m.start()])
        suffix_norm = _normalize_prose(cleaned[m.end():next_start])
        segments.append({
            "formula": m.group(0),
            "prefix_tail": " ".join(prefix_norm.split()[-context_words:]),
            "suffix_head": " ".join(suffix_norm.split()[:context_words]),
        })
        prev_end = m.end()
    return segments


_HEADING_RE = re.compile(r"\\(?:chapter|section|subsection)\*?\{([^}]*)\}")


def extract_headings(tex_source: str) -> list[str]:
    """Retourne le titre de chaque `\\chapter`/`\\section`/`\\subsection` de
    `tex_source`, dans l'ordre de lecture (délimiteurs LaTeX exclus, texte
    brut du titre inclus) — vérité terrain pour s'assurer qu'aucun titre ne
    manque à l'appel dans `pivot.md` (voir `fidelity_check.py`) : une
    section absente du pivot indiquerait un vrai trou de couverture, pas
    seulement un défaut de mise en forme."""
    return [m.group(1).strip() for m in _HEADING_RE.finditer(tex_source)]


_LSTLISTING_BLOCK_RE = re.compile(
    r"\\begin\{lstlisting\}(?:\[[^\]]*\])?\n?(.*?)\\end\{lstlisting\}",
    re.DOTALL,
)


def extract_code_blocks(tex_source: str) -> list[str]:
    """Retourne le contenu (sans les balises `\\begin`/`\\end` ni les
    options) de chaque bloc `lstlisting` de `tex_source`, dans l'ordre de
    lecture — vérité terrain pour vérifier la fidélité du code recopié dans
    `pivot.md` par `/rag-extraction` (voir `fidelity_check.py`) : ce code a
    déjà été extrait à position de caractère exacte depuis le PDF source par
    `/cours-condense` (`extraire_code_exact.py`), jamais retapé ni traduit —
    la référence la plus fiable disponible, plus fiable que de comparer deux
    extractions PDF différentes entre elles."""
    return [m.group(1).strip("\n") for m in _LSTLISTING_BLOCK_RE.finditer(tex_source)]


def extract_illustration_basenames(tex_source: str) -> list[str]:
    """Retourne le nom de fichier (sans le dossier) de chaque
    `\\includegraphics{illustrations/...}` de `tex_source`, dans l'ordre de
    lecture — utilise pour reappaireur les images natives detectees dans le
    PDF compile avec le fichier PNG d'origine (nom semantique) plutot que de
    garder le nom opaque `page_NNN_img_NN.ext` genere par l'extraction PDF.
    Les `\\includegraphics` ne referencant pas le dossier `illustrations/`
    (cas non attendu dans ce pipeline, mais pas suppose impossible) sont
    ignores : ce ne sont pas des illustrations generees par /cours-condense."""
    names = []
    for m in _INCLUDEGRAPHICS_RE.finditer(tex_source):
        path = m.group(1).strip().replace("\\", "/")
        if "illustrations/" in path:
            names.append(path.rsplit("/", 1)[-1])
    return names
