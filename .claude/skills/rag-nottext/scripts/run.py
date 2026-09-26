"""Etape /rag-nottext : decrit en langage naturel (et OCRise/transcrit si
pertinent) chaque element non-textuel de pivot.md — images, blocs de code,
formules d'affichage deja en LaTeX — extrait/produit par /rag-extraction, et
enrichit pivot.md en consequence pour que ce contenu devienne recherchable
en aval.

Usage:
    python run.py <pdf_condense | document_id | dossier_de_travail> [--force] [--model NAME] [--batch-size N]

Prerequis : rag_data/work/<document_id>/pivot.md doit exister (produit par
/rag-extraction).

--batch-size N traite au plus N elements par invocation puis s'arrete
(code de sortie 3, "nottext" pas encore marque "done") — a relancer avec
exactement la meme commande pour continuer : le lot suivant reprend
automatiquement la ou le precedent s'est arrete, en relisant pivot.md pour
determiner quels elements sont deja enrichis (aucun compteur separe a
maintenir). Sans cette option, tous les elements sont traites en une seule
invocation, comme avant — utile pour rester sous une limite de temps
d'execution unique sur un document a beaucoup d'elements non-textuels.

Ecrit dans <racine_projet>/rag_data/work/<document_id>/ :
    nottext_meta.json  liste structuree, un objet par element traite
                        (type, identifiant, fichier, page, original,
                        description, ocr_text, latex, erreur)
    pivot.md            mis a jour en place : chaque element recoit une
                        description juste apres lui, marquee par le
                        commentaire HTML invisible au rendu
                        DESCRIPTION_MARKER (voir _rag_lib/chunk.py — c'est ce
                        marqueur que /rag-chunking utilise pour decoupler le
                        texte embedde du contenu verbatim)
    images/             fichiers renommes en place (images uniquement)
    status.json         suivi de l'etape "nottext"

Les elements sont detectes via _rag_lib/chunk.py:split_into_blocks (meme
segmentation que /rag-chunking, une seule logique de parsing partagee).
Pour chaque bloc "image" : classification deterministe formule/generale
(_rag_lib/image_classifier.py), description via un appel `claude -p`
headless avec l'outil Read restreint au dossier de l'image
(_rag_lib/image_vision.py), puis OCR adapte (pix2tex si formule,
tesseract si generale et texte present). Pour un bloc "code" ou "formula"
(formule d'affichage deja en LaTeX, voir rag-extraction/SKILL.md —
"Resolution : course.tex") : description via un appel `claude -p` texte
seul (_rag_lib/code_description.py, _rag_lib/formula_description.py) — pas
besoin de l'outil Read, ces blocs sont deja du texte.
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
import re
import sys
import unicodedata
from pathlib import Path

# La console Windows (cp1252/cp850 selon l'hote) ne represente pas tous les
# caracteres qu'un appel `claude -p` peut renvoyer (guillemets typographiques,
# accents combines...) — sans ce reglage, un `print()` sur un message
# contenant un tel caractere leve `UnicodeEncodeError` et interrompt le
# script en PLEIN milieu du traitement (verifie empiriquement sur un document
# reel), perdant tout le travail deja paye en appels `claude -p` puisque
# `pivot.md`/`nottext_meta.json` ne sont ecrits qu'une fois TOUS les elements
# traites (voir `main()`). `errors="replace"` degrade proprement (caractere
# illisible affiche comme `?`) plutot que de faire planter tout le run pour
# un probleme d'affichage sans rapport avec la validite du traitement.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_rag_lib"))

import checks
import chunk as chunk_mod
import paths
import status as status_lib
from _claude_code_client import MAX_GROUP_SIZE
from chunk import DESCRIPTION_MARKER, OCR_MARKER
from code_description import CodeDescriptionError, describe_code_batch
from formula_description import FormulaDescriptionError, describe_formula_batch
from image_classifier import classify
from image_vision import ImageVisionError, describe_image
from ocr_formula import extract_latex
from ocr_general import extract_text

_IMAGE_LINE_RE = re.compile(r"^!\[([^\]]*)\]\((images/[^)]+)\)$")
# `img` (extraction native PDF) ET `formula` (rasterisation de formule, voir
# rag-extraction/scripts/convert.py:_materialize_formula_runs_as_images) —
# les deux sont des noms opaques numerotes eligibles au renommage explicite,
# contrairement aux illustrations source qui gardent leur nom semantique
# d'origine (rel_path ne matche alors ni l'un ni l'autre, page_number reste
# None, aucun renommage : comportement voulu, voir SKILL.md).
_PAGE_IMG_RE = re.compile(r"^images/page_(\d+)_(?:img|formula)_(\d+)\.(\w+)$")


def _slugify(text: str, *, max_words: int = 6, max_len: int = 60) -> str:
    words = re.findall(r"[^\W\d_]+", text.lower(), flags=re.UNICODE)[:max_words]
    slug = "_".join(words)
    slug = unicodedata.normalize("NFKD", slug)
    slug = "".join(c for c in slug if not unicodedata.combining(c))
    slug = re.sub(r"[^a-z0-9_]+", "", slug)
    return slug[:max_len].strip("_") or "image"


def _unique_path(candidate: Path) -> Path:
    if not candidate.exists():
        return candidate
    stem, suffix, parent = candidate.stem, candidate.suffix, candidate.parent
    n = 1
    while True:
        alt = parent / f"{stem}_{n}{suffix}"
        if not alt.exists():
            return alt
        n += 1


def _hash_id(text: str) -> str:
    """Identifiant stable derive du texte brut d'un bloc — utilise comme
    `identifiant` dans `nottext_meta.json`, seule identite disponible pour
    un bloc de code ou de formule (pas de nom de fichier a renommer)."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _new_entry(entry_type: str | None, identifiant: str) -> dict:
    return {
        "type": entry_type,
        "identifiant": identifiant,
        "fichier_original": None,
        "fichier": None,
        "alt_original": None,
        "page": None,
        "original": None,
        "description": None,
        "ocr_text": None,
        "latex": None,
        "erreur": None,
    }


def _process_image(image_line: str, work_dir: Path, *, model: str | None) -> dict:
    identifiant = _hash_id(image_line)
    match = _IMAGE_LINE_RE.match(image_line)
    if not match:
        entry = _new_entry("image_generale", identifiant)
        entry["erreur"] = f"ligne image non reconnue: {image_line!r}"
        return entry

    alt_original = match.group(1)
    rel_path = match.group(2)
    image_path = work_dir / rel_path
    page_match = _PAGE_IMG_RE.match(rel_path)
    page_number = int(page_match.group(1)) if page_match else None

    entry = _new_entry(None, identifiant)
    entry["fichier_original"] = rel_path
    entry["fichier"] = rel_path
    entry["alt_original"] = alt_original
    entry["page"] = page_number

    if not image_path.exists():
        entry["type"] = "image_generale"
        entry["erreur"] = f"fichier introuvable: {image_path}"
        return entry

    try:
        ocr_text_general = extract_text(image_path)
    except RuntimeError as exc:
        entry["type"] = "image_generale"
        entry["erreur"] = str(exc)
        return entry

    categorie = classify(ocr_text_general)
    entry["type"] = "image_formule" if categorie == "formule" else "image_generale"

    try:
        entry["description"] = describe_image(image_path, categorie, model=model)
    except ImageVisionError as exc:
        entry["erreur"] = f"description: {exc}"
        return entry

    if categorie == "formule":
        try:
            entry["latex"] = extract_latex(image_path)
        except Exception as exc:  # pix2tex peut lever des erreurs variees (modele, image illisible)
            entry["erreur"] = f"ocr formule (pix2tex): {exc}"
            return entry
    else:
        entry["ocr_text"] = ocr_text_general if len(ocr_text_general.strip()) >= 3 else None

    if page_number is not None:
        new_path = _unique_path(image_path.with_name(f"page_{page_number:03d}_{_slugify(entry['description'])}{image_path.suffix}"))
        image_path.rename(new_path)
        entry["fichier"] = new_path.relative_to(work_dir).as_posix()

    return entry


def _process_code_group(code_blocks: list[str], *, model: str | None) -> list[dict]:
    """Traite jusqu'a `MAX_GROUP_SIZE` blocs de code EN UN SEUL appel
    `claude -p` (voir `code_description.describe_code_batch`) — retourne les
    entrees dans le MEME ORDRE que `code_blocks`. Un echec de l'appel groupe
    marque TOUS les blocs du groupe en erreur (jamais un retraitement
    partiel/approximatif) ; ils restent alors "pending" et seront retentes,
    eventuellement dans un groupement different, au prochain lot (voir
    `_find_pending_blocks`)."""
    entries = [_new_entry("code", _hash_id(b)) for b in code_blocks]
    for entry, block in zip(entries, code_blocks):
        entry["original"] = block
    try:
        descriptions = describe_code_batch(code_blocks, model=model)
    except CodeDescriptionError as exc:
        for entry in entries:
            entry["erreur"] = f"description (groupe de {len(code_blocks)}): {exc}"
        return entries
    for entry, description in zip(entries, descriptions):
        entry["description"] = description
    return entries


def _process_formula_group(formula_blocks: list[str], *, model: str | None) -> list[dict]:
    """Symetrique de `_process_code_group` pour les formules — voir
    `formula_description.describe_formula_batch`."""
    entries = [_new_entry("formule_texte", _hash_id(b)) for b in formula_blocks]
    for entry, block in zip(entries, formula_blocks):
        entry["original"] = block
    try:
        descriptions = describe_formula_batch(formula_blocks, model=model)
    except FormulaDescriptionError as exc:
        for entry in entries:
            entry["erreur"] = f"description (groupe de {len(formula_blocks)}): {exc}"
        return entries
    for entry, description in zip(entries, descriptions):
        entry["description"] = description
    return entries


def _find_pending_blocks(blocks: list["chunk_mod._Block"]) -> list["chunk_mod._Block"]:
    """Blocs image/code/formule pas encore enrichis — pas de paragraphe
    `DESCRIPTION_MARKER` juste apres, meme detection que
    `_rag_lib/chunk.py:_merge_description_blocks`, appliquee ici a l'etat
    COURANT de `pivot.md` plutot qu'a `nottext_meta.json` : permet de
    reprendre un traitement interrompu (voir `--batch-size`) uniquement a
    partir de ce que le pivot montre reellement, jamais d'un compteur separe
    qui pourrait diverger de l'etat sur disque."""
    pending: list["chunk_mod._Block"] = []
    for i, block in enumerate(blocks):
        if block.kind not in ("image", "code", "formula"):
            continue
        following = blocks[i + 1] if i + 1 < len(blocks) else None
        if following is not None and following.kind == "prose" and following.text.startswith(DESCRIPTION_MARKER):
            continue  # deja enrichi par un lot precedent
        pending.append(block)
    return pending


def _dedupe_entries(entries: list[dict]) -> list[dict]:
    """Ne garde que la DERNIERE entree par `identifiant` — un element en
    echec dans un lot puis retente avec succes dans un lot ulterieur (voir
    `_find_pending_blocks`, base sur `pivot.md`, pas sur ce fichier) laisse
    sinon plusieurs lignes pour le meme element dans `nottext_meta.json` :
    l'ancien echec ne doit jamais compter dans les totaux ni polluer le
    rapport final une fois l'element reellement enrichi. L'ordre d'insertion
    de `dict` (Python 3.7+) conserve la position de la PREMIERE apparition
    de chaque identifiant, avec la valeur la plus RECENTE — un ordre proche
    de l'ordre du document, sans avoir besoin de le retrier explicitement."""
    by_id: dict[str, dict] = {}
    for e in entries:
        by_id[e["identifiant"]] = e
    return list(by_id.values())


def _chunked(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _preview(text: str, *, max_len: int = 70) -> str:
    """Premiere ligne non vide du bloc (fences/delimiteurs retires),
    tronquee — donne une idee du contenu reellement en cours de traitement
    PENDANT l'attente d'un appel `claude -p` (qui peut prendre plusieurs
    secondes a plusieurs dizaines de secondes), jamais utilise pour la
    logique elle-meme."""
    for line in text.splitlines():
        line = line.strip().strip("`\\[]").strip()
        if line:
            return line[:max_len] + ("..." if len(line) > max_len else "")
    return "(vide)"


def _print_entry_result(entry: dict) -> None:
    if entry["erreur"]:
        print(f"  echec: {entry['erreur']}")
    else:
        renamed = f" -> {entry['fichier']}" if entry["fichier"] and entry["fichier"] != entry.get("fichier_original") else ""
        print(f"  {entry['type']}{renamed}")


def _print_progress(done_counts: dict[str, int], total_counts: dict[str, int]) -> None:
    """Affiche l'etat des lieux MIS A JOUR juste apres qu'un element (ou un
    groupe) vient d'etre traite — par opposition a `_print_status_overview`,
    appele une seule fois avant tout traitement. Sans cet affichage
    intermediaire, la progression reelle (combien traites MAINTENANT, sur
    combien au total) restait invisible entre le debut et la toute fin d'un
    lot, potentiellement long (plusieurs appels `claude -p` sequentiels,
    voir `_process_batch`)."""
    done_total = sum(done_counts.values())
    total_total = sum(total_counts.values())
    print(
        f">>> Progression : {done_total}/{total_total} traites "
        f"({done_counts['image']}/{total_counts['image']} image(s), "
        f"{done_counts['code']}/{total_counts['code']} bloc(s) de code, "
        f"{done_counts['formula']}/{total_counts['formula']} formule(s))"
    )
    print()


def _process_batch(
    batch: list["chunk_mod._Block"],
    *,
    work_dir: Path,
    model: str | None,
    done_counts: dict[str, int],
    total_counts: dict[str, int],
) -> list[dict]:
    """Traite `batch` et retourne les entrees dans le MEME ORDRE que `batch`
    (ordre du document) — indispensable pour que `_apply_replacements`
    puisse chercher chaque remplacement sequentiellement dans `pivot.md`.

    Regroupe les blocs "code" et "formula" par lots de `MAX_GROUP_SIZE`
    (voir `_process_code_group`/`_process_formula_group`) pour reduire le
    nombre d'appels `claude -p` sequentiels — jamais les images, traitees
    une par une (chacune necessite son propre `Read`, voir
    `image_vision.describe_image`). Le regroupement ne change QUE l'ordre
    des APPELS, jamais l'ordre des entrees retournees ici.

    Ordre de traitement volontaire : formules, puis blocs de code, puis
    images en dernier (choix explicite, sans rapport avec l'ordre
    d'apparition dans le document, deja preserve par ailleurs via
    `results[i]`).

    Affiche un apercu de CHAQUE element AVANT l'appel qui le concerne
    (jamais seulement apres) : un appel `claude -p` peut prendre plusieurs
    secondes a plusieurs dizaines de secondes, surtout pour un groupe de
    plusieurs blocs — sans cet affichage prealable, rien ne distingue un
    traitement normalement long d'un blocage."""
    results: dict[int, dict] = {}

    for kind, label, process_group in (
        ("formula", "formule(s)", _process_formula_group),
        ("code", "bloc(s) de code", _process_code_group),
    ):
        indices = [i for i, b in enumerate(batch) if b.kind == kind]
        for group in _chunked(indices, MAX_GROUP_SIZE):
            print(f"Traitement d'un groupe de {len(group)} {label} :")
            for i in group:
                print(f"  [{i}] {_preview(batch[i].text)}")
            entries = process_group([batch[i].text for i in group], model=model)
            for i, entry in zip(group, entries):
                _print_entry_result(entry)
                results[i] = entry
                if not entry["erreur"]:
                    done_counts[kind] += 1
            _print_progress(done_counts, total_counts)

    image_indices = [i for i, b in enumerate(batch) if b.kind == "image"]
    for i in image_indices:
        print(f"Traitement image : {_preview(batch[i].text)}")
        entry = _process_image(batch[i].text, work_dir, model=model)
        _print_entry_result(entry)
        results[i] = entry
        if not entry["erreur"]:
            done_counts["image"] += 1
        _print_progress(done_counts, total_counts)

    return [results[i] for i in range(len(batch))]


def _bare_text(entry: dict) -> str:
    """Texte du bloc tel qu'il apparait dans pivot.md AVANT tout
    enrichissement — pour une image, l'alt-text reellement capture a la
    lecture (`alt_original`, quasi toujours "Illustration" en pratique,
    produit par /rag-extraction), jamais une valeur figee en dur : un pivot
    modifie a la main avec un alt-text different doit rester retrouvable
    tel quel, pas silencieusement remplace par une hypothese."""
    if entry["type"] in ("image_formule", "image_generale"):
        alt = entry.get("alt_original") or "Illustration"
        return f"![{alt}]({entry['fichier_original']})"
    return entry["original"]


def _flatten_blank_lines(text: str) -> str:
    """Remplace toute sequence de lignes vides internes par un simple saut de
    ligne — indispensable avant d'inserer `ocr_text`/`latex` dans un
    paragraphe de pivot.md : tesseract emet souvent des lignes vides entre
    zones de texte detectees (verifie empiriquement, ex. `ocr_text` =
    "Transformer\n\nSequence en entree...\n\n(poids appris)"), et
    `split_into_blocks` (voir `_rag_lib/chunk.py`) coupe un nouveau bloc de
    prose a CHAQUE ligne vide (`\\n\\s*\\n`) — sans cet aplatissement, le
    paragraphe OCR_MARKER se retrouverait fragmente en plusieurs blocs dont
    seul le premier porte le marqueur, les suivants restant des blocs de
    prose isoles que `chunk_markdown` peut placer dans un chunk different de
    l'image/description dont ils dependent (meme categorie de bug que
    OCR_MARKER lui-meme corrige juste avant)."""
    return re.sub(r"\n\s*\n+", "\n", text.strip())


def _enriched_text(entry: dict) -> str:
    """Texte du bloc tel qu'il apparait dans pivot.md APRES enrichissement —
    remplace integralement `_bare_text(entry)` (voir `_apply_replacements`).
    Doit rester strictement synchronise avec `_merge_description_blocks`
    dans `_rag_lib/chunk.py` (memes marqueurs DESCRIPTION_MARKER/OCR_MARKER,
    meme absence de ligne vide entre chaque marqueur et son contenu) — sans
    OCR_MARKER, le paragraphe de transcription/OCR ci-dessous serait un bloc
    de prose isole que `chunk_markdown` pourrait placer dans un chunk
    different de l'image/description dont il depend (voir chunk.py). Voir
    aussi `_flatten_blank_lines` : le CONTENU de ce paragraphe (ocr_text/
    latex) doit lui-meme rester exempt de ligne vide interne, pour la meme
    raison."""
    if entry["type"] in ("image_formule", "image_generale"):
        alt = (entry["description"] or "").replace("]", "").replace("[", "")
        head = f"![{alt}]({entry['fichier']})"
    else:
        head = entry["original"]

    parts = [head, "", DESCRIPTION_MARKER, entry["description"]]
    if entry["type"] == "image_formule" and entry.get("latex"):
        latex = _flatten_blank_lines(entry["latex"])
        parts += ["", OCR_MARKER, f"Transcription LaTeX (OCR) : $${latex}$$"]
    if entry["type"] == "image_generale" and entry.get("ocr_text"):
        ocr_text = _flatten_blank_lines(entry["ocr_text"])
        parts += ["", OCR_MARKER, f"Texte detecte dans l'image : {ocr_text}"]
    return "\n".join(parts)


def _apply_replacements(markdown: str, replacements: list[tuple[str, str]]) -> str:
    """`replacements` : liste ordonnee (dans l'ordre d'apparition reel dans
    `markdown`) de (texte_a_chercher, texte_de_remplacement). Recherche
    sequentielle a partir de la fin du remplacement precedent — jamais
    depuis le debut du fichier — pour cibler correctement meme quand deux
    blocs ont un contenu original identique (ex. deux tres courts extraits
    de code identiques). Leve `ValueError` (message de `str.index`) si un
    `texte_a_chercher` reste introuvable a partir du curseur courant."""
    pieces = []
    cursor = 0
    for needle, replacement in replacements:
        pos = markdown.index(needle, cursor)
        pieces.append(markdown[cursor:pos])
        pieces.append(replacement)
        cursor = pos + len(needle)
    pieces.append(markdown[cursor:])
    return "".join(pieces)


def _apply_revert(markdown: str, entries: list[dict]) -> str:
    """Comme `_apply_replacements`, mais dans l'autre sens (enrichi -> bare)
    et avec une tolerance : si la forme enrichie d'une entree est
    introuvable a partir du curseur courant MAIS que sa forme bare l'est,
    ce bloc est deja dans son etat d'origine (revert precedent deja
    applique, ou pivot.md regenere en amont par `/rag-extraction --force`
    sans passer par `/rag-nottext --force` — `nottext_meta.json` reste alors
    perime mais le pivot ne porte plus aucune trace de l'enrichissement) :
    rien a retirer pour cette entree, le curseur avance simplement jusqu'a
    la fin de la forme bare trouvee. Ne leve `ValueError` que si NI l'une
    NI l'autre forme n'est trouvable — la seule situation ou l'etat du
    pivot est reellement incoherent avec `nottext_meta.json`."""
    pieces = []
    cursor = 0
    for entry in entries:
        enriched, bare = _enriched_text(entry), _bare_text(entry)
        pos = markdown.find(enriched, cursor)
        if pos != -1:
            pieces.append(markdown[cursor:pos])
            pieces.append(bare)
            cursor = pos + len(enriched)
            continue
        pos = markdown.find(bare, cursor)
        if pos != -1:
            pieces.append(markdown[cursor:pos + len(bare)])
            cursor = pos + len(bare)
            continue
        raise ValueError(
            f"ni la forme enrichie ni la forme d'origine du bloc {entry['identifiant']!r} "
            f"({entry['type']}) ne sont trouvables a partir du curseur courant"
        )
    pieces.append(markdown[cursor:])
    return "".join(pieces)


def _revert_previous_run(work_dir: Path, meta_path: Path) -> str | None:
    """Si un `nottext_meta.json` d'un run precedent existe, restaure les
    images a leur nom d'origine et retire chaque bloc enrichi de `pivot.md`
    pour retrouver l'etat produit par /rag-extraction — indispensable avant
    un retraitement (`--force`), sinon le script partirait d'un pivot deja
    mute par le run precedent.

    Retourne le markdown restaure (a ecrire par l'appelant), ou None si
    aucun run precedent n'est trouve (premier passage). Leve une erreur
    explicite plutot que de continuer silencieusement si l'etat sur disque
    ne correspond plus a ce que `nottext_meta.json` decrit (pivot modifie a
    la main entre-temps, par exemple) — jamais de correction approximative
    sur un etat incertain."""
    if not meta_path.exists():
        return None

    previous = json.loads(meta_path.read_text(encoding="utf-8"))
    pivot_path = work_dir / "pivot.md"
    if not pivot_path.exists():
        return None
    markdown = pivot_path.read_text(encoding="utf-8")

    ordered = [e for e in previous if not e.get("erreur")]

    # 1) restaurer les noms de fichiers image AVANT de toucher au texte —
    # _bare_text()/_enriched_text() dependent de fichier_original/fichier
    # tels qu'enregistres, coherents avec l'etat disque restaure ici.
    for entry in ordered:
        if entry["type"] not in ("image_formule", "image_generale"):
            continue
        original_rel, current_rel = entry["fichier_original"], entry["fichier"]
        if current_rel == original_rel:
            continue
        current_path, original_path = work_dir / current_rel, work_dir / original_rel
        if not current_path.exists():
            continue  # deja restaure (run precedent interrompu en cours de revert)
        if original_path.exists():
            raise RuntimeError(
                f"revert impossible : {current_path} et {original_path} existent "
                "tous les deux — etat incoherent, corrige manuellement le dossier "
                "images/ avant de relancer --force"
            )
        current_path.rename(original_path)

    # 2) retirer chaque bloc enrichi, dans l'ordre d'apparition dans le pivot
    # — tolere qu'un bloc soit deja bare (voir _apply_revert), leve
    # seulement si son etat est reellement incoherent avec les deux formes.
    try:
        return _apply_revert(markdown, ordered)
    except ValueError as exc:
        raise RuntimeError(
            "revert impossible : un bloc du run precedent n'est retrouvable ni enrichi ni sous "
            f"sa forme d'origine dans pivot.md (modifie manuellement depuis ?) — {exc}. Corrige "
            "pivot.md a la main, ou relance /rag-extraction --force avant de reessayer "
            "/rag-nottext --force"
        ) from exc


def _build_report(document_name: str, entries: list[dict], *, meta_path: Path) -> str:
    """Rapport Markdown complet, deja pret a etre relaye tel quel — meme
    principe que `rag-extraction/scripts/run.py:_build_report` (voir son
    SKILL.md : le format ne laisse aucune place a l'interpretation, c'est
    donc ce script, pas l'agent qui l'invoque, qui doit le produire, plutot
    que de laisser l'agent rouvrir `nottext_meta.json` pour reconstruire ce
    meme tableau a la main a chaque execution)."""
    n_images = sum(1 for e in entries if e["type"] in ("image_formule", "image_generale"))
    n_code = sum(1 for e in entries if e["type"] == "code")
    n_formules_texte = sum(1 for e in entries if e["type"] == "formule_texte")
    n_errors = sum(1 for e in entries if e["erreur"])

    lines: list[str] = []
    lines.append(f"## Elements non-textuels traites — `{document_name}`")
    lines.append("")
    lines.append(
        f"{len(entries)} element(s) traite(s) : {n_images} image(s), {n_code} bloc(s) de code, "
        f"{n_formules_texte} formule(s) texte, {n_errors} echec(s)."
    )
    lines.append(f"`nottext_meta.json` : `{meta_path}`")

    if entries:
        lines.append("")
        lines.append("| Identifiant / fichier | Type | Longueur description | Transcription |")
        lines.append("|---|---|---|---|")
        for e in entries:
            label = e.get("fichier") or e.get("fichier_original") or e["identifiant"]
            desc_len = len(e["description"]) if e["description"] else 0
            transcription = "oui" if (e.get("latex") or e.get("ocr_text")) else "non"
            status = "ECHEC" if e["erreur"] else (e["type"] or "?")
            lines.append(f"| `{label}` | {status} | {desc_len} | {transcription} |")

    if n_errors:
        lines.append("")
        lines.append(f"### Echecs ({n_errors})")
        lines.append("")
        for e in entries:
            if e["erreur"]:
                ident = e.get("fichier_original") or e["identifiant"]
                lines.append(f"- `{ident}` ({e['type']}) : {e['erreur']}")
        lines.append("")
        lines.append(
            "**Element(s) en echec detecte(s) — arrete-toi avant de proposer d'enchainer sur "
            "`/rag-chunking`**, jamais de description ou de transcription inventee pour combler "
            "un element en echec. Le traitement des autres elements n'est pas bloque par l'echec "
            "d'un seul."
        )

    return "\n".join(lines)


def _count_by_kind(blocks: list["chunk_mod._Block"]) -> dict[str, int]:
    counts = {"image": 0, "code": 0, "formula": 0}
    for b in blocks:
        if b.kind in counts:
            counts[b.kind] += 1
    return counts


def _format_counts(counts: dict[str, int]) -> str:
    total = sum(counts.values())
    return (
        f"{total} element(s) — {counts['image']} image(s), {counts['code']} bloc(s) de code, "
        f"{counts['formula']} formule(s)"
    )


def _print_status_overview(
    total_counts: dict[str, int], pending_counts: dict[str, int], done_counts: dict[str, int], *, document_name: str
) -> None:
    """Affiche, AVANT tout traitement, un etat des lieux complet du document :
    total d'elements non-textuels par type, deja traites par type, et
    restants par type — pour que ce qui va se passer (et sa taille) soit
    visible a l'ecran des le debut, jamais decouvert au fil des lignes
    "Traitement ..." une fois le lot deja en cours. Voir `_print_progress`
    pour la mise a jour de ces memes compteurs PENDANT le traitement du lot."""
    print(f"=== Etat des lieux — `{document_name}` ===")
    print(f"Total        : {_format_counts(total_counts)}")
    print(f"Deja traites : {_format_counts(done_counts)}")
    print(f"Restants     : {_format_counts(pending_counts)}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=str, help="PDF condense, document_id, dossier de travail, ou pivot.md")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument(
        "--batch-size", type=int, default=None,
        help="nombre max d'elements a traiter dans cette invocation (defaut : tous) — "
        "relancer la meme commande si des elements restent (voir code de sortie 3)",
    )
    args = parser.parse_args()

    try:
        work_dir = paths.resolve_work_dir(args.path)
    except ValueError as exc:
        print(f"ERREUR: {exc}", file=sys.stderr)
        return 1

    pivot_path = work_dir / "pivot.md"
    meta_path = work_dir / "nottext_meta.json"

    if not pivot_path.exists():
        print(f"ERREUR: pivot introuvable: {pivot_path} (lancez d'abord /rag-extraction)", file=sys.stderr)
        return 1

    if status_lib.is_done(work_dir, "nottext") and not args.force and meta_path.exists():
        print(f"deja fait (nottext): {meta_path}")
        return 0

    pre = checks.check_input("nottext", work_dir)
    print(pre.report())
    if not pre.ok:
        return 1

    if args.force:
        try:
            reverted = _revert_previous_run(work_dir, meta_path)
        except RuntimeError as exc:
            print(f"ERREUR: {exc}", file=sys.stderr)
            return 1
        if reverted is not None:
            print("Run precedent detecte : restauration de pivot.md et des noms de fichiers d'origine avant retraitement.")
            pivot_path.write_text(reverted, encoding="utf-8")
        # Repart d'un nottext_meta.json vide : le revert a retire toute trace
        # d'enrichissement de pivot.md, les entrees precedentes (eventuellement
        # accumulees sur plusieurs lots) ne doivent plus etre comptees.
        meta_path.write_text("[]", encoding="utf-8")

    markdown = pivot_path.read_text(encoding="utf-8")
    all_blocks = chunk_mod.split_into_blocks(markdown)
    pending_blocks = _find_pending_blocks(all_blocks)

    previous_entries = _dedupe_entries(
        json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else []
    )

    typed_blocks = [b for b in all_blocks if b.kind in ("image", "code", "formula")]
    total_counts = _count_by_kind(typed_blocks)
    pending_counts = _count_by_kind(pending_blocks)
    done_counts = {k: total_counts[k] - pending_counts[k] for k in total_counts}

    _print_status_overview(total_counts, pending_counts, done_counts, document_name=work_dir.name)

    if not pending_blocks:
        # Rien a traiter : soit un document sans element non-textuel, soit
        # le dernier lot d'un traitement par lots vient deja de tout
        # terminer (pending_blocks recalcule ici, pas suppose).
        if not meta_path.exists():
            # Ecrit une liste vide plutot que de ne rien ecrire : le controle
            # d'idempotence en tete de main() exige `meta_path.exists()` en
            # plus du statut "done" — sans ce fichier, un document sans
            # element non-textuel serait reexamine en entier a chaque
            # relance sans jamais pouvoir etre reconnu comme deja fait.
            meta_path.write_text("[]", encoding="utf-8")
        return _finish(work_dir, previous_entries, meta_path)

    batch = pending_blocks if args.batch_size is None else pending_blocks[: args.batch_size]

    print(f"--- Lot en cours : {len(batch)} element(s) sur {len(pending_blocks)} restant(s) ---")
    new_entries = _process_batch(
        batch, work_dir=work_dir, model=args.model, done_counts=dict(done_counts), total_counts=total_counts
    )

    try:
        replacements = [(_bare_text(e), _enriched_text(e)) for e in new_entries if not e["erreur"]]
        updated_markdown = _apply_replacements(markdown, replacements)
    except ValueError as exc:
        print(f"ERREUR: insertion des descriptions dans pivot.md a echoue: {exc}", file=sys.stderr)
        return 1

    # pivot.md ecrit avant nottext_meta.json : si le processus est interrompu
    # entre les deux, ce lot reste retrouvable comme "deja enrichi" au
    # prochain passage de _find_pending_blocks (base sur pivot.md, pas sur
    # le JSON) — au prix, dans cette seule fenetre etroite, de ne pas
    # figurer dans nottext_meta.json ; accepte comme degradation connue,
    # jamais un retraitement duplique ni une perte de contenu dans le pivot.
    pivot_path.write_text(updated_markdown, encoding="utf-8")
    all_entries = _dedupe_entries(previous_entries + new_entries)
    meta_path.write_text(json.dumps(all_entries, ensure_ascii=False, indent=2), encoding="utf-8")

    # Reverifie sur l'etat REEL de pivot.md APRES ecriture, jamais par simple
    # arithmetique (`len(pending_blocks) - len(batch)`) : un element qui
    # echoue reste sans marqueur, donc toujours pending, meme s'il faisait
    # partie du lot juste traite — seul un nouveau passage de
    # `_find_pending_blocks` sur le pivot fraichement ecrit le detecte
    # correctement. L'ancien calcul arithmetique declarait a tort "termine"
    # des que le lot couvrait la totalite de `pending_blocks` connu au
    # DEBUT de l'invocation, meme si certains de ces elements avaient encore
    # echoue et n'etaient donc jamais reellement enrichis (verifie
    # empiriquement : un document avec des echecs systematiques sur
    # certains elements concluait "done" en laissant un element jamais
    # traite, sans que rien ne le signale).
    still_pending = _find_pending_blocks(chunk_mod.split_into_blocks(updated_markdown))
    if still_pending and new_entries and all(e["erreur"] for e in new_entries):
        # Aucun progres possible : tout le lot vient d'echouer, et un element
        # en echec reste "pending" (voir plus haut) — sans cette sortie, le
        # code 3 ("relance la meme commande") boucle indefiniment sur un
        # element qui echoue a chaque tentative.
        print(
            f"Aucun des {len(batch)} element(s) du lot n'a abouti ({len(still_pending)} restant(s), "
            "tous en echec) — arret : relancer la meme commande ne changerait rien."
        )
        return _finish(work_dir, all_entries, meta_path)
    if still_pending:
        print(
            f"Lot de {len(batch)} element(s) traite(s), {len(still_pending)} restant(s) — "
            "relance exactement la meme commande pour continuer (etape non terminee, "
            "status.json pas encore marque 'done')."
        )
        return 3

    return _finish(work_dir, all_entries, meta_path)


def _finish(work_dir: Path, entries: list[dict], meta_path: Path) -> int:
    """Fin de traitement (plus aucun element en attente) : controle de
    sortie, statut, rapport. Code 0 si tout est bon, 2 (BLOQUANT) sinon —
    jamais 1, reserve aux erreurs techniques/prerequis (voir `_rag_lib/checks.py`)."""
    n_images = sum(1 for e in entries if e["type"] in ("image_formule", "image_generale"))
    n_code = sum(1 for e in entries if e["type"] == "code")
    n_formules_texte = sum(1 for e in entries if e["type"] == "formule_texte")
    n_errors = sum(1 for e in entries if e["erreur"])

    post = checks.check_output("nottext", work_dir)
    print(post.report())

    status_lib.mark_stage(
        work_dir, "nottext", "done" if post.ok else "failed", detail=post.detail(),
        n_images=n_images, n_code=n_code, n_formules_texte=n_formules_texte, n_errors=n_errors,
        verdict=post.verdict, verdict_reasons=post.blocking,
    )

    # Rapport en DERNIER : c'est lui que le SKILL demande de relayer tel quel.
    print(_build_report(work_dir.name, entries, meta_path=meta_path))

    return 0 if post.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
