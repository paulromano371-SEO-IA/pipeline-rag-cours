"""
Etape 2 (mecanique) du pipeline cours-condense : verifie, apres generation
des illustrations, que les regles impératives sans jugement de cette etape
ont bien ete respectees. Controle mecanique, pas une correction automatique
(sur le meme modele que verifier_calibrage_visuels.py) : ne modifie rien,
signale les anomalies.

Verifie, pour chaque chapitre de plan_cours.json :
  - Regle 0 : exactement une illustration par entree de concepts_cles_visuels
    (ni plus, ni moins) -- rien ne comparait ce compte avant ce script.
  - Charte de nommage : chaque fichier suit chNN_concept.png.
  - Regle 4 (proprete du code) : matplotlib.use('Agg'), plt.close(), dpi=300
    et une sortie .png sont bien presents dans le script
    illustrations_chapitreN.py correspondant.
  - Regle 2 (charte couleur stricte) : aucun code hexadecimal de couleur
    utilise dans le script en dehors de #1a365d (bleu) et #c25e00 (orange).

Usage:
    python verifier_illustrations.py <nom_du_livre> <plan_cours.json>

Sortie : rapport JSON sur stdout, avec "anomalies" (liste, vide si tout est
correct) et "pret" (bool).
"""
import sys
import os
import re
import json


COULEURS_AUTORISEES = {"1a365d", "c25e00"}
NOM_FICHIER_RE = re.compile(r"^ch\d{2}_[a-z0-9_]+\.png$")
HEX_COLOR_RE = re.compile(r"#([0-9a-fA-F]{6})\b")


def verifier_chapitre(nom_du_livre, chapitre):
    numero = chapitre.get("numero")
    titre = chapitre.get("titre", "?")
    concepts = chapitre.get("concepts_cles_visuels", [])
    prefixe = f"chapitre {numero} ('{titre}')"
    anomalies = []

    if numero is None:
        return [f"chapitre sans champ 'numero' (titre '{titre}') -- impossible de verifier ses illustrations"]

    illustrations_dir = os.path.join(nom_du_livre, "illustrations")
    # Prefixe large (sans exiger le "_") pour qu'un separateur fautif (tiret,
    # absence de separateur...) soit rattache au chapitre et signale par la
    # verification de convention ci-dessous, plutot que de disparaitre
    # silencieusement du compte (regle 0) faute de correspondre au filtre.
    prefixe_fichier = f"ch{numero:02d}"
    pngs = []
    if os.path.isdir(illustrations_dir):
        pngs = sorted(
            f for f in os.listdir(illustrations_dir)
            if f.startswith(prefixe_fichier) and f.lower().endswith(".png")
        )

    if len(pngs) != len(concepts):
        anomalies.append(
            f"{prefixe} : {len(concepts)} concept(s) visuel(s) au plan mais "
            f"{len(pngs)} illustration(s) trouvee(s) dans illustrations/ ({pngs})"
        )

    for f in pngs:
        if not NOM_FICHIER_RE.match(f):
            anomalies.append(f"{prefixe} : fichier '{f}' ne respecte pas la convention chNN_concept.png")

    if not concepts:
        return anomalies

    script_path = os.path.join(nom_du_livre, "scripts", f"illustrations_chapitre{numero}.py")
    if not os.path.isfile(script_path):
        anomalies.append(f"{prefixe} : script attendu introuvable ({script_path})")
        return anomalies

    with open(script_path, "r", encoding="utf-8") as f:
        code = f.read()
    nom_script = os.path.basename(script_path)

    # DiagramBuilder.save() (illustration_utils.py) applique deja dpi=300 et
    # plt.close() en interne : un script qui l'utilise n'a pas a repeter ces
    # appels lui-meme pour respecter la regle 4.
    utilise_diagram_builder_save = "DiagramBuilder" in code and re.search(r"\.save\(", code)

    if "matplotlib.use('Agg')" not in code and 'matplotlib.use("Agg")' not in code:
        anomalies.append(f"{prefixe} : matplotlib.use('Agg') absent de {nom_script}")
    if "plt.close(" not in code and not utilise_diagram_builder_save:
        anomalies.append(f"{prefixe} : plt.close() absent de {nom_script} (et n'utilise pas DiagramBuilder.save())")
    if "dpi=300" not in code and not utilise_diagram_builder_save:
        anomalies.append(f"{prefixe} : dpi=300 absent de {nom_script} (et n'utilise pas DiagramBuilder.save())")
    if ".png" not in code:
        anomalies.append(f"{prefixe} : aucune sortie .png explicite dans {nom_script}")

    hex_codes = {m.group(1).lower() for m in HEX_COLOR_RE.finditer(code)}
    hors_charte = sorted(hex_codes - COULEURS_AUTORISEES)
    if hors_charte:
        anomalies.append(
            f"{prefixe} : couleur(s) hors charte (ni #1a365d ni #c25e00) codee(s) en dur "
            f"dans {nom_script} : {hors_charte}"
        )

    return anomalies


def main():
    if len(sys.argv) != 3:
        print("Usage: python verifier_illustrations.py <nom_du_livre> <plan_cours.json>")
        sys.exit(1)

    nom_du_livre, plan_path = sys.argv[1], sys.argv[2]

    with open(plan_path, "r", encoding="utf-8") as f:
        plan = json.load(f)

    anomalies = []
    for chapitre in plan.get("chapitres", []):
        anomalies.extend(verifier_chapitre(nom_du_livre, chapitre))

    rapport = {"anomalies": anomalies, "pret": len(anomalies) == 0}
    print(json.dumps(rapport, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
