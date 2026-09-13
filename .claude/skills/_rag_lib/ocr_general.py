"""OCR généraliste (tesseract via pytesseract) pour le texte visible dans une
image générale (schéma, capture, légende intégrée...). Ne transcrit jamais
une formule en LaTeX — voir `ocr_formula.py` pour ça. Réutilisé aussi comme
signal d'entrée pour `image_classifier.classify` (même texte OCR brut sert
aux deux usages, pas de double passe tesseract par image).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytesseract
from PIL import Image

# Emplacement par défaut de l'installeur officiel sur Windows (UB-Mannheim) :
# le binaire n'est pas toujours sur le PATH juste après installation dans la
# même session shell, donc on retombe dessus explicitement si `which` échoue.
_WINDOWS_DEFAULT_TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Le paquet tessdata installé par winget ne fournit que l'anglais — pas de
# droits admin disponibles pour écrire fra.traineddata dans
# "Program Files\Tesseract-OCR\tessdata\", donc les modèles de langue
# (fra/eng/osd) sont conservés ici, à côté du code, et pointés via
# --tessdata-dir plutôt que dans l'installation système.
_LOCAL_TESSDATA_DIR = Path(__file__).resolve().parent / "tessdata"

_configured = False


def _configure_tesseract_cmd() -> None:
    global _configured
    if _configured:
        return
    if not shutil.which("tesseract") and Path(_WINDOWS_DEFAULT_TESSERACT).exists():
        pytesseract.pytesseract.tesseract_cmd = _WINDOWS_DEFAULT_TESSERACT
    _configured = True


def extract_text(image_path: Path, *, lang: str = "fra+eng") -> str:
    """Retourne le texte reconnu par tesseract dans l'image, chaîne vide si
    rien de significatif n'est détecté."""
    _configure_tesseract_cmd()
    # Pas de guillemets autour du chemin : pytesseract passe chaque argument
    # séparément au sous-processus (pas de shell), des guillemets manuels
    # seraient inclus tels quels dans le chemin et feraient échouer tesseract.
    config = f"--tessdata-dir {_LOCAL_TESSDATA_DIR}" if _LOCAL_TESSDATA_DIR.exists() else ""
    try:
        return pytesseract.image_to_string(Image.open(image_path), lang=lang, config=config).strip()
    except pytesseract.TesseractNotFoundError as exc:
        raise RuntimeError(
            "binaire tesseract introuvable — installe tesseract-ocr "
            "(ex. winget install --id UB-Mannheim.TesseractOCR)"
        ) from exc
