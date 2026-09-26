"""Découpage sémantique du pivot Markdown en chunks embeddables.

Chaque bloc Markdown (titre, paragraphe, bloc de code, image, formule
d'affichage) est traité comme une unité atomique, jamais scindée en plein
milieu — un extrait de code ou une phrase coupée en deux nuirait à la
qualité de l'embedding et à la lisibilité en citation. Les chunks sont
remplis gloutonnement jusqu'à une taille cible en tokens, avec un
recouvrement d'un bloc entre deux chunks consécutifs pour ne pas perdre le
contexte à la frontière. Le fil d'ariane des titres traversés est conservé
en métadonnée (utilisé ensuite pour préfixer le texte réellement embeddé,
dans `vector_store.py`).

Un bloc de code, d'image ou de formule directement suivi (voir
`/rag-nottext`) d'une description en langage naturel — repérée par le
marqueur `DESCRIPTION_MARKER`, un commentaire HTML invisible au rendu — est
fusionné avec elle en une seule unité atomique : le texte stocké (`Chunk.text`,
utilisé pour la citation) reste le bloc verbatim + sa description, mais le
texte réellement embeddé (`Chunk.embed_text`) ne retient QUE la description.
Nécessaire empiriquement (voir `/rag-nottext/SKILL.md`) : un modèle
d'embedding texte ne rapproche quasiment jamais une question en français
d'une formule LaTeX ou d'un extrait de code bruts, et mélanger verbatim et
description dans le même texte embeddé dilue la similarité au lieu de la
restaurer — d'autant plus que le verbatim est long (formule) par rapport à
la description.

Le comptage de tokens (`count_tokens`) utilise le tokenizer REEL du modele
d'embedding (`BAAI/bge-m3`, voir `vector_store.DEFAULT_MODEL_NAME` — a garder
synchronise avec `_TOKENIZER_MODEL_NAME` ci-dessous) plutot qu'un proxy
generique (`tiktoken`, utilise avant, calibre pour les modeles OpenAI, sans
rapport avec le tokenizer XLM-R de bge-m3) : mesure empirique sur le corpus
reel (voir `tools/analyze_embedder_candidates.py`) montrant que le proxy
precedent masquait une troncature silencieuse quasi systematique par
l'ancien modele d'embedding (128 tokens reels). Le budget de remplissage des
chunks (`target_tokens`) est lui aussi calcule sur `embed_text` (ce qui est
reellement envoye au modele) et non sur le verbatim brut -- un chunk riche
en blocs code/formule (description courte, verbatim long) peut donc
legitimement accumuler plus de contenu citable qu'un chunk de prose pure,
sans que ca ne reflete une sous-estimation de sa taille reellement embeddee.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from transformers import AutoTokenizer

from structural import is_structural_heading

# Doit rester synchronise avec `vector_store.DEFAULT_MODEL_NAME` -- pas
# d'import direct de `vector_store` ici pour eviter un import circulaire
# (`vector_store.py` importe deja `Chunk` depuis ce module).
_TOKENIZER_MODEL_NAME = "BAAI/bge-m3"
_TOKENIZER = AutoTokenizer.from_pretrained(_TOKENIZER_MODEL_NAME)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_FORMULA_ENV_NAMES = r"equation\*?|align\*?|gather\*?|multline\*?|eqnarray\*?|flalign\*?"

# Backreference `(?P=env)` sur l'environnement d'ouverture — jamais deux
# alternances independantes pour \begin/\end — pour ne jamais accepter un
# environnement mal apparie (ex. `\begin{align}...\end{equation}`), meme
# raison que `rag-extraction/scripts/tex_source.py:_FORMULA_RE`.
_DISPLAY_FORMULA_RE = re.compile(
    r"^(?:"
    r"\$\$.*\$\$"
    r"|\\\[.*\\\]"
    r"|\\begin\{(?P<env>" + _FORMULA_ENV_NAMES + r")\}.*\\end\{(?P=env)\}"
    r")$",
    re.DOTALL,
)

DEFAULT_TARGET_TOKENS = 400
DEFAULT_OVERLAP_BLOCKS = 1

# Commentaire HTML (invisible au rendu Markdown) qu'insère /rag-nottext juste
# avant chaque description générée, sur la même ligne de paragraphe qu'elle
# (aucune ligne vide entre les deux) — c'est ce qui permet à `chunk_markdown`
# de reconnaître une description générée d'une prose ordinaire qui suivrait,
# par coïncidence, un bloc de code/image/formule sans en être la description.
DESCRIPTION_MARKER = "<!-- rag-nottext:description -->"

# Meme principe que DESCRIPTION_MARKER, pour le paragraphe optionnel qui suit
# la description d'une image (transcription LaTeX OCR d'une formule, ou texte
# detecte par OCR general) — voir /rag-nottext/SKILL.md, "Mise a jour de
# pivot.md". Sans ce marqueur, ce paragraphe serait un bloc de prose isole
# aux yeux de `split_into_blocks` (separe par une ligne vide de la
# description qui le precede) : le decoupage en chunks glouton de
# `chunk_markdown` pourrait alors le placer dans un chunk DIFFERENT de
# l'image/description dont il depend, le rendant illisible hors contexte —
# corrige empiriquement, voir /rag-nottext/SKILL.md.
OCR_MARKER = "<!-- rag-nottext:ocr -->"


@dataclass
class _Block:
    kind: str  # "heading" | "code" | "image" | "formula" | "prose"
    text: str
    heading_level: int | None = None
    embed_text: str | None = None


@dataclass
class Chunk:
    index: int
    text: str
    heading_trail: list[str]
    token_count: int  # base sur embed_text, pas sur le verbatim -- voir chunk_markdown
    has_code: bool
    embed_text: str | None = None
    has_formula: bool = False
    has_image: bool = False


def count_tokens(text: str) -> int:
    return len(_TOKENIZER.encode(text, add_special_tokens=True))


def split_into_blocks(markdown: str) -> list[_Block]:
    """Scinde le Markdown en blocs atomiques, en traitant tout ce qui se
    trouve entre deux barres ```` ``` ```` comme un seul bloc de code — même
    s'il contient des lignes vides internes — pour ne jamais le couper.

    Publique (pas de `_` initial) : réutilisée telle quelle par
    `/rag-nottext`, qui a besoin de la même segmentation en blocs (au même
    ordre, avec la même détection code/image/formule) pour repérer les
    éléments non-textuels de `pivot.md` à décrire — une seule implémentation
    de "qu'est-ce qu'un bloc atomique dans ce pivot", jamais deux logiques de
    parsing divergentes à maintenir en parallèle."""
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
            elif _DISPLAY_FORMULA_RE.match(para):
                blocks.append(_Block(kind="formula", text=para))
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


def _merge_description_blocks(blocks: list[_Block]) -> list[_Block]:
    """Fusionne un bloc code/image/formule avec la description générée par
    `/rag-nottext` qui le suit immédiatement (repérée par `DESCRIPTION_MARKER`
    en tête du bloc de prose suivant), en une seule unité atomique : le texte
    stocké reste bloc + description (citation intacte), mais `embed_text` ne
    retient que la description, jamais le verbatim — voir la docstring du
    module. Un bloc code/image/formule pas encore traité par `/rag-nottext`
    (pas de description trouvée juste après) reste inchangé : son `embed_text`
    reste `None`, et l'appelant retombe alors sur son texte brut.

    Absorbe aussi, s'il est present, le paragraphe marque `OCR_MARKER` qui
    peut suivre la description (transcription LaTeX ou texte OCR d'une
    image) : ajoute a `text` (citation), jamais a `embed_text` (meme
    raisonnement que pour le verbatim code/formule — voir la docstring du
    module), mais surtout ne le laisse JAMAIS comme bloc de prose isole,
    sous peine d'atterrir dans un chunk different de l'image/description
    dont il depend lors du decoupage glouton de `chunk_markdown` (voir
    OCR_MARKER)."""
    merged: list[_Block] = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        following = blocks[i + 1] if i + 1 < len(blocks) else None
        if (
            block.kind in ("code", "image", "formula")
            and following is not None
            and following.kind == "prose"
            and following.text.startswith(DESCRIPTION_MARKER)
        ):
            description = following.text[len(DESCRIPTION_MARKER):].strip()
            text = f"{block.text}\n\n{description}"
            consumed = 2
            ocr_following = blocks[i + 2] if i + 2 < len(blocks) else None
            if (
                ocr_following is not None
                and ocr_following.kind == "prose"
                and ocr_following.text.startswith(OCR_MARKER)
            ):
                ocr_text = ocr_following.text[len(OCR_MARKER):].strip()
                text = f"{text}\n\n{ocr_text}"
                consumed = 3
            merged.append(
                _Block(
                    kind=block.kind,
                    text=text,
                    heading_level=block.heading_level,
                    embed_text=description,
                )
            )
            i += consumed
            continue
        merged.append(block)
        i += 1
    return merged


def drop_structural_sections(blocks: list[_Block]) -> tuple[list[_Block], list[tuple[str, int]]]:
    """Retire les sections structurelles (table des matières, sommaire,
    index... — voir `structural.py`) : le titre ET tout ce qui le suit jusqu'au
    prochain titre de niveau égal ou supérieur. Renvoie (blocs conservés,
    [(titre de section retirée, nombre de blocs retirés titre compris)]).

    À faire AVANT le découpage en chunks, jamais après : le découpage glouton
    fusionne volontiers la fin d'une table des matières avec le début de la
    section suivante (vérifié : chunk d'ISLR mêlant la fin de la TOC, le
    numéro de page, le titre du chapitre 1 et son texte, étiqueté du fil
    d'ariane du chapitre) — un filtre sur le fil d'ariane d'un chunk déjà
    formé ne le verrait pas."""
    kept: list[_Block] = []
    dropped: list[list] = []
    skip_level: int | None = None
    for block in blocks:
        if block.kind == "heading":
            if skip_level is not None and block.heading_level <= skip_level:
                skip_level = None
            if skip_level is None and is_structural_heading(block.text):
                skip_level = block.heading_level
                dropped.append([block.text, 1])
                continue
        if skip_level is not None:
            dropped[-1][1] += 1
            continue
        kept.append(block)
    return kept, [(title, n) for title, n in dropped]


def chunk_markdown(
    markdown: str,
    *,
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    overlap_blocks: int = DEFAULT_OVERLAP_BLOCKS,
    exclude_structural: bool = True,
) -> list[Chunk]:
    """Découpe `markdown` (sortie de `convert.py`) en chunks embeddables.
    `target_tokens` est une cible, pas une limite stricte : un bloc atomique
    (code, image, phrase unique) qui la dépasse à lui seul reste entier
    plutôt que d'être tronqué. `exclude_structural` (défaut) n'émet aucun
    chunk pour les sections structurelles (voir `drop_structural_sections`)."""
    blocks_in = split_into_blocks(markdown)
    if exclude_structural:
        blocks_in, _ = drop_structural_sections(blocks_in)
    raw_blocks = _merge_description_blocks(blocks_in)
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
        embed_text = "\n\n".join(b.embed_text or b.text for b in current_blocks)
        chunks.append(
            Chunk(
                index=len(chunks),
                text=text,
                embed_text=embed_text,
                heading_trail=_trail(),
                token_count=current_tokens,
                has_code=any(b.kind == "code" for b in current_blocks),
                has_formula=any(b.kind == "formula" for b in current_blocks),
                has_image=any(b.kind == "image" for b in current_blocks),
            )
        )
        carried = (
            [b for b in current_blocks[-overlap_blocks:] if b.kind != "heading"] if overlap_blocks else []
        )
        current_blocks = list(carried)
        current_tokens = sum(count_tokens(b.embed_text or b.text) for b in current_blocks)

    for block in blocks:
        # Budget calcule sur ce qui sera reellement embedde (embed_text pour
        # un bloc fusionne code/formule/image, verbatim sinon) -- voir la
        # docstring du module. Un bloc de code volumineux mais a description
        # courte pese donc peu dans ce budget, meme si son verbatim (garde
        # intact dans Chunk.text pour la citation) est long.
        block_tokens = count_tokens(block.embed_text or block.text)
        if current_blocks and current_tokens + block_tokens > target_tokens:
            _flush()

        # Fil d'ariane mis a jour APRES la decision de fermer le chunk : un
        # titre qui declenche la fermeture appartient au chunk SUIVANT, le
        # chunk qui se ferme doit garder le fil de sa propre section (mis a
        # jour avant, il recevait celui de la section suivante).
        if block.kind == "heading":
            for level in list(heading_trail):
                if level >= block.heading_level:
                    del heading_trail[level]
            heading_trail[block.heading_level] = block.text

        current_blocks.append(block)
        current_tokens += block_tokens

    _flush()
    return chunks
