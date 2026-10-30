"""Appels LLM de /expressions-article (claude -p headless, via _claude_code_client_systemfile).

Cache de prompt : le `system` des DEUX appels est strictement identique (le corps de l'article seul).
Les consignes, le catalogue et les expressions vont dans le message utilisateur.
Le jugement relit ainsi l'article depuis le cache juste après l'extraction. Le `system` passe par
fichier (limite de ligne de commande Windows, WinError 206).

Deux appels par article :
  1. extract_candidates : repère les expressions techniques écrites dans l'article et
     les qualifie quand elles sont vagues hors contexte, avec des mots tirés de l'article.
     Le code vérifie MOT POUR MOT que l'expression et
     les mots ajoutés pour la qualifier existent dans l'article.
  2. judge_candidates : le LLM décide, à partir du texte, du catalogue des articles
     existants et de la forme qualifiée, si chaque expression (a) mérite un nouvel
     article, (b) a déjà son article, (c) n'est qu'une mention secondaire, ou (d) reste
     ambiguë. Le graphe de concepts n'est qu'une indication consultative : le LLM peut
     s'en écarter, et une absence du graphe n'exclut rien.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import sys
from pathlib import Path

import paths
from _claude_code_client_systemfile import ClaudeCodeCallError, run_claude_code, strip_code_fence

from inc_articles import Article, catalogue, fold, word_in
from inc_usage import log_cache_hit, log_usage

EXPRESSIONS_DIR = paths.RAG_DATA_DIR / "expressions" / "work"

MAX_ATTEMPTS = 3
TIMEOUT_S = 600
# Sans --model, claude -p utilise le modèle par défaut du compte (Opus, plus cher).
# Haiku 5.5 pour toutes les étapes. Limite connue : à l'extraction, Haiku a trouvé 19 expressions
# là où Opus en a trouvé 40 sur le même article (rappel plus faible).
MODEL_EXTRACTION = "claude-haiku-5-5"
MODEL_JUGEMENT = "claude-haiku-5-5"
# Effort de réflexion : les tokens de réflexion dominaient les sorties (jusqu'à 18 k par jugement).
EFFORT = "medium"
DECISIONS = {"nouveau", "deja_traite", "secondaire", "ambigu"}

# Mots qui ne comptent pas quand on vérifie qu'une qualification vient de l'article.
_QUALIFIER_STOPWORDS = {
    "de", "du", "des", "le", "la", "les", "l", "un", "une", "et", "ou", "en", "au", "aux",
    "d", "a", "pour", "par", "sur", "dans", "avec", "qu", "est", "ce", "que",
}

EXTRACTION_SYSTEM = (
    "Tu lis un article de vulgarisation en français sur l'intelligence artificielle et le "
    "machine learning. Repère les expressions qui NOMMENT un objet technique à expliquer : "
    "méthode, outil, concept, métrique, modèle, acronyme, phénomène. N'en retiens PAS "
    "les mots courants (« modèle », « données », « exemple ») ni les expressions décoratives.\n"
    "Pour chaque expression :\n"
    "1. `expression` : recopiée EXACTEMENT comme elle apparaît dans l'article.\n"
    "2. `passage` : une phrase EXACTE de l'article, recopiée mot pour mot, qui contient l'expression.\n"
    "3. `qualifiee` : la même expression, complétée UNIQUEMENT si, hors de ce passage, elle serait "
    "vague ou ambiguë, et seulement avec des mots qui figurent dans l'article. "
    "Si l'expression est claire telle quelle, recopie `expression`.\n"
    "Règles strictes : n'invente rien ; une expression absente de l'article est une erreur. "
    "Sois exhaustif : relis tout l'article et retiens chaque objet technique nommé, y compris les "
    "variantes d'une même famille (ex. une méthode et sa version avancée, agentique ou « naïve »), "
    "sans te limiter aux plus connues. Ne tronque pas la liste : maximum 60 expressions.\n"
    "Réponds UNIQUEMENT avec un tableau JSON d'objets {\"expression\": ..., \"passage\": ..., "
    "\"qualifiee\": ...}, sans texte ni balise autour."
)

JUDGEMENT_SYSTEM = (
    "Tu décides, pour chaque expression technique d'un article, si elle mérite un NOUVEL article "
    "web dédié, rédigé à partir du corpus de livres. Tu disposes :\n"
    "- de l'article complet ;\n"
    "- du CATALOGUE des articles déjà publiés (slug et titre) ;\n"
    "- pour chaque expression, sa forme qualifiée (qui reprend le contexte de l'article), son passage, "
    "et une INDICATION du graphe de concepts du corpus (type, sens, nombre de livres). Cette indication "
    "est facultative et peut être ambiguë ou manquante : tu t'appuies d'abord sur le texte et le contexte, "
    "et tu peux la contredire.\n"
    "Décisions possibles :\n"
    "- `deja_traite` : un article du catalogue est déjà consacré à cet objet, même si son titre ou son slug "
    "ne reprend pas mot pour mot l'expression. Donne alors son slug dans `article_existant`, recopié EXACTEMENT "
    "tel qu'il figure dans le catalogue. "
    "L'article source que tu lis ne compte JAMAIS comme article dédié : il ne fait que traiter ces notions "
    "dans des sections, c'est précisément ce qu'un nouvel article doit approfondir.\n"
    "Un terme générique qui recouvre des variantes déjà traitées par le catalogue est `deja_traite` : il n'appelle "
    "pas un article générique supplémentaire. Donne alors l'article le plus central dans `article_existant`.\n"
    "- `nouveau` : l'objet est clairement identifié grâce au contexte, le corpus peut l'expliquer à part "
    "entière, et aucun article du catalogue ne lui est consacré. Donne `titre_question`, une question en "
    "française correcte qui reprend la forme qualifiée, en commençant par « Qu'est-ce qu'un… » ou « Qu'est-ce qu'une… » "
    "selon le genre de l'expression.\n"
    "- `secondaire` : simple mention en passant, détail, ou variante d'un terme déjà au centre de l'article. "
    "Pour `secondaire`, `raison` reste vide (\"\") : pas de justification à écrire.\n"
    "- `ambigu` : tu ne peux pas dire de quoi il s'agit, même avec le contexte. Ne propose rien.\n"
    "`raison` : une phrase en français qui justifie ta décision en citant le texte ou le catalogue.\n"
    "Réponds UNIQUEMENT avec un tableau JSON d'objets {\"expression\": ..., \"decision\": ..., "
    "\"article_existant\": slug ou null, \"titre_question\": ... ou null, \"raison\": ...}, sans texte ni balise autour."
)


class LlmStepError(RuntimeError):
    """Un appel LLM n'a pas donné une réponse exploitable après MAX_ATTEMPTS essais."""


def dossier_expression(slug: str) -> Path:
    """rag_data/expressions/work/<slug>/ : rapport, usage.jsonl, totaltoken.jsonl et cache/ de l'article."""
    return EXPRESSIONS_DIR / slug


def _call_json(system: str, user_text: str, label: str, slug: str, model: str | None) -> list:
    # `system` doit rester identique entre appels d'un même article : il porte le cache de prompt.
    last_error = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        meta: dict = {}
        try:
            raw = run_claude_code(system, user_text, model=model, timeout=TIMEOUT_S, meta=meta, effort=EFFORT)
        except ClaudeCodeCallError as exc:
            last_error = f"appel claude -p en échec : {exc}"
        else:
            log_usage(dossier_expression(slug), label, slug, attempt, meta)
            try:
                data = json.loads(strip_code_fence(raw))
                if isinstance(data, list):
                    return data
                last_error = "réponse JSON qui n'est pas un tableau"
            except json.JSONDecodeError as exc:
                last_error = f"JSON invalide : {exc}"
        print(f"  [{label}] essai {attempt}/{MAX_ATTEMPTS} : {last_error}", file=sys.stderr)
    raise LlmStepError(f"{label} : {last_error}")


def _qualifier_grounded(qualifiee: str, expression: str, body_folded: str) -> bool:
    """Chaque mot ajouté pour qualifier l'expression doit figurer dans l'article."""
    expr_words = set(fold(expression).split())
    for word in fold(qualifiee).split():
        if word in expr_words or word in _QUALIFIER_STOPWORDS or len(word) < 2:
            continue
        if not word_in(word, body_folded):
            return False
    return True


def extract_candidates(article: Article) -> list[dict]:
    """Expressions candidates, vérifiées mot pour mot contre le texte de l'article."""
    data = _call_json(article.body, EXTRACTION_SYSTEM,
                      "extraction", article.slug, MODEL_EXTRACTION)
    body_folded = fold(article.body)
    seen: set[str] = set()
    candidates: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        expression = str(item.get("expression", "")).strip()
        passage = str(item.get("passage", "")).strip()
        qualifiee = str(item.get("qualifiee", "") or "").strip() or expression
        key = fold(expression)
        if not expression or key in seen or not word_in(key, body_folded):
            continue  # expression absente du texte : écartée, jamais réparée
        seen.add(key)
        qualifiee_ok = word_in(fold(qualifiee), body_folded) or _qualifier_grounded(qualifiee, expression, body_folded)
        if not qualifiee_ok:
            qualifiee = expression  # qualification non ancrée dans l'article : on garde la forme d'origine
        candidates.append({
            "expression": expression,
            "qualifiee": qualifiee,
            "passage": passage,
            "passage_verifie": bool(passage) and fold(passage) in body_folded,
        })
    return candidates


def judge_candidates(article: Article, articles: list[Article], evidence: list[dict]) -> dict[str, dict]:
    """Décision éditoriale par expression. Retourne {expression_folded: verdict}.

    `evidence` : une entrée par candidat, avec l'indication du graphe (ou « absent du graphe »).
    Une expression absente de la réponse n'est pas proposée.
    """
    # Le system est strictement identique à celui de l'extraction (l'article seul) : c'est lui qui
    # est mis en cache. Consignes, catalogue et expressions vont dans le message utilisateur.
    payload = (JUDGEMENT_SYSTEM + "\n\nCATALOGUE DES ARTICLES DÉJÀ PUBLIÉS :\n" + catalogue(articles)
               + "\n\nEXPRESSIONS :\n" + json.dumps(evidence, ensure_ascii=False, indent=2))
    data = _call_json(article.body, payload, "jugement", article.slug, MODEL_JUGEMENT)
    known_slugs = {a.slug for a in articles}
    wanted = {fold(e["expression"]): e for e in evidence}
    verdicts: dict[str, dict] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        key = fold(str(item.get("expression", "")))
        if key not in wanted:
            continue
        decision = str(item.get("decision", "")).strip()
        article_existant = item.get("article_existant") or None
        titre = str(item.get("titre_question") or "").strip() or None
        note = ""
        if decision not in DECISIONS:
            decision, note = "ambigu", f"décision inconnue du LLM : {item.get('decision')!r}"
        elif decision == "deja_traite" and article_existant not in known_slugs:
            # Le modèle recopie parfois un slug avec une faute (article ou mot manquant) : on retient le slug
            # du catalogue le plus proche, et on ne tombe en « ambigu » que si aucun ne ressemble.
            proche = difflib.get_close_matches(str(article_existant), sorted(known_slugs), n=1, cutoff=0.85)
            if proche:
                article_existant = proche[0]
            else:
                decision, note = "ambigu", f"article_existant « {article_existant} » absent du catalogue"
        elif decision == "nouveau" and not titre:
            note = "pas de titre_question proposé"
        verdicts[key] = {
            "decision": decision,
            "article_existant": article_existant if decision == "deja_traite" else None,
            "titre_question": titre if decision == "nouveau" else None,
            "raison": str(item.get("raison", "")).strip(),
            "note": note,
        }
    return verdicts


def cache_key(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def article_fingerprint(article: Article) -> str:
    return cache_key(article.body, EXTRACTION_SYSTEM)


def load_cache(path: Path) -> dict:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
