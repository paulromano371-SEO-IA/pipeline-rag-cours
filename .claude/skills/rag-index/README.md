# /rag-index — Étape 5 du pipeline RAG

Ce document explique la commande `/rag-index` pour un lecteur humain. La consigne
exacte lue par Claude Code est dans `SKILL.md`, à côté de ce fichier.

---

## 1. À quoi sert la commande

`/rag-index` **embedde** chaque chunk produit par `/rag-chunking`, puis l'indexe dans
une **base vectorielle Chroma** locale.

- Le modèle d'embedding est **`BAAI/bge-m3`**, multilingue, exécuté **localement**
  via sentence-transformers. Aucune clé API n'est nécessaire.
- La base est **unique et commune à tout le corpus** : chaque document y ajoute ses
  chunks sans écraser ceux des autres. Il n'y a jamais une base par pdf.
- Le script est **déterministe** : aucun appel à un modèle de langage.

C'est la **cinquième étape du pipeline RAG**, après `/rag-chunking`.

### Chunks écartés comme bruit

Certains chunks ne sont pas indexés : ce sont des motifs de hachures mal extraits d'un
diagramme. Le critère exact (`_rag_lib/quality.py`, fonction `is_noise_text`) :

- le chunk fait **au moins 60 caractères** ;
- **moins de 30 %** de ses caractères non blancs sont alphabétiques.

Ce filtre est volontaire. Le nombre de chunks écartés est affiché et ne signale pas un défaut.

---

## 2. Utilisation

### Sans argument : lister les pdf à indexer

```bash
".venv-rag/Scripts/python.exe" ".claude/skills/rag-index/scripts/lib/lister_a_indexer.py"
```

Liste, en lecture seule, les pdf de `corpuscondense/` prêts pour `/rag-index` :
ceux dont `status.json` est à `done` pour `chunking`, et dont
`indexation_vectorielle` n'est pas encore `done`.

### Avec un pdf

```bash
".venv-rag/Scripts/python.exe" ".claude/skills/rag-index/scripts/run.py" "<pdf_condense_ou_document_id_ou_dossier_de_travail>"
```

Le pdf s'indique par le chemin de son PDF condensé (`corpuscondense/<nom>.pdf`), par
son `document_id`, ou par son dossier de travail.

| Option | Effet |
|---|---|
| `--force` | Réindexe le document même si `status.json` le marque comme fait. |

### Prérequis

- `rag_data/work/<document_id>/chunks.json` doit exister (produit par `/rag-chunking`).
- L'interpréteur à utiliser est **`.venv-rag/Scripts/python.exe`**, jamais `python` ni
  `py`. Un hook refuse ces appels.

### Exécution

- Toujours en **premier plan**, sans `run_in_background`. Un hook refuse les lancements
  en arrière-plan.
- Jamais deux commandes du pipeline en parallèle.
- Pas de sous-agent pour lire ou vérifier les sorties.

### Précaution sur la base partagée

Deux processus qui créent **en même temps** une base Chroma encore inexistante peuvent
entrer en conflit sur la création du schéma SQLite. Le processus perdant se retrouve avec
un client corrompu.

Une fois la base créée, des exécutions concurrentes sur des documents différents n'ont
pas reproduit l'erreur dans les tests, mais rien ne garantit cela à plus grande échelle.
**Lancer les indexations une à une.**

---

## 3. Étapes du script

### 3.1 Idempotence et vérification de la base

Sans `--force`, si `status.json` contient `indexation_vectorielle` à `done`, le script
ne fait rien. Mais ce marquage n'est pas cru sur parole : le comptage des chunks dans
Chroma est vérifié.

- Si la base contient bien le document : « déjà fait », arrêt sans erreur.
- Sinon : le script signale que `status.json` dit `done` mais que la base ne le confirme
  pas, puis réindexe.

Avec `--force`, ou lors d'un premier passage, un ré-import du même `document_id`
**remplace** entièrement ses chunks existants. Les anciennes entrées sont supprimées
d'abord, puis les nouvelles insérées. Ainsi, un document réindexé avec moins de chunks
ne laisse jamais d'entrées orphelines. Un simple upsert ne retire jamais les identifiants
qui ne sont plus fournis.

### 3.2 Contrôles d'entrée

`_rag_lib/checks.py` vérifie (code de sortie `1` si échec) :

- `chunks.json` est valide et non vide ;
- `/rag-chunking` est terminé ;
- le `document_id` est cohérent avec `meta.json` ;
- il existe au moins un chunk indexable.

### 3.3 Filtrage du bruit

Seuls les chunks qui ne sont pas du bruit sont indexés (section 1).

### 3.4 Indexation

`_rag_lib/vector_store.py`, fonction `index_chunks` :

1. supprime les entrées existantes du même `document_id` ;
2. calcule les embeddings avec `BAAI/bge-m3` ;
3. insère les nouveaux chunks.

### 3.5 Contrôles de sortie

`checks.py` vérifie (code `2` et étape `failed` si échec) :

- **comptage** : le nombre de chunks présents dans Chroma pour ce `document_id` est
  **exactement** égal au nombre attendu (chunks moins bruit) ;
- **requête de test** : le premier chunk indexable, réinterrogé avec son propre
  embedding, doit être retrouvé **en premier**. Le modèle étant déjà chargé dans le
  processus, il n'est pas rechargé.

---

## 4. Codes de sortie

| Code | Signification |
|---|---|
| `0` | Terminé, ou déjà fait et confirmé par la base. |
| `1` | Échec d'entrée (section 3.2). |
| `2` | Échec de sortie : comptage ou requête de test. L'étape est marquée `failed`. |

---

## 5. Sorties

### Base vectorielle

`rag_data/db/vector/`, **commune à tout le corpus**. Chaque chunk est identifié par le
`document_id` du document. Ce `document_id` est stable : il est calculé à l'extraction et
stocké dans `meta.json`.

### Statut

`status.json` est mis à jour dans le dossier de travail du document.

### Compte rendu affiché

- un titre court : « Indexation terminée — `<nom du document>` » ;
- le nombre de chunks indexés ;
- le nombre de chunks ignorés comme bruit (filtrage volontaire, pas un défaut) ;
- le nom du modèle d'embedding, `BAAI/bge-m3`.

Le `document_id` affiché est réutilisé tel quel par `/rag-graphe`. **Ne jamais le
recalculer à la main.** Il est dérivé automatiquement du même fichier `pivot.md`.

---

## 6. Ce qui dépend de cette étape

`/rag-concepts` et `/rag-graphe` **ne lisent pas la base vectorielle**. Ils ne dépendent
que de `chunks.json` et `concepts.json`.

L'indexation n'est donc le prérequis d'aucune étape suivante du pipeline. Elle sert à la
recherche directe, via `vector_store.search()`, au moment où le RAG est interrogé. Un
échec de cette étape ne bloque pas l'enchaînement sur `/rag-concepts`, mais il bloque la
recherche sur ce document.

---

## 7. Quand s'arrêter

- **Code `2`** : l'indexation n'est pas fiable pour ce document. Signaler l'échec, et ne
  pas considérer le document comme recherchable.
- **Code `1`** : corriger l'entrée, puis relancer.

---

## 8. Fichiers

| Fichier | Rôle |
|---|---|
| `SKILL.md` | Consigne lue par Claude Code. |
| `README.md` | Ce document. |
| `scripts/run.py` | Point d'entrée : vérification de la base, contrôles, filtrage du bruit, indexation, contrôle de sortie, statut. |
| `scripts/lib/lister_a_indexer.py` | Liste les pdf prêts pour `/rag-index`. Lecture seule. |
| `_rag_lib/vector_store.py` | Embeddings, insertion, suppression, recherche (`search`). |
| `_rag_lib/quality.py` | Critère de bruit (`is_noise_text`). |
| `_rag_lib/checks.py` | Contrôles d'entrée et de sortie. |

---

## 9. Ce que la commande n'est pas

- Elle **n'appelle aucun modèle de langage**.
- Elle **ne modifie pas** `chunks.json` ni `pivot.md`.
- Elle **ne supprime rien** dans la base, sauf les entrées du document réindexé.
- Elle **ne supprime rien** sous `rag_data/` en dehors de ces entrées.
