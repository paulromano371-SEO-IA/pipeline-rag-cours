"""Arbitrage « même concept ou non » par `claude -p`, commun aux deux étapes.

Le `system` (consigne de jugement) est passé par l'étape appelante : chaque étape a son prompt
dans `scripts/prompts/`. Seul le message (les deux concepts) change d'un appel à l'autre.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))  # scripts/ : commun.py

import json
from dataclasses import dataclass

from commun import ClaudeCodeCallError, appeler_claude, strip_code_fence

_EXCERPT_MAX_CHARS = 400


class ArbitrationError(RuntimeError):
    pass


@dataclass
class ArbitrationSide:
    """Ce qu'on sait d'un des deux concepts comparés."""
    name: str
    sense: str = ""
    documents: tuple[str, ...] = ()
    excerpt: str = ""
    type: str = ""


def format_side(label: str, side: ArbitrationSide) -> str:
    lines = [f'Concept {label}: "{side.name}"', f"  définition : {side.sense or '(non fournie)'}"]
    if side.type:
        lines.append(f"  type : {side.type}")
    if side.documents:
        lines.append(f"  livre(s) : {', '.join(side.documents)}")
    if side.excerpt:
        lines.append(f"  extrait : {side.excerpt[:_EXCERPT_MAX_CHARS]}")
    return "\n".join(lines)


def arbitrer(a: ArbitrationSide, b: ArbitrationSide, system: str) -> bool:
    """True si `a` et `b` désignent le même concept. Lève ArbitrationError si l'appel ou la réponse échoue."""
    prompt = format_side("A", a) + "\n\n" + format_side("B", b)
    try:
        result = appeler_claude(system, prompt)
    except ClaudeCodeCallError as exc:
        raise ArbitrationError(str(exc)) from exc

    raw = strip_code_fence(result)
    try:
        decision = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ArbitrationError(f"réponse non-JSON de claude -p : {raw!r}") from exc
    return bool(decision.get("same", False))
