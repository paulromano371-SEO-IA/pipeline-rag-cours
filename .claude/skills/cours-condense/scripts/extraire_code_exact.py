"""
Extraction de blocs de code en preservant la position x/y de chaque caractere
(regle 4 du prompt "Redaction du cours" : jamais de reconstruction par
proximite/flux continu, jamais de fusion de lignes, jamais d'espace invente).

Principe :
- On lit le PDF en mode "rawdict" (PyMuPDF), qui donne un bbox par CARACTERE.
- Les "line" fournies par PyMuPDF correspondent deja a des lignes physiques
  (meme ordonnee de base) : on ne les fusionne jamais.
- L'indentation d'une ligne = ecart entre son x0 et le x0 minimal du bloc,
  convertie en nombre de caracteres via la largeur mesuree d'un caractere
  monospace de la police du bloc (pas une valeur fixe en points).
- Un espace n'est inséré entre deux caracteres adjacents que si l'ecart
  horizontal entre eux depasse nettement (facteur ~1.6, pas un seuil absolu)
  la largeur mesuree d'un caractere a cet endroit precis.
- Les lignes vides du code source (aucun caractere dessine) sont retrouvees
  en comparant l'ecart vertical entre deux lignes consecutives a l'ecart
  vertical "normal" (interligne median du bloc) : un ecart ~2x -> 1 ligne
  vide inseree, ~3x -> 2 lignes vides, etc.
- Passe finale : suppression des espaces qui entoureraient une ponctuation
  ( . , ( ) [ ] : ) normalement collee en Python/Cypher.

Usage CLI (debogage / verification manuelle) :
    python extraire_code_exact.py <livre.pdf> <page_debut> <page_fin> [x0 y0 x1 y1]

Usage en import (dans le script que Claude ecrit pour l'etape de redaction) :
    from extraire_code_exact import extraire_code_pages
    code = extraire_code_pages("livre.pdf", 42, 44, bbox=(70, 100, 520, 700))
"""
import sys
import re
import pymupdf as fitz

PUNCT_NO_SPACE_BEFORE = set(",.):]")
PUNCT_NO_SPACE_AFTER = set("([")
GAP_FACTOR = 1.6


def _chars_from_rawdict(page, bbox=None):
    d = page.get_text("rawdict")
    lines = []  # each: list of chars {x0,y0,x1,y1,c}, plus y_top for sorting
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            chars = []
            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    x0, y0, x1, y1 = ch["bbox"]
                    if bbox:
                        bx0, by0, bx1, by1 = bbox
                        cx = (x0 + x1) / 2
                        cy = (y0 + y1) / 2
                        if not (bx0 <= cx <= bx1 and by0 <= cy <= by1):
                            continue
                    chars.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1, "c": ch["c"]})
            if chars:
                chars.sort(key=lambda c: c["x0"])
                y_top = min(c["y0"] for c in chars)
                lines.append({"chars": chars, "y_top": y_top})
    lines.sort(key=lambda l: l["y_top"])
    return lines


def _reconstruct_line(chars, block_min_x0, char_width):
    parts = []
    prev = None
    for ch in chars:
        if prev is not None:
            gap = ch["x0"] - prev["x1"]
            local_width = max(prev["x1"] - prev["x0"], 0.1)
            if gap > GAP_FACTOR * local_width:
                parts.append(" ")
        parts.append(ch["c"])
        prev = ch
    text = "".join(parts)

    first_x0 = chars[0]["x0"]
    indent_chars = round((first_x0 - block_min_x0) / char_width) if char_width else 0
    indent_chars = max(indent_chars, 0)

    return (" " * indent_chars) + text


def _strip_punct_spacing(line):
    # retire un espace juste avant une ponctuation fermante/simple
    for p in PUNCT_NO_SPACE_BEFORE:
        line = re.sub(r"\s+" + re.escape(p), p, line)
    # retire un espace juste apres une parenthese/crochet ouvrant
    for p in PUNCT_NO_SPACE_AFTER:
        line = re.sub(re.escape(p) + r"\s+", p, line)
    return line


def extraire_code_page(page, bbox=None):
    lines = _chars_from_rawdict(page, bbox=bbox)
    if not lines:
        return ""

    all_chars = [c for l in lines for c in l["chars"]]
    block_min_x0 = min(c["x0"] for c in all_chars)
    widths = [c["x1"] - c["x0"] for c in all_chars if (c["x1"] - c["x0"]) > 0]
    char_width = sorted(widths)[len(widths) // 2] if widths else 6.0

    # interligne median pour detecter les lignes vides
    tops = [l["y_top"] for l in lines]
    gaps = [tops[i + 1] - tops[i] for i in range(len(tops) - 1)]
    gaps_pos = [g for g in gaps if g > 0]
    line_height = sorted(gaps_pos)[len(gaps_pos) // 2] if gaps_pos else None

    out_lines = []
    for i, l in enumerate(lines):
        if i > 0 and line_height and line_height > 0:
            gap = l["y_top"] - lines[i - 1]["y_top"]
            nb_lignes_vides = round(gap / line_height) - 1
            for _ in range(max(nb_lignes_vides, 0)):
                out_lines.append("")
        rec = _reconstruct_line(l["chars"], block_min_x0, char_width)
        out_lines.append(_strip_punct_spacing(rec))

    return "\n".join(out_lines)


def extraire_code_pages(pdf_path, page_debut, page_fin, bbox=None):
    """page_debut/page_fin en numerotation 1-based, incluses."""
    doc = fitz.open(pdf_path)
    blocs = []
    for p in range(page_debut - 1, page_fin):
        blocs.append(extraire_code_page(doc[p], bbox=bbox))
    return "\n".join(blocs)


def main():
    # Sur Windows, stdout utilise par defaut le codepage de la console
    # (souvent cp1252), qui ne sait pas encoder les glyphes presents dans le
    # PDF (fleches de continuation de ligne, puces, etc.) : on force l'UTF-8.
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) not in (4, 8):
        print("Usage: python extraire_code_exact.py <livre.pdf> <page_debut> <page_fin> [x0 y0 x1 y1]")
        sys.exit(1)
    pdf_path = sys.argv[1]
    page_debut = int(sys.argv[2])
    page_fin = int(sys.argv[3])
    bbox = None
    if len(sys.argv) == 8:
        bbox = tuple(float(x) for x in sys.argv[4:8])
    print(extraire_code_pages(pdf_path, page_debut, page_fin, bbox=bbox))


if __name__ == "__main__":
    main()
