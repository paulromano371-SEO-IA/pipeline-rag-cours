"""Réparation des ligatures typographiques mal encodées (ff, fi, fl, ffi, ffl).

Certains PDF (typiquement issus de LaTeX) ont une police dont les glyphes de
ligature n'ont pas de correspondance Unicode correcte. PyMuPDF les extrait
alors comme un caractère de contrôle invisible en plein milieu d'un mot
("figées" -> "\x1cgées"), sans jamais produire de U+FFFD — c'est le gate
qualité (`quality.py`) qui les repère.

Le code de contrôle utilisé pour chaque ligature dépend de la police du
document (pas de standard universel, et les polices Type3 issues de dvips
n'exposent que des noms de glyphe opaques du type `/a19` — vérifié
empiriquement, aucun nom sémantique du type `/fi` à lire directement dans la
police). Approche déterministe, 100% Python : pour chaque occurrence, on
reconstitue le mot dans lequel le code tombe (fragment de lettres
immédiatement avant/après, accents compris) et on teste les 5 ligatures
candidates contre un dictionnaire français (`pyspellchecker`, hors ligne) —
celle qui reconstitue un vrai mot français est retenue pour tout le
document. Un code qui ne tombe jamais au milieu d'un mot (rien de part et
d'autre) n'est jamais une ligature texte — vérifié empiriquement, ce sont
d'autres glyphes de la même police opaque (guillemets, symboles de note...)
— et reste délibérément non résolu, jamais remplacé au hasard.

Un appel `claude -p` headless imbriqué a été utilisé ici auparavant, pour
deviner la ligature à partir du contexte affiché en langage naturel.
Abandonné : ce problème est entièrement déterministe (même code -> même
ligature partout dans un document, vérifié par vote unanime sur toutes les
occurrences), donc un dictionnaire hors ligne est strictement plus fiable,
plus rapide, et sans aucun risque de contention/timeout avec la session
appelante (voir SKILL.md, historique de l'incident : l'appel imbriqué
bloquait à 120s sur des occurrences hors-mot que le LLM ne pouvait de toute
façon pas résoudre correctement).

Les codes "hors-mot" ci-dessus ne sont pas toujours du bruit inoffensif :
`\og...\fg{}` (guillemets français ouvrant/fermant, commandes standard de
babel-french — pas une particularité de ce document) subit exactement le
même défaut de police, produisant deux codes de contrôle invisibles au lieu
de « / » — vérifié empiriquement, du texte réel silencieusement corrompu
("croisée \x13leave-one-out\x14" au lieu de "croisée « leave-one-out »"),
bien plus large qu'un simple problème de titre. Résolu par
`infer_symbol_pair_mapping` : sans dictionnaire ni référence à `course.tex`,
uniquement par alternance stricte ouverture/fermeture dans l'ordre de
lecture (une paire de guillemets encadre toujours un span borné, jamais
imbriquée en français) — même exigence de vote unanime qu'`_resolve_code`
avant de conclure.

Deux variantes supplémentaires du même défaut, vérifiées empiriquement sur
plusieurs livres :
- des guillemets droits collés à leur contenu (`` `` ``/`` '' ``, convention
  anglaise de citation LaTeX — SANS espace, contrairement à `\og`/`\fg{}`
  qui en a toujours une) : `find_symbol_candidate_occurrences` élargit la
  détection à une occurrence qui touche un mot d'UN SEUL côté (jamais des
  deux, ce qui resterait une ligature) pour les inclure. `infer_symbol_pair_mapping`
  choisit alors `"` (le même caractère aux deux bouts) plutôt que « / »,
  selon que la paire touche ou non un mot (voir `_pair_touches_word`) — un
  guillemet français authentique n'est jamais collé, un guillemet droit
  anglais l'est presque toujours ;
- un symbole employé SEUL, jamais en paire (tiret de séparation dans une
  légende `\caption{...}`, puce de liste en début de ligne — même glyphe de
  police pour les deux usages selon le contexte, vérifié empiriquement) :
  aucune paire ouvrant/fermant ne peut se former, donc `infer_symbol_pair_mapping`
  ne le résout jamais. Deviner un caractère précis (tiret cadratin ou
  demi-cadratin ? puce ?) serait une supposition non vérifiable — ce code
  est simplement supprimé par `remove_singleton_symbols`, avec nettoyage de
  l'espacement résiduel (jamais un caractère inventé à la place)."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from spellchecker import SpellChecker

_ALLOWED_CONTROL_CHARS = {"\n", "\t", "\r"}
_CANDIDATE_LIGATURES = ("ff", "fi", "fl", "ffi", "ffl")

_spell = SpellChecker(language="fr")


class LigatureRepairError(RuntimeError):
    pass


def _char_from_code(code: str) -> str:
    return chr(int(code.removeprefix("U+"), 16))


def _word_fragment(text: str, idx: int, *, step: int) -> str:
    """Étend depuis `idx` (exclu), dans la direction `step` (+1 ou -1), tant
    que les caractères sont des lettres (accents compris — `str.isalpha()`
    est Unicode-aware) : la portion du mot cassée par le caractère de
    contrôle, d'un côté donné. Vide si `idx` n'est pas adjacent à une
    lettre de ce côté (le code ne tombe pas au milieu d'un mot)."""
    chars: list[str] = []
    i = idx + step
    while 0 <= i < len(text) and text[i].isalpha():
        chars.append(text[i])
        i += step
    if step < 0:
        chars.reverse()
    return "".join(chars)


def find_ligature_occurrences(text: str) -> dict[str, list[tuple[str, str]]]:
    """Recense chaque caractère de contrôle non standard et, pour chaque
    occurrence, le fragment de mot immédiatement avant/après (les deux vides
    si le caractère ne tombe pas au milieu d'un mot — jamais une ligature
    texte dans ce cas, voir docstring du module)."""
    occurrences: dict[str, list[tuple[str, str]]] = {}
    for idx, c in enumerate(text):
        if c in _ALLOWED_CONTROL_CHARS:
            continue
        if unicodedata.category(c) != "Cc":
            continue
        code = f"U+{ord(c):04X}"
        left = _word_fragment(text, idx, step=-1)
        right = _word_fragment(text, idx, step=1)
        occurrences.setdefault(code, []).append((left, right))
    return occurrences


def _resolve_code(occurrences: list[tuple[str, str]]) -> str | None:
    """Teste chaque ligature candidate sur chaque occurrence du code
    (reconstitution du mot complet, vérifiée contre le dictionnaire
    français) et retient la ligature gagnante si — et seulement si — elle
    est la SEULE à jamais avoir matché, sur toutes les occurrences ayant
    matché au moins une candidate (une occurrence sans mot autour, ou sans
    candidate valide, est ignorée plutôt que traitée comme un désaccord :
    voir docstring du module, ces occurrences ne sont juste pas des
    ligatures). Un code sans aucune occurrence résolue, ou dont les
    occurrences se contredisent (candidates différentes gagnantes selon
    l'occurrence), reste non résolu — jamais une supposition au hasard."""
    winner: str | None = None
    for left, right in occurrences:
        if not left and not right:
            continue
        matches = [cand for cand in _CANDIDATE_LIGATURES if (left + cand + right).lower() in _spell]
        if len(matches) != 1:
            continue
        if winner is None:
            winner = matches[0]
        elif winner != matches[0]:
            return None
    return winner


def infer_ligature_mapping(occurrences: dict[str, list[tuple[str, str]]]) -> dict[str, str]:
    """Déduit, pour chaque code de contrôle, la ligature qu'il représente —
    voir `_resolve_code`."""
    mapping: dict[str, str] = {}
    for code, occs in occurrences.items():
        ligature = _resolve_code(occs)
        if ligature is not None:
            mapping[code] = ligature
    return mapping


_GUILLEMET_PAIR = {"open": "«", "close": "»"}
_STRAIGHT_QUOTE = '"'


def find_symbol_candidate_occurrences(text: str) -> dict[str, list[int]]:
    """Position (index dans `text`) de chaque occurrence d'un caractère de
    contrôle qui NE touche PAS un mot des DEUX côtés à la fois (voir
    `_word_fragment`) — une vraie ligature texte touche toujours un mot des
    deux côtés (voir `_resolve_code`), donc est exclue ici. Inclut en
    revanche une occurrence entièrement isolée (espaces des deux côtés,
    guillemets français `\\og`/`\\fg{}`) ET une occurrence qui touche un mot
    d'UN SEUL côté (guillemet droit collé à son contenu, `` `` ``/`` '' ``
    sans espace — vérifié empiriquement) : ces deux cas sont des candidats à
    être un symbole de police plutôt qu'une ligature."""
    positions: dict[str, list[int]] = {}
    for idx, c in enumerate(text):
        if c in _ALLOWED_CONTROL_CHARS:
            continue
        if unicodedata.category(c) != "Cc":
            continue
        if _word_fragment(text, idx, step=-1) and _word_fragment(text, idx, step=1):
            continue
        code = f"U+{ord(c):04X}"
        positions.setdefault(code, []).append(idx)
    return positions


def _pair_touches_word(text: str, code_open: str, code_close: str) -> bool:
    """Vrai si au moins une occurrence de `code_open`/`code_close` touche un
    mot d'un côté (voir `find_symbol_candidate_occurrences`) — signe d'un
    guillemet droit collé à son contenu (convention anglaise `` `` ``/`` '' ``,
    sans espace) plutôt que d'un guillemet français authentique (`\\og`/`\\fg{}`,
    toujours entouré d'espaces). Détermine le caractère de remplacement
    dans `infer_symbol_pair_mapping`."""
    targets = {_char_from_code(code_open), _char_from_code(code_close)}
    for idx, c in enumerate(text):
        if c not in targets:
            continue
        if _word_fragment(text, idx, step=-1) or _word_fragment(text, idx, step=1):
            return True
    return False


def _is_consistent_open_close_pair(ordered_codes: list[str], code_open: str, code_close: str) -> bool:
    """Vérifie que les occurrences de `code_open`/`code_close`, une fois les
    autres codes filtrés, alternent STRICTEMENT ouvrant/fermant/ouvrant/...
    en commençant par `code_open` — signature d'une vraie paire
    ouvrant/fermant (jamais imbriquée, jamais déséquilibrée). Un seul écart
    dans l'alternance, sur tout le document, invalide la paire entière :
    même philosophie que le vote unanime d'`_resolve_code`, jamais une
    correspondance à moitié fiable."""
    filtered = [c for c in ordered_codes if c in (code_open, code_close)]
    if not filtered or len(filtered) % 2 != 0:
        return False
    expected = code_open
    for c in filtered:
        if c != expected:
            return False
        expected = code_close if expected == code_open else code_open
    return True


def infer_symbol_pair_mapping(text: str) -> dict[str, str]:
    """Déduit, parmi les codes de contrôle candidats (voir
    `find_symbol_candidate_occurrences`), une éventuelle paire ouvrant/fermant
    de guillemets — par simple alternance stricte dans l'ordre de lecture,
    sans dictionnaire ni référence à `course.tex` (voir
    `_is_consistent_open_close_pair`). Le caractère de remplacement dépend
    du type de paire (voir `_pair_touches_word`) : `"` (le même aux deux
    bouts) pour un guillemet droit collé à son contenu, « / » pour un
    guillemet français authentique toujours isolé par des espaces.

    Chaque code ne peut appartenir qu'à UNE SEULE paire valide : si un code
    alterne parfaitement avec plusieurs codes différents (ambigu — ne
    devrait pas arriver avec de vrais guillemets, mais resterait un signal
    de confusion réel), aucune des paires impliquant ce code n'est retenue
    plutôt que de choisir au hasard. Un code candidat qui ne fait partie
    d'aucune paire valide reste non résolu ici (traité comme symbole isolé,
    voir `remove_singleton_symbols`) — jamais une supposition."""
    positions = find_symbol_candidate_occurrences(text)
    codes = sorted(positions)
    if len(codes) < 2:
        return {}

    ordered_codes = [code for _, code in sorted(
        (pos, code) for code, idxs in positions.items() for pos in idxs
    )]

    valid_pairs: list[tuple[str, str]] = []
    for i, code_a in enumerate(codes):
        for code_b in codes[i + 1:]:
            if _is_consistent_open_close_pair(ordered_codes, code_a, code_b):
                valid_pairs.append((code_a, code_b))

    mapping: dict[str, str] = {}
    involved = [code for pair in valid_pairs for code in pair]
    for code_open, code_close in valid_pairs:
        if involved.count(code_open) > 1 or involved.count(code_close) > 1:
            continue  # code ambigu (plusieurs pairages valides) : ecarte
        if _pair_touches_word(text, code_open, code_close):
            mapping[code_open] = _STRAIGHT_QUOTE
            mapping[code_close] = _STRAIGHT_QUOTE
        else:
            mapping[code_open] = _GUILLEMET_PAIR["open"]
            mapping[code_close] = _GUILLEMET_PAIR["close"]
    return mapping


def find_singleton_symbol_codes(text: str) -> set[str]:
    """Codes candidats (voir `find_symbol_candidate_occurrences`) qui ne
    font partie d'aucune paire ouvrant/fermant résolue par
    `infer_symbol_pair_mapping` — un symbole employé SEUL (tiret de
    séparation, puce de liste — même glyphe de police pour les deux usages
    selon le contexte, vérifié empiriquement), jamais en paire. Appelé sur
    le texte APRÈS remplacement des ligatures/paires déjà résolues (voir
    `auto_repair_ligatures`) : ces codes-là ont déjà disparu du texte à ce
    stade, donc automatiquement absents du résultat, sans avoir besoin de
    les exclure explicitement."""
    return set(find_symbol_candidate_occurrences(text))


def remove_singleton_symbols(text: str, codes: set[str]) -> str:
    """Supprime purement et simplement chaque caractère des `codes` (jamais
    remplacé par un caractère deviné — un tiret cadratin ou demi-cadratin ?
    une puce ? aucun moyen fiable de trancher sans référence externe, voir
    docstring du module) et absorbe AU PLUS UN espace/tabulation
    directement adjacent (avant en priorité, sinon après) pour ne pas
    laisser de double espace ni d'espace parasite en tête de ligne.

    Nettoyage LOCAL caractère par caractère, jamais une regex globale sur
    tout le texte : une regex du type `[ \\t]{2,}` -> un espace, appliquée
    sans distinction, écraserait aussi l'indentation Python à l'intérieur
    des blocs de code (deux espaces d'indentation ramenés à un seul) — bug
    trouvé empiriquement (2 blocs de code sur 16 devenaient non-identiques
    à `course.tex`, 88-92% de similarité au lieu de 100%, juste après
    l'ajout de ce nettoyage). Ne traverse jamais un saut de ligne : un
    espace de l'AUTRE côté d'un `\\n` n'est jamais absorbé, pour ne
    jamais toucher l'indentation de la ligne suivante."""
    if not codes:
        return text
    targets = {_char_from_code(code) for code in codes}
    result: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c not in targets:
            result.append(c)
            i += 1
            continue
        if result and result[-1] in (" ", "\t"):
            result.pop()
        elif i + 1 < n and text[i + 1] in (" ", "\t"):
            i += 1  # absorbe l'espace suivant a la place
        i += 1
    return "".join(result)


def repair_ligatures(text: str, mapping: dict[str, str]) -> str:
    """Remplace chaque caractère de contrôle par la ligature déduite."""
    result = text
    for code, ligature in mapping.items():
        result = result.replace(_char_from_code(code), ligature)
    return result


@dataclass
class RepairResult:
    text: str
    ligature_mapping: dict[str, str]
    symbol_pair_mapping: dict[str, str]
    singleton_codes_removed: set[str]

    @property
    def total_repairs(self) -> int:
        """Compte total pour l'affichage synthétique (voir `run.py`,
        `_build_report`) — ligatures ET paires de symboles confondues,
        chacune comptant pour le nombre de CODES résolus (2 par paire),
        cohérent avec le compte historique `ligature_repairs`."""
        return len(self.ligature_mapping) + len(self.symbol_pair_mapping)


def auto_repair_ligatures(text: str) -> RepairResult:
    """Enchaîne détection, inférence et remplacement — ligatures ET paires
    de symboles (guillemets, voir `infer_symbol_pair_mapping`) : même
    mécanisme sous-jacent (glyphe de police sans correspondance Unicode),
    même remplacement final (`repair_ligatures` ne fait qu'un remplacement
    caractère → chaîne, indifférent à ce qu'il répare). Une fois ces deux
    catégories remplacées, tout code candidat restant (voir
    `find_singleton_symbol_codes`) est un symbole employé seul, jamais en
    paire — supprimé par `remove_singleton_symbols`, jamais deviné.

    Retourne un `RepairResult` : les trois catégories sont gardées
    distinctes (pas un simple dict fusionné) pour que l'appelant puisse les
    reporter séparément dans le contrôle qualité (voir SKILL.md) —
    ligatures et guillemets sont de VRAIES corrections (texte restitué),
    la suppression de symboles isolés est une DÉGRADATION CONNUE ET
    ACCEPTÉE (contenu décoratif perdu, jamais du texte), les deux ne
    doivent jamais être comptés ensemble sous un même intitulé."""
    occurrences = find_ligature_occurrences(text)
    ligature_mapping = infer_ligature_mapping(occurrences)
    symbol_pair_mapping = infer_symbol_pair_mapping(text)
    text = repair_ligatures(text, {**ligature_mapping, **symbol_pair_mapping})
    singleton_codes = find_singleton_symbol_codes(text)
    text = remove_singleton_symbols(text, singleton_codes)
    return RepairResult(
        text=text,
        ligature_mapping=ligature_mapping,
        symbol_pair_mapping=symbol_pair_mapping,
        singleton_codes_removed=singleton_codes,
    )
