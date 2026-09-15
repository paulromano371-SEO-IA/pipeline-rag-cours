"""Description d'image en langage naturel, via un appel `claude -p` headless
avec l'outil `Read` restreint au dossier `images/` du document traité.

Contrairement aux autres appels `claude -p` de ce projet (`concepts.py`,
`entity_resolution.py` : texte seul, `--tools ""`), décrire une image exige que Claude
puisse réellement la voir — impossible via stdin seul. On active donc l'outil
`Read`, mais restreint via `--add-dir` au seul dossier contenant l'image
traitée (jamais au projet entier), pour garder l'appel aussi borné que
possible.

La classification formule/image générale n'est PAS faite ici : elle est déjà
déterminée en amont par `image_classifier.classify` (heuristique déterministe,
sans appel LLM) et simplement transmise à Claude comme contexte.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from _claude_code_client import strip_ansi_codes, strip_code_fence

DEFAULT_TIMEOUT = 120

_SYSTEM_PROMPT = (
    "Tu décris des images extraites d'un cours technique, pour un système de recherche "
    "documentaire (RAG) en français. On te donne le chemin d'un fichier image et son type déjà "
    "déterminé (formule mathématique isolée, ou image générale). Lis le fichier avec l'outil Read, "
    "puis réponds UNIQUEMENT avec un objet JSON de la forme {\"description\": \"...\"}, sans aucun "
    "texte ni balise de code autour. La description : 1 à 3 phrases factuelles en français, qui "
    "mentionnent explicitement le type de contenu (schéma, courbe, formule, capture d'écran, "
    "photo, architecture...). Si le contenu est ambigu ou illisible, dis-le explicitement dans la "
    "description plutôt que d'inventer une interprétation."
)


class ImageVisionError(RuntimeError):
    pass


def describe_image(image_path: Path, categorie: str, *, model: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Retourne la description en langage naturel de l'image. `categorie` est
    "formule" ou "generale" (voir `image_classifier.classify`), transmise à
    Claude comme contexte déjà établi, pas redemandée."""
    image_path = Path(image_path).resolve()
    image_dir = image_path.parent

    cmd = [
        "claude", "-p",
        "--allowedTools", "Read",
        "--add-dir", str(image_dir),
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--output-format", "json",
        "--system-prompt", _SYSTEM_PROMPT,
    ]
    if model:
        cmd += ["--model", model]

    input_text = f"Type déjà détecté : {categorie}. Chemin de l'image à lire et décrire : {image_path}"

    try:
        proc = subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ImageVisionError(f"claude -p (vision) a dépassé le délai ({timeout}s)") from exc

    if proc.returncode != 0:
        raise ImageVisionError(
            f"claude -p (vision) a échoué (code {proc.returncode}): {strip_ansi_codes(proc.stderr).strip()}"
        )

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ImageVisionError(f"réponse non-JSON de claude -p (vision) : {proc.stdout[:2000]!r}") from exc

    if payload.get("is_error"):
        raise ImageVisionError(f"claude -p (vision) a retourné une erreur: {payload}")

    if "result" not in payload:
        raise ImageVisionError(f"réponse claude -p (vision) sans champ 'result' : {payload}")

    raw = strip_code_fence(payload["result"])
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ImageVisionError(f"description non-JSON renvoyée par Claude : {raw!r}") from exc

    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ImageVisionError(f"réponse JSON sans description exploitable : {data!r}")

    return description.strip()
