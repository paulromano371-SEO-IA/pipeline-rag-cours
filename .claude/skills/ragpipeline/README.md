# ragpipeline : orchestration du pipeline RAG complet

Ce document explique comment fonctionne la commande `/ragpipeline`, comment
l'utiliser, quelles sont ses étapes et comment il enchaîne les autres skills.

Le fichier `SKILL.md`, à côté de celui-ci, est la consigne lue par Claude Code. Ce
README est la documentation pour un humain. En cas de désaccord, les SKILL.md des étapes
font foi pour leur propre comportement.

---

## 1. Ce que fait la commande

`/ragpipeline` fait entrer **un pdf** dans la base de connaissances, de bout en bout. Il
enchaîne sept étapes, chacune invoquée **comme une commande séparée**, après une étape 0 de
vérification (`Etape0_verifier_deja_condense.py`, section 4) :

```text
/cours-condense -> /rag-extraction -> /rag-nottext -> /rag-chunking -> /rag-index -> /rag-concepts -> /rag-graphe
```

Chaque étape reçoit un contexte frais. Le contexte reste ainsi borné en tokens.

C'est un **orchestrateur** : il décide de l'ordre, lit les verdicts, et s'arrête sur anomalie.
Il ne réimplémente jamais le travail d'une étape. Chaque étape reste responsable de ses propres
contrôles. Le seul script du skill, `scripts/lister_corpus.py`, ne lance aucune étape : il liste
les PDF sources (voir section 2).

---

## 2. Utilisation

```text
/ragpipeline
/ragpipeline <chemin_vers_livre.pdf>
/ragpipeline <chemin_vers_livre.pdf> --from <étape>
/ragpipeline <chemin_vers_livre.pdf> --force
/ragpipeline <chemin_vers_livre.pdf> --reset
/ragpipeline <chemin_vers_livre.pdf> --clear-graph
```

Le déclenchement peut aussi se faire par une phrase : « fais entrer ce pdf dans le RAG »,
ou « ingère ce document dans la base de connaissances ».

### Sans argument : lister le corpus

`/ragpipeline` seul ne lance aucune étape. Il affiche la liste numérotée des PDF de
`corpusdedepart/` **non traités de bout en bout**, avec le décompte. Un PDF est considéré traité
si son PDF condensé existe dans `corpuscondense/` et qu'un dossier `rag_data/work/` le pointe avec
l'étape `graphe` à `done`. Le script est
`scripts/lister_corpus.py`, lancé avec `.venv-rag/Scripts/python.exe`. Il ne liste que les PDF
directement à la racine de `corpusdedepart/` : `done/` et les autres sous-dossiers sont ignorés.

```bash
.venv-rag/Scripts/python.exe .claude/skills/ragpipeline/scripts/lister_corpus.py
```

### Point de départ

Avant toute étape, le pipeline lance `Etape0_verifier_deja_condense.py` sur le PDF source (étape 0, voir
section 4).

- **Un PDF source non condensé** (dans `corpusdedepart/`) : le pipeline part de `/cours-condense`.
- **Un PDF source déjà condensé** : `/cours-condense` n'est pas lancé. Le pipeline démarre à
  `/rag-extraction`, sur le PDF publié dans `corpuscondense/`.
- **Un cours condensé** (chemin déjà dans `corpuscondense/`) : `/cours-condense` n'a pas lieu
  d'être. Le pipeline démarre à `/rag-extraction`.

### Reprise

Chaque étape vérifie elle-même `rag_data/work/<document_id>/status.json`, et saute son
travail si elle est déjà `done`. Pour reprendre un pipeline interrompu, relancer simplement
`/ragpipeline` sur le même fichier source.

---

## 3. Emplacements

```text
<racine_projet>/
  corpusdedepart/        PDF sources, dans leur langue d'origine. Entrée de /cours-condense.
  corpuscondense/        Cours condensés en français. Sortie de /cours-condense, entrée de /rag-extraction.
  rag_data/              Toutes les données générées, centralisées.
    courscondense/<slug>/   Dossier de travail de /cours-condense.
    work/<document_id>/     Fichiers par document : pivot.md, images/, nottext_meta.json,
                            chunks.json, concepts.json, status.json, meta.json.
    db/vector/              Base Chroma, unique, commune au corpus.
    db/graph/               Graphe Kuzu, unique, commun au corpus.
```

**`rag_data/` ne doit jamais être supprimé**, même partiellement, même en cas d'échec ou de
test. Aucune action destructrice sur ce dossier sans demande explicite de l'utilisateur dans
le message en cours.

**Aucun dossier de travail n'est créé à côté d'un PDF source ou condensé.**

Le `document_id` (nom et hash du contenu du PDF condensé) détermine seul l'emplacement sous
`rag_data/work/`. Il est calculé par `/rag-extraction`. Ne pas le recalculer à la main : passer
le même chemin de PDF, ou le `document_id` affiché par une étape.

---

## 4. Déroulement

Une étape à la fois, dans l'ordre. Aucune étape n'est sautée.

| # | Commande | Rôle |
|---|---|---|
| 0 | `Etape0_verifier_deja_condense.py <corpusdedepart>/<livre>.pdf` | Vérifie, en lecture seule, si le livre a déjà été condensé. Code `1` : passe à l'étape 1. Code `0` : saute `/cours-condense` et démarre à `/rag-extraction` sur le PDF publié. Voir section 6. |
| 1 | `/cours-condense <corpusdedepart>/<livre>.pdf checkpoint=false` | Produit le cours condensé en français. C'est l'étape la plus longue. Elle n'est lancée que si l'étape 0 renvoie `1`. |
| 2 | `/rag-extraction <corpuscondense>/<livre>.pdf` | Pivot markdown et images, dans `rag_data/work/<document_id>/`. |
| 3 | `/rag-nottext <document_id> --batch-size 10` | Décrit les images, les blocs de code et les formules, puis enrichit `pivot.md`. |
| 4 | `/rag-chunking <document_id>` | Découpe en `chunks.json`. Les sections structurelles n'y donnent aucun chunk. |
| 5 | `/rag-index <document_id>` | Indexation vectorielle dans la base partagée. |
| 6 | `/rag-concepts <document_id> --batch-size 10` | Extraction des concepts, par lots. |
| 7 | `/rag-graphe <document_id> --batch-size 10` | Résolution des entités et graphe, par lots. Puis la consolidation du graphe partagé. |

### Pourquoi `checkpoint=false`

`/cours-condense` a des checkpoints bloquants par défaut. Ils interrompraient le pipeline pour
attendre une validation. Le pipeline l'invoque donc toujours avec `checkpoint=false`. Le
résumé de chaque checkpoint reste affiché.

### Consolidation

Une fois `/rag-graphe` terminé (`[FIN]`), la **consolidation du graphe partagé** est lancée
par lots, jusqu'à son propre `[FIN]` : `rag-graphe/scripts/consolidate.py --batch-size 10`.
Elle n'est pas enchaînée par le script du lot. Voir le README de `/rag-graphe`.

---

## 5. Règles d'exécution

- **Premier plan uniquement**, bloquant jusqu'à la fin de la commande. Pas de
  `run_in_background`, pas de `Popen` détaché, pas d'agent asynchrone, pas de planification.
  Un hook refuse ces commandes sur les scripts de skill.
- **Jamais deux étapes en parallèle.**
- **Interpréteur** : `.venv-rag/Scripts/python.exe`, jamais `python` ni `py`. Un hook refuse
  ces appels.
- **Pas de sous-agent** pour lire ou vérifier les sorties.
- **Par lots pour les étapes 3, 6 et 7.** Ces étapes font des appels `claude -p` séquentiels.
  Sur un pdf de taille normale, elles dépasseraient les 10 minutes d'une commande en premier
  plan. Un petit document tient en un seul lot, et le code de sortie est alors directement `0`.

### Aucune pause de validation

Le pipeline s'enchaîne **de bout en bout sans attendre de validation** entre deux étapes. Il
ne demande jamais « je continue ? ».

---

## 6. Codes de sortie et arrêts

Chaque étape vérifie ses entrées **avant** de travailler, et ses sorties **après**. Les
contrôles sont dans `_rag_lib/checks.py`. Le verdict (`ok` ou `bloquant`) et ses raisons sont
écrits dans `status.json`.

| Code | Sens | Action |
|---|---|---|
| `0` | OK | Étape suivante, immédiatement. |
| `1` | Erreur technique, ou entrée non satisfaite. | **Arrêt.** Rapporter la cause. |
| `2` | Bloquant (sortie en échec). | **Arrêt.** Rapporter les raisons. |
| `3` | Lot partiel (`/rag-nottext`, `/rag-concepts`, `/rag-graphe`). | **Pas un arrêt.** Relancer la même commande, sans `--force` ni `--reset`, jusqu'à `0`, `1` ou `2`. |

Tout autre code est un arrêt.

### Étape 0 : pdf déjà condensé

`Etape0_verifier_deja_condense.py` (dans `cours-condense/scripts/`) a ses propres codes :

| Code | Statut | Action |
|---|---|---|
| `1` | `NON_CONDENSE` | Passe à l'étape 1 (`/cours-condense`). |
| `0` | `DEJA_CONDENSE` | Saute `/cours-condense`. Démarre à `/rag-extraction` sur le PDF `PDF_PUBLIE` de `corpuscondense/`. |
| `2` | Erreur | Arrêt. Rapporter l'erreur. |

Deux cas font **arrêt** même avec le code `0` :

- `PDF_PUBLIE` absent : seul un PDF compilé dans `rag_data/courscondense/<slug>/out/` existe.
  Rien n'est recopié à la main.
- Une ligne `INCOHERENCE:` est affichée (indices contradictoires). Elle est rapportée telle quelle.

Ce contrôle n'est pas un arrêt volontaire : la vérification passée, le pipeline continue.

### Seuils bloquants par étape

| Étape | Bloquant si |
|---|---|
| `/rag-extraction` | Un bloquant qualité ou fidélité. `pivot.md` ou `meta.json` invalide. |
| `/rag-nottext` | Un élément en échec ou sans description. Une image référencée absente. |
| `/rag-chunking` | `chunks.json` vide ou invalide. Un bloc de code coupé. Un chunk de section structurelle. |
| `/rag-index` | Nombre de chunks dans Chroma différent de l'attendu. Chunk de test non retrouvé. |
| `/rag-concepts` | Plus de **5 %** de chunks indexables sans concept, ou aucun concept. |
| `/rag-graphe` | Plus de **5 %** de mentions ignorées. Comptes incohérents. Document absent du graphe. |

Le seuil commun est de **5 %**.

### Règles générales

- Un verdict bloquant marque l'étape `failed`. **Sauf `/rag-extraction`**, qui reste `done`
  avec ses compteurs de bloquants. Relancer la même commande retente l'étape au lieu de la
  sauter.
- Un `/rag-index` ou `/rag-graphe` dont `status.json` dit `done` mais dont la base ne contient
  pas le document détecte le problème, et refait son travail.
- `/cours-condense` n'a pas de verdict propre. Toute erreur d'exécution est un arrêt.
- **Jamais de correction silencieuse, ni de contenu inventé pour passer outre.** L'anomalie est
  rapportée. La décision de poursuivre ou de relancer revient à l'utilisateur.

---

## 7. Relance forcée : `--from`, `--force`, `--reset`

Ces options servent à refaire un document déjà traité, par exemple après un changement de
découpage ou de modèle d'embedding.

**Elles n'ont d'effet que si l'utilisateur les a écrites dans sa commande en cours.** Elles ne
sont jamais ajoutées de l'initiative du pipeline, ni reprises d'un lancement précédent. Elles
écrasent des données du document.

| Option | Effet |
|---|---|
| `--from <étape>` | Point de départ de la relance forcée. Les étapes avant sont exécutées normalement, et sautées si elles sont `done`. Implique `--force` à partir de l'étape indiquée. |
| `--force` | Relance chaque étape à partir du point de départ. Sans `--from`, le point de départ est `extraction`. |
| `--reset` | Sans `--from` ni `--force` : reconstruit uniquement `/rag-concepts` et `/rag-graphe`. Avec `--force` ou `--from`, n'ajoute rien. |
| `--clear-graph` | Destructif pour **tous** les pdf (section 8). |

Les noms d'étape valides pour `--from` sont : `courscondense`, `extraction`, `nottext`,
`chunking`, `index`, `concepts`, `graphe`.

**`--from courscondense` sur un pdf déjà condensé** : l'étape 0 renvoie `0`, et le pipeline
**s'arrête**. Il ne contourne pas la vérification. Une recondensation n'est pas lancée par ce
pipeline. `--force` ou `--from` à partir de `extraction` ne sont pas concernés, car ils
n'appellent pas `/cours-condense`.

### Ce que chaque étape écrase

| Étape | Lancée avec | Écrase |
|---|---|---|
| `/rag-extraction` | `--force` | `pivot.md`, `images/`, `meta.json`. |
| `/rag-nottext` | `--force --batch-size 10` | `pivot.md` (rétabli puis ré-enrichi), `nottext_meta.json`. Un appel `claude -p` par élément : c'est l'étape la plus coûteuse après `/cours-condense`. |
| `/rag-chunking` | `--force` | `chunks.json`. |
| `/rag-index` | `--force` | Les chunks **de ce document** dans Chroma, remplacés. |
| `/rag-concepts` | `--reset --batch-size 10` | `concepts.json` et l'entrée `extraction_concepts` de `status.json`, supprimés dès le départ. |
| `/rag-graphe` | `--reset --batch-size 10` | La contribution **de ce document** au graphe. |

Pour les étapes par lots, `--force` et `--reset` ne vont que sur le **tout premier lot**. Les
relances de la même étape (code `3`) se font sans eux.

### Pourquoi `/rag-concepts` et `/rag-graphe` reçoivent toujours `--reset`

Quand ces deux étapes sont forcées, elles reçoivent `--reset`, même si l'utilisateur n'a écrit
que `--force` ou `--from`.

- **`/rag-graphe`** : avec `--force` seul, rien n'est retiré du graphe. Les nœuds `Chunk`
  `<document_id>::<n>` de l'ancien découpage resteraient, avec les mêmes identifiants, reliés
  à d'anciens concepts. Le graphe serait faux. `--reset` ne retire que ce document. Les autres
  pdf et les concepts encore mentionnés ailleurs sont conservés. Les alias ajoutés à un
  concept partagé restent.
- **`/rag-concepts`** : `--force` ne supprime pas `concepts.json` au départ. Si le premier lot
  n'aboutit sur aucun chunk, l'ancien fichier resterait et pourrait être validé à tort.
  `--reset` le supprime, ainsi que son statut, avant de commencer.

### Annoncer avant une relance forcée

Avant la première étape forcée, annoncer en une phrase quelles étapes sont forcées, et ce qui
sera écrasé. Ce n'est pas une demande de confirmation : l'utilisateur a déjà décidé en écrivant
l'option.

### Reprendre une relance forcée interrompue

Les étapes en aval du point d'interruption gardent leur ancien `done`, et seraient sautées
à tort. Relancer avec les mêmes options et `--from <étape interrompue>`.

Exception : si seul un lot de `/rag-nottext` était en cours, `--from nottext` repartirait de
zéro. Terminer plutôt `/rag-nottext` seul, sans `--force`, puis relancer `--from chunking` avec
les mêmes options.

---

## 8. `--clear-graph` : destructif pour tout le corpus

Quand l'étape `/rag-graphe` est atteinte, `--clear-graph` procède ainsi :

1. `/rag-graphe <document_id> --clear-graph` vide **tout** le graphe partagé. Il remet à zéro
   l'étape `graphe` de chaque pdf, et s'arrête avec le code `0` sans rien reconstruire.
2. Puis `/rag-graphe <document_id> --reset --batch-size 10` reconstruit **ce** pdf.

Sans `--from` ni `--force`, `--clear-graph` seul relance uniquement l'étape `graphe`.

**Les autres pdf ne sont pas reconstruits par cette commande.** Lancer `/ragpipeline` sur
chacun ensuite. `--from graphe` suffit si leurs concepts sont à jour.

Pour repartir d'un graphe sans alias résiduel, avec plusieurs pdf, mettre `--clear-graph`
sur le **premier** pdf uniquement.

**Avant de vider**, annoncer quels pdf seront à reconstruire. La sortie du script donne la
liste exacte. `--dry-run` de `/rag-graphe` l'affiche sans rien modifier.

---

## 9. Résumé final

À la fin du pipeline, un résumé est donné à l'utilisateur. Ce n'est pas une question de
validation. Il contient, pour le document :

- le nombre de pages et de chunks traités ;
- le nombre de concepts extraits ;
- le nombre de mentions liées au graphe ;
- tout problème de qualité ou échec rencontré.

Après une relance forcée, rappeler quelles étapes ont été forcées.

Si `/rag-graphe` a fusionné des concepts avec des documents déjà présents dans le corpus, le
signaler. C'est le signe que la base de connaissances se densifie.

---

## 10. Fichiers

| Fichier | Rôle |
|---|---|
| `SKILL.md` | Consigne de l'orchestrateur, lue par Claude Code. |
| `README.md` | Le présent document. |
| `scripts/lister_corpus.py` | Liste les PDF de `corpusdedepart/` (sans argument). Ne lance aucune étape. |
| `cours-condense/scripts/Etape0_verifier_deja_condense.py` | Étape 0, appelé avant `/cours-condense`. Script de `cours-condense`, pas de `ragpipeline`. |

Le lancement des étapes reste le travail des skills de chaque étape, chacun avec son propre
`run.py` ou son wrapper de lot.

---

## 11. Ce que la commande n'est pas

- Elle **ne réimplémente pas** le travail d'une étape : elle invoque le skill correspondant.
- Elle **ne corrige rien** en silence, et n'invente pas de contenu pour passer outre un
  bloquant.
- Elle **ne supprime rien** sous `rag_data/`, sauf par `--reset`, `--force` ou `--clear-graph`,
  et seulement si l'utilisateur les a écrits dans sa commande.
- Elle **ne publie rien** sur WordPress.
