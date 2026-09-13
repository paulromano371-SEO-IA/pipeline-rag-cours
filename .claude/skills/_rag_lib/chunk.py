"""Découpage sémantique du pivot Markdown en chunks embeddables.

Chaque bloc Markdown (titre, paragraphe, bloc de code, image) est traité comme
une unité atomique, jamais scindée en plein milieu — un extrait de code ou
une phrase coupée en deux nuirait à la qualité de l'embedding et à la
lisibilité en citation. Les chunks sont remplis glouton­nement jusqu'à une
taille cible en tokens, avec un recouvrement d'un bloc entre deux chunks
consécutifs pour ne pas perdre le contexte à la frontière. Le fil d'ariane
des titres traversés est conservé en métadonnée (utilisé ensuite pour
préfixer le texte réellement embeddé, dans `vector_store.py`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import tiktoken

_ENCODING = tiktoken.get_encoding("cl100k_base")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")

DEFAULT_TARGET_TOKENS = 400
DEFAULT_OVERLAP_BLOCKS = 1


@dataclass
class _Block:
    kind: str  # "heading" | "code" | "image" | "prose"
    text: str
    heading_level: int | None = None


@dataclass
class Chunk:
    index: int
    text: str
    heading_trail: list[str]
    token_count: int
    has_code: bool


def count_tokens(text: str) -> int:
    return len(_ENCODING.encode(text))


def _split_into_blocks(markdown: str) -> list[_Block]:
    """Scinde le Markdown en blocs atomiques, en traitant tout ce qui se
    trouve entre deux barres ```` ``` ```` comme un seul bloc de code — même
    s'il contient des lignes vides internes — pour ne jamais le couper."""
    lines = markdown.split("\n")
    blocks: list[_Block] = []
    buffer: list[str] = []

    def _flush_prose_buffer() -> None:
        text = "\n".join(buffer).strip()
        buffer.clear()
        if not text:
            return
        for para in re.split(r"\n\s*\n", text):
            para = para.strip()
            if not para:
                continue
            heading_match = _HEADING_RE.match(para)
            if heading_match:
                blocks.append(
                    _Block(
                        kind="heading",
                        text=heading_match.group(2).strip(),
                        heading_level=len(heading_match.group(1)),
                    )
                )
            elif para.startswith("!["):
                blocks.append(_Block(kind="image", text=para))
            else:
                blocks.append(_Block(kind="prose", text=para))

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            _flush_prose_buffer()
            code_lines = [line]
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            if i < len(lines):
                code_lines.append(lines[i])  # barre fermante
                i += 1
            blocks.append(_Block(kind="code", text="\n".join(code_lines)))
            continue
        buffer.append(line)
        i += 1

    _flush_prose_buffer()
    return blocks


def _split_long_prose(block: _Block, max_tokens: int) -> list[_Block]:
    """Scinde un paragraphe de prose trop long sur des frontières de phrase.
    Ne s'applique jamais au code ni aux images : ce sont des unités atomiques
    qui peuvent dépasser la taille cible plutôt que d'être découpées."""
    if count_tokens(block.text) <= max_tokens:
        return [block]

    sentences = re.split(r"(?<=[.!?])\s+", block.text)
    parts: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if current and count_tokens(candidate) > max_tokens:
            parts.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        parts.append(current)
    return [_Block(kind="prose", text=p) for p in parts] if parts else [block]


def chunk_markdown(
    markdown: str,
    *,
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    overlap_blocks: int = DEFAULT_OVERLAP_BLOCKS,
) -> list[Chunk]:
    """Découpe `markdown` (sortie de `convert.py`) en chunks embeddables.
    `target_tokens` est une cible, pas une limite stricte : un bloc atomique
    (code, image, phrase unique) qui la dépasse à lui seul reste entier
    plutôt que d'être tronqué."""
    raw_blocks = _split_into_blocks(markdown)
    blocks: list[_Block] = []
    for b in raw_blocks:
        if b.kind == "prose":
            blocks.extend(_split_long_prose(b, target_tokens))
        else:
            blocks.append(b)

    chunks: list[Chunk] = []
    heading_trail: dict[int, str] = {}
    current_blocks: list[_Block] = []
    current_tokens = 0

    def _trail() -> list[str]:
        return [heading_trail[level] for level in sorted(heading_trail)]

    def _flush() -> None:
        nonlocal current_blocks, current_tokens
        if not current_blocks:
            return
        text = "\n\n".join(b.text for b in current_blocks)
        chunks.append(
            Chunk(
                index=len(chunks),
                text=text,
                heading_trail=_trail(),
                token_count=current_tokens,
                has_code=any(b.kind == "code" for b in current_blocks),
            )
        )
        carried = (
            [b for b in current_blocks[-overlap_blocks:] if b.kind != "heading"] if overlap_blocks else []
        )
        current_blocks = list(carried)
        current_tokens = sum(count_tokens(b.text) for b in current_blocks)

    for block in blocks:
        if block.kind == "heading":
            for level in list(heading_trail):
                if level >= block.heading_level:
                    del heading_trail[level]
            heading_trail[block.heading_level] = block.text

        block_tokens = count_tokens(block.text)
        if current_blocks and current_tokens + block_tokens > target_tokens:
            _flush()

        current_blocks.append(block)
        current_tokens += block_tokens

    _flush()
    return chunks
