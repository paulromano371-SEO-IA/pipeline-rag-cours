"""
Etape 1 (mecanique) : verification du calibrage du nombre de concepts
visuels (`concepts_cles_visuels`) de chaque chapitre de `plan_cours.json`
par rapport a la densite de figures du livre source (`nb_mentions_figure`
de `structure.json`).

Ce script ne corrige rien lui-meme (contrairement a controle_qualite.py sur
les doublons hyperref) : il n'existe aucune regle non ambigue pour convertir
un nombre de mentions en nombre d'illustrations (une mention peut etre une
capture d'ecran sans structure, ou un renvoi repete a la meme figure). Son
role est de rendre visible, de facon mecanique et systematique, tout
chapitre dont le nombre de concepts visuels parait bas au regard des autres
chapitres du meme livre -- pour forcer une relecture ciblee plutot qu'un
oubli silencieux.

Prerequis : chaque chapitre de plan_cours.json doit porter un champ
"pages_source": [debut, fin] (page de debut et de fin dans le PDF source,
1-indexe, inclusif) en plus des champs deja requis par le skill.

Usage:
    python verifier_calibrage_visuels.py <plan_cours.json> <structure.json>

Sortie: rapport JSON sur stdout, a coller/resumer dans le compte-rendu du
checkpoint de l'etape 1 (tableau chapitre / mentions / illustrations).
"""
import sys
import json
import statistics


def main():
    if len(sys.argv) != 3:
        print("Usage: python verifier_calibrage_visuels.py <plan_cours.json> <structure.json>")
        sys.exit(1)

    plan_path, structure_path = sys.argv[1], sys.argv[2]

    with open(plan_path, "r", encoding="utf-8") as f:
        plan = json.load(f)
    with open(structure_path, "r", encoding="utf-8") as f:
        structure = json.load(f)

    mentions_par_page = {p["page"]: p.get("nb_mentions_figure", 0) for p in structure["pages"]}

    chapitres = plan.get("chapitres", [])
    lignes = []
    chapitres_sans_pages_source = []

    for ch in chapitres:
        pages_source = ch.get("pages_source")
        nb_visuels = len(ch.get("concepts_cles_visuels", []))
        if not pages_source or len(pages_source) != 2:
            chapitres_sans_pages_source.append(ch.get("numero"))
            somme_mentions = None
        else:
            debut, fin = pages_source
            somme_mentions = sum(mentions_par_page.get(p, 0) for p in range(debut, fin + 1))
        lignes.append({
            "numero": ch.get("numero"),
            "titre": ch.get("titre"),
            "pages_source": pages_source,
            "somme_nb_mentions_figure": somme_mentions,
            "nb_concepts_visuels": nb_visuels,
        })

    # Ratio illustrations / mentions, uniquement sur les chapitres avec mentions > 0,
    # pour reperer les chapitres nettement sous-dotes par rapport a la mediane du livre.
    ratios = [
        l["nb_concepts_visuels"] / l["somme_nb_mentions_figure"]
        for l in lignes
        if l["somme_nb_mentions_figure"] not in (None, 0)
    ]
    mediane_ratio = statistics.median(ratios) if ratios else None

    chapitres_a_verifier = []
    if mediane_ratio is not None:
        for l in lignes:
            if l["somme_nb_mentions_figure"] in (None, 0):
                continue
            ratio = l["nb_concepts_visuels"] / l["somme_nb_mentions_figure"]
            # Seuil : moins de 40% de la mediane du livre ET au moins 2 fois moins
            # de visuels que ce que la mediane suggererait -- evite de signaler des
            # chapitres courts ou peu figures a raison.
            if ratio < 0.4 * mediane_ratio and l["nb_concepts_visuels"] <= max(1, round(mediane_ratio * l["somme_nb_mentions_figure"] * 0.5)):
                chapitres_a_verifier.append(l["numero"])

    rapport = {
        "tableau": lignes,
        "total_concepts_visuels": sum(l["nb_concepts_visuels"] for l in lignes),
        "mediane_ratio_illustrations_par_mention": mediane_ratio,
        "chapitres_a_verifier": chapitres_a_verifier,
        "chapitres_sans_pages_source": chapitres_sans_pages_source,
        "avertissement": (
            "Chapitres a verifier : leur nombre de concepts visuels parait bas "
            "au regard de la densite de figures du livre source pour ce chapitre, "
            "comparee aux autres chapitres. Relire ces chapitres pour un concept "
            "structurel manque, ou justifier explicitement l'ecart dans le "
            "compte-rendu du checkpoint (ex. chapitre a forte densite d'exemples "
            "textuels ou de captures d'ecran sans structure)."
            if chapitres_a_verifier else
            "Aucun chapitre signale : la repartition des concepts visuels est "
            "coherente avec la densite de figures du livre source."
        ),
    }

    print(json.dumps(rapport, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
