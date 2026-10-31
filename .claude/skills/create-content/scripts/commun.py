"""Socle commun des étapes de /create-content exécutées par `claude -p`.

Chaque étape LLM : un appel `claude -p` (Haiku 5.5, effort moyen, timeout 600 s),
une ligne par appel dans `rag_data/content/work/<slug>/usage.jsonl`, et une ligne de
statut affichée à la session qui lance la commande.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(next(p for p in _Path(__file__).resolve().parents if (p / ".claude" / "skills" / "_rag_lib").is_dir()) / ".claude" / "skills" / "_rag_lib"))
import venv_guard  # noqa: F401

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import paths
from _claude_code_client_systemfile import ClaudeCodeCallError, run_claude_code, strip_code_fence

MODEL = "claude-haiku-5-5"
EFFORT = "medium"
TIMEOUT_S = 600
MAX_ATTEMPTS = 3

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


class EtapeError(RuntimeError):
    """L'étape n'a pas produit de sortie exploitable après MAX_ATTEMPTS essais."""


def content_dir(slug: str) -> Path:
    return paths.RAG_DATA_DIR / "content" / "work" / slug


CONTENT_INDEX = paths.RAG_DATA_DIR / "content" / "INDEX.md"
PUBLISHED_DIR = paths.RAG_DATA_DIR / "content" / "published"  # traces : published/<slug>.json
def entete_index(nb_contenus: int) -> str:
    return (
        "# Index des contenus\n\n"
        "Dossier des articles : `rag_data/content/work/<slug>/`. Une ligne par contenu, mise à jour "
        "à chaque génération par /create-content.\n\n"
        f"Contenus créés : **{nb_contenus}**\n\n"
        "| Mis à jour | Slug | Titre | Niveau | Lecture (min) | Article publié |\n"
        "|---|---|---|---|---|---|\n"
    )


def article_publie(slug: str) -> bool:
    """Publié = trace published/<slug>.json présente (écrite par /publish-content après création du brouillon)."""
    return (PUBLISHED_DIR / f"{slug}.json").exists()


def mettre_a_jour_index(slug: str) -> None:
    """Ajoute ou remplace la ligne de `slug` dans rag_data/content/INDEX.md (une ligne par contenu)."""
    texte = (content_dir(slug) / "article.md").read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    front = re.match(r"^---\n(.*?)\n---", texte, re.S)
    if front:
        for ligne in front.group(1).splitlines():
            cle, sep, val = ligne.partition(":")
            if sep:
                meta[cle.strip()] = val.strip().strip('"')

    def cell(valeur: str) -> str:
        return str(valeur).replace("|", "\\|").replace("\n", " ")

    # « Non » à la création ; « Oui » dès que /publish-content a une trace pour le slug (une régénération garde « Oui »).
    publie = "Oui" if article_publie(slug) else "Non"
    ligne = (
        f"| {datetime.now().strftime('%Y-%m-%d %H:%M')} | `{slug}` | {cell(meta.get('title', slug))} "
        f"| {cell(meta.get('level', ''))} | {cell(meta.get('reading_time', ''))} | {publie} |"
    )
    # La ligne est d'abord posée dans un fichier temporaire du slug, puis recopiée dans l'index sous verrou.
    temp = content_dir(slug) / "index_ligne.tmp"
    temp.write_text(ligne, encoding="utf-8")
    verrou = CONTENT_INDEX.with_name("INDEX.md.lock")
    CONTENT_INDEX.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(20):  # 20 essais, espacés de 5 s
        try:
            os.close(os.open(verrou, os.O_CREAT | os.O_EXCL | os.O_WRONLY))  # échoue si le verrou existe déjà
            break
        except FileExistsError:
            time.sleep(5)
    else:
        print(f"  [index] verrou occupé : ligne de `{slug}` gardée dans {temp}", file=sys.stderr)
        return
    try:
        ligne = temp.read_text(encoding="utf-8").strip()
        lignes = CONTENT_INDEX.read_text(encoding="utf-8").splitlines() if CONTENT_INDEX.exists() else []
        contenus = [l for l in lignes if l.startswith("| 20")]  # une ligne de données par contenu
        marque = f"| `{slug}` |"
        for i, l in enumerate(contenus):
            if marque in l:
                contenus[i] = ligne
                break
        else:
            contenus.append(ligne)
        # Plus récent en premier : la colonne « Mis à jour » (AAAA-MM-JJ HH:MM) se trie comme du texte.
        contenus.sort(key=lambda l: l.split("|")[1].strip(), reverse=True)
        CONTENT_INDEX.write_text(entete_index(len(contenus)) + "\n".join(contenus) + "\n", encoding="utf-8")
        temp.unlink()
    finally:
        verrou.unlink()


def load_prompt(name: str) -> str:
    """Texte de l'étape, tel que copié depuis SKILL.md (jamais modifié à la main)."""
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _log_usage(slug: str, etape: str, attempt: int, meta: dict, dossier: Path) -> None:
    line = {
        "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "api", "etape": etape, "article": slug, "essai": attempt, **meta,
    }
    path = dossier / "usage.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def _sauver_refus(dossier: Path, etape: str, attempt: int, erreur: str, raw: str) -> None:
    """Garde la sortie brute d'un essai refusé : rag_data/content/work/<slug>/refus/<etape>_essai<N>.md."""
    path = dossier / "refus" / f"{etape}_essai{attempt}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"<!-- refus : {erreur} -->\n{raw}", encoding="utf-8")


def call_claude(slug: str, etape: str, system: str, user_text: str, parse, dossier: Path | None = None, nettoyer=strip_code_fence):
    """Appel `claude -p` + validation par `parse(raw) -> valeur`.

    `parse` lève `ValueError` si la réponse n'est pas exploitable : nouvel essai.
    Un échec d'infrastructure (process, délai) est aussi retenté.
    `dossier` : dossier de sortie de l'article (usage.jsonl y est écrit) ; par défaut rag_data/content/work/<slug>/.
    `nettoyer` : retrait de l'enveloppe de la réponse avant `parse` (par défaut `strip_code_fence`).
    """
    dossier = dossier or content_dir(slug)
    last_error = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        meta: dict = {}
        try:
            raw = run_claude_code(system, user_text, model=MODEL, timeout=TIMEOUT_S, meta=meta, effort=EFFORT)
        except ClaudeCodeCallError as exc:
            last_error = f"appel claude -p en échec : {exc}"
        else:
            _log_usage(slug, etape, attempt, meta, dossier)
            try:
                return parse(nettoyer(raw))
            except ValueError as exc:
                last_error = str(exc)
                _sauver_refus(dossier, etape, attempt, last_error, raw)
        print(f"  [{etape}] essai {attempt}/{MAX_ATTEMPTS} : {last_error}", file=sys.stderr)
    raise EtapeError(last_error)


def systeme_dossier(dossier_texte: str) -> str:
    """Préfixe commun aux étapes qui lisent le dossier (3 et 4) : même texte, mêmes octets, en tête de requête.

    Le cache de prompt ne se partage que si le début de la requête est identique. Le dossier est donc
    l'unique contenu du `system` ; les consignes de chaque étape et le plan vont après, dans le message.
    """
    return "DOSSIER (lu en entier par les étapes qui le citent) :\n\n" + dossier_texte


def bloc_voisins(voisins: list[dict]) -> str:
    """Textes complets des voisins, identiques octet pour octet en 2a et en 2b : ouvrent leur `system` commun."""
    corps = "\n\n".join(
        f"<voisin slug=\"{v['slug']}\" titre=\"{v['titre']}\">\n{v['texte']}\n</voisin>" for v in voisins
    )
    return "VOISINS (lus en entier) :\n\n" + corps + "\n\n"


def entete_expression(expression: str, slug: str) -> str:
    """Tête commune aux étapes 0 et 1 (même expression, même slug) : elle ouvre le `system` des deux étapes."""
    return f"EXPRESSION : {expression}\nSLUG : {slug}\n\n"


def replace_section(text: str, title: str, body: str) -> str:
    """Remplace le contenu de la section `## <title>` de plan.md (ou l'ajoute en fin)."""
    block = f"## {title}\n\n{body.strip()}\n"
    pattern = re.compile(rf"^## {re.escape(title)}\s*$.*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)
    if pattern.search(text):
        return pattern.sub(lambda _m: block + "\n", text, count=1)
    return text.rstrip() + "\n\n" + block


def read_section(text: str, title: str) -> str | None:
    m = re.search(rf"^## {re.escape(title)}\s*$(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    return m.group(1).strip() if m else None


def statut(numero: str, ok: bool, detail: str) -> None:
    print(f"[étape {numero}] {'OK' if ok else 'ÉCHEC'} — {detail}")


# --- Reprise : une étape déjà faite est sautée, sauf --force ---------------------------------

def pop_force() -> bool:
    """Retire `--force` de sys.argv (pour que le contrôle d'arguments de l'étape reste inchangé)."""
    if "--force" in sys.argv:
        sys.argv.remove("--force")
        return True
    return False


def _lignes_tableau(section: str | None) -> list[list[str]]:
    lignes = [l.strip() for l in (section or "").splitlines() if l.strip().startswith("|")][2:]
    return [[c.strip() for c in l.strip("|").split("|")] for l in lignes]


def _fait_0(slug: str, plan: str) -> str | None:
    return "section Cadrage présente" if read_section(plan, "Cadrage") else None


def _fait_1(slug: str, plan: str) -> str | None:
    lignes = _lignes_tableau(read_section(plan, "Facettes"))
    ok = lignes and all(len(c) >= 2 and "::" in c[1] for c in lignes)
    return f"{len(lignes)} facette(s) avec sous-requête dans plan.md" if ok else None


def _fait_2a(slug: str, plan: str) -> str | None:
    ok = (content_dir(slug) / "voisins.json").exists() and read_section(plan, "Voisins")
    return "voisins.json et section Voisins présents" if ok else None


_VALEURS_D_ATTENTE = {"", "—", "-", "à interroger"}  # valeurs écrites par l'étape 1 avant le croisement


def _fait_2b(slug: str, plan: str) -> str | None:
    section = read_section(plan, "Facettes")
    if section and "Aucun voisin : page blanche" in section:
        return "aucun voisin, déjà noté dans plan.md"
    lignes = _lignes_tableau(section)
    ok = lignes and all(len(c) >= 4 and c[2] not in _VALEURS_D_ATTENTE and c[3] not in _VALEURS_D_ATTENTE for c in lignes)
    return "colonnes Voisin et Décision remplies dans plan.md" if ok else None


def _fait_2c(slug: str, plan: str) -> str | None:
    d = content_dir(slug)
    return "dossier.md et dossier.json présents" if (d / "dossier.md").exists() and (d / "dossier.json").exists() else None


def _fait_3(slug: str, plan: str) -> str | None:
    return "section Plan présente" if read_section(plan, "Plan") else None


def _fait_4(slug: str, plan: str) -> str | None:
    return "article.md présent" if (content_dir(slug) / "article.md").exists() else None


_FAIT = {"0": _fait_0, "1": _fait_1, "2a": _fait_2a, "2b": _fait_2b, "2c": _fait_2c, "3": _fait_3, "4": _fait_4}


def deja_fait(numero: str, slug: str, force: bool) -> bool:
    """True (et affiche `DÉJÀ FAIT`) si l'étape a déjà produit son résultat dans rag_data/content/work/<slug>/.

    Les étapes en aval ne sont pas invalidées : après un `--force`, relancer aussi les suivantes.
    """
    if force:
        return False
    plan_path = content_dir(slug) / "plan.md"
    plan = plan_path.read_text(encoding="utf-8") if plan_path.exists() else ""
    preuve = _FAIT[numero](slug, plan)
    if not preuve:
        return False
    print(f"[étape {numero}] DÉJÀ FAIT — {preuve} (relancer avec --force pour la refaire, puis refaire les étapes suivantes)")
    return True
