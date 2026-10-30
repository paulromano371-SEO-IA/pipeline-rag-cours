# /rag-integrity : audit de qualité du graphe de concepts

Ce document explique la commande `/rag-integrity` : son rôle, ses deux étapes, et à quoi sert chaque script. Il est dérivé de `SKILL.md`, qui reste la consigne de référence.

---

## 1. À quoi sert la commande

`/rag-integrity` vérifie la **qualité du graphe de concepts partagé** (`rag_data/db/graph/`) après une ou plusieurs ingestions de pdfs.

- Elle est **transversale au corpus** : elle n'est liée à aucun `document_id` précis et ne fait pas partie de `/ragpipeline`.
- On la lance **à la demande** : typiquement après un nouveau pdf, ou pour contrôler le corpus dans son ensemble.

Elle se déroule en **deux étapes séparées**, toujours dans cet ordre :

| Étape | Script                          | Rôle                                                                           | Appel à Claude                                    |
| ----- | ------------------------------- | ------------------------------------------------------------------------------ | ------------------------------------------------- |
| 1     | `report.py`                     | Calcule les métriques du graphe et repère les cas ambigus, avec leurs preuves. | **Aucun** (déterministe)                          |
| 2     | `audit_lot.py` / `audit_run.py` | Claude juge chaque cas ambigu.                                                 | **Un appel par item** (deux pour un item signalé) |

L'idée : tout ce qui peut être **calculé** (comptages, similarités, extraits de chunks) est calculé une fois par un script. Claude ne voit que les cas qui demandent un **jugement**, avec les preuves nécessaires.

---

## 2. Interpréteur Python

Chaque script se lance **uniquement** avec l'interpréteur du projet :

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-integrity/scripts/report.py"
```

`<racine_projet>` est le dossier qui contient `.claude/`. Jamais `python` ni `py` (Python système) : un hook bloque ces appels.

---

## 3. Exécution

- Toujours **en premier plan**, jusqu'à la fin. Jamais en arrière-plan, et jamais deux commandes du pipeline en parallèle.
- Ne **jamais déléguer** à un sous-agent la lecture ou la vérification des sorties : elle se fait directement, dans le même tour.

---

## 4. Étape 1 : `report.py`

Cette étape se lance **toujours en premier**. Elle ne fait aucun appel à Claude.

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-integrity/scripts/report.py"
```

### Options utiles

| Option              | Valeur par défaut | Effet                                                            |
| ------------------- | ----------------- | ---------------------------------------------------------------- |
| `--hub-share`       | 0,10              | Seuil de la catégorie 1.3 (concept « hub »).                     |
| `--intra-threshold` | 0,88              | Seuil de la catégorie 2.4 (doublons intra-pdf).                |
| `--sample`          | 25                | Taille des échantillons de la catégorie 2.2.                     |
| `--max-items`       | 1000              | **Plafond total** d'items d'audit, toutes catégories confondues. |
| `--no-history`      | —                 | Désactive le delta avec le rapport précédent.                    |

La liste complète se trouve dans la docstring du script.

### Ce qui est écrit

Tout va dans `rag_data/audit/` :

- `rapport_graphe_<AAAAMMJJ_HHMMSS>.md` : le **rapport lisible** ;
- `rapport_graphe_<...>.json` : les mêmes données, plus `audit_items`, qui sert à l'étape 2 ;
- `historique.json` : un instantané par run, pour afficher le **delta** en tête de chaque rapport (le graphe s'améliore-t-il ou se dégrade-t-il ?).

### Le plafond `--max-items`

Sans plafond, un corpus de nombreux pdfs accumulerait des items sans limite, donc un coût d'appels à Claude sans limite à l'étape 2.

Au-delà de la limite, des items sont retirés en répartissant **équitablement** entre catégories (une catégorie n'est jamais vidée au profit d'une autre). Le dépassement est signalé dans « Signaux à examiner ».

### Relayer le rapport

Le rapport Markdown se **relaie tel quel**, sans résumé ni tableau reformulé : les chiffres et les extraits de chunks réels doivent être visibles.

---

## 5. Registre des décisions (`rag_data/audit/decisions.json`)

C'est la **mémoire persistante** de l'audit. Elle est indépendante des rapports datés. Une entrée par item, avec un identifiant stable, et **deux états seulement** :

| État      | Signification                                                                                                                    |
| --------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `ok`      | Rien à traiter. L'item n'est plus remonté ni rejugé, tant que son contexte et la version de la consigne du juge ne changent pas. |
| `signale` | Problème confirmé, encore à traiter. Listé en tête de rapport (« État de l'audit »), sans être rejugé.                           |

### Ce que ça change

- `report.py` n'écrit dans `audit_items` que les items **nouveaux ou au contexte modifié**. Un nouveau pdf ne génère donc que les items qui touchent ses concepts.
- Chaque verdict de l'étape 2 est enregistré dans ce registre.
- **Problème corrigé dans le graphe** : rien à faire, l'item disparaît tout seul.
- **Faux signalement ou cas acceptable** : on l'écarte avec `audit_accept.py`. Cela ne touche **jamais** le graphe.
- **Hubs (1.3)** : métrique du rapport seulement, plus jugés par Claude. Un hub est presque toujours le sujet central d'un pdf.
- `--force` (premier lot) rejuge tous les items du rapport et remplace leurs entrées.

### Gérer les signalements

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-integrity/scripts/audit_accept.py" --list
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-integrity/scripts/audit_accept.py" <id> [<id> ...]
```

- `--list` affiche les signalements ouverts. Certains peuvent ne plus figurer dans le dernier rapport.
- `audit_accept.py <id>` passe l'item à `ok`.

---

## 6. Étape 2 : audit par lots

Claude juge les items **par lots** (comme `/rag-nottext`, `/rag-concepts` et `/rag-graphe`) : un appel par item, deux pour un item signalé. Au-delà de 10 minutes, la commande passerait en arrière-plan, ce qui est interdit. Un lot de **6** items fait au pire 6 × 2 × 45 s, soit 9 minutes.

Chaque lot se lance avec `audit_lot.py`, **un lot par appel** :

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-integrity/scripts/audit_lot.py" --batch-size 6 [--force]
```

- Sans argument, le script prend **le rapport le plus récent** sous `rag_data/audit/`.
- `--force` (premier lot seulement) rejuge tout depuis zéro. Sans lui, la commande **reprend là où le lot précédent s'est arrêté**.

### La ligne de bilan

La sortie se termine par **une seule ligne** :

| Ligne        | Signification                            | Code de sortie |
| ------------ | ---------------------------------------- | -------------- |
| `[LOT n/N]`  | Lot partiel : relancer la même commande. | 0              |
| `[FIN]`      | Audit terminé.                           | 0              |
| `[BLOQUANT]` | Blocage.                                 | 2              |
| `[ERREUR]`   | Erreur technique.                        | 1              |

Après **chaque** lot, la sortie brute est affichée dans le chat. Sur `[BLOQUANT]` ou `[ERREUR]`, le lot suivant ne se lance pas : la cause est rapportée à l'utilisateur.

### Reprise et audit incrémental

Sans `--force`, avant de juger un item, le script cherche un item de **même identifiant et même contexte** (mêmes extraits, mêmes alias) dans un rapport précédent. S'il existe, il reprend ce verdict **sans appeler Claude**.

Sans cela, chaque nouveau rapport rejugerait les quelque 200 items déjà tranchés. Le coût grandirait à chaque ingestion au lieu de rester proportionnel à ce qui a changé.

Un item dont le contexte a changé n'est **jamais** repris tel quel : il est rejugé.

### Fichiers écrits

- `rapport_graphe_<...>.verdicts.json` : un verdict par item (`verdict`, `reason`, `prompt_version`, et `premiere_passe` si l'item a été confirmé). Mis à jour **après chaque item** : une interruption ne perd jamais plus d'un item.
- `rapport_graphe_<...>_audit.md` : la **synthèse finale**, écrite au dernier lot. Elle s'ouvre sur le tableau « Bilan de l'audit », puis détaille chaque catégorie.

---

## 7. Le tableau « Bilan de l'audit »

C'est **l'indicateur principal** : le chiffre à suivre d'un audit à l'autre pour savoir si le graphe se dégrade. Une ligne par catégorie jugée, plus un total. Il est calculé à partir des verdicts, jamais recopié à la main.

| Catégorie | Jugés | Fausses alertes | Doutes sans verdict | Vrais problèmes |
| --------- | ----- | --------------- | ------------------- | --------------- |

- **Jugés** : items jugés dans **cet** audit (nouveaux ou modifiés).
- **Fausses alertes** : signalées en première passe, écartées en deuxième. `-` si la catégorie n'a pas de deuxième passe.
- **Doutes sans verdict** : preuve insuffisante (verdict `indetermine`).
- **Vrais problèmes** : verdict à signaler, confirmé. C'est la **dernière colonne**, la plus importante.

Les signalements d'audits précédents encore ouverts sont donnés sur une ligne sous le tableau.

Le tableau figure en tête de `..._audit.md` et dans la sortie du dernier lot. Après le `[FIN]`, il est **recopié tel quel** dans le chat, en tableau Markdown.

---

## 8. Catégories jugées

Le sens du verdict change selon la catégorie, car la question posée n'est pas la même :

| Catégorie                | Question posée à Claude                                                    | Verdict « à signaler » |
| ------------------------ | -------------------------------------------------------------------------- | ---------------------- |
| 1.3 Hubs                 | _(plus jugée : métrique du rapport seulement)_                             | —                      |
| 2.1 Concepts partagés    | Cette fusion inter-pdfs, **déjà faite**, est-elle correcte ?             | `fusion_a_tort`        |
| 2.2 Alias suspects       | Cet alias désigne-t-il vraiment le même concept ?                          | `fusion_a_tort`        |
| 2.3 Fusions manquées     | Ces deux concepts, **jamais fusionnés**, sont-ils identiques ?             | `fusion_manquee`       |
| 2.4 Doublons intra-pdf | Ces deux concepts proches, du même pdf, forment-ils un doublon ?         | `doublon`              |
| 2.5 Alias incohérents    | Cette forme partagée par deux concepts est-elle une erreur de résolution ? | `incoherent`           |

En 2.1 à 2.5, le verdict `indetermine` s'ajoute à ceux du tableau (preuve insuffisante).

---

## 9. Règles de jugement

Un **faux signalement** est le pire échec de cette étape : il pousse à corriger un graphe qui n'avait aucun défaut. La règle est donc : **en cas de doute, ne pas accuser**.

1. **Preuve qualifiée.** Chaque extrait fait 400 caractères et porte un champ `terme` :

   - `exact` : le nom ou un alias apparaît tel quel ;
   - `approché` : seul un radical apparaît ;
   - `absent` : rien trouvé. Cet extrait ne prouve rien.

   Pour 2.2, `alias_origine` indique où l'alias a été **extrait** (mention, sens, extrait), lu dans `concepts.json`. C'est la vraie preuve d'une fusion.

2. **Verdict `indetermine`.** Le verdict « à signaler » n'est rendu que si les données montrent **positivement** le problème. Une preuve absente ou hors sujet donne `indetermine`, jamais une accusation par défaut. La catégorie 1.3 garde ses deux verdicts, car elle juge une fréquence et un usage, pas l'identité de deux notions.

3. **Confirmation en deuxième passe.** Un item signalé est rejugé sur les **chunks complets** (plafonnés à 2 500 caractères chacun, embarqués dans le rapport pour rester utilisables même après un nettoyage de `work/`). La deuxième passe ne confirme que sur preuve positive, et son verdict est le verdict final. La première reste sous `premiere_passe`. Un rapport sans `confirmation` laisse le signalement marqué « indisponible » (non confirmé). Coût : un appel de plus par item signalé, environ 15 %.

Chaque jugement se fonde **uniquement** sur les extraits de chunks réels, jamais sur le seul nom du concept, ni sur un extrait dont `terme` vaut `absent`.

### Le sens des concepts

Chaque concept porte un `sens` : sa définition courte, produite par `/rag-concepts`. Le juge s'en sert pour distinguer deux notions voisines ou un même mot employé dans deux sens. C'est une **indication seulement** : fixée à la création du concept à partir d'un seul passage, elle ne prime jamais sur les extraits.

`report.py` signale en « Signaux à examiner » la part de concepts sans `sens`. Au-delà de **10 %** (`checks.MAX_MISSING_SENSE_RATE`), le graphe doit être ré-extrait.

Comme le `sens` fait partie du contexte et de la consigne, un rapport antérieur n'a pas ses verdicts repris : tout est rejugé une fois. Il en va de même pour toute modification de la preuve ou des règles.

---

## 10. Sorties

Toutes dans `rag_data/audit/`.

| Fichier                                     | Contenu                                   |
| ------------------------------------------- | ----------------------------------------- |
| `rapport_graphe_<horodatage>.md`            | Rapport lisible.                          |
| `rapport_graphe_<horodatage>.json`          | Données et `audit_items`.                 |
| `rapport_graphe_<horodatage>.verdicts.json` | Verdicts par item.                        |
| `rapport_graphe_<horodatage>_audit.md`      | Synthèse du jugement.                     |
| `decisions.json`                            | Registre des décisions.                   |
| `historique.json`                           | Instantanés pour le delta entre rapports. |

---

## 11. Ce qu'on retient du résultat

- D'abord le tableau « Bilan de l'audit ».
- Ensuite les **vrais problèmes**, avec la raison donnée par Claude. C'est le livrable utile.
- Les deux rapports restent dans `rag_data/audit/`. Le prochain `report.py` affichera le delta par rapport à celui-ci, dans « Évolution depuis le dernier rapport ».

---

## 12. Dossier `scripts/`

| Fichier           | Rôle                                                                         |
| ----------------- | ---------------------------------------------------------------------------- |
| `report.py`       | Étape 1 : métriques, catégories, preuves, rapport, historique. Déterministe. |
| `audit_run.py`    | Étape 2 : jugement par Claude, confirmation en deuxième passe, synthèse.     |
| `audit_lot.py`    | Wrapper : traite un lot par appel et affiche la ligne de bilan.              |
| `audit_accept.py` | Passe un item à `ok` dans le registre, ou liste les signalements ouverts.    |
| `decisions.py`    | Lecture et écriture du registre `decisions.json`.                            |
| `__pycache__/`    | Cache Python généré. Ne rien y modifier.                                     |

---

## 13. Ce que la commande n'est pas

- Elle **ne modifie jamais le graphe**. Une correction relève de `/rag-graphe` ou de la consolidation.
- Elle **ne recalcule pas** une métrique avec Claude. Claude ne tranche que les cas ambigus.
- Elle **ne supprime rien** dans `rag_data/`. Le registre ne fait qu'ajouter ou mettre à jour des entrées.
- Elle **n'est pas une étape** de `/ragpipeline`.
