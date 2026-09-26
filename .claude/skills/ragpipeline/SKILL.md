---
name: ragpipeline
description: Enchaîne tout le pipeline RAG sur un livre/support, de bout en bout, en invoquant successivement /cours-condense, /rag-extraction, /rag-nottext, /rag-chunking, /rag-index, /rag-concepts et /rag-graphe. Usage: /ragpipeline <chemin_vers_livre.pdf> [--from ETAPE] [--force] [--reset] [--clear-graph]. Déclenche aussi sur "fais entrer ce livre dans le RAG", "ingère ce document dans la base de connaissances".
---

# /ragpipeline — Orchestrateur du pipeline RAG complet

Enchaîne les 7 étapes du pipeline sur un document source, chacune invoquée
comme une commande séparée (pas un agent autonome unique) pour garder
chaque étape dans un contexte frais et borné en tokens :

```
/cours-condense -> /rag-extraction -> /rag-nottext -> /rag-chunking -> /rag-index -> /rag-concepts -> /rag-graphe
```

## Emplacements standardisés (à respecter strictement)

```
<racine_projet>/
  corpusdedepart/             PDF sources (langue d'origine) UNIQUEMENT — entrée de
                               /cours-condense. Jamais de dossier de travail ici.
  corpuscondense/              cours condensés en français (PDF finaux) UNIQUEMENT —
                               sortie de /cours-condense, entrée de /rag-extraction.
                               Jamais de dossier de travail ici non plus.
  rag_data/                   TOUTES les données générées par le pipeline, centralisées.
                               **Ne JAMAIS supprimer ce dossier ni son contenu, même
                               partiellement, même en cas d'échec ou de test — pas
                               d'action destructrice sur rag_data/ sans demande
                               explicite de l'utilisateur dans le message en cours.**
    courscondense/<slug>/       dossier de travail de /cours-condense (extraction/,
                                 illustrations/, scripts/, course.tex, glossaire.json,
                                 plan_cours.json, out/) — /cours-condense lui-même n'est
                                 pas modifié, cette redirection se fait par une
                                 instruction ajoutée à son invocation (voir étape 1)
    work/<document_id>/         fichiers intermédiaires PAR DOCUMENT du reste du
                                 pipeline (pivot.md, images/, nottext_meta.json,
                                 chunks.json, concepts.json, status.json, meta.json)
    db/vector/                    base Chroma UNIQUE, commune à tout le corpus
    db/graph/                      graphe Kuzu UNIQUE, commun à tout le corpus
```

**Jamais de dossier de travail créé à côté d'un PDF source ou condensé.**
`document_id` (nom + hash du contenu du PDF condensé) détermine seul
l'emplacement sous `rag_data/work/`, calculé automatiquement par
`/rag-extraction`. Ne recrée jamais cette logique à la main : passe toujours
le même chemin de PDF (ou le `document_id` qu'un script a affiché) à l'étape
suivante.

## Interpréteur Python (obligatoire)

**Tout script du pipeline se lance UNIQUEMENT avec
`"<racine_projet>/.venv-rag/Scripts/python.exe"`, jamais avec `python`/`py`.**
Avant de lancer une étape, charge son SKILL.md et reprends sa commande exacte
(script `run.py` ou `*_lot.py` selon l'étape) ; ne la devine jamais.

## Règle d'exécution (aucune tâche en arrière-plan)

**Chaque commande (`python .../scripts/run.py ...`) s'exécute en foreground,
de façon strictement bloquante, jusqu'à complétion — jamais via
`run_in_background`, `Popen` détaché, ou tout autre mécanisme asynchrone.**
Le script lui-même est synchrone (y compris ses appels internes `claude -p`,
via `subprocess.run(..., timeout=...)`) ; c'est à l'appelant (toi) de ne pas
briser cette garantie en le lançant en arrière-plan. Lancer deux commandes du
pipeline en parallèle, ou l'une en arrière-plan pendant qu'une autre tourne,
risque une contention sur les appels `claude -p` imbriqués (verrou de
session/auth) qui peut faire timeout un appel pourtant fonctionnel isolément.
Même règle que `/cours-condense` : pas de `ScheduleWakeup`, pas d'agent
async, pour aucune étape du pipeline.

**Une étape à la fois, dans l'ordre, sans sauter d'étape.**

## Aucune pause de validation

Le pipeline s'enchaîne de bout en bout **sans jamais attendre de validation
entre deux étapes** : `/cours-condense` est toujours invoqué avec
`checkpoint=false` (son résumé de checkpoint reste affiché, mais l'étape
suivante démarre immédiatement dans le même tour). Les six autres étapes
n'ont aucune pause de validation de routine. Ne demande donc jamais « je
continue ? » entre deux étapes.

## Arrêts obligatoires (garde-fous, actifs même sans pause de validation)

Ce ne sont pas des validations de routine mais des arrêts sur anomalie,
conservés quoi qu'il arrive — `checkpoint=false` ne les désactive jamais.
**Arrête-toi et rapporte à l'utilisateur, sans enchaîner sur l'étape
suivante**, dans exactement ces cas :

Chaque étape après `/cours-condense` vérifie elle-même ses entrées AVANT de
travailler et ses sorties APRÈS (module partagé `_rag_lib/checks.py`,
déterministe, aucun appel LLM — le contrôle de sortie de l'étape N et le
contrôle d'entrée de l'étape N+1 partagent les mêmes fonctions de
validation). Le verdict (`ok`/`bloquant`) et ses raisons sont écrits dans
`status.json` (`verdict`, `verdict_reasons`) et affichés en clair dans la
sortie du script (`Vérification d'entrée/de sortie — /rag-<étape> : OK|BLOQUANT`).
Un verdict bloquant marque l'étape `failed` dans `status.json` (sauf
`/rag-extraction`, qui reste `done` avec ses compteurs de bloquants, comme
avant) : relancer la même commande la retente au lieu de la sauter.

**Convention de code de sortie, identique pour toutes les étapes après
`/cours-condense` :**

| Code | Sens | Action |
|---|---|---|
| `0` | OK | étape suivante immédiate |
| `1` | erreur technique ou prérequis non satisfait (contrôle d'ENTRÉE en échec, fichier absent, exception) | **arrêt**, rapporte la cause |
| `2` | BLOQUANT (contrôle de SORTIE en échec) | **arrêt**, rapporte les raisons |
| `3` | lot partiel de `/rag-nottext`, `/rag-concepts` ou `/rag-graphe` (`--batch-size`) | **pas un arrêt** : relance la même commande (sans `--force`/`--reset`) jusqu'à `0`, `1` ou `2` |

Tout code autre que `0` et `3` est un arrêt. Contrôles bloquants par étape
(seuil d'échec commun : **> 5 %**) :

| Étape | Sortie bloquante si |
|---|---|
| `/rag-extraction` | au moins un `BLOQUANT` qualité ou fidélité ; `pivot.md`/`meta.json` invalides |
| `/rag-nottext` | élément non-textuel en échec ou sans description ; image référencée absente |
| `/rag-chunking` | `chunks.json` vide/invalide ; bloc de code coupé dans un chunk ; chunk de section structurelle (table des matières...) |
| `/rag-index` | nombre de chunks dans Chroma différent de l'attendu (comptage exact) ; chunk de test non retrouvé |
| `/rag-concepts` | plus de 5 % des chunks indexables sans concepts, ou aucun concept |
| `/rag-graphe` | plus de 5 % de mentions ignorées ; comptes incohérents ; document absent du graphe |

- Un `/rag-index` ou `/rag-graphe` dont `status.json` dit `done` mais dont
  la base ne contient pas réellement le document (base supprimée, corrompue)
  le détecte lui-même et refait son travail au lieu de le sauter.
- `/cours-condense` n'a pas de verdict propre ; toute erreur d'exécution
  reste un arrêt.
- Jamais de correction silencieuse ni de contenu inventé pour passer outre :
  rapporte l'anomalie, la décision de poursuivre ou de relancer revient à
  l'utilisateur.

Ne réimplémente jamais la logique d'une étape ici : invoque le skill
correspondant (`Skill` tool, ou directement son script si le skill est déjà
chargé dans cette conversation) plutôt que de réécrire son traitement en
ligne.

## Déroulement

1. **`/cours-condense <corpusdedepart>/<livre>.pdf checkpoint=false`** —
   produit le cours condensé en français. Étape la plus longue/coûteuse
   (agent autonome complet) ; si elle a déjà tourné pour ce livre, ne la
   relance pas sauf demande explicite.

   **Toujours invoquée avec `checkpoint=false`** (voir "Aucune pause de
   validation" ci-dessous) : sans lui, les checkpoints des étapes 1, 2 et 3
   de `/cours-condense` sont bloquants par défaut et interrompraient le
   pipeline pour attendre une validation.

   Le skill est autonome sur ce point (pas besoin d'instruction
   supplémentaire de ta part) : il travaille lui-même sous
   `rag_data/courscondense/<slug_du_livre>/` et publie automatiquement son
   PDF final vers `<racine_projet>/corpuscondense/<slug>.pdf` en dernière
   étape (voir `cours-condense/SKILL.md`, étape 6.5). C'est cette copie dans
   `corpuscondense/` qui sert d'entrée à l'étape suivante.
2. **`/rag-extraction <corpuscondense>/<livre>.pdf`** — pivot markdown +
   images, écrits dans `rag_data/work/<document_id>/`. Note le
   `document_id` affiché : réutilise-le (ou le même chemin de PDF) pour
   toutes les étapes suivantes.
3. **`/rag-nottext <document_id_ou_pdf>`** — décrit en langage naturel
   chaque image (avec OCR/LaTeX adapté, nommage explicite), bloc de code et
   formule d'affichage déjà en LaTeX, puis enrichit `pivot.md` en
   conséquence — sans cette étape, ce contenu (en particulier une formule,
   qu'elle soit restée image ou déjà en LaTeX) reste invisible à la
   recherche vectorielle et au graphe de concepts, un modèle d'embedding
   texte ne rapprochant quasiment jamais une question en français d'un
   verbatim LaTeX/code/image brut (vérifié empiriquement).
4. **`/rag-chunking <document_id_ou_pdf>`** — chunks.json. Les sections
   structurelles (table des matières, index...) n'y donnent aucun chunk :
   rien n'en est indexé, extrait en concepts ni relié au graphe.
5. **`/rag-index <document_id_ou_pdf>`** — indexation vectorielle (base
   partagée `rag_data/db/vector/`).
6. **`/rag-concepts <document_id_ou_pdf> --batch-size 10`** — extraction de
   concepts par chunk, par lots.
7. **`/rag-graphe <document_id_ou_pdf> --batch-size 10`** — résolution
   d'entités + graphe (base partagée `rag_data/db/graph/`), par lots.

**Par lots, toujours** pour les étapes 3, 6 et 7 : `/rag-nottext
--batch-size 10`, `/rag-concepts --batch-size 10`, `/rag-graphe
--batch-size 10`. Ces trois étapes font des appels `claude -p` séquentiels et
dépasseraient, sur un livre de taille normale, les 10 minutes d'une commande
foreground (l'application la passerait alors en arrière-plan, ce que ce
pipeline interdit). Sur un petit document, un seul lot suffit et le code de
sortie est directement `0`. Code `3` = relancer la même commande, sans
`--force`/`--reset` (à réserver au tout premier lot), jusqu'à `0`, `1` ou `2`.

## Reprise

Chaque étape vérifie elle-même `rag_data/work/<document_id>/status.json` et
saute son propre travail si déjà `done` (sauf `--force`). Pour reprendre un
pipeline interrompu, relance simplement `/ragpipeline` sur le même fichier
source : les étapes déjà faites se sautent d'elles-mêmes.

Si le chemin donné est déjà un cours condensé (dans `corpuscondense/`),
`/cours-condense` est sans objet : démarre à `/rag-extraction`.

## Relance forcée : `--from`, `--force`, `--reset`

`/ragpipeline <pdf> [--from ETAPE] [--force] [--reset] [--clear-graph]`

Pour refaire un document déjà traité (ex. après un changement de découpage
ou de modèle d'embedding). **Ces options n'ont d'effet que si l'utilisateur
les a écrites dans SA commande en cours** : jamais ajoutées de ta propre
initiative, jamais reprises d'un lancement précédent — elles écrasent des
données de ce document.

| Option | Effet |
|---|---|
| `--from ETAPE` | Point de départ de la relance forcée (`courscondense`, `extraction`, `nottext`, `chunking`, `index`, `concepts`, `graphe`). Les étapes avant ETAPE s'exécutent normalement (sautées si `done`). Implique `--force` à partir de ETAPE. |
| `--force` | Relance avec `--force` chaque étape à partir du point de départ, même si elle est `done`. Sans `--from`, le point de départ est `extraction` — jamais `courscondense`, sauf `--from courscondense` explicite (étape la plus longue). |
| `--reset` | Raccourci : sans `--from` ni `--force`, reconstruit uniquement `/rag-concepts` et `/rag-graphe` (point de départ `concepts`). Avec `--force` ou `--from`, n'ajoute rien : ces deux étapes reçoivent de toute façon `--reset` quand elles sont forcées (voir ci-dessous). |

Ce que chaque étape reçoit lorsqu'elle est forcée, et ce qu'elle écrase — pour
les étapes par lots, ces options ne vont que sur le **tout premier lot** ; les
relances de la même étape (code `3`) se font sans elles :

| Étape | Lancée avec | Écrase |
|---|---|---|
| `/rag-extraction` | `--force` | `pivot.md`, `images/`, `meta.json` |
| `/rag-nottext` | `--force --batch-size 10` | `pivot.md` (rétabli puis ré-enrichi), `nottext_meta.json` — un appel `claude -p` par élément, la plus coûteuse |
| `/rag-chunking` | `--force` | `chunks.json` |
| `/rag-index` | `--force` | les chunks DE CE document dans Chroma (remplacés) |
| `/rag-concepts` | `--reset --batch-size 10` | `concepts.json` et l'entrée `extraction_concepts` de `status.json`, supprimés dès le départ |
| `/rag-graphe` | `--reset --batch-size 10` | la contribution DE CE document au graphe |

**`/rag-concepts` et `/rag-graphe` forcés reçoivent toujours `--reset`, même
si l'utilisateur n'a écrit que `--force` ou `--from`** :
- `/rag-graphe` : `--force` seul ne retire rien du graphe, et les nœuds
  `Chunk` `<document_id>::<n>` de l'ancien découpage y resteraient avec les
  mêmes identifiants, reliés à d'anciens concepts (la numérotation des chunks
  a changé) — un graphe faux. `--reset` ne retire que ce document ; les
  autres livres et les concepts encore mentionnés ailleurs sont conservés
  (les alias qu'il avait ajoutés à un concept partagé, eux, restent).
- `/rag-concepts` : `--force` ne supprime pas `concepts.json` au départ. Si le
  premier lot n'aboutit sur aucun chunk, l'ancien fichier — numéroté selon
  l'ancien découpage — resterait en place et le contrôle de sortie pourrait
  le valider à tort. `--reset` le supprime, ainsi que l'entrée de statut,
  avant de commencer.

**`--clear-graph`** (destructif pour TOUS les livres) : quand l'étape
`/rag-graphe` est atteinte, lance d'abord `/rag-graphe <document_id_ou_pdf>
--clear-graph` — il vide tout le graphe partagé, remet à zéro l'étape `graphe`
de chaque livre et s'arrête avec le code `0` sans rien reconstruire (voir son
SKILL.md) — puis, dans la foulée, `/rag-graphe <document_id_ou_pdf> --reset
--batch-size 10` pour reconstruire ce livre-ci. Sans `--from` ni `--force`,
`--clear-graph` seul relance uniquement l'étape `graphe` (point de départ
`graphe`). Comme les autres options : jamais ajouté de ta propre initiative.
Les autres livres ne sont **pas** reconstruits par cette commande : lance
`/ragpipeline` sur chacun d'eux ensuite (`--from graphe` suffit si leurs
concepts sont à jour). Pour repartir d'un graphe sans alias résiduel avec
plusieurs livres, mets `--clear-graph` sur le **premier** livre uniquement.
Annonce avant de vider quels livres seront à reconstruire ; la sortie du
script en donne la liste exacte (`--dry-run` de `/rag-graphe` l'affiche sans
rien modifier).

**Avant la première étape forcée**, annonce en une phrase quelles étapes sont
forcées et ce qui sera écrasé (voir le tableau). Ce n'est pas une demande de
confirmation : l'utilisateur a déjà décidé en écrivant l'option.

**Reprise d'une relance forcée interrompue** : les étapes en aval du point
d'interruption portent encore leur ancien `done` (données de l'ancien
découpage) et seraient sautées. Relance donc avec les mêmes options et
`--from <étape interrompue>`. Exception : si seul un lot de `/rag-nottext`
était en cours, `--from nottext` repartirait de zéro — termine plutôt
`/rag-nottext` seul (sans `--force`), puis relance `--from chunking` avec les
mêmes options.

## À la fin

Ce résumé final n'est pas une question de validation : le pipeline est
terminé. Résume à l'utilisateur, pour ce document : nombre de pages/chunks traités,
nombre de concepts extraits, nombre de mentions liées au graphe, et surtout
tout problème de qualité ou échec rencontré en cours de route. Après une
relance forcée, rappelle quelles étapes ont été forcées. Si
`/rag-graphe` a fusionné des concepts avec des documents déjà présents dans
le corpus, signale-le — c'est le signal que la base de connaissances se
densifie.
