---
name: rag-index
description: >-
  Embedde et indexe les chunks (produits par /rag-chunking) dans la base
  vectorielle locale (Chroma).
  Usage: /rag-index <pdf_condense_ou_document_id>.
  Cinquième étape du pipeline RAG.
---

# /rag-index — Étape 5 du pipeline RAG

Embedde chaque chunk (modèle multilingue local via sentence-transformers,
aucune clé API) et l'indexe dans une base Chroma partagée entre tous les
documents du corpus.
Les chunks de bruit non textuel (motifs de hachures mal extraits d'un
diagramme) sont exclus — critère exact (`_rag_lib/quality.py`,
`is_noise_text`) : un chunk d'au moins 60 caractères dont moins de 30% des
caractères non-espace sont alphabétiques. Script déterministe, aucun appel
LLM.

## Exécution

Toujours en foreground, bloquant jusqu'à complétion — jamais via
`run_in_background` ni aucun mécanisme async. Contrairement à d'autres
étapes, ce script ne fait aucun appel `claude -p` : la contention entre
appels LLM imbriqués n'est donc pas la raison ici. La vraie raison, vérifiée
empiriquement : deux processus qui initialisent en même temps un
`PersistentClient` Chroma sur un dossier de base **pas encore créé**
entrent en course sur la création du schéma SQLite (`InternalError: table
collections already exists`, puis un client corrompu dans le processus
perdant). Une fois la base déjà initialisée par un premier run, des
invocations concurrentes sur des documents différents n'ont pas reproduit
l'erreur dans nos tests — mais rien ne garantit cette absence de risque à
plus grande échelle : n'exécute jamais deux invocations de `/rag-index` en
parallèle sur cette base partagée.

**Interdiction de déléguer à un sous-agent** (outil `Agent`) la lecture ou
vérification du résultat d'indexation — même raison que ci-dessus (éviter
toute exécution concurrente sur la base partagée), pas une histoire de
contention `claude -p` puisqu'il n'y en a aucune ici.

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-index/scripts/run.py" "<pdf_condense_ou_document_id_ou_dossier_de_travail>"
```

Options : `--force`.

## Prérequis

`rag_data/work/<document_id>/chunks.json` doit exister (produit par `/rag-chunking`).

## Idempotence

Sans `--force` : si `status.json` contient déjà
`{"indexation_vectorielle": {"status": "done"}}`, le script ne fait rien —
quel que soit l'état réel de la base Chroma. Contrairement à
`/rag-chunking`/`/rag-extraction`, cette vérification ne s'accompagne
d'aucun contrôle d'existence d'un fichier de sortie (il n'y en a pas, la
sortie vit dans la base partagée) : si la base Chroma est supprimée ou
corrompue mais que `status.json` dit encore `done`, le script sautera son
travail à tort, sans que rien ne le détecte.

Avec `--force` (ou lors d'un premier passage) : un ré-import du même
`document_id` **remplace** entièrement ses chunks existants dans la base —
les anciennes entrées de ce document sont d'abord supprimées, puis les
nouvelles insérées, pour qu'un document réindexé avec moins de chunks
qu'auparavant ne laisse jamais d'entrées périmées orphelines (comportement
vérifié : sans cette suppression préalable, un simple upsert ne retire
jamais les ids qui ne sont plus fournis).

## Sortie

Base vectorielle persistée dans `<racine_projet>/rag_data/db/vector/`
(**base UNIQUE, commune à tout le corpus** — chaque document y ajoute ses
chunks sans écraser les autres, jamais une base par livre). Le `document_id`
stable (calculé à l'extraction, stocké dans `meta.json`) identifie ce
document dans la base. `status.json` mis à jour.

## Critère de sortie exploitable

Ni `/rag-concepts` ni `/rag-graphe` ne lisent la base vectorielle — vérifié
dans leur code, ils ne dépendent que de `chunks.json`/`concepts.json`.
L'indexation n'est donc le prérequis mécanique d'aucune étape suivante du
pipeline : elle est exploitable uniquement pour une recherche/retrieval
directe via `vector_store.search()`, au moment où l'utilisateur interroge le
RAG — pas comme condition de blocage avant d'enchaîner sur `/rag-concepts`.

## Après exécution

Affiche un résumé structuré : titre court ("Indexation terminée —
`<nom du document>`"), puis synthèse chiffrée (nombre de chunks indexés,
nombre de chunks ignorés comme bruit, nom du modèle d'embedding utilisé —
`BAAI/bge-m3`). Le nombre de chunks ignorés comme bruit est un filtrage
volontaire, pas un défaut.

**Contrôles** (`_rag_lib/checks.py`) :
- **Entrée** (code `1` si échec) : `chunks.json` valide et non vide,
  `/rag-chunking` terminé, `document_id` cohérent avec `meta.json`, au moins
  un chunk indexable.
- **Sortie** (code `2` et étape `failed` si échec) : le nombre de chunks
  présents dans Chroma pour ce `document_id` doit être **exactement** égal au
  nombre attendu (chunks moins bruit), et une requête de test (le premier
  chunk indexable, réinterrogé par son propre embedding) doit le retrouver
  en premier — le modèle est déjà en cache dans ce process, pas de
  rechargement.
- **Faux « déjà fait »** : sans `--force`, un `status.json` à `done` n'est
  plus cru sur parole — le comptage Chroma est vérifié, et si la base ne
  contient pas le document, le script le signale et réindexe.

Le `document_id` affiché est réutilisé tel quel par `/rag-graphe` — ne le
recalcule jamais manuellement, il est dérivé automatiquement du même
fichier `pivot.md`.
