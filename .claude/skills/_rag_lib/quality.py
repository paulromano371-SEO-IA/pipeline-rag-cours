"""Gate qualité post-extraction.

Détecte, page par page, les signes d'une extraction ratée : problèmes
d'encodage, pages quasi vides (probablement scannées), images en trop basse
résolution. Objectif : flaguer ces pages pour un traitement différencié
(ex. relecture visuelle ciblée) plutôt que de les laisser contaminer
silencieusement le RAG.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

_REPLACEMENT_CHAR = "�"
_ALLOWED_CONTROL_CHARS = {"\n", "\t", "\r"}
_ENCODING_ISSUE_RATIO_THRESHOLD = 0.01
MIN_IMAGE_DIMENSION = 150
LOW_TEXT_DENSITY_THRESHOLD = 40
NOISE_ALPHA_RATIO_THRESHOLD = 0.3
NOISE_MIN_LENGTH = 60

# Seuls types que ce module (ou /rag-extraction en aval) corrige/traite lui
# meme — jamais bloquants, contrairement aux autres types que ce module se
# contente de signaler sans les corriger. control_char_ligature : voir
# /rag-extraction:auto_repair_ligatures, appele des qu'une occurrence est
# detectee. unresolved_control_char : le reliquat APRES cette reparation
# (ligature en plein mot jamais reconnue par le dictionnaire, ou symbole
# de police sans paire ouvrant/fermant — voir ligature_repair.py) ; une
# degradation connue et acceptee (contenu decoratif perdu au pire, jamais
# du texte), jamais une raison de bloquer la suite du pipeline. Fixe ici,
# jamais laisse a l'appreciation de l'appelant (le detail exact d'un des
# types "bloquant par defaut" reste ouvert a une exception cas par cas
# justifiee explicitement, mais la valeur PAR DEFAUT ne doit jamais
# dependre d'une relecture du texte de ce module).
_NEVER_BLOCKING_KINDS = {"control_char_ligature", "unresolved_control_char"}


def _is_blocking_by_default(kind: str) -> bool:
    return kind not in _NEVER_BLOCKING_KINDS


@dataclass
class QualityIssue:
    page_number: int
    kind: str
    detail: str
    # Calcule automatiquement depuis `kind` si non fourni explicitement —
    # aucun appelant existant n'a besoin d'etre modifie pour en beneficier.
    # Ne le fixe explicitement que pour une exception cas par cas justifiee
    # (voir SKILL.md de /rag-extraction) ; jamais pour reproduire la valeur
    # par defaut, qui reste toujours calculee ici, au meme endroit, jamais
    # eparpillee dans le texte d'un skill.
    blocking: bool | None = None

    def __post_init__(self) -> None:
        if self.blocking is None:
            self.blocking = _is_blocking_by_default(self.kind)


@dataclass
class QualityReport:
    issues: list[QualityIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def blocking_issues(self) -> list[QualityIssue]:
        return [i for i in self.issues if i.blocking]

    @property
    def indicative_issues(self) -> list[QualityIssue]:
        return [i for i in self.issues if not i.blocking]

    def pages_needing_review(self) -> set[int]:
        return {issue.page_number for issue in self.issues}


def _replacement_char_ratio(text: str) -> float:
    if not text:
        return 0.0
    return text.count(_REPLACEMENT_CHAR) / len(text)


def count_control_chars(text: str) -> int:
    """Compte les caractères de contrôle non standard (hors saut de
    ligne/tabulation). Observé en pratique : des ligatures (fi, ff, fl...)
    dont la police PDF n'a pas de correspondance Unicode correcte ressortent
    comme un caractère de contrôle invisible en plein milieu d'un mot — un
    défaut silencieux qui ne produit AUCUN U+FFFD et passerait inaperçu si on
    ne cherchait que ça. Public (pas de `_`) : réutilisé par `run.py` après
    `auto_repair_ligatures` pour compter le reliquat non résolu (voir
    `unresolved_control_char` ci-dessus), pas seulement ici en amont de la
    réparation."""
    return sum(
        1
        for c in text
        if c not in _ALLOWED_CONTROL_CHARS and c != _REPLACEMENT_CHAR and unicodedata.category(c) == "Cc"
    )


def _alphabetic_ratio(text: str) -> float:
    non_space = [c for c in text if not c.isspace()]
    if not non_space:
        return 1.0
    return sum(1 for c in non_space if c.isalpha()) / len(non_space)


def is_noise_text(text: str, *, min_length: int = NOISE_MIN_LENGTH, threshold: float = NOISE_ALPHA_RATIO_THRESHOLD) -> bool:
    """Détecte un texte fait presque uniquement de symboles répétés (`- - -`,
    `$$$$$`, `##########`...) plutôt que de vrai texte — trouvé en pratique
    sur des pages contenant un diagramme dont les motifs de remplissage
    (hachures) ont été extraits comme du texte par PyMuPDF au lieu d'être
    reconnus comme des éléments graphiques. Réutilisable au-delà de la page
    (ex. avant d'envoyer un chunk à une extraction LLM) pour éviter de
    gaspiller un appel sur du contenu qui n'a aucune valeur sémantique."""
    if len(text) < min_length:
        return False
    return _alphabetic_ratio(text) < threshold


def assess_extraction_quality(result) -> QualityReport:
    """`result` : une `convert.ExtractionResult` (voir
    `rag-extraction/scripts/convert.py`) — pas de dépendance d'import ici,
    `convert.py` n'est utilisé que par `/rag-extraction`, contrairement à ce
    module partagé par plusieurs étapes du pipeline."""
    report = QualityReport()

    for page in result.pages:
        ratio = _replacement_char_ratio(page.markdown)
        if ratio > _ENCODING_ISSUE_RATIO_THRESHOLD:
            report.issues.append(
                QualityIssue(
                    page.page_number,
                    "encoding",
                    f"{ratio:.1%} de caractères de remplacement (U+FFFD)",
                )
            )

        control_char_count = count_control_chars(page.markdown)
        if control_char_count > 0:
            # Zéro tolérance : même une seule occurrence corrompt un mot
            # entier (ex. "figées" -> "\\x1cgées"), donc pas de seuil de ratio ici.
            report.issues.append(
                QualityIssue(
                    page.page_number,
                    "control_char_ligature",
                    f"{control_char_count} caractère(s) de contrôle probablement issus d'une ligature mal encodée",
                )
            )

        if is_noise_text(page.markdown):
            report.issues.append(
                QualityIssue(
                    page.page_number,
                    "non_textual_noise",
                    f"{_alphabetic_ratio(page.markdown):.1%} de caractères alphabétiques seulement — "
                    "probablement un diagramme dont les motifs de remplissage ont été extraits comme du texte",
                )
            )

        if page.char_count < LOW_TEXT_DENSITY_THRESHOLD and not page.images:
            report.issues.append(
                QualityIssue(
                    page.page_number,
                    "low_text_density",
                    f"seulement {page.char_count} caractères extraits — page probablement scannée",
                )
            )

        for image in page.images:
            # Les zones de formule (convert.py, page_NNN_formula_NN.png) sont
            # rasterisees volontairement au plus juste (hauteur = une ou deux
            # lignes de texte) : leur "basse resolution" en pixels ne reflete
            # aucun probleme de qualite de l'image source, juste leur nature
            # de crop de texte plutot que d'illustration — jamais pertinent
            # ici, contrairement a une vraie image embarquee basse resolution.
            if "_formula_" in image.path.name:
                continue
            if min(image.width, image.height) < MIN_IMAGE_DIMENSION:
                report.issues.append(
                    QualityIssue(
                        page.page_number,
                        "low_resolution_image",
                        f"{image.path.name} : {image.width}x{image.height}px",
                    )
                )

    return report
