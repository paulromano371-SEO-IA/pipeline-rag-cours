# /rag-graphe : résolution des concepts et graphe de connaissances

Ce document explique la commande `/rag-graphe` : ce qu'elle fait, comment l'utiliser, et à quoi sert chaque fichier.

Le fichier `SKILL.md`, à côté de celui-ci, est la consigne lue par Claude Code. Ce README est la documentation pour un humain. En cas de désaccord, `SKILL.md` fait foi.

---

## 1. Ce que fait la commande

`/rag-graphe` prend les concepts extraits par `/rag-concepts` et les **résout** en entités canoniques. Il les relie ensuite dans un **graphe de connaissances** (Kuzu).

Pour chaque mention de concept, la commande compare, par similarité d'embedding, le nom et la définition courte (`sense`) aux concepts déjà connus du graphe partagé, tous documents confondus.

C'est la **septième et dernière étape du pipeline RAG**. Elle permet de repérer qu'un même concept est traité dans plusieurs documents sources : c'est la valeur ajoutée du GraphRAG.

Le graphe est **unique et commun à tout le corpus**, jamais un graphe par pdf.

---

## 2. Règles de résolution

| Similarité | Cas | Traitement |
| --- | --- | --- |
| **≥ 0,92**, et concept déjà vu dans **ce** document | Fusion automatique | Ajout d'un alias. Pas d'appel LLM. |
| **< 0,80** | Nouveau concept | Pas d'appel LLM. |
| Entre les deux, ou **≥ 0,92 vers un autre pdf** | Arbitrage | Un appel `claude -p` par candidat. |

Un concept vu **seulement dans d'autres pdfs** n'est jamais fusionné automatiquement. Le même mot peut y avoir un autre sens : « lambda » en Python, ou en régularisation. Il va donc à l'arbitrage.

L'arbitrage porte sur les **3 concepts les plus proches**, et non sur le premier seulement. Il s'arrête au premier « même concept ». Pour chaque côté, il reçoit le nom, la définition, le ou les pdfs, et, pour la mention, un extrait du chunk.

Une mention dont l'arbitrage échoue (timeout, réponse non JSON...) est **ignorée et journalisée** sur stderr. Le traitement continue avec les mentions suivantes. Le nombre de mentions ignorées est rapporté à la fin.

---

## 3. Utilisation

### Lister les pdfs à traiter

Sans argument, la commande liste les pdfs de `corpuscondense/` prêts pour `/rag-graphe` : extraction des concepts à `done`, graphe pas encore `done`. C'est une lecture seule.

```bash
".venv-rag/Scripts/python.exe" ".claude/skills/rag-graphe/scripts/lib/lister_a_graphe.py"
```

La liste est présentée en Markdown numéroté, puis Claude attend votre choix :

- **Un numéro** : le pdf correspondant est traité.
- **Un nom** : Claude cherche le pdf dans `corpuscondense/` (insensible à la casse, correspondance exacte ou sous-chaîne unique).
- **`--all`** : tous les pdfs de la liste sont traités **un par un, dans l'ordre**. Un tableau récapitulatif, avec le verdict et le `document_id` de chaque pdf, est affiché à la fin.

### Prérequis

`rag_data/work/<document_id>/concepts.json` doit exister, produit par `/rag-concepts`.

### Interpréteur Python

Tous les scripts se lancent **uniquement** avec `.venv-rag/Scripts/python.exe`, jamais avec `python` ou `py`. Un hook `PreToolUse` (`.claude/hooks/block_system_python.py`) refuse les autres appels. `<racine_projet>` désigne le dossier qui contient `.claude/`.

### Exécution

Les commandes tournent **toujours en premier plan**, de façon bloquante. Un hook (`.claude/hooks/block_rag_background.py`) refuse tout `run_in_background` sur ces scripts. Jamais deux commandes du pipeline en parallèle.

Claude ne délègue pas à un sous-agent la lecture ou la vérification des sorties de ce skill.

---

## 4. Traitement par lots

Sur un pdf de plusieurs centaines de chunks, une seule invocation dépasserait les **10 minutes** d'une commande en premier plan. On passe donc par des lots, avec `--batch-size N` (par exemple **10**, en chunks).

### Lancer un lot

`etape1_lot.py` traite **un lot par appel** :

```bash
".venv-rag/Scripts/python.exe" ".claude/skills/rag-graphe/scripts/etape1_lot.py" "<document_id_ou_pdf>" --batch-size 10
```

Chaque invocation traite au plus N chunks, puis s'arrête. La sortie se termine par **une seule ligne de bilan** :

| Ligne | Signification | Code de sortie |
| --- | --- | --- |
| `[LOT n/N]` | Lot partiel : relancer la même commande. | 0 |
| `[FIN]` | Étape terminée. | 0 |
| `[BLOQUANT]` | Plus de 5 % de mentions ignorées, ou incohérence. | 2 |
| `[ERREUR]` | Erreur technique. | 1 |

**C'est cette ligne, et non le code de sortie, qui dit s'il faut relancer.** Un `[LOT n/N]` n'est pas une erreur : il signale seulement qu'il reste du travail.

### Enchaîner les lots

- **Relancer exactement la même commande.** La reprise s'appuie sur `graphe_progress.json`. Tant que la ligne est `[LOT n/N]`, on relance sans demander de confirmation.
- Pendant l'avancement, une ligne `>>> Progression : X/Y chunk(s) traités` est affichée après chaque chunk.
- Entre deux lots, `status.json` est marqué `failed` avec le détail « lot partiel en cours ». Il n'est **jamais `done`** avant le dernier lot.
- **Un lot où aucun chunk n'est mené à bout termine le traitement**, avec le code 2. Les chunks dont une mention a été ignorée restent « à refaire » : sans cette règle, le traitement bouclerait indéfiniment.
- Une interruption pendant un lot fait perdre ce lot, pas les lots déjà terminés.

### Relayer chaque lot

Après **chaque** lot, la sortie brute du script est collée dans un bloc de code, avec sa ligne `[LOT n/N]` reprise telle quelle. Aucun résumé à la place de la sortie, y compris pour les lots intermédiaires. Un pdf de 12 lots donne donc 12 relais.

Sur `[BLOQUANT]` ou `[ERREUR]`, le lot suivant ne se lance pas : la cause est rapportée à l'utilisateur.

---

## 5. Consolidation du graphe

La consolidation est une **sous-étape suivante**, lancée automatiquement par le skill dès que `etape1_lot.py` affiche `[FIN]`, sans demander à l'utilisateur. Elle ne se lance jamais sur `[BLOQUANT]` ou `[ERREUR]`. Elle porte sur le graphe **partagé**, pas sur un document.

### À quoi elle sert

Rattraper les **doublons** que la résolution a laissés passer. Exemple : `numpy` et `array numpy`.

Toutes les paires de concepts à similarité ≥ 0,80 sont jugées par `claude -p` (nom, type, définition, pdfs et extrait de chunk des deux côtés). Sur « même concept », le moins mentionné est fusionné dans l'autre : alias et liens sont reportés.

### Fonctionnement d'un lot

Un lot prend les N premières paires à juger. Il se déroule en trois temps :

1. **Préparation** : le processus parent lit les extraits des deux concepts de chaque paire. Lui seul lit le graphe.
2. **Jugements en parallèle** : un thread par paire, un seul appel `claude -p` chacun. Il renvoie « même concept », « distinct » ou une erreur. Il ne touche pas au graphe.
3. **Écritures par le parent**, dans l'ordre de similarité :
   - si un des deux concepts a déjà été fusionné dans ce lot, la paire est **sautée** (elle sera recalculée à la relance) ;
   - si l'arbitrage a échoué, la paire est retentée au lot suivant ;
   - sinon, la fusion ou le verdict « distinct » est appliqué, puis sauvegardé.

### Lancer la consolidation

```bash
".venv-rag/Scripts/python.exe" ".claude/skills/rag-graphe/scripts/etape2_consolidation.py" --batch-size 10
```

- Un lot par appel. Relancer tant que la dernière ligne est `[LOT n/N] consolidation ...`.
- Se termine par `[FIN]`, `[BLOQUANT]` ou `[ERREUR]`, avec les mêmes codes de sortie que l'étape 1 (0, 2, 1).
- `--dry-run` liste les paires à juger et les renommages prévus, **sans rien modifier**.
- Elle peut aussi se lancer seule, par exemple après avoir ajouté plusieurs pdfs.

### Règles

- **Mémoire des jugements** : chaque paire jugée (fusionnée ou distincte) est notée dans `rag_data/db/consolidation_decisions.json`, avec les pdfs qui mentionnaient chaque concept. Une relance ne rejuge pas une paire tranchée. Exception : une paire « distincte » dont un concept est mentionné depuis par un **nouveau** pdf. Une décision d'un ancien format, sans cette mémoire, n'est jamais rejugée.
- **Garde sur le type** : deux types précis différents (`method` et `tool`, par exemple) ne sont jamais jugés ni fusionnés. Les types génériques `concept` et `other` sont compatibles avec tout. À la fusion, le type précis l'emporte.
- **Nom canonique** : au `[FIN]`, chaque concept prend pour forme canonique son nom **le plus fréquent** dans le corpus. Égalité : ordre alphabétique. Une forme de 3 caractères ou moins n'est jamais retenue tant qu'un alias plus long existe. Le nom ne dépend donc pas de l'ordre d'ingestion des pdfs.
- **Hors suivi** : la consolidation n'est pas tracée dans `status.json` et échappe aux contrôles de document. `"graphe": "done"` reste vrai même si elle échoue. Seul son propre `[FIN]` indique qu'elle est terminée.
- Elle ne touche ni aux chunks ni aux liens d'un pdf, sauf le report des liens du concept supprimé vers le concept conservé.

---

## 6. Contrôles de sortie

Les contrôles sont dans `_rag_lib/checks.py`.

### Entrée (code 1 si échec)

- `concepts.json` valide, avec au moins une mention ;
- `/rag-concepts` terminé ;
- CLI `claude` disponible.

### Sortie bloquante (code 2, étape `failed`)

- plus de **5 %** de mentions ignorées ;
- comptes incohérents : mentions liées et ignorées ne font pas le total de `concepts.json` ;
- aucun lien ;
- nombre de chunks du document dans le graphe différent de l'attendu.

**Dans ce cas, ne pas enchaîner sur la consolidation.**

### Reprise

Un chunk contenant au moins une mention ignorée n'est **pas** marqué « fait » dans `graphe_progress.json`. Après un verdict bloquant, ce fichier est conservé, et une relance ne retente que ces chunks.

Un verdict `ok` avec quelques mentions ignorées (5 % ou moins) clôt l'étape : ces mentions ne sont plus retentées.

### Faux « déjà fait »

Un `done` dans `status.json` est vérifié contre le graphe. S'il ne contient pas le document, l'étape est reconstruite.

---

## 7. Sorties

- **Graphe Kuzu** : `rag_data/db/graph/`. Base **unique**, commune à tout le corpus.
- **Statut** : `rag_data/work/<document_id>/status.json`. `"graphe": "done"` marque le document comme entièrement traité par le pipeline RAG.
- **Reprise** : `rag_data/work/<document_id>/graphe_progress.json`. Il n'est **jamais supprimé** automatiquement.
- **Décisions de consolidation** : `rag_data/db/consolidation_decisions.json`.

---

## 8. Requête de contrôle

Pour montrer les concepts partagés entre deux documents, on peut interroger le graphe avec `ConceptGraph.shared_concepts(doc_a, doc_b)` (module `_rag_lib/graph_store.py`). C'est la preuve concrète que le pipeline relie plusieurs sources entre elles.

---

## 9. Quand s'arrêter

- **Code 2** de `etape1_lot.py` : ne pas enchaîner sur la consolidation.
- **Code 2** de `etape2_consolidation.py` : l'intégrité du graphe est en cause. Ne rien enchaîner.

---

## 10. Dossier `scripts/`

| Fichier | Rôle |
| --- | --- |
| `commun.py` | Socle commun : modèle Claude, appels `claude -p`, prompts, chemins. |
| `etape1_resolution.py` | Étape 1 : contrôles, décisions en parallèle, écritures du parent, verdict. Appelé par `etape1_lot.py`. |
| `etape1_lot.py` | Wrapper de l'étape 1 : traite un lot par appel et affiche la ligne de bilan. |
| `etape2_consolidation.py` | Étape 2 : consolidation du graphe partagé (jugement des doublons, fusion, noms canoniques). |
| `prompts/etape1.md`, `prompts/etape2.md` | Consigne d'arbitrage « même concept ou non » de chaque étape. |
| `lib/arbitrage.py` | Arbitrage `claude -p` entre deux concepts, commun aux deux étapes. |
| `lib/resolution.py` | Décision d'une mention (fonction pure, sans écriture) : similarité, seuils, filet lexical, arbitrage. |
| `lib/graphe_document.py` | Étape 1, par lot : `decider_lot` (threads), puis `ecrire_lot` (parent, seul écrivain Kuzu). |
| `lib/reprise.py` | Étape 1 : état de reprise entre lots (`graphe_progress.json`). |
| `lib/lister_a_graphe.py` | Liste les pdfs prêts pour le graphe (commande sans argument, lecture seule). |
| `lib/consolidation.py` | Étape 2 : paires candidates, mémoire des jugements, noms canoniques. |
| `lib/clear_graph.py` | Vidage du graphe. |
| `__pycache__/` | Cache Python généré. Ne rien y modifier. |

La logique du graphe est dans `_rag_lib/graph_store.py` (classe `ConceptGraph`). Les contrôles sont dans `_rag_lib/checks.py`.

---

## 11. Ce que la commande n'est pas

- Elle **n'extrait pas** les concepts : c'est `/rag-concepts`.
- Elle **ne modifie pas** `chunks.json` ni `concepts.json`.
- Elle **ne lit pas** la base vectorielle : elle travaille sur le graphe.
- Elle **ne supprime rien** dans le graphe, en dehors de la consolidation de doublons décrite ci-dessus.
