"""Socle commun des étapes de /cours-condense exécutées par `claude -p`.

Chaque étape LLM : un appel `claude -p` (MODEL, effort EFFORT, délai TIMEOUT_S),
une ligne par appel dans <dossier>/usage.jsonl, et une ligne de statut affichée
à la session qui lance la commande.

Le `system` de chaque appel d'une même étape est identique d'un appel à l'autre
(mêmes octets, même préfixe) pour maximiser le cache de prompt : ce qui varie
(chapitre, lot de pages) va dans le message utilisateur, jamais dans le system.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(next(p for p in _Path(__file__).resolve().parents if (p / ".claude" / "skills" / "_rag_lib").is_dir()) / ".claude" / "skills" / "_rag_lib"))
import venv_guard  # noqa: F401

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from _claude_code_client_systemfile_withtools import ClaudeCodeCallError, run_claude_code

# Sortie ligne à ligne : sans cela, Python met en tampon la sortie redirigée (fichier, pipe)
# et le suivi d'avancement n'apparaît qu'à la fin de l'étape.
sys.stdout.reconfigure(line_buffering=True)

MODEL = "claude-haiku-5-5"
EFFORT = "medium"
TIMEOUT_S = 600
MAX_ATTEMPTS = 3

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
LIB_DIR = SCRIPTS_DIR / "lib"
PROMPTS_DIR = SCRIPTS_DIR / "prompts"
RACINE_PROJET = next(p for p in SCRIPTS_DIR.parents if (p / ".claude" / "skills" / "_rag_lib").is_dir())
# Verrou du pipeline : un par pdf, dans son dossier de travail (rag_data/courscondense/<slug>/).
# Une étape longue à la fois POUR UN PDF ; deux livres différents peuvent tourner en même temps.
# Lu aussi par le hook block_rag_background.py.
NOM_VERROU = ".etape_en_cours.json"


class EtapeError(RuntimeError):
    """L'étape n'a pas produit de sortie exploitable après MAX_ATTEMPTS essais."""


def load_prompt(name: str) -> str:
    """Texte de l'étape, tel que copié depuis SKILL.md (jamais modifié à la main)."""
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def statut(numero: str, ok: bool, detail: str) -> None:
    print(f"[étape {numero}] {'OK' if ok else 'ÉCHEC'} — {detail}")


def pop_force() -> bool:
    """Retire `--force` de sys.argv (pour que le contrôle d'arguments de l'étape reste inchangé)."""
    if "--force" in sys.argv:
        sys.argv.remove("--force")
        return True
    return False


def pop_option(nom: str) -> str | None:
    """Retire `nom <valeur>` de sys.argv et renvoie la valeur (None si absent)."""
    if nom in sys.argv:
        i = sys.argv.index(nom)
        if i + 1 >= len(sys.argv):
            raise SystemExit(f"option {nom} sans valeur")
        valeur = sys.argv[i + 1]
        del sys.argv[i:i + 2]
        return valeur
    return None


def pid_vivant(pid: int) -> bool:
    """Vrai si un processus de PID `pid` tourne. Windows : OpenProcess (ne jamais utiliser os.kill(pid, 0), qui envoie un signal)."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def lancer_avec_verrou(numero: str, fonction) -> int:
    """Exécute `fonction()` (une étape longue) en posant le verrou du pipeline.

    Le verrou est celui du pdf : `sys.argv[1]` est le dossier de travail du pdf (`<nom_du_livre>`).
    Refuse de démarrer si un autre processus tient déjà le verrou de CE pdf (PID vivant).
    Un verrou orphelin (PID mort) est ignoré. Le verrou est retiré à la sortie, même en cas d'erreur.
    """
    verrou = Path(sys.argv[1]).resolve() / NOM_VERROU
    verrou.parent.mkdir(parents=True, exist_ok=True)
    if verrou.exists():
        try:
            occupant = json.loads(verrou.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            occupant = {}
        if pid_vivant(occupant.get("pid", -1)) and occupant.get("pid") != os.getpid():
            statut(numero, False, f"étape {occupant.get('etape')} en cours sur ce livre (PID {occupant.get('pid')}) : attendre sa fin")
            return 2
    verrou.write_text(json.dumps({
        "pid": os.getpid(),
        "etape": numero,
        "debut": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        return fonction()
    finally:
        try:
            if json.loads(verrou.read_text(encoding="utf-8-sig")).get("pid") == os.getpid():
                verrou.unlink()
        except (OSError, ValueError):
            pass


def extraire_bloc(raw: str, langue: str = "") -> str:
    """Contenu du premier bloc ```<langue> ... ``` de la réponse, sinon la réponse entière."""
    m = re.search(r"```(?:" + re.escape(langue) + r")?\s*\n(.*?)```", raw, re.S) if langue else None
    if m:
        return m.group(1).strip()
    return re.sub(r"^```[a-zA-Z]*\s*\n|\n?```\s*$", "", raw.strip()).strip()


def pages_texte(dossier: Path, debut: int, fin: int) -> str:
    """Texte brut des pages `debut`..`fin` (1-indexé, inclus), extraites par Etape1_2 dans extraction/pages/."""
    blocs = []
    for n in range(debut, fin + 1):
        chemin = dossier / "extraction" / "pages" / f"page_{n:04d}.txt"
        if chemin.exists():
            blocs.append(f"=== PAGE {n} ===\n{chemin.read_text(encoding='utf-8')}")
    return "\n\n".join(blocs)


def _log_usage(dossier: Path, etape: str, attempt: int, meta: dict) -> None:
    line = {
        "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "api", "etape": etape, "essai": attempt, **meta,
    }
    dossier.mkdir(parents=True, exist_ok=True)
    with (dossier / "usage.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def call_claude(dossier: Path, etape: str, system: str, user_text: str, parse, max_attempts: int = MAX_ATTEMPTS,
                renvoyer_erreur: bool = False):
    """Appel `claude -p` + validation par `parse(raw) -> valeur`.

    `parse` lève `ValueError` si la réponse n'est pas exploitable : nouvel essai.
    Un échec d'infrastructure (process, délai) est aussi retenté.
    Si `renvoyer_erreur` est vrai, le motif du refus précédent est ajouté au message de l'essai suivant.
    """
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        meta: dict = {}
        texte = user_text
        if renvoyer_erreur and last_error:
            texte += f"\n\nTentative précédente refusée : {last_error}\nCorrige ton résultat en conséquence."
        try:
            raw = run_claude_code(system, texte, model=MODEL, timeout=TIMEOUT_S, meta=meta, effort=EFFORT)
        except ClaudeCodeCallError as exc:
            last_error = f"appel claude -p en échec : {exc}"
        else:
            _log_usage(dossier, etape, attempt, meta)
            try:
                return parse(raw)
            except ValueError as exc:
                last_error = str(exc)
        print(f"  [{etape}] essai {attempt}/{max_attempts} : {last_error}", file=sys.stderr)
    raise EtapeError(last_error)


# --------------------------------------------------------------------------
# Sessions persistantes (lecteur du plan, rédacteur des chapitres)
# --------------------------------------------------------------------------

_sys.path.insert(0, str(LIB_DIR))
from session_claude import appeler_session  # noqa: E402

# Au-delà de cette part de la fenêtre de contexte, la rédaction passe à une nouvelle session (passation).
SEUIL_CONTEXTE = 0.8


class SessionPerdue(EtapeError):
    """La session à reprendre n'existe plus (transcription supprimée) : il faut la reconstruire."""


def charger_etat(chemin: Path) -> dict:
    try:
        return json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def sauver_etat(chemin: Path, etat: dict) -> None:
    chemin.write_text(json.dumps(etat, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def contexte_plein(etat: dict) -> bool:
    """Vrai si le contexte de la session dépasse SEUIL_CONTEXTE de la fenêtre (mesures du dernier appel)."""
    return bool(etat.get("fenetre")) and etat.get("contexte_tokens", 0) > SEUIL_CONTEXTE * etat["fenetre"]


def call_claude_session(dossier: Path, etape: str, etat_path: Path, system: str, user_text: str, parse,
                        *, repartir_de_zero: bool = False, max_attempts: int = MAX_ATTEMPTS):
    """Comme `call_claude`, mais dans la session dont l'identifiant est dans `etat_path`.

    - Sans session (ou `repartir_de_zero`) : crée une session avec `system` ; le system n'est fixé qu'à
      cette création, pas aux reprises.
    - Avec une session : reprise par `--resume --fork-session`. Chaque essai repart de l'état sain ; la
      session issue d'un essai réussi devient la tête, un essai raté ne pollue jamais le contexte.
    - Un essai invalide est retenté avec la raison du refus ajoutée au message.
    Écrit l'identifiant de la nouvelle tête et la taille du contexte dans `etat_path`.
    """
    etat = charger_etat(etat_path)
    tete = None if repartir_de_zero else etat.get("session_id")
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        texte = user_text
        if attempt > 1 and last_error:
            texte += f"\n\nATTENTION : ta réponse précédente a été refusée ({last_error}). Corrige ce point."
        try:
            r = appeler_session(system, texte, workdir=dossier / "_sessions", model=MODEL, effort=EFFORT,
                                timeout=TIMEOUT_S, resume=tete, fork=bool(tete))
        except ClaudeCodeCallError as exc:
            if tete and ("No conversation found" in str(exc) or "session" in str(exc).lower()):
                raise SessionPerdue(str(exc)) from exc
            last_error = f"appel claude -p en échec : {exc}"
        else:
            # `total_cost_usd` d'une session reprise est CUMULÉ depuis la création de la session : le coût de
            # cet appel est la différence avec le cumul de la tête dont il est issu.
            cumul = r["meta"].get("total_cost_usd") or 0.0
            r["meta"]["cout_appel_usd"] = round(cumul - (etat.get("cout_cumule", 0.0) if tete else 0.0), 6)
            _log_usage(dossier, etape, attempt, r["meta"])
            try:
                valeur = parse(r["result"])
            except ValueError as exc:
                last_error = str(exc)
            else:
                etat.update(session_id=r["session_id"], contexte_tokens=r["contexte_tokens"], fenetre=r["fenetre"],
                            cout_cumule=cumul)
                sauver_etat(etat_path, etat)
                return valeur
        print(f"  [{etape}] essai {attempt}/{max_attempts} : {last_error}", file=sys.stderr)
    raise EtapeError(last_error)


def charger_plan(dossier: Path) -> list[dict]:
    return json.loads((dossier / "plan_cours.json").read_text(encoding="utf-8"))


def assembler_course(dossier: Path) -> None:
    """Reconstruit course.tex = squelette (jusqu'au premier \\newpage inclus) + chapitres/ch*.tex + \\end{document}.

    Le préambule ajouté après coup (étapes 4.2 et 4.3) est conservé : il est avant \\begin{document}, donc dans l'en-tête.
    """
    course = dossier / "course.tex"
    texte = course.read_text(encoding="utf-8")
    entete = texte.split("\\newpage\n", 1)[0] + "\\newpage\n"
    chapitres = sorted((dossier / "chapitres").glob("ch*.tex"))
    corps = "\n\n".join(c.read_text(encoding="utf-8").strip() for c in chapitres)
    course.write_text(entete + "\n" + corps + "\n\n\\end{document}\n", encoding="utf-8")
