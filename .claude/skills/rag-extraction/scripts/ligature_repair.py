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
avant de conclure."""

from __future__ import annotations

import unicodedata

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


_SYMBOL_PAIR = {"open": "«", "close": "»"}


def find_standalone_occurrences(text: str) -> dict[str, list[int]]:
    """Position (index dans `text`) de chaque occurrence d'un caractère de
    contrôle qui ne tombe JAMAIS au milieu d'un mot (voir `_word_fragment`)
    — jamais une ligature texte (voir `_resolve_code`), mais candidat à être
    un symbole autonome rendu par la même police opaque (guillemet,
    tiret...). Ignore silencieusement une occurrence du même code qui, elle,
    tombe au milieu d'un mot ailleurs dans le document (jamais observé en
    pratique — un glyphe de police donné ne sert qu'à un seul usage — mais
    resterait sans danger : cette occurrence-là est simplement absente
    d'ici, traitée uniquement côté ligature)."""
    positions: dict[str, list[int]] = {}
    for idx, c in enumerate(text):
        if c in _ALLOWED_CONTROL_CHARS:
            continue
        if unicodedata.category(c) != "Cc":
            continue
        if _word_fragment(text, idx, step=-1) or _word_fragment(text, idx, step=1):
            continue
        code = f"U+{ord(c):04X}"
        positions.setdefault(code, []).append(idx)
    return positions


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
    """Déduit, parmi les codes de contrôle "autonomes" (voir
    `find_standalone_occurrences`), une éventuelle paire ouvrant/fermant de
    guillemets français (`\\og`/`\\fg{}`) — par simple alternance stricte
    dans l'ordre de lecture, sans dictionnaire ni référence à `course.tex`
    (voir `_is_consistent_open_close_pair`).

    Chaque code ne peut appartenir qu'à UNE SEULE paire valide : si un code
    alterne parfaitement avec plusieurs codes différents (ambigu — ne
    devrait pas arriver avec de vrais guillemets, mais resterait un signal
    de confusion réel), aucune des paires impliquant ce code n'est retenue
    plutôt que de choisir au hasard. Un code "autonome" qui ne fait partie
    d'aucune paire valide reste non résolu (guillemet incomplet, symbole
    d'une autre nature...) — jamais une supposition."""
    positions = find_standalone_occurrences(text)
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
        mapping[code_open] = _SYMBOL_PAIR["open"]
        mapping[code_close] = _SYMBOL_PAIR["close"]
    return mapping


def repair_ligatures(text: str, mapping: dict[str, str]) -> str:
    """Remplace chaque caractère de contrôle par la ligature déduite."""
    result = text
    for code, ligature in mapping.items():
        result = result.replace(_char_from_code(code), ligature)
    return result


def auto_repair_ligatures(text: str) -> tuple[str, dict[str, str]]:
    """Enchaîne détection, inférence et remplacement — ligatures ET paires
    de symboles autonomes (guillemets, voir `infer_symbol_pair_mapping`) :
    même mécanisme sous-jacent (glyphe de police sans correspondance
    Unicode), même remplacement final (`repair_ligatures` ne fait qu'un
    remplacement caractère → chaîne, indifférent à ce qu'il répare).
    Retourne le texte réparé (inchangé si aucun code résolu) et la
    correspondance utilisée (les deux catégories fusionnées — un code donné
    ne peut appartenir qu'à l'une des deux, voir leurs docstrings
    respectives)."""
    occurrences = find_ligature_occurrences(text)
    mapping = infer_ligature_mapping(occurrences)
    mapping = {**mapping, **infer_symbol_pair_mapping(text)}
    return repair_ligatures(text, mapping), mapping
