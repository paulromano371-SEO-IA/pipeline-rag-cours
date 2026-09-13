# -*- coding: utf-8 -*-
"""Etape 5 (mecanique) / Prompt 3 - Etape 1ter : corrige automatiquement,
sur course.tex, les accents manquants a haute confiance detectes en
combinant plusieurs outils complementaires, en boucle jusqu'a
stabilisation, AVANT le controle final (controle_qualite.py) qui reste le
portillon en lecture seule.

Pourquoi combiner plusieurs outils plutot qu'un seul : chacun a des angles
morts differents, verifies empiriquement pendant le developpement de ce
script (voir stress_test/RAPPORT.md dans l'historique du projet) :
  - un dictionnaire seul (pyspellchecker) ne voit pas les homographes
    grammaticaux (a/a, ou/ou) ni les conjugaisons ambigues (un verbe du
    1er groupe au present et son participe passe masculin singulier sont
    ORTHOGRAPHIQUEMENT IDENTIQUES sans accent : "impressionne" est un mot
    valide qu'il s'agisse du present ou du participe fautif) ;
  - LanguageTool (regles grammaticales) couvre une partie de ces cas mais
    pas tous (aucune regle fiable pour ou/ou dans ce jeu de tests, et les
    participes coordonnes ou en apposition sans auxiliaire lui echappent) ;
  - spaCy (analyse syntaxique) comble ces trous mais peut mal etiqueter un
    verbe simple au present comme un participe (ex. "Ce chapitre explore"),
    ou halluciner un lemme qui n'existe pas a partir d'un adjectif invariant
    (ex. "disparates" -> lemme fantaisiste "disparater").

D'ou les etages ci-dessous, chacun avec ses propres garde-fous, executes
dans cet ordre a chaque iteration :
  A. Dictionnaire (pyspellchecker, via controle_qualite.py) : mots sans
     forme valide non accentuee -> corrige si suggestion unique.
  B. LanguageTool : regles ciblees uniquement (FR_SPELLING_RULE si la
     suggestion est une variante accentuee du meme mot, A_ACCENT /
     A_A_ACCENT2 pour a/a, AUX_AVOIR_VCONJ / AUX_ETRE_VCONJ pour les
     participes juste apres un auxiliaire).
  C. spaCy : participes -er non couverts par B (coordonnes, en apposition
     sans auxiliaire), avec TROIS garde-fous obligatoires avant
     application : (1) le lemme propose doit etre un vrai mot du
     dictionnaire francais, (2) un verbe ROOT sans auxiliaire n'est jamais
     traite comme participe (c'est presque toujours un present pris a
     tort pour un participe), (3) la correction ne doit JAMAIS differer du
     mot original par autre chose qu'un accent (empeche de remplacer un
     mot par un autre completement different, ex. l'imparfait
     "existaient" ne doit jamais devenir "existees"). L'accord (invariable
     avec avoir en voix active, accorde avec le sujet reel avec etre) est
     determine par la dependance grammaticale, pas par la morphologie
     (peu fiable) du participe lui-meme.
  D. Heuristique ou/ou (reprise de controle_qualite.py).
  E. Heuristique a/a (sujet pronominal exclu de la conversion ; quelques
     contextes deja identifies comme faux positifs avec sujet nominal +
     verbe avoir sont ecartes explicitement).
  F. Table de secours pour les mots a plusieurs formes accentuees
     possibles que le dictionnaire seul ne peut pas trancher et que les
     etages B/C n'ont pas resolus via le contexte reel.

Toutes les corrections sont journalisees. Le code (lstlisting/\\lstinline/
commentaires) est protege a chaque etage : jamais modifie.

Determinisme (regle d'execution du skill) : versions figees ci-dessous
(modele spaCy fr_core_news_md, version de LanguageTool) pour que le
resultat soit identique a chaque lancement, meme si des versions plus
recentes deviennent disponibles entre deux executions du skill.

Dependances (installees/telechargees automatiquement au premier lancement
si absentes -- voir _bootstrap_dependances ci-dessous) :
  pip install pyspellchecker spacy language_tool_python install-jdk
  python -m spacy download fr_core_news_md
  (+ JRE portable telecharge par install-jdk, LanguageTool telecharge par
  language_tool_python -- aucune installation systeme requise)

Usage:
    python corriger_accents.py <course.tex> [--max-iterations N]
"""
import sys
import os
import re
import argparse

SPACY_MODEL = "fr_core_news_md"
SPACY_MODEL_VERSION_ATTENDUE = "3.8.0"
LANGUAGETOOL_VERSION = "6.8"
JRE_VERSION = "17"


def _bootstrap_dependances():
    """Installe silencieusement ce qui manque (JRE portable, modele spaCy)
    pour que le script fonctionne sans configuration manuelle prealable,
    sur n'importe quel poste ou tourne le skill."""
    jdk_home = os.path.join(os.path.expanduser("~"), ".jdk")
    java_deja_present = os.environ.get("JAVA_HOME") and os.path.exists(
        os.path.join(os.environ["JAVA_HOME"], "bin", "java.exe" if os.name == "nt" else "java")
    )
    if not java_deja_present:
        existing = None
        if os.path.isdir(jdk_home):
            for d in os.listdir(jdk_home):
                if d.startswith(f"jdk-{JRE_VERSION}"):
                    existing = os.path.join(jdk_home, d)
                    break
        if existing:
            os.environ["JAVA_HOME"] = existing
        else:
            print(f"JRE portable absent : telechargement (une seule fois, ~180 Mo)...", file=sys.stderr)
            import jdk
            os.environ["JAVA_HOME"] = jdk.install(JRE_VERSION)
    os.environ["PATH"] = os.path.join(os.environ["JAVA_HOME"], "bin") + os.pathsep + os.environ.get("PATH", "")

    import importlib.util
    if importlib.util.find_spec(SPACY_MODEL) is None:
        print(f"Modele spaCy {SPACY_MODEL} absent : telechargement (une seule fois, ~45 Mo)...", file=sys.stderr)
        import subprocess
        subprocess.run([sys.executable, "-m", "spacy", "download", SPACY_MODEL], check=True)


_bootstrap_dependances()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from controle_qualite import (
    LSTLISTING_RE, LSTINLINE_RE, COMMENT_RE, CAPTION_VALUE_RE, _masquer,
    VerificateurAccents, check_accents_manquants, ligne_de,
)

import spacy
import language_tool_python
import unicodedata


# --------------------------------------------------------------------------
# Protection du code (identique au reste du skill)
# --------------------------------------------------------------------------

def _proteger(texte):
    """Protege (remplace par un jeton \\x00PROTn\\x00, restitue tel quel par
    _restituer) tout ce qui n'est pas de la prose francaise a corriger : code
    des lstlisting/\\lstinline, commentaires LaTeX. Exception : la valeur de
    caption=... dans les options d'un lstlisting est une legende en francais
    destinee au lecteur, pas du code -- elle doit rester en clair pour que
    les etages de correction (A-E ci-dessous) s'appliquent dessus comme sur
    n'importe quel autre paragraphe."""
    proteges = []

    def _capturer_str(s):
        proteges.append(s)
        return f"\x00PROT{len(proteges) - 1}\x00"

    def _proteger_lstlisting(m):
        bloc = m.group(0)
        opts = m.group("opts")
        if opts:
            cap = CAPTION_VALUE_RE.search(opts)
            if cap:
                decalage = m.start("opts") - m.start(0)
                debut, fin = decalage + cap.start(), decalage + cap.end()
                return _capturer_str(bloc[:debut]) + bloc[debut:fin] + _capturer_str(bloc[fin:])
        return _capturer_str(bloc)

    t = LSTLISTING_RE.sub(_proteger_lstlisting, texte)
    t = LSTINLINE_RE.sub(lambda m: _capturer_str(m.group(0)), t)
    t = COMMENT_RE.sub(lambda m: _capturer_str(m.group(0)), t)
    return t, proteges


def _restituer(texte, proteges):
    return re.sub(r"\x00PROT(\d+)\x00", lambda m: proteges[int(m.group(1))], texte)


def _strip_accents(word):
    nfkd = unicodedata.normalize("NFKD", word)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _appliquer_casse(original, suggestion):
    if original.isupper() and len(original) > 1:
        return suggestion.upper()
    if original[0].isupper():
        return suggestion[0].upper() + suggestion[1:]
    return suggestion


# --------------------------------------------------------------------------
# Etage A : dictionnaire
# --------------------------------------------------------------------------

def etage_dictionnaire(texte, journal, verificateur):
    problemes = check_accents_manquants(texte, verificateur)
    par_mot = {}
    for p in problemes:
        par_mot.setdefault(p["mot"].lower(), set()).update(p["suggestions"])
    non_ambigus = {mot: next(iter(s)) for mot, s in par_mot.items() if len(s) == 1}

    texte_protege, proteges = _proteger(texte)
    for mot_lower, correction in non_ambigus.items():
        pattern = re.compile(r"\b" + re.escape(mot_lower) + r"\b", re.IGNORECASE)

        def _remplacer(m, correction=correction, mot_lower=mot_lower):
            original = m.group(0)
            corrige = _appliquer_casse(original, correction)
            journal.append(("A:dictionnaire", original, corrige))
            return corrige

        texte_protege = pattern.sub(_remplacer, texte_protege)
    return _restituer(texte_protege, proteges)


# --------------------------------------------------------------------------
# Etage B : LanguageTool (regles ciblees)
# --------------------------------------------------------------------------

_ORDRE_ACCORD = ["Masc,Sing", "Fem,Sing", "Masc,Plur", "Fem,Plur"]

_tool = None


def _get_tool():
    global _tool
    if _tool is None:
        print("Chargement de LanguageTool...", file=sys.stderr)
        _tool = language_tool_python.LanguageTool(
            "fr", language_tool_download_version=LANGUAGETOOL_VERSION
        )
    return _tool


def _trouver_accord_sujet(nlp, phrase_texte, position_mot):
    """Cherche, via spaCy, le sujet grammatical (nsubj/nsubj:pass) du verbe
    a la position donnee dans la phrase, et retourne (genre, nombre) de ce
    sujet, ou None si indetermine."""
    doc = nlp(phrase_texte)
    # trouve le token le plus proche de la position
    cible = None
    for tok in doc:
        if tok.idx <= position_mot < tok.idx + len(tok.text):
            cible = tok
            break
    if cible is None:
        return None
    for child in cible.children:
        if child.dep_ in ("nsubj", "nsubj:pass"):
            morph = child.morph.to_dict()
            return morph.get("Gender", "Masc"), morph.get("Number", "Sing")
    # participe en apposition : chercher le nom qu'il modifie (le head si
    # le head n'est pas un auxiliaire)
    head = cible.head
    if head != cible:
        morph = head.morph.to_dict()
        if morph.get("Gender") or morph.get("Number"):
            return morph.get("Gender", "Masc"), morph.get("Number", "Sing")
    return None


def etage_languagetool(texte, journal, nlp):
    tool = _get_tool()
    texte_protege, proteges = _proteger(texte)

    # LanguageTool a une limite de taille par requete geree en interne
    # (splitting automatique) mais on prefere lui donner tout le texte
    # protege d'un coup pour beneficier du contexte inter-phrases.
    matches = tool.check(texte_protege)

    remplacements = []  # (start, end, original, correction)
    for m in matches:
        start, end = m.offset, m.offset + m.error_length
        original = texte_protege[start:end]
        if not original.isalpha():
            continue

        if m.rule_id in ("A_ACCENT", "A_A_ACCENT2") and original.lower() == "a":
            correction = _appliquer_casse(original, "à")
            remplacements.append((start, end, original, correction, m.rule_id))
            continue

        if m.rule_id == "FR_SPELLING_RULE":
            if not m.replacements:
                continue
            top = m.replacements[0]
            if _strip_accents(top).lower() == original.lower() and top.lower() != original.lower():
                remplacements.append((start, end, original, top, m.rule_id))
            continue

        if m.rule_id == "AUX_AVOIR_VCONJ":
            if not m.replacements:
                continue
            # par defaut (regle enoncee par LanguageTool lui-meme) : reste
            # au masculin singulier sauf COD anteposee (cas rare, ignore ici)
            correction = m.replacements[0]
            remplacements.append((start, end, original, correction, m.rule_id))
            continue

        if m.rule_id == "AUX_ETRE_VCONJ":
            if len(m.replacements) < 4:
                continue
            accord = _trouver_accord_sujet(nlp, texte_protege[max(0, start - 200):end + 50], min(200, start))
            idx = 0
            if accord:
                genre, nombre = accord
                cle = f"{genre},{nombre}"
                if cle in _ORDRE_ACCORD:
                    idx = _ORDRE_ACCORD.index(cle)
            correction = m.replacements[idx]
            remplacements.append((start, end, original, correction, m.rule_id))
            continue

    for start, end, original, correction, rule_id in sorted(remplacements, key=lambda r: -r[0]):
        correction_cassee = _appliquer_casse(original, correction) if correction[0].islower() else correction
        texte_protege = texte_protege[:start] + correction_cassee + texte_protege[end:]
        journal.append((f"B:languagetool:{rule_id}", original, correction_cassee))

    return _restituer(texte_protege, proteges)


# --------------------------------------------------------------------------
# Etage C : spaCy, participes -er non couverts par B
# --------------------------------------------------------------------------

_SUFFIXES_PARTICIPE = {
    ("Masc", "Sing"): "é", ("Masc", "Plur"): "és",
    ("Fem", "Sing"): "ée", ("Fem", "Plur"): "ées",
}


def etage_spacy_participes(texte, journal, nlp, verificateur):
    texte_protege, proteges = _proteger(texte)
    # Split en gardant les separateurs captures (groupe entre parentheses) :
    # paragraphes[0], separateurs[0], paragraphes[1], separateurs[1], ... --
    # necessaire pour retomber juste sur la position absolue meme si un
    # separateur fait 3 sauts de ligne ou plus (un "+ 2" fixe aurait
    # progressivement decale toutes les positions suivantes).
    morceaux = re.split(r"(\n{2,})", texte_protege)
    paragraphes = morceaux[0::2]
    separateurs = morceaux[1::2]

    remplacements = []
    offset = 0
    for idx, para in enumerate(paragraphes):
        if para.strip():
            doc = nlp(para)
            for tok in doc:
                if tok.pos_ != "VERB":
                    continue
                morph = tok.morph.to_dict()
                if morph.get("VerbForm") != "Part" or morph.get("Tense") != "Past":
                    continue
                mot = tok.text
                if len(mot) < 3 or not mot.isalpha() or "é" in mot or "É" in mot:
                    continue
                lemma = tok.lemma_.lower()
                if not lemma.endswith("er"):
                    continue

                # Garde-fou 1 : le lemme propose par spaCy doit etre un vrai
                # mot francais (evite les lemmes fantaisistes issus d'un
                # mauvais etiquetage d'un adjectif, ex. "disparater",
                # "metaboliquer", "sequentiellemer" -- aucun de ces
                # infinitifs n'existe).
                if lemma not in verificateur.spell:
                    continue

                # Garde-fou 2 : le verbe root d'une phrase SANS auxiliaire
                # est presque toujours un present de l'indicatif ("Ce
                # chapitre explore...", "Commençons par...") pris a tort
                # pour un participe par le morphologizer -- jamais un vrai
                # participe employe seul (celui-ci serait alors une
                # apposition/relative reduite, donc PAS le root).
                a_aux = any(c.dep_ in ("aux", "aux:tense", "aux:pass") for c in tok.children)
                if tok.dep_ == "ROOT" and not a_aux:
                    continue

                # regle du participe passe francais : avec l'auxiliaire
                # AVOIR (voix active), le participe NE S'ACCORDE PAS avec le
                # sujet (seulement avec un COD antepose, cas rare ignore
                # ici) -> reste invariable masculin singulier. Avec ETRE
                # (passif/pronominal), il s'accorde avec le sujet. Il faut
                # donc d'abord identifier l'auxiliaire avant de decider.
                auxiliaire_lemma = None
                for child in tok.children:
                    if child.dep_ in ("aux", "aux:tense", "aux:pass"):
                        auxiliaire_lemma = child.lemma_.lower()
                        break

                if auxiliaire_lemma == "avoir":
                    # invariable (pas de COD antepose gere ici, cf commentaire ci-dessus)
                    attendu = lemma[:-2] + _SUFFIXES_PARTICIPE[("Masc", "Sing")]
                else:
                    # accord : sujet reel si trouvable, sinon morph du token
                    # (moins fiable mais mieux que rien)
                    genre, nombre = None, None
                    for child in tok.children:
                        if child.dep_ in ("nsubj", "nsubj:pass"):
                            cm = child.morph.to_dict()
                            genre, nombre = cm.get("Gender"), cm.get("Number")
                            break
                    if genre is None:
                        genre = morph.get("Gender", "Masc")
                        nombre = morph.get("Number", "Sing")
                    suffixe = _SUFFIXES_PARTICIPE.get((genre, nombre), "é")
                    racine = lemma[:-2]
                    attendu = racine + suffixe

                if mot.lower() == attendu:
                    continue

                # Garde-fou 3 (le plus important) : la correction proposee ne
                # doit JAMAIS differer du mot original par autre chose qu'un
                # accent -- sinon on risque de remplacer un mot par un autre
                # totalement different (ex. "existaient" -> "existees" serait
                # une corruption, pas une correction d'accent).
                if _strip_accents(attendu).lower() != mot.lower():
                    continue

                pos_absolue = offset + tok.idx
                remplacements.append((pos_absolue, pos_absolue + len(mot), mot, attendu))
        offset += len(para) + (len(separateurs[idx]) if idx < len(separateurs) else 0)

    for start, end, original, correction in sorted(remplacements, key=lambda r: -r[0]):
        correction_cassee = _appliquer_casse(original, correction)
        texte_protege = texte_protege[:start] + correction_cassee + texte_protege[end:]
        journal.append(("C:spacy_participe", original, correction_cassee))

    return _restituer(texte_protege, proteges)


# --------------------------------------------------------------------------
# Etage D : heuristique ou/ou (reprise de controle_qualite.py)
# --------------------------------------------------------------------------

def etage_ou_ou(texte, journal):
    texte_protege, proteges = _proteger(texte)
    texte_prose = texte_protege
    tokens = list(re.finditer(r"[A-Za-zÀ-ÿ']+", texte_prose))
    mots = [t.group() for t in tokens]

    remplacements = []
    for i, t in enumerate(tokens):
        if mots[i].lower() != "ou":
            continue
        precedent = mots[i - 1].lower() if i > 0 else ""
        if precedent not in {"cas", "situation", "moment", "fois", "endroit", "contexte", "scenario", "scénario"}:
            continue
        entre = texte_prose[tokens[i - 1].end():t.start()]
        if "," in entre:
            continue
        original = t.group()
        correction = _appliquer_casse(original, "où")
        remplacements.append((t.start(), t.end(), original, correction))

    for start, end, original, correction in sorted(remplacements, key=lambda r: -r[0]):
        texte_protege = texte_protege[:start] + correction + texte_protege[end:]
        journal.append(("D:ou_ou", original, correction))

    return _restituer(texte_protege, proteges)


# --------------------------------------------------------------------------
# Etage E : heuristique a/a (avec exclusions de contexte deja identifiees
# manuellement -- verbe avoir a sujet nominal, jamais preposition)
# --------------------------------------------------------------------------

_SUJETS_AVANT_A_VERBE = {"il", "elle", "on", "ça", "ca", "cela", "qui", "ce", "n", "y", "l"}

FAUX_POSITIFS_A_CONTEXTES = [
    "Cette puissance a un revers",
    "et ce revers a des conséquences",
    "et ce revers a des consequences",
    "l'idée a été d'utiliser",
    "l'idee a ete d'utiliser",
    "équipe a remporte le championnat",
    "equipe a remporte le championnat",
    "chunks de texte a été fusionnée",
    "chunks de texte a ete fusionnee",
    "quiconque les a déployés en production",
    "quiconque les a deployes en production",
    "Cette aisance a un revers",
    "retriever a déjà fait le travail",
    "retriever a deja fait le travail",
    "OpenAI a d'ailleurs formalisé cette pratique",
    "OpenAI a d'ailleurs formalise cette pratique",
    "cette information a déjà été donnée",
    "cette information a deja ete donnee",
    "chapitre précédent a montré comment",
    "chapitre precedent a montre comment",
]


def etage_a_a(texte, journal):
    texte_protege, proteges = _proteger(texte)
    tokens = list(re.finditer(r"[A-Za-zÀ-ÿ']+", texte_protege))
    mots = [t.group() for t in tokens]

    remplacements = []
    for i, t in enumerate(tokens):
        if mots[i].lower() != "a":
            continue
        precedent_brut = mots[i - 1].lower() if i > 0 else ""
        precedent = precedent_brut.split("'")[-1]
        if precedent in _SUJETS_AVANT_A_VERBE:
            continue
        contexte = texte_protege[max(0, t.start() - 30):t.start() + 40]
        if any(fp in contexte for fp in FAUX_POSITIFS_A_CONTEXTES):
            continue
        original = t.group()
        correction = _appliquer_casse(original, "à")
        remplacements.append((t.start(), t.end(), original, correction))

    for start, end, original, correction in sorted(remplacements, key=lambda r: -r[0]):
        texte_protege = texte_protege[:start] + correction + texte_protege[end:]
        journal.append(("E:a_a", original, correction))

    return _restituer(texte_protege, proteges)


# --------------------------------------------------------------------------
# Etage F : mots a plusieurs formes accentuees possibles (dictionnaire seul
# ne peut pas trancher) -- forme par defaut choisie par lecture manuelle du
# corpus lors du stress test precedent. Applique seulement si le mot est
# encore signale par le dictionnaire a ce stade (les etages B/C en ont deja
# resolu une bonne partie via le contexte grammatical reel).
# --------------------------------------------------------------------------

DEFAUT_AMBIGUS = {
    "agrege": "agrège", "amenes": "amenés", "apres": "après",
    "arriere": "arrière", "cede": "cédé", "chaine": "chaîne",
    "chaines": "chaînes", "cloture": "clôture", "complete": "complète",
    "considere": "considère", "controle": "contrôle", "decompose": "décompose",
    "deconnectes": "déconnectés", "dedie": "dédié", "delibere": "délibéré",
    "demarre": "démarre", "depasse": "dépasse", "deroule": "déroule",
    "differe": "diffère", "different": "différent", "echange": "échange",
    "echoue": "échoue", "eleve": "élevé", "enchaine": "enchaîne",
    "entraines": "entraînés", "equipes": "équipes", "etaye": "étayé",
    "etes": "êtes", "etudie": "étudié", "evenement": "événement",
    "evenements": "événements", "evite": "évite", "evolue": "évolue",
    "evoque": "évoque", "fenetre": "fenêtre", "frequente": "fréquente",
    "genes": "gènes", "gere": "gère", "interprete": "interprète",
    "maniere": "manière", "melanges": "mélangés", "merite": "mérite",
    "modele": "modèle", "parametre": "paramètre", "parametres": "paramètres",
    "precise": "précise", "precises": "précises", "prefaces": "préfaces",
    "pres": "près", "preserve": "préserve", "quete": "quête",
    "realise": "réalisé", "recree": "recréé", "releve": "relève",
    "repete": "répété", "reserves": "réservés", "reside": "réside",
    "resume": "résumé", "resumes": "résumés", "revele": "révèle",
    "separe": "sépare", "separes": "séparés", "serie": "série",
    "synthetise": "synthétise",
    # mots deja resolus (majoritairement) par les etages B/C mais gardes en
    # secours si le contexte n'a pas permis de trancher automatiquement :
    "decide": "décide", "decoupe": "découpe", "determine": "détermine",
    "entraine": "entraîné", "equipe": "équipe", "implemente": "implémenté",
}


def etage_ambigus(texte, journal, verificateur):
    problemes = check_accents_manquants(texte, verificateur)
    a_corriger = set()
    for p in problemes:
        mot_lower = p["mot"].lower()
        if len(p["suggestions"]) > 1 and mot_lower in DEFAUT_AMBIGUS:
            a_corriger.add(mot_lower)

    texte_protege, proteges = _proteger(texte)
    for mot_lower in a_corriger:
        correction = DEFAUT_AMBIGUS[mot_lower]
        pattern = re.compile(r"\b" + re.escape(mot_lower) + r"\b", re.IGNORECASE)

        def _remplacer(m, correction=correction):
            original = m.group(0)
            corrige = _appliquer_casse(original, correction)
            journal.append(("F:ambigu_defaut", original, corrige))
            return corrige

        texte_protege = pattern.sub(_remplacer, texte_protege)
    return _restituer(texte_protege, proteges)


# --------------------------------------------------------------------------
# Boucle principale
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--max-iterations", type=int, default=5)
    args = parser.parse_args()

    with open(args.path, "r", encoding="utf-8") as f:
        texte = f.read()

    print("Chargement du modele spaCy...", file=sys.stderr)
    nlp = spacy.load(SPACY_MODEL)
    if nlp.meta.get("version") != SPACY_MODEL_VERSION_ATTENDUE:
        print(
            f"ATTENTION: version du modele spaCy {SPACY_MODEL} = "
            f"{nlp.meta.get('version')}, attendue {SPACY_MODEL_VERSION_ATTENDUE} "
            "-- le resultat peut differer legerement d'une execution du skill a l'autre.",
            file=sys.stderr,
        )
    if "ner" in nlp.pipe_names:
        nlp.disable_pipes("ner")

    print("Construction du verificateur d'accents (dictionnaire)...", file=sys.stderr)
    verificateur = VerificateurAccents()

    journal_total = []
    for iteration in range(1, args.max_iterations + 1):
        journal = []
        avant = texte

        texte = etage_dictionnaire(texte, journal, verificateur)
        texte = etage_languagetool(texte, journal, nlp)
        texte = etage_spacy_participes(texte, journal, nlp, verificateur)
        texte = etage_ou_ou(texte, journal)
        texte = etage_a_a(texte, journal)
        texte = etage_ambigus(texte, journal, verificateur)

        journal_total.extend(journal)
        print(f"Iteration {iteration} : {len(journal)} corrections", file=sys.stderr)
        for etage, orig, corr in journal:
            print(f"  [{etage}] {orig!r} -> {corr!r}", file=sys.stderr)

        if texte == avant:
            print(f"Stabilise apres {iteration} iteration(s).", file=sys.stderr)
            break
    else:
        print(f"Limite de {args.max_iterations} iterations atteinte (peut ne pas etre stabilise).", file=sys.stderr)

    with open(args.path, "w", encoding="utf-8") as f:
        f.write(texte)

    print(f"\nTotal corrections appliquees : {len(journal_total)}", file=sys.stderr)


if __name__ == "__main__":
    main()
