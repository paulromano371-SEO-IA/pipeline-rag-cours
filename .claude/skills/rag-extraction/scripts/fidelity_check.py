"""Contrôle qualité de fidélité de `/rag-extraction` : compare `pivot.md`
(et son dossier `images/`) à la vérité terrain disponible dans le dossier de
travail de `/cours-condense` (`course.tex` + `illustrations/`) — quand ce
dossier n'est plus disponible (nettoyé, ou PDF condensé produit autrement),
ce contrôle est simplement sauté, jamais un échec.

Volets, dans l'ordre demandé :
1. Illustrations : aucune ne doit manquer, aucune référence cassée.
2. Blocs de code : chaque `lstlisting` de `course.tex` doit se retrouver
   quasi identique dans un bloc ``` de `pivot.md`.
3. Formules : chaque formule d'affichage de `course.tex` doit se retrouver
   soit reprise à l'identique en texte, soit en image encore présente sur
   disque (rasterisation de repli) — jamais silencieusement absente.
4. Titres : aucun `\chapter`/`\section`/`\subsection` de `course.tex` ne
   doit manquer parmi les titres Markdown de `pivot.md` (couverture
   structurelle, indépendante du contenu des sections).
5. Syntaxe Python : chaque bloc de code de `pivot.md` doit être analysable
   par `ast.parse()` — jamais bloquant (un bloc légitimement non-Python,
   commande shell ou JSON, échoue naturellement ce test sans être un défaut
   d'extraction), seulement indicatif pour repérage rapide.
6. Figures complètes : chaque `\includegraphics` de `course.tex` doit siéger
   dans un environnement `figure` avec `\caption` ET `\label` — déjà garanti
   par `verifier_structure_course_tex.py` de `/cours-condense`, donc
   redondant par construction ; conservé en défense en profondeur (coût
   négligeable, détecterait immédiatement une régression amont), jamais
   bloquant pour `/rag-extraction` puisque ce n'est pas cette étape qui en
   est responsable.

Aucune correction automatique ici : ce module ne fait que constater et
rapporter, à charge de l'appelant (`/rag-extraction`) de décider quoi faire
d'un problème signalé (voir SKILL.md, distinction bloquant/indicatif)."""

from __future__ import annotations

import ast
import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

from tex_source import (
    extract_code_blocks,
    extract_display_segments,
    extract_headings,
    extract_illustration_basenames,
)

_FENCE_RE = re.compile(r"```\n(.*?)\n```", re.DOTALL)
# Alt-text quelconque (jamais seulement "Illustration" en dur) : cette
# fonction ne s'execute aujourd'hui qu'avant /rag-nottext (alt-text toujours
# "Illustration" a ce stade), mais rester generique cote regex ne coute rien
# et evite un piege si ce controle est un jour reexecute apres enrichissement
# (alt-text alors remplace par une description, voir rag-nottext/scripts/run.py).
_IMAGE_REF_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_MD_HEADING_RE = re.compile(r"^#+\s+(.*)$", re.MULTILINE)
_COMMAND_RE = re.compile(r"\\[a-zA-Z]+\*?")
_FIGURE_ENV_RE = re.compile(r"\\begin\{figure\}.*?\\end\{figure\}", re.DOTALL)

# Similarite minimale (difflib) pour considerer un bloc de code retrouve a
# l'identique : jamais 1.0 strict, car `breaklines=true` du style listings
# peut retailler l'indentation d'affichage sans changer le code lui-meme ;
# en dessous de ce seuil, la difference est assez importante pour signaler
# un vrai risque de contenu tronque/altere plutot qu'un simple artefact de
# mise en page.
CODE_SIMILARITY_THRESHOLD = 0.95


@dataclass
class FidelityIssue:
    category: str  # "illustration" | "code" | "formula"
    blocking: bool
    detail: str


@dataclass
class FidelityReport:
    issues: list[FidelityIssue] = field(default_factory=list)
    n_illustrations_expected: int = 0
    n_illustrations_verified: int = 0
    n_code_blocks_expected: int = 0
    n_code_blocks_verified: int = 0
    n_formulas_expected: int = 0
    n_formulas_verified_exact: int = 0
    n_headings_expected: int = 0
    n_headings_verified: int = 0
    n_code_blocks_syntax_checked: int = 0
    n_code_blocks_syntax_valid: int = 0
    n_figures_expected: int = 0
    n_figures_complete: int = 0
    skipped: bool = False  # vrai si course.tex/illustrations introuvables

    @property
    def blocking_issues(self) -> list[FidelityIssue]:
        return [i for i in self.issues if i.blocking]

    @property
    def ok(self) -> bool:
        return not self.blocking_issues


def _normalize_code(text: str) -> str:
    """Compare le CONTENU (indentation/espaces internes comprise, car
    significative en Python), seulement insensible aux espaces de fin de
    ligne et aux lignes vides superflues en tête/queue — jamais une
    normalisation agressive qui masquerait une vraie troncature."""
    lines = [ln.rstrip() for ln in text.split("\n")]
    return "\n".join(lines).strip("\n")


def check_illustrations(
    illustrations_dir: Path, pivot_text: str, images_dir: Path, illustrations_from_source: bool,
    *, course_tex_illustrations: frozenset[str] = frozenset(),
) -> tuple[list[FidelityIssue], int, int]:
    """`illustrations_from_source` doit venir de
    `ExtractionResult.illustrations_from_source` (voir `convert.py`) : si
    `False`, `convert.py` est retombé sur l'extraction native du PDF sous
    noms opaques (`page_NNN_img_NN.ext`), donc chercher les noms
    sémantiques de `illustrations/` dans `pivot.md` échouerait
    systématiquement pour TOUTES les illustrations, même correctement
    extraites — un vrai bug trouvé en revue de code (ce paramètre était
    absent, donnant des faux BLOQUANT à chaque bascule sur ce repli). Dans
    ce cas, seul un contrôle de VOLUME (comptage) est possible ici, en
    indicatif — l'identité par nom exact reste hors de portée sans
    redescendre au niveau de `convert.py`.

    `course_tex_illustrations` (noms de base extraits des `\\includegraphics`
    de `course.tex` par `extract_illustration_basenames`, voir
    `run_fidelity_check`) permet de distinguer, pour un fichier présent dans
    `illustrations/` mais absent de `pivot.md`, deux causes distinctes :
    un vrai défaut de `/rag-extraction` (le fichier EST inséré dans
    `course.tex` mais n'a pas été repris), ou un fichier que
    `/cours-condense` lui-même n'a jamais inséré dans `course.tex` — dans ce
    second cas, `/rag-extraction` n'a structurellement aucune chance de le
    reprendre (il n'existe nulle part dans le PDF compilé), donc **non
    bloquant** ici, même principe que `check_figure_captions` pour une
    régression amont : jamais à corriger côté `/rag-extraction`."""
    if not illustrations_dir.exists():
        return [], 0, 0
    files = sorted(p.name for p in illustrations_dir.glob("*.png"))
    issues: list[FidelityIssue] = []

    if not illustrations_from_source:
        # Exclut les rasterisations de formule (page_NNN_formula_NN.png,
        # voir convert.py) du comptage : ce sont des crops de texte
        # mathematique, jamais des illustrations, et elles partagent le
        # meme images_dir des que course.tex/tex_segments sont indisponibles
        # — exactement le cas ou ce comptage de repli s'active. Sans ce
        # filtre, des formules rasterisees gonflaient artificiellement le
        # compte et masquaient de vraies illustrations manquantes (bug
        # trouve en revue de code).
        n_images = (
            sum(1 for p in images_dir.glob("*") if "_formula_" not in p.name)
            if images_dir.exists() else 0
        )
        if n_images < len(files):
            issues.append(FidelityIssue(
                "illustration", False,
                f"extraction native du PDF (noms opaques, pas de correspondance par nom possible) : "
                f"{n_images} image(s) dans images/ pour {len(files)} attendue(s) dans illustrations/ — "
                f"a verifier visuellement",
            ))
            return issues, len(files), min(n_images, len(files))
        return issues, len(files), len(files)

    verified = 0
    for name in files:
        referenced = name in pivot_text
        on_disk = (images_dir / name).exists()
        if referenced and on_disk:
            verified += 1
        elif not referenced:
            if course_tex_illustrations and name not in course_tex_illustrations:
                issues.append(FidelityIssue(
                    "illustration", False,
                    f"'{name}' présente dans illustrations/ mais jamais insérée dans course.tex "
                    f"lui-même (aucun \\includegraphics) — défaut de /cours-condense, pas de "
                    f"/rag-extraction",
                ))
            else:
                issues.append(FidelityIssue(
                    "illustration", True,
                    f"'{name}' présente dans illustrations/ mais absente de pivot.md (jamais référencée)",
                ))
        else:
            issues.append(FidelityIssue(
                "illustration", True,
                f"'{name}' référencée dans pivot.md mais introuvable dans images/ (fichier manquant)",
            ))
    return issues, len(files), verified


def check_broken_image_references(pivot_text: str, images_dir: Path) -> list[FidelityIssue]:
    """Vérification supplémentaire, indépendante de `course.tex` (s'applique
    donc même sans dossier de travail /cours-condense disponible) : toute
    référence `![Illustration](images/...)` dans `pivot.md` doit pointer
    vers un fichier qui existe réellement — une référence cassée serait
    invisible à l'utilisateur (l'image ne s'affiche simplement pas) tant
    qu'on ne vérifie pas explicitement chaque chemin."""
    issues: list[FidelityIssue] = []
    for m in _IMAGE_REF_RE.finditer(pivot_text):
        rel_path = m.group(1)
        full_path = images_dir.parent / rel_path
        if not full_path.exists():
            issues.append(FidelityIssue(
                "illustration", True,
                f"référence cassée dans pivot.md : '{rel_path}' n'existe pas sur disque",
            ))
    return issues


def check_code_blocks(course_tex_path: Path, pivot_text: str) -> tuple[list[FidelityIssue], int, int]:
    if not course_tex_path.exists():
        return [], 0, 0
    tex = course_tex_path.read_text(encoding="utf-8")
    expected_blocks = [_normalize_code(b) for b in extract_code_blocks(tex)]
    found_blocks = [_normalize_code(m) for m in _FENCE_RE.findall(pivot_text)]

    issues: list[FidelityIssue] = []
    verified = 0
    used = [False] * len(found_blocks)
    # Appariement GLOBAL (chaque bloc attendu cherche sa meilleure
    # correspondance parmi TOUS les blocs trouves non encore utilises),
    # plutot qu'un pointeur sequentiel a fenetre glissante : une premiere
    # version par fenetre locale s'est avérée fragile — un seul desaccord
    # structurel (ex. un bloc encore fragmente en deux malgre la fusion de
    # convert.py) faisait derailler le pointeur, et TOUS les blocs suivants
    # se comparaient alors a la mauvaise position, avec des scores
    # s'effondrant en cascade jusqu'a epuiser la liste bien avant la fin du
    # document (verifie empiriquement). La recherche globale n'a pas ce
    # mode d'echec : chaque bloc trouve sa vraie correspondance ou aucune,
    # independamment de l'etat des blocs voisins.
    for idx, expected in enumerate(expected_blocks):
        best_score, best_k = -1.0, None
        for k, found in enumerate(found_blocks):
            if used[k]:
                continue
            score = difflib.SequenceMatcher(None, expected, found).ratio()
            if score > best_score:
                best_score, best_k = score, k
        if best_k is not None:
            used[best_k] = True
        if best_score >= CODE_SIMILARITY_THRESHOLD:
            verified += 1
        else:
            issues.append(FidelityIssue(
                "code", True,
                f"bloc de code #{idx + 1} de course.tex non retrouvé à l'identique dans pivot.md "
                f"(meilleure correspondance : {max(best_score, 0):.0%}, seuil {CODE_SIMILARITY_THRESHOLD:.0%})",
            ))
    return issues, len(expected_blocks), verified


def check_formulas(course_tex_path: Path, pivot_text: str) -> tuple[list[FidelityIssue], int, int]:
    """Quand `course_tex_path` existe (seul cas où cette fonction fait plus
    que retourner tôt — voir `run_fidelity_check`), `convert.py` ne
    rasterise JAMAIS une formule non appariée, il la redescend
    systématiquement en prose (voir
    `_materialize_formula_runs_as_prose_fallback`) — donc aucune image
    `*_formula_*.png` n'existe jamais dans ce cas précis (cette fonction
    prenait autrefois `images_dir` pour vérifier un reliquat d'un
    comportement antérieur, retiré par revue de code : elle traitait alors
    à tort le cas "pas d'image restante" comme un BLOQUANT "perte de
    contenu probable", alors que le contenu est en réalité toujours présent
    — juste en prose approximative, jamais verbatim). Toute formule non
    retrouvée verbatim ici est donc **indicative**, jamais bloquante : c'est
    une dégradation connue et acceptée (formule trop large pour
    `_FORMULA_MAX_WIDTH`, ou run PDF non apparié avec confiance), pas une
    perte silencieuse."""
    if not course_tex_path.exists():
        return [], 0, 0
    tex = course_tex_path.read_text(encoding="utf-8")
    segments = extract_display_segments(tex)

    issues: list[FidelityIssue] = []
    verified_exact = 0

    for idx, seg in enumerate(segments):
        if seg["formula"] in pivot_text:
            verified_exact += 1
        else:
            issues.append(FidelityIssue(
                "formula", False,
                f"formule #{idx + 1} de course.tex non retrouvée à l'identique en texte dans pivot.md "
                f"— convertie en prose approximative (formule trop large ou non appariée avec confiance), "
                f"à vérifier manuellement si son contenu exact importe",
            ))
    return issues, len(segments), verified_exact


_SECTION_NUMBER_PREFIX_RE = re.compile(r"^\d+(?:\.\d+)*\s+")


def _normalize_heading(text: str) -> str:
    """Normalise un titre pour comparaison — y compris le préfixe de
    numérotation automatique (`\\thesection`, ex. "2.1 ") que LaTeX ajoute
    au rendu PDF mais qui n'existe évidemment pas dans le `\\section{...}`
    source : sans ce retrait, CHAQUE titre numéroté du PDF (donc la quasi
    totalité) se faisait à tort signaler comme absent (vérifié
    empiriquement — un livre entier retombait à ~15% de couverture de
    titres alors qu'ils étaient tous réellement présents)."""
    text = _SECTION_NUMBER_PREFIX_RE.sub("", text.strip())
    text = _COMMAND_RE.sub(" ", text)
    text = text.replace("{", " ").replace("}", " ")
    # "~" est l'espace insecable de LaTeX (typographie francaise avant
    # : ; ! ?) : un vrai caractere dans la source, un simple espace une
    # fois rendu dans le PDF — sans ce retrait, tout titre l'utilisant
    # (frequent en francais) se faisait a tort signaler absent.
    text = text.replace("~", " ")
    # "\og"/"\fg{}" (guillemets francais, babel-french) : deja retires par
    # _COMMAND_RE (commandes) et le remplacement des accolades ci-dessus, le
    # texte source ne porte donc plus aucune marque de guillemet a ce stade.
    # Le rendu PDF, lui, porte les vrais caracteres « / » (extraits tels
    # quels depuis pivot.md) — sans ce retrait cote pivot.md aussi, plus
    # aucun titre a guillemets ne matchait (l'inverse du probleme "$...$"
    # juste en dessous : ici c'est le cote pivot.md qui porte un caractere
    # que le cote course.tex n'a plus).
    text = text.replace("«", " ").replace("»", " ")
    # "$...$" (mode mathematique inline, ex. "estimer $f$") : pdflatex rend
    # le contenu (ici "f", en italique) mais jamais les symboles dollar
    # eux-memes — un titre source gardant "$f$" ne matchait donc jamais son
    # rendu PDF "f" sans ce retrait (verifie empiriquement : 7 titres a tort
    # signales absents sur un document a fort contenu mathematique, alors
    # que pivot.md les portait deja correctement). Simple retrait des
    # symboles $ (pas d'interpretation LaTeX du contenu interne) : suffisant
    # ici, les titres de chapitre/section ne contiennent jamais de maths
    # plus complexes qu'une variable isolee.
    text = text.replace("$", "")
    return re.sub(r"\s+", " ", text).strip().lower()


def check_section_headings(course_tex_path: Path, pivot_text: str) -> tuple[list[FidelityIssue], int, int]:
    if not course_tex_path.exists():
        return [], 0, 0
    tex = course_tex_path.read_text(encoding="utf-8")
    expected_titles = [_normalize_heading(t) for t in extract_headings(tex)]
    expected_titles = [t for t in expected_titles if t]
    found_titles = {_normalize_heading(t) for t in _MD_HEADING_RE.findall(pivot_text)}

    issues: list[FidelityIssue] = []
    verified = 0
    for idx, title in enumerate(expected_titles):
        if title in found_titles:
            verified += 1
        else:
            issues.append(FidelityIssue(
                "heading", True,
                f"titre #{idx + 1} de course.tex ('{title}') absent des titres Markdown de pivot.md",
            ))
    return issues, len(expected_titles), verified


def check_python_syntax(pivot_text: str) -> tuple[list[FidelityIssue], int, int]:
    """Vérifie que chaque bloc de code de `pivot.md` est syntaxiquement
    valide en Python (`ast.parse()`) — jamais bloquant : un bloc légitimement
    non-Python (commande shell, JSON, sortie de terminal...) échoue
    naturellement ce test sans être un défaut d'extraction, seulement un
    repère pour vérification manuelle rapide. Vérification indépendante de
    `course.tex` (s'applique donc même sans dossier de travail disponible),
    complémentaire à celle déjà faite en amont par `controle_qualite.py` de
    `/cours-condense` (qui, elle, valide `course.tex` avant compilation —
    ici on valide ce que `/rag-extraction` a réellement produit en aval,
    après tout le traitement de police/fusion/indentation)."""
    blocks = _FENCE_RE.findall(pivot_text)
    issues: list[FidelityIssue] = []
    valid = 0
    for idx, block in enumerate(blocks):
        try:
            ast.parse(block)
            valid += 1
        except SyntaxError as exc:
            issues.append(FidelityIssue(
                "code_syntax", False,
                f"bloc de code #{idx + 1} de pivot.md invalide en Python "
                f"(ligne {exc.lineno} : {exc.msg}) — normal si ce bloc n'est pas du Python (shell, JSON...)",
            ))
    return issues, len(blocks), valid


def check_figure_captions(course_tex_path: Path) -> tuple[list[FidelityIssue], int, int]:
    """Vérifie, pour chaque illustration insérée dans `course.tex`, que son
    `\\includegraphics` siège bien dans un environnement `figure` avec
    `\\caption` ET `\\label` — déjà garanti par
    `verifier_structure_course_tex.py` de `/cours-condense` (règle 7),
    donc redondant par construction. Conservé en défense en profondeur
    (coût négligeable) : jamais bloquant ici, une régression sur ce point
    relève de `/cours-condense`, pas de `/rag-extraction`."""
    if not course_tex_path.exists():
        return [], 0, 0
    tex = course_tex_path.read_text(encoding="utf-8")
    figures = [f for f in _FIGURE_ENV_RE.findall(tex) if "\\includegraphics" in f]

    issues: list[FidelityIssue] = []
    verified = 0
    for idx, fig in enumerate(figures):
        missing = []
        if "\\caption" not in fig:
            missing.append("caption")
        if "\\label" not in fig:
            missing.append("label")
        if missing:
            issues.append(FidelityIssue(
                "illustration", False,
                f"figure #{idx + 1} de course.tex incomplète (manque : {', '.join(missing)})",
            ))
        else:
            verified += 1
    return issues, len(figures), verified


def run_fidelity_check(
    pivot_path: Path, images_dir: Path, courscondense_dir: Path, *,
    illustrations_from_source: bool = True, page_range_active: bool = False,
) -> FidelityReport:
    """Point d'entrée unique, appelé par `/rag-extraction` juste après avoir
    écrit `pivot.md`. `courscondense_dir` peut ne pas exister (dossier de
    travail de /cours-condense nettoyé, ou PDF condensé produit autrement) —
    dans ce cas seule la vérification des références cassées (volet
    indépendant de course.tex) s'applique, le reste est marqué `skipped`.

    `illustrations_from_source` doit venir de
    `ExtractionResult.illustrations_from_source` (voir `convert.py`) — sans
    cette information, `check_illustrations` chercherait à tort les noms
    sémantiques de `illustrations/` dans `pivot.md` même quand `convert.py`
    est retombé sur l'extraction native à noms opaques, et signalerait
    alors TOUTES les illustrations comme manquantes à tort (bug trouvé en
    revue de code).

    `page_range_active=True` (l'appelant a utilisé `--pages`) désactive de
    la même façon que `convert.py` (voir son paramètre `page_range`) toute
    comparaison à `course.tex` : `pivot.md` ne couvre alors qu'un
    sous-ensemble de pages, jamais le livre entier, donc comparer ce
    sous-ensemble à la totalité de `course.tex` inonderait le rapport de
    faux BLOQUANT (titres/blocs de code/illustrations hors de la plage
    demandée, jamais réellement manquants) — bug trouvé en revue de code."""
    pivot_text = pivot_path.read_text(encoding="utf-8")
    report = FidelityReport()

    report.issues += check_broken_image_references(pivot_text, images_dir)

    syntax_issues, n_syntax_checked, n_syntax_valid = check_python_syntax(pivot_text)
    report.issues += syntax_issues
    report.n_code_blocks_syntax_checked, report.n_code_blocks_syntax_valid = n_syntax_checked, n_syntax_valid

    if page_range_active:
        report.skipped = True
        return report

    course_tex_path = courscondense_dir / "course.tex"
    illustrations_dir = courscondense_dir / "illustrations"
    if not courscondense_dir.exists():
        report.skipped = True
        return report

    course_tex_illustrations = frozenset(
        extract_illustration_basenames(course_tex_path.read_text(encoding="utf-8"))
    ) if course_tex_path.exists() else frozenset()

    illus_issues, n_illus_exp, n_illus_ver = check_illustrations(
        illustrations_dir, pivot_text, images_dir, illustrations_from_source,
        course_tex_illustrations=course_tex_illustrations,
    )
    report.issues += illus_issues
    report.n_illustrations_expected, report.n_illustrations_verified = n_illus_exp, n_illus_ver

    code_issues, n_code_exp, n_code_ver = check_code_blocks(course_tex_path, pivot_text)
    report.issues += code_issues
    report.n_code_blocks_expected, report.n_code_blocks_verified = n_code_exp, n_code_ver

    formula_issues, n_f_exp, n_f_ver = check_formulas(course_tex_path, pivot_text)
    report.issues += formula_issues
    report.n_formulas_expected = n_f_exp
    report.n_formulas_verified_exact = n_f_ver

    heading_issues, n_head_exp, n_head_ver = check_section_headings(course_tex_path, pivot_text)
    report.issues += heading_issues
    report.n_headings_expected, report.n_headings_verified = n_head_exp, n_head_ver

    figure_issues, n_fig_exp, n_fig_ver = check_figure_captions(course_tex_path)
    report.issues += figure_issues
    report.n_figures_expected, report.n_figures_complete = n_fig_exp, n_fig_ver

    return report
