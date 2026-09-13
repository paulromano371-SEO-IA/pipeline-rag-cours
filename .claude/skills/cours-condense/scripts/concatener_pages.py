"""
Etape 1 (mecanique) du pipeline cours-condense : concatene les fichiers
pages/page_NNNN.txt d'une plage donnee en un seul fichier a lire, avec les
bornes de chapitre determinees a partir de la table des matieres de
structure.json (entrees niveau: 1) plutot qu'un decoupage arbitraire.

Remplace la boucle Bash (seq/printf) refaite a la main a chaque lancement,
qui a produit un bug reel en session : `seq -w` change de largeur de
padding des qu'une plage franchit un palier de dizaine/centaine (ex.
chapitre couvrant les pages 90 a 107), cassant les chemins de fichiers
page_0090.txt vs page_090.txt. Ici le padding sur 4 chiffres est fixe,
identique a celui ecrit par extraire_structure.py (`page_{n:04d}.txt`),
jamais recalcule par une largeur de plage.

Usage:
    python concatener_pages.py <nom_du_livre> <page_debut> <page_fin> [<fichier_sortie>]

<nom_du_livre> est le dossier de travail (ex. rag_data/courscondense/mon_livre),
tel que renvoye par initialiser_arborescence.py.
<page_debut>/<page_fin> sont 1-indexes, inclusifs.
<fichier_sortie> est optionnel ; par defaut
<nom_du_livre>/extraction/lot_<page_debut>_<page_fin>.txt.

Chaque page est suivie d'un marqueur "=== FIN PAGE N ===" pour reperer les
bornes de page pendant la lecture, sans quoi le decoupage precis par page
serait perdu dans un bloc de texte continu.
"""
import sys
import os


def main():
    if len(sys.argv) not in (4, 5):
        print("Usage: python concatener_pages.py <nom_du_livre> <page_debut> <page_fin> [<fichier_sortie>]")
        sys.exit(1)

    nom_du_livre = sys.argv[1]
    page_debut = int(sys.argv[2])
    page_fin = int(sys.argv[3])

    if page_debut < 1 or page_fin < page_debut:
        print(f"ERREUR: plage de pages invalide ({page_debut}-{page_fin})", file=sys.stderr)
        sys.exit(1)

    pages_dir = os.path.join(nom_du_livre, "extraction", "pages")
    if len(sys.argv) == 5:
        fichier_sortie = sys.argv[4]
    else:
        fichier_sortie = os.path.join(nom_du_livre, "extraction", f"lot_{page_debut}_{page_fin}.txt")

    manquantes = []
    morceaux = []
    for n in range(page_debut, page_fin + 1):
        chemin_page = os.path.join(pages_dir, f"page_{n:04d}.txt")
        if not os.path.isfile(chemin_page):
            manquantes.append(chemin_page)
            continue
        with open(chemin_page, "r", encoding="utf-8") as f:
            contenu = f.read()
        morceaux.append(contenu)
        morceaux.append(f"\n\n=== FIN PAGE {n} ===\n\n")

    if manquantes:
        print("ERREUR: pages introuvables :", file=sys.stderr)
        for m in manquantes:
            print(f"  {m}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(os.path.dirname(fichier_sortie), exist_ok=True)
    with open(fichier_sortie, "w", encoding="utf-8") as f:
        f.write("".join(morceaux))

    print(f"OK: pages {page_debut} a {page_fin} ({page_fin - page_debut + 1} pages) -> {fichier_sortie}")


if __name__ == "__main__":
    main()
