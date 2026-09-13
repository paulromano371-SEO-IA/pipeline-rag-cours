"""Classification déterministe formule vs image générale, avant tout appel
Claude ou OCR spécialisé — heuristique reproductible sur le texte déjà
reconnu par un OCR généraliste (voir `ocr_general.extract_text`), jamais de
jugement LLM à cette étape.

Principe : une formule mathématique isolée, une fois passée dans un OCR
généraliste, produit soit très peu de texte reconnu (les glyphes maths ne
ressemblent à aucune lettre latine), soit un texte dominé par des symboles
mathématiques/lettres grecques et des mots très courts (variables isolées,
indices, exposants) — alors qu'une image générale (schéma, capture, photo)
produit soit peu de texte du tout, soit du texte français/anglais normal.
"""

from __future__ import annotations

import re

_MATH_CHARS = set(
    "∑∏∫√±×÷≤≥≠∈∉∀∃∇∂∞≈≡⊂⊆∪∩→↔⇒⇔"
    "αβγδεζηθικλμνξοπρστυφχψω"
    "ΓΔΘΛΞΠΣΦΨΩ"
)
_MATH_CHAR_RATIO_THRESHOLD = 0.04
_LOW_ALPHA_RATIO_THRESHOLD = 0.5
_SHORT_WORD_MAX_LEN = 3
_MIN_CHARS_FOR_JUDGEMENT = 3


def classify(ocr_text: str) -> str:
    """Retourne "formule" ou "generale". `ocr_text` est le texte déjà reconnu
    par un OCR généraliste sur l'image (chaîne vide si rien n'est reconnu :
    dans ce cas, l'image est traitée comme "generale" par défaut — une
    absence de texte ne signe pas une formule, c'est aussi le cas d'un
    schéma ou d'une photo sans texte)."""
    text = ocr_text.strip()
    if len(text) < _MIN_CHARS_FOR_JUDGEMENT:
        return "generale"

    math_chars = sum(1 for c in text if c in _MATH_CHARS)
    math_ratio = math_chars / len(text)
    if math_ratio > _MATH_CHAR_RATIO_THRESHOLD:
        return "formule"

    non_space = [c for c in text if not c.isspace()]
    alpha_ratio = (sum(1 for c in non_space if c.isalpha()) / len(non_space)) if non_space else 1.0

    words = re.findall(r"[^\W\d_]+", text, flags=re.UNICODE)
    avg_word_len = (sum(len(w) for w in words) / len(words)) if words else 0.0

    # Peu de lettres au total, et les "mots" reconnus sont très courts
    # (variables isolées, indices) plutôt que du texte normal : signe d'une
    # formule courte plutôt que d'un paragraphe mal reconnu.
    if alpha_ratio < _LOW_ALPHA_RATIO_THRESHOLD and 0 < avg_word_len <= _SHORT_WORD_MAX_LEN:
        return "formule"

    return "generale"
