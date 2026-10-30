# expressions-article : expressions à transformer en contenu dédié

Ce document explique simplement ce que fait la commande `/expressions-article`, comment l'utiliser et à quoi sert chaque script.

Le fichier `SKILL.md`, à côté de celui-ci, est la consigne lue par Claude Code. Ce README est la documentation pour un humain. En cas de désaccord, le code fait foi.

---

## 1. À quoi sert la commande

Pour un article de `rag_data/content/work/<slug>/`, la commande répond à une question : **quelles expressions du texte sont assez importantes pour mériter leur propre article, et lesquelles ont déjà le leur ?**

Pour chaque expression, elle attribue une catégorie :

| Catégorie | Signification | Commande proposée ? |
|---|---|---|
| **nouveau** | Le sujet est identifié, le corpus peut l'expliquer, et aucun article ne lui est consacré. | Oui : `/create-content` avec la question suggérée. |
| **déjà traitée** | Un article publié est consacré à ce sujet, même si son titre ne reprend pas le mot. | Non. |
| **secondaire** | Mention en passant, détail, variante d'un terme central. | Non. Elle n'apparaît ni dans le rapport ni dans l'affichage. |
| **ambiguë** | Le contexte ne permet pas d'identifier la notion. | Non. |

Une proposition « nouveau » peut être signalée **à vérifier** : qualification absente de l'article, article au slug ou au titre proche déjà existant, slug « déjà traité » inconnu du catalogue, ou aucun passage du corpus nommant la notion. Seules les propositions « nouveau » sans signalement reçoivent une commande.

**La commande ne modifie aucun article ni aucune base** : ni la base graphe (`rag_data/db/graph/`), ni la base vectorielle. Elle écrit seulement dans `rag_data/expressions/`. Elle ne lance **aucune** `/create-content` : c'est vous qui décidez lesquelles lancer.

---

## 2. Utilisation

Depuis Claude Code, à la racine du projet :

| Commande | Effet |
|---|---|
| `/expressions-article` | Liste numérotée des articles publiés sans rapport d'expressions. Rien n'est lancé tant que vous n'avez pas choisi. |
| `/expressions-article <slug>` | Analyse un article. Un slug est accepté même hors de la liste. |
| `/expressions-article <numéro>` | Analyse l'article à ce numéro dans la liste affichée. |
| `/expressions-article --all` | Analyse tous les articles de la liste. Annonce d'abord le nombre d'articles et le coût, puis attend votre feu vert. |
| `/expressions-article --refaire` | Liste **tous** les articles publiés, y compris ceux qui ont déjà un rapport (marqués « rapport existant »). Rien n'est lancé. |
| `/expressions-article <numéro ou slug> --refaire` | Réanalyse un article, même s'il a déjà un rapport. Le cache des appels LLM est ignoré. |
| `/expressions-article --all --refaire` | Réanalyse tous les articles publiés. Même annonce de coût et même feu vert que `--all`. |

**Quand utiliser `--refaire`.** Après l'ajout d'un pdf au RAG, les passages et le graphe changent : un ancien rapport peut donc ne plus être juste. Sans `--refaire`, un article déjà analysé n'est jamais réexaminé. Pour réanalyser tous les articles, tapez `--all --refaire`, et non `--refaire` suivi de `--all` : ce dernier ne prendrait que les articles sans rapport.

**La liste.** Un article y figure s'il est publié (trace `rag_data/content/published/<slug>.json`) et s'il n'a pas encore de rapport (`rag_data/expressions/work/<slug>/expressions.md` absent). Avec `--refaire`, la liste contient tous les articles publiés. Les numéros suivent la liste du moment : après une analyse, l'article analysé sort de la liste et les numéros suivants se décalent.

**Rien à traiter.** Si la liste est vide, la commande n'affiche ni liste ni question, et ne propose ni `--all` ni de slug. Elle affiche seulement :

> Aucun article publié n’est sans rapport d'expressions (N publiés, M avec rapport).

N est le nombre d'articles publiés, M celui de ces articles qui ont déjà un rapport. Sans argument, la commande sort avec le code 1 ; avec `--all`, avec le code 0.

Ligne de commande directe :

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/expressions-article/scripts/expressions_article.py" <slug|numéro>
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/expressions-article/scripts/expressions_article.py" --all
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/expressions-article/scripts/expressions_article.py" --refaire
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/expressions-article/scripts/expressions_article.py" <numéro|slug> --refaire
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/expressions-article/scripts/expressions_article.py" --all --refaire
```

`<racine_projet>` est le dossier qui contient `.claude/`.

**Interpréteur Python.** Tous les scripts se lancent uniquement avec `.venv-rag/Scripts/python.exe`, jamais avec `python` ni `py`. Un hook refuse les autres lancements.

---

## 3. Les étapes

Le point d'entrée est `expressions_article.py`. Pour chaque article, il enchaîne les étapes ci-dessous, écrit le rapport, gère le budget de temps et le cache.

### Étape A : extraction des expressions (appel LLM)

Le modèle relève les expressions techniques nommées dans le texte (méthodes, outils, concepts, métriques, acronymes), chacune avec une **phrase source**. Quand une expression est vague hors contexte, il donne une **forme qualifiée** : « pipeline de génération » devient « pipeline de génération RAG ».

Le code **vérifie mot pour mot** chaque expression, chaque passage et chaque mot ajouté. Ce qui n'est pas dans le texte est écarté, jamais réparé.

### Étape B : jugement (second appel LLM)

Pour chaque expression, le modèle reçoit l'article, le **catalogue des articles publiés** (slug et titre) et une indication du graphe. Il décide parmi les quatre catégories ci-dessus. Pour une catégorie « déjà traitée », il donne le slug de l'article existant, même si le titre ne reprend pas le mot pour mot. Exemple : « mémoire paramétrique » renvoie vers `qu-est-ce-qu-une-memoire-parametrique`.

Les deux appels partagent le même `system` (l'article seul), pour que le cache de prompt serve au jugement.

### Étape C : consultation du graphe (facultative)

Lecture seule de la base graphe Kuzu. Le script trouve la forme canonique ou un alias (sans accents, sans article initial, pluriel toléré), puis donne le type, le sens, le nombre de pdf et de passages, et les homonymes.

Une expression **absente du graphe n'est pas écartée** : elle est signalée comme telle. Le graphe est consultatif : le modèle peut le contredire, et il ne décide jamais.

### Étape D : vérifications lexicales

Ces indicateurs servent de **vérifications**, pas de décision :

- les articles dont le slug ou le titre contient l'expression ;
- le nombre de passages qui la contiennent mot pour mot, dans `rag_data/work/*/chunks.json`.

Ils alimentent la mention « à vérifier ».

### Étape E : lecture des articles

Lecture seule du frontmatter et du corps de chaque article. Les marqueurs `<!--src:...-->` sont retirés. Seuls les articles publiés entrent dans le catalogue transmis au modèle à l'étape B.

---

## 4. Les critères de décision

Le LLM décide, pour chaque expression, parmi quatre catégories :

- **nouveau** : l'objet est identifié grâce au contexte de l'article, le corpus peut l'expliquer, et aucun article du catalogue ne lui est consacré. Le LLM propose la question, avec la forme qualifiée (« Qu'est-ce qu'un pipeline de génération RAG ? »).
- **déjà traitée** : un article du catalogue est consacré à cet objet, même si son titre ou son slug ne reprend pas le mot pour mot. Pas de commande.
- **secondaire** : mention en passant, détail, variante d'un terme central. Pas de commande.
- **ambiguë** : le contexte ne permet pas d'identifier la notion. Pas de commande.

**Limites connues :**

- Le graphe n'est pas parfait : doublons et homonymes existent, et ils sont signalés (« concepts de même forme ») pour que vous tranchiez.
- Le jugement reste éditorial : chaque décision est justifiée par une phrase et un passage. Vous gardez la décision finale.
- La question proposée (accord « qu'est-ce qu'un / qu'une ») est écrite par le LLM : relisez-la avant de lancer `/create-content`.

---

## 5. Sorties

Un dossier par article, dans `rag_data/expressions/work/<slug>/` :

| Fichier | Contenu |
|---|---|
| `expressions.md` | Le rapport lisible : nouveaux sujets (avec la commande), déjà traitées, à vérifier, ambiguës. **Les secondaires n'y figurent pas.** |
| `expressions.json` | Toutes les expressions, y compris les secondaires, avec leur catégorie. |
| `usage.jsonl` | Une ligne par appel `claude -p` (`source: "api"`, avec les tokens réels) ou par étape servie par le cache (`source: "cache"`, aucun token). |
| `totaltoken.jsonl` | Une seule ligne, réécrite à chaque lancement : `article`, `appels_api`, et les totaux de tokens. |
| `cache/llm.json` | Cache des appels LLM. La clé est le hash du texte de l'article et des prompts, et des preuves du graphe. |

Hors dossier par article, mis à jour à chaque analyse dans `rag_data/expressions/` :

| Fichier | Contenu |
|---|---|
| `INDEX.md` | Une ligne par article analysé, avec les compteurs par catégorie. |
| `EXPRESSIONS.md` | Une ligne par nouveau sujet à rédiger : Date, Slug, Sujet, Commande, Pourquoi, Passage. Même contenu que la section « Nouveaux sujets à rédiger » du rapport, agrégé sur tous les articles. |

---

## 6. Codes de sortie et coût

| Code | Signification |
|---|---|
| 0 | Terminé. |
| 1 | Usage incorrect (sans argument : liste numérotée, rien n'est lancé). |
| 2 | Erreur à lire : graphe absent, ou appel LLM en échec sur un article. Les autres articles sont tout de même traités. |
| 3 | **Temps écoulé, pas une erreur.** Relancer **exactement la même commande** : le cache reprend là où le script s'est arrêté. |

**Coût.** Chaque article demande au plus deux appels `claude -p` (extraction, puis jugement si au moins une expression est relevée). Comptez environ 30 à 60 secondes par article. Le graphe est chargé une fois par lancement. La première consultation d'une expression absente du graphe charge aussi le modèle d'embedding, une seule fois.

**Budget.** Le script s'arrête de lui-même après `--budget` secondes (540 par défaut), avant la limite de 10 minutes du premier plan. Il vérifie le budget entre deux appels LLM ; un article interrompu n'a pas de rapport, et le cache garde ce qui est déjà fait.

**Exécution.** Toujours en premier plan, sans `run_in_background`, et jamais deux commandes de ce skill en parallèle. Chaque appel Bash doit passer `timeout: 600000`, sinon l'outil bascule la commande en arrière-plan après 120 secondes. Un hook refuse les lancements en arrière-plan.

**Options :**

- `--budget N` : secondes avant arrêt propre (540 par défaut).
- `--refaire` : ignore le cache des appels LLM et refait les appels. Élargit aussi la liste à tous les articles publiés.

---

## 7. Scripts

Tous vivent dans `scripts/`, à côté de ce README.

| Script | Rôle |
|---|---|
| `expressions_article.py` | **Point d'entrée.** Calcule la liste filtrée, enchaîne les étapes pour chaque article, écrit le rapport, gère le budget et le cache. |
| `inc_extraction.py` | Deux appels LLM : extraction des expressions, puis jugement. Passe par `_rag_lib/_claude_code_client_systemfile.py`, sans outil. |
| `inc_graphe.py` | Consultation facultative de la base graphe Kuzu, en lecture seule. |
| `inc_couverture.py` | Indicateurs lexicaux, utilisés comme vérifications et non comme décision. |
| `inc_articles.py` | Lecture du frontmatter et du corps des articles, et contrôle de publication (`is_published`). |
| `inc_usage.py` | Comptabilité des tokens (`usage.jsonl`, `totaltoken.jsonl`). Importé par les autres scripts ; ne se lance pas seul. |
| `inc_indexfileupdate.py` | Met à jour `INDEX.md` après chaque article analysé. Importé par `expressions_article.py` ; ne se lance pas seul. |
| `inc_expressionsfileupdate.py` | Met à jour `EXPRESSIONS.md` après chaque article analysé. Importé par `expressions_article.py` ; ne se lance pas seul. |
| `suggest_expressions.py` | **Outil complémentaire, hors pipeline**, au niveau du corpus entier et non d'un article. Lecture seule. Classe les concepts du graphe par couverture multi-pdf et remonte les intitulés de section réels qui les portent. Sortie dans `rag_data/audit/`. |

Lancement de `suggest_expressions.py` :

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/expressions-article/scripts/suggest_expressions.py"
```

Ses candidats sont des indices, à confirmer par une vraie recherche au moment de `/create-content`.

---

## 8. Ce que la commande n'est pas

- Elle **n'écrit ni ne modifie** aucun article.
- Elle **ne lance pas** `/create-content` et ne publie rien, ne touche pas à WordPress.
- Elle **ne corrige pas le graphe** : fusion de concepts et homonymes relèvent de `/rag-graphe` et `/rag-integrity`.
- Elle **ne supprime rien** sous `rag_data/`.
