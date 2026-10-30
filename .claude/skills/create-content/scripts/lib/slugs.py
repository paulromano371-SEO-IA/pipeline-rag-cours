"""Slug d'une expression, partagé par /create-content (gather, choix_expression).

Module léger, sans dépendance au modèle ni à la base : il peut être importé sans charger torch.
"""
from __future__ import annotations

import re
import unicodedata


def slugify_expression(text: str) -> str:
    """Slug ASCII lisible ("regression-lineaire"), accents normalises."""
    s = unicodedata.normalize("NFKD", text.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    for filler in ("comment-fonctionne-une-", "comment-fonctionne-un-", "comment-fonctionne-", "qu-est-ce-que-"):
        if s.startswith(filler) and len(s) > len(filler) + 3:
            s = s[len(filler):]
            break
    # Slug complet, iso avec l'expression. Garde-fou purement technique (chemins Windows) :
    # au-dela de 200 car., coupe a une frontiere de mot.
    if len(s) > 200:
        s = s[:200].rsplit("-", 1)[0]
    return s.strip("-") or "contenu"
