---
name: rag-chunking
description: >-
  Decoupe un pivot markdown (produit par /rag-extraction) en chunks
  embeddables, sans jamais couper un bloc de code ou une image.
  Usage: /rag-chunking <pdf_condense_ou_document_id>.
  Quatrieme etape du pipeline RAG.
---

# /rag-chunking — Etape 4 du pipeline RAG

Decoupe le markdown pivot en chunks (~400 tokens cible, recouvrement d'un
bloc) prets a etre embeddes. Script deterministe (tiktoken), aucun appel LLM.

## Execution

Toujours en foreground, bloquant jusqu'a completion — jamais via
`run_in_background` ni aucun mecanisme async. Contrairement a d'autres
etapes du pipeline, ce script ne fait aucun appel `claude -p` : la
contention entre appels imbriques n'est donc pas la raison ici. La vraie
raison : deux invocations en parallele sur le meme `document_id` ecriraient
concurremment `chunks.json`/`status.json`, avec un risque de corruption ou
d'ecrasement partiel — jamais deux commandes du pipeline en parallele sur le
meme document.

**Interdiction de deleguer a un sous-agent** (outil `Agent`) la lecture ou la
verification de `chunks.json` — pas pour eviter une contention `claude -p`
(inexistante ici), mais pour que le compte-rendu structure ci-dessous (en
particulier la distinction bloquant/indicatif) soit produit par la meme
instance qui vient d'executer le script, dans le meme tour de conversation,
plutot que redecide independamment par un sous-agent.

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-chunking/scripts/run.py" "<pdf_condense_ou_document_id_ou_dossier_de_travail>"
```

Le script accepte indifferemment : le meme chemin de PDF condense passe a
`/rag-extraction`, le `document_id` qu'il a affiche, ou directement le
dossier `rag_data/work/<document_id>/`.

Options :
- `--force`
- `--target-tokens N` (defaut 400)
- `--overlap-blocks N` (defaut 1)

## Prerequis

`rag_data/work/<document_id>/pivot.md` doit exister (produit par
`/rag-extraction`). Si absent, le script echoue explicitement — relance
`/rag-extraction` d'abord.

## Idempotence

Sans `--force` : si `status.json` contient deja `{"chunking": {"status": "done"}}`
et que `chunks.json` existe, le script ne fait rien — il affiche
`deja fait (chunking): <chemin>` et s'arrete (code 0). Avec `--force` :
`chunks.json` est entierement reecrit en un seul `write_text` (jamais un
ajout ni une mutation incrementale) — un `--force` repete ne peut donc
jamais accumuler ou dupliquer du contenu d'un run a l'autre.

## Sortie

`rag_data/work/<document_id>/chunks.json` — liste de chunks (index, texte,
fil d'ariane des titres, nombre de tokens, presence de code) + `status.json`
mis a jour. Toujours dans le dossier de travail centralise, jamais a cote du
PDF source.

## Critère de sortie exploitable

`/rag-index` et `/rag-concepts` ne verifient, dans leur code, que
l'existence de `chunks.json` — ni l'un ni l'autre ne relit `status.json`
pour confirmer que l'etape `chunking` est marquee `done`. Le chunking est
donc exploitable pour la suite des que `chunks.json` existe ; `status.json`
ne sert qu'au suivi interne et a l'idempotence de cette etape elle-meme. Les
deux restent synchronises en pratique ici (`chunks.json` est ecrit juste
avant le marquage `done`, jamais apres un echec — voir Idempotence
ci-dessus), mais ne suppose pas cette synchronisation pour d'autres etapes
sans l'avoir verifiee dans leur code.

## Blocs atomiques et recouvrement

Un bloc atomique (code, image) qui depasse `--target-tokens` a lui seul
n'est jamais scinde — il reste entier dans son propre chunk, meme si celui-ci
depasse la cible. Seul un bloc de prose peut etre scinde, sur des frontieres
de phrase.

`--overlap-blocks N` reporte les N derniers blocs du chunk qui vient d'etre
decoupe vers le chunk suivant — **sauf les titres, toujours exclus du
report** : si le dernier bloc d'un chunk est un titre, il n'est pas reporte,
et `--overlap-blocks 1` peut donc ne reporter aucun bloc dans ce cas precis.

## Apres execution

Affiche un resume structure, dans cet ordre :
1. un titre court ("Chunking termine — `<nom du document>`")
2. une phrase de synthese chiffree : nombre de chunks produits, nombre moyen
   de tokens par chunk, nombre de chunks contenant du code, proportion de
   chunks dont le fil d'ariane des titres est vide

**Distinction bloquant/indicatif, fixee ici :**
- **bloquant** : `chunk_markdown` produit une liste vide — le script marque
  deja l'etape `failed` dans `status.json` et retourne un code d'erreur non
  nul. Dans ce cas, **arrete-toi et ne propose jamais d'enchainer sur
  `/rag-index`**.
- **indicatif** : fil d'ariane vide pour la plupart des chunks — signale-le,
  mais sans bloquer la suite (l'extraction n'a probablement pas detecte les
  titres sur ce document) : la recherche fonctionne quand meme, juste avec
  moins de contexte.
