"""Description d'une formule mathématique d'affichage en langage naturel, via
un appel `claude -p` headless texte seul (voir
`_claude_code_client.run_claude_code`).

Ne traite QUE les formules déjà reprises en LaTeX exact dans `pivot.md`
(depuis `course.tex`, voir `rag-extraction/scripts/tex_source.py`) — jamais
une formule restée image (celles-ci passent par `image_vision.py` +
`ocr_formula.py`, comme avant). Le LaTeX étant déjà du texte, aucune raison
de passer par l'outil `Read` ni par un accès disque.

Réponse demandée en TEXTE BRUT, jamais en JSON : une seule valeur à
transporter (la description), le JSON n'apportait aucune structure utile et
exposait à un risque de syntaxe cassée (accolade manquante...) que du texte
brut n'a structurellement pas — vérifié empiriquement (échecs de parsing
JSON non-déterministes, corrigés par ce changement de format plutôt que
par une simple retentative)."""

from __future__ import annotations

from _claude_code_client import (
    ClaudeCodeCallError,
    MAX_FORMAT_RETRIES,
    build_batch_prompt,
    clean_plain_text_response,
    is_single_paragraph,
    parse_batch_response,
    run_claude_code,
)

DEFAULT_TIMEOUT = 120

_SYSTEM_PROMPT = (
    "Tu décris des formules mathématiques d'un cours technique, pour un système de recherche "
    "documentaire (RAG) en français. On te donne le LaTeX source exact d'une formule "
    "d'affichage isolée (jamais une formule inline dans une phrase). Réponds UNIQUEMENT avec la "
    "description elle-même, en texte brut — jamais de JSON, jamais de balise de code, jamais de "
    "préambule ni de guillemets autour du texte. La description : 1 à 3 phrases factuelles en "
    "français qui nomment la formule si elle est reconnaissable (ex. « règle de Bayes », "
    "« gradient de la fonction de coût ») et explicitent ce qu'elle représente — jamais une "
    "simple retranscription symbole par symbole. Si la formule est trop ambiguë pour être "
    "identifiée avec certitude, dis-le explicitement plutôt que d'inventer une interprétation."
)

_BATCH_SYSTEM_PROMPT = (
    "Tu décris plusieurs formules mathématiques d'un cours technique, pour un système de "
    "recherche documentaire (RAG) en français. On te donne N formules en LaTeX source exact, "
    "chacune précédée d'un marqueur ###k### (k = son numéro, à partir de 1) sur sa propre ligne. "
    "Réponds avec EXACTEMENT N descriptions, chacune précédée du même marqueur ###k### sur sa "
    "propre ligne, puis la description sur la ou les lignes suivantes — dans le même ordre, rien "
    "d'autre : jamais de JSON, jamais de balise de code, jamais de préambule ni de conclusion "
    "générale commune aux formules. Chaque description : 1 à 3 phrases factuelles en français qui "
    "nomment LA FORMULE CORRESPONDANTE si elle est reconnaissable et explicitent ce qu'elle "
    "représente — jamais une simple retranscription symbole par symbole, jamais une description "
    "qui mélange plusieurs formules entre elles. Si une formule est trop ambiguë pour être "
    "identifiée avec certitude, dis-le explicitement dans SA description plutôt que d'inventer "
    "une interprétation."
)


class FormulaDescriptionError(RuntimeError):
    pass


def describe_formula(formula_block: str, *, model: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Retourne la description en langage naturel de `formula_block` (le
    LaTeX complet, délimiteurs `$$...$$`/`\\[...\\]`/environnement inclus).

    Retente jusqu'à `MAX_FORMAT_RETRIES` fois (voir `_claude_code_client.py`)
    si la réponse est vide ou multi-paragraphe — un nouvel appel identique
    réussit la plupart du temps (vérifié empiriquement), jamais retenté en
    revanche pour un échec d'infrastructure (`ClaudeCodeCallError`)."""
    last_error: str | None = None

    for _ in range(MAX_FORMAT_RETRIES + 1):
        try:
            result = run_claude_code(_SYSTEM_PROMPT, formula_block, model=model, timeout=timeout)
        except ClaudeCodeCallError as exc:
            raise FormulaDescriptionError(str(exc)) from exc

        description = clean_plain_text_response(result)
        if not description:
            last_error = f"réponse vide de claude -p (formule) : {result!r}"
            continue

        if not is_single_paragraph(description):
            last_error = (
                f"description multi-paragraphe rejetée (casserait la fusion bloc+description de "
                f"chunk.py, qui suppose un seul paragraphe) : {description!r}"
            )
            continue

        return description

    raise FormulaDescriptionError(f"{last_error} (échec après {MAX_FORMAT_RETRIES + 1} tentative(s))")


def describe_formula_batch(formula_blocks: list[str], *, model: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> list[str]:
    """Décrit plusieurs formules EN UN SEUL appel `claude -p` (voir
    `_claude_code_client.MAX_GROUP_SIZE` pour la taille maximale d'un
    groupe) — réduit le nombre d'allers-retours séquentiels sur un document
    à beaucoup de formules, le vrai goulot d'étranglement de `/rag-nottext`
    sur un document volumineux (vérifié empiriquement).

    Retourne les descriptions dans le MÊME ORDRE que `formula_blocks`.
    Retente jusqu'à `MAX_FORMAT_RETRIES` fois SUR LE GROUPE ENTIER (jamais
    un retraitement partiel) si la réponse n'est pas exploitable avec
    certitude — voir `parse_batch_response`."""
    n = len(formula_blocks)
    input_text = build_batch_prompt(formula_blocks)
    last_error: str | None = None

    for _ in range(MAX_FORMAT_RETRIES + 1):
        try:
            result = run_claude_code(_BATCH_SYSTEM_PROMPT, input_text, model=model, timeout=timeout)
        except ClaudeCodeCallError as exc:
            raise FormulaDescriptionError(str(exc)) from exc

        descriptions = parse_batch_response(result, n)
        if descriptions is None:
            last_error = f"réponse de groupe non exploitable ({n} formule(s) attendue(s)) : {result!r}"
            continue

        if any(not d or not is_single_paragraph(d) for d in descriptions):
            last_error = f"une description du groupe est vide ou multi-paragraphe : {descriptions!r}"
            continue

        return descriptions

    raise FormulaDescriptionError(f"{last_error} (échec après {MAX_FORMAT_RETRIES + 1} tentative(s))")
