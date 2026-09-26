"""Étape 2 de /rag-integrity : juge, via `claude -p`, les items ambigus repérés
par report.py (rapport_graphe_<ts>.json -> rapport_graphe_<ts>.verdicts.json).

report.py est entièrement déterministe (aucun appel LLM) : il repère les
concepts hubs, les fusions inter-livres, les paires proches jamais fusionnées,
etc., et fournit pour chacun un contexte factuel (nom, type, alias, extraits
de chunks RÉELS). CE script est le seul endroit où Claude intervient dans
/rag-integrity : un appel `claude -p` headless par item, jamais sur la seule
base du nom du concept mais toujours avec les extraits comme preuve.

Usage:
    python audit_run.py [rapport_graphe_<ts>.json] [--batch-size 10] [--force] [--model NAME]

Sans argument, prend le rapport le plus récent sous rag_data/audit/.

Écrit <rapport>.verdicts.json : {"item_id": {"verdict", "reason"}}, mis à jour
après CHAQUE item (comme concepts.json pour /rag-concepts) : une interruption
ne perd jamais plus d'un item. À la fin (plus aucun item en attente), écrit
aussi <rapport>_audit.md : synthèse des verdicts, comptée par catégorie.

Audit incrémental entre rapports (sauf --force) : avant de juger quoi que ce
soit, reprend automatiquement le verdict d'un rapport précédent pour tout
item portant le MÊME id ET le MÊME contexte (mêmes extraits/alias) — sans
rejuger un item déjà tranché à chaque nouveau rapport, même après l'ajout
d'un livre qui ne le concerne pas. Voir _carry_forward_verdicts().
"""
from __future__ import annotations
# Garde-fou : force l'interpreteur du projet (.venv-rag), voir _rag_lib/venv_guard.py
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(next(p for p in _Path(__file__).resolve().parents if (p / ".claude" / "skills" / "_rag_lib").is_dir()) / ".claude" / "skills" / "_rag_lib"))
import venv_guard  # noqa: F401

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_rag_lib"))

from _claude_code_client import ClaudeCodeCallError, MAX_FORMAT_RETRIES, run_claude_code, strip_code_fence
import paths
import decisions

# batch_size (6, voir audit_lot.py) x 2 appels x DEFAULT_TIMEOUT ne doit jamais
# approcher les 10 minutes apres lesquelles l'application passe une commande
# foreground en arriere-plan (interdit par ce pipeline) : un item signale coute
# DEUX appels (jugement puis confirmation, voir judge_item), donc 6 x 2 x 45s =
# 540s = 9min, dans le pire cas absolu (chaque appel va au bout de son timeout)
# -- le cas normal mesure (~5-6s/appel, ~15 % d'items signales) reste tres en dessous.
DEFAULT_TIMEOUT = 45

# Une entrée par catégorie du rapport (voir report.py: audit_items[].category) :
# les valeurs de verdict autorisées et l'instruction spécifique à donner à
# Claude. Le "sens" du verdict change avec la catégorie (2.1 juge une fusion
# DÉJÀ faite, 2.3 juge une fusion JAMAIS faite) : jamais un enum partagé.
CATEGORIES: dict[str, dict] = {
    "1.3": {
        "verdicts": ["specifique", "generique"],
        "instructions": (
            "Ce concept est un HUB DANS UN LIVRE PRÉCIS (indiqué dans le sujet et dans le contexte, "
            "\"livre\") : il y est mentionné dans une grande part de ses chunks (\"part_dans_ce_livre\", "
            "\"chunks_du_concept_dans_ce_livre\" sur \"chunks_total_du_livre\"). Le même concept peut être "
            "jugé différemment dans un autre livre (item séparé) : ne juge QUE ce livre-là, jamais le "
            "concept en général. Juge s'il reste un bon point d'ancrage pour l'expansion de graphe "
            "(retrouver du contexte pertinent en suivant ce lien) DANS CE LIVRE, ou non. Marque-le "
            "GÉNÉRIQUE dans les deux cas suivants, même s'ils sont différents : (1) le terme est vague en "
            "lui-même (ex. \"modèle\", \"donnée\", \"méthode\"), qui ne désignerait rien de précis même peu "
            "fréquent ; (2) le terme est précis mais sa fréquence dans CE livre est si élevée (regarde "
            "\"part_dans_ce_livre\" dans le contexte) que suivre ce lien ramènerait une part trop grande "
            "du livre pour être utile, même si le mot lui-même est spécifique. Marque-le SPÉCIFIQUE seulement "
            "si le terme est à la fois précis ET reste un point d'ancrage raisonnablement sélectif dans ce "
            "livre malgré sa fréquence. Le contexte inclut aussi, si disponible, la répartition des mentions "
            "par type de chunk (\"repartition_chunks\" : avec_code, avec_formule, texte_seul). N'applique "
            "AUCUNE règle fixe sur cette répartition : interprète-la à la lumière de la NATURE du concept "
            "(son \"type\" : method/tool/metric/concept...). Pour une méthode ou un outil, une forte présence "
            "dans des chunks à code/formule est un signe de précision technique réelle, pas de dispersion "
            "superficielle. Pour un concept général non procédural, une telle dispersion sans lien évident "
            "avec son sens est au contraire suspecte. OBLIGATOIRE : ta \"reason\" doit explicitement mentionner "
            "\"repartition_chunks\" (les chiffres avec_code/avec_formule/texte_seul) et dire comment elle a "
            "pesé dans ton verdict — jamais un verdict basé sur la seule fréquence (\"part_dans_ce_livre\") "
            "sans t'être prononcé sur cette répartition, même pour conclure qu'elle ne change rien ici."
        ),
    },
    "2.1": {
        "verdicts": ["ok", "fusion_a_tort", "indetermine"],
        "instructions": (
            "Ce concept a été jugé identique dans plusieurs livres différents (une seule entité du graphe). "
            "Vérifie, à partir des extraits fournis (un par livre), qu'il s'agit BIEN de la MÊME notion à chaque "
            "fois. \"fusion_a_tort\" si les extraits décrivent en réalité des notions différentes."
        ),
    },
    "2.2": {
        "verdicts": ["ok", "fusion_a_tort", "indetermine"],
        "instructions": (
            "Un alias de ce concept ressemble peu, lexicalement, à sa forme canonique. Vérifie, à partir des "
            "extraits fournis, si l'alias désigne réellement le MÊME concept (synonyme, sigle, variante FR/EN "
            "légitime) ou si c'est une fusion à tort de deux notions différentes sous un seul nom."
        ),
    },
    "2.3": {
        "verdicts": ["fusion_manquee", "ok_distincts", "indetermine"],
        "instructions": (
            "Deux concepts distincts, extraits de deux livres différents, ont un embedding très proche mais "
            "n'ont JAMAIS été fusionnés. Vérifie, à partir des extraits fournis (un par concept), s'il s'agit en "
            "réalité de la MÊME notion (\"fusion_manquee\", un bug du pipeline) ou de deux notions réellement "
            "différentes malgré la proximité (\"ok_distincts\")."
        ),
    },
    "2.4": {
        "verdicts": ["doublon", "ok_distincts", "indetermine"],
        "instructions": (
            "Deux concepts distincts, extraits du MÊME livre, ont un embedding très proche. Vérifie, à partir des "
            "extraits fournis, s'il s'agit d'un doublon (même notion, deux entrées à fusionner) ou de deux "
            "notions réellement différentes."
        ),
    },
    "2.5": {
        "verdicts": ["incoherent", "coherent", "indetermine"],
        "instructions": (
            "Une même forme de surface (mot ou expression) est portée, comme alias ou nom canonique, par "
            "PLUSIEURS concepts distincts du graphe. Vérifie, à partir des extraits fournis pour chaque concept, "
            "si c'est une INCOHÉRENCE de résolution (\"incoherent\", l'un des deux rattachements est probablement "
            "faux) ou un chevauchement légitime et sans conséquence pratique (\"coherent\", ex. un sens général "
            "et un sens spécifique suffisamment distincts en contexte)."
        ),
    },
}

# Le contexte fourni contient, par concept, "sens" : sa définition courte (5 à 12
# mots, voir concepts.ConceptMention.sense). Ajoutée à TOUTES les catégories (donc
# prise en compte par _prompt_version : les verdicts rendus sans cette consigne
# sont rejugés). Fixée à la CRÉATION du concept, donc issue d'un seul passage.
_SENSE_NOTE = (
    " Chaque concept du contexte porte aussi un \"sens\" (définition courte, fixée à sa création à partir "
    "d'UN seul passage, donc pas forcément représentative de tous les livres ; vide = non fourni). C'est une "
    "indication pour distinguer deux notions voisines ou un même mot employé dans deux sens, jamais une preuve : "
    "en cas de désaccord entre le \"sens\" et les extraits, ce sont les extraits qui priment. Quand le "
    "\"sens\" a pesé dans ton verdict, dis-le dans \"reason\"."
)
for _cfg in CATEGORIES.values():
    _cfg["instructions"] += _SENSE_NOTE

# Nature de la preuve (voir report.py:chunk_excerpt_info) : sans ce champ, le
# juge ne distingue pas un extrait qui PROUVE la mention d'un simple debut de
# chunk -- c'est ce qui produisait des faux positifs ("extrait hors sujet").
_EVIDENCE_NOTE = (
    " Chaque extrait porte un champ \"terme\" : \"exact\" (le nom ou un alias du concept apparaît tel quel dans "
    "l'extrait), \"approché\" (seul un radical de mot apparaît : preuve plus faible) ou \"absent\" (rien "
    "trouvé : l'extrait est alors le DÉBUT du chunk, il ne prouve ni ne réfute rien)."
)
_ALIAS_NOTE = (
    " \"alias_origine\" donne l'endroit où l'alias suspect a été EXTRAIT du texte (nom tel qu'écrit, sens propre "
    "de cette mention, extrait) : c'est la preuve principale. Compare le \"sens_de_la_mention\" au \"sens\" du "
    "concept et regarde si l'extrait montre la même notion. Liste vide = origine introuvable, c'est une preuve "
    "manquante et non un indice."
)
for _cat, _cfg in CATEGORIES.items():
    if "indetermine" in _cfg["verdicts"]:
        _cfg["instructions"] += _EVIDENCE_NOTE + (_ALIAS_NOTE if _cat == "2.2" else "")

# Regle de doute, alignee sur l'arbitrage de /rag-graphe ("en cas de doute
# reel, reponds false" = ne pas fusionner) : ici, en cas de doute, ne pas
# ACCUSER. Un faux signalement est le pire echec de cette etape.
_PRUDENCE = (
    " RÈGLE DE PRUDENCE : ne rends le verdict « à signaler » que si les données montrent POSITIVEMENT le "
    "problème décrit ci-dessus. Un extrait dont \"terme\" vaut \"absent\", trop court ou hors sujet n'est PAS "
    "une preuve : réponds \"indetermine\" et dis en une phrase ce qui manque. Un faux signalement coûte plus "
    "cher qu'un problème non détecté à ce stade (les cas signalés sont revérifiés sur les chunks complets)."
)
_NO_PRUDENCE = (
    " Si les extraits sont insuffisants pour trancher avec confiance, choisis quand même la valeur la plus "
    'probable et dis-le dans "reason".'
)

# Catégories dont un signalement est confirmé en 2e passe, sur les chunks
# COMPLETS (voir judge_item). 1.3 (généricité d'un hub) juge une fréquence et
# un usage, pas l'identité de deux notions : pas de chunk "décisif" à relire.
CONFIRM_CATEGORIES = {"2.1", "2.2", "2.3", "2.4", "2.5"}
_CONFIRM_NOTE = (
    " DEUXIÈME PASSE. Un premier examen, fait sur de courts extraits, a signalé ce cas (verdict « {verdict} ») "
    "pour la raison : « {reason} ». Tu reçois maintenant les chunks COMPLETS (\"chunks_complets\"). Le premier "
    "avis peut être un faux positif dû à des extraits mal choisis : ne le confirme que si le texte complet montre "
    "positivement le problème. Si le texte montre au contraire que tout va bien, rends le verdict non signalé ; "
    "s'il ne permet toujours pas de trancher, \"indetermine\". Ta \"reason\" dit ce qui, dans les chunks, tranche."
)


def _prompt_version(category: str) -> str:
    """Empreinte courte de la consigne (instructions + enum de verdicts) d'une
    catégorie — change dès qu'on modifie CATEGORIES. Stockée avec chaque
    verdict pour que la reprise entre rapports (_carry_forward_verdicts)
    détecte une consigne modifiée et rejuge, au lieu de recycler un verdict
    rendu sous une définition périmée."""
    cfg = CATEGORIES[category]
    raw = cfg["instructions"] + "|" + ",".join(cfg["verdicts"])
    raw += "|" + (_PRUDENCE if "indetermine" in cfg["verdicts"] else _NO_PRUDENCE)
    if category in CONFIRM_CATEGORIES:
        raw += "|" + _CONFIRM_NOTE
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _system_prompt(category: str) -> str:
    cfg = CATEGORIES[category]
    verdicts = " | ".join(f'"{v}"' for v in cfg["verdicts"])
    return (
        "Tu es un auditeur de qualité pour un graphe de concepts (GraphRAG) construit automatiquement à partir "
        "de plusieurs livres. " + cfg["instructions"] + " Base ton jugement UNIQUEMENT sur les données fournies "
        "(nom(s), type(s), alias, extraits de chunks réels) — jamais sur une connaissance générale qui "
        "contredirait ces extraits." + (_PRUDENCE if "indetermine" in cfg["verdicts"] else _NO_PRUDENCE)
        + " Réponds UNIQUEMENT avec un objet JSON "
        f'{{"verdict": {verdicts}, "reason": "<15 mots max, en français>"}}, sans texte ni balise autour.'
    )


class AuditError(RuntimeError):
    pass


def _ask(system: str, prompt: str, category: str, *, model: str | None, timeout: int) -> dict:
    """Un appel `claude -p`, avec retry sur réponse MAL FORMÉE (JSON invalide,
    pas un objet, verdict hors énum) — jusqu'à MAX_FORMAT_RETRIES tentatives
    supplémentaires (voir _claude_code_client.py : ce type d'échec est
    majoritairement non-déterministe, un nouvel appel complet réussit la
    plupart du temps, même principe que describe_code/describe_formula/
    describe_image). Un échec d'INFRASTRUCTURE (ClaudeCodeCallError : timeout,
    process, erreur explicite de `claude -p`) n'est lui JAMAIS retenté ici —
    pas la même justification empirique. Retourne {"verdict", "reason"}."""
    format_error: str | None = None
    for _ in range(MAX_FORMAT_RETRIES + 1):
        try:
            result = run_claude_code(system, prompt, model=model, timeout=timeout)
        except ClaudeCodeCallError as exc:
            raise AuditError(str(exc)) from exc
        raw = strip_code_fence(result)
        try:
            decision = json.loads(raw)
        except json.JSONDecodeError:
            format_error = f"réponse non-JSON de claude -p : {raw!r}"
            continue
        if not isinstance(decision, dict):
            format_error = f"réponse JSON valide mais pas un objet : {decision!r}"
            continue
        verdict = decision.get("verdict")
        if verdict not in CATEGORIES[category]["verdicts"]:
            format_error = f"verdict hors enum attendu ({CATEGORIES[category]['verdicts']}) : {decision!r}"
            continue
        return {"verdict": verdict, "reason": str(decision.get("reason", ""))[:300]}
    raise AuditError(f"{format_error} (après {MAX_FORMAT_RETRIES + 1} tentative(s))")


def judge_item(item: dict, *, model: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Jugement d'un item, en une ou deux passes.

    1re passe : contexte du rapport (extraits courts, sens, alias...).
    2e passe (seulement si la 1re SIGNALE le cas, catégories de
    CONFIRM_CATEGORIES, et si le rapport fournit `confirmation`) : le même cas
    revu sur les chunks COMPLETS, avec la consigne de ne confirmer que sur une
    preuve positive. Le verdict de la 2e passe est le verdict final ; la 1re est
    conservée sous "premiere_passe". Une confirmation qui échoue (AuditError)
    fait échouer l'item : un signalement non confirmé n'est jamais rendu tel quel."""
    category = item["category"]
    if category not in CATEGORIES:
        raise AuditError(f"catégorie inconnue: {category!r}")
    prompt = f"Sujet : {item['subject']}\n\nContexte (JSON) :\n{json.dumps(item['context'], ensure_ascii=False, indent=2)}"

    first = _ask(_system_prompt(category), prompt, category, model=model, timeout=timeout)
    result = {**first, "prompt_version": _prompt_version(category)}
    if category not in CONFIRM_CATEGORIES or first["verdict"] != _FLAGGED[category]:
        return result
    chunks = (item.get("confirmation") or {}).get("chunks")
    if not chunks:
        return {**result, "confirmation": "indisponible"}  # rapport sans chunks complets : signalement NON confirmé
    system = _system_prompt(category) + _CONFIRM_NOTE.format(verdict=first["verdict"], reason=first["reason"])
    prompt2 = prompt + f"\n\nchunks_complets (JSON) :\n{json.dumps(chunks, ensure_ascii=False, indent=2)}"
    second = _ask(system, prompt2, category, model=model, timeout=timeout)
    return {**second, "prompt_version": _prompt_version(category), "premiere_passe": first}


# ---------------------------------------------------------------------------

def _prior_reports(exclude: Path) -> list[Path]:
    """Autres rapports (pas celui en cours), du plus récent au plus ancien."""
    candidates = sorted(
        (p for p in paths.AUDIT_DIR.glob("rapport_graphe_*.json")
         if not p.name.endswith(".verdicts.json") and p.resolve() != exclude.resolve()),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    return candidates


def _carry_forward_verdicts(report_path: Path, items: list[dict]) -> dict:
    """Reprend, pour chaque item, le verdict d'un rapport précédent portant le
    MÊME id ET le MÊME contexte (mêmes extraits/alias — donc la même preuve
    aurait été montrée à Claude) ET la MÊME consigne (voir _prompt_version) :
    sans ça, chaque nouveau rapport rejugerait tous les items déjà tranchés
    lors d'audits précédents, un coût réel en appels `claude -p` qui grandit
    avec le corpus à chaque livre ajouté, alors que la grande majorité des
    items n'ont pas changé. Un item dont le contexte ou la consigne diffère
    n'est PAS repris : son ancien verdict pourrait ne plus être justifié."""
    by_id = {it["id"]: it for it in items}
    remaining = set(by_id)
    carried: dict[str, dict] = {}
    for prior_path in _prior_reports(report_path):
        if not remaining:
            break
        v_path = _verdicts_path(prior_path)
        if not v_path.exists():
            continue
        try:
            prior_report = json.loads(prior_path.read_text(encoding="utf-8"))
            prior_verdicts = json.loads(v_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        prior_items = {it["id"]: it for it in prior_report.get("audit_items", [])}
        for item_id in list(remaining):
            if item_id not in prior_verdicts or item_id not in prior_items:
                continue
            item = by_id[item_id]
            prior_verdict = prior_verdicts[item_id]
            # Contexte inchangé ET consigne inchangée (voir _prompt_version) :
            # un verdict rendu sous une ancienne formulation de la catégorie
            # (ex. définition de "générique" revue) n'est jamais recyclé tel
            # quel, même si le contexte, lui, n'a pas bougé.
            if (prior_items[item_id].get("context") == item.get("context")
                    and prior_verdict.get("prompt_version") == _prompt_version(item["category"])):
                carried[item_id] = prior_verdict
                remaining.discard(item_id)
    return carried


def _latest_report() -> Path | None:
    # Le "*" du glob avale aussi ".verdicts" : exclut explicitement ces
    # fichiers (écrits APRÈS le rapport, donc plus récents que lui) pour ne
    # jamais les prendre par erreur pour "le dernier rapport".
    candidates = sorted(
        (p for p in paths.AUDIT_DIR.glob("rapport_graphe_*.json") if not p.name.endswith(".verdicts.json")),
        key=lambda p: p.stat().st_mtime,
    )
    return candidates[-1] if candidates else None


def _verdicts_path(report_path: Path) -> Path:
    return report_path.with_suffix("").with_suffix(".verdicts.json")


def _synthesis_path(report_path: Path) -> Path:
    return report_path.parent / f"{report_path.stem}_audit.md"


def _load_verdicts(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_verdicts(path: Path, verdicts: dict) -> None:
    path.write_text(json.dumps(verdicts, ensure_ascii=False, indent=2), encoding="utf-8")


_FLAGGED = {  # verdict par catégorie qui mérite d'être mis en avant dans la synthèse
    "1.3": "generique", "2.1": "fusion_a_tort", "2.2": "fusion_a_tort",
    "2.3": "fusion_manquee", "2.4": "doublon", "2.5": "incoherent",
}


def write_synthesis(path: Path, report: dict, verdicts: dict) -> None:
    items = report["audit_items"]
    by_cat: dict[str, list[dict]] = {}
    for item in items:
        by_cat.setdefault(item["category"], []).append(item)

    lines = [
        "# Synthèse de l'audit /rag-integrity", "",
        f"{len(verdicts)}/{len(items)} item(s) jugés par Claude — voir {report.get('_source_path', '')}.", "",
    ]
    titles = {
        "1.3": "1.3 — Concepts hubs (généricité)",
        "2.1": "2.1 — Concepts partagés entre livres (fusion à tort ?)",
        "2.2": "2.2 — Alias suspects (fusion à tort ?)",
        "2.3": "2.3 — Fusions manquées entre livres",
        "2.4": "2.4 — Doublons intra-livre",
        "2.5": "2.5 — Alias incohérents",
    }
    for cat in ["1.3", "2.1", "2.2", "2.3", "2.4", "2.5"]:
        cat_items = by_cat.get(cat, [])
        if not cat_items:
            continue
        judged = [(it, verdicts[it["id"]]) for it in cat_items if it["id"] in verdicts]
        flagged = [(it, v) for it, v in judged if v["verdict"] == _FLAGGED[cat]]
        undetermined = [(it, v) for it, v in judged if v["verdict"] == "indetermine"]
        # Signalés en 1re passe puis écartés par la confirmation sur chunks complets :
        # mesure directe des faux positifs que la 2e passe a évités.
        dismissed = [(it, v) for it, v in judged
                     if v.get("premiere_passe", {}).get("verdict") == _FLAGGED[cat] and v["verdict"] != _FLAGGED[cat]]
        summary = f"{len(judged)}/{len(cat_items)} jugés ; {len(flagged)} marqué(s) `{_FLAGGED[cat]}`"
        if cat in CONFIRM_CATEGORIES:
            summary += f" (après confirmation sur chunks complets ; {len(dismissed)} signalement(s) écarté(s) à cette étape)"
        summary += f" ; {len(undetermined)} indéterminé(s) (preuve insuffisante)."
        lines += ["", f"## {titles[cat]}", "", summary, ""]
        if flagged:
            lines += ["| Sujet | Verdict | Raison | Confirmation |", "|---|---|---|---|"]
            lines += [
                f"| {it['subject']} | {v['verdict']} | {v['reason']} | "
                f"{'2 passes' if 'premiere_passe' in v else v.get('confirmation', '1 passe')} |"
                for it, v in flagged
            ]
        else:
            lines.append("_(rien à signaler dans cette catégorie)_")
        if undetermined:
            lines += ["", "Indéterminés (à examiner à la main ou après amélioration de la preuve) :", "",
                      "| Sujet | Ce qui manque |", "|---|---|"]
            lines += [f"| {it['subject']} | {v['reason']} |" for it, v in undetermined]
    open_flags = report.get("open_flags") or []
    if open_flags:
        lines += ["", "## Signalements encore ouverts (jugés lors d'un audit précédent)", "",
                  "| Id | Catégorie | Sujet | Verdict | Raison |", "|---|---|---|---|---|"]
        lines += [f"| {f['id']} | {f['category']} | {f['subject']} | {f['verdict']} | {f['reason']} |" for f in open_flags]
        lines += ["", "À corriger dans le graphe (l'item disparaît alors du rapport), ou à écarter avec `audit_accept.py <id>`."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("report", nargs="?", type=Path, help="rapport_graphe_<ts>.json (défaut : le plus récent sous rag_data/audit/)")
    parser.add_argument("--batch-size", type=int, default=None, help="items max à juger dans cette invocation (défaut : tous)")
    parser.add_argument("--force", action="store_true", help="rejuge tous les items (ignore verdicts.json existant)")
    parser.add_argument("--model", type=str, default=None)
    args = parser.parse_args()

    report_path = args.report or _latest_report()
    if report_path is None or not report_path.exists():
        print(f"ERREUR: aucun rapport trouvé (attendu : rag_data/audit/rapport_graphe_*.json, ou --report explicite)", file=sys.stderr)
        return 1
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERREUR: rapport illisible ({report_path}): {exc}", file=sys.stderr)
        return 1
    items = report.get("audit_items", [])
    if not items:
        n_open = len(report.get("open_flags") or [])
        print(f"aucun item à juger (nouveaux ou modifiés) — rien à faire ; {report.get('n_settled_ok', 0)} déjà validé(s), "
              f"{n_open} signalement(s) encore ouvert(s) (voir le rapport, section « État de l'audit »)")
        write_synthesis(_synthesis_path(report_path), {**report, "_source_path": str(report_path)}, {})
        return 0

    verdicts_path = _verdicts_path(report_path)
    verdicts = {} if args.force else _load_verdicts(verdicts_path)
    if verdicts:
        print(f"reprise : {len(verdicts)} item(s) déjà jugé(s) (ce rapport), ignoré(s)")

    if not args.force:
        carried = _carry_forward_verdicts(report_path, items)
        new_carried = {k: v for k, v in carried.items() if k not in verdicts}
        if new_carried:
            verdicts.update(new_carried)
            _save_verdicts(verdicts_path, verdicts)
            print(f"repris de rapport(s) précédent(s) (même id, même contexte) : {len(new_carried)} item(s), ignorés")

    registry = decisions.load()
    by_id = {it["id"]: it for it in items}
    for item_id, verdict in verdicts.items():  # verdicts repris d'un rapport précédent : aussi dans le registre
        if item_id in by_id and not decisions.is_current(registry.get(item_id), by_id[item_id]):
            decisions.record(registry, by_id[item_id], verdict)
    decisions.save(registry)
    pending = [it for it in items if it["id"] not in verdicts]
    batch = pending if args.batch_size is None else pending[: args.batch_size]
    print(f"--- Lot en cours : {len(batch)} item(s) sur {len(pending)} restant(s) ---")

    successes = 0
    for item in batch:
        try:
            verdicts[item["id"]] = judge_item(item, model=args.model)
        except AuditError as exc:
            print(f"  item {item['id']} ({item['category']}): audit ignoré ({exc})", file=sys.stderr)
            continue
        successes += 1
        _save_verdicts(verdicts_path, verdicts)
        decisions.record(registry, item, verdicts[item["id"]])
        decisions.save(registry)
        print(f">>> Progression : {len(verdicts)}/{len(items)} item(s) traités")

    still_pending = [it for it in items if it["id"] not in verdicts]
    if args.batch_size is not None and still_pending and successes > 0:
        print(
            f"Lot de {len(batch)} item(s) traité(s) ({successes} réussi(s)), {len(still_pending)} restant(s) — "
            "relance exactement la même commande (sans --force) pour continuer."
        )
        return 3

    # Lot où rien n'a abouti (item(s) restant systématiquement en échec) : le
    # contrôle ci-dessous tranche, même règle que /rag-concepts et /rag-graphe.
    fail_rate = len(still_pending) / len(items) if items else 0.0
    write_synthesis(_synthesis_path(report_path), {**report, "_source_path": str(report_path)}, verdicts)
    print(f"synthèse écrite dans {_synthesis_path(report_path)}")
    if fail_rate > 0.05:
        print(f"BLOQUANT: {len(still_pending)}/{len(items)} item(s) ({fail_rate:.1%}) n'ont jamais pu être jugés (> 5 %)")
        return 2
    print(f"OK: {len(verdicts)}/{len(items)} item(s) jugés" + (f" ({len(still_pending)} non jugé(s), <= 5 %)" if still_pending else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
