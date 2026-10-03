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
import json
import re
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
CONTENT_DIR = _PROJECT_ROOT / "rag_data" / "content"

EXEMPT_LANGS = {"output", "console", "text", "txt", "result", "resultat", "sortie"}
_FENCE_BLOCK_RE = re.compile(r"^```([^\n`]*)\n(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)[^)]*\)")
_SRC_RE = re.compile(r"<!--\s*src:\s*([^>]*?)\s*-->")
_DISPLAY_FORMULA_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_LEAK_RE = re.compile(r"rag-nottext|DESCRIPTION DU PIPELINE|TRANSCRIPTION OCR|\bchunks?\b|\[S\d{3}\]", re.IGNORECASE)
_ELISION_RE = re.compile(r"^\s*(#|//)?\s*(\.\.\.|…)\s*$")
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
    ap.add_argument("target", help="slug (sous rag_data/content/) ou chemin du dossier de contenu")
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

    # --- fuites internes dans le texte lisible (hors commentaires src) -------
    readable = _SRC_RE.sub("", body)
    readable = re.sub(r"<!--.*?-->", "", readable, flags=re.DOTALL)
    for m in _LEAK_RE.finditer(_FENCE_BLOCK_RE.sub("", readable)):
        errors.append(f"fuite interne dans le texte : {m.group(0)!r}")

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
