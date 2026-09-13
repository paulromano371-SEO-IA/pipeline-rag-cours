"""
Etape 1 (mecanique) du pipeline cours-condense : validation structurelle de
plan_cours.json une fois ecrit, avant de passer a l'etape 2.

Controle mecanique, pas une correction automatique : ne modifie rien,
signale les anomalies. Complementaire de verifier_calibrage_visuels.py (qui
porte sur concepts_cles_visuels), celui-ci porte sur la structure et la
coherence chronologique du plan lui-meme :
  - champs obligatoires presents et du bon type pour chaque chapitre ;
  - pages_source valide (borne_debut <= borne_fin, dans [1, nb_pages]) ;
  - chapitres strictement ordonnes et sans chevauchement de pages source
    entre eux (la regle "respecte strictement la chronologie du livre
    original" de l'etape 1 devient ici verifiable plutot que laissee a
    l'auto-discipline).

N'exige PAS une couverture totale du livre sans le moindre trou : une
preface, une table des matieres, des references ou un index legitimement
exclus du plan condense creent des trous attendus. Un chevauchement de
pages entre deux chapitres, en revanche, n'est jamais legitime -- deux
chapitres ne peuvent pas tous les deux "couvrir" la meme page source.

Usage:
    python verifier_plan_cours.py <plan_cours.json> <structure.json>

Sortie : rapport JSON sur stdout, avec une cle "anomalies" (liste, vide si
tout est correct) et "pret" (bool).
"""
import sys
import json


CHAMPS_ATTENDUS = {
    "numero": int,
    "titre": str,
    "sections_source": list,
    "pages_source": list,
    "contient_code": bool,
    "concepts_cles_visuels": list,
}


def verifier_chapitre(chapitre, index, nb_pages):
    anomalies = []
    prefixe = f"chapitre[{index}]"

    for champ, type_attendu in CHAMPS_ATTENDUS.items():
        if champ not in chapitre:
            anomalies.append(f"{prefixe} : champ '{champ}' manquant")
            continue
        if not isinstance(chapitre[champ], type_attendu):
            anomalies.append(
                f"{prefixe} : champ '{champ}' devrait etre de type {type_attendu.__name__}, "
                f"trouve {type(chapitre[champ]).__name__}"
            )

    titre = chapitre.get("titre", f"(sans titre, {prefixe})")

    numero = chapitre.get("numero")
    if isinstance(numero, int) and not isinstance(numero, bool) and numero != index + 1:
        anomalies.append(
            f"{prefixe} '{titre}' : champ 'numero'={numero} ne correspond pas a sa position "
            f"1-indexee dans la liste 'chapitres' ({index + 1}) -- verifier_illustrations.py et "
            "verifier_structure_course_tex.py identifient/nomment les fichiers par ce numero, il "
            "doit rester coherent avec l'ordre chronologique du plan"
        )

    pages_source = chapitre.get("pages_source")
    if isinstance(pages_source, list):
        if len(pages_source) != 2 or not all(isinstance(p, int) for p in pages_source):
            anomalies.append(f"{prefixe} '{titre}' : pages_source doit etre [debut, fin] avec deux entiers")
        else:
            debut, fin = pages_source
            if debut < 1:
                anomalies.append(f"{prefixe} '{titre}' : pages_source[0]={debut} < 1")
            if fin < debut:
                anomalies.append(f"{prefixe} '{titre}' : pages_source fin ({fin}) < debut ({debut})")
            if nb_pages is not None and fin > nb_pages:
                anomalies.append(
                    f"{prefixe} '{titre}' : pages_source fin ({fin}) > nb_pages du livre ({nb_pages})"
                )

    if isinstance(chapitre.get("titre"), str) and not chapitre["titre"].strip():
        anomalies.append(f"{prefixe} : titre vide")

    if isinstance(chapitre.get("sections_source"), list) and len(chapitre["sections_source"]) == 0:
        anomalies.append(f"{prefixe} '{titre}' : sections_source vide")

    return anomalies


def verifier_ordre_et_chevauchements(chapitres):
    anomalies = []
    precedent = None
    for i, chapitre in enumerate(chapitres):
        pages_source = chapitre.get("pages_source")
        if not (isinstance(pages_source, list) and len(pages_source) == 2):
            continue
        debut, fin = pages_source
        titre = chapitre.get("titre", f"(chapitre {i})")
        if precedent is not None:
            debut_prec, fin_prec, titre_prec = precedent
            if debut <= fin_prec:
                anomalies.append(
                    f"chevauchement/desordre chronologique : '{titre_prec}' (pages {debut_prec}-{fin_prec}) "
                    f"et '{titre}' (pages {debut}-{fin}) se chevauchent ou ne sont pas dans l'ordre du livre"
                )
        precedent = (debut, fin, titre)
    return anomalies


def main():
    if len(sys.argv) != 3:
        print("Usage: python verifier_plan_cours.py <plan_cours.json> <structure.json>")
        sys.exit(1)

    plan_path, structure_path = sys.argv[1], sys.argv[2]

    with open(plan_path, "r", encoding="utf-8") as f:
        plan = json.load(f)
    with open(structure_path, "r", encoding="utf-8") as f:
        structure = json.load(f)

    nb_pages = structure.get("nb_pages")
    chapitres = plan.get("chapitres")

    anomalies = []
    if not isinstance(chapitres, list) or len(chapitres) == 0:
        anomalies.append("plan_cours.json : cle 'chapitres' absente, vide, ou n'est pas une liste")
        chapitres = []

    for i, chapitre in enumerate(chapitres):
        anomalies.extend(verifier_chapitre(chapitre, i, nb_pages))

    anomalies.extend(verifier_ordre_et_chevauchements(chapitres))

    rapport = {
        "nb_chapitres": len(chapitres),
        "nb_pages_livre": nb_pages,
        "anomalies": anomalies,
        "pret": len(anomalies) == 0,
    }
    print(json.dumps(rapport, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
