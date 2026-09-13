"""
Etape 3/4 (mecanique) du pipeline cours-condense : verifications
structurelles sur course.tex une fois la redaction terminee. Controle
mecanique, pas une correction automatique : ne modifie rien, signale les
anomalies. Regroupe plusieurs points de pattern-matching pur, sans aucun
jugement de qualite editoriale :

  - Regle 11 (etape 3) : chaque chapitre numerote se termine par un
    \\section*{Resume} avant le chapitre suivant. Jusqu'ici verifie
    seulement par une instruction de relecture manuelle a l'etape 6 --
    exactement le type de regle qui a deja echoue une fois pour le
    calibrage visuel (etape 1) et qu'on avait du rendre mecanique.
  - Regle 6 (partie couverture) : un chapitre `contient_code: true` dans
    plan_cours.json a bien au moins un lstlisting dans la section
    correspondante de course.tex (et inversement, signale un lstlisting
    dans un chapitre marque `contient_code: false`).
  - Regle 6 (partie structurelle) : chaque `\\end{lstlisting}` est suivi,
    avant le prochain lstlisting/section, d'une phrase d'explication (pas
    seulement d'une figure ou d'un saut direct vers autre chose).
  - Regle 7 (partie structurelle) : chaque `\\includegraphics` est bien
    dans un environnement `figure` avec `\\caption{}` et `\\label{}` ; aucune
    figure n'est inseree entre deux lstlisting consecutifs sans rupture de
    section (\\section/\\subsection/\\chapter) entre eux.

Usage:
    python verifier_structure_course_tex.py <course.tex> <plan_cours.json>

Sortie : rapport JSON sur stdout, avec "anomalies" (liste, vide si tout est
correct) et "pret" (bool).
"""
import sys
import os
import re
import json


CHAPTER_RE = re.compile(r"\\chapter\{([^}]*)\}")
LSTLISTING_OPEN_RE = re.compile(r"\\begin\{lstlisting\}")
LSTLISTING_CLOSE_RE = re.compile(r"\\end\{lstlisting\}")
FIGURE_BLOCK_RE = re.compile(r"\\begin\{figure\}.*?\\end\{figure\}", re.DOTALL)
# Tolerant a l'accent ou non (ce controle peut tourner avant ou apres la
# correction automatique des accents de l'etape 5).
RESUME_RE = re.compile(r"\\section\*\{R.sum.\}")
# Preuve mecanique d'une "phrase d'explication" : au moins 5 mots a la
# suite, plutot qu'un fragment de legende ou une commande LaTeX isolee.
PROSE_RE = re.compile(r"(?:[A-Za-zÀ-ÿ]+\s+){4,}[A-Za-zÀ-ÿ]+")
BOUNDARY_APRES_LISTING_RE = re.compile(
    r"(?P<listing>\\begin\{lstlisting\})|(?P<section>\\section|\\subsection|\\chapter)|(?P<fin>\\end\{document\})"
)


def ligne_de(texte, index_char):
    return texte.count("\n", 0, index_char) + 1


def decouper_chapitres(texte):
    matches = list(CHAPTER_RE.finditer(texte))
    chapitres = []
    for i, m in enumerate(matches):
        debut = m.end()
        fin = matches[i + 1].start() if i + 1 < len(matches) else len(texte)
        chapitres.append({"titre_tex": m.group(1), "texte": texte[debut:fin], "ligne": ligne_de(texte, m.start())})
    return chapitres


def check_chapitres_vs_plan(texte, plan_chapitres):
    anomalies = []
    chapitres_tex = decouper_chapitres(texte)

    if len(chapitres_tex) != len(plan_chapitres):
        anomalies.append(
            f"nombre de \\chapter{{}} dans course.tex ({len(chapitres_tex)}) "
            f"!= nombre de chapitres dans plan_cours.json ({len(plan_chapitres)}) "
            "-- verifications par chapitre non fiables tant que ce compte ne correspond pas"
        )
        return anomalies

    for chap_tex, chap_plan in zip(chapitres_tex, plan_chapitres):
        numero = chap_plan.get("numero", "?")
        titre_plan = chap_plan.get("titre", "?")
        bloc = chap_tex["texte"]
        a_du_code = bool(LSTLISTING_OPEN_RE.search(bloc))

        if chap_plan.get("contient_code") and not a_du_code:
            anomalies.append(
                f"chapitre {numero} ('{titre_plan}') : contient_code=true dans le plan "
                "mais aucun lstlisting trouve dans course.tex"
            )
        if chap_plan.get("contient_code") is False and a_du_code:
            anomalies.append(
                f"chapitre {numero} ('{titre_plan}') : contient_code=false dans le plan "
                "mais du lstlisting est present dans course.tex"
            )

        if not RESUME_RE.search(bloc):
            anomalies.append(
                f"chapitre {numero} ('{titre_plan}') : aucun \\section*{{Résumé}} trouve "
                "avant le chapitre suivant (règle 11)"
            )

    return anomalies


def check_listing_suivi_de_prose(texte):
    anomalies = []
    for m in LSTLISTING_CLOSE_RE.finditer(texte):
        suivant = BOUNDARY_APRES_LISTING_RE.search(texte, m.end())
        fin_segment = suivant.start() if suivant else len(texte)
        segment = texte[m.end():fin_segment]
        segment_sans_figures = FIGURE_BLOCK_RE.sub("", segment)
        if not PROSE_RE.search(segment_sans_figures):
            ligne = ligne_de(texte, m.end())
            anomalies.append(
                f"ligne {ligne} : bloc lstlisting non suivi d'une phrase d'explication "
                "avant le prochain listing/la prochaine section (règle 6)"
            )
    return anomalies


def check_figures(texte):
    """Partie fiable et bloquante de la regle 7 : structure figure/caption/label.
    Verifiee empiriquement sans faux positif sur ce cours."""
    anomalies = []
    for m in re.finditer(r"\\includegraphics", texte):
        ligne = ligne_de(texte, m.start())
        avant = texte.rfind(r"\begin{figure}", 0, m.start())
        apres = texte.find(r"\end{figure}", m.start())
        if avant == -1 or apres == -1:
            anomalies.append(f"ligne {ligne} : \\includegraphics hors d'un environnement figure (règle 7)")
            continue
        bloc_figure = texte[avant:apres]
        if r"\caption{" not in bloc_figure:
            anomalies.append(f"ligne {ligne} : figure sans \\caption{{}} (règle 7)")
        if r"\label{" not in bloc_figure:
            anomalies.append(f"ligne {ligne} : figure sans \\label{{}} (règle 7)")
    return anomalies


def check_figure_entre_listings_indicatif(texte):
    """Partie de la regle 7 sur la 'coupure d'un exemple continu' : NON
    BLOQUANTE, contrairement au reste de ce script.

    Verifie empiriquement (cours Essential GraphRAG) : meme en exigeant de
    la prose des deux cotes de la figure, ce controle produit des faux
    positifs francs -- une figure placee juste apres le paragraphe qui
    l'explique (comme la regle 7 le demande explicitement), suivie
    directement d'un lstlisting DIFFERENT et deja introduit par le
    paragraphe precedent, ressemble structurellement a une coupure alors
    qu'elle ne l'est pas. Distinguer "meme exemple continu coupe en deux" de
    "deux exemples distincts et sequentiels" exige de comprendre le rapport
    entre les deux blocs de code, ce qu'aucun motif syntaxique ne capture de
    maniere fiable. A juger au cas par cas, comme accents_homographes_suspects
    dans controle_qualite.py."""
    signalements = []
    for m in LSTLISTING_CLOSE_RE.finditer(texte):
        suivant = BOUNDARY_APRES_LISTING_RE.search(texte, m.end())
        if suivant and suivant.lastgroup == "listing":
            segment = texte[m.end():suivant.start()]
            if re.search(r"\\begin\{figure\}", segment):
                ligne = ligne_de(texte, m.end())
                signalements.append(f"ligne {ligne} : figure entre deux lstlisting consécutifs (à vérifier au cas par cas)")
    return signalements


def main():
    if len(sys.argv) != 3:
        print("Usage: python verifier_structure_course_tex.py <course.tex> <plan_cours.json>")
        sys.exit(1)

    course_tex_path, plan_path = sys.argv[1], sys.argv[2]

    with open(course_tex_path, "r", encoding="utf-8") as f:
        texte = f.read()
    with open(plan_path, "r", encoding="utf-8") as f:
        plan = json.load(f)

    anomalies = []
    anomalies.extend(check_chapitres_vs_plan(texte, plan.get("chapitres", [])))
    anomalies.extend(check_listing_suivi_de_prose(texte))
    anomalies.extend(check_figures(texte))

    rapport = {
        "anomalies": anomalies,
        "figures_entre_listings_a_verifier": check_figure_entre_listings_indicatif(texte),
        "pret": len(anomalies) == 0,
    }
    print(json.dumps(rapport, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
