---
name: rag-concepts
description: Extrait les concepts candidats de chaque chunk (produit par /rag-chunking), prépare le GraphRAG. Usage: /rag-concepts <pdf_condense_ou_document_id>. Sixième étape du pipeline RAG.
---

# /rag-concepts — Étape 6 du pipeline RAG

Pour chaque chunk (hors bruit), un appel `claude -p` headless (sans outils ni
MCP, ne consomme pas le contexte de cette conversation) extrait 3-8 concepts
significatifs avec nom, forme canonique, type et **définition courte**
(`sense`, 5 à 12 mots : ce que le mot désigne DANS CE PASSAGE). La définition
sert à `/rag-graphe` à ne pas fusionner deux homonymes de livres différents
(ex. `lambda` = fonction Python ou paramètre de régularisation ; `biais`
statistique ou paramètre d'un réseau). Un chunk dont l'extraction échoue est
ignoré et journalisé, sans interrompre le traitement des autres.

**Un `concepts.json` extrait avant l'ajout de `sense` (ou dont le LLM a omis
le champ) reste valide mais dégrade `/rag-graphe`** (résolution sur le nom
seul) : à régénérer avec `--reset` au premier lot. Le contrôle avertit quand
plus de 10 % des mentions n'ont pas de définition.

`concepts.json` est réécrit après CHAQUE chunk traité avec succès (pas
seulement à la fin) : une interruption (Ctrl+C, crash, timeout) ne perd
jamais plus d'un chunk de progression. Une reprise (relance sans `--force`,
`status.json` pas encore "done") saute automatiquement les chunks déjà
présents dans le fichier ; un chunk qui avait échoué est naturellement
retenté (il n'est jamais enregistré).

## Exécution

Toujours en foreground, bloquant jusqu'à complétion — jamais via
`run_in_background` ni aucun mécanisme async. Ce script fait un appel
`claude -p` par chunk ; le lancer en arrière-plan ou en parallèle d'une autre
étape risque une contention entre appels imbriqués.

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-concepts/scripts/run.py" "<pdf_condense_ou_document_id_ou_dossier_de_travail>"
```

Options : `--force`, `--reset` (supprime `concepts.json` et l'entrée
`extraction_concepts` de `status.json` avant de régénérer entièrement —
utile après un changement du prompt d'extraction, pour ne pas mélanger
anciennes et nouvelles formes canoniques), `--batch-size N` (voir
ci-dessous).

`--reset` ne supprime rien tant que le contrôle d'entrée n'a pas réussi
(chunks valides et à jour, CLI `claude` disponible) : en cas d'échec de ce
contrôle, `concepts.json` et son statut restent intacts et le script sort avec
le code 1.

## Traitement par lots (documents volumineux)

Un appel `claude -p` par chunk : sur un livre de plusieurs centaines de
chunks, une seule invocation dépasse la durée maximale d'une commande
foreground (10 minutes) et l'application la passe alors en arrière-plan.
Passe `--batch-size N` (ex. **10**) dès le premier appel :

- Chaque invocation envoie au plus N chunks à `claude -p`, écrit leurs
  résultats dans `concepts.json`, puis s'arrête avec le code de sortie **3**
  (« il reste du travail, ce n'est pas une erreur »). Une ligne
  `>>> Progression : X/Y chunk(s) traites` est affichée après chaque chunk.
- **Relance exactement la même commande, sans `--force` ni `--reset`** : la
  reprise retrouve les chunks déjà faits dans `concepts.json`. Répète
  automatiquement, sans jamais demander confirmation entre deux lots, tant
  que le code est 3.
- `--force`/`--reset` uniquement au TOUT PREMIER lot : les repasser ensuite
  repartirait de zéro à chaque invocation.
- `status.json` est `done` uniquement après le dernier lot (contrôle de sortie
  compris). Entre deux lots il est marqué `failed` avec le détail « lot
  partiel en cours : N chunk(s) restant(s) » — jamais `done`, pour qu'une
  relance sans `--force` ne croie pas l'étape terminée.
- **Un lot où aucun chunk n'aboutit termine le traitement** (le contrôle de
  sortie tranche : code 2 si > 5 % de chunks sans concepts) : sans cette
  règle, un chunk qui échoue à chaque tentative ferait boucler le code 3
  indéfiniment.

## Un message de retour par lot (obligatoire)

**Lance chaque lot avec `scripts/concepts_lot.py`** (un lot par appel, jamais
plusieurs lots enchaînés dans une même commande : au-delà de 10 minutes
l'application la passerait en arrière-plan) :

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-concepts/scripts/concepts_lot.py" "<document_id_ou_pdf>" --batch-size 10 [--reset]
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
livre de plusieurs lots donne donc autant de messages. Cette obligation
existe parce que l'affichage de l'application replie le résultat d'une
commande : sans ce message, un lot terminé est indiscernable d'un lot encore
en cours.

Sur `[BLOQUANT]` ou `[ERREUR]`, ne lance pas le lot suivant : rapporte la
cause à l'utilisateur.

## Prérequis

`rag_data/work/<document_id>/chunks.json` doit exister (produit par `/rag-chunking`).

## Coût / durée

Un appel `claude -p` par chunk indexable — pour un document de plusieurs
dizaines de chunks, prévoir plusieurs dizaines de secondes à quelques
minutes. Chaque appel est restreint (`--tools ""`), donc peu coûteux
individuellement, mais le volume total croît avec la taille du document.

## Sortie

`rag_data/work/<document_id>/concepts.json` — liste
`{"chunk_index": N, "mentions": [...]}` + `status.json` mis à jour.

## Après exécution

Rapporte le nombre total de mentions extraites et le nombre de chunks
ignorés.

**Contrôles** (`_rag_lib/checks.py`) :
- **Entrée** (code `1` si échec) : `chunks.json` valide, `/rag-chunking`
  terminé, CLI `claude` disponible dans le PATH.
- **Sortie bloquante** (code `2`, étape `failed`, **arrête-toi avant
  `/rag-graphe`**) : plus de **5 %** des chunks indexables (hors bruit) sans
  concepts, ou aucun concept extrait. Le taux est recalculé à partir de
  `concepts.json` (chunks attendus moins chunks présents), donc exact même
  après une reprise ; `n_noise` et `n_failed` sont enregistrés séparément
  dans `status.json` (`chunks_ignores` les additionne, conservé pour
  compatibilité). Relancer la même commande retente les chunks en échec.
- **Avertissement, non bloquant** (dans la sortie du contrôle, et au contrôle
  d'entrée de `/rag-graphe`) : plus de **10 %** des mentions sans `sense`.
