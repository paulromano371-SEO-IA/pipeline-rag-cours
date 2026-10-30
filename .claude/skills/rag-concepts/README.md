# rag-concepts : extraction des concepts par chunk

Ce document explique la commande `/rag-concepts` : ce qu'elle fait, comment
la lancer, ses étapes et ses sorties. Le fichier `SKILL.md`, à côté de celui-ci,
est la consigne lue par Claude Code. Ce README est la documentation pour un
humain. En cas de désaccord, le code fait foi.

---

## 1. Ce que fait la commande

`/rag-concepts` est la **sixième étape du pipeline RAG**. Pour chaque chunk
produit par `/rag-chunking` (hors bruit), un appel `claude -p` headless, sans
outil ni MCP, extrait **3 à 8 concepts significatifs**. Cet appel ne consomme
pas le contexte de la conversation en cours.

Pour chaque concept, l'extraction donne :

- son **nom** ;
- sa **forme canonique** ;
- son **type** ;
- une **définition courte** (`sense`), de 5 à 12 mots : ce que le mot désigne
  **dans ce passage**.

La définition sert à ne pas fusionner deux **homonymes** de pdf différents.
Exemples : `lambda` peut être une fonction Python ou un paramètre de
régularisation ; `biais` peut être une notion statistique ou un paramètre d'un
réseau de neurones.

Ces concepts alimentent le **GraphRAG** : `/rag-graphe` les relie entre eux.

Un chunk dont l'extraction échoue est ignoré et journalisé, sans interrompre le
traitement des autres.

---

## 2. Utilisation

Sans argument, la commande liste les pdf prêts (voir section 3).

Avec un argument, elle traite le pdf :

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-concepts/scripts/run.py" "<pdf_condense_ou_document_id_ou_dossier_de_travail>"
```

Le script accepte indifféremment le chemin du PDF condensé, le `document_id`, ou
le dossier `rag_data/work/<document_id>/`.

| Option | Effet |
|---|---|
| `--force` | Retraite le document même si l'étape est déjà marquée `done`. |
| `--reset` | Supprime `concepts.json` et l'entrée `extraction_concepts` de `status.json`, puis régénère tout. |
| `--batch-size N` | Traite au plus N chunks par invocation (voir section 5). |

`<racine_projet>` est le dossier qui contient `.claude/`.

### Quand utiliser `--reset`

- Après un changement du prompt d'extraction : sans `--reset`, les anciennes et
  les nouvelles formes canoniques se mélangeraient.
- Pour régénérer un `concepts.json` extrait avant l'ajout de `sense`, ou dont le
  modèle a omis ce champ. Ce fichier reste valide, mais dégrade `/rag-graphe`,
  qui résout alors sur le nom seul.

`--reset` ne supprime **rien** tant que le contrôle d'entrée n'a pas réussi
(chunks valides et à jour, CLI `claude` disponible). En cas d'échec, `concepts.json`
et son statut restent intacts, et le script sort avec le code 1.

### Prérequis

- `rag_data/work/<document_id>/chunks.json`, produit par `/rag-chunking`.
- La CLI `claude` disponible dans le PATH.

---

## 3. Sans argument : lister les pdf

La commande lance `scripts/lib/lister_a_concepts.py`, en premier plan. Ce script
liste les pdf de `corpuscondense/` prêts pour `/rag-concepts`, c'est-à-dire :

- `indexation_vectorielle` à `done` dans `status.json` ;
- `extraction_concepts` pas encore `done`.

La lecture est seule, sans modification. La liste est présentée en Markdown
numéroté, avec les noms exacts, puis la commande attend le choix :

- **Sans `--all`** :
  - un **numéro** : la liste est relancée pour avoir l'état à jour, puis le pdf
    correspondant est pris. Un numéro absent est signalé, et la question reposée ;
  - un **nom** : recherche insensible à la casse dans `corpuscondense/`
    (correspondance exacte ou sous-chaîne unique). Zéro ou plusieurs résultats
    sont signalés, et la question reposée.
- **Avec `--all`** : les pdf sont traités **un par un, dans l'ordre**. Chaque
  pdf est annoncé en une ligne (numéro, nom, `document_id`). Une fois la liste
  épuisée, un tableau récapitulatif est affiché : une ligne par pdf, avec son
  verdict et son `document_id`.

---

## 4. Exécution

- **Premier plan uniquement**, bloquant jusqu'à complétion. Jamais
  `run_in_background`, jamais deux commandes du pipeline en parallèle. Un hook
  `PreToolUse` (`.claude/hooks/block_rag_background.py`) refuse tout lancement en
  arrière-plan d'un script de skill.
- **Pas de sous-agent** pour lire ou vérifier les sorties : c'est fait directement,
  dans le même tour.
- **Interpréteur** : `<racine_projet>/.venv-rag/Scripts/python.exe` uniquement,
  jamais `python` ni `py`. Un hook (`.claude/hooks/block_system_python.py`) refuse
  les autres appels.

---

## 5. Traitement par lots (documents volumineux)

Un appel `claude -p` par chunk. Sur un pdf de plusieurs centaines de chunks,
une seule invocation dépasserait les 10 minutes d'une commande en premier plan,
et l'application la passerait en arrière-plan. D'où le découpage en lots.

### Lancer un lot

Le wrapper recommandé est `scripts/concepts_lot.py`. Il traite **un lot par appel** :

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-concepts/scripts/concepts_lot.py" "<document_id_ou_pdf>" --batch-size 10 [--reset]
```

`--reset` ne s'utilise que sur le **tout premier** lot.

La sortie se termine par **une seule ligne de bilan** :

| Ligne | Signification | Code de sortie |
|---|---|---|
| `[LOT n/N]` | Lot partiel : relancer la même commande. | 0 |
| `[FIN]` | Étape terminée. | 0 |
| `[BLOQUANT]` | Plus de 5 % de chunks sans concept, ou aucun concept. | 2 |
| `[ERREUR]` | Erreur technique. | 1 |

C'est cette ligne, et non le code de sortie, qui dit s'il faut relancer. Un lot
partiel sort en 0, car ce n'est pas une erreur. Seul `run.py`, appelé
directement, renvoie 3 sur un lot partiel.

### Enchaîner les lots

- Relancer **exactement** la même commande, sans `--force` ni `--reset`. La
  reprise retrouve les chunks déjà faits dans `concepts.json`.
- Entre deux lots, `status.json` est marqué `failed` avec le détail « lot partiel
  en cours : N chunk(s) restant(s) ». Il n'est jamais `done` avant le dernier lot.
- **Un lot où aucun chunk n'aboutit termine le traitement** (code 2). Sans cette
  règle, un chunk qui échoue à chaque tentative ferait boucler le traitement
  indéfiniment.

### Relayer chaque lot

Après chaque lot, la sortie brute du script est affichée dans un bloc de code,
avec sa ligne `[LOT n/N]`, avant de lancer le lot suivant. Sur `[BLOQUANT]` ou
`[ERREUR]`, le lot suivant ne se lance pas, et la cause est rapportée.

---

## 6. Écriture et reprise

- **Écriture immédiate** : `concepts.json` est réécrit **après chaque chunk
  réussi**. Une interruption (Ctrl+C, crash, timeout) ne perd jamais plus d'un
  chunk de progression.
- **Reprise** : une relance sans `--force` saute les chunks déjà présents dans
  `concepts.json`. Un chunk en échec n'y est jamais enregistré : il est donc
  retenté naturellement.

---

## 7. Contrôles

### Entrée (code 1 si échec)

`chunks.json` valide, `/rag-chunking` terminé, CLI `claude` disponible dans le PATH.
Ces contrôles sont dans `_rag_lib/checks.py`.

### Sortie bloquante (code 2, étape `failed`)

Plus de **5 %** des chunks indexables (hors bruit) sans concept, ou aucun concept
extrait.

Le taux est recalculé à partir de `concepts.json` (chunks attendus moins chunks
présents), donc il reste exact après une reprise. Les chunks de bruit (`n_noise`)
et les chunks en échec (`n_failed`) sont enregistrés séparément dans
`status.json` ; `chunks_ignores` les additionne, pour compatibilité.

**Dans ce cas, arrêter avant `/rag-graphe`.** Relancer la même commande retente
les chunks en échec.

### Avertissement (non bloquant)

Plus de **10 %** des mentions sans `sense`. L'avertissement apparaît dans la
sortie du contrôle de sortie, et au contrôle d'entrée de `/rag-graphe`.

---

## 8. Compte-rendu

La commande rapporte :

- le nombre total de mentions extraites ;
- le nombre de chunks ignorés.

---

## 9. Coût et durée

Un appel `claude -p` par chunk indexable. Pour un document de plusieurs dizaines
de chunks, prévoir quelques dizaines de secondes à quelques minutes. Chaque appel
est restreint (`--tools ""`), donc peu coûteux individuellement, mais le volume
total croît avec la taille du document.

---

## 10. Sorties

Dans `rag_data/work/<document_id>/` :

| Fichier | Contenu |
|---|---|
| `concepts.json` | Liste de `{"chunk_index": N, "mentions": [...]}`. Réécrit après chaque chunk. |
| `status.json` | Verdict, comptes (`n_noise`, `n_failed`, `chunks_ignores`), statut de l'étape `extraction_concepts`. |

---

## 11. Quand s'arrêter

- **Code 2** : ne pas enchaîner sur `/rag-graphe`.
- **Avertissement sur `sense`** : le signaler. Régénérer avec `--reset` si la
  qualité de la fusion compte.

---

## 12. Dossier `scripts/`

| Fichier | Rôle |
|---|---|
| `run.py` | Point d'entrée : contrôles, extraction, écriture progressive, verdict. |
| `concepts_lot.py` | Wrapper qui traite un lot par appel et affiche la ligne de bilan. |
| `lib/lister_a_concepts.py` | Liste les pdf prêts pour `/rag-concepts` (lecture seule). |
| `__pycache__/` | Cache Python généré. Ne rien y modifier. |

---

## 13. Ce que la commande n'est pas

- Elle **ne fusionne rien** : c'est `/rag-graphe`.
- Elle **ne modifie pas** `chunks.json`.
- Elle **n'utilise pas** la base vectorielle.
- Elle **ne supprime rien** sous `rag_data/` en dehors de `concepts.json` et de son
  statut, et seulement avec `--reset`, après contrôle d'entrée réussi.
