"""Suivi de progression simplifié pour le pipeline RAG (par commande, pas par
agent autonome) : un fichier JSON par document (`rag/status.json`), sans
verrou inter-processus (usage séquentiel, une commande à la fois).

Étapes suivies : extraction, images, chunking, indexation_vectorielle,
extraction_concepts, graphe.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

STAGES = ["extraction", "images", "chunking", "indexation_vectorielle", "extraction_concepts", "graphe"]

STATUS_FILENAME = "status.json"


def status_path(work_dir: Path) -> Path:
    return Path(work_dir) / STATUS_FILENAME


def load_status(work_dir: Path) -> dict:
    path = status_path(work_dir)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_status(work_dir: Path, data: dict) -> None:
    path = status_path(work_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def is_done(work_dir: Path, stage: str) -> bool:
    data = load_status(work_dir)
    entry = data.get(stage)
    return bool(entry and entry.get("status") == "done")


def mark_stage(work_dir: Path, stage: str, status: str, *, detail: str = "", **metadata) -> None:
    if stage not in STAGES:
        raise ValueError(f"étape inconnue {stage!r}, attendu l'une de {STAGES}")
    if status not in ("done", "failed"):
        raise ValueError(f"statut inconnu {status!r}, attendu 'done' ou 'failed'")

    data = load_status(work_dir)
    data[stage] = {
        "status": status,
        "detail": detail,
        "metadata": metadata,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    save_status(work_dir, data)
