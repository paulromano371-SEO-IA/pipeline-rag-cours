# /rag-extraction — Étape 2 du pipeline RAG

Convertit un PDF de cours condensé (`corpuscondense/`, sortie de `/cours-condense`) en markdown pivot : `rag_data/work/<document_id>/pivot.md`. Aucun appel externe à `claude` n'est fait à l'intérieur des scripts, aucune rédaction de contenu.

## Usage

- `/rag-extraction` : liste les pdf à extraire et attend ton choix.

- `/rag-extraction --all` : traite les PDFs de cette liste un à un de manière séquentielle.

- `/rag-extraction <chemin_vers_pdf_condense>` : traite un seul PDF (comportement actuel).

## Sans argument : lister les pdf

Lance, en premier plan :

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-extraction/scripts/lib/lister_a_extraire.py"
```

Présente la liste dans la réponse, en Markdown numéroté, **hors de tout bloc de code**, avec les noms exacts.

- **Sans `--all`** : attends le choix de l’utilisateur :
  - **Numéro** : relance la liste pour avoir l’état à jour, puis prends le pdf portant ce numéro. Numéro absent : signale-le et redemande.
  - **Nom** : cherche le pdf dans `corpusdedepart/` (insensible à la casse, correspondance exacte ou sous-chaîne unique). Zéro ou plusieurs correspondances : signale-le et redemande.
- **Avec `--all`** : Traite les pdf de la liste **un par un, dans l’ordre**. Annonce chaque pdf en une ligne (numéro, nom, `document_id`), puis, une fois la liste épuisée, affiche un tableau récapitulatif : une ligne par pdf, avec son verdict et son `document_id`.

Le condensé du pdf choisi est `corpuscondense/<slug>.pdf`, où `<slug>` = `_slugify_livre(<nom du PDF source sans extension>)` (`_rag_lib/paths.py`).

## Interpréteur Python

Tout script de ce skill se lance **uniquement** avec
`"<racine_projet>/.venv-rag/Scripts/python.exe"`, jamais avec `python`/`py`
(Python système). Techniquement imposé par un hook `PreToolUse` sur `Bash` et
`PowerShell` (`.claude/hooks/block_system_python.py`, voir
`.claude/settings.json`), qui refuse toute commande lançant un script de
`.claude/skills/*/scripts/` ou `tools/` avec `python`/`py`.

## Exécution

Toujours en foreground, bloquant jusqu'à complétion — jamais via
`run_in_background` ni aucun mécanisme async, et jamais deux commandes du
pipeline en parallèle. Techniquement imposé par un hook `PreToolUse` sur `Bash`
(`.claude/hooks/block_rag_background.py`, voir `.claude/settings.json`), qui
refuse tout `run_in_background: true` sur une commande référençant un script
d'un skill du projet.

**Interdiction de déléguer à un sous-agent** (outil `Agent`) la lecture ou la
vérification des sorties de ce skill : elle se fait directement, dans le même
tour de conversation, jamais « pour économiser du contexte ».

## Règle d'exécution (déterminisme)

Ce pipeline doit se dérouler **de façon identique à chaque lancement**, quel 
que soit le pdf traité. Sont interdits, à toutes les étapes, sauf demande 
explicite et ponctuelle de l'utilisateur dans le message qui déclenche la commande :

- **Déléguer à un sous-agent** (outil `Agent`) la lecture des pages extraites, 
  l'analyse du contenu ou la rédaction du cours. C'est toi, dans ce même tour de 
  conversation, qui lis directement chaque lot de pages (`Read`) et qui rédiges 
  — jamais un sous-agent "pour économiser du contexte". Si le volume est important, découpe en lots de lecture plus petits, mais reste le seul lecteur/rédacteur.
- **Toute tâche en arrière-plan ou planifiée** (`ScheduleWakeup`, agents async, etc.) pour une étape du pipeline lui-même.

## Avec un PDF : trois étapes

Annonce en une phrase ce que tu vas faire, puis lance les trois étapes **dans cet ordre, une à la fois, en premier plan** et affiche les retours dans la session principale. 

Pour corriger les problèmes dans le fichier `rag_data/work/<document_id>/pivot.md`, écris et exécute des scripts Python (sauvegardés dans `rag_data/work/<document_id>/scripts/`). 

Le nombre de création, modification de scripts python dans `rag_data/work/<document_id>/scripts/`est illimité. 

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-extraction/scripts/etape1_extraction.py" "<racine_projet>/corpuscondense/<nom_du_pdf>.pdf"
```

Affiche dans la session principale ce que fait le script etape1_extraction.py.

Si `etape1_extraction.py` signale des erreurs, il fait corriger par le modèle en session principale. Avant d'appliquer la correction par le modèle, sauvegarder `pivot.md`. Continuez ainsi jusqu'a ce qu'il n'y ait plus d'erreurs retournées.

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-extraction/scripts/etape2_verification.py" "<racine_projet>/corpuscondense/<nom_du_pdf>.pdf"
```

Affiche dans la session principale ce que fait le script etape2_verification.py.

Si `etape2_verification.py` signale des erreurs, il fait corriger par le modèle en session principale. Avant d'appliquer la correction par le modèle, sauvegarder `pivot.md`. Continuez ainsi jusqu'a ce qu'il n'y ait plus d'erreurs retournées.

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-extraction/scripts/etape3_statut.py" "<racine_projet>/corpuscondense/<nom_du_pdf>.pdf"
```

Affiche dans la session principale ce que fait le script etape3_statut.py.

Si `etape3_statut.py` signale des erreurs, il fait corriger par le modèle en session principale. Avant d'appliquer la correction par le modèle, sauvegarder `pivot.md`. Continuez ainsi jusqu'a ce qu'il n'y ait plus d'erreurs retournées.

Si `etape3_statut.py` a reussi alors afficher le message EXTRACTION REUSSIE !!!!

Options de l'étape 1 :

- `--force` : refait l'extraction même si elle est déjà faite.
- `--pages DEBUT:FIN` : limite à une plage de pages (fin exclue), pour tester.

| Étape | Rôle                                                                                                                                           | Ce qu'elle écrit           |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------- |
| 1     | Extraction du PDF, réparation des ligatures, écriture de `pivot.md` et `meta.json`                                                             | `etape1_extraction.json`   |
| 2     | Contrôle de fidélité contre `course.tex`. Corrige seule les titres bloquants d'après `course.tex`, en sauvegardant `pivot.avant_correction.md` | `etape2_verification.json` |
| 3     | Verdict, `status.json`, rapport final                                                                                                          | —                          |

## Après chaque étape

- Relaie la **sortie complète** de chaque étape, sans la reformuler ni la tronquer. Le rapport de l'étape 3 est affiché tel quel.
- Une ligne `ÉCHEC` : il fait corriger par le modèle en session principale.
- Étape 2 en `ÉCHEC` avec « etape1_extraction.json absent » : le pdf a été extrait avant cette version. Relance l'étape 1 avec `--force`. SInon il fait corriger par le modèle en session principale.
- Étape 3 en `BLOQUANT` (code `2`) : il reste des problèmes qualité ou fidélité. Applique la correction par le modèle sans demander l'accord de l'utilisateur :
  1. sauvegarde `pivot.md` (copie dans le scratchpad) ;
  2. corrige uniquement les défauts signalés dans le rapport ;
  3. relance l'étape 2, puis l'étape 3 ;
  4. Continue jusqu'à ce que le pivot.md soit OK
- Étape 3 en `OK` : l'extraction est exploitable. `/rag-nottext` peut démarrer. Afficher le message EXTRACTION REUSSIE !!!!

## Règles

- Ne supprime jamais rien sous `rag_data/`.

## Sorties

Dans `rag_data/work/<document_id>/` :

- `pivot.md` : texte structuré (titres, code, prose, images)
- `images/` : images natives et zones de formule
- `meta.json` : `document_id`, `source_pdf`
- `status.json` : suivi de l'étape `extraction`, avec les comptes de bloquants
- `etape1_extraction.json`, `etape2_verification.json` : résultats échangés entre les étapes
- `pivot.avant_correction.md` : présent seulement si l'étape 2 a corrigé des titres
