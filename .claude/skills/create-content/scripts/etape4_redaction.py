"""Étape 4 de /create-content : rédiger rag_data/content/work/<slug>/article.md.

Usage : etape4_redaction.py "<expression>"
Lit plan.md (en entier) et dossier.md (en entier), écrit article.md : frontmatter SEO puis
corps Markdown. Le sommaire n'est pas écrit ici (étape 5, toc.py). Texte de l'étape :
prompts/etape4.md, copié depuis SKILL.md. Codes de sortie : 0 OK, 2 échec.
"""
from __future__ import annotations

import re
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
_sys.path.insert(0, str(_Path(__file__).resolve().parent / "lib"))

import sys

from commun import EtapeError, call_claude, content_dir, deja_fait, load_prompt, pop_force, statut, systeme_dossier
from etape3_plan import ID_DOSSIER_RE
from gather import slugify_expression

CHAMPS = ("title", "description", "slug", "keywords", "reading_time", "level", "related")
ESSENTIEL = "## L'essentiel en 30 secondes"
CITATION_RE = re.compile(r"<!--src:([^>]*)-->")
CHUNK_RE = re.compile(r"S\d{3}")
CODE_BLOCK_RE = re.compile(r"^```.*?^```[ \t]*$", re.MULTILINE | re.DOTALL)

CONTRAT_SORTIE = (
    "\n\nFORMAT DE RÉPONSE (imposé par le script, ne rien ajouter) : réponds UNIQUEMENT avec le contenu complet "
    "du fichier article.md : frontmatter YAML entre deux lignes « --- », puis le corps Markdown. Pas de bloc de code "
    "autour, pas de texte avant ou après. Ne rédige pas le sommaire : il est généré à l'étape suivante."
)


def retirer_enveloppe(raw: str) -> str:
    """Retire seulement le bloc de code qui entoure toute la réponse (```markdown … ```).

    Les clôtures internes (blocs de code de l'article) sont conservées : sinon le contrôle
    des titres H1 lirait les commentaires « # » des blocs de code comme des titres."""
    texte = raw.strip()
    lignes = texte.split("\n")
    if len(lignes) >= 2 and lignes[0].strip().startswith("```") and lignes[-1].strip() == "```":
        return "\n".join(lignes[1:-1]).strip()
    return texte


def make_parser(slug: str, identifiants: set[str]):
    def parse(raw: str) -> str:
        texte = raw.replace("\r\n", "\n").strip()
        if not texte.startswith("---\n"):
            raise ValueError("frontmatter absent (pas de « --- » en tête)")
        fin = texte.find("\n---", 4)
        if fin < 0:
            raise ValueError("frontmatter non fermé")
        frontmatter = texte[4:fin]
        corps = texte[fin + 4:]
        for champ in CHAMPS:
            if not re.search(rf"^{champ}\s*:", frontmatter, re.MULTILINE):
                raise ValueError(f"champ de frontmatter absent : {champ}")
        m = re.search(r"^slug\s*:\s*\"?([^\"\n]+?)\"?\s*$", frontmatter, re.MULTILINE)
        if not m or m.group(1).strip() != slug:
            raise ValueError(f"slug du frontmatter différent de « {slug} »")
        # Les lignes « # » d'un bloc de code (commentaires Python, shell…) ne sont pas des titres.
        hors_code = CODE_BLOCK_RE.sub("", corps)
        if len(re.findall(r"^# ", hors_code, re.MULTILINE)) != 1:
            raise ValueError("il faut exactement un titre H1")
        if ESSENTIEL not in corps:
            raise ValueError(f"section « {ESSENTIEL} » absente")
        citations = CITATION_RE.findall(corps)
        if not citations:
            raise ValueError("aucune citation <!--src:...--> dans le corps")
        cites = {c for bloc in citations for c in CHUNK_RE.findall(bloc)}
        inconnus = sorted(cites - identifiants)
        if inconnus:
            raise ValueError(f"citations vers des chunks absents du dossier : {inconnus}")
        return texte
    return parse


def main() -> int:
    force = pop_force()
    if len(sys.argv) != 2:
        print("usage : etape4_redaction.py \"<expression>\"", file=sys.stderr)
        return 2
    expression = sys.argv[1].strip()
    slug = slugify_expression(expression)
    if deja_fait("4", slug, force):
        return 0
    dossier = content_dir(slug)
    plan_path = dossier / "plan.md"
    dossier_path = dossier / "dossier.md"
    if not plan_path.exists() or not dossier_path.exists():
        statut("4", False, "plan.md ou dossier.md absent : lancer les étapes précédentes d'abord")
        return 2

    plan = plan_path.read_text(encoding="utf-8")
    dossier_texte = dossier_path.read_text(encoding="utf-8")
    identifiants = set(ID_DOSSIER_RE.findall(dossier_texte))

    try:
        article = call_claude(
            slug, "etape4_redaction",
            system=systeme_dossier(dossier_texte),
            user_text=(
                load_prompt("etape4.md") + CONTRAT_SORTIE + "\n\n"
                f"EXPRESSION : {expression}\nSLUG : {slug}\n\nPLAN :\n{plan}"
            ),
            parse=make_parser(slug, identifiants),
            dossier=dossier,
            nettoyer=retirer_enveloppe,
        )
    except EtapeError as exc:
        statut("4", False, f"article non produit : {exc}")
        return 2

    (dossier / "article.md").write_text(article + "\n", encoding="utf-8")
    statut("4", True, f"article.md écrit : {len(article.split())} mot(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
