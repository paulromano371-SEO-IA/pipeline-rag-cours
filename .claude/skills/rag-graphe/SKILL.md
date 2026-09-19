---
name: rag-graphe
description: Résout les concepts extraits (produits par /rag-concepts) en entités canoniques et les relie dans le graphe de connaissances (Kuzu). Usage: /rag-graphe <pdf_condense_ou_document_id>. Septième et dernière étape du pipeline RAG.
---

# /rag-graphe — Étape 7 du pipeline RAG (GraphRAG)

Pour chaque mention de concept, compare par similarité d'embedding aux
concepts déjà connus du graphe partagé (tous documents confondus) :
- similarité >= 0.92 : fusion automatique (ajout d'alias), pas d'appel LLM.
- similarité < 0.80 : nouveau concept, pas d'appel LLM.
- entre les deux : arbitrage par un appel `claude -p` dédié (même/différent).

C'est cette étape qui permet de détecter qu'un même concept est traité dans
plusieurs documents sources différents — la valeur ajoutée du GraphRAG.

Une mention dont l'arbitrage `claude -p` échoue (timeout, réponse non-JSON...)
est ignorée et journalisée sur stderr, sans interrompre le traitement des
mentions suivantes — même principe que `/rag-concepts` pour un chunk en
échec. Le nombre de mentions ignorées est rapporté à la fin.

Le graphe Kuzu lui-même est déjà persisté au fil de l'eau (chaque concept/
lien est écrit dès sa création) ; un fichier `graphe_progress.json` dans le
dossier de travail du document retient en plus, après chaque chunk
entièrement traité, quels chunks ne plus retraiter — une interruption ne
perd jamais plus d'un chunk, et une reprise (relance sans `--force`) saute
les chunks déjà faits au lieu de ré-arbitrer inutilement des mentions déjà
liées. Ce fichier est supprimé automatiquement une fois l'étape terminée.

## Vider le graphe

`ConceptGraph.clear()` (`_rag_lib/graph_store.py`) supprime tous les nœuds
et relations sans supprimer le schéma ni le dossier de fichiers Kuzu — utile
après un changement du prompt d'extraction de concepts ou des seuils de
résolution d'entités, pour ne pas mélanger anciennes et nouvelles formes.
N'agit que sur le graphe : ne réinitialise pas le stage "graphe" du
`status.json` des documents qui y avaient déjà contribué, à faire séparément
si une reconstruction complète est voulue.

## Exécution

Toujours en foreground, bloquant jusqu'à complétion — jamais via
`run_in_background` ni aucun mécanisme async. L'arbitrage de résolution
d'entités appelle `claude -p` ; même règle que les autres étapes.

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-graphe/scripts/run.py" "<pdf_condense_ou_document_id_ou_dossier_de_travail>"
```

Options : `--force`, `--reset` (retire du graphe partagé la contribution de
CE document uniquement — ses concepts et liens ; un concept encore
mentionné par un AUTRE document est conservé, voir
`ConceptGraph.remove_document` — puis reconstruit entièrement. N'affecte
aucun autre document du corpus, contrairement à `ConceptGraph.clear()`),
`--batch-size N` (voir ci-dessous).

## Traitement par lots (documents volumineux)

Sur un livre à plusieurs centaines de chunks, une seule invocation dépasse la
durée maximale d'une commande foreground (10 minutes) et l'application la
passe en arrière-plan. Passe `--batch-size N` (ex. **30**, en chunks) dès le
premier appel :

- Chaque invocation traite au plus N chunks (résolution + liens, embedding
  limité aux mentions du lot), puis s'arrête avec le code de sortie **3**
  (« il reste du travail, ce n'est pas une erreur »). Une ligne
  `>>> Progression : X/Y chunk(s) traites` est affichée après chaque chunk.
- **Relance exactement la même commande, sans `--force` ni `--reset`** : la
  reprise s'appuie sur `graphe_progress.json`. Répète automatiquement, sans
  jamais demander confirmation entre deux lots, tant que le code est 3.
- `--force`/`--reset` uniquement au TOUT PREMIER lot, sinon chaque
  invocation repartirait de zéro.
- `status.json` est `done` uniquement après le dernier lot (contrôle de sortie
  compris). Entre deux lots il est marqué `failed` avec le détail « lot
  partiel en cours : N chunk(s) restant(s) » — jamais `done`.
- **Un lot où aucun chunk n'est mené à bout termine le traitement** (les
  chunks avec mention ignorée restent « à refaire », donc sans cette règle le
  code 3 bouclerait indéfiniment) : le contrôle de sortie tranche (code 2 si
  > 5 % de mentions ignorées).

## Prérequis

`rag_data/work/<document_id>/concepts.json` doit exister (produit par `/rag-concepts`).

## Sortie

Graphe Kuzu persisté dans `<racine_projet>/rag_data/db/graph/` (**base
UNIQUE, commune à tout le corpus**, jamais un graphe par livre). `status.json`
mis à jour — `"graphe": "done"` marque ce document comme entièrement traité
par le pipeline RAG.

## Après exécution

Rapporte le nombre de mentions résolues et liées.

**Contrôles** (`_rag_lib/checks.py`) :
- **Entrée** (code `1` si échec) : `concepts.json` valide et contenant au
  moins une mention, `/rag-concepts` terminé, CLI `claude` disponible.
- **Sortie bloquante** (code `2`, étape `failed`) : plus de **5 %** de
  mentions ignorées ; comptes incohérents (liées + ignorées différent du
  total de `concepts.json`) ; aucun lien ; nombre de chunks du document dans
  le graphe différent de l'attendu.
- **Reprise** : un chunk contenant au moins une mention ignorée n'est plus
  marqué « fait » dans `graphe_progress.json` (les compteurs sont tenus par
  chunk) ; après un verdict bloquant, le fichier de reprise est conservé et
  relancer la même commande ne retente que ces chunks. Un verdict `ok` avec
  quelques mentions ignorées (≤ 5 %) clôt l'étape : elles ne sont plus
  retentées ensuite.
- **Faux « déjà fait »** : un `done` dans `status.json` est vérifié contre le
  graphe ; s'il ne contient pas le document, il est reconstruit.

Si l'utilisateur le
demande, tu peux interroger le graphe directement (Python, module
`_rag_lib/graph_store.py`, fonction `ConceptGraph.shared_concepts(doc_a, doc_b)`)
pour montrer les concepts partagés entre deux documents — c'est la preuve
concrète que le pipeline relie plusieurs sources entre elles.
