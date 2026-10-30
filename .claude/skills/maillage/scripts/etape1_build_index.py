"""Etape 1 de /maillage : decompose les articles de rag_data/content/work/ en chunks,
les indexe et les relie par concepts, dans des bases A PART (jamais celles du
RAG des livres) :

    rag_data/maillage/db/vector/    base Chroma, collection "articles"
    rag_data/maillage/db/graph/     graphe Kuzu (Concept / Chunk / MENTIONS)
    rag_data/maillage/work/<slug>/  chunks.json + state.json de chaque article (+ usage.jsonl, totaltoken.jsonl)
    rag_data/maillage/work/concepts_cache.json   concepts par chunk (cle = hash du texte)
    rag_data/maillage/work/arbitrations.json     decisions d'arbitrage (meme concept ou non)

Etapes (extraction et nottext inutiles : article.md est deja du markdown) :
  1. chunking     chunk_markdown() sur le corps de l'article (frontmatter et
                  marqueurs <!--src:...--> retires : ils pollueraient les concepts)
  2. index        embeddings -> Chroma
  3. concepts     un `claude -p` par chunk (cache par hash du texte : un chunk
                  inchange n'est jamais recalcule, meme si l'article change)
  4. graphe       reconstruit en entier depuis le cache (graph.clear() puis
                  resolution d'entites identique a /rag-graphe : memes seuils,
                  meme arbitrage), puis controle d'integrite du stockage.
                  Reconstruction complete = pas d'alias residuels ; les
                  arbitrages en cache la rendent peu couteuse.

Reutilise _rag_lib et entity_resolution.py de /rag-graphe en LECTURE SEULE
(import) : aucun fichier partage n'est modifie.

Usage:
    etape1_build_index.py [--only SLUG ...] [--batch-size N] [--status]

--batch-size N : au plus N appels LLM (concepts, puis arbitrages) par invocation ;
code de sortie 3 tant qu'il reste du travail : relancer la meme commande.
Codes de sortie : 0 termine, 1 erreur d'usage, 2 bloquant, 3 lot partiel.
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
_sys.path.insert(0, str(_ROOT / ".claude" / "skills" / "rag-graphe" / "scripts"))

import argparse
import atexit
import hashlib
import functools
import json
import pickle
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

import concepts as _concepts  # noqa: E402
from chunk import Chunk, chunk_markdown  # noqa: E402
from concepts import ConceptExtractionError, ConceptMention, extract_concepts  # noqa: E402
import entity_resolution  # noqa: E402
from entity_resolution import (  # noqa: E402
    ArbitrationError, ArbitrationSide, _ARBITRATION_SYSTEM_PROMPT, _arbitrate_via_claude_code, _format_side,
    embedding_text, resolve_concept,
)
from _claude_code_client import ClaudeCodeCallError, MAX_FORMAT_RETRIES, run_claude_code, strip_code_fence  # noqa: E402
import etape4_usage as journal  # noqa: E402
from graph_store import ConceptGraph, GraphIntegrityError  # noqa: E402
from quality import is_noise_text  # noqa: E402
from vector_store import DocumentMetadata, embed_texts, get_collection, index_chunks  # noqa: E402

CONTENT_DIR = _ROOT / "rag_data" / "content" / "work"
MAILLAGE_DIR = _ROOT / "rag_data" / "maillage"
VECTOR_PATH = MAILLAGE_DIR / "db" / "vector"
GRAPH_PATH = MAILLAGE_DIR / "db" / "graph"
WORK_DIR = MAILLAGE_DIR / "work"
COLLECTION = "articles"
CONCEPTS_CACHE = WORK_DIR / "concepts_cache.json"
ARBITRATIONS = WORK_DIR / "arbitrations.json"
GRAPH_STATE = WORK_DIR / "graph_state.json"
EMBEDDINGS_CACHE = WORK_DIR / "embeddings_cache.pkl"
MODEL = "claude-haiku-5-5"
EFFORT = "medium"
TIME_LIMIT_S = 450  # une invocation reste sous la limite de 10 min du premier plan
STARTED = time.time()
MAX_SKIPPED_RATIO = 0.05  # meme seuil bloquant que le pipeline (mentions ignorees)


class BudgetExceeded(Exception):
    pass


class TimeUp(Exception):
    pass


def check_time() -> None:
    if time.time() - STARTED > TIME_LIMIT_S:
        raise TimeUp()


class Budget:
    """Nombre max d'appels LLM pour cette invocation (None = illimite)."""
    def __init__(self, n: int | None):
        self.left = n

    def spend(self) -> None:
        if self.left is not None:
            if self.left <= 0:
                raise BudgetExceeded()
            self.left -= 1


# ---------------------------------------------------------------- utilitaires
def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def article_body(folder: Path) -> str:
    """Corps de l'article pret a chunker : sans frontmatter ni commentaires HTML."""
    raw = (folder / "article.md").read_text(encoding="utf-8")
    raw = re.sub(r"\A---\n.*?\n---\n", "", raw, count=1, flags=re.S)
    raw = re.sub(r"<!--.*?-->", "", raw, flags=re.S)
    return re.sub(r"[ \t]+\n", "\n", raw).strip() + "\n"


class Doc:
    def __init__(self, folder: Path):
        self.folder = folder
        self.id = folder.name
        self.work = WORK_DIR / self.id
        self.body = article_body(folder)
        self.digest = sha(self.body)
        self.state = load_json(self.work / "state.json", {})
        self.chunks: list[Chunk] = []

    def save_state(self) -> None:
        save_json(self.work / "state.json", self.state)

    def load_chunks(self) -> None:
        self.chunks = [Chunk(**d) for d in load_json(self.work / "chunks.json", [])]

    def concept_chunks(self) -> list[Chunk]:
        return [c for c in self.chunks if not is_noise_text(c.text)]

    def index_chunks_list(self) -> list[Chunk]:
        return [c for c in self.chunks if c.has_code or c.has_formula or not is_noise_text(c.text)]


# ---------------------------------------------------------------- 1. chunking
def step_chunking(docs: list[Doc]) -> int:
    n_redone = 0
    for d in docs:
        if d.state.get("chunk_digest") == d.digest and (d.work / "chunks.json").exists():
            d.load_chunks()
            continue
        chunks = chunk_markdown(d.body)
        if not chunks:
            print(f"BLOQUANT: aucun chunk produit pour {d.id}", file=sys.stderr)
            raise SystemExit(2)
        save_json(d.work / "chunks.json", [asdict(c) for c in chunks])
        d.chunks = chunks
        d.state = {"chunk_digest": d.digest, "n_chunks": len(chunks)}  # un texte change invalide la suite
        d.save_state()
        n_redone += 1
    total = sum(len(d.chunks) for d in docs)
    print(f"[1/4 chunking] {total} chunk(s) pour {len(docs)} article(s) ({n_redone} article(s) re-decoupe(s))")
    return total


# ---------------------------------------------------------------- 2. index
def step_index(docs: list[Doc]) -> None:
    collection = get_collection(VECTOR_PATH, COLLECTION)
    n_redone = 0
    for d in docs:
        want = len(d.index_chunks_list())
        have = len(collection.get(where={"document_id": d.id})["ids"])
        if d.state.get("index_digest") == d.digest and have == want:
            continue
        n = index_chunks(d.index_chunks_list(), DocumentMetadata(document_id=d.id, source_path=str(d.folder / "article.md")),
                         db_path=VECTOR_PATH, collection_name=COLLECTION)
        have = len(collection.get(where={"document_id": d.id})["ids"])
        if have != n or n != want:
            print(f"BLOQUANT: index de {d.id} : {n} indexe(s), {have} dans la base, {want} attendu(s)", file=sys.stderr)
            raise SystemExit(2)
        d.state["index_digest"] = d.digest
        d.save_state()
        n_redone += 1
    stale = {m["document_id"] for m in collection.get()["metadatas"]} - {d.id for d in docs}
    note = f" ; ATTENTION documents orphelins dans la base : {sorted(stale)}" if stale else ""
    print(f"[2/4 index] {sum(len(d.index_chunks_list()) for d in docs)} chunk(s) dans {VECTOR_PATH} "
          f"({n_redone} article(s) re-indexe(s)){note}")


# ---------------------------------------------------------------- 3. concepts
def step_concepts(docs: list[Doc], budget: Budget) -> tuple[bool, int]:
    """Retourne (complet, nb_echecs). Reprise : un chunk deja en cache n'est jamais recalcule."""
    cache: dict = load_json(CONCEPTS_CACHE, {})
    todo = [(d, c) for d in docs for c in d.concept_chunks() if sha(c.text) not in cache]
    total = sum(len(d.concept_chunks()) for d in docs)
    print(f"[3/4 concepts] {total - len(todo)}/{total} chunk(s) en cache, {len(todo)} a extraire")
    for d in docs:
        n_cache = sum(1 for c in d.concept_chunks() if sha(c.text) in cache)
        if n_cache:
            journal.cache_hit("extraction_concepts", etape="concepts", article=d.id, nb_chunks=n_cache)
    failed = 0
    for k, (d, c) in enumerate(todo, 1):
        try:
            budget.spend()
        except BudgetExceeded:
            break
        try:
            with journal.bloc("extraction_concepts", etape="concepts", article=d.id, chunk=c.index):
                mentions = extract_concepts(c.text)
        except ConceptExtractionError as exc:
            print(f"  {d.id} chunk {c.index}: extraction ignoree ({exc})", file=sys.stderr)
            failed += 1
            continue
        cache[sha(c.text)] = [asdict(m) for m in mentions]
        save_json(CONCEPTS_CACHE, cache)  # checkpoint apres chaque chunk
        print(f">>> Progression : {total - len(todo) + k - failed}/{total} chunk(s) ({d.id} #{c.index})")
    remaining = [(d, c) for d in docs for c in d.concept_chunks() if sha(c.text) not in cache]
    return (not remaining), failed


# ---------------------------------------------------------------- 4. graphe
def graph_signature(docs: list[Doc]) -> str:
    cache = load_json(CONCEPTS_CACHE, {})
    parts = [f"{d.id}:{c.index}:{sha(c.text)}:{sha(json.dumps(cache.get(sha(c.text)), sort_keys=True))}"
             for d in docs for c in d.concept_chunks()]
    return sha("|".join(parts))


class _FastCosine:
    """Remplace graph_store.cosine_similarities DANS CE PROCESSUS (aucun fichier partage
    modifie) : meme resultat, mais la matrice des embeddings connus est conservee d'un appel
    a l'autre au lieu d'etre reconvertie en numpy a chaque mention (~3000 x 1024 flottants
    Python, ce qui rendait un passage complet interminable)."""
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.refs: list = []   # references gardees : evite qu'un id() soit reutilise
        self.ids: list[int] = []
        self.M = np.zeros((0, 0))

    def __call__(self, query, candidates):
        n = len(candidates)
        if n == 0:
            return []
        k = len(self.ids)
        ids = [id(c) for c in candidates]
        if n < k or ids[:k] != self.ids:
            self.reset()
            k = 0
        if n > k:
            rows = np.asarray(candidates[k:], dtype=np.float64)
            norms = np.linalg.norm(rows, axis=1, keepdims=True)
            rows = np.divide(rows, norms, out=np.zeros_like(rows), where=norms > 0)
            self.M = rows if k == 0 else np.vstack([self.M, rows])
            self.refs.extend(candidates[k:])
            self.ids = ids
        q = np.asarray(query, dtype=np.float64)
        qn = np.linalg.norm(q)
        if qn == 0:
            return [0.0] * n
        return (self.M @ (q / qn)).tolist()


_FAST_COSINE = _FastCosine()
entity_resolution.cosine_similarities = _FAST_COSINE
# meme fonction, memoisee : _lexically_related renormalisait toutes les formes connues a chaque mention
entity_resolution._normalize_for_containment = functools.lru_cache(maxsize=None)(entity_resolution._normalize_for_containment)


def run_claude_configured(system_prompt: str, input_text: str, *, model: str | None = None, timeout: int,
                          effort: str | None = None) -> str:
    """Modele et effort du maillage (MODEL, EFFORT) imposes a tous les `claude -p` de ce
    script, y compris ceux de concepts.py et entity_resolution.py (lus en LECTURE SEULE) qui
    ne transmettent ni effort ni modele : sans cela, le modele par defaut du compte (Opus) serait utilise.
    Chaque appel est journalise dans rag_data/maillage/usage.jsonl (voir etape4_usage.py)."""
    return journal.appel_claude(run_claude_code, system_prompt, input_text, model=model or MODEL,
                                effort=effort or EFFORT, timeout=timeout)


_concepts.run_claude_code = run_claude_configured
entity_resolution.run_claude_code = run_claude_configured


class FakeGraph:
    """Graphe en memoire pour les tours de simulation : memes appels que ConceptGraph, sans Kuzu."""
    def __init__(self) -> None:
        self.n = 0

    def create_concept(self, canonical_form, type_, alias, embedding, sense=""):
        self.n += 1
        return f"sim{self.n}"

    def add_alias(self, concept_id, alias) -> None:
        pass

    def add_chunk(self, *args) -> None:
        pass

    def link_mention(self, *args) -> None:
        pass


def pair_key(a: ArbitrationSide, b: ArbitrationSide) -> str:
    return " || ".join(sorted(f"{s.name} : {s.sense}" for s in (a, b)))


def embed_cached(texts: list[str]) -> list[list[float]]:
    """Embeddings des textes, mis en cache sur disque (cle = texte) : ~0,2 s par mention sur CPU,
    donc ~14 min pour tous les articles -- jamais a refaire. Leve TimeUp si l'invocation
    approche la limite ; la relance reprend la ou le cache s'est arrete."""
    cache: dict = pickle.loads(EMBEDDINGS_CACHE.read_bytes()) if EMBEDDINGS_CACHE.exists() else {}
    todo = [t for t in dict.fromkeys(texts) if t not in cache]
    if todo:
        print(f"[4/4 graphe] embeddings : {len(texts) - len(todo)}/{len(texts)} en cache, {len(todo)} a calculer")
    for i in range(0, len(todo), 32):
        try:
            check_time()
        except TimeUp:
            EMBEDDINGS_CACHE.write_bytes(pickle.dumps(cache))
            raise
        batch = todo[i:i + 32]
        cache.update(zip(batch, embed_texts(batch)))
        if (i // 32) % 10 == 9:
            EMBEDDINGS_CACHE.write_bytes(pickle.dumps(cache))  # checkpoint
    if todo:
        EMBEDDINGS_CACHE.write_bytes(pickle.dumps(cache))
    return [cache[t] for t in texts]


def build_plan(docs: list[Doc]) -> dict:
    """{doc_id: [(chunk, [ConceptMention], [embedding])]} ; embeddings via le cache disque."""
    cache = load_json(CONCEPTS_CACHE, {})
    entries = {d.id: [(c, [ConceptMention(**m) for m in cache[sha(c.text)]]) for c in d.concept_chunks()] for d in docs}
    texts = [embedding_text(m) for d in docs for _, ms in entries[d.id] for m in ms]
    embeds = iter(embed_cached(texts))
    return {d.id: [(c, ms, [next(embeds) for _ in ms]) for c, ms in entries[d.id]] for d in docs}


def resolve_all(docs: list[Doc], plan: dict, graph, arbitrate, log=None):
    """Meme boucle pour la simulation (FakeGraph) et la construction reelle (ConceptGraph)."""
    _FAST_COSINE.reset()
    known: list = []
    n_linked = n_skipped = 0
    for d in docs:
        for c, mentions, embeds in plan[d.id]:
            chunk_id = f"{d.id}::{c.index}"
            graph.add_chunk(chunk_id, d.id, c.index)
            for m, emb in zip(mentions, embeds):
                try:
                    out = resolve_concept(m, graph, known_concepts=known, embedding=emb,
                                          arbitrate_fn=arbitrate, document_id=None, excerpt=c.text)
                    # document_id=None : fusion automatique >= 0,92 meme si le concept n'a jamais
                    # ete vu dans CET article. La regle du pipeline (arbitrage systematique entre
                    # documents) protege les homonymes entre pdf (lambda Python / lambda
                    # regularisation) ; ici tous les articles traitent d'IA/ML et l'embedding
                    # inclut deja la definition du sens.
                except ArbitrationError as exc:
                    print(f"  {d.id} chunk {c.index}: mention {m.canonical_form!r} ignoree (arbitrage echoue : {exc})", file=sys.stderr)
                    n_skipped += 1
                    continue
                graph.link_mention(chunk_id, out.concept_id)
                n_linked += 1
        if log:
            log(d, known)
    return known, n_linked, n_skipped


# --- arbitrage EN LOTS : N paires par appel `claude -p` au lieu d'une (meme prompt, memes
# informations par paire, meme question) ; les decisions sont rejugees par tours car chaque
# fusion change les candidats des mentions suivantes.
BATCH_PAIRS = 20
ROUND_CAP = 400   # paires jugees par tour avant de resimuler
BATCH_SYSTEM = _ARBITRATION_SYSTEM_PROMPT.rsplit("Réponds UNIQUEMENT", 1)[0] + (
    "Tu reçois PLUSIEURS paires numérotées, à juger INDÉPENDAMMENT les unes des autres. Réponds UNIQUEMENT avec un "
    'objet JSON {"decisions": {"1": true, "2": false, ...}} comportant une clé par paire (true = MÊME concept), '
    "sans texte ni balise autour."
)


def judge_batch(items: list[tuple[str, tuple[ArbitrationSide, ArbitrationSide]]]) -> dict[str, bool]:
    prompt = "\n\n".join(f"=== PAIRE {i} ===\n{_format_side('A', a)}\n\n{_format_side('B', b)}"
                         for i, (_, (a, b)) in enumerate(items, 1))
    last = ""
    for attempt in range(MAX_FORMAT_RETRIES + 1):
        try:
            raw = json.loads(strip_code_fence(run_claude_configured(BATCH_SYSTEM, prompt, timeout=600)))["decisions"]
            out = {k: raw[str(i)] for i, (k, _) in enumerate(items, 1) if isinstance(raw.get(str(i)), bool)}
            if len(out) >= max(1, len(items) * 3 // 4):
                return out  # les paires sans reponse valide reviennent au tour suivant
            last = f"{len(out)}/{len(items)} decisions valides"
        except (ClaudeCodeCallError, json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
            last = str(exc)[:200]
        print(f"  lot invalide (essai {attempt + 1}) : {last}", file=sys.stderr)
    raise ArbitrationError(f"lot d'arbitrage inexploitable apres {MAX_FORMAT_RETRIES + 1} essais : {last}")


def arbitration_rounds(docs: list[Doc], plan: dict, budget: Budget) -> None:
    """Repete simulation -> jugement en lots jusqu'a ce qu'il ne manque plus aucune decision.
    Leve BudgetExceeded quand le budget d'appels de l'invocation est epuise."""
    decisions = load_json(ARBITRATIONS, {})
    if decisions:
        journal.cache_hit("arbitrage_lot", etape="graphe", nb_decisions=len(decisions))
    round_no = 0
    while True:
        misses: dict = {}

        def arb(a, b):
            k = pair_key(a, b)
            if k in decisions:
                return decisions[k]
            misses.setdefault(k, (a, b))
            return False  # hypothese provisoire "different" : corrigee au tour suivant

        known, _, _ = resolve_all(docs, plan, FakeGraph(), arb)
        if not misses:
            print(f"[4/4 graphe] simulation complete : {len(known)} concept(s), toutes les decisions sont en cache")
            return
        round_no += 1
        pending = list(misses.items())[:ROUND_CAP]
        print(f">>> Tour {round_no} : {len(misses)} paire(s) sans decision, {len(pending)} jugee(s) ce tour "
              f"({len(known)} concept(s) en simulation)")
        for i in range(0, len(pending), BATCH_PAIRS):
            check_time()
            budget.spend()
            with journal.bloc("arbitrage_lot", etape="graphe", tour=round_no, lot=i // BATCH_PAIRS + 1,
                              paires=len(pending[i:i + BATCH_PAIRS])):
                decisions.update(judge_batch(pending[i:i + BATCH_PAIRS]))
            save_json(ARBITRATIONS, decisions)
            print(f"    lot {i // BATCH_PAIRS + 1}/{(len(pending) + BATCH_PAIRS - 1) // BATCH_PAIRS} juge")


def calibrate(docs: list[Doc], plan: dict, n: int) -> int:
    """Compare le jugement EN LOT au jugement paire par paire deja en cache (ne modifie rien)."""
    decisions = load_json(ARBITRATIONS, {})
    hits: dict = {}

    def arb(a, b):
        k = pair_key(a, b)
        if k in decisions:
            hits.setdefault(k, (a, b))
            return decisions[k]
        return False

    resolve_all(docs, plan, FakeGraph(), arb)
    items = list(hits.items())
    if not items:
        print("Aucune paire deja arbitree retrouvee : calibration impossible.")
        return 1
    step = max(1, len(items) // n)
    sample = items[::step][:n]
    print(f"Calibration : {len(sample)} paire(s) (sur {len(items)} deja arbitrees une par une), lots de {BATCH_PAIRS}")
    agree, diffs = 0, []
    for i in range(0, len(sample), BATCH_PAIRS):
        with journal.bloc("calibrage_lot", etape="graphe", lot=i // BATCH_PAIRS + 1, paires=len(sample[i:i + BATCH_PAIRS])):
            res = judge_batch(sample[i:i + BATCH_PAIRS])
        for k, _ in sample[i:i + BATCH_PAIRS]:
            if k in res:
                if res[k] == decisions[k]:
                    agree += 1
                else:
                    diffs.append((k, decisions[k], res[k]))
    total = agree + len(diffs)
    print(f"Accord : {agree}/{total} ({agree / max(1, total):.0%}) ; desaccords : {len(diffs)}")
    for k, single, batch in diffs:
        print(f"  - {k}\n      une par une : {'meme' if single else 'different'} | en lot : {'meme' if batch else 'different'}")
    return 0


def step_graph(docs: list[Doc], budget: Budget) -> int:
    signature = graph_signature(docs)
    state = load_json(GRAPH_STATE, {})
    graph = ConceptGraph(GRAPH_PATH)
    atexit.register(graph.close)

    if state.get("signature") == signature:
        stats = graph.stats()
        problems = graph.check_integrity()
        if not problems and set(stats["chunks_by_document"]) == {d.id for d in docs}:
            print(f"[4/4 graphe] deja a jour : {stats['concepts']} concept(s), {stats['mentions']} lien(s) mention")
            return 0
        print("[4/4 graphe] etat 'a jour' non confirme par la base — reconstruction" + (f" ({problems[0]})" if problems else ""))
        for p in problems[:5]:
            print(f"  anomalie : {p}", file=sys.stderr)

    try:
        plan = build_plan(docs)
        arbitration_rounds(docs, plan, budget)
    except TimeUp:
        print("Temps d'invocation ecoule (progression sauvegardee) : relance la meme commande pour continuer.")
        return 3
    except BudgetExceeded:
        print("Lot d'appels epuise : decisions d'arbitrage deja en cache, relance la meme commande pour continuer.")
        return 3
    except ArbitrationError as exc:
        print(f"BLOQUANT: arbitrage en lots : {exc}", file=sys.stderr)
        return 2

    # Construction reelle : toutes les decisions sont en cache (simulation identique). Un manque
    # imprevu retombe sur l'arbitrage paire par paire (jamais attendu).
    decisions = load_json(ARBITRATIONS, {})

    def arbitrate(a, b):
        k = pair_key(a, b)
        if k in decisions:
            return decisions[k]
        budget.spend()
        with journal.bloc("arbitrage_unitaire", etape="graphe"):
            decisions[k] = _arbitrate_via_claude_code(a, b)
        save_json(ARBITRATIONS, decisions)
        return decisions[k]

    graph.clear()
    state.pop("signature", None)
    save_json(GRAPH_STATE, state)  # une reconstruction interrompue ne doit jamais passer pour a jour
    try:
        known, n_linked, n_skipped = resolve_all(
            docs, plan, graph, arbitrate,
            log=lambda d, kn: print(f">>> Graphe : {d.id} — {len(kn)} concept(s) au total"))
    except BudgetExceeded:
        print("Lot d'appels epuise pendant la construction : relance la meme commande.")
        return 3
    except GraphIntegrityError as exc:
        print("BLOQUANT: integrite du graphe pendant la construction :\n" + str(exc), file=sys.stderr)
        return 2

    problems = graph.check_integrity(known)
    if problems:
        print("BLOQUANT: le stockage Kuzu ne correspond pas a la memoire :", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 2
    stats = graph.stats()
    unlinked = [f"{d.id}::{c.index}" for d in docs for c in d.concept_chunks()
                if not graph.concepts_of_chunk(f"{d.id}::{c.index}")]
    ratio = n_skipped / max(1, n_linked + n_skipped)
    print(f"Graphe : {stats['concepts']} concept(s), {n_linked} mention(s) liee(s), {n_skipped} ignoree(s) ({ratio:.1%}), "
          f"{len(unlinked)} chunk(s) sans concept")
    if ratio > MAX_SKIPPED_RATIO:
        print(f"BLOQUANT: plus de {MAX_SKIPPED_RATIO:.0%} de mentions ignorees — relance pour retenter", file=sys.stderr)
        return 2
    state.update({"signature": signature, "concepts": stats["concepts"], "mentions": stats["mentions"],
                  "chunks_without_concepts": len(unlinked)})
    save_json(GRAPH_STATE, state)
    print(f"[4/4 graphe] OK -> {GRAPH_PATH}")
    return 0


# ---------------------------------------------------------------- statut
def print_status(docs: list[Doc]) -> None:
    cache = load_json(CONCEPTS_CACHE, {})
    print(f"{'article':62} chunks index concepts")
    for d in docs:
        d.load_chunks()
        cc = d.concept_chunks()
        done = sum(1 for c in cc if sha(c.text) in cache)
        print(f"{d.id[:62]:62} {len(d.chunks):6} {'oui' if d.state.get('index_digest') == d.digest else 'non':5} "
              f"{done}/{len(cc)}" if d.chunks else f"{d.id[:62]:62}      - non   -")
    g = load_json(GRAPH_STATE, {})
    print("graphe :", "a jour" if g.get("signature") == graph_signature(docs) else "a (re)construire")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="+", help="limite les etapes 1-3 a ces dossiers (le graphe n'est alors pas construit)")
    ap.add_argument("--batch-size", type=int, default=None)
    ap.add_argument("--status", action="store_true", help="affiche l'avancement, ne modifie rien")
    ap.add_argument("--calibrate", type=int, metavar="N",
                    help="compare le jugement en lot au jugement paire par paire sur N paires deja arbitrees ; ne modifie rien")
    args = ap.parse_args()

    folders = sorted(d for d in CONTENT_DIR.iterdir() if (d / "article.md").exists())
    all_docs = [Doc(f) for f in folders]
    if args.only:
        unknown = set(args.only) - {d.id for d in all_docs}
        if unknown:
            print(f"ERREUR: dossier(s) inconnu(s) : {sorted(unknown)}", file=sys.stderr)
            return 1
        docs = [d for d in all_docs if d.id in set(args.only)]
    else:
        docs = all_docs

    if args.status:
        print_status(all_docs)
        return 0

    if args.calibrate:
        for d in all_docs:
            d.load_chunks()
        try:
            return calibrate(all_docs, build_plan(all_docs), args.calibrate)
        except TimeUp:
            print("Temps d'invocation ecoule (embeddings sauvegardes) : relance la meme commande pour continuer.")
            return 3

    budget = Budget(args.batch_size)
    step_chunking(docs)
    step_index(docs)
    complete, failed = step_concepts(docs, budget)
    if not complete:
        if budget.left == 0:
            print("Lot de concepts termine, il en reste — relance exactement la meme commande pour continuer.")
            return 3
        print(f"BLOQUANT: {failed} chunk(s) en echec d'extraction (retentes a la relance).", file=sys.stderr)
        return 2
    if failed:
        print(f"ATTENTION: {failed} chunk(s) en echec, retentes a la relance.", file=sys.stderr)
    if args.only:
        print("[4/4 graphe] ignore (--only) : le graphe se construit sur tous les articles")
        return 0
    return step_graph(docs, budget)


if __name__ == "__main__":
    raise SystemExit(main())
