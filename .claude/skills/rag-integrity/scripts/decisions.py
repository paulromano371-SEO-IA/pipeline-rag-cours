"""Registre persistant des décisions de /rag-integrity (rag_data/audit/decisions.json).

Une entrée par item d'audit (id stable : `shared:<id>`, `alias:<id>`...), avec
UN état parmi deux :
  - "ok"      : rien à traiter (verdict non signalé, ou signalement écarté à la
                main via audit_accept.py) — l'item n'est plus jamais remonté
                tant que son contexte et la consigne du juge ne changent pas ;
  - "signale" : problème confirmé par le juge, encore à traiter — listé en tête
                de chaque rapport tant que l'item existe dans le graphe.

Un problème corrigé dans le graphe disparaît de lui-même : l'item n'est plus
produit par report.py, donc plus rien ne le remonte. Une entrée est « à jour »
(`is_current`) tant que l'empreinte du contexte (extraits, alias, sens) ET la
version de la consigne (`audit_run._prompt_version`) sont inchangées ; sinon
l'item est rejugé — même règle que la reprise entre rapports d'audit_run.py.

Les hubs (catégorie 1.3) ne sont plus jugés : ils ne sont jamais enregistrés.

Ce module ne dépend d'aucun appel `claude -p`. Il importe audit_run.py
uniquement à l'intérieur des fonctions (audit_run importe ce module).
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_rag_lib"))
import paths  # noqa: E402

DECISIONS_PATH = paths.AUDIT_DIR / "decisions.json"
NOT_AUDITED = {"1.3"}  # hubs : métrique du rapport uniquement, plus jugés par le LLM


def context_hash(item: dict) -> str:
    raw = json.dumps(item.get("context"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def state_of(category: str, verdict: str) -> str:
    """"signale" si le verdict est celui « à signaler » de la catégorie, sinon "ok"
    (y compris "indetermine" : preuve insuffisante, pas un problème établi)."""
    from audit_run import _FLAGGED
    return "signale" if verdict == _FLAGGED.get(category) else "ok"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save(registry: dict, path: Path = DECISIONS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def record(registry: dict, item: dict, verdict: dict) -> None:
    """Enregistre (ou remplace) la décision d'un item qui vient d'être jugé."""
    if item["category"] in NOT_AUDITED:
        return
    registry[item["id"]] = {
        "category": item["category"], "subject": item["subject"],
        "state": state_of(item["category"], verdict["verdict"]),
        "verdict": verdict["verdict"], "reason": verdict.get("reason", ""),
        "context_hash": context_hash(item), "prompt_version": verdict.get("prompt_version"),
        "date": _now(),
    }


def is_current(entry: dict | None, item: dict) -> bool:
    """L'entrée du registre vaut-elle encore pour cet item (même contexte, même consigne) ?"""
    if not entry:
        return False
    from audit_run import _prompt_version
    return (entry.get("context_hash") == context_hash(item)
            and entry.get("prompt_version") == _prompt_version(item["category"]))


def _bootstrap() -> dict:
    """Construit le registre à partir des rapports/verdicts déjà écrits (du plus
    ancien au plus récent : le dernier verdict d'un item l'emporte). Sans effet
    de bord sur ces fichiers."""
    from audit_run import _prompt_version
    registry: dict = {}
    reports = sorted(
        (p for p in paths.AUDIT_DIR.glob("rapport_graphe_*.json") if not p.name.endswith(".verdicts.json")),
        key=lambda p: p.stat().st_mtime,
    )
    for report_path in reports:
        verdicts_path = report_path.with_suffix("").with_suffix(".verdicts.json")
        if not verdicts_path.exists():
            continue
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            verdicts = json.loads(verdicts_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for item in report.get("audit_items", []):
            verdict = verdicts.get(item["id"])
            if not verdict or item.get("category") not in {"2.1", "2.2", "2.3", "2.4", "2.5"}:
                continue
            # Un verdict rendu sous une ancienne consigne (avant la 2e passe, `indetermine`...)
            # n'est pas repris : il serait rejugé de toute façon, et ses signalements
            # périmés polluent la liste des signalements ouverts.
            if verdict.get("prompt_version") == _prompt_version(item["category"]):
                record(registry, item, verdict)
            else:
                registry.pop(item["id"], None)  # un verdict plus ancien du même item ne doit pas survivre
    return registry


def load(path: Path = DECISIONS_PATH) -> dict:
    """Charge le registre ; le crée depuis les anciens verdicts s'il n'existe pas encore."""
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            print(f"AVERTISSEMENT: {path} illisible, non écrasé — corriger ou supprimer ce fichier", file=sys.stderr)
            raise
    registry = _bootstrap()
    if registry:
        save(registry, path)
        n_open = sum(1 for e in registry.values() if e["state"] == "signale")
        print(f"(registre {path.name} créé depuis les anciens verdicts : {len(registry)} item(s), {n_open} signalé(s))",
              file=sys.stderr)
    return registry


def split_items(items: list[dict], registry: dict) -> tuple[list[dict], list[dict], int]:
    """(à juger, signalements ouverts, nombre d'items déjà validés).

    - à jour et "ok"      -> écarté (compté) ;
    - à jour et "signale" -> ni rejugé ni ré-émis dans audit_items : renvoyé dans
                             les signalements ouverts, avec sa raison ;
    - absent / contexte ou consigne changés -> à juger."""
    to_judge, open_flags, n_ok = [], [], 0
    for item in items:
        entry = registry.get(item["id"])
        if not is_current(entry, item):
            to_judge.append(item)
        elif entry["state"] == "ok":
            n_ok += 1
        else:
            open_flags.append({"id": item["id"], "category": item["category"], "subject": item["subject"],
                               "verdict": entry["verdict"], "reason": entry.get("reason", ""), "date": entry.get("date")})
    return to_judge, open_flags, n_ok
