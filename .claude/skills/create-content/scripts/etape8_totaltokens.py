"""Étape 8 de /create-content : totaux de tokens (totaltoken.jsonl).

Usage : etape8_totaltokens.py "<expression>"
Lit rag_data/content/work/<slug>/usage.jsonl (une ligne par appel `claude -p`) et écrit
rag_data/content/work/<slug>/totaltoken.jsonl : une seule ligne, avec le nombre total d'appels et le
total de chaque type de tokens (input, cache_creation, cache_read, output). Aucun appel au modèle.
Le fichier est réécrit à chaque lancement. Seules les lignes `source == "api"` comptent
(un résultat repris du cache n'a coûté aucun appel).
Codes de sortie : 0 OK, 2 échec.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
_sys.path.insert(0, str(_Path(__file__).resolve().parent / "lib"))

import json
import sys

from commun import content_dir, statut
from gather import slugify_expression

TOKEN_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
TOKEN_LABELS = ("input", "cache_creation", "cache_read", "output")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage : etape8_totaltokens.py \"<expression>\"", file=sys.stderr)
        return 2
    slug = slugify_expression(sys.argv[1].strip())
    dossier = content_dir(slug)
    usage_path = dossier / "usage.jsonl"
    if not usage_path.exists():
        statut("8", False, f"{usage_path.name} absent : lancer les étapes précédentes d'abord")
        return 2

    total = {"article": slug, "appels_api": 0, **{k: 0 for k in TOKEN_FIELDS}}
    for n, line in enumerate(usage_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            statut("8", False, f"{usage_path.name} ligne {n} : JSON invalide")
            return 2
        if row.get("source") != "api":
            continue
        total["appels_api"] += 1
        usage = row.get("usage") or {}
        for k in TOKEN_FIELDS:
            total[k] += usage.get(k) or 0

    if not total["appels_api"]:
        statut("8", False, f"aucun appel api dans {usage_path.name}")
        return 2

    out = dossier / "totaltoken.jsonl"
    out.write_text(json.dumps(total, ensure_ascii=False) + "\n", encoding="utf-8")

    fmt = lambda n: f"{n:,}".replace(",", " ")
    print(f"Totaux des {total['appels_api']} appels API :\n")
    print("| Type de tokens | Quantité |\n|---|---:|")
    for label, k in zip(TOKEN_LABELS, TOKEN_FIELDS):
        print(f"| {label} | {fmt(total[k])} |")
    print()
    statut("8", True, f"{out.name} écrit ({total['appels_api']} appel(s) api)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
