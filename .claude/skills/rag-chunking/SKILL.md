---
name: rag-chunking
description: >-
  Découpe un pivot markdown (produit par /rag-extraction et enrichi par
  /rag-nottext) en chunks embeddables, sans jamais couper un bloc de code,
  une image ou une formule.
  Usage: /rag-chunking <pdf_condense_ou_document_id>.
  Quatrième étape du pipeline RAG.
---

# /rag-chunking — Étape 4 du pipeline RAG

Découpe le markdown pivot en chunks (~400 tokens cible, recouvrement d'un
bloc) prêts à être embeddés. Script déterministe, aucun appel LLM. Le
comptage de tokens utilise le tokenizer réel du modèle d'embedding
(`BAAI/bge-m3`, voir `_rag_lib/chunk.py`) — jamais un proxy générique type
tiktoken, dont le décompte n'a aucun rapport avec la vraie limite du modèle
qui embeddera ensuite ces chunks (`/rag-index`).

Un bloc code/image/formule directement suivi de la description générée par
`/rag-nottext` (marqueur `DESCRIPTION_MARKER`, voir `_rag_lib/chunk.py`) est
fusionné avec elle en une seule unité atomique : le texte du chunk
(`text`, utilisé pour la citation) garde le verbatim + la description, mais
le texte réellement embeddé (`embed_text`, utilisé par `/rag-index`) ne
retient QUE la description — jamais le LaTeX/code/légende brut, dont on a
vérifié empiriquement qu'il dilue la similarité d'embedding avec une
question en français au lieu de la restaurer. Un bloc pas encore traité par
`/rag-nottext` (ou dont le traitement a échoué) n'a pas de description à
fusionner : `embed_text` retombe alors sur le texte brut du bloc.

## Exécution

Toujours en foreground, bloquant jusqu'à complétion — jamais via
`run_in_background` ni aucun mécanisme async. Contrairement à d'autres
étapes du pipeline, ce script ne fait aucun appel `claude -p` : la
contention entre appels imbriqués n'est donc pas la raison ici. La vraie
raison : deux invocations en parallèle sur le même `document_id` écriraient
concurremment `chunks.json`/`status.json`, avec un risque de corruption ou
d'écrasement partiel — jamais deux commandes du pipeline en parallèle sur le
même document.

**Interdiction de déléguer à un sous-agent** (outil `Agent`) la lecture ou la
vérification de `chunks.json` — pas pour éviter une contention `claude -p`
(inexistante ici), mais pour que le compte-rendu structuré ci-dessous (en
particulier la distinction bloquant/indicatif) soit produit par la même
instance qui vient d'exécuter le script, dans le même tour de conversation,
plutôt que redécidé indépendamment par un sous-agent.

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-chunking/scripts/run.py" "<pdf_condense_ou_document_id_ou_dossier_de_travail>"
```

Le script accepte indifféremment : le même chemin de PDF condensé passé à
`/rag-extraction`, le `document_id` qu'il a affiché, ou directement le
dossier `rag_data/work/<document_id>/`.

Options :
- `--force`
- `--target-tokens N` (défaut 400)
- `--overlap-blocks N` (défaut 1)
- `--keep-structural` : conserve les sections structurelles au lieu de les
  exclure (voir ci-dessous) — à réserver à un livre dont une section de ce
  nom porte du vrai contenu ; le choix est enregistré dans `status.json` et
  respecté par les contrôles d'entrée de `/rag-index` et `/rag-concepts`.

## Sections structurelles exclues

Table des matières, sommaire, index, bibliographie, annexe(s), glossaire,
références, remerciements (liste unique `_rag_lib/structural.py`, partagée
avec `retrieval.py`) : reconnues sur le **titre** de la section (accents et
casse ignorés) — le titre du gabarit figé de `/cours-condense`, « Table des
matières », en fait partie. `chunk.py:drop_structural_sections` retire le
titre et tout ce qui suit jusqu'au prochain titre de niveau égal ou
supérieur, **avant** le découpage : aucun chunk n'est produit, donc rien
n'est embeddé, indexé, envoyé à l'extraction de concepts ni relié au graphe.
`pivot.md` n'est pas modifié (fidélité au PDF condensé).

Pourquoi avant le découpage et non sur le fil d'ariane des chunks : le
découpage glouton fusionne la fin de la table des matières avec le début de
la section suivante (constaté : un chunk mêlant la fin de la TOC, un numéro
de page, le titre du chapitre 1 et son texte), que ce filtre n'aurait pas vu.

Mesuré sur les deux premiers livres : ISLR 227 → 211 chunks, Bratanic
136 → 117 ; aucun bloc code/image/formule perdu. Le script annonce les
sections exclues (`Sections structurelles exclues ...`) et les enregistre
dans `status.json` (`structural_sections_excluded`,
`structural_blocks_excluded`).

## Prérequis

`rag_data/work/<document_id>/pivot.md` doit exister (produit par
`/rag-extraction`). Si absent, le script échoue explicitement — relance
`/rag-extraction` d'abord.

## Idempotence

Sans `--force` : si `status.json` contient déjà `{"chunking": {"status": "done"}}`
et que `chunks.json` existe, le script ne fait rien — il affiche
`deja fait (chunking): <chemin>` et s'arrête (code 0). Avec `--force` :
`chunks.json` est entièrement réécrit en un seul `write_text` (jamais un
ajout ni une mutation incrémentale) — un `--force` répété ne peut donc
jamais accumuler ou dupliquer du contenu d'un run à l'autre.

## Sortie

`rag_data/work/<document_id>/chunks.json` — liste de chunks (index, texte,
texte embeddé, fil d'ariane des titres, nombre de tokens, présence de code)
+ `status.json` mis à jour. Toujours dans le dossier de travail centralisé,
jamais à côté du PDF source.

## Critère de sortie exploitable

`/rag-index` et `/rag-concepts` ne vérifient, dans leur code, que
l'existence de `chunks.json` — ni l'un ni l'autre ne relit `status.json`
pour confirmer que l'étape `chunking` est marquée `done`. Le chunking est
donc exploitable pour la suite dès que `chunks.json` existe ; `status.json`
ne sert qu'au suivi interne et à l'idempotence de cette étape elle-même. Les
deux restent synchronisés en pratique ici (`chunks.json` est écrit juste
avant le marquage `done`, jamais après un échec — voir Idempotence
ci-dessus), mais ne suppose pas cette synchronisation pour d'autres étapes
sans l'avoir vérifiée dans leur code.

## Blocs atomiques et recouvrement

Un bloc atomique (code, image, formule) qui dépasse `--target-tokens` à lui
seul n'est jamais scindé — il reste entier dans son propre chunk, même si
celui-ci dépasse la cible. Seul un bloc de prose peut être scindé, sur des
frontières de phrase.

`--overlap-blocks N` reporte les N derniers blocs du chunk qui vient d'être
découpé vers le chunk suivant — **sauf les titres, toujours exclus du
report** : si le dernier bloc d'un chunk est un titre, il n'est pas reporté,
et `--overlap-blocks 1` peut donc ne reporter aucun bloc dans ce cas précis.

## Après exécution

Affiche un résumé structuré, dans cet ordre :
1. un titre court ("Chunking terminé — `<nom du document>`")
2. une phrase de synthèse chiffrée : nombre de chunks produits, nombre moyen
   de tokens par chunk, nombre de chunks contenant du code, proportion de
   chunks dont le fil d'ariane des titres est vide

**Contrôles d'entrée** (`_rag_lib/checks.py`, code de sortie `1` si échec) :
`pivot.md` non vide, `/rag-extraction` sans bloquant, `/rag-nottext` terminé
sans échec, et **aucun bloc code/image/formule sans description**
(`DESCRIPTION_MARKER`) — leur texte brut dégraderait l'embedding.

**Distinction bloquant/indicatif, fixée ici :**
- **bloquant** (code de sortie `2`, étape marquée `failed`, verdict écrit
  dans `status.json`) : `chunk_markdown` produit une liste vide, `chunks.json`
  invalide (forme, index dupliqués), `n_chunks` différent du contenu du
  fichier, bloc de code coupé/non refermé dans un chunk, ou chunk portant
  le fil d'ariane d'une section structurelle (sauf `--keep-structural`).
  Indicatif : chunk à points de conduite « . . . . » hors section
  reconnue (table des matières probable sous un titre non reconnu). Dans ce cas,
  **arrête-toi et ne propose jamais d'enchaîner sur `/rag-index`**.
- **indicatif** : fil d'ariane vide pour la plupart des chunks — signale-le,
  mais sans bloquer la suite (l'extraction n'a probablement pas détecté les
  titres sur ce document) : la recherche fonctionne quand même, juste avec
  moins de contexte.
