---
name: rag-concepts
description: Extrait les concepts candidats de chaque chunk (produit par /rag-chunking), prépare le GraphRAG. Usage: /rag-concepts <pdf_condense_ou_document_id>. Sixième étape du pipeline RAG.
---

# /rag-concepts — Étape 6 du pipeline RAG

Pour chaque chunk (hors bruit), un appel `claude -p` headless (sans outils ni
MCP, ne consomme pas le contexte de cette conversation) extrait 3-8 concepts
significatifs avec nom, forme canonique et type. Un chunk dont l'extraction
échoue est ignoré et journalisé, sans interrompre le traitement des autres.

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

## Traitement par lots (documents volumineux)

Un appel `claude -p` par chunk : sur un livre de plusieurs centaines de
chunks, une seule invocation dépasse la durée maximale d'une commande
foreground (10 minutes) et l'application la passe alors en arrière-plan.
Passe `--batch-size N` (ex. **40**) dès le premier appel :

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
