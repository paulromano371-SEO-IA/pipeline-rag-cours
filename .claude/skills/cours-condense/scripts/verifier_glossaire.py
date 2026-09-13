"""
Etape 3 (mecanique), regle 3 (partie verification) : verifie que course.tex
ne contient aucune variante interdite d'un terme deja fixe dans
glossaire.json. Decider une traduction reste un jugement (regle 3
elle-meme) ; verifier qu'on s'y est tenu partout ensuite ne l'est pas.

Schema attendu de glossaire.json, deux formes acceptees pour chaque entree :
    "agentic AI": "IA agentique"
ou, quand une variante incorrecte connue doit explicitement etre proscrite
(cas documente dans SKILL.md : "agentic AI" -> toujours "IA agentique",
jamais "IA agentielle") :
    "agentic AI": {"traduction": "IA agentique", "interdits": ["IA agentielle"]}

Sans "interdits" renseigne pour un terme, ce script ne peut rien verifier
sur ce terme (il ne devine aucune variante fautive de lui-meme) : chaque
fois qu'une confusion de traduction est identifiee pendant la redaction,
consigne la variante fautive dans "interdits" pour que ce controle la
detecte automatiquement dans tout le reste du cours, chapitre suivants
compris.

Usage:
    python verifier_glossaire.py <glossaire.json> <course.tex>

Sortie : rapport JSON sur stdout, avec "anomalies" (liste, vide si tout est
correct) et "pret" (bool). Si glossaire.json est introuvable, le rapport le
signale explicitement plutot que d'echouer silencieusement.
"""
import sys
import os
import re
import json


def ligne_de(texte, index_char):
    return texte.count("\n", 0, index_char) + 1


def main():
    if len(sys.argv) != 3:
        print("Usage: python verifier_glossaire.py <glossaire.json> <course.tex>")
        sys.exit(1)

    glossaire_path, course_tex_path = sys.argv[1], sys.argv[2]

    if not os.path.isfile(glossaire_path):
        rapport = {
            "anomalies": [f"glossaire.json introuvable ({glossaire_path}) -- aucune coherence verifiable"],
            "pret": False,
        }
        print(json.dumps(rapport, ensure_ascii=False, indent=2))
        return

    with open(glossaire_path, "r", encoding="utf-8") as f:
        glossaire = json.load(f)
    with open(course_tex_path, "r", encoding="utf-8") as f:
        texte = f.read()

    anomalies = []
    for terme, valeur in glossaire.items():
        if isinstance(valeur, str):
            interdits = []
        elif isinstance(valeur, dict):
            interdits = valeur.get("interdits", [])
        else:
            anomalies.append(f"glossaire['{terme}'] : valeur d'un type inattendu ({type(valeur).__name__})")
            continue

        for variante_interdite in interdits:
            for m in re.finditer(re.escape(variante_interdite), texte, re.IGNORECASE):
                ligne = ligne_de(texte, m.start())
                contexte = texte[max(0, m.start() - 40):m.start() + 40].replace("\n", " ")
                anomalies.append(
                    f"ligne {ligne} : variante interdite '{variante_interdite}' pour le terme "
                    f"'{terme}' (traduction fixee : voir glossaire.json) -- contexte: ...{contexte}..."
                )

    rapport = {"anomalies": anomalies, "pret": len(anomalies) == 0}
    print(json.dumps(rapport, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
