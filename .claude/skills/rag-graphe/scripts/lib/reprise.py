"""Reprise de l'étape 1 après interruption : chunks déjà liés et compteurs cumulés.

Contrairement à `concepts.json`, le graphe Kuzu est déjà persisté au fil de l'eau (chaque
`create_concept`/`link_mention` est un commit immédiat) : `graphe_progress.json` ne sert donc
qu'à savoir QUELS chunks ne plus retraiter, pas à stocker le résultat lui-même.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))  # scripts/ : commun.py

import json
from pathlib import Path

from commun import progress_path


def vide() -> dict:
    return {"chunks_done": [], "per_chunk": {}, "base_mentions": 0, "base_skipped": 0}


def charger(work_dir: Path) -> dict:
    """Etat de reprise : chunks deja entierement traites (toutes leurs mentions resolues et liees,
    succes ou echec journalise) + compteurs cumules, pour que le rapport final reste exact meme
    apres une ou plusieurs reprises."""
    path = progress_path(work_dir)
    if not path.exists():
        return vide()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return vide()
    data.setdefault("chunks_done", [])
    if "per_chunk" not in data:
        # Ancien format (avant le comptage par chunk) : ses totaux cumules ne
        # sont pas decomposables par chunk, on les garde comme base fixe.
        data["base_mentions"] = data.get("n_mentions", 0)
        data["base_skipped"] = data.get("n_skipped", 0)
        data["per_chunk"] = {}
    data.setdefault("base_mentions", 0)
    data.setdefault("base_skipped", 0)
    return data


def enregistrer(work_dir: Path, progress: dict) -> None:
    progress_path(work_dir).write_text(json.dumps(progress, ensure_ascii=False), encoding="utf-8")


def totaux(progress: dict) -> tuple[int, int]:
    """(mentions liees, mentions ignorees) cumulees. Comptees PAR CHUNK et
    remplacees a chaque nouvelle tentative : un chunk retente ne compte
    jamais deux fois."""
    per_chunk = progress["per_chunk"].values()
    return (
        progress["base_mentions"] + sum(c["linked"] for c in per_chunk),
        progress["base_skipped"] + sum(c["skipped"] for c in per_chunk),
    )
