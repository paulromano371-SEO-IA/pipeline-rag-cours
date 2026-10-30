"""Etape 3 de /create-content : controle DETERMINISTE de l'article contre le
dossier (aucun appel LLM). Le RAG est la seule source de faits : ce script
verifie mecaniquement ce qui peut l'etre.

Bloquant (code de sortie 2) :
  - code : chaque bloc ``` (hors langage output/console/text) doit reprendre le
    code d'un chunk du dossier a l'identique -- toutes ses lignes (les lignes
    "..." / "# ..." d'elision sont tolerees) doivent exister dans le code du dossier ;
  - images : chaque image de l'article existe dans <dossier>/images/ ET vient
    du dossier ; texte alternatif non vide ;
  - citations : chaque <!--src:Sxxx--> designe un identifiant du dossier ;
  - fuites : aucun marqueur interne du pipeline (rag-nottext, [DESCRIPTION DU
    PIPELINE], "chunk", identifiants [Sxxx] visibles) dans le texte lisible ;
  - references interdites : aux pdf sources (« dans ce pdf », « l'auteur »,
    « chapitre 3 », mots pdf/ouvrage/auteur) ou a un autre contenu du site
    (titre d'un article existant, « dans notre article »...) ;
  - structure : frontmatter (title, description, slug), un seul H1, >= 3 H2 ;
  - voix : aucune marque de vouvoiement (vous/votre/vos) -- le projet impose
    le tutoiement, uniformement.

Avertissements (n'echouent pas, sauf --strict) :
  - formule d'affichage $$...$$ absente du dossier (derivee par le redacteur ?) ;
  - code / formule / image du dossier jamais utilise (couverture) ;
  - paragraphe > 120 mots, ou > 80 mots sans citation <!--src:--> (remplissage
    possible) ;
  - variable utilisee dans un bloc de code sans definition visible dans un
    bloc precedent -- heuristique signalant qu'une elision a peut-etre coupe
    une ligne necessaire (voir Regles d'or, code verbatim) ;
  - legende d'image sans aucun mot en commun avec la description canonique de
    cette image dans le dossier -- signale un risque de legende racontant un
    autre exemple que celui montre ;
  - gras de lecture en diagonale : expression > 8 mots, 2 gras a moins de 15
    mots, > 2 par paragraphe, gras consecutifs sur des puces, section H2 sans
    gras, puce de « L'essentiel » sans gras, densite hors 60-150 mots/gras,
    meme expression en gras deux fois ;
  - faible taux de matiere distinctive citee (signal de risque commodity, voir
    gather.py / dossier.json).

Pas de borne de longueur : le nombre de mots est indique a titre informatif
seulement, jamais un avertissement -- la pedagogie prime, jamais un chiffre.

Usage:
    python verify.py <slug_ou_dossier> [--article article.md] [--strict]
"""
from __future__ import annotations
# Garde-fou : force l'interpreteur du projet (.venv-rag), voir _rag_lib/venv_guard.py
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(next(p for p in _Path(__file__).resolve().parents if (p / ".claude" / "skills" / "_rag_lib").is_dir()) / ".claude" / "skills" / "_rag_lib"))
import venv_guard  # noqa: F401

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

_PROJECT_ROOT = Path(__file__).resolve().parents[5]
CONTENT_DIR = _PROJECT_ROOT / "rag_data" / "content" / "work"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import toc as toc_mod  # noqa: E402  (sommaire cliquable : meme code que toc.py)

EXEMPT_LANGS = {"output", "console", "text", "txt", "result", "resultat", "sortie"}
_FENCE_BLOCK_RE = re.compile(r"^```([^\n`]*)\n(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)[^)]*\)")
_SRC_RE = re.compile(r"<!--\s*src:\s*([^>]*?)\s*-->")
_DISPLAY_FORMULA_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_LEAK_RE = re.compile(r"rag-nottext|DESCRIPTION DU PIPELINE|TRANSCRIPTION OCR|\bchunks?\b|\[S\d{3}\]", re.IGNORECASE)
_ELISION_RE = re.compile(r"^\s*(#|//)?\s*(\.\.\.|…)\s*$")

# --- references interdites : aux pdf sources, aux autres articles ---------
# L'article doit se tenir seul : jamais « dans ce pdf », « l'auteur explique »,
# ni « dans notre article sur... » / titre d'un autre contenu. Les liens vers
# d'autres contenus se decident a la publication (champ `related`), pas ici.
_APO = r"['’]"
_BOOK_REF_STRONG_RE = re.compile(
    r"\b(?:ce|cet|cette|ces|le|la|les|un|une|l|notre|nos|d|du|des|au|aux)(?:" + _APO + r"|\s+)"
    r"(?:premier\s+|second\s+|dernier\s+|autre\s+|autres\s+)?"
    r"(?:livres?|ouvrages?|manuels?|chapitres?|auteurs?|autrices?)\b",
    re.IGNORECASE,
)
_BOOK_LOCATOR_RE = re.compile(r"\b(?:chapitre|page|section|figure|listing|tableau)\s+\d+(?:[.,]\d+)*\b", re.IGNORECASE)
_BOOK_WORD_RE = re.compile(r"\b(?:livres?|ouvrages?|manuels?|auteurs?|autrices?|chapitres?)\b", re.IGNORECASE)
_ARTICLE_REF_RES = [
    re.compile(r"\b(?:notre|nos)\s+(?:autres?\s+)?(?:articles?|billets?|tutoriels?|guides?)\b", re.IGNORECASE),
    re.compile(r"\bdans\s+l" + _APO + r"article\b", re.IGNORECASE),
    re.compile(r"\bl" + _APO + r"article\s+(?:cité|précédent|voisin|complémentaire|dédié|plus haut|ci-dessus|sur\b|«|\")", re.IGNORECASE),
    re.compile(r"\b(?:un\s+)?(?:autre|précédent|prochain|dernier)\s+article\b", re.IGNORECASE),
    re.compile(r"\barticles?\s+(?:cité|cités|précédent|précédents|voisin|voisins|complémentaire|complémentaires)\b", re.IGNORECASE),
    re.compile(r"\b(?:voir|lis|lire|consulte|consulter|renvoie|renvoi)\b[^.\n]{0,40}\barticles?\b", re.IGNORECASE),
    re.compile(r"\barticle\s+original\b", re.IGNORECASE),
]
# attribution a des auteurs par leur nom (« Edge et ses collègues », « Sarmah et al. »)
_AUTHOR_ATTRIB_RE = re.compile(r"\bet\s+(?:ses|leurs)\s+collègues\b|\bet\s+al\b\.?", re.IGNORECASE)
# langage de bloc de code seul sur une ligne, hors fence : clôtures ``` perdues
_BARE_LANG_LINE_RE = re.compile(r"^(?:python|py|bash|sh|shell|powershell|sql|cypher|json|yaml|text)\s*$", re.MULTILINE)
_VOUVOIEMENT_RE = re.compile(r"\b(vous|votre|vos)\b", re.IGNORECASE)

# --- detection heuristique de variables non definies (pas un vrai parseur
# Python : approximatif par construction, pense pour reperer les elisions qui
# auraient coupe une ligne de definition necessaire) --------------------------
import keyword as _keyword_mod  # noqa: E402

_PY_KEYWORDS = set(_keyword_mod.kwlist) | set(getattr(_keyword_mod, "softkwlist", []))
_PY_BUILTINS_AND_ALIASES = {
    "self", "cls",
    "np", "pd", "sm", "skl", "plt", "sns", "os", "sys", "math", "re", "json",
    "print", "len", "range", "str", "int", "float", "list", "dict", "set", "tuple",
    "type", "zip", "map", "filter", "sum", "min", "max", "sorted", "enumerate",
    "open", "input", "abs", "round", "isinstance", "super", "next", "iter",
}
# chaines (y compris triple-guillemets, prefixe f/r/b/u optionnel) et commentaires
_STRING_RE = re.compile(
    r'[rRbBuUfF]{0,2}(?:"""(?:.*?)"""|\'\'\'(?:.*?)\'\'\'|"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')',
    re.DOTALL,
)
_REPL_PROMPT_RE = re.compile(r"^\s*(In|Out)\s*\[\d+\]:\s?")
_REPL_OUT_RE = re.compile(r"^\s*Out\s*\[\d+\]:")
_ASSIGN_TARGETS_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*)*)\s*=(?!=)")
_IMPORT_NAME_RE = re.compile(r"^import\s+([A-Za-z_][A-Za-z0-9_.]*)(?:\s+as\s+([A-Za-z_][A-Za-z0-9_]*))?")
_FROM_IMPORT_RE = re.compile(r"^from\s+[A-Za-z_][A-Za-z0-9_.]*\s+import\s+(.+)")
_FOR_TARGET_RE = re.compile(r"\bfor\s+([A-Za-z_][A-Za-z0-9_]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*)*)\s+in\b")
_WITH_AS_RE = re.compile(r"\bas\s+([A-Za-z_][A-Za-z0-9_]*)")
_DEF_TARGET_RE = re.compile(r"^def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)")
_CLASS_TARGET_RE = re.compile(r"^class\s+([A-Za-z_][A-Za-z0-9_]*)")
_LAMBDA_PARAMS_RE = re.compile(r"\blambda\s+([^:]*):")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DUNDER_RE = re.compile(r"^__[A-Za-z0-9_]+__$")

# --- detection heuristique de legende incoherente avec l'image -------------
_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\-]{3,}")
_GENERIC_CAPTION_WORDS = {
    "image", "figure", "graphique", "graphiques", "schema", "schéma", "montre",
    "montrent", "illustre", "illustrant", "illustrent", "represente", "représente",
    "represents", "deux", "trois", "quatre", "gauche", "droite", "haut", "bas",
    "nuage", "nuages", "points", "point", "courbe", "courbes", "droites",
    "titre", "intitule", "intitulé", "axe", "axes", "abscisse", "abscisses",
    "ordonnee", "ordonnée", "valeur", "valeurs", "chaque", "entre", "avec",
    "sans", "dans", "pour", "cette", "ces", "les", "des", "une", "un", "donnees",
    "données", "comparaison", "comparant", "compare", "illustration", "cote",
    "côte", "côté", "ligne", "lignes", "diagramme", "represent",
}


def _content_words(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text) if w.lower() not in _GENERIC_CAPTION_WORDS}


def _code_input_lines(fence_body: str) -> list[str]:
    """Isole les lignes d'ENTREE d'un bloc de code (REPL In/Out ou script
    classique), en retirant les sections Out[n]: -- ce sont des affichages,
    jamais du code, et les inclure produirait de faux positifs massifs."""
    lines = fence_body.splitlines()
    if not any(_REPL_PROMPT_RE.match(ln) for ln in lines):
        return lines
    out: list[str] = []
    in_output = False
    for ln in lines:
        if _REPL_OUT_RE.match(ln):
            in_output = True
            continue
        m = _REPL_PROMPT_RE.match(ln)
        if m:
            in_output = False
            out.append(_REPL_PROMPT_RE.sub("", ln))
        elif not in_output:
            out.append(ln)
    return out


def _strip_strings_and_comments(text: str) -> str:
    """Retire le contenu des chaines (y compris triple-guillemets multi-
    lignes -- en preservant le nombre de sauts de ligne pour ne pas fausser
    le decoupage In/Out) et des commentaires `#`. Heuristique, pas un lexer
    Python : suffisant pour ne plus confondre du texte litteral avec du code."""
    text = _STRING_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return "\n".join(re.sub(r"#.*", "", ln) for ln in text.split("\n"))


def _names_defined_by_line(line: str) -> set[str]:
    """Noms qu'une ligne de code definit (assignation, import, for, with..as,
    parametres de def/lambda) -- utilise a la fois pour peupler la portee
    connue et pour exclure ces lignes du scan d'usage (une ligne qui DEFINIT
    un nom ne l'UTILISE pas au sens ou on l'entend ici)."""
    defined: set[str] = set()
    am = _ASSIGN_TARGETS_RE.match(line)
    if am:
        defined.update(t.strip() for t in am.group(1).split(","))
    im = _IMPORT_NAME_RE.match(line)
    if im:
        defined.add(im.group(2) or im.group(1).split(".")[0])
    fim = _FROM_IMPORT_RE.match(line)
    if fim:
        for part in fim.group(1).split(","):
            part = part.strip()
            name = part.split(" as ")[-1].strip() if " as " in part else part.split(".")[0].strip()
            if name and name != "*":
                defined.add(name)
    for fm in _FOR_TARGET_RE.finditer(line):
        defined.update(t.strip() for t in fm.group(1).split(","))
    for wm in _WITH_AS_RE.finditer(line):
        defined.add(wm.group(1))
    dm = _DEF_TARGET_RE.match(line)
    if dm:
        defined.add(dm.group(1))
        for param in dm.group(2).split(","):
            name = param.strip().split(":")[0].split("=")[0].strip().lstrip("*")
            if name:
                defined.add(name)
    for lm in _LAMBDA_PARAMS_RE.finditer(line):
        defined.update(p.strip() for p in lm.group(1).split(","))
    return defined


def _join_continuations(lines: list[str]) -> list[str]:
    """Fusionne les lignes coupees a l'interieur de parentheses/crochets/
    accolades (frequent dans les extraits PDF re-justifies, ex. une
    signature `def f(a,` suivie de `b):` sur la ligne d'apres) en une seule
    ligne logique -- sinon les parametres d'une signature multi-ligne ne
    sont jamais reconnus comme definis."""
    logical: list[str] = []
    buf = ""
    depth = 0
    for ln in lines:
        buf = f"{buf} {ln}".strip() if buf else ln
        depth += ln.count("(") + ln.count("[") + ln.count("{")
        depth -= ln.count(")") + ln.count("]") + ln.count("}")
        if depth <= 0:
            logical.append(buf)
            buf = ""
            depth = 0
    if buf:
        logical.append(buf)
    return logical


def check_undefined_names(body: str) -> list[str]:
    """Heuristique deterministe (pas un vrai analyseur Python) : dans chaque
    bloc de code, releve les identifiants utilises sans avoir ete definis
    dans ce bloc ou un bloc precedent de l'article -- pense pour reperer
    qu'une elision a coupe une ligne de definition necessaire. Les noms
    utilises en position d'appel (`nom(...)`) sont exemptes : ce sont tres
    souvent des fonctions d'un import non visible dans le fragment du
    dossier, pas des variables locales oubliees."""
    known: set[str] = set(_PY_BUILTINS_AND_ALIASES)
    found: list[str] = []
    for m in _FENCE_BLOCK_RE.finditer(body + "\n"):
        lang, raw_code = m.group(1).strip().lower(), m.group(2)
        if lang in EXEMPT_LANGS:
            continue
        cleaned = _strip_strings_and_comments(raw_code)
        code_lines = [
            ln.strip() for ln in _code_input_lines(cleaned)
            if ln.strip() and not _ELISION_RE.match(ln) and not ln.lstrip().startswith(("%", "!"))
        ]
        code_lines = _join_continuations(code_lines)

        # passe 1 : toutes les definitions du bloc, independamment de l'ordre
        # (une comprehension comme `[f(i) for i in range(n)]` definit `i`
        # sur une ligne qui peut suivre son usage a l'ecran)
        defined_in_block: set[str] = set()
        for line in code_lines:
            defined_in_block |= _names_defined_by_line(line)
        scope = known | defined_in_block

        # passe 2 : usages, en sautant les lignes qui ne font que definir
        # (import/def/class : leur propre nom n'est pas un "usage")
        seen_here: set[str] = set()
        for line in code_lines:
            if (_IMPORT_NAME_RE.match(line) or _FROM_IMPORT_RE.match(line)
                    or _DEF_TARGET_RE.match(line) or _CLASS_TARGET_RE.match(line)):
                continue
            am = _ASSIGN_TARGETS_RE.match(line)
            rhs = line[am.end():] if am else line
            for token_m in _IDENT_RE.finditer(rhs):
                name = token_m.group(0)
                if name in _PY_KEYWORDS or _DUNDER_RE.match(name) or name in scope or name in seen_here:
                    continue
                start = token_m.start()
                if start > 0 and rhs[start - 1] == ".":
                    continue  # attribut (obj.nom), pas une variable libre
                after = rhs[token_m.end():token_m.end() + 2]
                if after.startswith("=") and not after.startswith("=="):
                    continue  # argument nomme d'un appel (kwarg=...)
                if rhs[token_m.end():].lstrip().startswith("("):
                    continue  # appel : suppose defini par un import hors du fragment
                found.append(
                    f"variable '{name}' utilisee sans definition visible dans un bloc de code "
                    f"precedent -- une elision a-t-elle coupe sa definition ? : {line[:70]!r}"
                )
                seen_here.add(name)
        known |= defined_in_block
    return found


def check_caption_consistency(body_no_code: str, items: list[dict]) -> list[str]:
    """Heuristique : verifie qu'au moins un mot plein de la description
    canonique d'une image (dossier.json) se retrouve dans sa legende ou le
    texte qui l'entoure dans l'article -- signale une legende qui pourrait
    raconter un autre exemple que celui que l'image montre reellement."""
    dossier_alt: dict[str, set[str]] = {}
    for it in items:
        for img in it.get("images", []):
            wp = img.get("web_path")
            if wp and img.get("alt"):
                dossier_alt.setdefault(wp, set()).update(_content_words(img["alt"]))

    found: list[str] = []
    paragraphs = re.split(r"\n\s*\n", body_no_code)
    for idx, para in enumerate(paragraphs):
        for m in _IMAGE_RE.finditer(para):
            alt, path = m.group(1), m.group(2)
            ref_words = dossier_alt.get(path)
            if not ref_words or len(ref_words) < 3:
                continue  # description du dossier trop pauvre pour juger
            context = " ".join(paragraphs[max(0, idx - 1):idx + 2])
            article_words = _content_words(alt) | _content_words(context)
            if not (ref_words & article_words):
                found.append(
                    f"legende de l'image {path} sans mot en commun avec la description du dossier "
                    f"-- verifie qu'elle decrit bien ce que montre l'image, pas un autre exemple "
                    f"(dossier : {', '.join(sorted(ref_words)[:6])})"
                )
    return found


# --- duplication de texte avec les autres contenus (AVERTISSEMENTS seulement) ---
# Jamais bloquant : le redacteur a recu les voisins a eviter ; ce controle sert a
# lui montrer ce qui a quand meme fuite. Hors perimetre (peuvent servir un autre
# argument) : images, formules, blocs de code, commentaires, sommaire.
_SHINGLE = 10                 # mots consecutifs identiques = passage commun
_SENT_MIN_WORDS = 8
_SENT_SIM = 0.80
_MAX_REPORTED = 6
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+|\n+")
_MD_NOISE_RE = re.compile(r"^[\s>\-*|]+|[*_`|]")


def prose_of(text: str) -> str:
    _, body = split_frontmatter(text)
    body = _FENCE_BLOCK_RE.sub("", body)
    body = _DISPLAY_FORMULA_RE.sub("", body)
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    body = _IMAGE_RE.sub("", body)
    return toc_mod._TOC_RE.sub("", body)


def prose_tokens(prose: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(prose)]


def prose_sentences(prose: str) -> list[list[str]]:
    out = []
    for raw in _SENT_SPLIT_RE.split(prose):
        toks = prose_tokens(_MD_NOISE_RE.sub("", raw))
        if len(toks) >= _SENT_MIN_WORDS:
            out.append(toks)
    return out


def shared_runs(a: list[str], b: list[str], n: int = _SHINGLE) -> list[tuple[int, int]]:
    """Passages de `a` (debut, fin en mots) qui apparaissent aussi dans `b`,
    sur au moins `n` mots consecutifs ; les chevauchements sont fusionnes."""
    if len(a) < n or len(b) < n:
        return []
    grams_b = {tuple(b[i:i + n]) for i in range(len(b) - n + 1)}
    runs: list[list[int]] = []
    for i in range(len(a) - n + 1):
        if tuple(a[i:i + n]) in grams_b:
            if runs and i <= runs[-1][1]:
                runs[-1][1] = i + n
            else:
                runs.append([i, i + n])
    return [(s, e) for s, e in runs]


def declared_neighbors(content_dir: Path) -> set[str]:
    try:
        data = json.loads((content_dir / "voisins.json").read_text(encoding="utf-8"))
        return {n["slug"] for n in data.get("neighbors", [])}
    except (OSError, ValueError, KeyError):
        return set()


def check_duplication(content_dir: Path, article_text: str) -> list[str]:
    neighbors = declared_neighbors(content_dir)
    own_prose = prose_of(article_text)
    own_tokens = prose_tokens(own_prose)
    own_sents = prose_sentences(own_prose)
    own_h2 = {toc_mod.norm_title(t): t for t in toc_mod.h2_titles(split_frontmatter(article_text)[1])
              if not toc_mod.is_essentiel(t)}
    warnings: list[str] = []
    for other_dir in sorted(p for p in CONTENT_DIR.iterdir() if p.is_dir()):
        if other_dir.resolve() == content_dir.resolve() or not (other_dir / "article.md").exists():
            continue
        try:
            other_text = (other_dir / "article.md").read_text(encoding="utf-8")
        except OSError:
            continue
        other_prose = prose_of(other_text)
        other_tokens = prose_tokens(other_prose)
        found: list[str] = []

        runs = shared_runs(own_tokens, other_tokens)
        if runs:
            covered = sum(e - s for s, e in runs)
            share = covered / max(1, len(own_tokens))
            longest = sorted(runs, key=lambda r: r[0] - r[1])[:_MAX_REPORTED]
            samples = " | ".join("« " + " ".join(own_tokens[s:min(e, s + 14)]) + (" ..." if e - s > 14 else "") + " »"
                                  for s, e in longest)
            found.append(f"{len(runs)} passage(s) de >= {_SHINGLE} mots identiques ({share:.0%} de l'article) : {samples}")

        other_sents = prose_sentences(other_prose)
        other_sets = [(toks, set(toks)) for toks in other_sents]
        near = []
        for toks in own_sents:
            sa = set(toks)
            best, best_ratio = None, 0.0
            for otoks, sb in other_sets:
                if len(sa & sb) / len(sa | sb) < 0.5:
                    continue
                ratio = difflib.SequenceMatcher(None, toks, otoks).ratio()
                if ratio > best_ratio:
                    best, best_ratio = otoks, ratio
            if best is not None and best_ratio >= _SENT_SIM:
                near.append((best_ratio, toks))
        if near:
            near.sort(key=lambda x: -x[0])
            samples = " | ".join(f"{r:.0%} « " + " ".join(t[:16]) + (" ..." if len(t) > 16 else "") + " »"
                                  for r, t in near[:_MAX_REPORTED])
            found.append(f"{len(near)} phrase(s) quasi identique(s) : {samples}")

        other_h2 = {toc_mod.norm_title(t): t for t in toc_mod.h2_titles(split_frontmatter(other_text)[1])
                    if not toc_mod.is_essentiel(t)}
        common_h2 = [own_h2[k] for k in own_h2 if k in other_h2]
        # Des titres generiques (« Pieges et limites »...) reviennent partout : on ne
        # les signale que pour un voisin declare ou un contenu deja en doublon de texte.
        if common_h2 and (other_dir.name in neighbors or found):
            found.append(f"titre(s) H2 identique(s), plan a differencier : {', '.join(repr(t) for t in common_h2)}")

        if found:
            warnings.append(f"DUPLICATION avec '{other_dir.name}' -- " + " ;; ".join(found))
    return warnings


# --- facettes du catalogue de l'intention (AVERTISSEMENTS seulement) -----------
# Miroir de la table de SKILL.md (etape 2). Chaque facette : (libelle, motif
# insensible a la casse/accents qui la reconnait dans plan.md ou dans une
# etiquette `--query "<facette>::..."`).
FACET_CATALOG: dict[str, list[tuple[str, str]]] = {
    "definition": [("nature / ce que c'est", r"nature|ce que c.est"), ("a quoi ca sert", r"sert|utilit"),
                   ("composants et fonctionnement", r"composant|fonctionnement"),
                   ("place dans son ecosysteme", r"ecosysteme|place"), ("exemple concret", r"exemple"),
                   ("idees recues", r"idees? recue")],
    "decision": [("criteres de decision", r"critere"), ("signaux et seuils chiffres", r"signaux?|seuil"),
                 ("contre-indications", r"contre"), ("alternatives", r"alternative"),
                 ("cout / benefice", r"cout|benefice"), ("cas limites", r"cas limite")],
    "procedure": [("prerequis", r"prerequis"), ("etapes dans l'ordre", r"etape"),
                  ("commande ou code", r"commande|code"), ("parametres cles", r"parametre"),
                  ("verifier le resultat", r"verifier"), ("erreurs frequentes", r"erreur")],
    "comparaison": [("axes de comparaison", r"axe"), ("forces et faiblesses", r"force|faiblesse"),
                    ("cas d'usage de chacune", r"cas d.usage"), ("critere de choix", r"critere")],
    "causalite": [("mecanisme", r"mecanisme"), ("cause racine", r"cause"), ("preuves et chiffres", r"preuve|chiffre"),
                  ("consequences", r"consequence"), ("exceptions", r"exception")],
    "optimisation": [("symptome et mesure", r"symptome|mesure"), ("cause probable", r"cause"),
                     ("levier d'action", r"levier"), ("effet attendu", r"effet attendu"),
                     ("effets de bord", r"effets? de bord")],
    "panorama": [("critere de classement", r"classement|critere"), ("chaque element", r"element"),
                 ("comment choisir", r"choisir")],
}
_INTENT_RE = re.compile(r"(definition|decision|procedure|comparaison|causalite|optimisation|diagnostic|panorama)")
_RETIRED_RE = re.compile(r"renvoi|hors perimetre|ecarte|deja couvert|couverte? par")


def _fold(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def check_facets(content_dir: Path, dossier: dict) -> list[str]:
    """plan.md doit contenir une ligne par facette du catalogue de l'intention
    retenue (sous-requete etiquetee, ou retrait justifie : renvoi / hors
    perimetre). Avertissements seulement."""
    plan_path = content_dir / "plan.md"
    if not plan_path.exists():
        return ["plan.md absent : cadrage, voisins, tableau des facettes et plan doivent y etre consignes (SKILL.md, etapes 0-3)"]
    plan = _fold(plan_path.read_text(encoding="utf-8"))
    out: list[str] = []
    m = re.search(r"intention[^\n]*", plan)
    intent_m = _INTENT_RE.search(m.group(0)) if m else None
    if not intent_m:
        return ["plan.md : intention non identifiee (famille attendue dans la section Cadrage : definition, decision, procedure, comparaison, causalite, optimisation/diagnostic, panorama)"]
    key = intent_m.group(1)
    key = {"diagnostic": "optimisation"}.get(key, key)
    fs = re.search(r"##\s*facettes(.*?)(?=\n##\s|\Z)", plan, re.S)
    facet_section = fs.group(1) if fs else ""
    if not facet_section.strip():
        out.append("plan.md : section « Facettes » absente ou vide (tableau facette -> voisin -> difference attendu)")
    tagged = _fold(" ".join(f for f in (dossier.get("query_facets") or {}).values() if f))
    untagged = [q for q, f in (dossier.get("query_facets") or {}).items() if not f and q != dossier.get("expression")]
    if untagged:
        out.append(f"{len(untagged)} sous-requete(s) sans facette etiquetee dans gather.py : {untagged}")
    for label, pat in FACET_CATALOG[key]:
        rows = [ln for ln in facet_section.splitlines() if re.search(pat, ln)]
        if not rows:
            out.append(f"facette du catalogue « {label} » ({key}) absente du tableau de plan.md -- la traiter, ou la retirer avec une raison ecrite")
        elif not re.search(pat, tagged) and not any(_RETIRED_RE.search(r) for r in rows):
            out.append(f"facette « {label} » : aucune sous-requete etiquetee ni retrait justifie (renvoi / hors perimetre) dans plan.md")
    return out


# --- gras de lecture en diagonale (AVERTISSEMENTS seulement) --------------------
# Le gras sert au lecteur qui survole : il doit retrouver le fil de l'argument
# en ne lisant que les passages en gras. Seuils (voir SKILL.md, passe de gras) :
BOLD_MAX_WORDS = 8            # longueur maximale d'une expression en gras
BOLD_MIN_GAP_WORDS = 15       # mots minimum entre deux gras d'un meme paragraphe
BOLD_MAX_PER_PARAGRAPH = 2    # maximum par paragraphe de prose (1 par puce)
BOLD_WORDS_PER_BOLD = (60, 150)  # densite tolerée : mots de prose pour 1 gras (cible 80-120)
_BOLD_RE = re.compile(r"\*\*(?!\s)(.+?)(?<!\s)\*\*")
_CALLOUT_LABEL_RE = re.compile(r"^\s*>\s*\*\*[\U0001F300-\U0001FAFF☀-➿️]+[^*]*\*\*")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


def _bold_blocks(body: str) -> list[dict]:
    """Decoupe le texte lisible en blocs (paragraphes de prose ou puces) avec
    leur H2. Ecarte code, titres, images, formules, tableaux, sommaire et
    etiquettes d'encadre (> **💡 Intuition**)."""
    text = _SRC_RE.sub("", body)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = _FENCE_BLOCK_RE.sub("", text)
    blocks: list[dict] = []
    h2 = ""
    for para in re.split(r"\n\s*\n", text):
        lines = [ln for ln in para.splitlines() if ln.strip()]
        if not lines:
            continue
        first = lines[0].lstrip()
        if first.startswith("## "):
            h2 = first[3:].strip()
            lines = lines[1:]
            if not lines:
                continue
            first = lines[0].lstrip()
        if first.startswith(("#", "![", "$$", "|", "**Sommaire**")):
            continue
        if all(ln.lstrip().startswith("- [") for ln in lines):
            continue  # sommaire cliquable
        if all(_LIST_ITEM_RE.match(ln) or ln.startswith((" ", "\t")) for ln in lines):
            items: list[str] = []
            for ln in lines:
                if _LIST_ITEM_RE.match(ln) or not items:
                    items.append(_LIST_ITEM_RE.sub("", ln, count=1))
                else:
                    items[-1] += " " + ln.strip()
            for it in items:
                blocks.append({"h2": h2, "kind": "item", "text": it.strip()})
            blocks[-1]["last_of_list"] = True
            continue
        cleaned = " ".join(_CALLOUT_LABEL_RE.sub(">", ln).lstrip("> ").strip() for ln in lines)
        blocks.append({"h2": h2, "kind": "para", "text": cleaned})
    return blocks


def check_bold(body: str) -> list[str]:
    warns: list[str] = []
    blocks = _bold_blocks(body)
    ess = toc_mod.ESSENTIEL_TITLE
    seen: dict[str, int] = {}
    per_h2: dict[str, int] = {}
    prose_words = 0
    total_bold = 0
    prev_item_had_bold = False
    for b in blocks:
        t, h2 = b["text"], b["h2"]
        spans = [(m.start(), m.end(), m.group(1)) for m in _BOLD_RE.finditer(t)]
        words = len(re.findall(r"\w+", _BOLD_RE.sub("", t))) + sum(len(re.findall(r"\w+", s[2])) for s in spans)
        prose_words += words
        total_bold += len(spans)
        per_h2.setdefault(h2, 0)
        per_h2[h2] += len(spans)
        in_ess = h2.strip().lower() == ess.strip().lower()
        shown = norm_ws(t)[:50]
        for _, _, s in spans:
            n = len(re.findall(r"\w+", s))
            if n > BOLD_MAX_WORDS:
                warns.append(f"gras trop long ({n} mots, max {BOLD_MAX_WORDS}) : **{s[:60]}**")
            key = s.strip().lower()
            seen[key] = seen.get(key, 0) + 1
        if spans and words >= 6 and len(re.findall(r"\w+", _BOLD_RE.sub("", t))) <= 3:
            warns.append(f"paragraphe presque entierement en gras : {shown}...")
        if b["kind"] == "para":
            if len(spans) > BOLD_MAX_PER_PARAGRAPH:
                warns.append(f"{len(spans)} gras dans un meme paragraphe (max {BOLD_MAX_PER_PARAGRAPH}) : {shown}...")
            for (s0, e0, _), (s1, _, _) in zip(spans, spans[1:]):
                gap = len(re.findall(r"\w+", t[e0:s1]))
                if gap < BOLD_MIN_GAP_WORDS:
                    warns.append(f"deux gras a {gap} mots d'ecart (min {BOLD_MIN_GAP_WORDS}) : {shown}...")
            prev_item_had_bold = False
        else:
            if len(spans) > 1:
                warns.append(f"{len(spans)} gras dans une meme puce (max 1) : {shown}...")
            if in_ess and not spans:
                warns.append(f"puce de « {ess} » sans expression-cle en gras : {shown}...")
            if not in_ess and spans and prev_item_had_bold:
                warns.append(f"gras sur deux puces consecutives : {shown}...")
            prev_item_had_bold = bool(spans)
            if b.get("last_of_list"):
                prev_item_had_bold = False
    for k, n in seen.items():
        if n > 1:
            warns.append(f"meme expression en gras {n} fois : **{k[:50]}** (une seule fois, a sa premiere apparition utile)")
    for h2, n in per_h2.items():
        if h2 and n == 0:
            warns.append(f"section « {h2} » sans aucun gras")
    if prose_words:
        if total_bold == 0:
            warns.append("aucun gras dans l'article (lecture en diagonale impossible)")
        else:
            wpb = prose_words / total_bold
            lo, hi = BOLD_WORDS_PER_BOLD
            if wpb > hi:
                warns.append(f"gras trop rares : 1 pour {wpb:.0f} mots (cible 80-120, tolere {lo}-{hi})")
            elif wpb < lo:
                warns.append(f"gras trop nombreux : 1 pour {wpb:.0f} mots (cible 80-120, tolere {lo}-{hi})")
    return warns


def check_references(content_dir: Path, text: str) -> list[str]:
    """Erreurs (bloquantes) : reference a un pdf source (« dans ce pdf »,
    « l'auteur », « chapitre 3 »...) ou a un autre contenu du site (titre d'un
    article existant, « dans notre article »...). Les mots isoles pdf /
    ouvrage / auteur ne sont signales que s'ils restent apres les motifs forts."""
    errs: list[str] = []
    seen: set[str] = set()

    def add(label: str, m: re.Match) -> None:
        frag = norm_ws(text[max(0, m.start() - 25): m.end() + 25])
        key = f"{label}:{m.group(0).lower()}"
        if key not in seen:
            seen.add(key)
            errs.append(f"{label} : {m.group(0)!r} (...{frag}...) -- reformule sans citer d'ouvrage ni d'auteur")

    strong_spans: list[tuple[int, int]] = []
    for m in _BOOK_REF_STRONG_RE.finditer(text):
        strong_spans.append(m.span())
        add("reference a un livre source", m)
    for m in _BOOK_LOCATOR_RE.finditer(text):
        add("localisation dans un livre source", m)
    for m in _BOOK_WORD_RE.finditer(text):
        if not any(a <= m.start() < b for a, b in strong_spans):
            add("mot renvoyant aux livres sources", m)

    for rx in _ARTICLE_REF_RES:
        for m in rx.finditer(text):
            add("reference a un autre article", m)

    for m in _AUTHOR_ATTRIB_RE.finditer(text):
        add("attribution a des auteurs par leur nom", m)

    folded_text = _fold(text)
    if CONTENT_DIR.is_dir():
        for other in sorted(CONTENT_DIR.iterdir()):
            other_article = other / "article.md"
            if not other.is_dir() or other.resolve() == content_dir.resolve() or not other_article.exists():
                continue
            title = split_frontmatter(other_article.read_text(encoding="utf-8"))[0].get("title", "").strip()
            ft = _fold(title)
            if len(ft) >= 15 and ft in folded_text:
                errs.append(f"reference a un autre contenu : le titre « {title} » ({other.name}) apparait dans l'article "
                            "-- les liens vers d'autres contenus se decident a la publication (champ `related`), pas dans le texte")
    return errs


def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def strip_ws(s: str) -> str:
    return re.sub(r"\s+", "", s)


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}, text
    fm: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "-")):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip("\"'")
    return fm, text[m.end():]


def dossier_code_lines(items: list[dict]) -> set[str]:
    """Toutes les lignes (espaces normalises) des blocs ``` du dossier."""
    lines: set[str] = set()
    for it in items:
        for m in _FENCE_BLOCK_RE.finditer(it["text"] + "\n"):
            for ln in m.group(2).splitlines():
                if ln.strip():
                    lines.add(norm_ws(ln))
        # chunk de code dont la cloture a ete tronquee : on garde tout apres l'ouverture
        if it["has_code"] and it["text"].count("```") % 2 == 1:
            tail = it["text"].split("```", 1)[1]
            for ln in tail.splitlines()[1:]:
                if ln.strip():
                    lines.add(norm_ws(ln))
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="slug (sous rag_data/content/work/) ou chemin du dossier de contenu")
    ap.add_argument("--article", default="article.md")
    ap.add_argument("--strict", action="store_true", help="les avertissements deviennent bloquants")
    args = ap.parse_args()

    content_dir = Path(args.target) if Path(args.target).is_dir() else CONTENT_DIR / args.target
    dossier_path, article_path = content_dir / "dossier.json", content_dir / args.article
    for p in (dossier_path, article_path):
        if not p.exists():
            print(f"ECHEC : fichier introuvable : {p}")
            return 1

    dossier = json.loads(dossier_path.read_text(encoding="utf-8"))
    items = dossier["items"]
    ids = {it["id"] for it in items}
    raw = article_path.read_text(encoding="utf-8")
    fm, body = split_frontmatter(raw)

    errors: list[str] = []
    warnings: list[str] = []

    # --- structure -----------------------------------------------------------
    for key in ("title", "description", "slug"):
        if not fm.get(key):
            errors.append(f"frontmatter : champ '{key}' manquant ou vide")
    if fm.get("description") and not 80 <= len(fm["description"]) <= 170:
        warnings.append(f"description SEO de {len(fm['description'])} caracteres (cible 80-170)")
    body_nocode = _FENCE_BLOCK_RE.sub("", body)
    h1 = re.findall(r"^# (?!#)", body_nocode, re.MULTILINE)
    h2 = re.findall(r"^## (?!#)", body_nocode, re.MULTILINE)
    if len(h1) != 1:
        errors.append(f"{len(h1)} titre(s) H1 (exactement 1 attendu)")
    if len(h2) < 3:
        errors.append(f"{len(h2)} section(s) H2 (au moins 3 attendues)")

    # --- blocs fixes : « L'essentiel en 30 secondes » + Sommaire cliquable juste avant ---
    h2_list = toc_mod.h2_titles(body)
    ess_match = next((m for m in toc_mod._H2_RE.finditer(body) if toc_mod.is_essentiel(toc_mod.plain_title(m.group(1)))), None)
    if ess_match is None:
        errors.append(f"H2 obligatoire absent : '## {toc_mod.ESSENTIEL_TITLE}'")
    else:
        toc_entries = toc_mod.parse_toc(body)
        if toc_entries is None:
            errors.append("sommaire cliquable absent (a placer juste avant « L'essentiel en 30 secondes ») -- "
                          "lance toc.py <slug> qui l'ecrit tout seul")
        else:
            expected = list(zip(h2_list, toc_mod.unique_ids(h2_list)))
            if [(norm, a) for norm, a in toc_entries] != expected:
                errors.append("sommaire cliquable different des H2 de l'article (titre ou ancre) -- relance toc.py <slug>")
            toc_end = toc_mod._TOC_RE.search(body).end()
            if body[toc_end:ess_match.start()].strip():
                errors.append("le sommaire doit etre JUSTE avant « L'essentiel en 30 secondes » (rien entre les deux)")
            if toc_mod._TOC_RE.search(body).start() > ess_match.start():
                errors.append("le sommaire est place apres « L'essentiel en 30 secondes » : il doit le preceder")

    # --- fuites internes dans le texte lisible (hors commentaires src) -------
    readable = _SRC_RE.sub("", body)
    readable = re.sub(r"<!--.*?-->", "", readable, flags=re.DOTALL)
    for m in _LEAK_RE.finditer(_FENCE_BLOCK_RE.sub("", readable)):
        errors.append(f"fuite interne dans le texte : {m.group(0)!r}")

    # --- blocs de code : clôtures ``` perdues ----------------------------------
    if body.count("```") % 2 == 1:
        errors.append("bloc de code non clos : nombre impair de ``` dans l'article")
    for m in _BARE_LANG_LINE_RE.finditer(_FENCE_BLOCK_RE.sub("", body)):
        errors.append(f"bloc de code sans ``` d'ouverture : ligne « {m.group(0).strip()} » seule")

    # --- references interdites : pdf sources, autres articles -------------
    scan_text = _FENCE_BLOCK_RE.sub("", readable) + "\n" + fm.get("title", "") + "\n" + fm.get("description", "")
    errors.extend(check_references(content_dir, scan_text))

    # --- voix : tutoiement impose, aucun vouvoiement ------------------------
    vouv_hits = _VOUVOIEMENT_RE.findall(_FENCE_BLOCK_RE.sub("", readable))
    if vouv_hits:
        distinct = sorted({w.lower() for w in vouv_hits})
        errors.append(f"voix non uniforme : {len(vouv_hits)} marque(s) de vouvoiement "
                       f"({', '.join(distinct)}) -- le projet impose le tutoiement ('tu'), reformule.")

    # --- citations -----------------------------------------------------------
    cited: set[str] = set()
    for m in _SRC_RE.finditer(body):
        for sid in re.split(r"[,\s]+", m.group(1).strip()):
            if not sid:
                continue
            if sid not in ids:
                errors.append(f"citation <!--src:{sid}--> inconnue du dossier")
            else:
                cited.add(sid)
    if not cited:
        warnings.append("aucune citation <!--src:Sxxx--> : tracabilite impossible")

    # --- code verbatim -------------------------------------------------------
    known_code = dossier_code_lines(items)
    used_code_ids: set[str] = set()
    for m in _FENCE_BLOCK_RE.finditer(body + "\n"):
        lang, code = m.group(1).strip().lower(), m.group(2)
        if lang in EXEMPT_LANGS:
            continue
        missing = []
        for ln in code.splitlines():
            if not ln.strip() or _ELISION_RE.match(ln):
                continue
            if norm_ws(ln) not in known_code:
                missing.append(ln.strip())
        if missing:
            shown = "; ".join(missing[:3]) + (" ..." if len(missing) > 3 else "")
            errors.append(f"bloc de code non fidele au dossier ({len(missing)} ligne(s) absente(s)) : {shown}")
        else:
            first = next((norm_ws(x) for x in code.splitlines() if x.strip() and not _ELISION_RE.match(x)), "")
            for it in items:
                if it["has_code"] and first and first in {norm_ws(x) for x in it["text"].splitlines()}:
                    used_code_ids.add(it["id"])

    # --- images --------------------------------------------------------------
    allowed_images = {img["web_path"]: it["id"] for it in items for img in it["images"] if img["web_path"]}
    used_images: set[str] = set()
    for m in _IMAGE_RE.finditer(_FENCE_BLOCK_RE.sub("", body)):
        alt, path = m.group(1), m.group(2)
        if not alt.strip():
            errors.append(f"image {path} sans texte alternatif")
        if path not in allowed_images:
            errors.append(f"image {path} ne vient pas du dossier")
        elif not (content_dir / path).exists():
            errors.append(f"image {path} absente du disque")
        else:
            used_images.add(path)

    # --- formules ------------------------------------------------------------
    dossier_formula_text = strip_ws(" ".join(it["text"] for it in items if it["has_formula"]))
    used_formula_ids: set[str] = set()
    for m in _DISPLAY_FORMULA_RE.finditer(_FENCE_BLOCK_RE.sub("", body)):
        f = strip_ws(m.group(1))
        if f and f not in dossier_formula_text:
            warnings.append(f"formule absente du dossier (derivee ? a verifier) : $$ {norm_ws(m.group(1))[:70]} $$")
        elif f:
            for it in items:
                if it["has_formula"] and f in strip_ws(it["text"]):
                    used_formula_ids.add(it["id"])

    # --- couverture ----------------------------------------------------------
    unused_images = sorted(set(allowed_images) - used_images)
    if unused_images:
        warnings.append(f"{len(unused_images)} image(s) du dossier non utilisee(s) : {', '.join(unused_images)}")
    unused_formulas = [it["id"] for it in items if it["has_formula"] and it["id"] not in used_formula_ids]
    if unused_formulas:
        warnings.append(f"formule(s) du dossier non reprise(s) : {', '.join(unused_formulas)}")
    unused_code = [it["id"] for it in items if it["has_code"] and it["id"] not in used_code_ids]
    if unused_code:
        warnings.append(f"code du dossier non repris : {', '.join(unused_code)}")

    # --- lisibilite web (nombre de mots : informatif, aucune borne) ----------
    words = len(re.findall(r"\w+", _FENCE_BLOCK_RE.sub("", readable)))
    for para in re.split(r"\n\s*\n", _FENCE_BLOCK_RE.sub("", readable)):
        n = len(re.findall(r"\w+", para))
        if n > 120 and not para.lstrip().startswith(("|", "$$")):
            warnings.append(f"paragraphe de {n} mots (> 120) : {norm_ws(para)[:60]}...")

    # --- remplissage : prose longue sans aucune citation --------------------
    body_no_code = _FENCE_BLOCK_RE.sub("", body)
    for para in re.split(r"\n\s*\n", body_no_code):
        stripped = para.strip()
        if not stripped or stripped.startswith(("#", ">", "-", "|", "$$", "![", "*")):
            continue
        n = len(re.findall(r"\w+", _SRC_RE.sub("", stripped)))
        if n > 80 and "<!--src:" not in stripped:
            warnings.append(f"paragraphe de {n} mots sans citation <!--src:--> (remplissage possible) : "
                             f"{norm_ws(stripped)[:60]}...")

    # --- gras de lecture en diagonale (avertissements) ----------------------
    warnings.extend(check_bold(body))

    # --- variables de code non definies (elision qui aurait coupe une ligne) -
    warnings.extend(check_undefined_names(body))

    # --- coherence legende <-> description canonique de l'image --------------
    warnings.extend(check_caption_consistency(body_no_code, items))

    # --- taux de matiere distinctive citee (signal commodity, cf. gather.py) -
    distinctive_ids = {it["id"] for it in items if it.get("distinctive")}
    distinctive_cited = cited & distinctive_ids
    distinctive_rate = (len(distinctive_cited) / len(cited)) if cited else 0.0
    if cited and distinctive_ids and distinctive_rate < 0.20:
        warnings.append(f"faible taux de matiere distinctive citee ({distinctive_rate:.0%}) -- "
                         "risque de contenu commodity, voir le rapport final au lecteur")

    # --- duplication de texte avec les autres contenus (jamais bloquant) -----
    dup_warnings: list[str] = []
    if CONTENT_DIR.is_dir():
        dup_warnings = check_duplication(content_dir, raw)
    warnings.extend(dup_warnings)

    # --- facettes du catalogue de l'intention (plan.md) ---------------------
    warnings.extend(check_facets(content_dir, dossier))

    # --- compte rendu --------------------------------------------------------
    print(f"Verification de {article_path}")
    print(f"  Mots : {words}  (~{max(1, round(words / 200))} min de lecture)")
    print(f"  Sections H2 : {len(h2)}   Citations distinctes : {len(cited)}/{len(ids)} chunks du dossier")
    print(f"  Images utilisees : {len(used_images)}/{len(allowed_images)}   "
          f"Code repris : {len(used_code_ids)}   Formules reprises : {len(used_formula_ids)}")
    if distinctive_ids:
        print(f"  Matiere distinctive citee : {len(distinctive_cited)}/{len(cited)} "
              f"({distinctive_rate:.0%}) -- signal de non-commodity")
    print(f"  Livres pertinents (dossier) : {dossier.get('book_coverage', '?')}")
    print(f"  Duplication avec les autres contenus : "
          + (f"{len(dup_warnings)} contenu(s) concerne(s) (avertissements ci-dessous, non bloquants)" if dup_warnings
             else "aucune (ou aucun autre contenu a comparer)"))
    for e in errors:
        print(f"  ERREUR : {e}")
    for w in warnings:
        print(f"  AVERTISSEMENT : {w}")
    if errors or (args.strict and warnings):
        print("RESULTAT : ECHEC -- corriger l'article puis relancer verify.py.")
        return 2
    print("RESULTAT : OK" + (f" ({len(warnings)} avertissement(s))" if warnings else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
