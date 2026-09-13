---
name: rag-graphe
description: Resout les concepts extraits (produits par /rag-concepts) en entites canoniques et les relie dans le graphe de connaissances (Kuzu). Usage: /rag-graphe <pdf_condense_ou_document_id>. Septieme et derniere etape du pipeline RAG.
---

# /rag-graphe — Etape 7 du pipeline RAG (GraphRAG)

Pour chaque mention de concept, compare par similarite d'embedding aux
concepts deja connus du graphe partage (tous documents confondus) :
- similarite >= 0.92 : fusion automatique (ajout d'alias), pas d'appel LLM.
- similarite < 0.80 : nouveau concept, pas d'appel LLM.
- entre les deux : arbitrage par un appel `claude -p` dedie (meme/different).

C'est cette etape qui permet de detecter qu'un meme concept est traite dans
plusieurs documents sources differents — la valeur ajoutee du GraphRAG.

## Execution

Toujours en foreground, bloquant jusqu'a completion — jamais via
`run_in_background` ni aucun mecanisme async. L'arbitrage de resolution
d'entites appelle `claude -p` ; meme regle que les autres etapes.

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-graphe/scripts/run.py" "<pdf_condense_ou_document_id_ou_dossier_de_travail>"
```

Options : `--force`.

## Prerequis

`rag_data/work/<document_id>/concepts.json` doit exister (produit par `/rag-concepts`).

## Sortie

Graphe Kuzu persiste dans `<racine_projet>/rag_data/db/graph/` (**base
UNIQUE, commune a tout le corpus**, jamais un graphe par livre). `status.json`
mis a jour — `"graphe": "done"` marque ce document comme entierement traite
par le pipeline RAG.

## Apres execution

Rapporte le nombre de mentions resolues et liees. Si l'utilisateur le
demande, tu peux interroger le graphe directement (Python, module
`_rag_lib/graph_store.py`, fonction `ConceptGraph.shared_concepts(doc_a, doc_b)`)
pour montrer les concepts partages entre deux documents — c'est la preuve
concrete que le pipeline relie plusieurs sources entre elles.
