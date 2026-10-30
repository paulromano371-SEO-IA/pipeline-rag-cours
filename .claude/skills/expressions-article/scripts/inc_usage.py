"""Journal de tokens par article de /expressions-article.

Chaque article a son dossier `rag_data/expressions/work/<slug>/` (même organisation que
`rag_data/content/work/<slug>/` de /create-content), qui contient :
  - usage.jsonl     : une ligne par appel `claude -p` (source "api") ou par résultat repris
                      du cache (source "cache") ;
  - totaltoken.jsonl: une seule ligne, réécrite à chaque lancement : nombre d'appels API et
                      total des 4 types de tokens, calculé à partir de usage.jsonl.
Le calcul des totaux reprend le format de /create-content (étape 8) : seules les
lignes `source == "api"` comptent.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

TOKEN_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
TOKEN_LABELS = ("input", "cache_creation", "cache_read", "output")


def _append(dossier: Path, line: dict) -> None:
    dossier.mkdir(parents=True, exist_ok=True)
    line = {"date": datetime.now(timezone.utc).isoformat(timespec="seconds"), **line}
    with (dossier / "usage.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def log_usage(dossier: Path, label: str, slug: str, attempt: int, meta: dict) -> None:
    """Une ligne par appel API réussi : usage réel (tokens, cache de prompt lu/écrit, coût) tel que renvoyé par claude -p."""
    _append(dossier, {"source": "api", "etape": label, "article": slug, "essai": attempt, **meta})


def log_cache_hit(dossier: Path, label: str, slug: str) -> None:
    """Une ligne quand une étape est servie par le cache : aucun appel, aucun token."""
    _append(dossier, {"source": "cache", "etape": label, "article": slug})


def ecrire_totaux(dossier: Path, slug: str) -> dict:
    """Écrit totaltoken.jsonl (réécrit à chaque lancement) et renvoie les totaux.

    Sans usage.jsonl, ou sans aucun appel API, les totaux sont à zéro : le fichier est tout de même écrit.
    """
    dossier.mkdir(parents=True, exist_ok=True)
    total = {"article": slug, "appels_api": 0, **{k: 0 for k in TOKEN_FIELDS}}
    usage_path = dossier / "usage.jsonl"
    if usage_path.exists():
        for line in usage_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("source") != "api":
                continue
            total["appels_api"] += 1
            usage = row.get("usage") or {}
            for k in TOKEN_FIELDS:
                total[k] += usage.get(k) or 0
    (dossier / "totaltoken.jsonl").write_text(json.dumps(total, ensure_ascii=False) + "\n", encoding="utf-8")
    return total


def rendre_totaux(total: dict) -> str:
    """Tableau Markdown (2 colonnes), à recopier tel quel dans la réponse."""
    fmt = lambda n: f"{n:,}".replace(",", " ")
    lines = [f"**{total['article']}** : {total['appels_api']} appel(s) API", "",
             "| Type de tokens | Quantité |", "|---|---:|"]
    lines += [f"| {label} | {fmt(total[k])} |" for label, k in zip(TOKEN_LABELS, TOKEN_FIELDS)]
    return "\n".join(lines)
