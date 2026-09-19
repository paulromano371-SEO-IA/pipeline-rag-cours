"""Analyse hors-pipeline, lecture seule : pour une requete donnee, mesure si
la recherche remonte, en plus du meilleur passage textuel, TOUT le contenu
associe (formules, images, code) de la meme section -- pas seulement le
chunk le plus proche par similarite cosinus. Compare DEUX strategies :

1. La recherche brute (`vector_store.search`, top-k plat) -- la methode
   d'origine, dont les limites ont motive la construction de `retrieval.py`.
2. `retrieval.retrieve` (section + expansion par concept via le graphe) --
   la strategie actuelle, censee corriger ces limites.

Contexte : l'objectif final du RAG n'est pas de retrouver LE chunk qui
repond le mieux a une question, mais de fournir a un systeme de generation
de contenu (CMS via MCP) TOUT ce qui est necessaire pour ecrire un contenu
complet sur un sujet -- texte, formules, images, code source. Un simple
top-k par similarite ne garantit pas cette couverture : rien ne dit qu'une
formule ou la description d'une image de la meme section remonte dans les
memes rangs que le texte qui l'entoure, et le code associe vit parfois dans
une section SOEUR (ex. un "atelier pratique"), jamais dans le meme
`heading_trail`.

Methode : le meilleur resultat de la recherche brute (rang 1) definit la
"section de reference" (son `heading_trail` exact). Tous les chunks du meme
document partageant EXACTEMENT ce fil d'ariane, dans `chunks.json`, forment
l'inventaire de verite-terrain de cette section (compte de chunks avec
code / formule / image / texte pur). On mesure ensuite, pour la recherche
brute a differentes tailles de top_k ET pour `retrieve()`, combien de ces
chunks -- et de quels types -- sont effectivement ramenes ; puis, pour
`retrieve()` seulement, quel contenu supplementaire HORS de cette section
est ramene (la valeur ajoutee propre a l'expansion par concept).

Limite connue : `Chunk` (voir `_rag_lib/chunk.py`) expose `has_code` et
`has_formula` mais pas de `has_image` equivalent -- la presence d'image est
donc detectee ici par un motif Markdown (`![`) dans le texte du chunk, un
proxy fiable (c'est le meme motif que `split_into_blocks` utilise pour
detecter un bloc image) mais pas un champ natif du schema.

Usage:
    python analyze_retrieval_coverage.py "<requete>" [--top-k-list 5,10,20,40] [--db-path ...] [--graph-db-path ...]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_RAG_LIB = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "_rag_lib"
sys.path.insert(0, str(_RAG_LIB))

import paths  # noqa: E402
from vector_store import search  # noqa: E402
from retrieval import retrieve  # noqa: E402

_IMAGE_MARKER_RE = re.compile(r"!\[")


@dataclass
class ContentTypes:
    has_code: bool
    has_formula: bool
    has_image: bool

    @property
    def has_any_nontext(self) -> bool:
        return self.has_code or self.has_formula or self.has_image

    def label(self) -> str:
        tags = []
        if self.has_code:
            tags.append("code")
        if self.has_formula:
            tags.append("formule")
        if self.has_image:
            tags.append("image")
        return "+".join(tags) if tags else "texte seul"


def content_types_of(chunk: dict) -> ContentTypes:
    return ContentTypes(
        has_code=bool(chunk.get("has_code")),
        has_formula=bool(chunk.get("has_formula")),
        has_image=bool(_IMAGE_MARKER_RE.search(chunk.get("text", ""))),
    )


def load_chunks(document_id: str) -> list[dict]:
    work_dir = paths.work_dir_for(document_id)
    chunks_path = work_dir / "chunks.json"
    if not chunks_path.exists():
        raise FileNotFoundError(f"chunks.json introuvable pour {document_id} ({chunks_path})")
    return json.loads(chunks_path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", type=str)
    parser.add_argument("--top-k-list", type=str, default="5,10,20,40")
    parser.add_argument("--db-path", type=str, default=str(paths.VECTOR_DB_PATH))
    parser.add_argument("--graph-db-path", type=str, default=str(paths.GRAPH_DB_PATH))
    args = parser.parse_args()

    top_k_list = sorted(int(x) for x in args.top_k_list.split(","))
    max_top_k = top_k_list[-1]

    result = search(args.query, db_path=Path(args.db_path), top_k=max_top_k)
    metadatas = result["metadatas"][0]
    ids = result["ids"][0]
    distances = result["distances"][0]

    if not metadatas:
        print("Aucun resultat -- base vide ou erreur de recherche.", file=sys.stderr)
        return 1

    top1 = metadatas[0]
    document_id = top1["document_id"]
    reference_trail = top1["heading_trail"]

    print(f"Requete : {args.query!r}")
    print(f"Meilleur resultat -> document={document_id}  section={reference_trail!r}  distance={distances[0]:.4f}")

    all_chunks = load_chunks(document_id)
    section_chunks = [c for c in all_chunks if " > ".join(c["heading_trail"]) == reference_trail]
    print(f"\nSection de reference : {len(section_chunks)} chunk(s) au total dans chunks.json")

    section_types = {c["index"]: content_types_of(c) for c in section_chunks}
    section_index_set = set(section_types)

    totals = {
        "code": sum(1 for t in section_types.values() if t.has_code),
        "formule": sum(1 for t in section_types.values() if t.has_formula),
        "image": sum(1 for t in section_types.values() if t.has_image),
        "texte seul": sum(1 for t in section_types.values() if not t.has_any_nontext),
    }
    print("Inventaire de la section :")
    for kind, n in totals.items():
        print(f"  - {kind:10s}: {n}")

    retrieved_in_order: list[tuple[int, dict, float]] = []
    for meta, dist in zip(metadatas, distances):
        if meta["document_id"] != document_id:
            continue
        idx = meta["chunk_index"]
        if idx in section_index_set:
            retrieved_in_order.append((idx, section_types[idx], dist))

    first_rank_by_type: dict[str, int | None] = {"code": None, "formule": None, "image": None, "texte seul": None}
    for rank, (idx, types, _dist) in enumerate(retrieved_in_order, start=1):
        for kind, present in [("code", types.has_code), ("formule", types.has_formula), ("image", types.has_image)]:
            if present and first_rank_by_type[kind] is None:
                first_rank_by_type[kind] = rank
        if not types.has_any_nontext and first_rank_by_type["texte seul"] is None:
            first_rank_by_type["texte seul"] = rank

    print(f"\nChunks de la section retrouves dans le top-{max_top_k} (tous rangs confondus, meme document) :")
    for idx, types, dist in retrieved_in_order:
        print(f"  rang trouve -- chunk #{idx:4d}  type={types.label():14s}  distance={dist:.4f}")
    if not retrieved_in_order:
        print("  (aucun)")

    print(f"\n=== Couverture par taille de top_k (document {document_id!r}) ===")
    header = f"{'top_k':>6s} | " + " | ".join(f"{k:>10s}" for k in totals)
    print(header)
    print("-" * len(header))
    for top_k in top_k_list:
        retrieved_subset_idx = {
            meta["chunk_index"]
            for meta in metadatas[:top_k]
            if meta["document_id"] == document_id and meta["chunk_index"] in section_index_set
        }
        row = []
        for kind in totals:
            if kind == "texte seul":
                total_k = totals[kind]
                found_k = sum(
                    1 for idx in retrieved_subset_idx if not section_types[idx].has_any_nontext
                )
            elif kind == "code":
                total_k = totals[kind]
                found_k = sum(1 for idx in retrieved_subset_idx if section_types[idx].has_code)
            elif kind == "formule":
                total_k = totals[kind]
                found_k = sum(1 for idx in retrieved_subset_idx if section_types[idx].has_formula)
            else:
                total_k = totals[kind]
                found_k = sum(1 for idx in retrieved_subset_idx if section_types[idx].has_image)
            row.append(f"{found_k}/{total_k}")
        print(f"{top_k:>6d} | " + " | ".join(f"{v:>10s}" for v in row))

    print("\nPremier rang (dans le top_k max) ou chaque type apparait :")
    for kind, rank in first_rank_by_type.items():
        print(f"  - {kind:10s}: {'jamais' if rank is None else f'rang {rank}'}")

    print(f"\n=== retrieval.retrieve() (section + expansion par concept) ===")
    retrieved = retrieve(args.query, db_path=Path(args.db_path), graph_db_path=Path(args.graph_db_path))

    by_reason: dict[str, int] = {}
    for r in retrieved:
        key = r.reason.split(":")[0]
        by_reason[key] = by_reason.get(key, 0) + 1
    print(f"Total : {len(retrieved)} chunk(s) -- " + ", ".join(f"{k}={v}" for k, v in sorted(by_reason.items())))

    in_section_idx = {r.chunk_index for r in retrieved if r.document_id == document_id and r.chunk_index in section_index_set}
    row = []
    for kind in totals:
        total_k = totals[kind]
        if kind == "texte seul":
            found_k = sum(1 for idx in in_section_idx if not section_types[idx].has_any_nontext)
        elif kind == "code":
            found_k = sum(1 for idx in in_section_idx if section_types[idx].has_code)
        elif kind == "formule":
            found_k = sum(1 for idx in in_section_idx if section_types[idx].has_formula)
        else:
            found_k = sum(1 for idx in in_section_idx if section_types[idx].has_image)
        row.append(f"{kind}={found_k}/{total_k}")
    print("Couverture de la section de reference :", "  ".join(row))

    outside = [r for r in retrieved if not (r.document_id == document_id and r.chunk_index in section_index_set)]
    if outside:
        print(f"\nContenu supplementaire HORS de la section de reference ({len(outside)} chunk(s)) -- la valeur ajoutee de l'expansion par concept :")
        seen_sections = sorted({(r.document_id, " > ".join(r.heading_trail)) for r in outside})
        for doc, sec in seen_sections:
            print(f"  - [{doc}] {sec}")
    else:
        print("\nAucun contenu supplementaire hors de la section de reference.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
