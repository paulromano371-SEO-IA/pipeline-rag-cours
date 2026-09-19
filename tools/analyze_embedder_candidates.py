"""Analyse hors-pipeline, lecture seule : pour chaque document deja passe par
/rag-extraction (+ /rag-nottext si disponible), mesure la longueur reelle du
texte qui serait effectivement embedde (`embed_text`), par type de contenu
(prose / code / formule / image) et au niveau des chunks tels que
/rag-chunking les assemblerait aujourd'hui avec ses parametres par defaut,
pour plusieurs modeles d'embedding candidats.

Objectif : choisir un couple tokenizer/modele d'embedding sur la base de
donnees mesurees sur le corpus reel, plutot que sur des specs generiques.
Ne modifie aucun fichier du pipeline (pivot.md, chunks.json, status.json) --
importe `chunk.py` en lecture seule pour reutiliser exactement la meme
segmentation en blocs que /rag-chunking.

La longueur maximale reellement appliquee par un modele sentence-transformers
n'est PAS forcement `tokenizer.model_max_length` (ce champ refletc la limite
du tokenizer sous-jacent, ex. 512 pour un XLM-R, meme quand le wrapper
sentence-transformers tronque plus tot) : elle est verifiee ici en lisant
`sentence_bert_config.json` (`max_seq_length`) sur le Hub, avec repli sur la
valeur documentee dans CANDIDATES si ce fichier est absent (cas des modeles
qui ne sont pas des sentence-transformers "classiques", ex. bge-m3).

Usage:
    python analyze_embedder_candidates.py [--target-tokens N] [--overlap-blocks N]

Premiere execution : telecharge le tokenizer (et sentence_bert_config.json
quand present) de chaque modele candidat depuis Hugging Face (quelques Mo
chacun), mis en cache localement (~/.cache/huggingface) pour les executions
suivantes.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

_RAG_LIB = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "_rag_lib"
sys.path.insert(0, str(_RAG_LIB))

from chunk import (  # noqa: E402
    DEFAULT_OVERLAP_BLOCKS,
    DEFAULT_TARGET_TOKENS,
    _merge_description_blocks,
    chunk_markdown,
    split_into_blocks,
)
import paths  # noqa: E402

from huggingface_hub import hf_hub_download  # noqa: E402
from huggingface_hub.utils import HfHubHTTPError  # noqa: E402
from transformers import AutoTokenizer  # noqa: E402

# `max_tokens` = repli utilise seulement si sentence_bert_config.json est
# absent du repo HF du modele (voir docstring du module).
CANDIDATES = [
    {
        "name": "paraphrase-multilingual-MiniLM-L12-v2 (actuel)",
        "hf_id": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "max_tokens": 128,
    },
    {
        "name": "paraphrase-multilingual-mpnet-base-v2",
        "hf_id": "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
        "max_tokens": 128,
    },
    {
        "name": "multilingual-e5-base",
        "hf_id": "intfloat/multilingual-e5-base",
        "max_tokens": 512,
    },
    {
        "name": "multilingual-e5-large",
        "hf_id": "intfloat/multilingual-e5-large",
        "max_tokens": 512,
    },
    {
        "name": "bge-m3",
        "hf_id": "BAAI/bge-m3",
        "max_tokens": 8192,
    },
    {
        "name": "gte-multilingual-base",
        "hf_id": "Alibaba-NLP/gte-multilingual-base",
        "max_tokens": 8192,
    },
]


@dataclass
class BlockStat:
    kind: str
    embed_text: str


def collect_block_stats(pivot_md: str) -> list[BlockStat]:
    """Longueur par bloc atomique (fusionne bloc+description), independamment
    de tout regroupement en chunks -- donne la distribution "brute" par type
    de contenu."""
    merged = _merge_description_blocks(split_into_blocks(pivot_md))
    stats = []
    for b in merged:
        if b.kind == "heading":
            continue
        text = b.embed_text or b.text
        if not text.strip():
            continue
        stats.append(BlockStat(kind=b.kind, embed_text=text))
    return stats


def collect_chunk_texts(pivot_md: str, target_tokens: int, overlap_blocks: int) -> list[str]:
    """Reproduit exactement `embedding_text()` de vector_store.py (breadcrumb
    de titres + embed_text) sur les chunks tels que /rag-chunking les
    assemblerait aujourd'hui avec ces parametres -- donne la distribution
    "reelle", apres le regroupement glouton qui peut cumuler plusieurs
    descriptions courtes dans un seul texte embedde."""
    chunks = chunk_markdown(pivot_md, target_tokens=target_tokens, overlap_blocks=overlap_blocks)
    texts = []
    for c in chunks:
        body = c.embed_text or c.text
        if c.heading_trail:
            breadcrumb = " > ".join(c.heading_trail)
            body = f"{breadcrumb}\n\n{body}"
        texts.append(body)
    return texts


def resolve_true_max_seq_length(hf_id: str, fallback: int) -> tuple[int, str]:
    try:
        cfg_path = hf_hub_download(hf_id, "sentence_bert_config.json")
    except (HfHubHTTPError, OSError):
        return fallback, "repli (pas de sentence_bert_config.json sur le Hub pour ce modele)"
    import json

    cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
    value = cfg.get("max_seq_length")
    if value is None:
        return fallback, "repli (max_seq_length absent de sentence_bert_config.json)"
    return int(value), "verifie sur le Hub (sentence_bert_config.json)"


def print_length_row(label: str, lengths: list[int], limit: int) -> None:
    if not lengths:
        print(f"    {label:10s}: n=0")
        return
    over = sum(1 for L in lengths if L > limit)
    avg = sum(lengths) / len(lengths)
    mx = max(lengths)
    pct = 100 * over / len(lengths)
    print(
        f"    {label:10s}: n={len(lengths):4d}  moy={avg:6.1f}  max={mx:5d}  "
        f"tronques={over:4d} ({pct:5.1f}%)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-tokens", type=int, default=DEFAULT_TARGET_TOKENS)
    parser.add_argument("--overlap-blocks", type=int, default=DEFAULT_OVERLAP_BLOCKS)
    args = parser.parse_args()

    work_dirs = sorted(d for d in paths.WORK_DIR.iterdir() if d.is_dir() and (d / "pivot.md").exists())
    if not work_dirs:
        print(f"Aucun pivot.md trouve sous {paths.WORK_DIR}", file=sys.stderr)
        return 1

    print(f"Documents analyses : {len(work_dirs)}")
    for d in work_dirs:
        print(f"  - {d.name}")

    all_block_stats: list[BlockStat] = []
    all_chunk_texts: list[str] = []
    for d in work_dirs:
        pivot_md = (d / "pivot.md").read_text(encoding="utf-8")
        all_block_stats.extend(collect_block_stats(pivot_md))
        all_chunk_texts.extend(collect_chunk_texts(pivot_md, args.target_tokens, args.overlap_blocks))

    by_kind: dict[str, list[BlockStat]] = defaultdict(list)
    for s in all_block_stats:
        by_kind[s.kind].append(s)

    print(f"\nBlocs (fusionnes bloc+description) : {len(all_block_stats)} total")
    for kind, items in sorted(by_kind.items()):
        print(f"  - {kind}: {len(items)}")
    print(
        f"Chunks simules (target_tokens={args.target_tokens}, "
        f"overlap_blocks={args.overlap_blocks}) : {len(all_chunk_texts)}"
    )

    print("\n" + "=" * 100)
    for cand in CANDIDATES:
        print(f"\n### {cand['name']}")
        print(f"    repo HF : {cand['hf_id']}")
        try:
            tok = AutoTokenizer.from_pretrained(cand["hf_id"])
        except Exception as exc:  # reseau, repo prive, etc. -- on continue les autres candidats
            print(f"    ECHEC chargement tokenizer : {exc}")
            continue

        limit, source = resolve_true_max_seq_length(cand["hf_id"], cand["max_tokens"])
        print(f"    limite reelle utilisee : {limit} tokens ({source})")

        print("    -- Par type de bloc (embed_text, avant regroupement en chunks) --")
        for kind, items in sorted(by_kind.items()):
            lengths = [len(tok.encode(s.embed_text, add_special_tokens=True)) for s in items]
            print_length_row(kind, lengths, limit)

        print("    -- Chunks assembles (apres regroupement glouton actuel) --")
        chunk_lengths = [len(tok.encode(t, add_special_tokens=True)) for t in all_chunk_texts]
        print_length_row("chunks", chunk_lengths, limit)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
