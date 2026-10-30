"""Couverture d'une question : combien de pdf et de passages y répondent réellement.

Le décompte lexical du sujet (mot pour mot) ne dit pas si les pdf traitent la question
reformulée. Ici, on interroge la base vectorielle avec la question, on prend les TOP_N passages
les plus proches, puis un appel LLM (Haiku, effort medium) dit lesquels répondent vraiment à
la question. Les décomptes sont ceux de ces passages retenus : pdf distincts et passages.

Lecture seule sur la base vectorielle (rag_data/db/vector). La question est embeddée avec le
même modèle que l'index (BAAI/bge-m3), sans préfixe. Aucun module d'une autre commande n'est
importé : chromadb et sentence-transformers sont appelés directement.
Mis en cache (rag_data/expressions/cache/question_couverture.json) : clé = question, candidats,
modèle et consigne. Une même question sur les mêmes candidats n'est jamais redemandée.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import paths
from _claude_code_client_systemfile import ClaudeCodeCallError, run_claude_code, strip_code_fence

from inc_usage import log_cache_hit, log_usage

VECTOR_DB_PATH = paths.VECTOR_DB_PATH
COLLECTION_NAME = "documents"
EMBED_MODEL = "BAAI/bge-m3"

CACHE_DIR = paths.RAG_DATA_DIR / "expressions" / "cache"
CACHE_FILE = CACHE_DIR / "question_couverture.json"

TOP_N = 40  # passages candidats jugés par question (plafond des décomptes)
EXTRAIT_MAX = 700  # caractères par passage envoyé au jugement
MODEL_JUGEMENT = "claude-haiku-5-5"
EFFORT_JUGEMENT = "medium"
TIMEOUT_S = 600
MAX_ATTEMPTS = 3

SYSTEM_JUGEMENT = """Tu juges si des extraits de livres répondent à une question d'un site pédagogique francophone.

Tu reçois :
- « question » : la question à laquelle l'article doit répondre ;
- « extraits » : une liste d'extraits, chacun avec un « id ».

Un extrait répond à la question s'il parle de l'objet visé par la question, dans le sens de la question, et le définit, l'explique, le démontre ou le décrit. Il ne répond pas dans les cas suivants :
- il cite le terme en passant, sans que l'objet soit son sujet ;
- il parle d'un autre objet voisin (par exemple l'échantillonnage ou les probabilités des mots, quand la question porte sur le jeton) ;
- il emploie le même mot dans un autre sens (par exemple un jeton de casino pour un jeton de texte).

N'invente rien. Ne juge que d'après les extraits.

Sortie : uniquement un objet JSON, sans texte autour :
{"pertinents": [id, …]}
La liste peut être vide. Les identifiants reprennent exactement ceux reçus."""

_modele = None
_collection = None


def _embedder(texte: str) -> list[float]:
    """Embedde la question avec le même modèle que l'index (chargé une seule fois)."""
    global _modele
    if _modele is None:
        from sentence_transformers import SentenceTransformer
        _modele = SentenceTransformer(EMBED_MODEL)
    return _modele.encode([texte], convert_to_numpy=True)[0].tolist()


def _ouvrir_collection():
    global _collection
    if _collection is None:
        import chromadb
        client = chromadb.PersistentClient(path=str(VECTOR_DB_PATH))
        _collection = client.get_collection(COLLECTION_NAME)
    return _collection


def _candidats(question: str) -> list[dict]:
    """Les TOP_N passages les plus proches de la question : id, livre, texte."""
    vecteur = _embedder(question)
    resultat = _ouvrir_collection().query(
        query_embeddings=[vecteur], n_results=TOP_N, include=["documents", "metadatas"],
    )
    candidats = []
    for texte, meta in zip(resultat["documents"][0], resultat["metadatas"][0]):
        candidats.append({"livre": meta["document_id"], "chunk": meta["chunk_index"], "texte": texte or ""})
    return candidats


def _cle(question: str, candidats: list[dict]) -> str:
    empreinte = [question, MODEL_JUGEMENT, EFFORT_JUGEMENT, EXTRAIT_MAX, SYSTEM_JUGEMENT,
                 [(c["livre"], c["chunk"]) for c in candidats]]
    return hashlib.sha256(json.dumps(empreinte, ensure_ascii=False).encode("utf-8")).hexdigest()


def _charger() -> dict:
    return json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}


def _sauver(cache: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _juger(question: str, candidats: list[dict], label: str) -> list[int]:
    """Un appel Haiku medium : renvoie les indices des candidats qui répondent à la question."""
    extraits = [{"id": i, "texte": c["texte"][:EXTRAIT_MAX]} for i, c in enumerate(candidats)]
    user = json.dumps({"question": question, "extraits": extraits}, ensure_ascii=False, indent=2)
    derniere_erreur = ""
    for essai in range(1, MAX_ATTEMPTS + 1):
        meta: dict = {}
        try:
            raw = run_claude_code(SYSTEM_JUGEMENT, user, model=MODEL_JUGEMENT, timeout=TIMEOUT_S,
                                  meta=meta, effort=EFFORT_JUGEMENT)
        except ClaudeCodeCallError as exc:
            derniere_erreur = f"appel claude -p en échec : {exc}"
        else:
            log_usage(CACHE_DIR, "couverture-question", label, essai, meta)
            try:
                data = json.loads(strip_code_fence(raw))
                ids = [int(i) for i in data["pertinents"]]
                if all(0 <= i < len(candidats) for i in ids):
                    return sorted(set(ids))
                derniere_erreur = "identifiant hors liste"
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                derniere_erreur = f"réponse inexploitable : {exc}"
        print(f"  [couverture-question] essai {essai}/{MAX_ATTEMPTS} : {derniere_erreur}", file=sys.stderr)
    raise RuntimeError(derniere_erreur)


def couverture_question(question: str) -> dict | None:
    """Décompte des pdf et passages qui répondent à `question`.

    Renvoie {"livres": int, "passages": int, "candidats": int} ou None si le jugement a échoué
    (un avertissement est alors écrit sur stderr). `passages` est plafonné à TOP_N."""
    try:
        candidats = _candidats(question)
    except Exception as exc:  # base vectorielle absente ou illisible : on le signale, sans bloquer
        print(f"ATTENTION : recherche vectorielle impossible pour « {question} » : {exc}", file=sys.stderr)
        return None
    if not candidats:
        return {"livres": 0, "passages": 0, "candidats": 0}

    cache = _charger()
    cle = _cle(question, candidats)
    if cle in cache:
        log_cache_hit(CACHE_DIR, "couverture-question", question[:60])
        entree = cache[cle]
    else:
        try:
            retenus = _juger(question, candidats, question[:60])
        except RuntimeError as exc:
            print(f"ATTENTION : couverture non jugée pour « {question} » : {exc}", file=sys.stderr)
            return None
        entree = {
            "question": question,
            "livres": len({candidats[i]["livre"] for i in retenus}),
            "passages": len(retenus),
            "retenus": [f"{candidats[i]['livre']}::{candidats[i]['chunk']}" for i in retenus],
        }
        cache[cle] = entree
        _sauver(cache)
    return {"livres": entree["livres"], "passages": entree["passages"], "candidats": len(candidats)}
