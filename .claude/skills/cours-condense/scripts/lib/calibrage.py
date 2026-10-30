"""Borne des concepts visuels d'un chapitre, tirée du pdf source (pas d'une cible de longueur).

Partagée par l'étape 1.3 (consigne donnée au planificateur) et l'étape 1.5 (contrôle bilatéral) : une
seule définition de la borne, pour que la consigne et le contrôle ne divergent jamais.
"""
import math

# Un chapitre n'a pas plus de concepts visuels que cette part des mentions de figures de ses pages source.
# Les mentions comptent aussi les renvois répétés à une même figure : la borne reste donc inférieure au
# nombre de mentions. Réglable ici, à un seul endroit.
RATIO_MAX_VISUELS = 0.5


def borne_visuels(mentions: int | None) -> int:
    """Nombre maximal de concepts visuels pour un chapitre dont les pages comptent `mentions` mentions de figures."""
    if not mentions:
        return 1
    return max(1, math.ceil(RATIO_MAX_VISUELS * mentions))
