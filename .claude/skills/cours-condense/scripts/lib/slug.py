"""
Slugification du nom d'un pdf, partagee par les scripts de cours-condense.

Regle (documentee dans SKILL.md) : minuscules, sans accents, espaces et
ponctuation -> '_'. Un seul point de verite pour cette transformation.
"""
import re
import unicodedata


def slugifier(nom_livre):
    """Meme regle que documentee dans SKILL.md : minuscules, sans accents,
    espaces/ponctuation -> '_'. Un seul point de verite pour cette
    transformation, reutilise identiquement a chaque lancement."""
    sans_accents = unicodedata.normalize("NFKD", nom_livre)
    sans_accents = "".join(c for c in sans_accents if not unicodedata.combining(c))
    minuscule = sans_accents.lower()
    slug = re.sub(r"[^a-z0-9]+", "_", minuscule)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug
