"""Met à jour rag_data/expressions/EXPRESSIONS.md après chaque article analysé par /expressions-article.

Une ligne par « nouveau sujet à rédiger » (section « Nouveaux sujets à rédiger » du rapport) :
date + heure de l'analyse, slug de l'article source, sujet (forme qualifiée), commande
(question brute du jugement), pourquoi, passage, question (reformulation contextualisée), pdf et
passages des chunks (« pdf / passages »), puis contenu consistant probable (libellé sur cinq niveaux, calculé par le tableau de couverture × unicité).

- Question : jugement LLM à partir de sujet, pourquoi, passage et des connaissances générales,
  sans jamais relire l'article. Mis en cache (rag_data/expressions/cache/questions.json) : une même
  entrée n'est jamais redemandée. Les entrées non cachées partent par lots de BATCH_SIZE.
- Contenu consistant probable : aucun appel LLM. Le libellé vient de la couverture du corpus
  (pdf et passages des chunks, décomptes lexicaux) croisée avec l'unicité (aucun article du
  catalogue ne la traite).

Les lignes d'un slug sont remplacées à chaque analyse, jamais dupliquées ; un article sans
nouveau sujet n'a plus aucune ligne. Tableau trié du plus récent au plus ancien.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(next(p for p in _Path(__file__).resolve().parents if (p / ".claude" / "skills" / "_rag_lib").is_dir()) / ".claude" / "skills" / "_rag_lib"))
import venv_guard  # noqa: F401

import hashlib
import json
import sys
from typing import Callable

import paths
from _claude_code_client_systemfile import ClaudeCodeCallError, run_claude_code, strip_code_fence

from inc_question import couverture_question
from inc_usage import log_usage

EXPRESSIONS_DIR = paths.RAG_DATA_DIR / "expressions"
EXPRESSIONS_FILE = EXPRESSIONS_DIR / "EXPRESSIONS.md"
CACHE_DIR = EXPRESSIONS_DIR / "cache"
QUESTIONS_CACHE = CACHE_DIR / "questions.json"

MODEL_LLM = "claude-haiku-5-5"
EFFORT_LLM = "medium"
TIMEOUT_S = 600
BATCH_SIZE = 40
MAX_ATTEMPTS = 3

# Libellé spécial, réservé à une notion unique (unicité oui) dans 2 pdf ou plus et 10 passages ou plus.
OPPORTUNITE = "Opportunité"

# Tableau de consistance : couverture du corpus (lignes) × unicité (colonnes). Libellés fixes.
# Couverture : lexical.livres / lexical.passages (chunks). Ordre des tests = ordre du tableau.
TABLEAU_CONSISTANCE = {
    # couverture : (unicité oui, unicité non)
    "Forte": ("Très probable", "Probable"),
    "Moyenne": ("Probable", "Possible"),
    "Faible": ("Possible", "Peu probable"),
    "Absente": ("Peu probable", "Improbable"),
}

ENTETE_COLONNES = ("| Date | Slug | Sujet | Commande | Pourquoi | Passage | Question "
                   "| Sujet : livres / passages | Question : livres / passages | Contenu consistant probable ? |")
LIBELLE_NON_EVALUE = "Non évalué"

SYSTEM_QUESTIONS = """Tu rédiges les questions-titres d'un site pédagogique francophone sur le machine learning, le deep learning, les LLM et le RAG.

Pour chaque élément, tu reçois :
- « sujet » : le terme à traiter ;
- « pourquoi » : la raison pour laquelle ce terme mérite un article ;
- « passage » : une phrase de livre qui l'emploie ;
- « commande_actuelle » : une première question, à améliorer si elle est peu claire.

Tu n'as que ces champs et tes propres connaissances. Tu n'as pas accès à l'article d'origine : n'invente aucun fait, ne cite aucun chiffre, ne décris pas le contenu de l'article. Ton rôle est seulement de dire, à partir de ces indices, de quel objet il s'agit et de le formuler comme une question claire.

Règles de formulation :
1. Une question par élément, en français, de la forme « Qu'est-ce qu'une … ? », « Qu'est-ce qu'un … ? », « Qu'est-ce que le … ? » ou « Qu'est-ce que la … ? », selon le genre et la nature de l'objet.
2. Qualifie le terme par son domaine seulement si le mot seul est vague ou ambigu dans ce contexte : « vraisemblance » devient « une vraisemblance en probabilité ». Ne qualifie pas un terme déjà clair (« une régression linéaire »).
3. Un outil, un framework ou une bibliothèque est une entité nommée : « Qu'est-ce que le framework LangChain ? », jamais « Qu'est-ce qu'un LangChain ? ».
4. Un indicateur ou une métrique se présente comme une mesure, avec le domaine où on la lit : « Qu'est-ce que le TPS en inférence ? ». Ne l'appelle pas « un TPS ».
5. Une méthode ou un concept se formule comme tel : « Qu'est-ce qu'une régression logistique ? ».
6. Reste court : une seule phrase, qui se termine par « ? ».

Sortie : uniquement un tableau JSON, un objet par élément, dans le même ordre, sans texte autour :
[{"id": 0, "question": "Qu'est-ce que … ?"}, …]
Le champ « id » reprend exactement l'identifiant reçu."""

def _cle(*valeurs: str) -> str:
    """Clé de cache : hash des champs qui fondent le jugement."""
    return hashlib.sha256(json.dumps(list(valeurs), ensure_ascii=False).encode("utf-8")).hexdigest()


def _charger(chemin) -> dict:
    return json.loads(chemin.read_text(encoding="utf-8")) if chemin.exists() else {}


def _sauver(chemin, cache: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _appel_lot(system: str, charges: list[dict], valide: Callable[[dict, object], bool], champ: str) -> dict[int, object]:
    """Un appel claude -p pour un lot ; renvoie {id: valeur de `champ`}. Lève RuntimeError si aucune réponse exploitable."""
    user = json.dumps(charges, ensure_ascii=False, indent=2)
    derniere_erreur = ""
    for essai in range(1, MAX_ATTEMPTS + 1):
        meta: dict = {}
        try:
            raw = run_claude_code(system, user, model=MODEL_LLM, timeout=TIMEOUT_S, meta=meta, effort=EFFORT_LLM)
        except ClaudeCodeCallError as exc:
            derniere_erreur = f"appel claude -p en échec : {exc}"
        else:
            log_usage(CACHE_DIR, champ, "(lot)", essai, meta)
            try:
                data = json.loads(strip_code_fence(raw))
                if not isinstance(data, list):
                    raise ValueError("réponse qui n'est pas un tableau")
                resultats = {int(o["id"]): o[champ] for o in data}
                if set(resultats) == set(range(len(charges))) and all(valide(c, resultats[i]) for i, c in enumerate(charges)):
                    return resultats
                derniere_erreur = "identifiants manquants ou valeur invalide"
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                derniere_erreur = f"réponse inexploitable : {exc}"
        print(f"  [{champ}] essai {essai}/{MAX_ATTEMPTS} : {derniere_erreur}", file=sys.stderr)
    raise RuntimeError(derniere_erreur)


def _par_lots(system: str, payloads: list[dict], valide, champ: str, nom: str) -> dict[int, object]:
    """Interroge le LLM par lots de BATCH_SIZE ; renvoie {index dans payloads: valeur} pour les lots réussis.

    `payloads` ne contient que les champs que le LLM doit voir pour ce jugement."""
    obtenus: dict[int, object] = {}
    for debut in range(0, len(payloads), BATCH_SIZE):
        lot = payloads[debut:debut + BATCH_SIZE]
        charges = [{"id": i, **payload} for i, payload in enumerate(lot)]
        try:
            resultats = _appel_lot(system, charges, valide, champ)
        except RuntimeError as exc:
            print(f"ATTENTION : {nom} non formulé pour {len(lot)} entrée(s) : {exc}", file=sys.stderr)
            continue
        for i in range(len(lot)):
            obtenus[debut + i] = resultats[i]
    return obtenus


def formuler_questions(entrees: list[dict]) -> list[str]:
    """Une question par entrée (même ordre). Entrées : sujet, pourquoi, passage, titre_question.

    En cas d'échec du LLM sur un lot, la commande d'origine est gardée (avertissement sur stderr)."""
    cache = _charger(QUESTIONS_CACHE)
    cles = [_cle(e["sujet"], e["pourquoi"], e["passage"]) for e in entrees]
    manquantes = [i for i, cle in enumerate(cles) if cle not in cache]
    payloads = [{"sujet": entrees[i]["sujet"], "pourquoi": entrees[i]["pourquoi"],
                 "passage": entrees[i]["passage"], "commande_actuelle": entrees[i]["titre_question"]}
                for i in manquantes]
    obtenus = _par_lots(SYSTEM_QUESTIONS, payloads,
                        lambda c, v: isinstance(v, str) and bool(v.strip()), "question", "la question")
    for rang, i in enumerate(manquantes):
        if rang in obtenus:
            cache[cles[i]] = str(obtenus[rang]).strip()
    _sauver(QUESTIONS_CACHE, cache)
    return [cache.get(cle, e["titre_question"]) for cle, e in zip(cles, entrees)]


def couverture(livres: int | None, passages: int | None) -> str:
    """Forte, Moyenne, Faible ou Absente, selon le tableau de consistance (ordre des tests = ordre du tableau)."""
    L = livres or 0
    P = passages or 0
    if P >= 10 or (L >= 2 and P >= 5):
        return "Forte"
    if (L == 1 and 5 <= P <= 9) or (L >= 2 and 1 <= P <= 4):
        return "Moyenne"
    if L == 1 and 1 <= P <= 4:
        return "Faible"
    return "Absente"  # aucun passage dans les chunks


def consistance(livres: int | None, passages: int | None, unicite_oui: bool) -> str:
    """Libellé final : couverture du corpus croisée avec l'unicité.

    Cas particulier : une notion unique (unicité oui) présente dans 2 pdf ou plus et 10 passages
    ou plus est une Opportunité, et non un simple Très probable."""
    L = livres or 0
    P = passages or 0
    if unicite_oui and L >= 2 and P >= 10:
        return OPPORTUNITE
    oui, non = TABLEAU_CONSISTANCE[couverture(livres, passages)]
    return oui if unicite_oui else non


def entete_fichier() -> str:
    return (
        "# Nouveaux sujets à rédiger\n\n"
        "Alimenté par /expressions-article à chaque analyse d'article : une ligne par nouveau sujet "
        "(section « Nouveaux sujets à rédiger » du rapport `rag_data/expressions/work/<slug>/expressions.md`). "
        "La colonne Question est reformulée par le LLM à partir de Sujet, Pourquoi et Passage. "
        "Sujet : livres / passages qui contiennent le sujet mot pour mot (chunks, sans appel LLM). "
        "Question : livres / passages dont le jugement LLM (Haiku, effort medium) dit qu'ils répondent à la question, "
        "parmi les 40 passages les plus proches sémantiquement. La colonne Contenu consistant probable ? "
        "est calculée par règle sur les décomptes Question, croisée avec l'unicité.\n\n"
        f"{ENTETE_COLONNES}\n"
        "|---|---|---|---|---|---|---|---|---|---|\n"
    )


def mettre_a_jour_expressions(result: dict, generated_at: str) -> None:
    """Remplace les lignes de l'article `result` (sortie de process_article) dans EXPRESSIONS.md."""
    slug = result["slug"]

    def cell(valeur) -> str:
        return str(valeur).replace("|", "\\|").replace("\n", " ")

    nouveaux = [r for r in result["rows"] if r["categorie"] == "nouveau"]
    entrees = [{"sujet": r["forme_qualifiee"], "pourquoi": r["raison"], "passage": r["passage"],
                "titre_question": r["titre_question"]} for r in nouveaux]
    questions = formuler_questions(entrees) if entrees else []

    nouvelles_lignes = []
    for r, e, q in zip(nouveaux, entrees, questions):
        lx = r["lexical"]  # décomptes du sujet (mot pour mot dans les chunks)
        # Décomptes de la question : pdf et passages qui y répondent réellement (jugement LLM).
        qc = couverture_question(q)
        # Unicité : toutes les lignes « nouveau » sont par construction des notions sans article dédié.
        # La probabilité se lit sur la question, pas sur le sujet : c'est elle qui sera rédigée.
        c = consistance(qc["livres"], qc["passages"], unicite_oui=True) if qc else LIBELLE_NON_EVALUE
        commande = '"' + e["titre_question"] + '"'
        decompte_sujet = f"{lx['livres']} / {lx['passages']}"
        decompte_question = f"{qc['livres']} / {qc['passages']}" if qc else "?"
        nouvelles_lignes.append(
            f"| {generated_at} | `{slug}` | {cell(e['sujet'])} | {cell(commande)} "
            f"| {cell(e['pourquoi'])} | {cell(e['passage'])} | {cell(q)} | {cell(decompte_sujet)} "
            f"| {cell(decompte_question)} | {cell(c)} |"
        )

    lignes = EXPRESSIONS_FILE.read_text(encoding="utf-8").splitlines() if EXPRESSIONS_FILE.exists() else []
    donnees = [l for l in lignes if l.startswith("| 20")]  # une ligne de données = une ligne commençant par la date
    # La colonne 2 (index 2 après le « | » initial) est le slug : Date et Slug ne contiennent jamais de pipe.
    donnees = [l for l in donnees if l.split("|")[2].strip() != f"`{slug}`"]
    donnees.extend(nouvelles_lignes)
    # Plus récent en premier : la colonne Date (AAAA-MM-JJ HH:MM) se trie comme du texte ; tri stable,
    # donc l'ordre des lignes d'un même article est conservé.
    donnees.sort(key=lambda l: l.split("|")[1].strip(), reverse=True)
    EXPRESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    EXPRESSIONS_FILE.write_text(entete_fichier() + "\n".join(donnees) + ("\n" if donnees else ""), encoding="utf-8")
