"""Etape /rag-images : nomme, decrit et OCRise chaque image extraite par
/rag-extraction (rag_data/work/<document_id>/images/), et enrichit pivot.md
en consequence pour que ce contenu devienne recherchable en aval.

Usage:
    python run.py <pdf_condense | document_id | dossier_de_travail> [--force] [--model NAME]

Prerequis : rag_data/work/<document_id>/pivot.md doit exister (produit par
/rag-extraction).

Ecrit dans <racine_projet>/rag_data/work/<document_id>/ :
    images_meta.json   liste structuree par image (fichier, page, categorie,
                        description, ocr_text, latex, erreur)
    pivot.md            mis a jour en place : texte alternatif remplace par la
                        description, paragraphe explicatif + transcription
                        (LaTeX ou OCR) insere juste apres chaque image
    images/             fichiers renommes en place (nom explicite derive de
                        la description, prefixe par le numero de page)
    status.json         suivi de l'etape "images"

Pour chaque image : classification deterministe formule/generale (heuristique
sur un premier passage tesseract, voir _rag_lib/image_classifier.py, aucun
jugement Claude a cette etape) ; description en langage naturel via un appel
`claude -p` headless avec l'outil Read restreint au dossier de l'image (voir
_rag_lib/image_vision.py) ; puis OCR adapte au type detecte : transcription
LaTeX via pix2tex si "formule" (_rag_lib/ocr_formula.py), texte tesseract si
"generale" et du texte est effectivement present (_rag_lib/ocr_general.py).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_rag_lib"))

import paths
import status as status_lib
from image_classifier import classify
from image_vision import ImageVisionError, describe_image
from ocr_formula import extract_latex
from ocr_general import extract_text

_IMAGE_LINE_RE = re.compile(r"^!\[([^\]]*)\]\((images/[^)]+)\)$", re.MULTILINE)
_PAGE_IMG_RE = re.compile(r"^images/page_(\d+)_img_(\d+)\.(\w+)$")


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


def _process_image(rel_path: str, work_dir: Path, *, model: str | None) -> dict:
    image_path = work_dir / rel_path
    match = _PAGE_IMG_RE.match(rel_path)
    page_number = int(match.group(1)) if match else None

    result = {
        "fichier_original": rel_path,
        "fichier": rel_path,
        "page": page_number,
        "categorie": None,
        "description": None,
        "ocr_text": None,
        "latex": None,
        "erreur": None,
    }

    if not image_path.exists():
        result["erreur"] = f"fichier introuvable: {image_path}"
        return result

    try:
        ocr_text_general = extract_text(image_path)
    except RuntimeError as exc:
        result["erreur"] = str(exc)
        return result

    categorie = classify(ocr_text_general)
    result["categorie"] = categorie

    try:
        description = describe_image(image_path, categorie, model=model)
    except ImageVisionError as exc:
        result["erreur"] = f"description: {exc}"
        return result
    result["description"] = description

    if categorie == "formule":
        try:
            result["latex"] = extract_latex(image_path)
        except Exception as exc:  # pix2tex peut lever des erreurs variees (modele, image illisible)
            result["erreur"] = f"ocr formule (pix2tex): {exc}"
            return result
    else:
        result["ocr_text"] = ocr_text_general if len(ocr_text_general.strip()) >= 3 else None

    if page_number is not None:
        new_path = _unique_path(image_path.with_name(f"page_{page_number:03d}_{_slugify(description)}{image_path.suffix}"))
        image_path.rename(new_path)
        result["fichier"] = new_path.relative_to(work_dir).as_posix()

    return result


def _previous_block(entry: dict) -> str:
    """Reconstruit exactement le bloc (ligne image + paragraphe(s) explicatifs)
    tel qu'un run precedent l'avait insere dans pivot.md, a partir de son
    entree dans images_meta.json — jamais approxime, pour ne retirer que ce
    qui a ete effectivement ecrit."""
    alt = (entry.get("description") or "").replace("]", "").replace("[", "")
    lines = [f"![{alt}]({entry['fichier']})", "", entry.get("description") or ""]
    if entry.get("latex"):
        lines += ["", f"$${entry['latex']}$$"]
    if entry.get("ocr_text"):
        lines += ["", f"Texte detecte dans l'image : {entry['ocr_text']}"]
    return "\n".join(lines)


def _revert_previous_run(work_dir: Path, meta_path: Path) -> str | None:
    """Si un `images_meta.json` d'un run precedent existe, restaure les
    images a leur nom d'origine et reconstruit le `pivot.md` bienni tel qu'il
    etait avant tout enrichissement — indispensable avant un retraitement
    (`--force`), sinon le script partirait d'un pivot deja mute par le run
    precedent : le regex de mise a jour ne remplace que la ligne image, pas
    le paragraphe explicatif deja insere en dessous, qui s'accumulerait alors
    a chaque nouveau passage plutot que d'etre remplace proprement.

    Retourne le markdown restaure (a ecrire par l'appelant), ou None si aucun
    run precedent n'est trouve (premier passage). Leve une erreur explicite
    plutot que de continuer silencieusement si l'etat sur disque ne
    correspond plus a ce que `images_meta.json` decrit (pivot modifie a la
    main entre-temps, par exemple) — jamais de correction approximative sur
    un etat incertain.
    """
    if not meta_path.exists():
        return None

    previous = json.loads(meta_path.read_text(encoding="utf-8"))
    pivot_path = work_dir / "pivot.md"
    if not pivot_path.exists():
        return None
    markdown = pivot_path.read_text(encoding="utf-8")

    for entry in previous:
        if entry.get("erreur"):
            continue  # rien n'a ete renomme ni insere pour une image en echec

        original_rel = entry["fichier_original"]
        current_rel = entry["fichier"]
        current_path = work_dir / current_rel
        original_path = work_dir / original_rel

        if current_rel != original_rel:
            if not current_path.exists():
                continue  # deja restaure (run precedent interrompu en cours de revert)
            if original_path.exists():
                raise RuntimeError(
                    f"revert impossible : {current_path} et {original_path} existent "
                    "tous les deux — etat incoherent, corrige manuellement le dossier "
                    "images/ avant de relancer --force"
                )
            current_path.rename(original_path)

        block = _previous_block(entry)
        bare_line = f"![Illustration]({original_rel})"
        if block in markdown:
            markdown = markdown.replace(block, bare_line, 1)
        elif bare_line not in markdown:
            raise RuntimeError(
                f"revert impossible : le bloc insere par le run precedent pour "
                f"{original_rel} est introuvable tel quel dans pivot.md (modifie "
                "manuellement depuis ?) — corrige pivot.md a la main, ou relance "
                "/rag-extraction --force avant de reessayer /rag-images --force"
            )

    return markdown


def _update_pivot(markdown: str, results: list[dict]) -> str:
    by_original = {r["fichier_original"]: r for r in results}

    def _replace(match: "re.Match[str]") -> str:
        rel_path = match.group(2)
        info = by_original.get(rel_path)
        if info is None or info["erreur"]:
            return match.group(0)
        alt = info["description"].replace("]", "").replace("[", "")
        lines = [f"![{alt}]({info['fichier']})", "", info["description"]]
        if info["latex"]:
            lines += ["", f"$${info['latex']}$$"]
        if info["ocr_text"]:
            lines += ["", f"Texte detecte dans l'image : {info['ocr_text']}"]
        return "\n".join(lines)

    return _IMAGE_LINE_RE.sub(_replace, markdown)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=str, help="PDF condense, document_id, dossier de travail, ou pivot.md")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--model", type=str, default=None)
    args = parser.parse_args()

    try:
        work_dir = paths.resolve_work_dir(args.path)
    except ValueError as exc:
        print(f"ERREUR: {exc}", file=sys.stderr)
        return 1

    pivot_path = work_dir / "pivot.md"
    meta_path = work_dir / "images_meta.json"

    if not pivot_path.exists():
        print(f"ERREUR: pivot introuvable: {pivot_path} (lancez d'abord /rag-extraction)", file=sys.stderr)
        return 1

    if status_lib.is_done(work_dir, "images") and not args.force and meta_path.exists():
        print(f"deja fait (images): {meta_path}")
        return 0

    if args.force:
        try:
            reverted = _revert_previous_run(work_dir, meta_path)
        except RuntimeError as exc:
            print(f"ERREUR: {exc}", file=sys.stderr)
            return 1
        if reverted is not None:
            print("Run precedent detecte : restauration du pivot et des noms de fichiers d'origine avant retraitement.")
            pivot_path.write_text(reverted, encoding="utf-8")

    markdown = pivot_path.read_text(encoding="utf-8")
    rel_paths = list(dict.fromkeys(m.group(2) for m in _IMAGE_LINE_RE.finditer(markdown)))

    if not rel_paths:
        status_lib.mark_stage(work_dir, "images", "done", n_images=0, n_formules=0, n_generales=0, n_errors=0)
        print("OK: aucune image dans ce document")
        return 0

    results = []
    for rel_path in rel_paths:
        print(f"Traitement {rel_path}...")
        result = _process_image(rel_path, work_dir, model=args.model)
        if result["erreur"]:
            print(f"  echec: {result['erreur']}")
        else:
            print(f"  {result['categorie']} -> {result['fichier']}")
        results.append(result)

    updated_markdown = _update_pivot(markdown, results)
    pivot_path.write_text(updated_markdown, encoding="utf-8")
    meta_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    n_formules = sum(1 for r in results if r["categorie"] == "formule")
    n_generales = sum(1 for r in results if r["categorie"] == "generale")
    n_errors = sum(1 for r in results if r["erreur"])

    status_lib.mark_stage(
        work_dir, "images", "done" if n_errors == 0 else "failed",
        detail="" if n_errors == 0 else f"{n_errors} image(s) en echec",
        n_images=len(results), n_formules=n_formules, n_generales=n_generales, n_errors=n_errors,
    )

    print(f"OK: {len(results)} image(s) traitee(s) ({n_formules} formule(s), {n_generales} generale(s), {n_errors} echec(s))")
    print(f"images_meta.json: {meta_path}")
    if n_errors:
        print("Images en echec (a corriger avant d'enchainer sur /rag-chunking) :", file=sys.stderr)
        for r in results:
            if r["erreur"]:
                print(f"  {r['fichier_original']}: {r['erreur']}", file=sys.stderr)

    return 1 if n_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
