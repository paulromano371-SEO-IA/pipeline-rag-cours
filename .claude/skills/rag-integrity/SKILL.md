# /rag-integrity — Audit de qualité du graphe de concepts

Transversal au corpus (pas une étape de `/ragpipeline`, pas liée à un
`document_id` précis) : évalue l'état du graphe Kuzu partagé
(`rag_data/db/graph/`) après une ou plusieurs ingestions. À lancer à la
demande — typiquement après `/ragpipeline` sur un nouveau livre, ou pour
vérifier le corpus dans son ensemble.

Deux étapes strictement séparées, toujours dans cet ordre :

1. **`report.py`** — entièrement déterministe (AUCUN appel `claude -p`).
   Recalcule les métriques structurelles du graphe (volumes par livre,
   concepts hubs, alias suspects, paires jamais fusionnées, doublons
   intra-livre...) et repère, avec preuves (extraits de chunks réels), les
   cas ambigus qui méritent un jugement. Écrit un rapport Markdown lisible
   par un humain ET un `.json` structuré (`audit_items`) lisible par
   l'étape 2 — cette dernière ne relit jamais le graphe Kuzu elle-même.
2. **`audit_run.py` / `audit_lot.py`** — Claude (`claude -p` headless, un
   appel par item) juge chaque item ambigu à partir du contexte fourni par
   report.py (nom, type, alias, extraits) : fusion à tort ? concept trop
   générique ? doublon ? C'est le seul endroit de `/rag-integrity` où un
   LLM intervient — jamais pour recalculer une métrique, seulement pour
   trancher un cas que le calcul seul ne peut pas trancher (voir
   `report.py`, section "Il n'existe pas de vérité de référence...").

Cette séparation est volontaire : tout ce qui peut être calculé (comptages,
similarités d'embedding, extraits de chunks) est calculé une fois pour
toutes par un script déterministe, jamais redemandé à Claude ni recalculé à
chaque jugement — Claude ne voit que ce qui exige réellement un jugement
sémantique, avec les preuves nécessaires pour le faire avec précision.

## Exécuter report.py (étape 1, toujours en premier)

Toujours en foreground, bloquant jusqu'à complétion — jamais via
`run_in_background`. Aucun appel `claude -p` (voir ci-dessus), donc pas de
risque de contention, mais même règle que le reste du pipeline RAG par
cohérence.

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-integrity/scripts/report.py"
```

Options utiles : `--hub-share 0.10` (seuil de 1.3), `--intra-threshold 0.88`
(seuil de 2.4), `--sample 25` (taille des échantillons de 2.2), `--max-items 300`
(plafond TOTAL d'items d'audit toutes catégories confondues, indépendant des
bornes par section — voir plus bas), `--no-history` (désactive le delta avec
le rapport précédent). Voir la docstring du script pour la liste complète —
ne jamais la dupliquer ici.

Écrit, sous `rag_data/audit/` (jamais ailleurs — voir `_rag_lib/paths.py`) :
- `rapport_graphe_<AAAAMMJJ_HHMMSS>.md` — le rapport humain (structurellement
  identique à l'ancien `tools/graph_quality_report.py`, déplacé ici) ;
- `rapport_graphe_<...>.json` — les mêmes données + `audit_items`, entrée de
  l'étape 2 ;
- `historique.json` — un instantané chiffré par run, pour le delta affiché
  en tête de chaque rapport ("le graphe s'améliore-t-il ou se dégrade-t-il
  avec ce nouveau livre, et pourquoi").

**`--max-items` (défaut 300)** plafonne le nombre TOTAL d'items d'audit,
toutes catégories confondues — indépendant de `--max-shared`/`--sample`/
`--max-pairs`, qui ne bornent que l'affichage (et les items) de CHAQUE
section prise séparément. Sans ce plafond global, un corpus à beaucoup de
livres accumule des items sans limite, donc sans limite sur le coût total
en appels `claude -p` de l'étape 2. Le dépassement retire des items en
répartissant équitablement entre catégories (jamais une catégorie vidée au
profit d'une autre) et le signale dans "Signaux à examiner".

**Après l'exécution, relaie le rapport Markdown tel quel** (comme les autres
étapes du pipeline RAG relaient leur compte-rendu) : ne résume jamais les
tableaux à la place de les montrer, l'utilisateur doit voir les chiffres
réels et les extraits de chunks, pas une paraphrase.

## Registre des décisions (`rag_data/audit/decisions.json`)

Mémoire persistante de l'audit, indépendante des rapports datés : une entrée
par item (id stable), avec **deux états seulement** :

- `ok` : rien à traiter. L'item n'est plus jamais remonté ni rejugé, tant que
  l'empreinte de son contexte (extraits, alias, sens) et la version de la
  consigne du juge (`audit_run._prompt_version`) ne changent pas.
- `signale` : problème confirmé par le juge, encore à traiter. Listé en tête
  de chaque rapport (section « État de l'audit ») tant que l'item existe dans
  le graphe, sans être rejugé.

Effets :
- `report.py` n'écrit dans `audit_items` que les items **nouveaux ou au
  contexte modifié** ; il indique combien sont déjà validés (`n_settled_ok`)
  et liste les signalements ouverts (`open_flags`). Un nouveau livre ne
  génère donc que les items qui touchent ses concepts, pas tout le corpus.
- `audit_run.py` (via `audit_lot.py`) enregistre chaque verdict dans le
  registre. Le registre est créé automatiquement au premier usage à partir des
  anciens `*.verdicts.json` (verdicts rendus avec la consigne actuelle
  seulement).
- **Problème corrigé dans le graphe** : rien à faire, l'item n'est plus produit
  par `report.py` et disparaît. **Faux signalement ou acceptable** : l'écarter
  avec `audit_accept.py <id>` (passe l'état à `ok`, ne touche jamais au
  graphe) ; `audit_accept.py --list` liste les signalements ouverts du registre
  (dont certains peuvent ne plus figurer dans le dernier rapport).
- **Hubs (1.3)** : métrique du rapport uniquement, plus jugés par le LLM (un
  hub est presque toujours le sujet central d'un livre).
- `--force` (premier lot) rejuge tous les items du rapport et remplace leurs
  entrées du registre.

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-integrity/scripts/audit_accept.py" --list
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-integrity/scripts/audit_accept.py" <id> [<id> ...]
```

## Exécuter l'audit (étape 2, par lots)

Comme `/rag-nottext`, `/rag-concepts` et `/rag-graphe` : un appel `claude -p`
par item (deux pour un item signalé, voir « Règles de jugement »), donc
toujours par lots sur un corpus de taille normale (au-delà de 10 minutes,
l'application passerait la commande en arrière-plan, ce que ce pipeline
interdit : lots de 6, soit au pire 6 × 2 × 45 s = 9 min). **Lance chaque lot avec `scripts/audit_lot.py`** (un lot
par appel, jamais plusieurs lots enchaînés dans une même commande) :

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-integrity/scripts/audit_lot.py" --batch-size 6 [--force]
```

Sans argument positionnel, prend automatiquement le rapport le plus récent
sous `rag_data/audit/` (celui que `report.py` vient d'écrire) — passe un
chemin explicite uniquement pour auditer un rapport plus ancien. `--force`
(premier lot seulement) rejuge tous les items depuis zéro ; sans lui, la
commande reprend là où le lot précédent s'est arrêté.

**Audit incrémental, sauf `--force`** : avant de juger quoi que ce soit, un
item portant le MÊME id (même concept) ET le MÊME contexte (mêmes extraits,
mêmes alias) qu'un item déjà jugé dans un rapport PRÉCÉDENT reprend
directement ce verdict, sans rappeler `claude -p`. Sans ça, chaque nouveau
rapport (ex. après l'ajout d'un livre au corpus) rejugerait l'intégralité
des ~200 items déjà tranchés lors d'audits précédents, même ceux que le
nouveau livre ne concerne pas — un coût en appels `claude -p` qui grandirait
à chaque ingestion au lieu de rester proportionnel à ce qui a réellement
changé. Un item dont le contexte a changé (nouvel extrait, nouvel alias)
n'est jamais repris tel quel : il est rejugé, l'ancienne preuve n'étant plus
forcément celle qui justifierait encore le même verdict.

Le script termine sa sortie par UNE ligne de bilan : `[LOT n/N]` (lot
partiel : relancer la même commande), `[FIN]` (audit terminé), `[BLOQUANT]`
ou `[ERREUR]`. Code de sortie : **0** pour `[LOT n/N]` et `[FIN]` (un lot
partiel n'est pas une erreur), **2** pour `[BLOQUANT]`, **1** pour
`[ERREUR]`.

**Après CHAQUE lot, avant de lancer le suivant, écris un message dans le
chat** qui contient la sortie brute du script (bloc de code markdown) et sa
ligne `[LOT n/N]` reprise telle quelle — même obligation, pour la même
raison (l'affichage de l'application replie le résultat d'une commande),
que `/rag-nottext`/`/rag-concepts`/`/rag-graphe`. Sur `[BLOQUANT]` ou
`[ERREUR]`, ne lance pas le lot suivant : rapporte la cause à l'utilisateur.

Écrit, à côté du rapport audité :
- `rapport_graphe_<...>.verdicts.json` — `{"item_id": {"verdict", "reason", "prompt_version"[, "premiere_passe"]}}`,
  mis à jour après CHAQUE item (comme `concepts.json` pour `/rag-concepts`) :
  une interruption ne perd jamais plus d'un item ;
- `rapport_graphe_<...>_audit.md` — synthèse finale (écrite au dernier lot
  uniquement) : par catégorie (1.3, 2.1, 2.2, 2.3, 2.4, 2.5), le nombre
  d'items jugés et la liste de ceux marqués comme problématiques
  (`generique`, `fusion_a_tort`, `fusion_manquee`, `doublon`, `incoherent`)
  avec la raison donnée par Claude.

## Catégories jugées et leur verdict

Le "sens" du verdict change avec la catégorie (2.1 juge une fusion DÉJÀ
faite, 2.3 juge une fusion JAMAIS faite) — voir `audit_run.py:CATEGORIES`
pour le détail exact des instructions envoyées à Claude :

| Catégorie | Question posée à Claude | Verdict "à signaler" |
|---|---|---|
| 1.3 Hubs | _(plus jugés : métrique du rapport seulement, voir « Registre des décisions »)_ | — |
| 2.1 Concepts partagés | Cette fusion inter-livres déjà faite est-elle correcte ? | `fusion_a_tort` |
| 2.2 Alias suspects | Cet alias désigne-t-il vraiment le même concept ? | `fusion_a_tort` |
| 2.3 Fusions manquées | Ces deux concepts jamais fusionnés sont-ils en fait identiques ? | `fusion_manquee` |
| 2.4 Doublons intra-livre | Ces deux concepts proches du même livre sont-ils un doublon ? | `doublon` |
| 2.5 Alias incohérents | Cette forme partagée par 2 concepts est-elle une erreur de résolution ? | `incoherent` |

En 2.1 à 2.5, `indetermine` s'ajoute aux verdicts de chaque ligne (preuve insuffisante, voir « Règles de jugement »).

## Règles de jugement (préviennent les faux positifs)

Un faux signalement est le pire échec de cette étape : il envoie l'utilisateur
corriger un graphe qui n'avait pas de défaut. Trois règles, alignées sur
l'arbitrage de `/rag-graphe` (« en cas de doute, ne pas fusionner » ; ici,
en cas de doute, **ne pas accuser**) :

1. **Preuve qualifiée.** Chaque extrait fait 400 caractères (comme l'arbitrage
   de `/rag-graphe`) et porte un champ `terme` : `exact` (le nom ou un alias
   apparaît tel quel), `approché` (seul un radical de mot apparaît) ou
   `absent` (rien trouvé : l'extrait est le début du chunk et ne prouve rien).
   Pour 2.2, `alias_origine` donne l'endroit où l'alias suspect a été
   **extrait** (mention telle qu'écrite, `sens_de_la_mention`, extrait), lu dans
   `concepts.json` de chaque livre : c'est la vraie preuve d'une fusion, pas un
   chunk quelconque du concept. Liste vide (ex. `work/` vidé) = preuve manquante.
2. **Verdict `indetermine`** (2.1 à 2.5). Le juge ne rend le verdict « à
   signaler » que si les données montrent positivement le problème ; une
   preuve absente ou hors sujet donne `indetermine`, jamais une accusation par
   défaut. 1.3 (généricité) garde ses deux verdicts : elle juge une fréquence
   et un usage, pas l'identité de deux notions.
3. **Confirmation en 2e passe** (2.1 à 2.5). Un item signalé est rejugé sur les
   **chunks complets** (`confirmation.chunks` du rapport, plafonnés à 2 500
   caractères chacun ; ils y sont embarqués pour que l'audit reste possible
   après un nettoyage de `work/`). La 2e passe ne confirme que sur preuve
   positive ; son verdict est le verdict final, la 1re est gardée sous
   `premiere_passe`. Un rapport sans `confirmation` laisse le signalement
   marqué « indisponible » (non confirmé). Coût : un appel de plus par item
   signalé seulement (~15 %).

La synthèse (`_audit.md`) affiche, par catégorie, les verdicts finaux, le
nombre de signalements écartés en 2e passe (les faux positifs évités) et la
liste des `indetermine` avec ce qui manque.

Chaque concept du contexte porte aussi son **`sens`** (définition courte
produite par `/rag-concepts`, stockée dans le graphe). Le juge s'en sert pour
distinguer deux notions voisines ou un même mot employé dans deux sens, mais
ce n'est qu'une indication : fixée à la création du concept à partir d'un
seul passage, elle ne prime jamais sur les extraits. `report.py` signale en
"Signaux à examiner" (et affiche en 1.2) la part de concepts sans `sens`
(seuil `checks.MAX_MISSING_SENSE_RATE`, 10 %) : un graphe construit avant
l'ajout de `sense` doit être ré-extrait, sinon le juge travaille sans. Le
`sens` faisant partie du contexte et de la consigne, les verdicts d'un
rapport antérieur à cet ajout ne sont pas repris : tout est rejugé une fois
(de même pour toute modification de la preuve ou des règles ci-dessus : la
consigne et le contexte font partie de la comparaison de reprise).

Chaque jugement se base **uniquement sur les extraits de chunks réels**
fournis par `report.py` (jamais sur le seul nom du concept, ni sur un extrait
dont `terme` vaut `absent`) — c'est ce qui
permet une précision fine (ex. distinguer un hub légitime comme `embedding`,
réutilisé à l'identique dans plusieurs livres, d'un hub genuinement vague).

**(Historique — 1.3 n'est plus audité, voir « Registre des décisions » ; le code de jugement reste pour relire d'anciens rapports.)
1.3 était audité par (concept, livre), pas par concept seul** : un concept
peut franchir le seuil de hub dans plusieurs livres à la fois (ex. `llm` à
49% dans un livre ET 27% dans un autre) avec une généricité potentiellement
différente dans chacun. Le tableau 1.3 du rapport reste agrégé (une ligne
par concept, pour rester lisible), mais chaque livre où le seuil est franchi
reçoit son propre item d'audit, son propre extrait et son propre verdict —
jamais un seul jugement appliqué à tort à tous les livres concernés.

## À la fin

Résume à l'utilisateur : nombre d'items jugés par catégorie, et surtout ceux
marqués comme problématiques (voir tableau ci-dessus) avec la raison donnée
par Claude — c'est le livrable utile, pas juste "l'audit est terminé".
Rappelle que les deux rapports (`rapport_graphe_<...>.md` et
`..._audit.md`) restent tous les deux sous `rag_data/audit/` pour référence
ultérieure, et que le prochain `report.py` affichera un delta par rapport à
celui-ci (section "Évolution depuis le dernier rapport").
