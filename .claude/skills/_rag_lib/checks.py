"""Contrôles d'entrée et de sortie de chaque étape du pipeline RAG.

Une SEULE définition de "valide" par artefact : le contrôle de sortie de
l'étape N et le contrôle d'entrée de l'étape N+1 s'appuient sur les mêmes
fonctions de validation (`_validate_chunks_file`, `_pending_blocks`...), pour
ne jamais diverger. Tout est déterministe (aucun appel LLM), en lecture seule
sur les fichiers du document — seule exception : ouvrir Chroma/Kuzu pour
compter, ce qui ne modifie rien.

Convention de sortie des scripts qui utilisent ce module :
    0  OK
    1  erreur technique / prérequis non satisfait (contrôle d'ENTRÉE en échec)
    2  BLOQUANT (contrôle de SORTIE en échec)
    3  lot partiel (`--batch-size` de /rag-nottext, /rag-concepts, /rag-graphe ; pas une erreur)

Usage en ligne de commande (diagnostic, sans rien modifier) :
    python checks.py in|out <extraction|nottext|chunking|index|concepts|graphe> <pdf | document_id | dossier>
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import paths
import status as status_lib
from structural import is_structural_trail

# Seuils décidés avec l'utilisateur : au-delà de 5 % d'échecs, l'étape est
# bloquante (jamais un graphe/concept set "presque complet" présenté comme done).
MAX_FAILURE_RATE = 0.05
# Part de mentions sans "sense" (definition courte, voir concepts.py) au-dela de
# laquelle on avertit : un concepts.json ancien (extrait avant l'ajout du champ)
# ou un LLM qui l'omet fait retomber /rag-graphe sur une resolution par le nom seul.
MAX_MISSING_SENSE_RATE = 0.10

STATUS_KEYS = {
    "extraction": "extraction",
    "nottext": "nottext",
    "chunking": "chunking",
    "index": "indexation_vectorielle",
    "concepts": "extraction_concepts",
    "graphe": "graphe",
}

_CHUNK_REQUIRED_KEYS = {"index", "text", "heading_trail", "token_count", "has_code"}
_CHUNK_OPTIONAL_KEYS = {"embed_text", "has_formula", "has_image"}
_IMAGE_REF_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
_MAX_LISTED = 5


@dataclass
class CheckResult:
    stage: str
    phase: str  # "entree" | "sortie"
    blocking: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.blocking

    @property
    def verdict(self) -> str:
        return "ok" if self.ok else "bloquant"

    def detail(self) -> str:
        return "; ".join(self.blocking)

    def report(self) -> str:
        label = "entrée" if self.phase == "entree" else "sortie"
        head = f"Vérification de {label} — /rag-{self.stage} : {'OK' if self.ok else 'BLOQUANT'}"
        lines = [head]
        lines += [f"  - BLOQUANT : {reason}" for reason in self.blocking]
        lines += [f"  - note : {note}" for note in self.notes]
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Briques partagées
# --------------------------------------------------------------------------

def _load_json(path: Path):
    """(données, message d'erreur). Jamais d'exception : un JSON illisible
    est un résultat du contrôle, pas un crash."""
    if not path.exists():
        return None, f"{path.name} introuvable"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return None, f"{path.name} illisible ({exc})"


def _stage_entry(work_dir: Path, stage: str) -> dict:
    return status_lib.load_status(work_dir).get(STATUS_KEYS[stage], {})


def _require_done(work_dir: Path, stage: str, res: CheckResult) -> None:
    entry = _stage_entry(work_dir, stage)
    if entry.get("status") != "done":
        current = entry.get("status", "absente")
        res.blocking.append(f"étape amont /rag-{stage} non terminée (status.json : {current})")


def _require_claude_cli(res: CheckResult) -> None:
    if shutil.which("claude") is None:
        res.blocking.append("CLI `claude` introuvable dans le PATH (appels `claude -p` impossibles)")


def _require_writable(path: Path, res: CheckResult, label: str) -> None:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if not os.access(probe, os.W_OK):
        res.blocking.append(f"{label} non accessible en écriture ({probe})")


def _require_nonempty_text(path: Path, res: CheckResult) -> str | None:
    if not path.exists():
        res.blocking.append(f"{path.name} introuvable ({path})")
        return None
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        res.blocking.append(f"{path.name} vide")
        return None
    return text


def _validate_chunks_file(work_dir: Path, res: CheckResult) -> list[dict] | None:
    """Liste de chunks valide : JSON lisible, non vide, chaque entrée
    instanciable en `Chunk` (clés exactes), index uniques."""
    data, err = _load_json(work_dir / "chunks.json")
    if err:
        res.blocking.append(err)
        return None
    if not isinstance(data, list) or not data:
        res.blocking.append("chunks.json n'est pas une liste non vide")
        return None
    allowed = _CHUNK_REQUIRED_KEYS | _CHUNK_OPTIONAL_KEYS
    seen: set[int] = set()
    for pos, entry in enumerate(data):
        if not isinstance(entry, dict):
            res.blocking.append(f"chunks.json : entrée {pos} n'est pas un objet")
            return None
        keys = set(entry)
        if not _CHUNK_REQUIRED_KEYS <= keys or not keys <= allowed:
            res.blocking.append(
                f"chunks.json : entrée {pos} de forme invalide "
                f"(manquantes : {sorted(_CHUNK_REQUIRED_KEYS - keys)}, inconnues : {sorted(keys - allowed)})"
            )
            return None
        if entry["index"] in seen:
            res.blocking.append(f"chunks.json : index {entry['index']} dupliqué")
            return None
        seen.add(entry["index"])
    return data


def _structural_chunks(chunks: list[dict]) -> list[int]:
    """Index des chunks dont le fil d'ariane est une section structurelle
    (table des matières, index...) — voir `structural.py`."""
    return [c["index"] for c in chunks if is_structural_trail(c["heading_trail"])]


def _reject_structural_chunks(work_dir: Path, chunks: list[dict], res: CheckResult) -> None:
    """Contrôle d'ENTRÉE de /rag-index et /rag-concepts : un chunks.json
    produit avant l'exclusion des sections structurelles (ou avec
    --keep-structural non enregistré) ne doit pas atteindre l'aval. Une
    exclusion volontairement désactivée est lue dans status.json."""
    if _stage_entry(work_dir, "chunking").get("metadata", {}).get("keep_structural"):
        return
    found = _structural_chunks(chunks)
    if found:
        res.blocking.append(
            f"chunks.json contient {len(found)} chunk(s) de section(s) structurelle(s) "
            f"(table des matières...) : {found[:_MAX_LISTED]} — relancer /rag-chunking --force"
        )


def _note_missing_sense(entries: list[dict], res: "CheckResult") -> None:
    """Avertit (sans bloquer) si trop de mentions n'ont pas de `sense`."""
    mentions = [m for e in entries for m in e["mentions"]]
    if not mentions:
        return
    missing = sum(1 for m in mentions if not str(m.get("sense") or "").strip())
    rate = missing / len(mentions)
    res.metrics["missing_sense_rate"] = round(rate, 4)
    if rate > MAX_MISSING_SENSE_RATE:
        res.notes.append(
            f"{missing}/{len(mentions)} mention(s) sans définition ('sense', {rate:.0%} > {MAX_MISSING_SENSE_RATE:.0%}) — "
            "concepts.json extrait avec l'ancien prompt ou définition omise par le LLM : /rag-graphe résoudra ces "
            "mentions sur le nom seul (homonymes non distingués). Relancer /rag-concepts --reset pour les regénérer."
        )


def _validate_concepts_file(work_dir: Path, res: CheckResult) -> list[dict] | None:
    data, err = _load_json(work_dir / "concepts.json")
    if err:
        res.blocking.append(err)
        return None
    if not isinstance(data, list):
        res.blocking.append("concepts.json n'est pas une liste")
        return None
    for pos, entry in enumerate(data):
        ok = (
            isinstance(entry, dict)
            and isinstance(entry.get("chunk_index"), int)
            and isinstance(entry.get("mentions"), list)
            and all(
                isinstance(m, dict) and {"name", "canonical_form", "type"} <= set(m)
                for m in entry["mentions"]
            )
        )
        if not ok:
            res.blocking.append(f"concepts.json : entrée {pos} de forme invalide")
            return None
    return data


def _document_id(work_dir: Path) -> str:
    data, _ = _load_json(work_dir / "meta.json")
    if isinstance(data, dict) and data.get("document_id"):
        return data["document_id"]
    return work_dir.name


def _missing_image_refs(pivot_text: str, work_dir: Path) -> list[str]:
    missing = []
    for ref in _IMAGE_REF_RE.findall(pivot_text):
        if "://" in ref:
            continue
        if not (work_dir / ref).exists():
            missing.append(ref)
    return missing


def _pending_blocks(pivot_text: str) -> list:
    """Blocs image/code/formule sans description `/rag-nottext` juste après —
    même logique que `rag-nottext/scripts/run.py:_find_pending_blocks` et que
    `chunk.py:_merge_description_blocks` (importé, jamais réimplémenté avec
    un parseur différent)."""
    import chunk as chunk_mod

    blocks = chunk_mod.split_into_blocks(pivot_text)
    pending = []
    for i, block in enumerate(blocks):
        if block.kind not in ("image", "code", "formula"):
            continue
        following = blocks[i + 1] if i + 1 < len(blocks) else None
        if following is not None and following.kind == "prose" and following.text.startswith(chunk_mod.DESCRIPTION_MARKER):
            continue
        pending.append(block)
    return pending


def _indexable(chunks: list[dict]) -> list[dict]:
    """Même filtre exact que `rag-index/scripts/run.py`."""
    from quality import is_noise_text

    return [c for c in chunks if c["has_code"] or c.get("has_formula", False) or not is_noise_text(c["text"])]


def _extraction_blocking_counts(work_dir: Path) -> tuple[int, int]:
    meta = _stage_entry(work_dir, "extraction").get("metadata", {})
    return int(meta.get("quality_blocking_issues") or 0), int(meta.get("fidelity_blocking_issues") or 0)


def _require_extraction_clean(work_dir: Path, res: CheckResult) -> None:
    quality, fidelity = _extraction_blocking_counts(work_dir)
    if quality:
        res.blocking.append(f"/rag-extraction a laissé {quality} problème(s) qualité BLOQUANT(S)")
    if fidelity:
        res.blocking.append(f"/rag-extraction a laissé {fidelity} problème(s) de fidélité BLOQUANT(S)")


# --------------------------------------------------------------------------
# Contrôles d'ENTRÉE
# --------------------------------------------------------------------------

def check_extraction_input(pdf_path: Path) -> CheckResult:
    res = CheckResult("extraction", "entree")
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file():
        res.blocking.append(f"PDF condensé introuvable : {pdf_path}")
        return res
    if pdf_path.stat().st_size == 0:
        res.blocking.append(f"PDF condensé vide : {pdf_path}")
        return res
    try:
        import pymupdf

        with pymupdf.open(str(pdf_path)) as doc:
            if doc.page_count < 1:
                res.blocking.append("PDF condensé sans aucune page")
    except Exception as exc:  # PDF corrompu, PyMuPDF absent...
        res.blocking.append(f"PDF condensé illisible par PyMuPDF ({type(exc).__name__}: {exc})")
    try:
        import spellchecker  # noqa: F401  (dictionnaire français hors ligne)
    except ImportError:
        res.blocking.append("module `pyspellchecker` indisponible (réparation des ligatures/césures impossible)")
    _require_writable(paths.WORK_DIR, res, "rag_data/work/")
    return res


def check_input(stage: str, work_dir: Path) -> CheckResult:
    work_dir = Path(work_dir)
    res = CheckResult(stage, "entree")

    if stage == "nottext":
        text = _require_nonempty_text(work_dir / "pivot.md", res)
        _require_done(work_dir, "extraction", res)
        _require_extraction_clean(work_dir, res)
        if text is not None:
            missing = _missing_image_refs(text, work_dir)
            if missing:
                res.blocking.append(f"{len(missing)} image(s) référencée(s) par pivot.md absente(s) de images/ : {missing[:_MAX_LISTED]}")
        _require_claude_cli(res)

    elif stage == "chunking":
        text = _require_nonempty_text(work_dir / "pivot.md", res)
        _require_done(work_dir, "nottext", res)
        _require_extraction_clean(work_dir, res)
        if _stage_entry(work_dir, "nottext").get("metadata", {}).get("n_errors"):
            res.blocking.append("/rag-nottext a des éléments en échec (n_errors > 0)")
        if text is not None:
            try:
                pending = _pending_blocks(text)
            except Exception as exc:  # tokenizer bge-m3 non chargeable, etc.
                res.blocking.append(f"analyse de pivot.md impossible ({type(exc).__name__}: {exc})")
            else:
                if pending:
                    kinds: dict[str, int] = {}
                    for b in pending:
                        kinds[b.kind] = kinds.get(b.kind, 0) + 1
                    res.blocking.append(
                        f"{len(pending)} bloc(s) non-textuel(s) sans description /rag-nottext "
                        f"({', '.join(f'{n} {k}' for k, n in sorted(kinds.items()))}) — "
                        "leur texte brut dégraderait l'embedding"
                    )

    elif stage == "index":
        chunks = _validate_chunks_file(work_dir, res)
        if chunks is not None:
            _reject_structural_chunks(work_dir, chunks, res)
        _require_done(work_dir, "chunking", res)
        meta, _ = _load_json(work_dir / "meta.json")
        if isinstance(meta, dict) and meta.get("document_id") and meta["document_id"] != work_dir.name:
            res.blocking.append(f"meta.json : document_id {meta['document_id']!r} different du nom du dossier {work_dir.name!r}")
        _require_writable(paths.VECTOR_DB_PATH, res, "rag_data/db/vector/")
        if chunks is not None and not _indexable(chunks):
            res.blocking.append("aucun chunk indexable (tous classés comme bruit)")

    elif stage == "concepts":
        chunks = _validate_chunks_file(work_dir, res)
        if chunks is not None:
            _reject_structural_chunks(work_dir, chunks, res)
        _require_done(work_dir, "chunking", res)
        _require_claude_cli(res)

    elif stage == "graphe":
        data = _validate_concepts_file(work_dir, res)
        _require_done(work_dir, "concepts", res)
        if data is not None and sum(len(e["mentions"]) for e in data) == 0:
            res.blocking.append("concepts.json ne contient aucune mention")
        if data is not None:
            _note_missing_sense(data, res)
        _require_writable(paths.GRAPH_DB_PATH, res, "rag_data/db/graph/")
        _require_claude_cli(res)

    else:
        raise ValueError(f"étape inconnue pour un contrôle d'entrée : {stage!r}")
    return res


# --------------------------------------------------------------------------
# Contrôles de SORTIE
# --------------------------------------------------------------------------

def check_output(stage: str, work_dir: Path, **kw) -> CheckResult:
    """`kw` : compteurs de l'exécution en cours quand `status.json` n'est pas
    encore écrit (`quality_blocking`/`fidelity_blocking` pour extraction,
    `n_mentions`/`n_skipped` et `graph` pour graphe, `deep` pour index)."""
    work_dir = Path(work_dir)
    res = CheckResult(stage, "sortie")

    if stage == "extraction":
        _require_nonempty_text(work_dir / "pivot.md", res)
        meta, err = _load_json(work_dir / "meta.json")
        if err:
            res.blocking.append(err)
        elif not isinstance(meta, dict) or meta.get("document_id") != work_dir.name:
            res.blocking.append("meta.json : document_id absent ou différent du dossier de travail")
        if "quality_blocking" in kw or "fidelity_blocking" in kw:
            quality, fidelity = int(kw.get("quality_blocking", 0)), int(kw.get("fidelity_blocking", 0))
            if quality:
                res.blocking.append(f"{quality} problème(s) qualité BLOQUANT(S) (voir rapport)")
            if fidelity:
                res.blocking.append(f"{fidelity} problème(s) de fidélité BLOQUANT(S) (voir rapport)")
        else:
            _require_extraction_clean(work_dir, res)

    elif stage == "nottext":
        text = _require_nonempty_text(work_dir / "pivot.md", res)
        entries, err = _load_json(work_dir / "nottext_meta.json")
        if err:
            res.blocking.append(err)
            entries = []
        elif not isinstance(entries, list):
            res.blocking.append("nottext_meta.json n'est pas une liste")
            entries = []
        ids = [e.get("identifiant") for e in entries]
        if len(ids) != len(set(ids)):
            res.blocking.append("nottext_meta.json : identifiants dupliqués")
        failed = [e for e in entries if e.get("erreur")]
        if failed:
            res.blocking.append(f"{len(failed)} élément(s) non-textuel(s) en échec")
        if text is not None:
            try:
                pending = _pending_blocks(text)
            except Exception as exc:
                res.blocking.append(f"analyse de pivot.md impossible ({type(exc).__name__}: {exc})")
            else:
                if pending:
                    res.blocking.append(f"{len(pending)} bloc(s) non-textuel(s) sans description dans pivot.md")
            missing = _missing_image_refs(text, work_dir)
            if missing:
                res.blocking.append(f"{len(missing)} image(s) référencée(s) absente(s) de images/ : {missing[:_MAX_LISTED]}")
        res.metrics["n_entries"] = len(entries)

    elif stage == "chunking":
        chunks = _validate_chunks_file(work_dir, res)
        if chunks is not None:
            # Compteur de l'exécution en cours si fourni : status.json contient
            # encore ceux d'un run précédent tant que mark_stage n'a pas eu lieu.
            entry = _stage_entry(work_dir, "chunking")
            expected = kw.get("n_chunks")
            if expected is None and entry.get("status") == "done":
                expected = entry.get("metadata", {}).get("n_chunks")
            if expected is not None and expected != len(chunks):
                res.blocking.append(f"status.json annonce {expected} chunks, chunks.json en contient {len(chunks)}")
            broken = [
                c["index"] for c in chunks
                if sum(1 for line in c["text"].split("\n") if line.strip().startswith("```")) % 2
            ]
            if broken:
                res.blocking.append(f"bloc(s) de code coupé(s)/non refermé(s) dans les chunks {broken[:_MAX_LISTED]}")
            keep_structural = kw.get("keep_structural")
            if keep_structural is None:  # appel hors run.py (CLI) : lit le choix enregistré
                keep_structural = _stage_entry(work_dir, "chunking").get("metadata", {}).get("keep_structural", False)
            if not keep_structural:
                structural = _structural_chunks(chunks)
                if structural:
                    res.blocking.append(
                        f"{len(structural)} chunk(s) issu(s) d'une section structurelle "
                        f"(table des matières, index...) : {structural[:_MAX_LISTED]}"
                    )
                # Indicatif : une table des matières qui ne serait PAS sous un
                # titre reconnu (points de conduite ". . ." en nombre) échappe
                # à l'exclusion par titre.
                leaders = [c["index"] for c in chunks if c["text"].count(". . . .") >= 3 and c["index"] not in structural]
                if leaders:
                    res.notes.append(
                        f"{len(leaders)} chunk(s) avec des points de conduite (table des matières probable hors "
                        f"section reconnue) : {leaders[:_MAX_LISTED]}"
                    )
            mean = sum(c["token_count"] for c in chunks) / len(chunks)
            res.metrics["mean_tokens"] = round(mean)
            if mean < 50:
                res.notes.append(f"tokens moyens très bas ({mean:.0f}) — découpage suspect")
            empty_trail = sum(1 for c in chunks if not c["heading_trail"])
            if empty_trail / len(chunks) > 0.5:
                res.notes.append(
                    f"fil d'ariane vide pour {empty_trail}/{len(chunks)} chunks — titres probablement non détectés à l'extraction"
                )

    elif stage == "index":
        _check_index_output(work_dir, res, deep=bool(kw.get("deep", True)), n_indexed=kw.get("n_indexed"))

    elif stage == "concepts":
        chunks = _validate_chunks_file(work_dir, res)
        results = _validate_concepts_file(work_dir, res)
        if chunks is not None and results is not None:
            from quality import is_noise_text

            expected = [c["index"] for c in chunks if not is_noise_text(c["text"])]
            n_noise = len(chunks) - len(expected)
            done = {r["chunk_index"] for r in results}
            failed = [i for i in expected if i not in done]
            rate = len(failed) / len(expected) if expected else 0.0
            n_mentions = sum(len(r["mentions"]) for r in results)
            res.metrics.update(n_noise=n_noise, n_failed=len(failed), n_mentions=n_mentions, failure_rate=round(rate, 4))
            if n_mentions == 0:
                res.blocking.append("aucun concept extrait")
            _note_missing_sense(results, res)
            if rate > MAX_FAILURE_RATE:
                res.blocking.append(
                    f"{len(failed)}/{len(expected)} chunks sans concepts ({rate:.1%} > seuil {MAX_FAILURE_RATE:.0%}) "
                    f"— relancer la même commande retente les chunks en échec"
                )
            elif failed:
                res.notes.append(f"{len(failed)}/{len(expected)} chunks sans concepts ({rate:.1%}, sous le seuil {MAX_FAILURE_RATE:.0%})")

    elif stage == "graphe":
        _check_graphe_output(work_dir, res, kw)

    else:
        raise ValueError(f"étape inconnue pour un contrôle de sortie : {stage!r}")
    return res


def _check_index_output(work_dir: Path, res: CheckResult, *, deep: bool, n_indexed: int | None = None) -> None:
    chunks = _validate_chunks_file(work_dir, res)
    if chunks is None:
        return
    try:
        indexable = _indexable(chunks)
        import chunk as chunk_mod
        import vector_store

        document_id = _document_id(work_dir)
        collection = vector_store.get_collection(paths.VECTOR_DB_PATH)
        stored = collection.get(where={"document_id": document_id}, include=[])["ids"]
    except Exception as exc:
        res.blocking.append(f"base vectorielle inaccessible ({type(exc).__name__}: {exc})")
        return

    res.metrics.update(expected=len(indexable), in_db=len(stored))
    if len(stored) != len(indexable):
        res.blocking.append(f"{len(stored)} chunk(s) dans Chroma pour ce document, {len(indexable)} attendu(s) (comptage exact exigé)")
        return
    if n_indexed is None:
        n_indexed = _stage_entry(work_dir, "index").get("metadata", {}).get("n_indexed")
    if n_indexed is not None and n_indexed != len(indexable):
        res.blocking.append(f"status.json annonce {n_indexed} chunk(s) indexé(s), {len(indexable)} attendu(s)")

    if deep and indexable:
        try:
            probe = min(indexable, key=lambda c: c["index"])
            embedding = vector_store.embed_texts([vector_store.embedding_text(chunk_mod.Chunk(**probe))])[0]
            hit = collection.query(query_embeddings=[embedding], n_results=1, where={"document_id": document_id})
            found = hit["ids"][0][0] if hit["ids"] and hit["ids"][0] else None
        except Exception as exc:
            res.blocking.append(f"requête de test impossible ({type(exc).__name__}: {exc})")
            return
        expected_id = f"{document_id}::{probe['index']}"
        if found != expected_id:
            res.blocking.append(f"requête de test : le chunk {expected_id} n'est pas retrouvé (obtenu : {found})")
        else:
            res.notes.append(f"requête de test : le chunk {probe['index']} est retrouvé en premier")


def _check_graphe_output(work_dir: Path, res: CheckResult, kw: dict) -> None:
    entries = _validate_concepts_file(work_dir, res)
    if entries is None:
        return
    meta = _stage_entry(work_dir, "graphe").get("metadata", {})
    n_mentions = kw.get("n_mentions", meta.get("n_mentions"))
    n_skipped = kw.get("n_skipped", meta.get("n_skipped"))
    if n_mentions is None or n_skipped is None:
        res.blocking.append("compteurs n_mentions/n_skipped introuvables (status.json)")
        return

    total = sum(len(e["mentions"]) for e in entries)
    res.metrics.update(total_mentions=total, n_mentions=n_mentions, n_skipped=n_skipped)
    if n_mentions + n_skipped != total:
        res.blocking.append(f"comptes incohérents : {n_mentions} liée(s) + {n_skipped} ignorée(s) au lieu de {total} mention(s) dans concepts.json")
    if n_mentions == 0:
        res.blocking.append("aucune mention liée au graphe")
    rate = n_skipped / total if total else 0.0
    if rate > MAX_FAILURE_RATE:
        res.blocking.append(
            f"{n_skipped}/{total} mention(s) ignorée(s) ({rate:.1%} > seuil {MAX_FAILURE_RATE:.0%}) "
            "— relancer la même commande retente les chunks concernés"
        )
    elif n_skipped:
        res.notes.append(f"{n_skipped}/{total} mention(s) ignorée(s) ({rate:.1%}, sous le seuil {MAX_FAILURE_RATE:.0%})")

    try:
        graph = kw.get("graph")
        if graph is None:
            from graph_store import ConceptGraph

            graph = ConceptGraph(paths.GRAPH_DB_PATH)
        document_id = _document_id(work_dir)
        in_graph = graph.count_chunks(document_id)
        links = graph.count_mentions(document_id)
    except Exception as exc:
        res.blocking.append(f"graphe inaccessible ({type(exc).__name__}: {exc})")
        return
    res.metrics.update(chunks_in_graph=in_graph, links_in_graph=links)
    if in_graph != len(entries):
        res.blocking.append(f"{in_graph} chunk(s) de ce document dans le graphe, {len(entries)} attendu(s)")
    if n_mentions and links == 0:
        res.blocking.append("aucun lien MENTIONS dans le graphe pour ce document")


# --------------------------------------------------------------------------
# Ligne de commande (diagnostic)
# --------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[1] not in ("in", "out") or argv[2] not in STATUS_KEYS:
        print(__doc__, file=sys.stderr)
        return 1
    phase, stage, raw = argv[1], argv[2], argv[3]
    if phase == "in" and stage == "extraction":
        res = check_extraction_input(Path(raw))
    else:
        try:
            work_dir = paths.resolve_work_dir(raw)
        except ValueError as exc:
            print(f"ERREUR: {exc}", file=sys.stderr)
            return 1
        res = check_input(stage, work_dir) if phase == "in" else check_output(stage, work_dir)
    print(res.report())
    return 0 if res.ok else (1 if phase == "in" else 2)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
