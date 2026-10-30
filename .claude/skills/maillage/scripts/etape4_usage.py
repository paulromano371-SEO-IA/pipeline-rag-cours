"""Journal d'usage de /maillage : une ligne par appel `claude -p`.

- Un appel rattaché à un article (extraction de concepts, proposition de lien, critique) va dans
  rag_data/maillage/work/<slug>/usage.jsonl, à côté du rapport de l'article.
- Un appel partagé entre articles (arbitrage du graphe, calibrage) va dans rag_data/maillage/usage.jsonl.
- Après chaque appel réel rattaché à un article, rag_data/maillage/work/<slug>/totaltoken.jsonl est réécrit :
  une seule ligne, avec le nombre d'appels API et le total de chaque type de tokens (même format que
  create-content). Aucun appel au modèle.

- Un appel réel écrit `"source": "api"`, avec `"statut"` (`ok` ou `erreur`), `"essai"` (1 = premier essai ;
  2 et plus = relance après réponse au format invalide ou échec d'infrastructure, dans le même bloc) et l'usage
  renvoyé par claude -p (coût, tokens, cache de prompt lu/écrit, durée).
- Un résultat servi par le cache de résultats écrit `"source": "cache"` : aucun appel, aucun token.
- `type_appel` (quel appel) et `etape` (quelle étape du pipeline) se posent par `bloc(...)` autour de chaque
  boucle d'appels. Un appel hors de tout bloc sort avec `"type_appel": "non_classe"`, pour qu'un trou dans la
  trace se voie.

`script` (etape1_build_index ou etape3_links) vient du nom du script lancé.
"""
from __future__ import annotations

import json
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / ".claude" / "skills" / "_rag_lib").is_dir())
MAILLAGE_DIR = _ROOT / "rag_data" / "maillage"
WORK_DIR = MAILLAGE_DIR / "work"
USAGE_LOG = MAILLAGE_DIR / "usage.jsonl"
COMMANDE = "maillage"
TOKEN_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")

_contexte: dict = {"type_appel": "non_classe", "essai": 0}


@contextmanager
def bloc(type_appel: str, etape: str, **champs):
    """Pose le type d'appel, l'étape et les champs (article, cible, tour, lot...) pour les appels `claude -p` du bloc.
    Les essais repartent de 0 à chaque bloc ; le bloc précédent est restauré à la sortie."""
    global _contexte
    precedent = _contexte
    _contexte = {"type_appel": type_appel, "etape": etape, **champs, "essai": 0}
    try:
        yield
    finally:
        _contexte = precedent


def _ecrit(ligne: dict, contexte: dict) -> None:
    entree = {
        "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commande": COMMANDE,
        "script": Path(sys.argv[0]).stem,
        **contexte,
        **ligne,
    }
    article = entree.get("article")
    journal = (WORK_DIR / article / "usage.jsonl") if article else USAGE_LOG
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entree, ensure_ascii=False) + "\n")
    if entree.get("source") == "api":
        if article:
            ecrit_totaux(article)
        else:
            ecrit_totaux_graphe()


def _somme(journal: Path, filtre) -> dict:
    """Additionne les lignes `api` de `journal` qui passent `filtre` : nombre d'appels et 4 types de tokens."""
    total = {"appels_api": 0, **{k: 0 for k in TOKEN_FIELDS}}
    for line in journal.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("source") != "api" or not filtre(row):
            continue
        total["appels_api"] += 1
        usage = row.get("usage") or {}
        for k in TOKEN_FIELDS:
            total[k] += usage.get(k) or 0
    return total


def ecrit_totaux(slug: str) -> dict:
    """Réécrit work/<slug>/totaltoken.jsonl depuis work/<slug>/usage.jsonl : une ligne, appels `api` seulement
    (un résultat repris du cache n'a coûté aucun appel), et seulement ceux de etape3_links : les appels
    de etape1_build_index (extraction de concepts) restent dans usage.jsonl mais ne sont pas comptés."""
    total = {"article": slug, **_somme(WORK_DIR / slug / "usage.jsonl", lambda r: r.get("script") == "etape3_links")}
    (WORK_DIR / slug / "totaltoken.jsonl").write_text(json.dumps(total, ensure_ascii=False) + "\n", encoding="utf-8")
    return total


def ecrit_totaux_graphe() -> dict:
    """Réécrit rag_data/maillage/totaltoken.jsonl depuis rag_data/maillage/usage.jsonl (appels partagés entre
    articles : arbitrages et calibrage de etape1_build_index). Une seule ligne, appels `api` seulement."""
    total = {"portee": "graphe", **_somme(USAGE_LOG, lambda r: True)}
    (MAILLAGE_DIR / "totaltoken.jsonl").write_text(json.dumps(total, ensure_ascii=False) + "\n", encoding="utf-8")
    return total


def cache_hit(type_appel: str, etape: str, **champs) -> None:
    """Une ligne quand un résultat (ou un lot de résultats) est repris du cache : aucun appel, aucun token."""
    _ecrit({"source": "cache", "type_appel": type_appel, "etape": etape, **champs}, contexte={})


def appel_claude(run, system: str, texte: str, *, model: str, effort: str, timeout: int) -> str:
    """Un appel `claude -p` via `run` (run_claude_code), journalisé : une ligne par appel, réussi ou non.
    Une exception est journalisée puis relancée telle quelle pour l'appelant."""
    _contexte["essai"] += 1
    meta: dict = {}
    debut = time.time()
    try:
        raw = run(system, texte, model=model, timeout=timeout, meta=meta, effort=effort)
    except Exception as exc:
        _ecrit({"source": "api", "statut": "erreur", "erreur": str(exc)[:500], "modele": model, "effort": effort,
                "duree_appel_s": round(time.time() - debut, 1)}, contexte=_contexte)
        raise
    _ecrit({"source": "api", "statut": "ok", "modele": model, "effort": effort,
            "duree_appel_s": round(time.time() - debut, 1), **meta}, contexte=_contexte)
    return raw
