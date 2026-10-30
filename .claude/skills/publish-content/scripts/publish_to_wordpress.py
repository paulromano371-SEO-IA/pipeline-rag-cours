"""Publie un article genere par /create-content (rag_data/content/work/<slug>/)
sur WordPress via l'API REST (wp-json), images comprises.

Pour chaque image referencee dans article.md :
  - Texte alternatif = le texte alt ecrit par l'article (accessible, deja
    redige pour un lecteur qui ne voit pas l'image).
  - Legende (caption WP, affichee sous l'image sur le site) = la phrase qui
    suit l'image dans article.md (« quoi regarder dedans », exigee par le
    skill create-content) -- elle est retiree du corps de l'article pour ne
    pas etre dupliquee, WordPress l'affiche via <figcaption>.
  - Description = la description technique detaillee produite par le
    pipeline RAG (dossier.json). Aucun des trois champs ne mentionne la source
    (livre, section).

Le corps de l'article (Markdown) est converti en HTML (blocs Gutenberg pour
les images). Les commentaires de tracabilite <!--src:...--> sont retires.
Les formules LaTeX ($...$ et $$...$$) sont preservees telles quelles dans le
contenu (protegees du rendu Markdown pour que backslashes/underscores ne
soient pas alteres), et le shortcode [latexpage] est ajoute en tete du post
si l'article en contient : c'est ce que le plugin WP QuickLaTeX attend pour
les rendre.

Usage :
    publish_to_wordpress.py <slug> [--dry-run]

<slug> : nom du dossier sous rag_data/content/work/ (celui produit par
/create-content). Necessite un fichier .env a la racine du projet avec
WP_URL, WP_USER, WP_APP_PASSWORD (voir .env.example).

L'article est toujours cree en **brouillon** sur le site (jamais publie
automatiquement) : ce script ne propose pas d'option pour le mettre en ligne.
Le passage en ligne se fait a la main, dans WordPress.
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
import sys
from datetime import datetime
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import requests
import yaml
from dotenv import dotenv_values
from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[4]
CONTENT_DIR = ROOT / "rag_data" / "content" / "work"
PUBLISHED_DIR = ROOT / "rag_data" / "content" / "published"  # traces : published/<slug>.json
CONTENT_INDEX = ROOT / "rag_data" / "content" / "INDEX.md"  # colonne « Article publié »

# Sommaire cliquable : les ancres du sommaire (ecrit par create-content/scripts/lib/toc.py)
# pointent vers des `id` que le convertisseur Markdown ne pose pas sur les H2 ;
# on les ajoute ici avec LA MEME fonction, pour que liens et ancres coincident.
sys.path.insert(0, str(ROOT / ".claude" / "skills" / "create-content" / "scripts" / "lib"))
import toc as toc_mod  # noqa: E402
import make_cover as make_cover_mod  # noqa: E402  (champs WP de la couverture)
ENV_PATH = Path(__file__).resolve().parent / ".env"

IMG_RE = re.compile(r"^!\[([^\]]*)\]\((images/[^)]+)\)\s*$")
SRC_COMMENT_RE = re.compile(r"<!--src:[^>]*-->")
INLINE_MD_RE = re.compile(r"[`*]")

# Formules LaTeX a proteger du rendu Markdown (backslashes, underscores...
# ne doivent pas etre interpretes) puis restituees telles quelles pour
# QuickLaTeX. $$...$$ d'abord (sinon le motif inline mangerait les $$).
# Le $ inline n'a jamais d'espace colle a l'interieur dans ce corpus ($X$,
# jamais $ X $ ni X $) -- contrairement aux montants en euros/dollars a la
# francaise ("1 000 $") qu'il ne faut surtout pas prendre pour des maths.
_MATH_DISPLAY_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_MATH_INLINE_RE = re.compile(r"(?<!\$)\$(?!\$|\s)([^\n$]+?)(?<!\s)(?<!\$)\$(?!\$)")
_MATH_PLACEHOLDER_RE = re.compile("(\\d+)")


# Jeton opaque pour un $ de montant (ex. "1 000 $") : PAS une entite HTML
# directement, car CommonMark decode les entites HTML presentes dans le texte
# source et les reemet telles quelles -- "&#36;" ecrit avant le rendu
# redeviendrait "$" apres coup, sans avoir rien protege.
_CURRENCY_PLACEHOLDER = ""


def md_plain(text: str) -> str:
    """Retire la ponctuation Markdown inline (`code`, **gras**) pour un champ
    qui n'accepte que du texte brut (alt, meta description). Ne touche pas
    aux underscores : trop presents dans les identifiants Python (__init__,
    __next__...) pour servir de marqueur d'emphase fiable ici."""
    return INLINE_MD_RE.sub("", text)


def protect_math(text: str, store: list[str]) -> str:
    """Remplace chaque formule $...$/$$...$$ par un jeton opaque au rendu
    Markdown (caracteres hors alphabet Markdown), et l'ajoute a `store` pour
    restitution ulterieure via restore_math()."""
    def repl(m: re.Match) -> str:
        store.append(m.group(0))
        return f"{len(store) - 1}"
    text = _MATH_DISPLAY_RE.sub(repl, text)
    text = _MATH_INLINE_RE.sub(repl, text)
    # Tout $ restant est, par construction, un montant ("1 000 $") jamais une
    # formule (deja capturee ci-dessus) -- mais le plugin WP QuickLaTeX fait
    # son propre scan de $...$ sur le contenu stocke, sans notre heuristique
    # "pas d'espace colle" : un montant non echappe s'apparie avec le $ de la
    # formule suivante et avale tout le texte entre les deux, provoquant une
    # erreur de compilation en cascade. On le remplace par un jeton opaque,
    # converti en entite HTML "&#36;" apres coup (build_content_html), une
    # fois le rendu Markdown termine -- voir _CURRENCY_PLACEHOLDER.
    text = text.replace("$", _CURRENCY_PLACEHOLDER)
    return text


def restore_math(content_html: str, store: list[str]) -> str:
    """Reinsere chaque formule telle quelle (PAS de html.escape : QuickLaTeX
    lit le contenu du post tel que stocke, un `&`/`<`/`>` echappe en entite
    HTML casserait tout environnement aligned/matrice ou toute inegalite)."""
    return _MATH_PLACEHOLDER_RE.sub(lambda m: store[int(m.group(1))], content_html)


def load_wp_config() -> dict:
    if not ENV_PATH.exists():
        print(f"ERREUR : {ENV_PATH} introuvable. Copie .env.example vers .env "
              "et renseigne WP_URL, WP_USER, WP_APP_PASSWORD.", file=sys.stderr)
        sys.exit(1)
    cfg = dotenv_values(ENV_PATH)
    missing = [k for k in ("WP_URL", "WP_USER", "WP_APP_PASSWORD") if not cfg.get(k)]
    if missing:
        print(f"ERREUR : variables manquantes dans .env : {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    cfg["WP_URL"] = cfg["WP_URL"].rstrip("/")
    return cfg


def load_article(slug: str) -> tuple[dict, str, Path]:
    content_dir = CONTENT_DIR / slug
    article_path = content_dir / "article.md"
    if not article_path.exists():
        print(f"ERREUR : {article_path} introuvable.", file=sys.stderr)
        sys.exit(1)
    raw = article_path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        print("ERREUR : article.md sans frontmatter YAML.", file=sys.stderr)
        sys.exit(1)
    end = raw.find("\n---", 3)
    if end == -1:
        print("ERREUR : frontmatter YAML sans '---' de fermeture dans article.md.", file=sys.stderr)
        sys.exit(1)
    frontmatter = yaml.safe_load(raw[3:end])
    body = raw[end + 4:].lstrip("\n")
    # article.md commence par "# <titre>", identique au titre du frontmatter
    # deja envoye dans le champ "title" du post WordPress. Sans ce retrait,
    # ce H1 se retrouve duplique dans le corps -- affiche sans style (le
    # theme ne colorie que les H2 de contenu) juste avant la premiere vraie
    # section, et repousse la premiere image hors de vue au-dessus d'elle.
    body_lines = body.split("\n", 1)
    if body_lines[0].startswith("# "):
        body = body_lines[1].lstrip("\n") if len(body_lines) > 1 else ""
    return frontmatter, body, content_dir


def load_image_metadata(content_dir: Path) -> dict:
    """web_path -> {alt (description technique riche), book, section}"""
    dossier_path = content_dir / "dossier.json"
    lookup = {}
    if not dossier_path.exists():
        return lookup
    dossier = json.loads(dossier_path.read_text(encoding="utf-8"))
    for item in dossier.get("items", []):
        for img in item.get("images", []):
            web_path = img.get("web_path")
            if web_path and web_path not in lookup:
                lookup[web_path] = {
                    "alt": img.get("alt", ""),
                    "book": item.get("book", ""),
                    "section": item.get("section", ""),
                }
    return lookup


def extract_images_and_strip_captions(body: str) -> tuple[str, list[dict]]:
    """Trouve chaque image, capture le paragraphe-legende qui suit (s'il y en
    a un) et le retire du corps pour eviter une duplication avec le champ
    Legende WordPress. Retourne (nouveau_corps, liste_images_dans_l_ordre)."""
    lines = body.split("\n")
    n = len(lines)
    images = []
    i = 0
    while i < n:
        m = IMG_RE.match(lines[i].strip())
        if not m:
            i += 1
            continue
        alt, rel = m.group(1), m.group(2)
        j = i + 1
        while j < n and lines[j].strip() == "":
            j += 1
        caption = None
        if j < n:
            candidate = lines[j].strip()
            if (candidate and not candidate.startswith("#")
                    and not candidate.startswith("```")
                    and not candidate.startswith("<!--")
                    and not IMG_RE.match(candidate)):
                cap_start = j
                para = []
                while j < n and lines[j].strip() != "":
                    para.append(lines[j].strip())
                    j += 1
                cap_end = j - 1
                caption = SRC_COMMENT_RE.sub("", " ".join(para)).strip()
                for k in range(cap_start, cap_end + 1):
                    lines[k] = ""
        images.append({"alt": alt, "rel": rel, "caption": caption})
        i = j
    new_body = SRC_COMMENT_RE.sub("", "\n".join(lines))
    new_body = re.sub(r"\n{3,}", "\n\n", new_body)
    return new_body, images


def find_existing_media(cfg: dict, file_path: Path) -> tuple[dict | None, list[str]]:
    """Cherche dans la mediatheque WP une image deja presente pour ce fichier.

    Candidats : medias dont le nom de fichier est `<stem>.<ext>` ou
    `<stem>-<n>.<ext>` (suffixe ajoute par WP quand un fichier de meme nom est
    reuploade) ou `<stem>-scaled.<ext>` (grandes images). Chaque candidat est
    retenu sur le seul nom de fichier (aucun telechargement : un meme nom est
    considere comme la meme image).

    Retourne (media_existant_ou_None, avertissements).
    """
    auth = (cfg["WP_USER"], cfg["WP_APP_PASSWORD"])
    stem, ext = file_path.stem, file_path.suffix.lower()
    name_re = re.compile(rf"^{re.escape(stem)}(-\d+|-scaled)?{re.escape(ext)}$", re.IGNORECASE)
    resp = requests.get(
        f"{cfg['WP_URL']}/wp-json/wp/v2/media",
        params={"search": stem, "media_type": "image", "per_page": 100,
                "_fields": "id,source_url,alt_text,media_details"},
        auth=auth, timeout=30,
    )
    if resp.status_code != 200:
        print(f"ATTENTION : recherche media {file_path.name} -> {resp.status_code}, "
              "verification impossible, l'image sera uploadee.", file=sys.stderr)
        return None, []

    for cand in resp.json():
        source_url = cand.get("source_url", "")
        urls = [source_url]
        original = (cand.get("media_details") or {}).get("original_image")
        if original:  # WP garde l'original quand il sert une version "-scaled"
            urls.append(source_url.rsplit("/", 1)[0] + "/" + original)
        if any(u and name_re.match(u.rsplit("/", 1)[-1]) for u in urls):
            return {"id": cand["id"], "source_url": cand["source_url"],
                    "alt_text": cand.get("alt_text", "")}, []
    return None, []


def upload_media(cfg: dict, file_path: Path, alt_text: str, caption: str | None,
                  description: str, dry_run: bool, check_existing: bool = True) -> dict:
    if check_existing and cfg is not None:
        existing, warnings = find_existing_media(cfg, file_path)
        for w in warnings:
            print(f"    ATTENTION : {w} -> non reutilise.")
        if existing:
            print(f"  DEJA PRESENTE dans WordPress : {file_path.name} -> media #{existing['id']} "
                  f"({existing['source_url']}), meme nom : pas de re-upload, "
                  "metadonnees existantes conservees.")
            if existing.get("alt_text") and existing["alt_text"] != alt_text:
                print(f"    Texte alternatif existant : {existing['alt_text']}")
                print(f"    Texte alternatif de l'article (utilise dans le bloc image) : {alt_text}")
            existing["reused"] = True
            return existing

    if dry_run:
        print(f"  [dry-run] uploaderait {file_path.name}")
        print(f"    Texte alternatif : {alt_text}")
        print(f"    Legende          : {caption or '(aucune)'}")
        print(f"    Description      : {description[:200]}{'...' if len(description) > 200 else ''}")
        return {"id": 0, "source_url": f"images/{file_path.name}"}

    auth = (cfg["WP_USER"], cfg["WP_APP_PASSWORD"])
    data = file_path.read_bytes()
    mime = "image/png" if file_path.suffix.lower() == ".png" else "image/jpeg"
    resp = requests.post(
        f"{cfg['WP_URL']}/wp-json/wp/v2/media",
        headers={
            "Content-Disposition": f'attachment; filename="{file_path.name}"',
            "Content-Type": mime,
        },
        data=data,
        auth=auth,
        timeout=60,
    )
    print(f"POST /wp/v2/media ({file_path.name}) -> {resp.status_code}")
    if resp.status_code not in (200, 201):
        print(resp.text)
        sys.exit(1)
    media = resp.json()

    patch = requests.post(
        f"{cfg['WP_URL']}/wp-json/wp/v2/media/{media['id']}",
        json={
            "alt_text": alt_text,
            "caption": caption or "",
            "description": description,
            "title": file_path.stem,
        },
        auth=auth,
        timeout=30,
    )
    print(f"POST /wp/v2/media/{media['id']} (metadonnees) -> {patch.status_code}")
    if patch.status_code not in (200, 201):
        print(patch.text)
        sys.exit(1)
    return patch.json()


_H2_HTML_RE = re.compile(r"<h2>(.*?)</h2>", re.DOTALL)


def add_h2_ids(content_html: str) -> str:
    """Pose `id` sur chaque <h2>, calcule comme le sommaire de toc.py."""
    matches = list(_H2_HTML_RE.finditer(content_html))
    titles = [toc_mod.plain_title(html.unescape(re.sub(r"<[^>]+>", "", m.group(1)))) for m in matches]
    ids = iter(toc_mod.unique_ids(titles))
    return _H2_HTML_RE.sub(lambda m: f'<h2 id="{next(ids)}">{m.group(1)}</h2>', content_html)


def build_content_html(body: str, images: list[dict], media_by_rel: dict) -> str:
    math_store: list[str] = []
    md = MarkdownIt("commonmark").enable("table")
    content_html = add_h2_ids(md.render(protect_math(body, math_store)))

    for img in images:
        rel = img["rel"]
        media = media_by_rel.get(rel)
        if not media:
            continue
        alt_escaped = html.escape(md_plain(img["alt"]))
        figure = (
            f'<!-- wp:image {{"id":{media["id"]},"sizeSlug":"large","linkDestination":"none"}} -->\n'
            f'<figure class="wp-block-image size-large">'
            f'<img src="{media["source_url"]}" alt="{alt_escaped}" class="wp-image-{media["id"]}"/>'
        )
        if img["caption"]:
            caption_html = md.renderInline(protect_math(img["caption"], math_store))
            figure += f'<figcaption class="wp-element-caption">{caption_html}</figcaption>'
        figure += "</figure>\n<!-- /wp:image -->"

        # count=1 : si la meme image revient plusieurs fois dans l'article
        # (chaque occurrence avec sa propre legende dans `images`), ne
        # consommer que la prochaine balise <img> encore non remplacee, dans
        # l'ordre du document -- sinon un seul .sub() global remplacerait
        # toutes les occurrences par la legende de la premiere.
        img_tag_re = re.compile(r'<img src="' + re.escape(rel) + r'"[^>]*>')
        content_html = img_tag_re.sub(figure, content_html, count=1)

    content_html = re.sub(
        r"<p>\s*(<!-- wp:image.*?<!-- /wp:image -->)\s*</p>",
        r"\1",
        content_html,
        flags=re.DOTALL,
    )

    content_html = restore_math(content_html, math_store)
    content_html = content_html.replace(_CURRENCY_PLACEHOLDER, "&#36;")
    if math_store:
        # Requis par WP QuickLaTeX pour rendre les formules de ce post.
        content_html = "[latexpage]\n" + content_html
    return content_html


def create_post(cfg: dict, frontmatter: dict, content_html: str,
                 dry_run: bool, featured_media_id: int = 0) -> dict:
    # Image mise en avant = <slug>.png (make_cover.py), JAMAIS une image du
    # corps : le theme l'affiche au-dessus du contenu, une image du corps
    # serait dupliquee visuellement.
    payload = {
        "title": frontmatter.get("title", ""),
        "content": content_html,
        "excerpt": frontmatter.get("description", ""),
        "slug": frontmatter.get("slug", ""),
        "status": "draft",
    }
    if featured_media_id:
        payload["featured_media"] = featured_media_id

    if dry_run:
        print("  [dry-run] creerait le post avec :")
        print(json.dumps({k: v for k, v in payload.items() if k != "content"},
                          ensure_ascii=False, indent=2))
        print(f"    content : {len(content_html)} caracteres HTML")
        return {"id": 0, "link": "(dry-run)"}

    auth = (cfg["WP_USER"], cfg["WP_APP_PASSWORD"])
    resp = requests.post(f"{cfg['WP_URL']}/wp-json/wp/v2/posts", json=payload, auth=auth, timeout=30)
    print(f"POST /wp/v2/posts -> {resp.status_code}")
    if resp.status_code not in (200, 201):
        print(resp.text)
        sys.exit(1)
    return resp.json()


def is_published(slug: str) -> bool:
    """Publie = trace published/<slug>.json presente (ecrite apres chaque creation de brouillon)."""
    return (PUBLISHED_DIR / f"{slug}.json").exists()


def marquer_publie_dans_index(slug: str) -> None:
    """Passe a « Oui » la colonne « Article publié » de la ligne du slug dans rag_data/content/INDEX.md."""
    if not CONTENT_INDEX.exists():
        return
    lignes = CONTENT_INDEX.read_text(encoding="utf-8").splitlines()
    marque = f"| `{slug}` |"
    for i, l in enumerate(lignes):
        if l.startswith("| 20") and marque in l:
            lignes[i] = re.sub(r"\|\s*(Oui|Non)\s*\|$", "| Oui |", l)
            break
    CONTENT_INDEX.write_text("\n".join(lignes) + "\n", encoding="utf-8")


def list_unpublished() -> None:
    """Articles de rag_data/content/work/ (avec article.md) sans trace de publication."""
    slugs = sorted(p.name for p in CONTENT_DIR.iterdir()
                   if p.is_dir() and (p / "article.md").exists() and not is_published(p.name))
    print(f"Articles non publies : {len(slugs)}")
    for n, slug in enumerate(slugs, 1):
        print(f"{n}. {slug}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("slug", nargs="?", help="dossier de rag_data/content/work/ ; sans slug, liste les non publies")
    parser.add_argument("--list", action="store_true", help="Liste les articles non publies et sort.")
    parser.add_argument("--dry-run", action="store_true",
                         help="N'ecrit rien sur WordPress : affiche ce qui serait envoye "
                              "(les images deja presentes sont quand meme detectees, en lecture seule).")
    parser.add_argument("--no-check-existing", action="store_true",
                         help="Ne cherche pas les images deja presentes dans la mediatheque : "
                              "uploade toutes les images.")
    args = parser.parse_args()

    if args.list or not args.slug:
        list_unpublished()
        return

    cfg = None if args.dry_run and not ENV_PATH.exists() else load_wp_config()

    frontmatter, body, content_dir = load_article(args.slug)
    image_meta = load_image_metadata(content_dir)
    body, images = extract_images_and_strip_captions(body)

    print(f"Article : {frontmatter.get('title')!r} ({args.slug})")
    print(f"Images referencees dans article.md : {len(images)}")

    media_by_rel = {}
    for img in images:
        rel = img["rel"]
        if rel in media_by_rel:
            continue
        file_path = content_dir / rel
        if not file_path.exists():
            print(f"ATTENTION : image {rel} introuvable sur disque, ignoree.", file=sys.stderr)
            continue
        meta = image_meta.get(rel, {})
        description = meta.get("alt", img["alt"])
        # Jamais de mention de la source (pdf, section) dans les champs
        # Texte alternatif / Legende / Description de la mediatheque WP.
        print(f"- {rel}")
        media = upload_media(
            cfg, file_path,
            md_plain(img["alt"]),
            md_plain(img["caption"]) if img["caption"] else None,
            md_plain(description),
            args.dry_run,
            check_existing=not args.no_check_existing,
        )
        media_by_rel[rel] = media

    reused = [r for r, m in media_by_rel.items() if m.get("reused")]
    print()
    print(f"Images : {len(reused)} deja presente(s) dans WordPress (reutilisee(s)), "
          f"{len(media_by_rel) - len(reused)} a uploader"
          + (" (dry-run : rien n'est uploade)" if args.dry_run else "") + ".")

    content_html = build_content_html(body, images, media_by_rel)

    # Image mise en avant : <slug>.png (absente -> pas d'image mise en avant).
    cover_path = content_dir / f"{args.slug}.png"
    featured_id = 0
    if cover_path.exists():
        print(f"- {cover_path.name} (image mise en avant)")
        f = make_cover_mod.wp_fields(args.slug, frontmatter)
        cover = upload_media(
            cfg, cover_path,
            md_plain(f["alt"]), md_plain(f["caption"]), md_plain(f["description"]),
            args.dry_run,
            check_existing=not args.no_check_existing,  # nom unique (= slug)
        )
        featured_id = cover["id"]
    else:
        print(f"ATTENTION : {args.slug}.png absent (lance make_cover.py <slug>) : "
              "article cree sans image mise en avant.", file=sys.stderr)

    post = create_post(cfg, frontmatter, content_html, args.dry_run, featured_id)

    print()
    print("Statut : draft")
    print(f"Post ID : {post.get('id')}")
    print(f"Lien : {post.get('link', post.get('guid', {}).get('rendered', '(dry-run)') if isinstance(post.get('guid'), dict) else '')}")

    if not args.dry_run:
        # Trace de publication : published/<slug>.json (6 champs, meme format que les traces reconstruites)
        PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
        trace = PUBLISHED_DIR / f"{args.slug}.json"
        trace.write_text(json.dumps({
            "folder": args.slug,
            "title": frontmatter.get("title", ""),
            "wp_post_id": post.get("id"),
            "wp_status": post.get("status", "draft"),
            "url": post.get("link"),
            "date": (datetime.strptime(post["date"], "%Y-%m-%dT%H:%M:%S") if post.get("date")
                     else datetime.now()).strftime("%Y%m%d-%H%M%S"),
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Trace : {trace.relative_to(ROOT)}")
        marquer_publie_dans_index(args.slug)


if __name__ == "__main__":
    main()
