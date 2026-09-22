"""OCR spécialisé formule mathématique -> LaTeX, via pix2tex (modèle dédié,
pas un LLM). Chargé paresseusement : le modèle (poids ~téléchargés au premier
usage) n'est instancié qu'au premier appel réel, pour ne pas ralentir
l'import de ce module quand aucune image n'est une formule.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL.Image import Image

_model = None


def _get_model():
    global _model
    if _model is None:
        from pix2tex.cli import LatexOCR

        _model = LatexOCR()
    return _model


def extract_latex_from_image(image: "Image") -> str:
    """Retourne la transcription LaTeX de la formule visible dans `image`
    (déjà en mémoire — évite d'écrire un fichier temporaire par région de
    formule détectée dans un PDF). Ne garantit pas un LaTeX compilable à
    l'identique — pix2tex peut se tromper sur une formule complexe ou de
    mauvaise qualité d'image ; la transcription reste indicative pour la
    recherche, pas une vérité absolue."""
    model = _get_model()
    return model(image).strip()


def extract_latex(image_path: Path) -> str:
    """Même transcription que `extract_latex_from_image`, à partir d'un
    fichier image sur disque plutôt que déjà en mémoire."""
    from PIL import Image

    return extract_latex_from_image(Image.open(image_path))
