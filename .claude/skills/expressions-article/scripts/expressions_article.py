"""Étape de /expressions-article : quelles expressions d'un article méritent un NOUVEL article ?

Pour chaque article de rag_data/content/work/<slug>/article.md :
  1. extraction LLM des expressions techniques, qualifiées par le contexte de l'article
     qualifiées par le contexte de l'article si besoin, vérifiées mot pour mot ;
  2. jugement LLM : déjà traitée par un article du catalogue, nouveau sujet, simple mention,
     ou ambiguë. Le LLM lit le texte et le catalogue des articles publiés ; le graphe de
     concepts n'est qu'une indication consultative (type, sens, nombre de pdf) ;
  3. vérifications déterministes : la qualification est ancrée dans l'article, l'article
     « déjà traité » existe, un passage du corpus nomme la notion (sinon « à vérifier »).

Écrit, par article, rag_data/expressions/work/<slug>/ : expressions.md, expressions.json,
usage.jsonl, totaltoken.jsonl et cache/llm.json. Met aussi à jour rag_data/expressions/INDEX.md
(une ligne par article) et rag_data/expressions/EXPRESSIONS.md (une ligne par nouveau sujet à rédiger).
Ne modifie jamais un article, ni la base graphe, ni rag_data/content/work/.

Usage :
    expressions_article.py <slug|numéro> | --all [--budget 540] [--refaire]
  sans argument : liste numérotée des articles publiés sans rapport (code 1, rien n'est lancé).
  --all = tous ces articles non traités ; un numéro désigne la même liste.
  --refaire : liste et --all portent sur TOUS les articles publiés (réanalyse, cache LLM ignoré),
  pour relancer l'analyse après l'ajout d'un pdf au RAG.
Codes de sortie : 0 terminé, 1 usage, 2 erreur (graphe absent, appel LLM en échec),
3 temps écoulé (relancer la même commande : le cache reprend là où on s'est arrêté).
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(next(p for p in _Path(__file__).resolve().parents if (p / ".claude" / "skills" / "_rag_lib").is_dir()) / ".claude" / "skills" / "_rag_lib"))
import venv_guard  # noqa: F401

import argparse
import json
import sys
import time
from datetime import datetime, timezone

import paths
from inc_articles import catalogue, fold, is_published, list_slugs, load_all, load_article
from inc_couverture import ChunkIndex, coverage
from inc_extraction import (  # noqa: E402
    JUDGEMENT_SYSTEM,
    LlmStepError,
    article_fingerprint,
    cache_key,
    dossier_expression,
    extract_candidates,
    judge_candidates,
    load_cache,
    save_cache,
)
from inc_graphe import GraphIndex, Lookup
from inc_usage import ecrire_totaux, log_cache_hit, rendre_totaux  # noqa: E402
from inc_indexfileupdate import mettre_a_jour_index  # noqa: E402
from inc_expressionsfileupdate import mettre_a_jour_expressions  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


class BudgetExceeded(RuntimeError):
    """Budget de temps de l'invocation épuisé : arrêt propre, cache à jour."""


class Clock:
    def __init__(self, budget_s: float):
        self._start = time.monotonic()
        self._budget = budget_s

    def check(self) -> None:
        if time.monotonic() - self._start > self._budget:
            raise BudgetExceeded()


def _evidence(candidate: dict, lk: Lookup) -> dict:
    """Ce que le LLM voit du graphe : une indication, jamais une décision."""
    if not lk.trouve:
        graphe = "absent du graphe (indication seulement : la notion peut être nommée autrement)"
    else:
        graphe = {
            "type": lk.type, "sens": lk.sens, "livres": lk.documents, "passages": lk.mentions,
            "concepts_de_meme_forme": lk.homonymes,
        }
    return {
        "expression": candidate["expression"],
        "forme_qualifiee": candidate["qualifiee"],
        "passage": candidate["passage"],
        "graphe": graphe,
    }


def process_article(slug: str, index: GraphIndex, chunks: ChunkIndex, articles, publies, args, clock: Clock) -> dict:
    article = load_article(slug)
    dossier = dossier_expression(slug)
    cache_path = dossier / "cache" / "llm.json"
    fingerprint = article_fingerprint(article)
    cache = load_cache(cache_path)
    if cache.get("fingerprint") != fingerprint:
        cache = {"fingerprint": fingerprint}

    if "candidats" not in cache or args.refaire:
        clock.check()
        cache["candidats"] = extract_candidates(article)
        save_cache(cache_path, cache)
    else:
        log_cache_hit(dossier, "extraction", slug)
    candidats = cache["candidats"]

    lookups = {c["expression"]: index.lookup(c["qualifiee"]) for c in candidats}
    evidence = [_evidence(c, lookups[c["expression"]]) for c in candidats]
    key = cache_key(json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                    JUDGEMENT_SYSTEM, catalogue(publies))
    if candidats:
        if cache.get("jugement", {}).get("key") == key and not args.refaire:
            verdicts = cache["jugement"]["verdicts"]
            log_cache_hit(dossier, "jugement", slug)
        else:
            clock.check()
            verdicts = judge_candidates(article, publies, evidence)
            cache["jugement"] = {"key": key, "verdicts": verdicts}
            save_cache(cache_path, cache)
    else:
        verdicts = {}

    # L'article source ne compte jamais comme article dédié à la notion qu'il traite dans une section.
    for verdict in verdicts.values():
        if verdict["decision"] == "deja_traite" and verdict["article_existant"] == slug:
            verdict.update(decision="nouveau", article_existant=None,
                           note="citation de l'article source requalifiée en nouveau sujet")

    rows = []
    for c in candidats:
        lk = lookups[c["expression"]]
        cov = coverage(c["qualifiee"], articles, chunks)
        # Une forme qualifiée peut ne jamais apparaître mot pour mot dans les pdf alors que
        # la notion d'origine y est : on prend le meilleur des deux comptages.
        passages_forme = max(cov.chunks, coverage(c["expression"], articles, chunks).chunks)
        row = {
            "expression": c["expression"],
            "forme_qualifiee": c["qualifiee"],
            "passage": c["passage"],
            "passage_verifie": c["passage_verifie"],
            "graphe": {
                "trouve": lk.trouve, "forme": lk.forme, "type": lk.type, "sens": lk.sens,
                "livres": lk.documents, "passages": lk.mentions, "homonymes": lk.homonymes,
                "proches": lk.proches,
            },
            "lexical": {"articles_proches": cov.traite_par, "mentionne_dans": cov.mentionne_dans,
                        "passages": passages_forme, "livres": cov.livres},
        }
        verdict = verdicts.get(fold(c["expression"]))
        _classify(row, verdict)
        rows.append(row)
    return {"slug": slug, "titre": article.title, "rows": rows}


def _classify(row: dict, verdict: dict | None) -> None:
    """Catégorie finale : la décision du LLM, confirmée ou nuancée par les vérifications déterministes."""
    if verdict is None:
        row.update(categorie="a_verifier", motif="le LLM n'a pas tranché cette expression",
                   raison="", titre_question=None, article_existant=None)
        return
    row.update(raison=verdict["raison"], titre_question=verdict["titre_question"],
               article_existant=verdict["article_existant"])
    decision = verdict["decision"]
    if decision == "secondaire":
        row.update(categorie="secondaire", motif="mention secondaire selon le LLM")
    elif decision == "ambigu":
        row.update(categorie="ambigu", motif=verdict["note"] or "contexte insuffisant pour identifier la notion")
    elif decision == "deja_traite":
        row.update(categorie="deja_traite", motif=f"article dédié existant : {verdict['article_existant']}")
    else:  # nouveau
        problemes = []
        if row["lexical"]["articles_proches"]:
            problemes.append("un article au titre ou au slug proche existe : " + ", ".join(row["lexical"]["articles_proches"]))
        if row["lexical"]["passages"] == 0 and not row["graphe"]["trouve"]:
            problemes.append("aucun passage ni concept ne nomme la notion dans le corpus")
        if not verdict["titre_question"]:
            problemes.append("pas de titre de question proposé")
        if problemes:
            row.update(categorie="a_verifier", motif=" ; ".join(problemes))
        else:
            row.update(categorie="nouveau", motif="aucun article dédié ; le corpus peut l'expliquer")


def _graph_line(r: dict) -> str:
    g = r["graphe"]
    if not g["trouve"]:
        return "graphe : absent (indication)"
    ambigu = f", {g['homonymes']} concepts de même forme" if g["homonymes"] > 1 else ""
    return f"graphe : {g['type']}, {g['livres']} livre(s), {g['passages']} passage(s){ambigu}"


def render_markdown(result: dict, generated_at: str) -> str:
    rows = result["rows"]
    out = [f"# Expressions à traiter : {result['titre']}", ""]
    out.append(f"Source : `rag_data/content/work/{result['slug']}/article.md` · généré le {generated_at}")
    out.append("")
    out.append(f"{len(rows)} expression(s) repérée(s). Aucune commande n'est lancée automatiquement.")
    out.append("")

    def section(title: str, categorie: str, detail) -> None:
        selected = [r for r in rows if r["categorie"] == categorie]
        out.append(f"## {title} ({len(selected)})")
        out.append("")
        if not selected:
            out.append("Aucune.")
            out.append("")
        for r in selected:
            out.extend(detail(r))
        out.append("")

    def nouveau(r: dict) -> list[str]:
        lines = [f"- **{r['forme_qualifiee']}** — {_graph_line(r)}"]
        lines.append(f"  - pourquoi : {r['raison']}")
        lines.append(f"  - passage : « {r['passage']} »{'' if r['passage_verifie'] else ' (non vérifié mot pour mot)'}")
        lines.append(f"  - commande : `/create-content \"{r['titre_question']}\"`")
        return lines

    def deja(r: dict) -> list[str]:
        return [f"- **{r['forme_qualifiee']}** — {r['motif']}"]

    def a_verifier(r: dict) -> list[str]:
        return [f"- **{r['forme_qualifiee']}** — {r['motif']}"]

    def ambigu(r: dict) -> list[str]:
        return [f"- **{r['expression']}** — {r['motif']}"]

    section("Nouveaux sujets à rédiger", "nouveau", nouveau)
    section("Déjà traitées par un article", "deja_traite", deja)
    section("À vérifier", "a_verifier", a_verifier)
    section("Ambiguës (non proposées)", "ambigu", ambigu)
    return "\n".join(out)


def has_report(slug: str) -> bool:
    return (dossier_expression(slug) / "expressions.md").is_file()


def list_pending(refaire: bool = False) -> list[str]:
    """Articles publiés sans rapport d'expression ; avec refaire, tous les articles publiés (réanalyse)."""
    return [slug for slug in list_slugs()
            if is_published(slug) and (refaire or not has_report(slug))]


def message_rien_a_traiter() -> str:
    publies = [slug for slug in list_slugs() if is_published(slug)]
    avec_rapport = [slug for slug in publies if (dossier_expression(slug) / "expressions.md").is_file()]
    return (f"Aucun article publié n’est sans rapport d'expressions "
            f"({len(publies)} publiés, {len(avec_rapport)} avec rapport).")


def list_numbered(refaire: bool = False) -> None:
    slugs = list_pending(refaire)
    if not slugs:
        print(message_rien_a_traiter())
        return
    if refaire:
        print("Articles publiés, à réanalyser (numéro, puis slug ; « rapport existant » = déjà analysé) :\n")
        for n, slug in enumerate(slugs, start=1):
            marque = " — rapport existant" if has_report(slug) else ""
            print(f"{n}. {slug}{marque}")
        print("\nTous les articles publiés : --all --refaire")
        return
    print("Articles publiés sans rapport d'expression (numéro, puis slug) :\n")
    for n, slug in enumerate(slugs, start=1):
        print(f"{n}. {slug}")
    print("\nTous les articles non traités : --all")


def resolve_targets(target: str, refaire: bool = False) -> list[str] | None:
    pending = list_pending(refaire)
    if target == "--all":
        return pending
    if target.isdigit() and 1 <= int(target) <= len(pending):
        return [pending[int(target) - 1]]
    if target in list_slugs():  # un slug explicite reste accepté, même hors liste
        return [target]
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", nargs="?", help="slug, numéro ou --all")
    ap.add_argument("--budget", type=float, default=540, help="secondes avant arrêt propre (défaut 540)")
    ap.add_argument("--refaire", action="store_true", help="ignore le cache des appels LLM")
    ap.add_argument("--all", action="store_true", help="tous les articles non traités")
    args = ap.parse_args()
    if args.all:
        args.target = "--all"

    if not args.target:
        list_numbered(args.refaire)
        return 1
    targets = resolve_targets(args.target, args.refaire)
    if targets is None:
        print(f"Article inconnu : {args.target}\n")
        list_numbered(args.refaire)
        return 1
    if not targets:
        print(message_rien_a_traiter())
        return 0

    graph_path = paths.GRAPH_DB_PATH
    if not graph_path.exists():
        print(f"ERREUR : graphe introuvable ({graph_path}) : lancer /rag-graphe d'abord.", file=sys.stderr)
        return 2

    clock = Clock(args.budget)
    index = GraphIndex(graph_path)
    chunks = ChunkIndex()
    articles = load_all()
    publies = [a for a in articles if is_published(a.slug)]
    generated_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    code = 0
    totaux: list[dict] = []
    try:
        for n, slug in enumerate(targets, start=1):
            print(f"[{n}/{len(targets)}] {slug} : en cours…", file=sys.stderr, flush=True)
            debut = time.monotonic()
            dossier = dossier_expression(slug)
            try:
                try:
                    result = process_article(slug, index, chunks, articles, publies, args, clock)
                except LlmStepError as exc:
                    print(f"ERREUR sur {slug} : {exc}", file=sys.stderr, flush=True)
                    code = 2
                    continue
                dossier.mkdir(parents=True, exist_ok=True)
                (dossier / "expressions.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                report = render_markdown(result, generated_at)
                (dossier / "expressions.md").write_text(report, encoding="utf-8")
                mettre_a_jour_index(result, generated_at)
                mettre_a_jour_expressions(result, generated_at)
                print(report, flush=True)
                print(f"[{n}/{len(targets)}] {slug} : terminé en {time.monotonic() - debut:.0f} s", file=sys.stderr, flush=True)
                print(flush=True)
            finally:
                # Même en cas d'erreur ou de budget épuisé : les appels déjà faits sont comptés.
                totaux.append(ecrire_totaux(dossier, slug))
    except BudgetExceeded:
        print(f"Temps écoulé après {args.budget:.0f} s : progression sauvegardée. Relancer la même commande.",
              file=sys.stderr)
        code = 3
    finally:
        index.close()
    for total in totaux:
        print(rendre_totaux(total))
        print()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
