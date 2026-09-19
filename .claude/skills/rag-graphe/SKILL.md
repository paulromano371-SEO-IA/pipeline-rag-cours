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

Une mention dont l'arbitrage `claude -p` echoue (timeout, reponse non-JSON...)
est ignoree et journalisee sur stderr, sans interrompre le traitement des
mentions suivantes — meme principe que `/rag-concepts` pour un chunk en
echec. Le nombre de mentions ignorees est rapporte a la fin.

Le graphe Kuzu lui-meme est deja persiste au fil de l'eau (chaque concept/
lien est ecrit des sa creation) ; un fichier `graphe_progress.json` dans le
dossier de travail du document retient en plus, apres chaque chunk
entierement traite, quels chunks ne plus retraiter — une interruption ne
perd jamais plus d'un chunk, et une reprise (relance sans `--force`) saute
les chunks deja faits au lieu de re-arbitrer inutilement des mentions deja
liees. Ce fichier est supprime automatiquement une fois l'etape terminee.

## Vider le graphe

`ConceptGraph.clear()` (`_rag_lib/graph_store.py`) supprime tous les noeuds
et relations sans supprimer le schema ni le dossier de fichiers Kuzu — utile
apres un changement du prompt d'extraction de concepts ou des seuils de
resolution d'entites, pour ne pas melanger anciennes et nouvelles formes.
N'agit que sur le graphe : ne reinitialise pas le stage "graphe" du
`status.json` des documents qui y avaient deja contribue, a faire separement
si une reconstruction complete est voulue.

## Execution

Toujours en foreground, bloquant jusqu'a completion — jamais via
`run_in_background` ni aucun mecanisme async. L'arbitrage de resolution
d'entites appelle `claude -p` ; meme regle que les autres etapes.

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-graphe/scripts/run.py" "<pdf_condense_ou_document_id_ou_dossier_de_travail>"
```

Options : `--force`, `--reset` (retire du graphe partage la contribution de
CE document uniquement — ses concepts et liens ; un concept encore
mentionne par un AUTRE document est conserve, voir
`ConceptGraph.remove_document` — puis reconstruit entierement. N'affecte
aucun autre document du corpus, contrairement a `ConceptGraph.clear()`).

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
