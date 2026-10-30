"""Etape 3 de /maillage : pour chaque article SOURCE, lit EN ENTIER la source et chacune des
cibles retenues par etape2_pairs.py, et propose les liens internes (ancre + cible) ; ecrit les
rapports rag_data/maillage/work/<slug>/<slug>.md et .json, et rag_data/maillage/INDEX.md.

Principe : UN appel `claude -p` par paire (source complete + une cible complete + les concepts
partages repérés par le graphe). Le LLM doit :
  - choisir une ancre ECRITE TELLE QUELLE dans un paragraphe de la source (1 a 7 mots) ;
  - prouver que la cible traite le sujet de l'ancre en profondeur (sujet central de la page ou
    rubrique entiere qui lui est consacree) en CITANT un passage de la cible ;
  - noter l'adequation du lien (1 a 5) ;
  - ne rien proposer si le lien serait faible (zero lien est une reponse normale).
Le code VERIFIE : l'ancre existe mot pour mot dans le paragraphe (hors formule et hors lien
existant) ; la citation existe mot pour mot dans la cible ; le niveau est « central » ou
« section ». Puis il tranche entre cibles concurrentes (pages soeurs : definition / cas
d'usage / criteres de choix / maths) par l'adequation puis le score de paire, et applique les
quotas (UN SEUL lien par URL cible, sans exception ; 2 liens max par paragraphe ;
jamais deux fois la meme ancre ; jamais deux liens dans une meme phrase).

Reprise : chaque reponse LLM est mise en cache (work/links_cache.json, cle = hash du texte des
deux articles + prompt) ; une relance ne refait que ce qui manque. Une invocation s'arrete
avant 10 min (code de sortie 3 : relancer la meme commande).

Usage:
    etape3_links.py <slug|numero> | --all  [--show-pairs] [--offline] [--max-targets N]
  --show-pairs : affiche les paires de la source (meme mise en forme que pairs/PAIRES_URLS.md :
                 titre numerote, source, nombre de cibles, puces score/titre/URL/concepts), SANS
                 aucun appel LLM, puis s'arrete.
Codes de sortie : 0 termine, 1 erreur d'usage, 2 echec d'appel, 3 temps ecoule (relancer).
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
import hashlib
import json
import re
import sys
import time
import unicodedata
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path

from _claude_code_client_systemfile import ClaudeCodeCallError, MAX_FORMAT_RETRIES, run_claude_code, strip_code_fence  # noqa: E402
import etape4_usage as journal  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

CONTENT_DIR = _ROOT / "rag_data" / "content" / "work"
MAILLAGE_DIR = _ROOT / "rag_data" / "maillage"
PAIRS_DIR = MAILLAGE_DIR / "pairs"
WORK_DIR = MAILLAGE_DIR / "work"
PUBLISHED_DIR = MAILLAGE_DIR / "published"  # traces de /publish-maillage : published/<slug>.json
CONTENT_PUBLISHED_DIR = _ROOT / "rag_data" / "content" / "published"  # etat WordPress : <slug>.json, wp_status == "publish"


def publie_sans_rapport(folder: str) -> bool:
    """Article publie sur WordPress et sans rapport de maillage (filtre de --all)."""
    publie = load_json(CONTENT_PUBLISHED_DIR / f"{folder}.json", {}).get("wp_status") == "publish"
    return publie and not (WORK_DIR / folder / f"{folder}.md").exists()
CACHE_PATH = WORK_DIR / "links_cache.json"
WP_API = "https://www.ranksignal.fr/wp-json/wp/v2/posts"
TIME_LIMIT_S = 450
STARTED = time.time()
MAX_LINKS_PER_SOURCE = 12
MODEL = "claude-haiku-5-5"
EFFORT = "medium"

STOP = set("""a au aux avec ce ces cet cette dans de des du elle en et il ils je la le les leur leurs lui ma mais me
meme mes moi mon ne nos notre nous on ou par pas pour qu que qui sa se ses si son sur ta te tes toi ton tu un une
vos votre vous est sont etre avoir fait faire peut plus moins tres aussi comme donc alors car ni y d l s n c j m t""".split())


class TimeUp(Exception):
    pass


def check_time() -> None:
    if time.time() - STARTED > TIME_LIMIT_S:
        raise TimeUp()


def run_claude_configured(system_prompt: str, input_text: str, *, timeout: int) -> str:
    """Tous les `claude -p` de ce script utilisent MODEL et EFFORT (jamais le modele par defaut du compte).
    Chaque appel est journalise dans rag_data/maillage/usage.jsonl (voir etape4_usage.py)."""
    return journal.appel_claude(run_claude_code, system_prompt, input_text, model=MODEL, effort=EFFORT, timeout=timeout)


# ---------------------------------------------------------------- texte
def norm_char(c: str) -> str:
    if c in "’‘`´":
        return "'"
    d = unicodedata.normalize("NFD", c)
    return d[0].lower() if d else c.lower()


def norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", "".join(norm_char(c) for c in text)).strip()


def stem(tok: str) -> str:
    return tok[:-1] if len(tok) > 3 and tok[-1] in "sx" else tok


def stem_words(text: str) -> tuple[str, ...]:
    return tuple(stem("".join(norm_char(c) for c in m.group())) for m in re.finditer(r"[A-Za-zÀ-ÿŒœ0-9]+", text))


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def clean(s: str) -> str:
    s = re.sub(r"<!--.*?-->", "", s)
    s = re.sub(r"\*\*|__|`", "", s)
    return re.sub(r"(?<!\*)\*(?!\*)", "", s)


@dataclass
class Article:
    folder: str
    slug: str
    title: str
    expression: str
    paragraphs: list[str]
    para_section: list[str]
    outline: str            # titres H2 + prose, dans l'ordre : ce que lit le LLM pour une cible
    url: str = ""
    words: int = 0


def load_article(folder: Path) -> Article:
    raw = (folder / "article.md").read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n(.*)", raw, re.S)
    fm, body = {}, raw
    if m:
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip().strip("\"'")
        body = m.group(2)
    expression = ""
    if (folder / "dossier.json").exists():
        expression = str(json.loads((folder / "dossier.json").read_text(encoding="utf-8")).get("expression", "")).strip()
    paras, secs, outline = [], [], []
    cur = ""
    in_code = in_math = in_toc = False
    buf: list[str] = []

    def flush():
        if buf:
            p = clean(" ".join(buf)).strip()
            if len(p) > 40:
                paras.append(p)
                secs.append(cur)
                outline.append(p)
            buf.clear()

    for line in body.splitlines():
        st = line.strip()
        if st.startswith("```"):
            flush(); in_code = not in_code; continue
        if in_code:
            continue
        if st.startswith("$$"):
            flush()
            if not (st.endswith("$$") and len(st) > 3):
                in_math = not in_math
            continue
        if in_math:
            continue
        if in_toc:  # le Sommaire = l'etiquette + sa liste de liens : exclu en entier
            if not st or re.match(r"^([-*]|\d+\.)\s+", st):
                continue
            in_toc = False
        if re.match(r"^\*\*Sommaire\*\*\s*:?\s*$", st, re.I):
            flush()
            in_toc = True
            continue
        if st.startswith("#"):  # seuls les vrais titres (balises H) sont exclus ; la FAQ ne l'est jamais
            flush()
            if st.startswith("## "):
                cur = clean(st[3:]).strip()
                outline.append(f"## {cur}")
            continue
        if st.startswith(("![", "|")) or re.match(r"^-{3,}$", st) or not st:
            flush(); continue
        is_item = bool(re.match(r"^([-*]|\d+\.)\s+", st))
        st = re.sub(r"^>\s*", "", st)
        st = re.sub(r"^([-*]|\d+\.)\s+", "", st)
        if is_item:
            flush(); buf.append(st); flush()
        else:
            buf.append(st)
    flush()
    return Article(folder=folder.name, slug=fm.get("slug") or folder.name, title=fm.get("title", folder.name),
                   expression=expression, paragraphs=paras, para_section=secs, outline="\n\n".join(outline),
                   words=sum(len(p.split()) for p in paras))


def fetch_urls() -> dict[str, str]:
    urls, page = {}, 1
    while True:
        try:
            req = urllib.request.Request(f"{WP_API}?per_page=100&page={page}&_fields=slug,link",
                                         headers={"User-Agent": "internal-linking/1.0"})
            data = json.load(urllib.request.urlopen(req, timeout=20))
        except Exception:
            break
        urls.update({p["slug"]: p["link"] for p in data})
        if len(data) < 100:
            break
        page += 1
    return urls


def cibles_publiees(pairs: list[dict], arts: dict[str, Article]) -> list[dict]:
    """Garde les paires dont la cible a une URL WordPress : une cible non publiee n'est jamais proposee."""
    return [p for p in pairs if arts[p["target"]].url]


def link_spans(para: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in re.finditer(r"\[[^\]]*\]\([^)]*\)", para)]


def find_anchor(para: str, anchor: str) -> int:
    """Position de `anchor` mot pour mot dans `para` (hors formule $...$ et hors lien existant), ou -1."""
    masked = re.sub(r"\$[^$]*\$", lambda m: " " * len(m.group()), para)
    spans = link_spans(para)
    for m in re.finditer(re.escape(anchor), masked):
        if not any(a <= m.start() < b for a, b in spans):
            return m.start()
    return -1


def sentence_around(para: str, s: int, e: int) -> str:
    a = max(para.rfind(". ", 0, s), para.rfind("? ", 0, s), para.rfind("! ", 0, s))
    a = 0 if a < 0 else a + 2
    cands = [i for i in (para.find(". ", e), para.find("? ", e), para.find("! ", e)) if i >= 0]
    b = min(cands) + 1 if cands else len(para)
    sent, k = para[a:b], s - a
    return sent[:k] + "**" + sent[k:k + (e - s)] + "**" + sent[k + (e - s):]


# ---------------------------------------------------------------- appel LLM par paire
SYSTEM = """Tu es éditeur SEO exigeant, spécialiste du maillage interne d'un blog pédagogique (data science / IA).
On te donne UN ARTICLE SOURCE (paragraphes numérotés) et UN ARTICLE CIBLE (texte intégral, avec l'expression de
recherche pour laquelle il a été écrit, qui donne son sujet et son intention réels). Tu décides s'il existe, dans
la source, une ou deux ancres de lien interne VERS la cible qui rendraient un vrai service au lecteur.

Un lien n'est justifié QUE SI :
1. L'ancre désigne, dans CETTE phrase, LE SUJET CENTRAL de la cible : celui de son expression de recherche et de son
   titre. Une notion que la cible traite seulement comme un chapitre, une rubrique ou un exemple (même longuement) ne
   suffit PAS : la page n'est pas la page de référence de cette notion. Exemple : « moindres carrés » ne mène pas à
   « Qu'est-ce qu'une régression linéaire ? » ; « K plus proches voisins » ne mène pas à une page sur la régression
   linéaire qui compare ses alternatives.
2. Le lecteur peut avoir ici une raison d'approfondir CE sujet : notion introduite, renvoi, comparaison — ou
   simplement parce que la source traite d'un sujet VOISIN dont la cible est la page de référence ou le guide
   complémentaire (ex. un article sur l'outil RAGAS qui écrit « évaluer un RAG » renvoie naturellement vers la page
   sur l'évaluation d'un RAG). Dès que la source NOMME le sujet central de la cible, le lien est légitime, MÊME SI la
   source en parle déjà elle-même : ne rejette jamais pour ce seul motif. Pas de lien si la mention est anodine,
   décorative, un exemple chiffré, un détail technique, ou si le mot est employé dans un autre sens.
3. L'intention de la cible correspond au besoin du lecteur à cet endroit (définition « qu'est-ce que », cas d'usage
   « à quels problèmes », critères de choix « quand utiliser », maths vulgarisées, guide pratique « comment »).
   Si une autre page du blog (page sœur) serait plus adaptée à ce moment, ne propose pas celle-ci.

L'ancre doit être LE NOM du sujet de la cible (ou un synonyme, une abréviation, une variante directe), jamais une
de ses composantes : « taille des fragments », « RecursiveCharacterTextSplitter » ou « chevauchement » sont des
composantes du chunking, pas le chunking ; « ordonnée à l'origine » est une composante de la régression linéaire,
pas la régression linéaire. Si le texte source ne contient aucune expression qui NOMME le sujet de la cible, il n'y
a pas de lien.

AUCUNE ancre dans un titre : les paragraphes fournis sont du texte courant, les titres (H1 à H6) n'y figurent pas, et
tu ne dois jamais proposer d'ancre qui en soit tirée (règle SEO : pas de lien dans les balises H). Le Sommaire (table
des matières) n'y figure pas non plus. Les blocs de FAQ (questions en gras comprises) sont du texte courant comme un
autre : rien n'y est exclu.

L'ancre : un GROUPE NOMINAL de 1 à 7 mots — ou un INFINITIF avec son complément (« évaluer un RAG ») — qui NOMME le
sujet ; jamais une proposition entière ni un verbe conjugué (« la régression logistique prend le relais » est
interdit : ancre = « régression logistique » ; « Évaluer un RAG, c'est surveiller trois relations » : ancre =
« Évaluer un RAG »). ÉCRITE EXACTEMENT comme
dans le paragraphe (même casse, mêmes accents, mêmes mots), sans article au début (« régression linéaire », pas
« la régression linéaire »). Si le nom du sujet est suivi, entre parenthèses, de son équivalent (sigle et forme
développée : « RSS (residual sum of squares) », « modèles additifs (GAM) »), l'ancre peut inclure la parenthèse, car
les deux désignent le même concept ; si la parenthèse contient autre chose (précision, aparté, exemple), l'ancre
s'arrête au nom du sujet. Jamais une formule, un nombre, un titre de section, ni un mot générique seul
(« régression », « modèle », « données », « agent »). Elle doit rester claire hors contexte. Les sections
« L'essentiel en 30 secondes », « À retenir » et la FAQ sont du texte comme un autre : une ancre qui s'y trouve est
valable, y compris quand c'est la SEULE occurrence du sujet (ne la rejette jamais pour cette seule raison). Si le
sujet apparaît plusieurs fois, préfère la première occurrence naturelle dans le texte.

PREUVE OBLIGATOIRE : "preuve_cible" = une citation EXACTE (copiée mot pour mot, 4 à 30 mots) de l'article cible
qui montre que SON sujet est celui de l'ancre (sa définition, son titre, sa phrase d'ouverture). Sans citation
exacte, pas de lien.

"niveau" : toujours "central" (le sujet de la page). Si ce n'est pas le cas, ne propose pas le lien.
"adequation" : 1 à 5 (5 = lien évident et très utile ; 3 = utile ; ≤ 2 = ne le propose pas).
MIEUX VAUT ZÉRO LIEN QU'UN LIEN FAIBLE : une réponse sans lien est normale et fréquente. UN SEUL lien au maximum
vers cette cible (règle absolue : jamais deux liens de la source vers la même page) : choisis la meilleure ancre,
la première occurrence naturelle dans le corps du texte, et laisse les autres.

Réponds UNIQUEMENT par un JSON :
{"links":[{"paragraph":<n>,"anchor":"<expression exacte>","niveau":"central","preuve_cible":"<citation exacte>",
"adequation":<1-5>,"reason":"<une phrase : ce que le lecteur trouvera sur la cible et pourquoi ici>"}],
 "aucun_motif":"<si links est vide : pourquoi, en une phrase>"}"""


def source_block(src: Article) -> str:
    paras = "\n\n".join(f"[{i}] ({src.para_section[i] or 'introduction'}) {p}" for i, p in enumerate(src.paragraphs))
    return f"=== ARTICLE SOURCE : {src.title} ===\n\n{paras}"


def build_prompt(tgt: Article, shared: list[dict]) -> str:
    hints = "; ".join(f"{x['concept_cible']} (≈ « {x['concept_source']} » dans la source)" if x["via"] == "soft"
                      else x["concept_cible"] for x in shared[:8])
    return (f"=== ARTICLE CIBLE ===\nExpression de recherche d'origine : {tgt.expression or tgt.title}\n"
            f"Titre : {tgt.title}\n"
            f"Concepts que la source mentionne et que la cible traite en profondeur (repérés par le graphe, "
            f"à titre d'indice) : {hints}\n\nTexte intégral :\n{tgt.outline}")


def call_pair(src: Article, tgt: Article, shared: list[dict], cache: dict) -> dict | None:
    # La source (identique pour toutes les cibles d'une même source) part dans le system prompt ; la cible dans l'entrée.
    system = SYSTEM + "\n\n" + source_block(src)
    prompt = build_prompt(tgt, shared)
    key = sha(system + prompt + MODEL + EFFORT)
    if key in cache:
        journal.cache_hit("proposition_lien", etape="liens", article=src.folder, cible=tgt.folder)
        return cache[key]
    check_time()
    last = ""
    with journal.bloc("proposition_lien", etape="liens", article=src.folder, cible=tgt.folder):
        for attempt in range(MAX_FORMAT_RETRIES + 1):
            extra = f"\n\nATTENTION, ta réponse précédente était invalide : {last}" if last else ""
            try:
                obj = json.loads(strip_code_fence(run_claude_configured(system, prompt + extra, timeout=400)))
                if not isinstance(obj.get("links"), list):
                    raise ValueError("champ « links » absent")
                cache[key] = {"links": obj["links"], "motif": str(obj.get("aucun_motif", "")).strip()}
                CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
                return cache[key]
            except (ClaudeCodeCallError, json.JSONDecodeError, ValueError, TypeError, AttributeError) as exc:
                last = str(exc)[:200]
                print(f"  essai {attempt + 1} invalide ({src.folder[:25]} -> {tgt.folder[:25]}) : {last}", file=sys.stderr)
    return None


# ---------------------------------------------------------------- critique independante
CRITIC_SYSTEM = """Tu es relecteur SEO. Pour chaque lien interne proposé, une seule question :
l'ANCRE NOMME-T-ELLE LE SUJET DE LA PAGE CIBLE (ou en est-elle un synonyme, une abréviation ou une variante directe),
au point que la page cible soit LA page de référence qu'un lecteur attend en cliquant sur cette ancre ?

ok=true  : l'ancre désigne le sujet même de la page (ex. « RAG agentique » -> page « RAG agentique » ;
           « régression logistique » -> page sur la régression logistique).
ok=false : l'ancre nomme une composante, un outil, une technique, une métrique, un exemple ou un chapitre de la
           page (ex. « taille des fragments » -> page sur le chunking ; « moindres carrés » -> page « Qu'est-ce
           qu'une régression linéaire ? » ; « RecursiveCharacterTextSplitter » -> page sur le chunking), ou une
           expression mal découpée, ou un autre sens du mot.
Un infinitif avec son complément qui nomme le sujet (« évaluer un RAG » -> page sur l'évaluation d'un RAG) est une
ancre valide : ok=true.
Tu juges UNIQUEMENT le lien entre l'ancre et le SUJET de la page, pas l'intention de la page ni l'utilité du lien
(déjà jugées ailleurs). Le sujet d'une page inclut le problème qu'elle traite : « hallucinations » ->
« Éviter que l'agent hallucine sur les documents récupérés » est ok=true, car l'ancre nomme le sujet de la page.
En cas de doute sur le fait que l'ancre soit une COMPOSANTE plutôt que le sujet, ok=false.

Réponds UNIQUEMENT par un JSON : {"verdicts":[{"id":<n>,"ok":true|false,"motif":"<quelques mots>"}]} avec une entrée
par lien."""


def critic(src: Article, cands: list["Link"], arts: dict, cache: dict) -> dict[int, tuple[bool, str]]:
    if not cands:
        return {}
    blocks = []
    for i, l in enumerate(cands, 1):
        t = arts[l.target]
        sent = sentence_around(src.paragraphs[l.para_idx], l.start, l.end)
        blocks.append(f"=== LIEN {i} ===\nAncre : « {l.anchor} »\nPhrase de la source : {sent}\n"
                      f"Page cible : {t.title}\nExpression de recherche de la page : {t.expression or t.title}\n"
                      f"Rubriques de la page : {' | '.join(h[3:] for h in t.outline.split(chr(10) * 2) if h.startswith('## '))}\n"
                      f"Début de la page : {' '.join(t.paragraphs[:2])[:600]}")
    prompt = "\n\n".join(blocks)
    key = sha(CRITIC_SYSTEM + prompt + MODEL + EFFORT)
    if key not in cache:
        check_time()
        last = ""
        with journal.bloc("critique_liens", etape="liens", article=src.folder, nb_liens=len(cands)):
            for attempt in range(MAX_FORMAT_RETRIES + 1):
                try:
                    obj = json.loads(strip_code_fence(run_claude_configured(CRITIC_SYSTEM, prompt, timeout=400)))
                    got = {int(v["id"]): (bool(v["ok"]), str(v.get("motif", ""))) for v in obj["verdicts"]}
                    if set(got) >= set(range(1, len(cands) + 1)):
                        cache[key] = {str(k): list(v) for k, v in got.items()}
                        CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
                        break
                    last = "verdicts manquants"
                except (ClaudeCodeCallError, json.JSONDecodeError, ValueError, TypeError, KeyError, AttributeError) as exc:
                    last = str(exc)[:200]
                print(f"  critique : essai {attempt + 1} invalide : {last}", file=sys.stderr)
            else:
                raise ClaudeCodeCallError(f"critique inexploitable : {last}")
    else:
        journal.cache_hit("critique_liens", etape="liens", article=src.folder, nb_liens=len(cands))
    return {int(k): (v[0], v[1]) for k, v in cache[key].items()}


# ---------------------------------------------------------------- validation par le code
@dataclass
class Link:
    target: str
    para_idx: int
    start: int
    end: int
    anchor: str
    niveau: str
    preuve: str
    adequation: int
    reason: str
    pair_score: float


def validate(src: Article, tgt: Article, raw: list, pair_score: float) -> tuple[list[Link], list[str]]:
    ok, errs = [], []
    tnorm = norm_text(tgt.outline + " " + tgt.title)
    for it in raw:
        try:
            pi, anchor = int(it["paragraph"]), str(it["anchor"]).strip()
            niveau, preuve, adq = str(it["niveau"]), str(it["preuve_cible"]).strip(), int(it["adequation"])
        except (KeyError, TypeError, ValueError):
            errs.append(f"entrée invalide : {str(it)[:80]}"); continue
        if not 0 <= pi < len(src.paragraphs):
            errs.append(f"paragraphe hors limites : {pi}"); continue
        words = anchor.split()
        if not 1 <= len(words) <= 7 or all(w in STOP for w in stem_words(anchor)) or any(ch.isdigit() for ch in anchor) and len(words) == 1:
            errs.append(f"ancre inadmissible : « {anchor} »"); continue
        pos = find_anchor(src.paragraphs[pi], anchor)
        if pos < 0:
            errs.append(f"ancre absente du paragraphe {pi} : « {anchor} »"); continue
        if niveau != "central":
            errs.append(f"niveau « {niveau} » : le sujet n'est pas celui de la page cible"); continue
        if len(preuve.split()) < 3 or norm_text(preuve) not in tnorm:
            errs.append(f"citation absente de la cible : « {preuve[:60]} »"); continue
        if adq < 3:
            errs.append(f"adéquation {adq} < 3"); continue
        ok.append(Link(tgt.folder, pi, pos, pos + len(anchor), src.paragraphs[pi][pos:pos + len(anchor)], niveau, preuve, adq,
                       str(it.get("reason", "")).strip(), pair_score))
    return ok, errs


def select(links: list[Link]) -> list[Link]:
    """Tranche entre cibles concurrentes et applique les quotas."""
    links = sorted(links, key=lambda l: (-l.adequation, -l.pair_score))
    kept: list[Link] = []
    per_t, per_p, anchors, sents = defaultdict(list), Counter(), set(), set()
    for l in links:
        key = stem_words(l.anchor)
        if key in anchors or per_p[l.para_idx] >= 2:
            continue
        if any(k.para_idx == l.para_idx and not (l.end <= k.start or l.start >= k.end) for k in kept):
            continue
        if per_t[l.target]:
            continue  # REGLE ABSOLUE : jamais deux liens de la source vers la meme URL cible
        if len(kept) >= MAX_LINKS_PER_SOURCE:
            break
        kept.append(l); per_t[l.target].append(l); per_p[l.para_idx] += 1; anchors.add(key)
    return sorted(kept, key=lambda l: (l.para_idx, l.start))


# ---------------------------------------------------------------- rapport
def write_report(src: Article, arts: dict, kept: list[Link], examined: list[dict]) -> None:
    byt = defaultdict(list)
    for l in kept:
        byt[l.target].append(l)
    L = [f"# Maillage interne — {src.title}", "",
         f"- Article source : `{src.slug}`", f"- URL : {src.url or '(non publié / inconnu)'}",
         f"- {len(kept)} liens proposés vers {len(byt)} articles ({len(examined)} cibles lues en entier)", "",
         "## Liens proposés", ""]
    if not kept:
        L += ["Aucun lien n'a passé la grille de pertinence.", ""]
    for tid in sorted(byt, key=lambda x: arts[x].title):
        t = arts[tid]
        L += [f"### → {t.title}", "", f"URL cible : {t.url or t.slug}  ",
              f"Intention de la page : {t.expression or t.title}", ""]
        for i, l in enumerate(byt[tid], 1):
            L += [f"**{i}. Ancre : « {l.anchor} »** — adéquation {l.adequation}/5 ({l.niveau})", "",
                  f"- Pourquoi : {l.reason}",
                  f"- Preuve dans la cible : « {l.preuve} »",
                  f"- Section : {src.para_section[l.para_idx] or '(introduction)'}",
                  f"- Contexte : {sentence_around(src.paragraphs[l.para_idx], l.start, l.end)}", ""]
    L += ["## Cibles lues sans lien", ""]
    for e in examined:
        if e["target"] not in byt:
            L.append(f"- {arts[e['target']].title} (score de paire {e['score']:.2f}) — {e['motif'] or 'aucun lien assez solide'}")
    if not any(e["target"] not in byt for e in examined):
        L.append("- (aucune)")
    dossier = WORK_DIR / src.folder
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / f"{src.folder}.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    (dossier / f"{src.folder}.json").write_text(json.dumps({
        "source": src.slug, "url": src.url,
        "links": [{"target": arts[l.target].slug, "target_url": arts[l.target].url, "anchor": l.anchor,
                   "section": src.para_section[l.para_idx], "paragraph_index": l.para_idx,
                   "context": sentence_around(src.paragraphs[l.para_idx], l.start, l.end),
                   "adequation": l.adequation, "niveau": l.niveau, "preuve_cible": l.preuve, "reason": l.reason}
                  for l in kept],
        "cibles_examinees": [{"target": arts[e["target"]].slug, "score_paire": e["score"], "motif_sans_lien": e["motif"],
                              "propositions_rejetees": e["rejets"]} for e in examined]},
        ensure_ascii=False, indent=2), encoding="utf-8")


def write_index(arts: list[Article]) -> None:
    """INDEX.md : une ligne par article (date + heure, source, liens, cibles, adéquation ≥ 4), plus récent en premier.

    Trois colonnes Oui/Non, lues sur le disque à chaque écriture :
    - Fichiers d'indexation : work/<slug>/chunks.json et state.json présents ;
    - Fichiers de maillage : work/<slug>/<slug>.md et <slug>.json présents ;
    - Liens publiés : published/<slug>.json présent avec au moins un lien posé (écrit par /publish-maillage).

    Date = date du rapport s'il existe, sinon date du dossier de travail. Article sans rapport : colonnes à « — ».
    """
    rows = []
    for a in arts:
        dossier = WORK_DIR / a.folder
        if not dossier.exists():  # article pas encore passé par build_index : absent de l'index
            continue
        rapport = dossier / f"{a.folder}.json"
        analyse = rapport.exists() and "cibles_examinees" in load_json(rapport, {})
        when = datetime.fromtimestamp((rapport if analyse else dossier).stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        if analyse:
            links = load_json(rapport, {})["links"]
            cols = f"{len(links)} | {len({l['target'] for l in links})} | {sum(l['adequation'] >= 4 for l in links)}"
        else:
            cols = "— | — | —"
        indexation = (dossier / "chunks.json").exists() and (dossier / "state.json").exists()
        maillage = (dossier / f"{a.folder}.md").exists() and rapport.exists()
        pub = PUBLISHED_DIR / f"{a.folder}.json"
        publie = pub.exists() and any(l.get("status") == "pose" for l in load_json(pub, {}).get("liens", []))
        oui_non = lambda ok: "Oui" if ok else "Non"
        cols += f" | {oui_non(indexation)} | {oui_non(maillage)} | {oui_non(publie)}"
        rows.append((when, f"| {when} | `{a.folder}` | {cols} |"))
    rows.sort(key=lambda r: r[0], reverse=True)
    head = ["# Maillage interne — index des rapports", "", f"Articles analysés : **{len(rows)}**", "",
            "| Mis à jour | Article source | Liens proposés | Cibles distinctes | Adéquation ≥ 4 "
            "| Fichiers d'indexation | Fichiers de maillage | Liens publiés |", "|---|---|---|---|---|---|---|---|"]
    (MAILLAGE_DIR / "INDEX.md").write_text("\n".join(head + [r[1] for r in rows]) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("which", nargs="?", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--max-targets", type=int, default=None, help="limite le nombre de cibles lues par source")
    ap.add_argument("--show-pairs", action="store_true", help="affiche les paires et le cout estime, sans appel LLM")
    ap.add_argument("--index", action="store_true", help="regenere INDEX.md seul, sans appel LLM ni reseau")
    args = ap.parse_args()

    folders = sorted(d for d in CONTENT_DIR.iterdir() if (d / "article.md").exists())
    arts_list = [load_article(d) for d in folders]
    arts = {a.folder: a for a in arts_list}
    if args.index:
        write_index(arts_list)
        print(f"INDEX.md regenere : {MAILLAGE_DIR / 'INDEX.md'}")
        return 0
    if not args.all and not args.which:
        print("ERREUR: indique un slug, un numéro ou --all", file=sys.stderr)
        return 1
    if args.all:
        sources = [a for a in arts_list if publie_sans_rapport(a.folder)]
        if not sources:
            print("Aucun article publié sans rapport de maillage : rien à faire.")
            return 0
    elif args.which.isdigit():
        sources = [arts_list[int(args.which) - 1]]
    elif args.which in arts:
        sources = [arts[args.which]]
    else:
        print(f"ERREUR: article inconnu : {args.which}", file=sys.stderr)
        return 1
    if not (PAIRS_DIR / f"{sources[0].folder}.json").exists():
        print("ERREUR: paires introuvables — lance d'abord etape2_pairs.py", file=sys.stderr)
        return 1

    urls = {} if args.offline else fetch_urls()
    if not urls and not args.offline:
        print("ERREUR: aucune URL WordPress récupérée (API injoignable ?) : arrêt, aucun rapport écrit", file=sys.stderr)
        return 1
    for a in arts_list:
        a.url = urls.get(a.slug, "")
    read = set()
    n_pairs = 0
    for src in sources:
        tg = cibles_publiees(load_json(PAIRS_DIR / f"{src.folder}.json", {"targets": []})["targets"], arts)
        if args.max_targets:
            tg = tg[:args.max_targets]
        read |= {src.folder} | {p["target"] for p in tg}
        n_pairs += len(tg)
    print(f"{len(read)} article(s) lus en entier : {len(sources)} source(s) + {n_pairs} paire(s) source-cible "
          f"(corpus : {len(arts_list)} articles, {sum(1 for a in arts_list if a.url)} URL WordPress résolues)")
    if args.show_pairs:
        # Meme mise en forme markdown que rag_data/maillage/pairs/PAIRES_URLS.md : a relayer telle quelle.
        for src in sources:
            pairs = cibles_publiees(load_json(PAIRS_DIR / f"{src.folder}.json", {"targets": []})["targets"], arts)
            if args.max_targets:
                pairs = pairs[:args.max_targets]
            print(f"\n##{arts_list.index(src) + 1}. {src.title}")
            print(f"Source : {src.url or src.slug}")
            print(f"{len(pairs)} cible(s)\n")
            for p in pairs:
                tgt = arts[p["target"]]
                concepts = ", ".join(x["concept_cible"] for x in p["shared"][:5])
                print(f"- **{p['score']:.2f}** — {tgt.title} — {tgt.url or tgt.slug}  ")
                print(f"  concepts : {concepts}")
        return 0

    cache = load_json(CACHE_PATH, {})
    failed = 0
    try:
        for src in sources:
            pairs = cibles_publiees(load_json(PAIRS_DIR / f"{src.folder}.json", {"targets": []})["targets"], arts)
            if args.max_targets:
                pairs = pairs[:args.max_targets]
            all_links: list[Link] = []
            examined = []
            incomplete = False
            for p in pairs:
                tgt = arts[p["target"]]
                res = call_pair(src, tgt, p["shared"], cache)
                if res is None:
                    failed += 1
                    incomplete = True
                    continue
                ok, errs = validate(src, tgt, res["links"], p["score"])
                all_links += ok
                examined.append({"target": tgt.folder, "score": p["score"], "motif": res["motif"], "rejets": errs})
                print(f"  {src.folder[:30]} -> {tgt.folder[:30]} : {len(ok)} lien(s) valide(s), {len(errs)} rejet(s)")
            if incomplete:
                print(f"[{src.folder}] incomplet (appel en échec) : pas de rapport", file=sys.stderr)
                continue
            try:
                verdicts = critic(src, all_links, arts, cache)
            except ClaudeCodeCallError as exc:
                print(f"[{src.folder}] critique en échec ({exc}) : pas de rapport", file=sys.stderr)
                failed += 1
                continue
            survivors = []
            for i, l in enumerate(all_links, 1):
                ok_, motif = verdicts.get(i, (False, "pas de verdict"))
                if ok_:
                    survivors.append(l)
                else:
                    for e in examined:
                        if e["target"] == l.target:
                            e["rejets"].append(f"critique : « {l.anchor} » n'est pas le nom du sujet de la page ({motif})")
                    print(f"  critique : « {l.anchor} » -> {l.target[:30]} ECARTE ({motif})")
            kept = select(survivors)
            write_report(src, arts, kept, examined)
            write_index(arts_list)
            print(f"[{src.folder}] {1 + len(pairs)} article(s) lus (1 source + {len(pairs)} cible(s)), {len(kept)} liens retenus vers {len({l.target for l in kept})} articles "
                  f"-> rag_data/maillage/work/{src.folder}/{src.folder}.md")
    except TimeUp:
        print("Temps d'invocation écoulé (progression en cache) : relance exactement la même commande pour continuer.")
        return 3
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
