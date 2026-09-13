"""Publication fiable du cours condense produit par /cours-condense vers le
catalogue plat corpuscondense/.

Le dossier de travail de /cours-condense est redirige (par instruction
d'invocation, /cours-condense lui-meme n'est pas modifie) vers
rag_data/courscondense/<slug>/ plutot que la racine du projet. A l'interieur,
le skill cree son propre sous-dossier dont il choisit le nom (sa propre
slugification du titre du livre, potentiellement differente de <slug>) —
observe en pratique sur un run reel (voir RAG-PIPELINE-ARCHITECTURE.md,
section 16bis). On ne presuppose donc jamais la profondeur exacte de
out/*.pdf : recherche recursive, PDF le plus recent retenu en cas de
plusieurs candidats (residu d'une tentative precedente).

Usage:
    python publish_condense.py <rag_data/courscondense/<slug>/ | source.pdf> [--dest-name NOM.pdf]
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths


def find_output_pdf(work_dir: Path) -> Path | None:
    candidates = sorted(work_dir.rglob("out/*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def publish(work_dir: Path, *, dest_name: str | None = None) -> Path:
    pdf = find_output_pdf(work_dir)
    if pdf is None:
        raise FileNotFoundError(
            f"aucun PDF trouve sous {work_dir} (attendu : un fichier out/*.pdf, "
            "cherche recursivement — /cours-condense a-t-il bien termine ?)"
        )

    paths.CORPUS_CONDENSE_DIR.mkdir(parents=True, exist_ok=True)
    dest = paths.CORPUS_CONDENSE_DIR / (dest_name or f"{work_dir.name}.pdf")
    shutil.copy2(pdf, dest)
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="dossier de travail rag_data/courscondense/<slug>/, ou PDF source (pour en deriver le slug)")
    parser.add_argument("--dest-name", type=str, default=None, help="nom de fichier dans corpuscondense/ (defaut: <slug>.pdf)")
    args = parser.parse_args()

    input_path = args.path.resolve()
    if input_path.is_dir():
        work_dir = input_path
    elif input_path.suffix.lower() == ".pdf":
        work_dir = paths.courscondense_work_dir_for(input_path)
    else:
        print(f"ERREUR: chemin invalide: {input_path}", file=sys.stderr)
        return 1

    if not work_dir.exists():
        print(f"ERREUR: dossier de travail introuvable: {work_dir}", file=sys.stderr)
        return 1

    try:
        dest = publish(work_dir, dest_name=args.dest_name)
    except FileNotFoundError as exc:
        print(f"ERREUR: {exc}", file=sys.stderr)
        return 1

    print(f"OK: publie vers {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
