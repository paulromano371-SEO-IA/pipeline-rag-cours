"""Garde-fou d'interpreteur : tout script d'entree du pipeline RAG doit tourner
dans <racine_projet>/.venv-rag, jamais dans le Python systeme.

A importer EN PREMIER (juste apres `from __future__`) par chaque script
d'entree, avant tout import de dependance tierce :

    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(next(p for p in _Path(__file__).resolve().parents if (p / ".claude" / "skills" / "_rag_lib").is_dir()) / ".claude" / "skills" / "_rag_lib"))
    import venv_guard  # noqa: F401

Si l'interpreteur courant n'est pas celui du venv, le script est relance
tel quel (memes arguments, foreground, meme code de sortie) avec
`.venv-rag/Scripts/python.exe`. Si le venv est introuvable, arret avec un
message clair (code 1) plutot qu'un `ModuleNotFoundError` trompeur.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VENV = ROOT / ".venv-rag"
VENV_PYTHON = VENV / "Scripts" / "python.exe"
_ENV_FLAG = "RAG_VENV_GUARD_REEXEC"


def _in_venv() -> bool:
    try:
        return Path(sys.prefix).resolve() == VENV.resolve()
    except OSError:
        return False


def _ensure_venv() -> None:
    if _in_venv():
        return
    main_file = getattr(sys.modules.get("__main__"), "__file__", None)
    if not main_file:  # session interactive / `python -c` : rien a relancer
        return
    if not VENV_PYTHON.is_file():
        sys.exit(f"[venv_guard] Interpreteur du projet introuvable : {VENV_PYTHON}")
    if os.environ.get(_ENV_FLAG):
        sys.exit(
            f"[venv_guard] Relance dans {VENV_PYTHON} sans effet "
            f"(sys.prefix={sys.prefix}) : venv corrompu ou deplace ?"
        )
    print(
        f"[venv_guard] Python systeme detecte ({sys.executable}) : "
        f"relance avec {VENV_PYTHON}",
        file=sys.stderr,
    )
    env = dict(os.environ, **{_ENV_FLAG: "1"})
    result = subprocess.run([str(VENV_PYTHON), *sys.argv], env=env)
    sys.exit(result.returncode)


_ensure_venv()
