"""Applique sur WordPress le maillage interne propose par /maillage.

Pour un article source, lit le rapport rag_data/maillage/work/<dossier>/<dossier>.json
(liens : ancre, URL cible, contexte), recupere le post WordPress en ligne
(retrouve par son slug, lu dans l'URL du rapport) et y insere chaque lien :

    <a href="/blog/maths/le-coefficient-de-determination/" title="ancre">ancre</a>

Le href est RELATIF (le domaine est retire de l'URL cible).

Regles d'insertion (le script ne devine jamais : en cas de doute, il saute le
lien et le signale) :
  - l'ancre est cherchee dans le texte du post, sans tenir compte de la casse ni
    des accents, en mots entiers ; le texte original de la page est conserve ;
  - parmi plusieurs occurrences, celle dont le paragraphe ressemble le plus a la
    phrase de contexte du rapport est retenue ;
  - jamais dans un lien existant, un titre (h1-h6), un bloc de code, une
    legende d'image, une formule LaTeX, ni dans un commentaire / attribut HTML ;
  - un seul lien par URL cible : si le post pointe deja vers cette page, le lien
    est ignore (le script est donc relancable sans doublon) ;
  - le HTML doit rester equilibre (l'ancre peut contenir ou etre contenue
    dans <strong>/<em> sans casser les balises).

Avant toute ecriture, le contenu d'origine est sauvegarde dans
rag_data/maillage/backup/. Le statut du post n'est pas modifie.

Usage :
    publish_maillage.py <slug|numero|--all> [--dry-run]

Necessite le .env de /publish-content (WP_URL, WP_USER, WP_APP_PASSWORD).
"""
from __future__ import annotations
# Garde-fou : force l'interpreteur du projet (.venv-rag), voir _rag_lib/venv_guard.py
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(next(p for p in _Path(__file__).resolve().parents if (p / ".claude" / "skills" / "_rag_lib").is_dir()) / ".claude" / "skills" / "_rag_lib"))
import venv_guard  # noqa: F401

import argparse
import html
import json
import re
import subprocess
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[4]
CONTENT_DIR = ROOT / "rag_data" / "content" / "work"
CONTENT_PUBLISHED_DIR = ROOT / "rag_data" / "content" / "published"  # etat WordPress : <slug>.json, wp_status == "publish"
MAILLAGE_DIR = ROOT / "rag_data" / "maillage"
WORK_DIR = MAILLAGE_DIR / "work"  # rapports de /maillage : work/<slug>/<slug>.json
BACKUP_DIR = MAILLAGE_DIR / "backup"
PUBLISHED_DIR = MAILLAGE_DIR / "published"
INDEX_SCRIPT = ROOT / ".claude" / "skills" / "maillage" / "scripts" / "etape3_links.py"
ENV_PATH =ROOT / ".claude" / "skills" / "publish-content" / "scripts" / ".env"

MASK = "\u0001"  # remplace les formules dans la copie de travail (meme longueur)
# Memes motifs que publish_to_wordpress.py : $$...$$ puis $...$ sans espace colle.
_MATH_DISPLAY_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_MATH_INLINE_RE = re.compile(r"(?<!\$)\$(?!\$|\s)([^\n$]+?)(?<!\s)(?<!\$)\$(?!\$)")

TOKEN_RE = re.compile(r"<!--.*?-->|<[^>]+>|[^<]+", re.DOTALL)
TAG_NAME_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)")
CHAR_RE = re.compile(r"&(?:#\d+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);|.", re.DOTALL)

PROTECTED_TAGS = {"a", "h1", "h2", "h3", "h4", "h5", "h6", "code", "pre", "kbd",
                  "figcaption", "script", "style", "button", "textarea", "svg"}
BLOCK_TAGS = {"p", "li", "ul", "ol", "td", "th", "tr", "table", "thead", "tbody",
              "div", "blockquote", "figure", "section", "article", "br", "hr",
              "h1", "h2", "h3", "h4", "h5", "h6", "dd", "dt", "dl", "details", "summary"}
VOID_TAGS = {"br", "hr", "img", "input", "meta", "link", "source", "wbr"}

MIN_SCORE = 0.5          # ressemblance minimale paragraphe <-> contexte
TIMEOUT_LECTURE = 60     # secondes, lecture du post (GET)
TIMEOUT_ECRITURE = 90    # secondes, mise a jour du post (POST)

def load_wp_config() -> dict:
    if not ENV_PATH.exists():
        print(f"ERREUR : {ENV_PATH} introuvable (c'est le .env de /publish-content : "
              "WP_URL, WP_USER, WP_APP_PASSWORD).", file=sys.stderr)
        sys.exit(1)
    cfg = dotenv_values(ENV_PATH)
    missing = [k for k in ("WP_URL", "WP_USER", "WP_APP_PASSWORD") if not cfg.get(k)]
    if missing:
        print(f"ERREUR : variables manquantes dans .env : {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    cfg["WP_URL"] = cfg["WP_URL"].rstrip("/")
    return cfg


# --------------------------------------------------------------------------- #
# Normalisation et flux de texte
# --------------------------------------------------------------------------- #
def norm_char(c: str) -> str:
    """Caractere -> forme de comparaison (minuscule, sans accent, espace unique).
    Peut renvoyer '' (diacritique isole) ou plusieurs caracteres (ligature)."""
    if c == MASK:
        return MASK
    if c.isspace() or c == " " or c == " ":
        return " "
    if c in "’‘ʼ`":
        return "'"
    d = unicodedata.normalize("NFKD", c)
    d = "".join(ch for ch in d if not unicodedata.combining(ch))
    return d.lower()


def norm_text(text: str) -> str:
    out: list[str] = []
    for c in text:
        for n in norm_char(c):
            if n == " " and out and out[-1] == " ":
                continue
            out.append(n)
    return "".join(out).strip()


def mask_math(raw: str) -> str:
    """Copie de `raw` de meme longueur, formules remplacees par MASK."""
    def repl(m: re.Match) -> str:
        return MASK * len(m.group(0))
    return _MATH_INLINE_RE.sub(repl, _MATH_DISPLAY_RE.sub(repl, raw))


class Stream:
    """Texte visible du post, avec pour chaque caractere sa position dans le
    HTML brut, son bloc (paragraphe, item de liste...) et s'il est protege."""

    def __init__(self, raw: str):
        self.chars: list[str] = []
        self.start: list[int] = []
        self.end: list[int] = []
        self.block: list[int] = []
        self.protected: list[bool] = []
        self.block_words: dict[int, set[str]] = {}
        self.existing_hrefs: list[str] = []
        self._build(raw)

    def _build(self, raw: str) -> None:
        work = mask_math(raw)
        stack: list[str] = []
        prot_depth = 0
        block = 0
        pos = 0
        for m in TOKEN_RE.finditer(work):
            tok = m.group(0)
            tstart = m.start()
            if tok.startswith("<!--"):
                pos = m.end()
                continue
            if tok.startswith("<"):
                tm = TAG_NAME_RE.match(tok)
                if tm:
                    closing, name = tm.group(1) == "/", tm.group(2).lower()
                    if name in BLOCK_TAGS:
                        block += 1
                    if name == "a" and not closing:
                        hm = re.search(r'href\s*=\s*"([^"]*)"|href\s*=\s*\'([^\']*)\'', tok)
                        if hm:
                            self.existing_hrefs.append(html.unescape(hm.group(1) or hm.group(2) or ""))
                    if not closing and not tok.endswith("/>") and name not in VOID_TAGS:
                        stack.append(name)
                        if name in PROTECTED_TAGS:
                            prot_depth += 1
                    elif closing and name in stack:
                        while stack:  # depile jusqu'a la balise ouvrante correspondante
                            top = stack.pop()
                            if top in PROTECTED_TAGS:
                                prot_depth -= 1
                            if top == name:
                                break
                pos = m.end()
                continue
            # noeud de texte
            for cm in CHAR_RE.finditer(tok):
                s, e = tstart + cm.start(), tstart + cm.end()
                ch = html.unescape(cm.group(0)) if cm.group(0).startswith("&") else cm.group(0)
                for n in norm_char(ch):
                    if n == " " and self.chars and self.chars[-1] == " " and self.block[-1] == block:
                        continue
                    self.chars.append(n)
                    self.start.append(s)
                    self.end.append(e)
                    self.block.append(block)
                    self.protected.append(prot_depth > 0)
        for i, ch in enumerate(self.chars):
            self.block_words.setdefault(self.block[i], set())
        by_block: dict[int, list[str]] = {}
        for i, ch in enumerate(self.chars):
            by_block.setdefault(self.block[i], []).append(ch if ch != MASK else " ")
        for b, cs in by_block.items():
            self.block_words[b] = set(re.findall(r"[a-z0-9]{4,}", "".join(cs)))
        self.text = "".join(self.chars)

    def occurrences(self, anchor_norm: str) -> list[tuple[int, int]]:
        """Indices [i, j) des occurrences valides : mots entiers, meme bloc,
        aucun caractere protege ni formule."""
        found = []
        if not anchor_norm:
            return found
        n = len(anchor_norm)
        i = self.text.find(anchor_norm)
        while i != -1:
            j = i + n
            before_ok = i == 0 or not self.text[i - 1].isalnum()
            after_ok = j >= len(self.text) or not self.text[j].isalnum()
            if (before_ok and after_ok
                    and len(set(self.block[i:j])) == 1
                    and not any(self.protected[i:j])
                    and MASK not in self.text[i:j]):
                found.append((i, j))
            i = self.text.find(anchor_norm, i + 1)
        return found


def context_words(context: str) -> set[str]:
    ctx = _MATH_INLINE_RE.sub(" ", _MATH_DISPLAY_RE.sub(" ", context)).replace("*", " ")
    return set(re.findall(r"[a-z0-9]{4,}", norm_text(ctx)))


def balanced(fragment: str) -> bool:
    """Les balises du fragment HTML sont-elles equilibrees (rien qui deborde) ?"""
    stack: list[str] = []
    for m in re.finditer(r"<[^>]+>", re.sub(r"<!--.*?-->", "", fragment, flags=re.DOTALL)):
        tok = m.group(0)
        tm = TAG_NAME_RE.match(tok)
        if not tm:
            continue
        closing, name = tm.group(1) == "/", tm.group(2).lower()
        if name in VOID_TAGS or tok.endswith("/>"):
            continue
        if not closing:
            stack.append(name)
        else:
            if not stack or stack[-1] != name:
                return False
            stack.pop()
    return not stack


def relative_url(target_url: str) -> str:
    p = urlparse(target_url)
    return p.path + (f"?{p.query}" if p.query else "")


def already_linked(hrefs: list[str], target_url: str) -> bool:
    path = urlparse(target_url).path.rstrip("/")
    for h in hrefs:
        hp = urlparse(h).path.rstrip("/")
        if hp and hp == path:
            return True
    return False


def strip_anchor_tags(raw: str, s: int, e: int) -> tuple[str, int, int] | None:
    """Ancre [s, e) qui chevauche des balises : retire les balises de l'ancre ET
    leurs balises jumelles hors de l'ancre (ex. `memoire <strong>param` + `</strong>`),
    pour que le HTML reste equilibre. Retourne (html, s, e) ou None si impossible."""
    tag_re = re.compile(r"<[^>]+>")
    frag = raw[s:e]
    work = mask_math(raw)  # formules masquees (meme longueur) : leur '<' n'est pas une balise
    stack: list[str] = []
    unmatched_closers: list[str] = []
    masked = re.sub(r"<!--.*?-->", lambda x: " " * len(x.group(0)), work[s:e], flags=re.DOTALL)
    for m in tag_re.finditer(masked):
        tok = m.group(0)
        tm = TAG_NAME_RE.match(tok)
        if not tm or tok.endswith("/>"):
            continue
        closing, name = tm.group(1) == "/", tm.group(2).lower()
        if name in VOID_TAGS:
            continue
        if not closing:
            stack.append(name)
        elif stack and stack[-1] == name:
            stack.pop()
        elif not stack:
            unmatched_closers.append(name)
        else:
            return None
    plain = tag_re.sub("", frag)
    edits: list[tuple[int, int, str]] = [(s, e, plain)]

    def tags(text: str, offset: int):
        for m in tag_re.finditer(text):
            tm = TAG_NAME_RE.match(m.group(0))
            if tm and not m.group(0).endswith("/>"):
                yield offset + m.start(), offset + m.end(), tm.group(1) == "/", tm.group(2).lower()

    after = list(tags(work[e:], e))
    for name in stack:  # ouvertes dans l'ancre : retirer la fermante qui suit
        depth = 0
        for a, b, closing, n in after:
            if n != name:
                continue
            if not closing:
                depth += 1
            elif depth == 0:
                edits.append((a, b, ""))
                after = [x for x in after if x[0] != a]
                break
            else:
                depth -= 1
        else:
            return None
    before = list(tags(work[:s], 0))
    for name in unmatched_closers:  # fermees dans l'ancre : retirer l'ouvrante qui precede
        depth = 0
        for a, b, closing, n in reversed(before):
            if n != name:
                continue
            if closing:
                depth += 1
            elif depth == 0:
                edits.append((a, b, ""))
                before = [x for x in before if x[0] != a]
                break
            else:
                depth -= 1
        else:
            return None
    removed_before = sum(b - a for a, b, rep in edits if b <= s)
    new = raw
    for a, b, rep in sorted(edits, key=lambda x: x[0], reverse=True):
        new = new[:a] + rep + new[b:]
    s2 = s - removed_before
    return new, s2, s2 + len(plain)


def insert_link(raw: str, link: dict) -> tuple[str | None, dict]:
    """Insere un lien. Retourne (nouveau_html | None, info). info['reason'] dit
    pourquoi un lien est saute."""
    info: dict = {"anchor": link["anchor"], "target_url": link["target_url"]}
    stream = Stream(raw)

    if already_linked(stream.existing_hrefs, link["target_url"]):
        info["status"] = "deja_lie"
        info["reason"] = "le post contient deja un lien vers cette page"
        return None, info

    anchor_norm = norm_text(link["anchor"])
    occs = stream.occurrences(anchor_norm)
    if not occs:
        info["status"] = "introuvable"
        info["reason"] = "ancre absente du texte lie-able (titre, code, formule, lien existant ou texte modifie ?)"
        return None, info

    ctx = context_words(link.get("context", ""))
    scored = []
    for i, j in occs:
        words = stream.block_words.get(stream.block[i], set())
        score = len(ctx & words) / len(ctx) if ctx else 0.0
        scored.append((score, i, j))
    best = max(scored, key=lambda t: (t[0], -t[1]))
    score, i, j = best
    # Une seule occurrence valable : on la lie meme si la phrase de contexte du
    # rapport est introuvable dans le post. Plusieurs : il faut un contexte qui colle.
    if len(occs) > 1 and score < MIN_SCORE:
        info["status"] = "ambigu"
        info["reason"] = (f"{len(occs)} occurrences mais aucun paragraphe ne ressemble assez "
                          f"au contexte du rapport (meilleur score {score:.2f} < {MIN_SCORE})")
        return None, info

    s, e = stream.start[i], stream.end[j - 1]
    # Ancre toujours "a plat" : balises retirees de l'ancre (et leurs jumelles hors
    # de l'ancre), pour que tous les liens aient le meme rendu.
    stripped = False
    if "<" in raw[s:e]:
        fixed = strip_anchor_tags(raw, s, e)
        if fixed is None:
            info["status"] = "html_desequilibre"
            info["reason"] = "l'ancre contient des balises dont les jumelles sont introuvables"
            return None, info
        raw, s, e = fixed
        stripped = True
    # Enveloppe de mise en forme qui ne contient QUE l'ancre (<em>retriever</em>) : retiree aussi.
    while True:
        mo = re.search(r"<(strong|em|b|i)(?:\s[^>]*)?>$", raw[:s], re.IGNORECASE)
        if not mo or not raw[e:].lower().startswith(f"</{mo.group(1).lower()}>"):
            break
        close_len = len(mo.group(1)) + 3
        raw = raw[:mo.start()] + raw[s:e] + raw[e + close_len:]
        s, e = mo.start(), mo.start() + (e - s)
        stripped = True
    if not balanced(raw[s:e]):
        info["status"] = "html_desequilibre"
        info["reason"] = "l'ancre chevauche des balises, nettoyage impossible"
        return None, info
    if stripped:
        info["stripped"] = True

    shown = raw[s:e]
    plain = html.unescape(re.sub(r"<[^>]+>", "", shown)).strip()
    href = relative_url(link["target_url"])
    open_tag = f'<a href="{html.escape(href, quote=True)}" title="{html.escape(plain, quote=True)}">'
    new = raw[:s] + open_tag + shown + "</a>" + raw[e:]
    info.update(status="pose", score=round(score, 2), occurrences=len(occs), href=href,
                before=raw[max(0, s - 70):s], shown=shown, after=raw[e:e + 70],
                open_tag=open_tag)
    return new, info


# --------------------------------------------------------------------------- #
# WordPress
# --------------------------------------------------------------------------- #
def fetch_post(cfg: dict, slug: str) -> dict:
    resp = requests.get(
        f"{cfg['WP_URL']}/wp-json/wp/v2/posts",
        params={"slug": slug, "context": "edit", "status": "any",
                "_fields": "id,link,slug,status,content,modified"},
        auth=(cfg["WP_USER"], cfg["WP_APP_PASSWORD"]), timeout=TIMEOUT_LECTURE,
    )
    print(f"GET /wp/v2/posts?slug={slug} -> {resp.status_code}")
    if resp.status_code != 200:
        print(resp.text)
        sys.exit(1)
    posts = resp.json()
    if len(posts) != 1:
        print(f"ERREUR : {len(posts)} post(s) trouve(s) pour le slug {slug!r} (1 attendu).", file=sys.stderr)
        sys.exit(1)
    return posts[0]


def update_post(cfg: dict, post_id: int, content: str) -> dict:
    resp = requests.post(
        f"{cfg['WP_URL']}/wp-json/wp/v2/posts/{post_id}",
        json={"content": content},  # pas de "status" : le statut reste inchange
        params={"context": "edit", "_fields": "id,link,status,content,modified"},
        auth=(cfg["WP_USER"], cfg["WP_APP_PASSWORD"]), timeout=TIMEOUT_ECRITURE,
    )
    print(f"POST /wp/v2/posts/{post_id} -> {resp.status_code}")
    if resp.status_code not in (200, 201):
        print(resp.text)
        sys.exit(1)
    return resp.json()


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def report_path(folder: str) -> Path:
    """Rapport JSON d'un article produit par /maillage : work/<slug>/<slug>.json."""
    return WORK_DIR / folder / f"{folder}.json"


def is_published(folder: str) -> bool:
    """Liens publiés = trace published/<slug>.json avec au moins un lien posé (même règle que INDEX.md)."""
    pub = PUBLISHED_DIR / f"{folder}.json"
    return pub.exists() and any(l.get("status") == "pose"
                                for l in json.loads(pub.read_text(encoding="utf-8")).get("liens", []))


def est_publie(folder: str) -> bool:
    """Article publié sur WordPress : rag_data/content/published/<slug>.json avec wp_status == "publish"."""
    etat = CONTENT_PUBLISHED_DIR / f"{folder}.json"
    return etat.exists() and json.loads(etat.read_text(encoding="utf-8")).get("wp_status") == "publish"


def resolve_targets(arg: str) -> list[str]:
    folders = sorted(p.name for p in CONTENT_DIR.iterdir() if p.is_dir())
    if arg == "--all":
        # Seuls les articles publiés sur WordPress dont le maillage n'a pas encore été posé (même règle que --list).
        return [f for f in folders if report_path(f).exists() and est_publie(f) and not is_published(f)]
    if arg.isdigit():
        n = int(arg)
        if not 1 <= n <= len(folders):
            print(f"ERREUR : numero {n} hors de 1..{len(folders)}.", file=sys.stderr)
            sys.exit(1)
        folder = folders[n - 1]
    else:
        if arg not in folders:
            print(f"ERREUR : {arg!r} n'est pas un dossier de rag_data/content/.", file=sys.stderr)
            sys.exit(1)
        folder = arg
    if not est_publie(folder):
        print(f"ERREUR : {folder!r} n'est pas publié sur WordPress (pas de wp_status \"publish\" dans "
              f"rag_data/content/published/). Rien n'est fait.", file=sys.stderr)
        sys.exit(1)
    return [folder]


def process(folder: str, cfg: dict, dry_run: bool) -> dict:
    rapport = report_path(folder)
    if not rapport.exists():
        print(f"ERREUR : rapport {rapport} introuvable (lance /maillage {folder}).", file=sys.stderr)
        return {"folder": folder, "error": "rapport absent"}
    report = json.loads(rapport.read_text(encoding="utf-8"))
    links = report.get("links", [])
    wp_slug = urlparse(report["url"]).path.rstrip("/").rsplit("/", 1)[-1]

    print(f"\n=== {folder} ===")
    print(f"URL source : {report['url']}")
    print(f"Liens dans le rapport : {len(links)}")
    if not links:
        return {"folder": folder, "poses": 0, "ignores": 0}

    site_host = urlparse(cfg["WP_URL"]).netloc.lower().removeprefix("www.")
    post = fetch_post(cfg, wp_slug)
    raw = post["content"]["raw"]
    print(f"Post #{post['id']} ({post['status']}) : {post['link']}")
    if urlparse(post["link"]).path.rstrip("/") != urlparse(report["url"]).path.rstrip("/"):
        print(f"ATTENTION : le lien du post ({post['link']}) differe de l'URL du rapport.", file=sys.stderr)

    current = raw
    results = []
    for n, link in enumerate(links, 1):
        if urlparse(link["target_url"]).netloc.lower().removeprefix("www.") != site_host:
            print(f"ATTENTION : cible hors du site ({link['target_url']}), lien sans domaine tout de meme.",
                  file=sys.stderr)
        new, info = insert_link(current, link)
        results.append(info)
        label = f"[{n}/{len(links)}] « {link['anchor']} » -> {relative_url(link['target_url'])}"
        if new is None:
            print(f"{label}\n    IGNORE ({info['status']}) : {info['reason']}")
            continue
        current = new
        sh = re.sub(r"\s+", " ", info["shown"])
        print(f"{label}\n    POSE (score contexte {info['score']}, {info['occurrences']} occurrence(s))"
              + (" - balises de l'ancre retirees" if info.get("stripped") else ""))
        print(f"    ...{re.sub(chr(10), ' ', info['before'])}{info['open_tag']}{sh}</a>"
              f"{re.sub(chr(10), ' ', info['after'])}...")

    poses = [r for r in results if r["status"] == "pose"]
    ignores = [r for r in results if r["status"] != "pose"]
    print(f"\nBilan {folder} : {len(poses)} lien(s) pose(s), {len(ignores)} ignore(s)"
          + (" (dry-run : rien n'est ecrit)" if dry_run else "") + ".")

    out = {"folder": folder, "poses": len(poses), "ignores": len(ignores)}
    if dry_run or not poses:
        return out

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = BACKUP_DIR / f"{wp_slug}-{stamp}.html"
    backup.write_text(raw, encoding="utf-8")
    print(f"Sauvegarde du contenu d'origine : {backup}")

    updated = update_post(cfg, post["id"], current)
    new_raw = updated.get("content", {}).get("raw")
    if new_raw is not None and new_raw != current:
        print("ATTENTION : WordPress a modifie le contenu a l'enregistrement "
              "(filtrage HTML ?). Verifier le post dans l'editeur.", file=sys.stderr)
    final = new_raw if new_raw is not None else current
    missing = [r["href"] for r in poses if f'href="{r["href"]}"' not in final]
    if missing:
        print(f"ATTENTION : lien(s) absent(s) du contenu enregistre : {missing}", file=sys.stderr)
    print(f"Statut du post : {updated.get('status')} (inchange) - {updated.get('link')}")

    PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
    (PUBLISHED_DIR / f"{folder}.json").write_text(json.dumps({
        "folder": folder, "wp_post_id": post["id"], "url": report["url"], "date": stamp,
        "backup": str(backup.relative_to(ROOT)),
        "liens": [{k: r[k] for k in ("anchor", "target_url", "href", "status", "reason") if k in r}
                  for r in results],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    # Colonne « Liens publiés » de INDEX.md : régénérée par etape3_links.py --index (sans appel LLM).
    refresh = subprocess.run([sys.executable, str(INDEX_SCRIPT), "--index"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
    if refresh.returncode != 0:
        print(f"ATTENTION : INDEX.md non mis à jour : {refresh.stderr.strip()}", file=sys.stderr)
    return out


def list_reports() -> None:
    """Articles publiés sur WordPress, dont le maillage est généré (work/<slug>/<slug>.md et .json)
    mais pas encore posé.

    Numéro = celui de /maillage (ordre alphabétique des dossiers de rag_data/content/work/).
    """
    folders = sorted(p.name for p in CONTENT_DIR.iterdir() if p.is_dir())
    rows = []
    for n, f in enumerate(folders, 1):
        rp = report_path(f)
        if not (rp.exists() and (WORK_DIR / f / f"{f}.md").exists()):
            continue
        if not est_publie(f) or is_published(f):
            continue
        nb = len(json.loads(rp.read_text(encoding="utf-8")).get("links", []))
        rows.append(f"n°{n} : {f} ({nb} lien(s))")
    print(f"Articles avec maillage généré et non publié : {len(rows)}")
    print("\n".join(rows))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", nargs="?", help="slug (dossier de rag_data/content/) ou numero")
    parser.add_argument("--all", action="store_true",
                        help="Traite tous les articles publies avec un rapport de maillage non encore pose.")
    parser.add_argument("--list", action="store_true",
                        help="Liste les articles qui ont un rapport de maillage et exit.")
    parser.add_argument("--dry-run", action="store_true",
                        help="N'ecrit rien : affiche chaque lien avec son contexte avant/apres.")
    args = parser.parse_args()
    if args.all:
        if args.target:
            parser.error("--all ne se combine pas avec un slug ou un numero.")
        args.target = "--all"

    if args.list or not args.target:
        list_reports()
        return 0
    cfg = load_wp_config()
    folders = resolve_targets(args.target)
    if not folders:
        print("Aucun rapport de maillage a appliquer.")
        return 0
    totals = [process(f, cfg, args.dry_run) for f in folders]
    if len(folders) > 1:
        print("\n=== Total ===")
        for t in totals:
            print(f"- {t['folder']} : " + (t["error"] if "error" in t
                  else f"{t['poses']} pose(s), {t['ignores']} ignore(s)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
