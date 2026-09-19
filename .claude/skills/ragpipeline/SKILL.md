---
name: ragpipeline
description: Enchaîne tout le pipeline RAG sur un livre/support, de bout en bout, en invoquant successivement /cours-condense, /rag-extraction, /rag-nottext, /rag-chunking, /rag-index, /rag-concepts et /rag-graphe. Usage: /ragpipeline <chemin_vers_livre.pdf> [--from ETAPE] [--force]. Déclenche aussi sur "fais entrer ce livre dans le RAG", "ingère ce document dans la base de connaissances".
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
| `/rag-chunking` | `chunks.json` vide/invalide ; bloc de code coupé dans un chunk |
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
4. **`/rag-chunking <document_id_ou_pdf>`** — chunks.json.
5. **`/rag-index <document_id_ou_pdf>`** — indexation vectorielle (base
   partagée `rag_data/db/vector/`).
6. **`/rag-concepts <document_id_ou_pdf> --batch-size 40`** — extraction de
   concepts par chunk, par lots.
7. **`/rag-graphe <document_id_ou_pdf> --batch-size 30`** — résolution
   d'entités + graphe (base partagée `rag_data/db/graph/`), par lots.

**Par lots, toujours** pour les étapes 3, 6 et 7 : `/rag-nottext
--batch-size 15`, `/rag-concepts --batch-size 40`, `/rag-graphe
--batch-size 30`. Ces trois étapes font des appels `claude -p` séquentiels et
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

`--from ETAPE` (valeurs : `courscondense`, `extraction`, `nottext`, `chunking`,
`index`, `concepts`, `graphe`) force le redémarrage à partir de cette étape
avec `--force`, utile si un changement en amont (ex. nouveau modèle
d'embedding) doit se repropager sans tout refaire depuis `courscondense`.

## À la fin

Ce résumé final n'est pas une question de validation : le pipeline est
terminé. Résume à l'utilisateur, pour ce document : nombre de pages/chunks traités,
nombre de concepts extraits, nombre de mentions liées au graphe, et surtout
tout problème de qualité ou échec rencontré en cours de route. Si
`/rag-graphe` a fusionné des concepts avec des documents déjà présents dans
le corpus, signale-le — c'est le signal que la base de connaissances se
densifie.
