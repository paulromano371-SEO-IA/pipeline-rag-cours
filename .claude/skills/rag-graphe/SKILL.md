---
name: rag-graphe
description: Résout les concepts extraits (produits par /rag-concepts) en entités canoniques et les relie dans le graphe de connaissances (Kuzu). Usage: /rag-graphe <pdf_condense_ou_document_id>. Septième et dernière étape du pipeline RAG.
---

# /rag-graphe — Étape 7 du pipeline RAG (GraphRAG)

Pour chaque mention de concept, compare par similarité d'embedding (nom +
définition courte `sense`, produite par `/rag-concepts`) aux concepts déjà
connus du graphe partagé (tous documents confondus) :
- similarité >= 0.92 ET concept déjà vu dans CE document : fusion automatique
  (ajout d'alias), pas d'appel LLM. Un concept vu seulement dans d'AUTRES
  livres n'est jamais fusionné automatiquement : le même mot peut y avoir un
  autre sens ("lambda" en Python / en régularisation), il va à l'arbitrage.
- similarité < 0.80 : nouveau concept, pas d'appel LLM.
- entre les deux (ou >= 0.92 vers un autre livre) : arbitrage par un appel
  `claude -p` par candidat, sur les 3 concepts les plus proches (pas
  seulement le premier), jusqu'au premier « même concept ». L'arbitrage reçoit
  pour chaque côté le nom, la définition, le(s) livre(s) et, pour la mention,
  un extrait du chunk.

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

`--clear-graph` (**destructif pour tout le corpus**) vide le graphe partagé
(concepts, chunks et liens de TOUS les documents, alias compris), remet à zéro
l'étape `graphe` de chaque document (`status.json` et `graphe_progress.json`)
puis **s'arrête : rien n'est reconstruit**. Chaque document doit ensuite être
reconstruit à la main (`/rag-graphe <document> --reset --batch-size 10`). C'est
la seule façon de supprimer les alias résiduels qu'un `--reset` laisse sur les
concepts partagés avec un autre livre. Le script annonce d'abord l'impact
(nombre de concepts, liens, chunks par document, documents dont le statut est
remis à zéro, documents sans dossier de travail donc non reconstructibles) ;
`--dry-run` l'affiche **sans rien modifier**. Comme `--reset`, il exige un
`concepts.json` valide pour le document donné en argument (on ne vide pas ce
que l'on ne peut pas refaire). **Jamais lancé de ta propre initiative** :
uniquement si l'utilisateur l'a écrit dans sa commande en cours (directement,
ou via `/ragpipeline --clear-graph`).

`--reset` ne retire rien du graphe tant que le contrôle d'entrée n'a pas
réussi (`concepts.json` valide, `/rag-concepts` terminé, CLI `claude`
disponible) : en cas d'échec, la contribution du document au graphe reste
intacte et le script sort avec le code 1. `remove_document` supprime les
nœuds et les liens de ce document, mais pas les alias qu'il avait ajoutés à
un concept encore mentionné par un autre livre.

## Traitement par lots (documents volumineux)

Sur un livre à plusieurs centaines de chunks, une seule invocation dépasse la
durée maximale d'une commande foreground (10 minutes) et l'application la
passe en arrière-plan. Passe `--batch-size N` (ex. **10**, en chunks) dès le
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

## Un message de retour par lot (obligatoire)

**Lance chaque lot avec `scripts/graphe_lot.py`** (un lot par appel, jamais
plusieurs lots enchaînés dans une même commande : au-delà de 10 minutes
l'application la passerait en arrière-plan) :

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-graphe/scripts/graphe_lot.py" "<document_id_ou_pdf>" --batch-size 10 [--reset]
```

`--reset` uniquement sur le tout premier lot. Le script termine sa sortie par
UNE ligne de bilan : `[LOT n/N]` (lot partiel : relancer la même commande),
`[FIN]` (étape terminée), `[BLOQUANT]` ou `[ERREUR]`. **C'est cette ligne, et
non le code de sortie, qui dit s'il faut relancer** : le script sort en **0**
pour `[LOT n/N]` et `[FIN]` (un lot partiel n'est pas une erreur, et l'interface
n'a donc pas à l'afficher comme telle), en **2** pour `[BLOQUANT]` et en **1**
pour `[ERREUR]`. Seul `run.py` (appelé directement, sans ce script) renvoie 3 sur
un lot partiel — convention du pipeline, inchangée.

**Après CHAQUE lot, avant de lancer le suivant, écris un message dans le
chat** qui contient : la sortie brute du script, collée dans un bloc de code
markdown, et sa ligne `[LOT n/N]` reprise telle quelle. Sans exception :
jamais deux lots enchaînés sans ce message entre les deux, jamais un résumé
condensé à la place de la sortie, y compris pour les lots intermédiaires. Un
livre de 12 lots donne donc 12 messages. Cette obligation existe parce que
l'affichage de l'application replie le résultat d'une commande : sans ce
message, un lot terminé est indiscernable d'un lot encore en cours.

Sur `[BLOQUANT]` ou `[ERREUR]`, ne lance pas le lot suivant : rapporte la
cause à l'utilisateur.

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
