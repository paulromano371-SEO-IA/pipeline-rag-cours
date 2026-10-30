---
name: maillage
description: >-
  Propose le maillage interne des articles de rag_data/content/work/ : pour chaque
  article source, repère les expressions de son texte qui peuvent servir
  d'ancres vers les autres articles, en s'appuyant sur une base ChromaDB et un
  graphe Kuzu À PART construits sur les articles (chunks, concepts), puis sur la
  lecture intégrale des paires d'articles liées. Un rapport par article dans
  rag_data/maillage/. Ne modifie aucun article et ne touche pas à WordPress.
  Usage : /maillage <slug>, /maillage <numéro> ou /maillage --all.
---

# /maillage — Propositions de maillage interne

`/maillage <slug|numéro|--all>` produit, pour chaque article source, la liste
des liens internes possibles vers les autres articles de `rag_data/content/work/`.
`<slug>` est le nom d'un dossier de `rag_data/content/work/` ; un numéro désigne le
n-ième dossier dans l'ordre alphabétique (`1` = le premier) ; `--all` traite
tous les articles **publiés sans rapport de maillage** (même filtre que la liste ci-dessous ; un
article non publié n'est jamais traité par `--all`). Sans argument (ou avec un slug inconnu), liste en **liste numérotée**
(`1.`, `2.`, ... jamais à puces) **seulement les articles publiés sans rapport de maillage** :
ceux pour lesquels `rag_data/content/published/<slug>.json` existe avec `"wp_status": "publish"`
(à ne pas confondre avec `rag_data/maillage/published/`, traces de `/publish-maillage`) **et**
`rag_data/maillage/work/<slug>/<slug>.md` n'existe pas. Un article non publié (pas de fichier
`published/<slug>.json`, ou `wp_status` différent de `publish`) n'est pas listé. Demande lequel
traiter plutôt que de deviner. Chaque numéro est celui du dossier dans l'ordre alphabétique de
**tous** les dossiers de `rag_data/content/work/` (et non son rang dans la liste filtrée) : le
`<numéro>` accepté par `etape3_links.py` reste donc juste même quand des articles sont masqués.
Termine la liste par une dernière entrée « Tous les articles publiés sans rapport (`--all`) ». Si aucun article publié
n'est sans rapport, n'affiche aucune liste, n'articule aucune question et ne propose ni `--all` ni de slug : affiche
exactement cette ligne, avec les compteurs calculés (`N` = articles publiés, `M` = publiés ayant un rapport) :
`Aucun article publié n'est sans rapport de maillage (N publiés, M avec rapport).`
Ne cite pas les articles non publiés, n'appelle pas `wrap_up` et ne pose pas de question : la commande s'arrête là.

`<racine_projet>` = le dossier qui contient `.claude/` (remonte depuis ce
SKILL.md jusqu'au premier parent qui le contient ; ne suppose aucun chemin fixe).

**Avant tout traitement, affiche les paires.** Une fois le
slug (ou le numéro, ou `--all`) choisi, lance d'abord `etape3_links.py <slug|numéro|--all>
--show-pairs` : il affiche, sans aucun appel LLM, les cibles de la source en
markdown, dans la mise en forme de `rag_data/maillage/pairs/PAIRES_URLS.md` (titre
numéroté, `Source : <URL>`, nombre de cibles, puis une puce par cible
« **score** — titre — URL » suivie de `concepts : …`). `etape2_pairs.py` garde 20 cibles
par source par défaut (`--top`).

**La sortie doit apparaître DANS LE TEXTE DE TA RÉPONSE, pas seulement dans le bloc
Bash.** Le bloc d'outil est replié et défile : l'utilisateur ne le lit pas. Après
l'appel, recopie intégralement, dans ton message, tout ce que le script a affiché à
partir de la ligne `## <n>. <titre>` (une réponse qui renvoie au bloc Bash par
« ci-dessus », « dans la sortie » ou un résumé est une **erreur**). Écris-la en
markdown brut, sans bloc de code, pour qu'elle s'affiche rendue (titre, puces,
liens), sans la reformater, la trier ni la raccourcir. Ne t'arrête pas pour demander
confirmation : enchaîne directement avec la suite ci-dessous.

**Après l'affichage, réaffiche les paires si elles ont été recalculées.** Lance
`etape1_build_index.py` puis `etape2_pairs.py` (voir « Exécution »). Si des articles ont été
ajoutés ou modifiés dans `rag_data/content/work/`, ou si un réglage de `etape2_pairs.py` a
changé, les paires de la source ont pu changer. Quand `etape2_pairs.py` a terminé, relance
`etape3_links.py <slug|numéro|--all> --show-pairs` et recopie sa sortie dans ta réponse
(mêmes règles que ci-dessus), précédée d'une ligne qui dit si les paires ont changé
par rapport à la première liste (ex. « 7 → 10 cibles ») ou sont restées identiques.
**Enchaîne ensuite directement avec `etape3_links.py`, sans redemander de confirmation.**

## Interpréteur Python

Tout script de ce skill se lance **uniquement** avec
`"<racine_projet>/.venv-rag/Scripts/python.exe"`, jamais avec `python`/`py`
(Python système). Techniquement imposé par un hook `PreToolUse` sur `Bash` et
`PowerShell` (`.claude/hooks/block_system_python.py`, voir
`.claude/settings.json`), qui refuse toute commande lançant un script de
`.claude/skills/*/scripts/` ou `tools/` avec `python`/`py`. Voir `/ragpipeline`,
section « Interpréteur Python ». `<racine_projet>` = le dossier contenant
`.claude/`.

## Exécution

Toujours en foreground, bloquant jusqu'à complétion — jamais via
`run_in_background` ni aucun mécanisme async, et jamais deux commandes du
pipeline en parallèle. Techniquement imposé par un hook `PreToolUse` sur `Bash`
(`.claude/hooks/block_rag_background.py`, voir `.claude/settings.json`), qui
refuse tout `run_in_background: true` sur une commande référençant un script
d'un skill du projet. Voir `/ragpipeline`, section « Pourquoi le foreground ».

**Interdiction de déléguer à un sous-agent** (outil `Agent`) la lecture ou la
vérification des sorties de ce skill : elle se fait directement, dans le même
tour de conversation, jamais « pour économiser du contexte ».

Trois scripts, à enchaîner dans cet ordre (chacun est incrémental : relancé sans
rien de nouveau, il répond « déjà à jour ») :

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/maillage/scripts/etape1_build_index.py"
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/maillage/scripts/etape2_pairs.py"
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/maillage/scripts/etape3_links.py" "<slug|numéro>"
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/maillage/scripts/etape3_links.py" --all
```

**Code de sortie 3 = travail partiel, pas une erreur** : `etape1_build_index.py` et
`etape3_links.py` s'arrêtent avant la limite de 10 minutes du premier plan et
sauvegardent leur progression ; relance **exactement la même commande** tant que
le code est 3 (la progression est en cache). Code 2 = bloquant, à lire. Après
modification, ou ajout, d'un article, relance `etape1_build_index.py` : seuls les
articles modifiés sont recalculés (le graphe est reconstruit depuis les caches).

**Rien ne doit rester caché dans les blocs Bash repliés.** Après l'affichage des paires,
exécute les scripts un par un et, **après CHAQUE appel** (`etape1_build_index.py`, puis
`etape2_pairs.py`, puis chaque lancement de `etape3_links.py`), recopie dans le texte de ta
réponse la sortie complète du script, telle quelle, sans la résumer, **avant**
de passer à l'appel suivant. Les scripts n'impriment ni avertissement ni barre
de chargement : leur sortie brute est lisible, ne la filtre pas. Ne renvoie
jamais l'utilisateur vers le bloc Bash (« voir ci-dessus », « sortie
ci-dessus »). Pour `etape2_pairs.py` (une ligne par source), recopie toutes les lignes.

**Affiche le résultat à la fin.** Quand `etape3_links.py` a terminé pour un article
(sortie « N liens retenus »), lis `rag_data/maillage/work/<dossier>/<dossier>.md` et affiche
son contenu **dans le texte de ta réponse**, en markdown rendu : ce sont les
liens proposés, avec ancre, cible, preuve, section et phrase de contexte, puis
les cibles lues sans lien. Pour `--all`, affiche à la fin le tableau de
`rag_data/maillage/INDEX.md` et indique où sont les rapports, sans recopier les
29 rapports ; propose d'afficher ceux que l'utilisateur choisira.

## Ce que font les scripts

Tout vit à part du RAG des pdf : `rag_data/maillage/db/vector/` (Chroma,
collection `articles`) et `rag_data/maillage/db/graph/` (Kuzu). Les bibliothèques
de `_rag_lib` et `entity_resolution.py` de `/rag-graphe` sont utilisées en
lecture seule ; aucun fichier partagé n'est modifié.

1. **`etape1_build_index.py`** — pour chaque article : chunking (frontmatter et
   marqueurs `<!--src:...-->` retirés), index Chroma, extraction des concepts par
   chunk (`claude -p`, cache par hash du texte), puis graphe de concepts. La
   résolution d'entités est celle de `/rag-graphe` (mêmes seuils), avec deux
   écarts assumés : (a) fusion automatique ≥ 0,92 même entre articles
   (`document_id=None`), car tous traitent d'IA/ML ; (b) arbitrages « même concept ou
   non » jugés **en lots de 20 paires** par appel au lieu d'une paire par appel
   (accord de 98 % avec le jugement paire par paire, `--calibrate N`), par tours
   de simulation en mémoire jusqu'à ce que toutes les décisions soient en cache.
   Contrôle d'intégrité Kuzu à la fin. Les embeddings des mentions sont mis en
   cache sur disque. Option `--status` : avancement, ne modifie rien.
2. **`etape2_pairs.py`** — classe, pour chaque source, les cibles qui traitent EN
   PROFONDEUR de concepts que la source mentionne. Un concept est *central* dans
   une cible s'il figure dans ≥ 3 de ses chunks (≥ 2 s'il est dans son titre ou
   un H2) : une notion citée une fois ne crée aucune paire. Score pondéré par la
   rareté du concept (idf) ; doublons non fusionnés rattrapés par similarité
   d'embedding. Écrit `rag_data/maillage/pairs/` (dont `centralite.json`, pour
   auditer ce que le graphe pense de chaque article).
3. **`etape3_links.py`** — un appel `claude -p` par paire retenue : le LLM lit la
   source et la cible EN ENTIER et propose au plus 2 ancres ; il doit citer un
   passage exact de la cible prouvant que son sujet central est celui de l'ancre.
   **Jamais d'ancre dans un titre** (règle SEO : pas de lien dans les balises H) :
   les titres `#` à `######` et le **Sommaire** (étiquette `**Sommaire**` + sa liste
   de liens) ne sont jamais proposés au LLM comme texte à lier. Les blocs **FAQ**
   (questions en gras et réponses) ne sont **jamais** exclus.
   L'ancre est un groupe nominal ou un infinitif avec son complément qui NOMME le
   sujet (« évaluer un RAG »). Les sections « À retenir »,
   « L'essentiel en 30 secondes » et la FAQ sont du texte comme un autre : une
   ancre qui s'y trouve est valable, même seule occurrence du sujet. Le code
   vérifie que l'ancre et la citation existent mot pour mot. Un second appel, **indépendant**, vérifie pour chaque
   lien que l'ancre NOMME le sujet de la page cible (et non une composante,
   un outil ou un chapitre). Le code tranche enfin entre pages sœurs
   (adéquation puis score de paire) et applique les quotas : **règle absolue, un seul
   lien par URL cible** (deux liens d'une même source vers la même page sont
   interdits), 2 liens max par paragraphe, jamais deux fois la même ancre. Zéro lien est une
   réponse normale : le rappel est volontairement modeste, la précision visée haute.

## Sorties (dans `rag_data/maillage/`)

Tout ce qui concerne un article vit dans son dossier `work/<slug>/` :

- `work/<slug>/<slug>.md` : rapport lisible — pour chaque lien : ancre, adéquation, raison,
  citation de la cible, section, phrase de contexte avec l'ancre en gras ; puis
  les cibles lues sans lien et pourquoi.
- `work/<slug>/<slug>.json` : mêmes liens, exploitables par un script (`target`,
  `target_url`, `anchor`, `section`, `paragraph_index`, `context`, `adequation`,
  `preuve_cible`, `reason`) + `cibles_examinees` (motif sans lien et propositions
  rejetées, y compris par la critique, pour auditer la grille).
- `work/<slug>/usage.jsonl` : une ligne par appel `claude -p` rattaché à cet article (etape4_usage.py),
  réussi ou non : extraction de ses concepts, propositions de liens, critiques.
  `source` : `api` (appel réel, avec `statut` ok/erreur, coût, tokens) ou `cache` (résultat repris, sans appel).
  `type_appel` : `extraction_concepts`, `proposition_lien`, `critique_liens` (`non_classe` = appel hors bloc,
  à investiguer). `etape` : `concepts` ou `liens` ; `script` : `etape1_build_index` ou `etape3_links` ;
  `essai` : 1 = premier essai, 2 et plus = relance dans le même bloc. Pour le coût total : additionner
  `total_cost_usd` des lignes `api`.
- `work/<slug>/totaltoken.jsonl` : une seule ligne, réécrite à chaque appel API : `article`, `appels_api`
  (nombre d'appels réels de `etape3_links`, les liens ; les appels d'extraction de concepts de
  `etape1_build_index` ne sont pas comptés) et le total de chacun des 4 types de tokens : `input_tokens`,
  `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`.
- `rag_data/maillage/usage.jsonl` : appels partagés entre articles, qui n'appartiennent à aucun slug
  (arbitrages du graphe `arbitrage_lot`, `arbitrage_unitaire`, `calibrage_lot`, tous issus de `etape1_build_index`). Même format que ci-dessus.
- `rag_data/maillage/totaltoken.jsonl` : une seule ligne, réécrite à chaque appel API du graphe, calculée depuis
  `rag_data/maillage/usage.jsonl` : `portee` = `graphe`, `appels_api` et les 4 totaux de tokens.
- `INDEX.md` : tableau de tous les articles (un par ligne, `work/<slug>/`), avec trois colonnes Oui/Non lues sur le disque :
  - **Fichiers d'indexation** : `work/<slug>/chunks.json` et `state.json` présents (renseigné par cette commande) ;
  - **Fichiers de maillage** : `work/<slug>/<slug>.md` et `<slug>.json` présents (renseigné par cette commande) ;
  - **Liens publiés** : `published/<slug>.json` avec au moins un lien posé. Non par défaut ; passe à Oui quand `/publish-maillage` applique le maillage sur l'article.
  Régénérer seul, sans appel LLM : `etape3_links.py --index`. `pairs/INDEX.md` : paires par source.
- `db/`, `work/`, `pairs/` : bases et caches (ne rien y supprimer à la main).
  `_profils.json` : reliquat d'une ancienne méthode, inutilisé.

La commande est en **lecture seule** sur les articles : appliquer les liens
dans `article.md` ou sur WordPress est une étape distincte, à demander
explicitement.
