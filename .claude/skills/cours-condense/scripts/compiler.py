"""
Etape 6 (mecanique) / Prompt 3 - Etape 2 : compilation pdflatex x2 + rapport.

Ecrit en Python (pas en .sh) pour rester portable quel que soit le shell
utilise (PowerShell, Bash Git, etc.) sur cette machine Windows.

Usage:
    python compiler.py <chemin_course.tex> <nom_pdf_final_sans_extension>

Le .tex doit deja se trouver dans le dossier ou l'on veut compiler (les
images referencees par \\includegraphics sont relatives a ce dossier).
Produit <dossier>/out/<nom_pdf_final>.pdf et nettoie les fichiers auxiliaires.

IMPORTANT : le PATH par defaut de cette machine contient une entree cassee
("...\\.local\\bin\\claude.exe" listee comme si c'etait un dossier) qui fait
planter MiKTeX des le demarrage ("cannot retrieve attributes for the
directory"). On reconstruit donc un PATH nettoye avant tout appel a
pdflatex, et on y ajoute le dossier bin de MiKTeX au cas ou il ne serait pas
deja dans le PATH systeme/utilisateur de la session courante.
"""
import sys
import os
import subprocess
import re
import shutil


def build_clean_env():
    env = os.environ.copy()
    entries = [e for e in env.get("PATH", "").split(os.pathsep) if "claude.exe" not in e.lower()]
    miktex_bin = os.path.join(os.path.expanduser("~"), "AppData", "Local", "Programs",
                               "MiKTeX", "miktex", "bin", "x64")
    if os.path.isdir(miktex_bin) and miktex_bin not in entries:
        entries.append(miktex_bin)
    env["PATH"] = os.pathsep.join(entries)
    return env


MAX_PASSES = 5
RERUN_MARKERS = (
    "Rerun to get cross-references right",
    "Rerun to get outlines right",
    "Label(s) may have changed",
    "There were undefined references",
)


def run_pdflatex(pdflatex_exe, tex_path, tex_dir, env, log_path):
    cmd = [pdflatex_exe, "-interaction=nonstopmode", "-halt-on-error",
           f"-output-directory={tex_dir}", tex_path]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    output = (result.stdout or "") + (result.stderr or "")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(output)
    return result.returncode


def extraire_lignes(log_path, pattern, exclude=None):
    if not os.path.exists(log_path):
        return []
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        lignes = f.readlines()
    trouve = [l.rstrip("\n") for l in lignes if re.search(pattern, l)]
    if exclude:
        trouve = [l for l in trouve if exclude not in l]
    return trouve


def needs_rerun(log_path):
    """Vrai si le log de cette passe indique que LaTeX n'a pas fini de
    stabiliser ses references croisees (sommaire, numeros de figure/section) :
    dans ce cas une passe de plus est necessaire, sinon les numeros de page
    du sommaire (et toute reference \\ref/\\cref) restent ceux de la passe
    precedente, potentiellement decales par rapport a la pagination reelle."""
    if not os.path.exists(log_path):
        return False
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        contenu = f.read()
    return any(marqueur in contenu for marqueur in RERUN_MARKERS)


def compter_pages(pdf_path):
    """pypdf n'est pas installe dans .venv-rag sur ce poste ; pymupdf (fitz)
    l'est deja (utilise par extraire_structure.py) et sert de solution de
    repli fiable plutot que de dependre d'un paquet absent."""
    try:
        import pypdf
        return len(pypdf.PdfReader(pdf_path).pages)
    except Exception:
        pass
    try:
        import pymupdf
        doc = pymupdf.open(pdf_path)
        return doc.page_count
    except Exception:
        return None


def main():
    if len(sys.argv) != 3:
        print("Usage: python compiler.py <chemin_course.tex> <nom_pdf_final_sans_extension>")
        sys.exit(1)

    tex_path = sys.argv[1]
    nom_sortie = sys.argv[2]

    tex_dir = os.path.dirname(tex_path) or "."
    tex_file = os.path.basename(tex_path)
    tex_base = os.path.splitext(tex_file)[0]

    env = build_clean_env()
    pdflatex_exe = shutil.which("pdflatex", path=env["PATH"])
    if not pdflatex_exe:
        print("ECHEC: pdflatex introuvable dans le PATH nettoye.")
        sys.exit(1)

    logs = []
    status = None
    for i in range(1, MAX_PASSES + 1):
        log_i = os.path.join(tex_dir, f"{tex_base}.pass{i}.log")
        logs.append(log_i)
        print(f"=== Passe {i} ===")
        status = run_pdflatex(pdflatex_exe, tex_path, tex_dir, env, log_i)
        if status != 0:
            break
        if i >= 2 and not needs_rerun(log_i):
            break
    else:
        print(f"AVERTISSEMENT: references non stabilisees apres {MAX_PASSES} passes "
              "(cas rare) ; le sommaire/les numeros de reference peuvent rester decales.")

    log_final = logs[-1]
    nb_passes = len(logs)

    print()
    print(f"--- Resultat (passe {nb_passes}/{nb_passes} effectuees): {'OK' if status == 0 else 'ECHEC'} ---")

    print()
    print(f"--- Erreurs (passe {nb_passes}) ---")
    erreurs = extraire_lignes(log_final, r"^! ")
    print("\n".join(erreurs) if erreurs else "(aucune)")

    print()
    print(f"--- Warnings LaTeX notables (passe {nb_passes}) ---")
    warnings = extraire_lignes(log_final, r"Warning", exclude="There were undefined")
    print("\n".join(warnings) if warnings else "(aucun)")

    pdf_path = os.path.join(tex_dir, f"{tex_base}.pdf")

    if status != 0 or not os.path.exists(pdf_path):
        print()
        print(f"ECHEC: pas de PDF genere, voir {log_final} pour le detail.")
        sys.exit(1)

    print()
    print("--- Nombre de pages du PDF genere ---")
    nb_pages = compter_pages(pdf_path)
    print(nb_pages if nb_pages is not None else "(impossible de compter les pages : ni pypdf ni pymupdf disponibles)")

    out_dir = os.path.join(tex_dir, "out")
    os.makedirs(out_dir, exist_ok=True)
    final_pdf = os.path.join(out_dir, f"{nom_sortie}.pdf")
    shutil.copy(pdf_path, final_pdf)
    print()
    print(f"PDF livre: {final_pdf}")

    for suffix in (".aux", ".log", ".toc", ".out"):
        p = os.path.join(tex_dir, f"{tex_base}{suffix}")
        if os.path.exists(p):
            os.remove(p)
    for p in logs:
        if os.path.exists(p):
            os.remove(p)

    print("Fichiers auxiliaires supprimes.")


if __name__ == "__main__":
    main()
