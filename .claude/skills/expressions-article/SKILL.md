---
name: expressions-article
description: >-
  Repère, dans un article de rag_data/content/work/, les expressions (méthodes, outils,
  concepts, métriques, acronymes) qui méritent un contenu dédié. Vérifie chaque
  expression dans la base graphe (existe-t-elle dans le corpus, et est-ce un
  concept, une méthode, un outil ou une métrique ?), puis dans les articles
  existants (déjà traitée, simplement mentionnée, ou absente), et propose les
  commandes /create-content prêtes à lancer. Lecture seule. Usage :
  /expressions-article <slug>, /expressions-article <numéro> ou
  /expressions-article --all.
---

# /expressions-article — Expressions à transformer en contenu dédié

`/expressions-article <slug|numéro|--all>` lit un article de `rag_data/content/work/`
et répond à une question : quelles expressions de ce texte sont assez importantes
pour mériter leur propre article, et lesquelles ont déjà le leur ? Un rapport par
article a son dossier `rag_data/expressions/work/<slug>/`, sur le modèle de
`rag_data/content/work/<slug>/`.

`<slug>` est le nom d'un dossier de `rag_data/content/work/` ; un numéro désigne le
n-ième article de la liste filtrée ci-dessous ; `--all` traite tous les articles de cette
liste. Un slug explicite reste accepté, même hors liste.

`<racine_projet>` = le dossier qui contient `.claude/` (remonte depuis ce SKILL.md
jusqu'au premier parent qui le contient ; ne suppose aucun chemin fixe).

## Règles d'or

1. **Lecture seule sur le contenu.** Ce skill ne modifie jamais un article de
   `rag_data/content/work/`, ni la base graphe (`rag_data/db/graph/`), ni la base vectorielle.
   Ses sorties sont `rag_data/expressions/work/<slug>/`, `rag_data/expressions/INDEX.md` et
   `rag_data/expressions/EXPRESSIONS.md`.
2. **Le LLM décide ; le graphe ne fait que conseiller.** La décision (nouveau sujet,
   déjà traitée, secondaire, ambiguë) vient du LLM, qui lit le contexte de l'article et
   le catalogue des articles publiés. La base graphe ne fournit que des indications
   facultatives (type, sens, nombre de pdf) : une absence du graphe n'exclut rien,
   et le LLM peut la contredire.
3. **Aucune commande `/create-content` n'est lancée automatiquement.** Le rapport
   donne les commandes ; c'est l'utilisateur qui décide lesquelles lancer.
4. **Foreground, séquentiel, sans sous-agent** (voir « Exécution »).
5. **Une seule sortie utilisateur :** le rapport est recopié intégralement dans ta
   réponse (voir « Affiche le résultat »).

## Interpréteur Python

Tout script de ce skill se lance **uniquement** avec
`"<racine_projet>/.venv-rag/Scripts/python.exe"`, jamais avec `python`/`py`
(Python système). Techniquement imposé par un hook `PreToolUse` sur `Bash` et
`PowerShell` (`.claude/hooks/block_system_python.py`, voir `.claude/settings.json`),
qui refuse toute commande lançant un script de `.claude/skills/*/scripts/` avec
`python`/`py`. Voir `/ragpipeline`, section « Interpréteur Python ».

## Exécution

Toujours en foreground, bloquant jusqu'à complétion — jamais via
`run_in_background` ni aucun mécanisme async, et jamais deux commandes de ce skill en
parallèle. Techniquement imposé par le hook `.claude/hooks/block_rag_background.py`
(voir `.claude/settings.json`). Voir `/ragpipeline`, section « Pourquoi le foreground ».

**Timeout obligatoire.** Chaque appel Bash qui lance ce script passe `timeout: 600000`
(600 s, le maximum en premier plan). Sans ce paramètre, l'outil bascule la commande en
arrière-plan après 120 s, ce qui viole la règle foreground. Le script s'arrête de lui-même
à `--budget 540` s, avant cette limite.

**Interdiction de déléguer à un sous-agent** (outil `Agent`) la lecture ou la
vérification des sorties de ce skill : elle se fait directement, dans le même tour.

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/expressions-article/scripts/expressions_article.py" "<slug|numéro>"
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/expressions-article/scripts/expressions_article.py" --all
```

Sans `<slug|numéro|--all>`, le script ne fait qu'afficher la liste numérotée filtrée (code 1).

**`--refaire`** (réanalyse, ex. après l'ajout d'un pdf au RAG, qui modifie les indications du
graphe) : la liste et `--all` portent alors sur **tous les articles publiés**, y compris ceux qui
ont déjà un rapport (marqués « rapport existant »), et le cache des appels LLM est ignoré.

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/expressions-article/scripts/expressions_article.py" --refaire
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/expressions-article/scripts/expressions_article.py" "<numéro|slug>" --refaire
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/expressions-article/scripts/expressions_article.py" --all --refaire
```

Si aucun article publié n'est sans rapport, n'affiche aucune liste, n'articule aucune question
et ne propose ni `--all` ni de slug : affiche exactement cette ligne, avec les compteurs
calculés (N = articles publiés, M = publiés ayant un rapport) :
`Aucun article publié n’est sans rapport d'expressions (N publiés, M avec rapport).`
Ne cite pas les articles non publiés, n'appelle pas `wrap_up` et ne pose pas de question :
la commande s'arrête là (code 0 avec `--all`, code 1 sans argument).

**Coût.** Chaque article demande au plus deux appels `claude -p` (extraction, puis
jugement si le graphe confirme au moins une expression), soit environ 30 à 60 s par
article. Avant un `--all`, annonce le nombre d'articles (ceux de la liste filtrée) et le
coût, puis attends le feu vert. Un slug seul ne demande pas de confirmation.

**Déroulé.** Une seule invocation à la fois : tant qu'une commande de ce skill tourne
(y compris si elle est passée en arrière-plan), aucune autre n'est lancée. Dans une
invocation traite un seul slug ; `--all` enchaîne les invocations, une par article, dans
l'ordre de la liste (voir « Cas `--all` »). Relance automatique uniquement sur code 3 ; un code 2
n'est pas relancé, il est signalé à la fin. Le graphe est chargé une fois par invocation ; la première consultation
d'une expression absente du graphe charge aussi le modèle d'embedding (une fois).

**Code de sortie 3 = travail partiel, pas une erreur.** Le script s'arrête avant la
limite de 10 minutes du premier plan (`--budget`, 540 s par défaut) et sauvegarde sa
progression dans `rag_data/expressions/work/<slug>/cache/`. Relance **exactement la même
commande** tant que le code est 3 : les appels déjà faits ne sont pas refaits.
Code 2 = erreur à lire (graphe absent, appel LLM en échec sur un article ; les autres
articles sont tout de même traités). `--refaire` ignore le cache des appels LLM.

**Rien ne doit rester caché dans les blocs Bash repliés.** Après chaque appel, recopie
dans le texte de ta réponse la sortie complète du script, telle quelle, sans la résumer.
Ne renvoie jamais l'utilisateur vers le bloc Bash (« voir ci-dessus »).

## Ce que font les scripts

Tous vivent dans `scripts/`, à côté de ce SKILL.md.

1. **`expressions_article.py`** — point d'entrée. Calcule la liste filtrée (`list_pending` :
   publiés sans `expressions.md`) ; c'est elle que numérotent les numéros et `--all`. Pour
   chaque article : charge le texte, enchaîne les étapes ci-dessous, écrit le rapport, gère
   le budget de temps et le cache. Lit `rag_data/content/work/`, `rag_data/content/published/`
   et `rag_data/work/`, écrit `rag_data/expressions/work/<slug>/`. Si la liste est vide, affiche
   la ligne de « Aucun article publié n’est sans rapport d'expressions » décrite ci-dessus.
2. **`inc_extraction.py`** — deux appels LLM via `_rag_lib/_claude_code_client.py`
   (`claude -p` headless, sans outil, via `_rag_lib/_claude_code_client_systemfile.py`) ; le `system`
   des deux appels est identique (l'article seul), pour que le cache de prompt serve au jugement :
   - *extraction* : les expressions techniques nommées dans le texte, avec une phrase
     source, et une **forme qualifiée** quand l'expression est vague hors contexte
     (« pipeline de génération » -> « pipeline de génération RAG »). Le code **vérifie**
     chaque expression, chaque passage et chaque mot ajouté mot pour mot dans l'article ;
     ce qui n'est pas dans le texte est écarté, jamais réparé.
   - *jugement* : pour chaque expression, le LLM reçoit l'article, le **catalogue des
     articles publiés** (slug et titre) et l'indication du graphe. Il décide : `nouveau`
     (avec la question à poser, ex. « Qu'est-ce qu'un pipeline de génération RAG ? »),
     `deja_traite` (avec le slug de l'article existant, même si le titre ne reprend pas
     le mot pour mot), `secondaire`, ou `ambigu`.
3. **`inc_graphe.py`** — consultation facultative de la base graphe Kuzu (lecture seule) :
   forme canonique ou alias (sans accents, sans article initial, pluriel toléré),
   type, sens, nombre de pdf et de passages, homonymes. Une expression absente est
   signalée comme telle, sans être exclue. Les concepts proches par embedding ne sont
   qu'un indice.
4. **`inc_couverture.py`** — indicateurs lexicaux, utilisés comme **vérifications** et non
   comme décision : articles dont le slug ou le titre contiennent l'expression, et
   nombre de passages mot pour mot dans `rag_data/work/*/chunks.json`.
5. **`inc_articles.py`** — lecture du frontmatter et du corps, sans marqueurs `<!--src:-->`.
   Fournit aussi `is_published(slug)` : lecture directe de la trace
   `rag_data/content/published/<slug>.json`, sans importer `/publish-content`. Le catalogue
   transmis au LLM ne contient que les articles publiés.
6. **`suggest_expressions.py`** — outil complémentaire, au niveau du corpus entier (et
   non d'un article) : parcourt le graphe, classe les concepts par couverture
   multi-pdf, et remonte les intitulés de section réels qui les portent. Lecture
   seule, sortie dans `rag_data/audit/`. Lancement :
   ```bash
   "<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/expressions-article/scripts/suggest_expressions.py"
   ```
7. **`inc_usage.py`** — comptabilité des tokens. Écrit `usage.jsonl` (une ligne par appel
   `claude -p`, ou par étape servie par le cache) et `totaltoken.jsonl` (total par article).
   Importé par `expressions_article.py` et `inc_extraction.py` ; ne se lance pas seul.
8. **`inc_indexfileupdate.py`** — met à jour `rag_data/expressions/INDEX.md` après chaque
   article analysé : une ligne (date + heure, slug, titre, expressions repérées, nouveaux
   sujets, déjà traitées, à vérifier). Tableau trié du plus récent au plus ancien, compteur
   « Articles analysés » recalculé ; une ligne existante pour le même slug est remplacée.
   Importé par `expressions_article.py` ; ne se lance pas seul.
9. **`inc_expressionsfileupdate.py`** — met à jour `rag_data/expressions/EXPRESSIONS.md` après chaque
   article analysé : une ligne par « nouveau sujet à rédiger » (Date, Slug, Sujet, Commande,
   Pourquoi, Passage), tirée de la section « Nouveaux sujets à rédiger » du rapport. Les lignes
   d'un slug sont remplacées à chaque analyse, jamais dupliquées ; un article sans nouveau
   sujet n'a plus aucune ligne. Tableau trié du plus récent au plus ancien.
   Importé par `expressions_article.py` ; ne se lance pas seul. Aucun import d'un script d'une autre commande.

## Critères de décision (ce qui devient une proposition)

Le LLM décide, pour chaque expression, parmi quatre catégories :

- **nouveau** : l'objet est identifié grâce au contexte de l'article, le corpus peut
  l'expliquer, et aucun article du catalogue ne lui est consacré. Le LLM propose la
  question, avec la forme qualifiée (« Qu'est-ce qu'un pipeline de génération RAG ? »).
- **déjà traitée** : un article du catalogue est consacré à cet objet, même si son titre
  ou son slug ne reprend pas le mot pour mot (« mémoire paramétrique » ->
  `qu-est-ce-qu-une-memoire-parametrique`). Pas de commande.
- **secondaire** : mention en passant, détail, variante d'un terme central. Pas de commande.
- **ambiguë** : le contexte ne permet pas d'identifier la notion. Pas de commande.

Les vérifications déterministes ne décident pas, elles signalent : une qualification
absente de l'article, un article au slug ou au titre proche déjà existant, un slug
« déjà traité » inconnu du catalogue, ou aucun passage du corpus nommant la notion
mettent la proposition en **à vérifier**, avec le motif. Seules les propositions
« nouveau » sans signalement reçoivent une commande.

La base graphe est consultée, mais elle n'est qu'une indication : son type, son sens et
son nombre de pdf sont donnés au LLM, qui peut s'en écarter. Une expression absente
du graphe n'est pas écartée pour autant.

Limites connues :
- Le graphe n'est pas parfait : doublons et homonymes existent, et il est signalé
  (« concepts de même forme ») pour que l'utilisateur tranche.
- Le jugement reste éditorial : chaque décision est justifiée par une phrase et un
  passage. L'utilisateur garde la décision finale et lance lui-même les commandes.
- La question proposée (accord « qu'est-ce qu'un / qu'une ») est écrite par le LLM :
  à relire avant de lancer `/create-content`.

## Sorties (dans `rag_data/expressions/work/<slug>/`)

Un dossier par article, comme `rag_data/content/work/<slug>/` :

- `expressions.md` — le rapport lisible : nouveaux sujets (avec la commande), déjà traitées,
  à vérifier, ambiguës. **Les secondaires n'y figurent pas** : elles ne sont ni affichées
  ni écrites dans le rapport, pour économiser des tokens.
- `expressions.json` — toutes les expressions, y compris les secondaires, avec leur catégorie.
- `usage.jsonl` — une ligne par appel `claude -p` (`source: "api"`, avec les tokens réels) ou
  par étape servie par le cache (`source: "cache"`, aucun token). Propre à l'article.
- `totaltoken.jsonl` — une seule ligne, réécrite à chaque lancement : `article`,
  `appels_api`, et le total de `input_tokens`, `cache_creation_input_tokens`,
  `cache_read_input_tokens`, `output_tokens` calculé depuis `usage.jsonl`.
- `cache/llm.json` — cache des appels LLM (extraction, jugement), clé = hash du texte
  de l'article et des prompts, et des preuves du graphe pour le jugement.

Hors dossier par article, mis à jour à chaque analyse dans `rag_data/expressions/` :

- `INDEX.md` — une ligne par article analysé (compteurs par catégorie).
- `EXPRESSIONS.md` — une ligne par nouveau sujet à rédiger, colonnes Date, Slug, Sujet,
  Commande, Pourquoi, Passage. Même contenu que la section « Nouveaux sujets à rédiger »
  du rapport, agrégé sur tous les articles.

## Affiche le résultat

Quand le script a terminé, sa sortie apparaît sur la sortie standard, dans cet ordre :
d'abord le rapport Markdown de chaque article, puis, en fin de sortie, un tableau des
totaux de tokens par article (« Type de tokens / Quantité »). **Recopie les deux
intégralement dans le texte de ta réponse**, en markdown rendu, sans les résumer ni les
trier ; les tableaux de totaux ne vont jamais dans un bloc de code. Pour `--all`, même règle,
article par article : voir « Cas `--all` : un article à la fois ».

## Cas `--all` : un article à la fois

Quand l'utilisateur demande `--all`, ne lance **jamais** le script sur tout le lot d'un coup :
le budget de 540 s couperait le lot en plusieurs invocations et le retour serait incomplet.
Procède article par article, dans l'ordre de la liste filtrée (même logique que
`/publish-maillage --all`) :

1. Établis la liste avec `expressions_article.py` sans argument (liste numérotée), annonce le
   nombre d'articles et le coût, puis attends le feu vert.
2. Pour chaque slug, dans l'ordre, lance `expressions_article.py <slug>` (foreground,
   `timeout: 600000`). Sur code 3, relance exactement la même commande jusqu'à obtenir
   un autre code.
3. Dès que l'article est terminé (code 0), recopie dans ta réponse son rapport
   `expressions.md` complet, sans le résumer, puis le tableau de totaux de tokens de cet
   appel. Ne passe à l'article suivant qu'après cette recopie.
4. Sur code 2 pour un article, signale l'erreur dans ta réponse et passe à l'article suivant.
5. Ne traite jamais deux articles en parallèle.
6. Pas de liste récapitulative des rapports en fin de lot : une seule ligne indique le nombre
   d'articles traités.

## Hors périmètre

- Écrire ou modifier un article, lancer `/create-content`, publier, toucher à
  WordPress.
- Corriger le graphe (fusion de concepts, homonymes) : c'est le rôle de `/rag-graphe`
  et `/rag-integrity`.
- Supprimer quoi que ce soit sous `rag_data/` (règle générale du projet).
