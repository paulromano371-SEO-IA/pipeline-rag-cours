"""Verification LEXICALE d'une lacune presumee du corpus -- lecture seule.

Avant d'ecrire « le corpus ne couvre pas X » (rapport final de /create-content),
on le verifie ici SANS passer par la recherche semantique ni par la memoire du
modele : le script parcourt directement les `chunks.json` de tous les pdf
(`rag_data/work/*/chunks.json`) et compte les chunks qui contiennent chaque
terme (insensible a la casse et aux accents).

    python check_gap.py "<sujet>" "<terme 1>" ["<terme 2>" ...] [--max-hits N]

- `<sujet>` : expression qui doit apparaitre dans le meme chunk pour que le
  terme compte comme « lie au sujet » (ex. "regression logistique").
- chaque `<terme>` est cherche tel quel ; plusieurs formes se separent par `|`
  (ex. "softmax|multinomial").

Sortie par terme : nombre de chunks contenant le terme (tous pdf), dont
combien mentionnent aussi le sujet, et quelques extraits. Verdict a rapporter :
  - 0 chunk                  -> « absent du corpus » (verifie par recherche directe)
  - chunks sans le sujet     -> le terme existe mais pas rattache au sujet
  - chunks avec le sujet     -> matiere EXISTANTE : relancer gather.py avec une
                                sous-requete reprenant ces mots, ne pas annoncer une lacune
Sans verification, la formule correcte est « non remonte par mes requetes ».
"""
from __future__ import annotations
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(next(p for p in _Path(__file__).resolve().parents if (p / ".claude" / "skills" / "_rag_lib").is_dir()) / ".claude" / "skills" / "_rag_lib"))
import venv_guard  # noqa: F401

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import paths  # noqa: E402


def fold(text: str) -> str:
    """Minuscules sans accents, espaces normalises."""
    s = unicodedata.normalize("NFKD", text.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("subject", type=str)
    ap.add_argument("terms", nargs="+", type=str)
    ap.add_argument("--max-hits", type=int, default=3, help="extraits affiches par terme")
    args = ap.parse_args()

    subject_alts = [fold(a) for a in args.subject.split("|") if a.strip()]
    term_alts = {t: [fold(a) for a in t.split("|") if a.strip()] for t in args.terms}

    chunk_files = sorted(paths.WORK_DIR.glob("*/chunks.json"))
    if not chunk_files:
        print("ECHEC : aucun chunks.json sous rag_data/work/ -- corpus non ingere ?")
        return 3

    stats = {t: {"all": 0, "with_subject": 0, "hits": []} for t in args.terms}
    n_chunks = 0
    for cf in chunk_files:
        try:
            chunks = json.loads(cf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        doc_id = cf.parent.name
        for ch in chunks:
            n_chunks += 1
            text = fold(ch.get("text", ""))
            has_subject = any(a in text for a in subject_alts)
            for t, alts in term_alts.items():
                m = next((a for a in alts if a in text), None)
                if m is None:
                    continue
                st = stats[t]
                st["all"] += 1
                if has_subject:
                    st["with_subject"] += 1
                if len(st["hits"]) < args.max_hits and (has_subject or not any(h[3] for h in st["hits"])):
                    i = text.find(m)
                    snippet = text[max(0, i - 80): i + 120]
                    st["hits"].append((re.sub(r"__[0-9a-f]{8}$", "", doc_id), ch.get("index"),
                                       " > ".join(ch.get("heading_trail", []))[:70], has_subject, snippet))

    print(f"Verification lexicale : {len(chunk_files)} livre(s), {n_chunks} chunk(s) parcourus ; sujet = {args.subject!r}")
    for t in args.terms:
        st = stats[t]
        if st["all"] == 0:
            verdict = "ABSENT du corpus (recherche directe)"
        elif st["with_subject"] == 0:
            verdict = "present mais jamais rattache au sujet"
        else:
            verdict = "PRESENT et rattache au sujet -- ne PAS annoncer de lacune, relancer gather.py avec ces mots"
        print(f"\n- {t!r} : {st['all']} chunk(s), dont {st['with_subject']} avec le sujet -> {verdict}")
        for doc, idx, trail, with_subj, snip in st["hits"]:
            print(f"    [{doc} #{idx}] {trail}{' (sujet)' if with_subj else ''} : ...{snip}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
