"""Étape 5 de /create-content : sommaire puis vérification, boucle jusqu'à OK.

Usage : etape5_verification.py "<expression>"
Boucle : réparation déterministe des clôtures ``` perdues, puis toc.py (sommaire), puis verify.py
(contrôle mécanique). Code 2 de verify.py = erreurs : un appel claude -p corrige l'article
(jamais le script, jamais le dossier), puis on relance. Le sommaire et les clôtures sont
recalculés après chaque correction, et une vérification finale a lieu après le dernier tour.
Texte de l'étape : prompts/etape5.md, copié depuis SKILL.md.
Codes de sortie : 0 OK (verify.py sans erreur), 2 échec ou boucle épuisée.

Cache : la correction reprend le même `system` que l'étape 4 (le dossier, via systeme_dossier),
pour lire le dossier depuis le cache écrit à l'étape 4.
"""
from __future__ import annotations

import subprocess
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
_sys.path.insert(0, str(_Path(__file__).resolve().parent / "lib"))

import sys

from commun import (EFFORT, MAX_ATTEMPTS, MODEL, TIMEOUT_S, EtapeError, _log_usage, content_dir,
                    load_prompt, statut, systeme_dossier)
from _claude_code_client_systemfile import ClaudeCodeCallError, run_claude_code
from etape3_plan import ID_DOSSIER_RE
from etape4_redaction import CONTRAT_SORTIE as CONTRAT_ARTICLE, make_parser as parse_article
from gather import slugify_expression
from verify import _BARE_LANG_LINE_RE

SCRIPTS = _Path(__file__).resolve().parent / "lib"
TOC = SCRIPTS / "toc.py"
VERIFY = SCRIPTS / "verify.py"

# Plafond de tours de correction : la skill dit « boucle jusqu'à OK » sans borne ; ce plafond
# évite une boucle sans fin. À valider avec l'utilisateur.
MAX_TOURS = 5

# Consigne ajoutée à la correction : le sommaire est écrit par toc.py, jamais par le modèle.
CONSIGNE_SOMMAIRE = (
    "\n\nSi l'article contient un bloc « **Sommaire** » (liste de liens vers les H2), "
    "conserve-le tel quel : il est généré par un script, tu ne le rédiges pas et tu ne le supprimes pas."
)


def retirer_enveloppe(raw: str) -> str:
    """Retire seulement une enveloppe ``` englobant toute la réponse. Contrairement à
    `strip_code_fence` (utilisé par `call_claude`), les ``` internes des blocs de code sont conservés."""
    texte = raw.strip()
    lignes = texte.split("\n")
    if len(lignes) >= 2 and lignes[0].startswith("```") and lignes[-1].strip() == "```":
        return "\n".join(lignes[1:-1]).strip()
    return texte


def appel(slug: str, etape: str, system: str, user_text: str, parse, dossier: _Path):
    """Équivalent de `call_claude` sans le retrait des clôtures ``` (voir `retirer_enveloppe`)."""
    derniere = ""
    for essai in range(1, MAX_ATTEMPTS + 1):
        meta: dict = {}
        try:
            raw = run_claude_code(system, user_text, model=MODEL, timeout=TIMEOUT_S, meta=meta, effort=EFFORT)
        except ClaudeCodeCallError as exc:
            derniere = f"appel claude -p en échec : {exc}"
        else:
            _log_usage(slug, etape, essai, meta, dossier)
            try:
                return parse(retirer_enveloppe(raw))
            except ValueError as exc:
                derniere = str(exc)
        print(f"  [{etape}] essai {essai}/{MAX_ATTEMPTS} : {derniere}", file=sys.stderr)
    raise EtapeError(derniere)


def lancer(script: _Path, slug: str) -> tuple[int, str]:
    proc = subprocess.run([sys.executable, str(script), slug], capture_output=True, text=True, encoding="utf-8")
    return proc.returncode, proc.stdout + proc.stderr


def reparer_fences(texte: str) -> str:
    """Rétablit les clôtures ``` perdues autour d'une ligne qui ne contient que le nom d'un
    langage (python, bash…). Hors bloc, c'est une ouverture perdue. Dans un bloc, c'est un bloc
    précédent resté non clos : on ferme celui-ci avant d'en ouvrir un nouveau. Un bloc encore
    ouvert en fin de texte reçoit sa fermeture."""
    sortie = []
    dedans = False
    for ligne in texte.split("\n"):
        if ligne.startswith("```"):
            dedans = not dedans
        elif _BARE_LANG_LINE_RE.match(ligne):
            if dedans:
                sortie.append("```")
            ligne = "```" + ligne.strip()
            dedans = True
        sortie.append(ligne)
    if dedans:
        sortie.append("```")
    return "\n".join(sortie)


def trouver_bloc_casse(lignes: list[str]) -> tuple[int, int] | None:
    """Indices (ouverture, fermeture) du premier bloc ``` qui contient un titre H2 : c'est un bloc
    dont la clôture a été perdue, et la prose qui suit est rendue dans le code."""
    ouverture = None
    for i, ligne in enumerate(lignes):
        if not ligne.startswith("```"):
            continue
        if ouverture is None:
            ouverture = i
            continue
        if any(l.startswith("## ") for l in lignes[ouverture + 1:i]):
            return ouverture, i
        ouverture = None
    return None


def corriger_bloc(texte: str, dossier_texte: str, dossier: _Path, slug: str) -> str | None:
    """Appel ciblé : seul le bloc défectueux est renvoyé corrigé, le reste de l'article ne bouge pas.
    Renvoie le texte complet corrigé, ou None si aucun bloc n'est en cause."""
    lignes = texte.split("\n")
    trouve = trouver_bloc_casse(lignes)
    if trouve is None:
        return None
    debut, fin = trouve
    fragment = "\n".join(lignes[debut:fin + 1])

    def parse(raw: str) -> str:
        frag = raw.strip()
        if not frag.startswith("```"):
            raise ValueError("le fragment doit commencer par la ligne ``` ouvrante")
        if frag.count("```") % 2 != 0:
            raise ValueError("nombre impair de ``` dans le fragment")
        premier = frag.split("\n", 1)[0]
        dans = False
        for ligne in frag.split("\n"):
            if ligne.startswith("```"):
                dans = not dans
            elif dans and ligne.startswith("## "):
                raise ValueError("un titre ## est resté dans le bloc de code")
        if not premier.startswith("```"):
            raise ValueError("ouverture invalide")
        return frag

    corrige = appel(
        slug, "etape5_bloc",
        system=systeme_dossier(dossier_texte),
        user_text=(
            "Le bloc de code ci-dessous est mal fermé : la prose qui suit a été rendue dans le code. "
            "Rends UNIQUEMENT ce fragment corrigé : le code (identique à celui du dossier, sans l'altérer) "
            "dans un bloc ``` correctement ouvert et fermé, puis la prose dans le texte normal, sans ``` "
            "autour, en gardant les titres ## et les citations <!--src:...--> telles quelles. "
            "Pas de texte avant ni après le fragment.\n\n"
            f"FRAGMENT :\n{fragment}"
        ),
        parse=parse,
        dossier=dossier,
    )
    return "\n".join(lignes[:debut] + corrige.split("\n") + lignes[fin + 1:])


def verifier(slug: str, tour: int) -> tuple[int, str]:
    """toc.py puis verify.py. Renvoie le code de verify.py et sa sortie."""
    code_toc, sortie_toc = lancer(TOC, slug)
    if code_toc != 0:
        return 3, f"toc.py en échec (code {code_toc}) : {sortie_toc}"
    return lancer(VERIFY, slug)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage : etape5_verification.py \"<expression>\"", file=sys.stderr)
        return 2
    expression = sys.argv[1].strip()
    slug = slugify_expression(expression)
    dossier = content_dir(slug)
    article_path = dossier / "article.md"
    dossier_path = dossier / "dossier.md"
    if not article_path.exists() or not dossier_path.exists():
        statut("5", False, "article.md ou dossier.md absent : lancer les étapes précédentes d'abord")
        return 2

    dossier_texte = dossier_path.read_text(encoding="utf-8")
    identifiants = set(ID_DOSSIER_RE.findall(dossier_texte))

    for tour in range(1, MAX_TOURS + 1):
        article = article_path.read_text(encoding="utf-8")
        reparé = reparer_fences(article)
        if reparé != article:
            article_path.write_text(reparé, encoding="utf-8")

        code, sortie = verifier(slug, tour)
        if code == 3:
            statut("5", False, sortie)
            return 2
        if code == 0:
            avertissements = sortie.count("AVERTISSEMENT")
            statut("5", True, f"verify.py OK au tour {tour} ({avertissements} avertissement(s) restant(s))")
            return 0
        if code != 2:
            statut("5", False, f"verify.py en échec inattendu (code {code})")
            return 2

        article = article_path.read_text(encoding="utf-8")
        try:
            ciblé = corriger_bloc(article, dossier_texte, dossier, slug)
            if ciblé is not None:
                article_path.write_text(reparer_fences(ciblé) + "\n", encoding="utf-8")
                continue
            corrige = appel(
                slug, f"etape5_correction_tour{tour}",
                system=systeme_dossier(dossier_texte),
                user_text=(
                    load_prompt("etape5.md") + CONTRAT_ARTICLE + CONSIGNE_SOMMAIRE + "\n\n"
                    f"ARTICLE :\n{article}\n\nSORTIE DE verify.py :\n{sortie}"
                ),
                parse=parse_article(slug, identifiants),
                dossier=dossier,
            )
        except EtapeError as exc:
            statut("5", False, f"correction non produite au tour {tour} : {exc}")
            return 2
        article_path.write_text(reparer_fences(corrige) + "\n", encoding="utf-8")

    # Dernier tour de correction : la version finale doit elle aussi être contrôlée.
    code, sortie = verifier(slug, MAX_TOURS + 1)
    if code == 0:
        avertissements = sortie.count("AVERTISSEMENT")
        statut("5", True, f"verify.py OK après la dernière correction ({avertissements} avertissement(s) restant(s))")
        return 0
    statut("5", False, f"verify.py encore en erreur après {MAX_TOURS} tours de correction")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
