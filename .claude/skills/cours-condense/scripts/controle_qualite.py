"""
Etape 5 (mecanique) / Prompt 3 - Etape 1 : controles automatiques sur
course.tex avant compilation. Ce script ne corrige rien tout seul pour les
points 1-3 (il liste les problemes, a Claude de decider de la correction) ;
il corrige lui-meme le point 5 (doublons hyperref/hypersetup) car la regle
est sans ambiguite ("supprime-la").

Version 2 (remplace l'ancienne version a liste figee), issue d'un stress test
qui a mis en evidence quatre angles morts du script original :

  1. accents_manquants s'appuyait sur une liste figee d'une quarantaine de
     radicaux -> corrige ici par une verification contre un vrai dictionnaire
     francais (pyspellchecker), avec correction suggeree automatiquement en
     inversant la table d'accents du dictionnaire lui-meme.
  2. \\lstinline{...} (et \\lstinline|...|) n'etait jamais masque -> tout le
     contenu de code inline polluait mots_interdits / citations / accents.
  3. Les commentaires LaTeX (% ...) n'etaient jamais exclus.
  4. Un bloc de code sans option language= et qui ressemble a du Cypher/bash
     (pas du Python) echouait ast_parse sans aucune indication -> ajout d'une
     heuristique qui detecte ce cas et le signale distinctement plutot que de
     le compter comme une vraie erreur de syntaxe Python.

Limite assumee et documentee (non resolue ici) : les homographes grammaticaux
francais (a/a, ou/ou, la/la, du/du, sur/sur...) sont indetectables par un
dictionnaire seul, puisque les deux formes sont chacune un mot valide. Une
detection heuristique best-effort est fournie (accents_homographes_suspects)
mais n'est PAS bloquante pour pret_a_compiler : elle doit etre jugee au cas
par cas, comme le fait deja le script pour mots_interdits/accents_manquants.

Usage:
    python controle_qualite.py <course.tex>

Sortie: rapport JSON sur stdout.

Dependance : pyspellchecker (pip install pyspellchecker)
"""
import sys
import re
import ast
import json
import unicodedata

try:
    from spellchecker import SpellChecker
except ImportError:
    print(
        "ERREUR: le module 'pyspellchecker' est requis (pip install pyspellchecker)",
        file=sys.stderr,
    )
    sys.exit(2)

INTERDITS = ["youtube", "vidéo", "video", "timestamp"]
CITATION_RE = re.compile(r"\[\d+\]")

LSTLISTING_RE = re.compile(
    r"\\begin\{lstlisting\}(\[(?P<opts>[^\]]*)\])?(?P<body>.*?)\\end\{lstlisting\}",
    re.DOTALL,
)
# \lstinline accepte n'importe quel caractere non-lettre comme delimiteur ;
# en pratique ce cours n'utilise que { } et | |.
LSTINLINE_RE = re.compile(r"\\lstinline\{[^{}]*\}|\\lstinline\|[^|]*\|")
# Commentaire LaTeX : un '%' non precede d'un backslash, jusqu'a la fin de ligne.
COMMENT_RE = re.compile(r"(?<!\\)%[^\n]*")
# La valeur de caption=... dans les options d'un lstlisting est du texte
# francais destine a etre affiche (legende sous le bloc de code), pas du
# code : elle doit rester visible aux controles (et correctible), a la
# difference du reste du bloc lstlisting (langage, code source).
# Capture jusqu'a la fin de la chaine d'options (jamais jusqu'a la premiere
# virgule) : dans toutes les occurrences reelles de ce cours, caption= est
# toujours la derniere option avant le "]" fermant, et une legende peut
# elle-meme contenir une virgule ("caption=Chargement, decoupage et
# validation") -- s'arreter a la premiere virgule tronquerait alors la
# protection/le controle en plein milieu de la legende.
CAPTION_VALUE_RE = re.compile(r"(?<=caption=).*")

MOTS_MIN_LONGUEUR = 3
# Mots courts/frequents qui existent aussi comme sigles/identifiants techniques
# et qu'on ne veut pas faire remonter comme "inconnus du dictionnaire" ->
# a completer au besoin plutot que d'abaisser MOTS_MIN_LONGUEUR globalement.
IGNORER_TOUJOURS = {
    "llm", "llms", "rag", "graphrag", "cypher", "neo4j", "json", "python",
    "api", "url", "http", "https", "id", "ids", "gpt", "openai", "csv",
    "sql", "html", "pdf", "ok",
}


def ligne_de(texte, index_char):
    return texte.count("\n", 0, index_char) + 1


def _blanchir(s):
    return "".join(c if c == "\n" else " " for c in s)


def _blanchir_lstlisting(m):
    """Comme _blanchir, mais preserve la valeur de caption=... (texte francais
    affiche au lecteur) au lieu de la neutraliser avec le reste du bloc
    (langage, code source) qui lui doit rester exclu des controles."""
    bloc = m.group(0)
    opts = m.group("opts")
    if opts:
        cap = CAPTION_VALUE_RE.search(opts)
        if cap:
            decalage = m.start("opts") - m.start(0)
            debut, fin = decalage + cap.start(), decalage + cap.end()
            return _blanchir(bloc[:debut]) + bloc[debut:fin] + _blanchir(bloc[fin:])
    return _blanchir(bloc)


def _masquer(texte):
    """Neutralise (remplace par des espaces, meme longueur/sauts de ligne
    pour ne pas decaler les numeros de ligne) tout ce qui n'est pas de la
    prose destinee au lecteur : blocs lstlisting (hors legende caption=...),
    \\lstinline inline, et commentaires LaTeX."""
    t = LSTLISTING_RE.sub(_blanchir_lstlisting, texte)
    t = LSTINLINE_RE.sub(lambda m: _blanchir(m.group(0)), t)
    t = COMMENT_RE.sub(lambda m: _blanchir(m.group(0)), t)
    return t


def check_mots_interdits(texte):
    texte_prose = _masquer(texte)
    problemes = []
    for mot in INTERDITS:
        for m in re.finditer(re.escape(mot), texte_prose, re.IGNORECASE):
            ligne = ligne_de(texte, m.start())
            contexte = texte[max(0, m.start() - 40):m.start() + 40].replace("\n", " ")
            problemes.append({"ligne": ligne, "mot": mot, "contexte": contexte})
    return problemes


INTERDITS_MISE_EN_FORME = [
    (re.compile(r"\\mbox\{"), r"\mbox (conteneur a largeur fixe interdit, etape 4 regle 6)"),
    (re.compile(r"\\fbox\{"), r"\fbox (conteneur a largeur fixe interdit, etape 4 regle 6)"),
    (re.compile(r"\\parbox\{"), r"\parbox (conteneur a largeur fixe interdit, etape 4 regle 6)"),
    (
        re.compile(r"\\pagestyle\{fancy\}|\\fancyhead|\\fancyfoot"),
        "en-tete/pied personnalise (fancyhdr) reproduisant potentiellement le titre du chapitre, sans demande explicite (etape 4 regle 6)",
    ),
]


def check_mise_en_forme_interdite(texte):
    """Regle 6 de l'etape 4 : interdictions de mise en forme. Ce sont des
    commandes LaTeX de structure, jamais du contenu a l'interieur d'un
    lstlisting (qui contient du Python/Cypher, pas du LaTeX) : verifie donc
    le texte brut, sans le masquer."""
    problemes = []
    for pattern, message in INTERDITS_MISE_EN_FORME:
        for m in pattern.finditer(texte):
            ligne = ligne_de(texte, m.start())
            contexte = texte[max(0, m.start() - 40):m.start() + 40].replace("\n", " ")
            problemes.append({"ligne": ligne, "motif": message, "contexte": contexte})
    return problemes


def check_citations(texte):
    texte_prose = _masquer(texte)
    problemes = []
    for m in CITATION_RE.finditer(texte_prose):
        ligne = ligne_de(texte, m.start())
        contexte = texte[max(0, m.start() - 40):m.start() + 40].replace("\n", " ")
        problemes.append({"ligne": ligne, "motif": m.group(), "contexte": contexte})
    return problemes


def _strip_accents(word):
    nfkd = unicodedata.normalize("NFKD", word)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


class VerificateurAccents:
    """Construit, une seule fois, l'index inverse
    (mot sans accent en minuscule) -> [formes accentuees connues du
    dictionnaire francais]. Permet de detecter tout mot de la prose qui
    correspond exactement a un mot francais UNIQUEMENT sous sa forme
    accentuee (donc ecrit sans son accent obligatoire), sans dependre d'une
    liste ecrite a la main."""

    def __init__(self):
        self.spell = SpellChecker(language="fr")
        self._index_inverse = {}
        for mot in self.spell.word_frequency.dictionary:
            sans_accent = _strip_accents(mot).lower()
            if sans_accent != mot.lower():
                self._index_inverse.setdefault(sans_accent, set()).add(mot)

    def suggerer(self, mot_minuscule):
        """Retourne la liste des formes accentuees possibles pour mot_minuscule
        s'il correspond a un mot francais connu uniquement accentue, sinon None."""
        return self._index_inverse.get(mot_minuscule)


def check_accents_manquants(texte, verificateur):
    texte_prose = _masquer(texte)
    problemes = []
    for m in re.finditer(r"[A-Za-zÀ-ÿ]+", texte_prose):
        mot = m.group()
        if len(mot) < MOTS_MIN_LONGUEUR:
            continue
        mot_lower = mot.lower()
        if mot_lower in IGNORER_TOUJOURS:
            continue
        if mot_lower in verificateur.spell:
            # deja un mot francais valide tel quel (correctement accentue,
            # ou mot qui n'a legitimement pas d'accent)
            continue
        suggestions = verificateur.suggerer(mot_lower)
        if not suggestions:
            continue
        ligne = ligne_de(texte, m.start())
        contexte = texte[max(0, m.start() - 40):m.start() + 40].replace("\n", " ")
        problemes.append({
            "ligne": ligne,
            "mot": mot,
            "suggestions": sorted(suggestions),
            "contexte": contexte,
        })
    return problemes


# --- Detection heuristique best-effort des homographes grammaticaux a/a, ou/ou ---
# Non bloquante pour pret_a_compiler : a juger au cas par cas par Claude,
# exactement comme accents_manquants et mots_interdits.
_SUJETS_AVANT_A_VERBE = {"il", "elle", "on", "ça", "ca", "cela", "qui", "ce", "n", "y", "l"}


def check_homographes_suspects(texte):
    texte_prose = _masquer(texte)
    problemes = []
    tokens = list(re.finditer(r"[A-Za-zÀ-ÿ']+", texte_prose))
    mots = [t.group() for t in tokens]
    for i, t in enumerate(tokens):
        mot_lower = t.group().lower()
        if mot_lower == "a":
            # le mot precedent peut etre une contraction ("qu'il", "s'il",
            # "puisqu'il"...) : le pronom sujet est alors le dernier segment
            # apres l'apostrophe, pas le token entier.
            precedent_brut = mots[i - 1].lower() if i > 0 else ""
            precedent = precedent_brut.split("'")[-1]
            if precedent not in _SUJETS_AVANT_A_VERBE:
                ligne = ligne_de(texte, t.start())
                contexte = texte[max(0, t.start() - 40):t.start() + 40].replace("\n", " ")
                problemes.append({
                    "ligne": ligne, "mot": t.group(), "suggestion": "à (verifier)",
                    "contexte": contexte,
                })
        elif mot_lower == "ou":
            # heuristique faible : "ou" est tres majoritairement la
            # conjonction (or) ; on ne signale que les cas precedes
            # DIRECTEMENT (sans virgule intercalee, qui indiquerait une
            # disjonction plutot qu'une relative) par un nom frequent
            # introduisant une relative de lieu/temps ("le cas ou", "la
            # situation ou"), pour limiter le bruit.
            precedent = mots[i - 1].lower() if i > 0 else ""
            if precedent in {"cas", "situation", "moment", "fois", "endroit", "contexte", "scenario", "scénario"}:
                entre = texte_prose[tokens[i - 1].end():t.start()]
                if "," not in entre:
                    ligne = ligne_de(texte, t.start())
                    contexte = texte[max(0, t.start() - 40):t.start() + 40].replace("\n", " ")
                    problemes.append({
                        "ligne": ligne, "mot": t.group(), "suggestion": "où (verifier)",
                        "contexte": contexte,
                    })
    return problemes


_CYPHER_HINTS = re.compile(
    r"^\s*(MATCH|MERGE|CREATE\s+CONSTRAINT|CREATE\s+VECTOR|CREATE\s+FULLTEXT|"
    r"RETURN|WITH|UNWIND|CALL\s+db\.|CALL\s+apoc\.)\b",
    re.MULTILINE,
)
_BASH_HINTS = re.compile(r"^\s*(docker|pip|python|curl|export)\b", re.MULTILINE)


def _ressemble_non_python(code):
    if _CYPHER_HINTS.search(code):
        return "Cypher"
    if _BASH_HINTS.search(code) and "def " not in code and "import " not in code:
        return "bash/shell"
    return None


def check_code_python(texte):
    resultats = []
    for i, m in enumerate(LSTLISTING_RE.finditer(texte), start=1):
        opts = m.group("opts") or ""
        body = m.group("body")
        code = body.lstrip("\n")
        lang_match = re.search(r"language\s*=\s*\{?(\w*)\}?", opts, re.IGNORECASE)
        if lang_match:
            langue = lang_match.group(1).lower() or "none"
        else:
            langue = "python"

        entree = {
            "listing_numero": i,
            "ligne_debut": ligne_de(texte, m.start()),
            "langue": langue,
        }
        if langue == "python":
            try:
                ast.parse(code)
                entree["ast_parse"] = "ok"
            except SyntaxError as e:
                devine = _ressemble_non_python(code)
                if devine:
                    entree["ast_parse"] = "ignore (detection heuristique : ressemble a du " + devine + ")"
                    entree["suggestion"] = (
                        f"ce bloc ressemble a du {devine}, pas du Python : ajoutez "
                        f"language={{}} ou language={{{devine}}} dans les options du "
                        "lstlisting si confirme"
                    )
                else:
                    entree["ast_parse"] = "echec"
                    entree["erreur"] = f"{e.msg} (ligne {e.lineno}, col {e.offset})"
        else:
            entree["ast_parse"] = "ignore (langue != python)"
        resultats.append(entree)
    return resultats


def fix_doublons_hyperref(texte):
    nb_supprimees = 0

    def garder_une_seule_usepackage(t):
        nonlocal nb_supprimees
        pattern = re.compile(r"\\usepackage(\[[^\]]*\])?\{hyperref\}")
        occurrences = list(pattern.finditer(t))
        if len(occurrences) <= 1:
            return t
        for m in reversed(occurrences[1:]):
            t = t[:m.start()] + t[m.end():]
            nb_supprimees += 1
        return t

    def garder_un_seul_hypersetup(t):
        nonlocal nb_supprimees
        pattern = re.compile(r"\\hypersetup\{")
        occurrences = list(pattern.finditer(t))
        if len(occurrences) <= 1:
            return t
        for m in reversed(occurrences[1:]):
            depth = 1
            j = m.end()
            while j < len(t) and depth > 0:
                if t[j] == "{":
                    depth += 1
                elif t[j] == "}":
                    depth -= 1
                j += 1
            t = t[:m.start()] + t[j:]
            nb_supprimees += 1
        return t

    texte = garder_une_seule_usepackage(texte)
    texte = garder_un_seul_hypersetup(texte)
    return texte, nb_supprimees


def main():
    if len(sys.argv) != 2:
        print("Usage: python controle_qualite.py <course.tex>")
        sys.exit(1)

    path = sys.argv[1]
    with open(path, "r", encoding="utf-8") as f:
        texte = f.read()

    verificateur = VerificateurAccents()

    rapport = {
        "mots_interdits": check_mots_interdits(texte),
        "accents_manquants": check_accents_manquants(texte, verificateur),
        "accents_homographes_suspects": check_homographes_suspects(texte),
        "citations_numerotees": check_citations(texte),
        "blocs_code_python": check_code_python(texte),
        "mise_en_forme_interdite": check_mise_en_forme_interdite(texte),
    }

    texte_corrige, nb_supprimees = fix_doublons_hyperref(texte)
    rapport["doublons_hyperref_supprimes"] = nb_supprimees
    if nb_supprimees > 0:
        with open(path, "w", encoding="utf-8") as f:
            f.write(texte_corrige)

    rapport["pret_a_compiler"] = (
        len(rapport["mots_interdits"]) == 0
        and len(rapport["accents_manquants"]) == 0
        and len(rapport["citations_numerotees"]) == 0
        and len(rapport["mise_en_forme_interdite"]) == 0
        and all(
            b["ast_parse"].startswith("ok") or b["ast_parse"].startswith("ignore")
            for b in rapport["blocs_code_python"]
        )
    )
    # accents_homographes_suspects est volontairement exclu de pret_a_compiler :
    # trop de faux positifs possibles pour bloquer automatiquement, a juger
    # au cas par cas comme le reste.

    print(json.dumps(rapport, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
