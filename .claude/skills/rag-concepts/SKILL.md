---
name: rag-concepts
description: Extrait les concepts candidats de chaque chunk (produit par /rag-chunking), prepare le GraphRAG. Usage: /rag-concepts <pdf_condense_ou_document_id>. Sixieme etape du pipeline RAG.
---

# /rag-concepts — Etape 6 du pipeline RAG

Pour chaque chunk (hors bruit), un appel `claude -p` headless (sans outils ni
MCP, ne consomme pas le contexte de cette conversation) extrait 3-8 concepts
significatifs avec nom, forme canonique et type. Un chunk dont l'extraction
echoue est ignore et journalise, sans interrompre le traitement des autres.

`concepts.json` est reecrit apres CHAQUE chunk traite avec succes (pas
seulement a la fin) : une interruption (Ctrl+C, crash, timeout) ne perd
jamais plus d'un chunk de progression. Une reprise (relance sans `--force`,
`status.json` pas encore "done") saute automatiquement les chunks deja
presents dans le fichier ; un chunk qui avait echoue est naturellement
retente (il n'est jamais enregistre).

## Execution

Toujours en foreground, bloquant jusqu'a completion — jamais via
`run_in_background` ni aucun mecanisme async. Ce script fait un appel
`claude -p` par chunk ; le lancer en arriere-plan ou en parallele d'une autre
etape risque une contention entre appels imbriques.

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-concepts/scripts/run.py" "<pdf_condense_ou_document_id_ou_dossier_de_travail>"
```

Options : `--force`, `--reset` (supprime `concepts.json` et l'entree
`extraction_concepts` de `status.json` avant de regenerer entierement —
utile apres un changement du prompt d'extraction, pour ne pas melanger
anciennes et nouvelles formes canoniques).

## Prerequis

`rag_data/work/<document_id>/chunks.json` doit exister (produit par `/rag-chunking`).

## Cout / duree

Un appel `claude -p` par chunk indexable — pour un document de plusieurs
dizaines de chunks, prevoir plusieurs dizaines de secondes a quelques
minutes. Chaque appel est restreint (`--tools ""`), donc peu couteux
individuellement, mais le volume total croit avec la taille du document.

## Sortie

`rag_data/work/<document_id>/concepts.json` — liste
`{"chunk_index": N, "mentions": [...]}` + `status.json` mis a jour.

## Apres execution

Rapporte le nombre total de mentions extraites et le nombre de chunks
ignores. Si le taux d'echec est eleve (plusieurs chunks ignores sur peu de
chunks), alerte l'utilisateur avant d'enchainer sur `/rag-graphe` — un
graphe construit sur une extraction tres partielle a moins de valeur.
