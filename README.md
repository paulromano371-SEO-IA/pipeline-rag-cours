# ![](/images/logo.png)

## Rag4Wordpress est né de l'idée de rendre accessible l'apprentisage de l'IA.

![](/images/1ereimage.png)

Cette solution transforme des pdfs techniques complexes en **supports de cours condensé pédagogiques de grande qualité et en français**, puis ingère ces supports dans une base de connaissance interrogeable : recherche vectorielle (RAG classique)  et graphe de concepts (GraphRAG), le tout via une série de [skills Claude Code](/tree/main/.claude/skills) enchaînés.

**Objectif** : vulgariser des pdfs techniques complexes pour en tirer des contenus web très pédagogiques et uniques. Le corpus condensé et ingéré est la seule source de faits : chaque article est rédigé à partir de ce que contiennent réellement les supports condensés, puis envoyé sur un site WordPress.

## Vue d'ensemble du pipeline

```
pdf (corpusdedepart/)
      │
      ▼
1. /cours-condense   →  cours condensé en français, PDF (corpuscondense/)
      │
      ▼
2. /rag-extraction   →  markdown pivot structuré + images extraites du cours condensé
      │
      ▼
3. /rag-nottext       →  description en langage naturel des images, du code et des formules
      │
      ▼
4. /rag-chunking     →  découpage en chunks embeddables
      │
      ▼
5. /rag-index        →  indexation vectorielle (base Chroma)
      │
      ▼
6. /rag-concepts     →  extraction des concepts de chaque chunk
      │
      ▼
7. /rag-graphe       →  résolution des concepts en entités, graphe de connaissances (Kuzu)
      │
      ▼
8. /rag-integrity    →  audit de qualité du graphe (à la demande, transversal au corpus)
      │
      ▼
9. /expressions-article  →  détection des expressions méritant un article dédié, vérifiées dans le graphe
      │
      ▼
10. /create-content  →  création d'un article web pédagogique (Markdown, frontmatter SEO, images)
      │
      ▼
11. /publish-content →  publication d'un article en brouillon WordPress (images, texte alternatif, légendes)
      │
      ▼
12. /maillage        →  propositions de liens internes entre articles
      │
      ▼
13. /publish-maillage →  publication des liens internes dans les articles déjà en ligne
```

Les étapes 1 à 7 forment l'**ingestion** d'un pdf dans la base RAG. Les étapes 8 à 13 exploitent ensuite ce corpus : audit, rédaction d'articles, publication et maillage.

Chaque étape est une commande indépendante (un skill Claude Code), invocable seule. Les étapes 1 à 7 peuvent être enchaînées automatiquement par l'orchestrateur `/ragpipeline`.

## Les étapes en détail

### 1. `/cours-condense` — Génération du cours condensé

`/cours-condense <chemin_vers_livre.pdf>`

Lit le pdf source **intégralement** (jamais un extrait) et produit un support de cours condensé en français, compilé en PDF via LaTeX. Le texte est réécrit dans un ton pédagogique, le code source est repris à l'identique (jamais reformulé), et des illustrations vectorielles originales sont créées pour appuyer les explications. Aucune image du pdf source n'est reprise. Aucune longueur cible n'est fixée à l'avance : le plan et la compression découlent uniquement du contenu réellement essentiel du pdf.

**Exemple de support condensé** : 

![](/images/exemple_titre_support_condensé.png)

![](/images/exemple_sommaire_support_condensé.png)

### 2. `/rag-extraction` — Conversion en markdown pivot

`/rag-extraction <chemin_vers_pdf_condense>`

Convertit le PDF condensé (sortie de l'étape 1) en un markdown "pivot" qui distingue proprement titres, code et prose, et extrait les images du cours condensé. Une réparation automatique des ligatures typographiques cassées (ex. "ﬁ", "ﬂ") est appliquée si nécessaire. Traite aussi la détection de formules mathématiques mal ordonnées par l'extraction PDF brute.

### 3. `/rag-nottext` — Description des éléments non-textuels

`/rag-nottext <pdf_condense_ou_document_id>`

Pour chaque image, bloc de code et formule d'affichage (déjà en LaTeX) produits à l'étape 2 : description en langage naturel, OCR/renommage adapté pour les images (classification formule mathématique vs image générale, LaTeX via pix2tex ou texte via tesseract, nom de fichier explicite), puis injection de cette description dans le markdown pivot, juste après l'élément. Un modèle d'embedding texte ne rapproche quasiment jamais une question en français d'un verbatim LaTeX, code ou image brut — vérifié empiriquement — donc sans cette étape, ce contenu reste invisible à la recherche vectorielle.

### 4. `/rag-chunking` — Découpage en chunks

`/rag-chunking <pdf_condense_ou_document_id>`

Découpe le markdown pivot enrichi en chunks d'environ 400 tokens (avec recouvrement), sans jamais couper un bloc de code, une image ou une formule au milieu. Pour un élément décrit par `/rag-nottext`, le texte réellement embeddé (calculé à l'étape suivante) ne retient que sa description en langage naturel — jamais le verbatim brut — pour ne pas diluer la similarité avec une question en français. Les sections structurelles (table des matières, sommaire, index, bibliographie...) sont exclues du découpage : elles restent dans `pivot.md` mais n'engendrent aucun chunk, donc rien n'est indexé, envoyé à l'extraction de concepts ni relié au graphe. Étape purement déterministe (basée sur le tokenizer réel du modèle d'embedding, `BAAI/bge-m3`), aucun appel à un modèle de langage.

### 5. `/rag-index` — Indexation vectorielle

`/rag-index <pdf_condense_ou_document_id>`

Calcule les embeddings de chaque chunk (modèle multilingue local `BAAI/bge-m3` via `sentence-transformers`, sans clé API externe) et les indexe dans une base vectorielle Chroma partagée entre tous les documents du corpus. Les chunks de texte "bruité" (motifs mal extraits d'un diagramme) sont filtrés automatiquement.

### 6. `/rag-concepts` — Extraction des concepts

`/rag-concepts <pdf_condense_ou_document_id>`

Pour chaque chunk indexable, un appel `claude -p` headless extrait 3 à 8 concepts significatifs (nom, forme canonique, type et définition courte `sense` : ce que le mot désigne dans ce passage, pour distinguer les homonymes d'un pdf à l'autre, par exemple `lambda` en Python ou en régularisation). Cette étape prépare la construction du graphe de connaissances de l'étape suivante.

### 7. `/rag-graphe` — Construction du graphe de connaissances (GraphRAG)

`/rag-graphe <pdf_condense_ou_document_id>`

Résout chaque concept extrait en comparant sa similarité d'embedding (forme canonique + définition) aux concepts déjà connus du graphe (commun à tout le corpus) :

- similarité ≥ 0.92 et concept déjà vu dans ce même document : fusion automatique avec un concept existant,
- similarité < 0.80 : nouveau concept,
- entre les deux, ou ≥ 0.92 vers un concept vu seulement dans d'autres pdfs : arbitrage par un appel `claude -p` dédié, sur les 3 concepts les plus proches, avec la définition, le pdf et un extrait du chunk de chaque côté.

C'est cette étape qui permet de repérer qu'un même concept est traité dans plusieurs pdfs différents — l'intérêt principal du GraphRAG par rapport à une recherche vectorielle seule.

Une fois le graphe du document terminé, une **consolidation** du graphe partagé (`rag-graphe/scripts/consolidate.py`, lancée par le skill, par lots) rattrape les doublons restants : les paires de concepts à similarité ≥ 0.80 sont jugées par `claude -p` et fusionnées si besoin, puis chaque concept reçoit pour forme canonique son nom le plus fréquent dans le corpus.

### 8. `/rag-integrity` — Audit du graphe

`/rag-integrity`

Hors pipeline et transversal au corpus, à lancer à la demande (par exemple après l'ajout d'un pdf). En deux étapes : `report.py` calcule de façon déterministe les métriques du graphe (volumes par pdf, hubs, alias suspects, fusions manquées ou à tort, doublons) et repère les cas ambigus avec des extraits de chunks réels ; puis `audit_lot.py` fait juger ces cas par `claude -p`, par lots. Les rapports, l'historique et le registre des décisions sont écrits sous `rag_data/audit/`. Cette commande ne fait qu'auditer l'intégrité du graphe RAG et ne modifie jamais rien.

### 9. `/expressions-article` — Expressions à approfondir

`/expressions-article <slug>` (ou `<numéro>`, ou `--all`)

Commande qui repère dans un article de `rag_data/content/work/` les expressions (méthodes, outils, concepts, métriques, acronymes) qui méritent un contenu dédié. Chacune est vérifiée dans la base graphe (existe-t-elle dans le corpus, et de quel type est-elle ?), puis dans les articles existants (déjà traitée, simplement mentionnée, ou absente). Le skill propose les expressions trouvées à la commande suivante `/create-content` .

### 10. `/create-content` — Rédaction d'un article web

`/create-content <expression>` (ex. `/create-content comment fonctionne une régression logistique ?`)

Cette commande exploite le corpus déjà ingéré pour rédiger **un article web pédagogique** (Markdown + frontmatter SEO + images) sur une expression donnée. Le RAG est la **seule source de faits** : Claude ne fait que le travail éditorial (angle, progression, pédagogie) et n'apporte jamais une affirmation technique tirée de ses connaissances générales. Ce que le corpus ne couvre pas est signalé dans le rapport final, jamais comblé de mémoire.

Le skill procède en 5 étapes, toujours en foreground :

1. **Constitution du dossier** — 5 à 8 sous-requêtes (définition, intuition, formule, apprentissage, code, interprétation, limites, voisins) passées à `gather.py`, qui appelle `retrieval.retrieve()`, fusionne et dédoublonne les chunks, puis écrit `dossier.md` / `dossier.json` et copie les images. Il mesure aussi la couverture multi-pdfs et repère les chunks à « signal distinctif » (chiffre précis, entité nommée) pour aider à produire un contenu non générique. Lecture seule, aucun appel à un modèle de langage.
2. **Conception** — lecture complète du dossier, choix d'une idée directrice, d'un fil conducteur concret et d'une progression du concret vers l'abstrait.
3. **Rédaction** de `article.md` selon une trame : accroche, l'essentiel en 30 secondes, intuition, formalisation, code, lecture des résultats, pièges, pour aller plus loin, à retenir. Tutoiement imposé, aucune longueur cible.
4. **Vérification** par `verify.py` (déterministe, bloquante) : code repris à l'identique du dossier, images et formules issues du dossier, citations de traçabilité `<!--src:Sxxx-->` valides, aucune fuite de la mécanique interne (« chunk », identifiants), structure du frontmatter, absence de vouvoiement. Elle émet aussi des avertissements (couverture non utilisée, paragraphes trop longs, variable de code possiblement non définie, légende d'image incohérente, faible part de matière distinctive).
5. **Relecture éditoriale** (accroche, fact-check, notation unique, code, légendes) puis rapport final : couverture, lacunes du corpus, décisions éditoriales et verdict commodity / non-commodity.

Sortie : `rag_data/content/work/<slug>/` (`article.md`, `dossier.md`, `dossier.json`, `images/`, `plan.md`), plus une ligne dans `rag_data/content/INDEX.md`. Le skill ne modifie jamais un autre contenu de `rag_data/content/work/` et n'écrit jamais dans les bases.

**Exemple de contenu crée** :

![](/images/contenu_cree_par_create_content.png) 

### 11. `/publish-content` — Publication sur WordPress

`/publish-content <slug>` (le slug est le nom du dossier sous `rag_data/content/work/`)

Cette commande envoie sur un site WordPress, via l'API REST, un article produit par `/create-content`. L'article est **toujours créé en brouillon** : aucune option ne permet de le publier directement, la mise en ligne se fait à la main dans WordPress.

- **Images** : chacune est uploadée avec son texte alternatif (celui de l'article), sa légende (la phrase qui suit l'image, retirée du corps pour éviter le doublon) et sa description détaillée issue du pipeline RAG. Aucun champ ne mentionne la source (pdf, section). Une image déjà présente dans la médiathèque (même nom de fichier) est réutilisée au lieu d'être ré-uploadée ; `--no-check-existing` force l'upload.
- **Contenu** : Markdown converti en HTML avec blocs image Gutenberg, commentaires de traçabilité `<!--src:...-->` retirés, titre, extrait (description SEO) et slug repris du frontmatter. Les formules LaTeX sont préservées et le shortcode `[latexpage]` ajouté pour le plugin QuickLaTeX du site.
- **Procédure** : toujours lancer d'abord `--dry-run` (rien n'est écrit sur le site, il affiche ce qui serait envoyé et quelles images seraient réutilisées), relire, puis relancer sans l'option.
- **Identifiants** : copier `.claude/skills/publish-content/scripts/.env.example` vers `.env` dans le même dossier et renseigner `WP_URL`, `WP_USER` et `WP_APP_PASSWORD` (mot de passe d'application WordPress, pas le mot de passe de connexion). Ce `.env` est exclu du dépôt via `.gitignore`.

**Exemple de contenu publié** :

![](/images/exemple_contenu_web_avec_create_content.png) 

### 12. `/maillage` — Propositions de maillage interne

`/maillage <slug>` (ou `<numéro>`, ou `--all`)

Cette commande construit une base vectorielle et un graphe Kuzu **à part** ( totalement indépendant de celles des cours condensés) sur les articles web crées. Pour chaque article crée, repère les expressions de son texte qui peuvent servir d'ancres vers les autres articles, puis fait relire chaque paire d'articles liée. Un rapport par article est écrit dans `rag_data/maillage/work/<slug>/`. Ne modifie aucun article et ne touche pas à WordPress. Tout se passe en local avec les articles crées en local avec la commande ` /create-content `

### 13. `/publish-maillage` — Insertion des liens sur WordPress

`/publish-maillage <slug>` (ou `<numéro>`, ou `--all`)

Cette commande insère dans l'article déjà en ligne sur WordPress les liens relatifs proposés par `/maillage`, à partir du rapport. Un dry-run est obligatoire d'abord, le contenu d'origine est sauvegardé avant écriture, et le statut du post ne change pas.

### Orchestrateur : `/ragpipeline`

`/ragpipeline <chemin_vers_livre.pdf> [--from ETAPE] [--force]`

Enchaîne automatiquement les étapes d'ingestion 1 à 7 sur un pdf source, puis la consolidation du graphe. `--from` permet de reprendre le pipeline à une étape donnée (utile après une interruption), `--force` de relancer une étape déjà marquée comme terminée.

## Outils d'analyse et d'exploration (`tools/`)

Scripts hors-pipeline pour explorer ou auditer le corpus déjà ingéré. Seuls `rename_concept.py` et `migrate_chunk_modalities.py` modifient les bases (voir leur description), les autres sont en lecture seule :

- **`analyze_embedder_candidates.py`** — mesure la longueur réelle du texte qui serait embeddé (par type de contenu et au niveau des chunks) pour plusieurs modèles d'embedding candidats, afin de choisir un couple tokenizer/modèle sur données mesurées plutôt que sur des specs génériques.
- **`analyze_retrieval_coverage.py`** — pour une requête donnée, compare la recherche vectorielle brute (top-k plat) à `retrieval.retrieve()` (section + expansion par concept via le graphe), et mesure si tout le contenu associé (formules, images, code) d'une section est effectivement remonté.
- **`retrieval.py`** — module de récupération combinant recherche vectorielle, expansion par section (fil d'ariane) et expansion par concept via le graphe (`retrieval.retrieve()`), utilisé par `rag_query.py` et `analyze_retrieval_coverage.py`.
- **`rag_query.py`** — interroge le RAG avec `retrieval.retrieve()` et affiche le résultat de façon lisible (regroupé par document puis section), pour une exploration manuelle rapide.
- **`rename_concept.py`** — change la forme canonique d'un concept du graphe (le nouveau nom doit déjà être un de ses alias ; `--dry-run` pour simuler). Modifie `rag_data/db/graph/`, à ne pas lancer pendant qu'une étape du pipeline tourne.
- **`migrate_chunk_modalities.py`** — migration ponctuelle qui ajoute `has_image` aux `chunks.json` et copie `has_formula` / `has_image` dans les métadonnées Chroma, sans ré-embedder ni toucher au graphe (simulation par défaut, `--apply` pour écrire ; la base d'origine est sauvegardée).

Le rapport de qualité du graphe, ancien `graph_quality_report.py`, est désormais le skill `/rag-integrity` (ci-dessus).

## Organisation des données

```
corpusdedepart/     PDF sources (pdfs d'origine)
corpuscondense/     cours condensés en français, PDF finaux 
rag_data/           toutes les données générées par le pipeline (chunks, embeddings,
                    base vectorielle Chroma, graphe Kuzu, audits, articles dans content/)
.claude/skills/     le code du pipeline (ce qui est versionné dans ce dépôt)
.claude/hooks/      garde-fous exécutés automatiquement par Claude Code (voir plus bas)
```

`corpusdedepart/`, `corpuscondense/` et `rag_data/` sont exclus du dépôt via `.gitignore` : ce sont des données volumineuses. Aucun des trois n'existe donc après un `git clone`.

- `corpuscondense/` et `rag_data/` sont créés automatiquement par le pipeline à la première exécution (`/cours-condense` puis les étapes suivantes) — rien à faire.
- `corpusdedepart/` n'est en revanche créé par aucun script : c'est l'emplacement conventionnel où déposer ses PDF sources (les pdfs complets à condenser). À créer manuellement (`mkdir corpusdedepart`) avant la première utilisation, puis y placer ses PDF avant de lancer `/cours-condense <chemin_vers_livre.pdf>` — ou, à défaut, passer directement le chemin du PDF où qu'il se trouve, sans utiliser ce dossier.

## Installation

⚠️ **Windows uniquement.** Ce projet n'a été développé et testé que sous Windows — `requirements.txt` contient des paquets Windows-only (`win32_setctime`, `pyreadline3`), et les dépendances système ci-dessous (Tesseract, Visual C++ Build Tools) s'installent via `winget`. Aucune compatibilité macOS/Linux n'est garantie ni maintenue actuellement.

### Étape 0 — Récupérer le projet

Cloner (ou télécharger puis extraire) ce dépôt, puis se placer à sa racine (le dossier contenant `.claude/`, `requirements.txt`, ce `README.md`) — toutes les commandes des étapes suivantes s'exécutent depuis cet emplacement :

```bash
git clone https://github.com/paulromano371-SEO-IA/pipeline-rag-cours
cd pipeline-rag-cours
```

### Étape 1 — Visual C++ Build Tools (obligatoire AVANT l'étape 3)

`stringzilla` (dépendance de `pix2tex`) n'a pas de wheel précompilé pour Windows sur PyPI : `pip` compile ses sources localement à l'étape 3, ce qui échoue sans compilateur C++. Aucune commande du projet n'installe ce compilateur automatiquement — il faut le faire manuellement, et avant l'étape 3. Il faut supporter le standard **C++17** (n'importe quelle version de Visual Studio 2019+ ou les Build Tools 2022 conviennent, pas de version exacte à respecter) :

```bash
winget install --id Microsoft.VisualStudio.2022.BuildTools --override "--quiet --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
```

(ou `winget install --id Microsoft.VisualStudio.2022.BuildTools` sans `--override` pour l'installeur graphique, puis cocher le workload **"Desktop development with C++"** manuellement)

### Étape 2 — Tesseract OCR (recommandée AVANT l'étape 3)

```bash
winget install --id UB-Mannheim.TesseractOCR
```

Installe le binaire dans `C:\Program Files\Tesseract-OCR\tesseract.exe` : c'est l'emplacement attendu par le code (`.claude/skills/rag-nottext/scripts/ocr_general.py`), pas besoin de l'ajouter manuellement au PATH ni de redémarrer le terminal. Les modèles de langue français/anglais sont déjà fournis dans `.claude/skills/rag-nottext/scripts/tessdata/` (le paquet winget n'inclut que l'anglais). Doit être fait avant de lancer `/rag-nottext`, qui l'utilise via `ocr_general.py` pour toute image du pipeline.

### Étape 3 — Environnement Python

```bash
python -m venv .venv-rag
.venv-rag/Scripts/python.exe -m pip install -r requirements.txt
```

## Exploitation dans Claude Desktop

Une fois le dépôt cloné/téléchargé et l'installation (ci-dessus) terminée :

1. Ouvrir **Claude Desktop**, onglet **Code**.

2. Ouvrir ce dossier (`pipeline-rag-cours`, celui qui contient `.claude/`) comme répertoire de travail du projet.

3. Rien d'autre à configurer : dès que ce dossier est le répertoire de travail, Claude Code détecte automatiquement les skills sous `.claude/skills/` (visibles en tapant `/` dans la conversation) et applique les hooks de `.claude/settings.json` sans action manuelle.

4. Déposer le(s) pdf source(s) (PDF) dans `corpusdedepart/` (à créer si besoin, voir plus haut).

5. Lancer le pipeline directement dans la conversation Claude Code, par exemple :
   
   ```
   /ragpipeline
   ```
   
   ou étape par étape avec `/cours-condense`, `/rag-extraction`, etc. (voir « Les étapes en détail » plus haut).

Le venv Python (`.venv-rag/`) et Tesseract n'ont besoin d'être ni activés ni référencés manuellement : les scripts de chaque skill appellent directement `.venv-rag/Scripts/python.exe`. Un garde-fou (`_rag_lib/venv_guard.py`) relance automatiquement un script lancé par erreur avec le Python système, et un hook Claude Code (`.claude/hooks/block_system_python.py`) refuse ces commandes.

## Points d'attention techniques

- **Exécution en arrière-plan du pipeline RAG : bloquée automatiquement, pas seulement déconseillée.** Le hook `.claude/hooks/block_rag_background.py` refuse toute commande `run_in_background` référençant un script des étapes 1 à 7 — Claude ne peut donc pas violer cette règle par erreur. Pourquoi cette règle existe : plusieurs étapes appellent `claude -p` en interne, et des invocations concurrentes provoqueraient des timeouts ou des corruptions de la base partagée (Chroma, Kuzu) si le hook ne les empêchait pas.
- **Délégation de la lecture de `pivot.md` à un sous-agent : bloquée automatiquement, même logique.** Le hook `.claude/hooks/block_rag_extraction_subagent_read.py` refuse toute délégation à l'outil Agent de la lecture ou de la vérification de `pivot.md` (sortie de `/rag-extraction`) et de son rapport qualité/fidélité — même contrainte de déterminisme que ci-dessus.
- `rag_data/` centralise l'état de tout le corpus (base vectorielle et graphe uniques, communs à tous les documents) — ne jamais le supprimer, même partiellement.
