---
name: rag-index
description: >-
  Embedde et indexe les chunks (produits par /rag-chunking) dans la base
  vectorielle locale (Chroma).
  Usage: /rag-index <pdf_condense_ou_document_id>.
  Cinquieme etape du pipeline RAG.
---

# /rag-index — Etape 5 du pipeline RAG

Embedde chaque chunk (modele multilingue local via sentence-transformers,
aucune cle API) et l'indexe dans une base Chroma partagee entre tous les
documents du corpus.
Les chunks de bruit non textuel (motifs de hachures mal extraits d'un
diagramme) sont exclus — critere exact (`_rag_lib/quality.py`,
`is_noise_text`) : un chunk d'au moins 60 caracteres dont moins de 30% des
caracteres non-espace sont alphabetiques. Script deterministe, aucun appel
LLM.

## Execution

Toujours en foreground, bloquant jusqu'a completion — jamais via
`run_in_background` ni aucun mecanisme async. Contrairement a d'autres
etapes, ce script ne fait aucun appel `claude -p` : la contention entre
appels LLM imbriques n'est donc pas la raison ici. La vraie raison, verifiee
empiriquement : deux processus qui initialisent en meme temps un
`PersistentClient` Chroma sur un dossier de base **pas encore cree**
entrent en course sur la creation du schema SQLite (`InternalError: table
collections already exists`, puis un client corrompu dans le processus
perdant). Une fois la base deja initialisee par un premier run, des
invocations concurrentes sur des documents differents n'ont pas reproduit
l'erreur dans nos tests — mais rien ne garantit cette absence de risque a
plus grande echelle : n'execute jamais deux invocations de `/rag-index` en
parallele sur cette base partagee.

**Interdiction de deleguer a un sous-agent** (outil `Agent`) la lecture ou
verification du resultat d'indexation — meme raison que ci-dessus (eviter
toute execution concurrente sur la base partagee), pas une histoire de
contention `claude -p` puisqu'il n'y en a aucune ici.

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-index/scripts/run.py" "<pdf_condense_ou_document_id_ou_dossier_de_travail>"
```

Options : `--force`.

## Prerequis

`rag_data/work/<document_id>/chunks.json` doit exister (produit par `/rag-chunking`).

## Idempotence

Sans `--force` : si `status.json` contient deja
`{"indexation_vectorielle": {"status": "done"}}`, le script ne fait rien —
quel que soit l'etat reel de la base Chroma. Contrairement a
`/rag-chunking`/`/rag-extraction`, cette verification ne s'accompagne
d'aucun controle d'existence d'un fichier de sortie (il n'y en a pas, la
sortie vit dans la base partagee) : si la base Chroma est supprimee ou
corrompue mais que `status.json` dit encore `done`, le script sautera son
travail a tort, sans que rien ne le detecte.

Avec `--force` (ou lors d'un premier passage) : un re-import du meme
`document_id` **remplace** entierement ses chunks existants dans la base —
les anciennes entrees de ce document sont d'abord supprimees, puis les
nouvelles inserees, pour qu'un document reindexe avec moins de chunks
qu'auparavant ne laisse jamais d'entrees perimees orphelines (comportement
verifie : sans cette suppression prealable, un simple upsert ne retire
jamais les ids qui ne sont plus fournis).

## Sortie

Base vectorielle persistee dans `<racine_projet>/rag_data/db/vector/`
(**base UNIQUE, commune a tout le corpus** — chaque document y ajoute ses
chunks sans ecraser les autres, jamais une base par livre). Le `document_id`
stable (calcule a l'extraction, stocke dans `meta.json`) identifie ce
document dans la base. `status.json` mis a jour.

## Critère de sortie exploitable

Ni `/rag-concepts` ni `/rag-graphe` ne lisent la base vectorielle — verifie
dans leur code, ils ne dependent que de `chunks.json`/`concepts.json`.
L'indexation n'est donc le prerequis mecanique d'aucune etape suivante du
pipeline : elle est exploitable uniquement pour une recherche/retrieval
directe via `vector_store.search()`, au moment ou l'utilisateur interroge le
RAG — pas comme condition de blocage avant d'enchainer sur `/rag-concepts`.

## Apres execution

Affiche un resume structure : titre court ("Indexation terminee —
`<nom du document>`"), puis synthese chiffree (nombre de chunks indexes,
nombre de chunks ignores comme bruit, nom du modele d'embedding utilise —
`BAAI/bge-m3`). Ce script ne
produit aucun rapport de qualite type (contrairement a `/rag-extraction`) :
le seul signal — le nombre de chunks ignores comme bruit — est un filtrage
volontaire, pas un defaut ; ne fabrique pas de distinction
bloquant/indicatif qui ne correspondrait a rien de reel ici.

Le `document_id` affiche est reutilise tel quel par `/rag-graphe` — ne le
recalcule jamais manuellement, il est derive automatiquement du meme
fichier `pivot.md`.
