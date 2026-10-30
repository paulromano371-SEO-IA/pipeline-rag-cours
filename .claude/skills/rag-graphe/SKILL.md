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
  pdfs n'est jamais fusionné automatiquement : le même mot peut y avoir un
  autre sens ("lambda" en Python / en régularisation), il va à l'arbitrage.
- similarité < 0.80 : nouveau concept, pas d'appel LLM.
- entre les deux (ou >= 0.92 vers un autre pdf) : arbitrage par un appel
  `claude -p` par candidat, sur les 3 concepts les plus proches (pas
  seulement le premier), jusqu'au premier « même concept ». L'arbitrage reçoit
  pour chaque côté le nom, la définition, le(s) pdf(s) et, pour la mention,
  un extrait du chunk.

C'est cette étape qui permet de détecter qu'un même concept est traité dans
plusieurs documents sources différents — la valeur ajoutée du GraphRAG.

Une mention dont l'arbitrage `claude -p` échoue (timeout, réponse non-JSON...)
est ignorée et journalisée sur stderr, sans interrompre le traitement des
mentions suivantes — même principe que `/rag-concepts` pour un chunk en
échec. Le nombre de mentions ignorées est rapporté à la fin.

Les écritures dans Kuzu se font pour tout le lot, une fois toutes les décisions
prises. Une interruption pendant un lot fait donc perdre ce lot, pas les lots
déjà terminés. Un fichier `graphe_progress.json` dans le dossier de travail du
document retient, après chaque chunk entièrement traité, quels chunks ne plus
retraiter ; une reprise (relance de la même commande) saute les chunks déjà
faits au lieu de ré-arbitrer inutilement des mentions déjà liées. Ce fichier
n'est jamais supprimé automatiquement : il est conservé à la fin de l'étape.

## Sans argument : lister les pdfs

Lance, en premier plan :

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-graphe/scripts/lib/lister_a_graphe.py"
```

Liste les pdfs de `corpuscondense/` prêts pour `/rag-graphe` :
`extraction_concepts` à `done` dans `status.json`, et `graphe` pas encore
`done`. Lecture seule.

Présente la liste dans la réponse, en Markdown numéroté, **hors de tout bloc de code**, avec les noms exacts, puis attends le choix de l'utilisateur :

- **Sans `--all`** : attends le choix de l'utilisateur :
  - **Numéro** : relance la liste pour avoir l'état à jour, puis prends le pdf portant ce numéro. Numéro absent : signale-le et redemande.
  - **Nom** : cherche le pdf dans `corpuscondense/` (insensible à la casse, correspondance exacte ou sous-chaîne unique). Zéro ou plusieurs correspondances : signale-le et redemande.
- **Avec `--all`** : traite les pdfs de la liste **un par un, dans l'ordre**. Annonce chaque pdf en une ligne (numéro, nom, `document_id`), puis, une fois la liste épuisée, affiche un tableau récapitulatif : une ligne par pdf, avec son verdict et son `document_id`. 
- Affiche un bouton All pour l'option --all

Le pdf choisi se traite ensuite avec le PDF `corpuscondense/<nom>.pdf` (ou son `document_id`) comme argument, selon « Un message de retour par lot » ci-dessous.

## Interpréteur Python

Tout script de ce skill se lance **uniquement** avec
`"<racine_projet>/.venv-rag/Scripts/python.exe"`, jamais avec `python`/`py`
(Python système). Techniquement imposé par un hook `PreToolUse` sur `Bash` et
`PowerShell` (`.claude/hooks/block_system_python.py`, voir
`.claude/settings.json`), qui refuse toute commande lançant un script de
`.claude/skills/*/scripts/` ou `tools/` avec `python`/`py`. Voir `/ragpipeline`,
section « Interpréteur Python ». `<racine_projet>` = le dossier contenant
`.claude/`.

## Exécution

Toujours en foreground, bloquant jusqu'à complétion — jamais via
`run_in_background` ni aucun mécanisme async, et jamais deux commandes du
pipeline en parallèle. Techniquement imposé par un hook `PreToolUse` sur `Bash`
(`.claude/hooks/block_rag_background.py`, voir `.claude/settings.json`), qui
refuse tout `run_in_background: true` sur une commande référençant un script
d'un skill du projet. Voir `/ragpipeline`, section « Pourquoi le foreground ».

**Interdiction de déléguer à un sous-agent** (outil `Agent`) la lecture ou la
vérification des sorties de ce skill : elle se fait directement, dans le même
tour de conversation, jamais « pour économiser du contexte ».

## Traitement par lots (documents volumineux)

Sur un pdf à plusieurs centaines de chunks, une seule invocation dépasse la
durée maximale d'une commande foreground (10 minutes) et l'application la
passe en arrière-plan. Passe `--batch-size N` (ex. **10**, en chunks) dès le
premier appel :

- Chaque invocation traite au plus N chunks (résolution + liens, embedding
  limité aux mentions du lot), puis s'arrête. La ligne `[LOT n/N]` signale qu'il
  reste du travail : ce n'est pas une erreur. Une ligne
  `>>> Progression : X/Y chunk(s) traites` est affichée après chaque chunk.
- **Relance exactement la même commande** : la reprise s'appuie sur `graphe_progress.json`. Répète automatiquement, sans jamais demander confirmation entre deux lots, tant que la ligne est `[LOT n/N]`.
- `status.json` est `done` uniquement après le dernier lot (contrôle de sortie
  compris). Entre deux lots il est marqué `failed` avec le détail « lot
  partiel en cours : N chunk(s) restant(s) » — jamais `done`.
- **Un lot où aucun chunk n'est mené à bout termine le traitement** (les
  chunks avec mention ignorée restent « à refaire », donc sans cette règle le
  lot partiel bouclerait indéfiniment) : le contrôle de sortie tranche (code 2
  si > 5 % de mentions ignorées).

## Un message de retour par lot (obligatoire)

**Lance chaque lot avec `scripts/etape1_lot.py`** (un lot par appel, jamais
plusieurs lots enchaînés dans une même commande : au-delà de 10 minutes
l'application la passerait en arrière-plan) :

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-graphe/scripts/etape1_lot.py" "<document_id_ou_pdf>" --batch-size 10
```

Le script termine sa sortie par UNE ligne de bilan : `[LOT n/N]` (lot partiel : relancer la même commande), `[FIN]` (étape terminée), `[BLOQUANT]` ou `[ERREUR]`. **C'est cette ligne, et non le code de sortie, qui dit s'il faut relancer** : le script sort en **0**
pour `[LOT n/N]` et `[FIN]` (un lot partiel n'est pas une erreur, et l'interface
n'a donc pas à l'afficher comme telle), en **2** pour `[BLOQUANT]` et en **1**
pour `[ERREUR]`.

**Après CHAQUE lot, avant de lancer le suivant, écris un message dans le
chat** qui contient : la sortie brute du script, collée dans un bloc de code
markdown, et sa ligne `[LOT n/N]` reprise telle quelle. Sans exception :
jamais deux lots enchaînés sans ce message entre les deux, jamais un résumé
condensé à la place de la sortie, y compris pour les lots intermédiaires. Un
pdf de 12 lots donne donc 12 messages. Cette obligation existe parce que
l'affichage de l'application replie le résultat d'une commande : sans ce
message, un lot terminé est indiscernable d'un lot encore en cours.

Sur `[BLOQUANT]` ou `[ERREUR]`, ne lance pas le lot suivant : rapporte la
cause à l'utilisateur.

## Consolidation du graphe (sous-étape suivante, lancée par le skill)

**Quand** : une fois le graphe du document terminé, c'est-à-dire quand
`etape1_lot.py` affiche `[FIN]` (jamais avant, jamais sur `[BLOQUANT]` ou
`[ERREUR]`). Le skill la lance **automatiquement, sans demander à l'utilisateur**,
juste après ce `[FIN]`, dans le même enchaînement. Elle porte sur le graphe PARTAGÉ (tout le
corpus), pas sur un document.

**À quoi ça sert** : rattraper les doublons que la résolution d'entités a
laissés passer (ex. `numpy` / `array numpy`). Toutes les paires de concepts à
similarité >= 0.80 sont jugées par `claude -p` (nom, type, définition, pdfs
et extrait de chunk des DEUX côtés) ; sur « même concept », le moins mentionné
est fusionné dans l'autre (`ConceptGraph.merge_concepts` : alias et liens
reportés).

**Un lot par appel, un message par lot** (mêmes règles que ci-dessus :
foreground, jamais plusieurs lots dans une commande, sortie brute collée dans
un bloc de code + ligne de bilan reprise telle quelle) :

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-graphe/scripts/etape2_consolidation.py" --batch-size 10
```

La commande se relance identique tant que la dernière ligne est
`[LOT n/N] consolidation ...` (`n/N` compte les lots de consolidation, pas ceux
du graphe). Elle se termine par `[FIN]`, `[BLOQUANT]` ou `[ERREUR]` ; code de
sortie 0 pour `[LOT]`/`[FIN]`, 2 pour `[BLOQUANT]` (intégrité du graphe :
n'enchaîne rien), 1 pour `[ERREUR]`. `--dry-run` (propre à ce script) liste les
paires à juger et les renommages prévus sans rien modifier.

**Règles** :

- **Mémoire des jugements** : chaque paire jugée (fusionnée OU distincte) est
  notée dans `rag_data/db/consolidation_decisions.json` avec les pdfs qui
  mentionnaient chaque concept. Une relance ne rejuge pas une paire tranchée,
  sauf une paire « distincte » dont l'un des concepts est mentionné depuis par
  un NOUVEAU pdf (nouveau contexte). Une décision d'un ancien format, sans
  cette mémoire, n'est jamais rejugée.
- **Garde sur le type** : deux concepts de types précis différents (ex. `method`
  et `tool`) ne sont jamais jugés ni fusionnés (comptés « ignorées ») ;
  `concept` et `other`, types génériques sur lesquels l'extraction hésite, sont
  compatibles avec tout. À la fusion, le type précis l'emporte.
- **Nom canonique** : au `[FIN]` de la consolidation, chaque concept prend pour
  forme canonique son nom LE PLUS FRÉQUENT dans le corpus (mentions des
  `concepts.json` liées à ce concept ; égalité : ordre alphabétique ; une forme
  de 3 caractères ou moins n'est jamais choisie tant qu'un alias plus long
  existe) : le nom ne dépend plus de l'ordre d'ingestion des pdfs. Alias et
  embedding ne changent pas.
- **Hors suivi** : la consolidation n'est pas tracée dans `status.json` et
  échappe aux « Contrôles » ci-dessous (qui portent sur le document) ;
  `"graphe": "done"` reste vrai même si elle échoue. Seul son propre `[FIN]`
  dit qu'elle est terminée.
- Elle ne touche ni aux chunks ni aux liens d'un pdf, au-delà du report des
  liens du concept supprimé vers le concept conservé.

Elle peut aussi se lancer seule (même commande), par exemple après avoir
ajouté plusieurs pdfs.

## Prérequis

`rag_data/work/<document_id>/concepts.json` doit exister (produit par `/rag-concepts`).

## Sortie

Graphe Kuzu persisté dans `<racine_projet>/rag_data/db/graph/` (**base
UNIQUE, commune à tout le corpus**, jamais un graphe par pdf). `status.json`
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

Si l'utilisateur le demande, tu peux interroger le graphe directement (Python, module
`_rag_lib/graph_store.py`, fonction `ConceptGraph.shared_concepts(doc_a, doc_b)`)
pour montrer les concepts partagés entre deux documents — c'est la preuve
concrète que le pipeline relie plusieurs sources entre elles.
