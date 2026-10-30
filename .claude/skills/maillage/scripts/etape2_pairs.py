"""Etape 2 de /maillage : pour chaque article SOURCE, classe les articles CIBLES qui traitent
EN PROFONDEUR de concepts que la source mentionne -- ce sont les seules paires dignes d'etre
lues en entier par l'etape 3 (etape3_links.py).

Lecture seule sur le graphe et la base de /maillage (rag_data/maillage/db/) ; ecrit
rag_data/maillage/pairs/<slug>.json, centralite.json et INDEX.md.

Pour un concept c et un article a (graphe de etape1_build_index.py) :
  n(c, a)   nombre de chunks de a qui mentionnent c
  idf(c)    log(N / nb d'articles mentionnant c) : un concept present partout ne dit rien
  titre(c,a) c apparait dans le titre ou un H1/H2 de a
c est CENTRAL dans la cible T si n(c, T) >= 3, ou n(c, T) >= 2 et c figure dans le titre/H2 de T.
Un concept cite une seule fois dans T (« mentionne une fois apres une illustration ») n'est
jamais central : il ne cree aucune paire.

Score(S -> T) = somme, sur les concepts centraux c de T que S mentionne (au moins un chunk),
de  idf(c) x poids_T(c) x presence_S(c)  avec
  poids_T(c)    = min(n(c,T), 6) / 6, +0,5 si c est dans le titre/H2 de T
  presence_S(c) = min(n(c,S), 3) / 3
Les doublons que le graphe n'a pas fusionnes (« exactitude » / « answer_correctness ») sont
rattrapes par similarite d'embedding (>= SOFT_SIM) entre concepts de S et concepts centraux de T.

Usage:
    etape2_pairs.py [--top N] [--min-score F] [--only SLUG ...]
"""
from __future__ import annotations
# Sortie propre : pas d'avertissements techniques ni de barres de chargement dans ce qui est relaye a l'utilisateur.
import os as _os
import warnings as _warnings
_warnings.filterwarnings("ignore")
for _k, _v in (("TQDM_DISABLE", "1"), ("HF_HUB_DISABLE_PROGRESS_BARS", "1"), ("HF_HUB_VERBOSITY", "error"),
               ("TRANSFORMERS_VERBOSITY", "error"), ("HF_HUB_DISABLE_TELEMETRY", "1")):
    _os.environ.setdefault(_k, _v)
import sys as _sys
from pathlib import Path as _Path
_ROOT = next(p for p in _Path(__file__).resolve().parents if (p / ".claude" / "skills" / "_rag_lib").is_dir())
_sys.path.insert(0, str(_ROOT / ".claude" / "skills" / "_rag_lib"))
import venv_guard  # noqa: F401,E402

import argparse
import atexit
import json
import math
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

import numpy as np

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

from graph_store import ConceptGraph  # noqa: E402

CONTENT_DIR = _ROOT / "rag_data" / "content" / "work"
MAILLAGE_DIR = _ROOT / "rag_data" / "maillage"
GRAPH_PATH = MAILLAGE_DIR / "db" / "graph"
WORK_DIR = MAILLAGE_DIR / "work"
PAIRS_DIR = MAILLAGE_DIR / "pairs"

CENTRAL_MIN = 3            # chunks de T mentionnant le concept
CENTRAL_MIN_IN_TITLE = 2   # ... si le concept est dans le titre/H2 de T
SOFT_SIM = 0.90
MIN_NAME_LEN = 3


def norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower())
    return re.sub(r"\s+", " ", "".join(c for c in t if not unicodedata.combining(c)))


def headings_and_title(folder: Path) -> tuple[str, str]:
    raw = (folder / "article.md").read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n", raw, re.S)
    title = ""
    if m:
        t = re.search(r'^title:\s*"?(.*?)"?\s*$', m.group(1), re.M)
        title = t.group(1) if t else ""
        raw = raw[m.end():]
    heads = " | ".join(re.findall(r"^#{1,2}\s+(.*)$", raw, re.M))
    return title, heads


def in_text(aliases: list[str], haystack_norm: str) -> bool:
    for a in aliases:
        na = norm(a).strip()
        if len(na) >= MIN_NAME_LEN and re.search(r"(?<![a-z0-9])" + re.escape(na) + r"(?![a-z0-9])", haystack_norm):
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--top", type=int, default=20, help="cibles gardees par source (defaut 20)")
    ap.add_argument("--min-score", type=float, default=0.0)
    ap.add_argument("--only", nargs="+", help="sources a traiter (defaut : toutes)")
    args = ap.parse_args()

    if not GRAPH_PATH.exists():
        print("ERREUR: graphe introuvable — lance d'abord etape1_build_index.py", file=sys.stderr)
        return 1
    folders = sorted(d for d in CONTENT_DIR.iterdir() if (d / "article.md").exists())
    ids = [f.name for f in folders]
    meta = {}
    for f in folders:
        title, heads = headings_and_title(f)
        chunks = json.loads((WORK_DIR / f.name / "chunks.json").read_text(encoding="utf-8"))
        meta[f.name] = {"title": title, "heads_norm": norm(title + " | " + heads), "n_chunks": len(chunks), "chunks": chunks}

    graph = ConceptGraph(GRAPH_PATH)
    atexit.register(graph.close)
    concepts = graph.all_concepts()
    if not concepts:
        print("ERREUR: graphe vide", file=sys.stderr)
        return 1
    # n(c, a) et chunks par article
    counts: dict[str, dict[str, set]] = {}   # concept id -> doc -> {chunk_index}
    for c in concepts:
        by_doc: dict[str, set] = defaultdict(set)
        for _chunk_id, doc, idx in graph.mentions_of(c.id):
            by_doc[doc].add(idx)
        counts[c.id] = by_doc
    N = len(ids)
    by_id = {c.id: c for c in concepts}
    idf = {cid: math.log(N / max(1, len(d))) for cid, d in counts.items()}

    # concepts centraux par article cible
    central: dict[str, dict[str, dict]] = {a: {} for a in ids}
    for c in concepts:
        if len(norm(c.canonical_form).strip()) < MIN_NAME_LEN or idf[c.id] <= 0:
            continue
        for a in ids:
            n = len(counts[c.id].get(a, ()))
            if not n:
                continue
            titled = in_text(c.aliases, meta[a]["heads_norm"])
            if n >= CENTRAL_MIN or (titled and n >= CENTRAL_MIN_IN_TITLE):
                central[a][c.id] = {"n": n, "titled": titled, "w": min(n, 6) / 6 + (0.5 if titled else 0.0)}

    # centralite.json : lisible pour auditer ce que le graphe pense de chaque article
    PAIRS_DIR.mkdir(parents=True, exist_ok=True)
    cen_out = {a: [{"concept": by_id[cid].canonical_form, "chunks": v["n"], "dans_titre": v["titled"],
                    "idf": round(idf[cid], 2)}
                   for cid, v in sorted(central[a].items(), key=lambda kv: -kv[1]["w"] * idf[kv[0]])[:25]]
               for a in ids}
    (PAIRS_DIR / "centralite.json").write_text(json.dumps(cen_out, ensure_ascii=False, indent=1), encoding="utf-8")

    emb = {c.id: np.asarray(c.embedding, dtype=np.float64) for c in concepts}
    for k, v in emb.items():
        nv = np.linalg.norm(v)
        emb[k] = v / nv if nv else v
    all_ids = list(emb)
    E = np.vstack([emb[i] for i in all_ids])
    pos = {cid: i for i, cid in enumerate(all_ids)}

    sources = [a for a in ids if not args.only or a in args.only]
    index_rows = []
    for s in sources:
        s_concepts = {cid: len(d[s]) for cid, d in counts.items() if s in d}
        s_idx = np.array([pos[c] for c in s_concepts]) if s_concepts else np.array([], dtype=int)
        s_list = list(s_concepts)
        results = []
        for t in ids:
            if t == s or not central[t]:
                continue
            shared = []
            score = 0.0
            for cid, cv in central[t].items():
                match, via, sim = None, "exact", 1.0
                if cid in s_concepts:
                    match = cid
                elif len(s_idx):
                    sims = E[s_idx] @ E[pos[cid]]
                    j = int(np.argmax(sims))
                    if sims[j] >= SOFT_SIM:
                        match, via, sim = s_list[j], "soft", float(sims[j])
                if match is None:
                    continue
                presence = min(s_concepts[match], 3) / 3
                contrib = idf[cid] * cv["w"] * presence * (1.0 if via == "exact" else sim)
                score += contrib
                shared.append({
                    "concept_cible": by_id[cid].canonical_form, "concept_source": by_id[match].canonical_form, "via": via,
                    "idf": round(idf[cid], 2), "chunks_cible": sorted(counts[cid][t]), "chunks_source": sorted(counts[match][s]),
                    "dans_titre_cible": cv["titled"], "contribution": round(contrib, 3),
                })
            if shared and score > args.min_score:
                shared.sort(key=lambda x: -x["contribution"])
                results.append({"target": t, "title": meta[t]["title"], "score": round(score, 3),
                                "n_concepts_partages": len(shared), "shared": shared})
        results.sort(key=lambda r: -r["score"])
        results = results[:args.top]
        (PAIRS_DIR / f"{s}.json").write_text(json.dumps(
            {"source": s, "title": meta[s]["title"], "targets": results}, ensure_ascii=False, indent=1), encoding="utf-8")
        index_rows.append((s, meta[s]["title"], results))
        print(f"[{s[:60]}] {len(results)} cible(s) retenue(s)")

    lines = ["# Maillage — paires source → cibles (score)", "",
             "Score = concepts centraux de la cible que la source mentionne, pondérés (voir etape2_pairs.py).", ""]
    for s, title, results in index_rows:
        lines += [f"## {title}", f"`{s}`", ""]
        for r in results:
            top = ", ".join(x["concept_cible"] for x in r["shared"][:4])
            lines.append(f"- {r['score']:.2f} — {r['title']} (`{r['target'][:40]}`) : {top}")
        lines.append("")
    (PAIRS_DIR / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"-> {PAIRS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
