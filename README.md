# Pipeline RAG pour supports de cours

Ce projet transforme un livre technique complet (PDF) en un **support de cours condensé de grande qualité et en français **, puis ingère ce support dans une base de connaissances interrogeable : recherche vectorielle (RAG classique) **et** graphe de concepts (GraphRAG), le tout via une série de [skills Claude Code](https://docs.claude.com/claude-code) enchaînés.

L'idée générale : un livre de plusieurs centaines de pages est rarement exploitable tel quel comme support de cours. Le pipeline le condense d'abord fidèlement (texte, code, illustrations), puis découpe et indexe ce condensé pour qu'il devienne consultable par un assistant IA — avec la possibilité, à terme, de croiser les concepts entre plusieurs livres ingérés.

## Vue d'ensemble du pipeline

```
livre.pdf (corpusdedepart/)
      │
      ▼
1. /cours-condense   →  cours condensé en français, PDF (corpuscondense/)
      │
      ▼
2. /rag-extraction   →  markdown pivot structuré + images extraites
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
```

Chaque étape est une commande indépendante (un skill Claude Code), invocable seule ou enchaînée automatiquement par l'orchestrateur `/ragpipeline`.

## Les étapes en détail

### 1. `/cours-condense` — Génération du cours condensé
`/cours-condense <chemin_vers_livre.pdf> [checkpoint=false]`

Lit le livre source **intégralement** (jamais un extrait) et produit un support de cours condensé en français, compilé en PDF via LaTeX. Le texte est réécrit dans un ton pédagogique fidèle à l'auteur, le code source est repris à l'identique (jamais reformulé), et des illustrations vectorielles originales sont créées pour appuyer les explications. Aucune longueur cible n'est fixée à l'avance : le plan et la compression découlent uniquement du contenu réellement essentiel du livre.

### 2. `/rag-extraction` — Conversion en markdown pivot
`/rag-extraction <chemin_vers_pdf_condense>`

Convertit le PDF condensé (sortie de l'étape 1) en un markdown "pivot" qui distingue proprement titres, code et prose, et extrait les images natives du PDF. Une réparation automatique des ligatures typographiques cassées (ex. "ﬁ", "ﬂ") est appliquée si nécessaire. Traite aussi la détection de formules mathématiques mal ordonnées par l'extraction PDF brute.

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

Pour chaque chunk indexable, un appel `claude -p` headless extrait 3 à 8 concepts significatifs (nom, forme canonique, type). Cette étape prépare la construction du graphe de connaissances de l'étape suivante.

### 7. `/rag-graphe` — Construction du graphe de connaissances (GraphRAG)
`/rag-graphe <pdf_condense_ou_document_id>`

Résout chaque concept extrait en comparant sa similarité d'embedding aux concepts déjà connus du graphe (commun à tout le corpus) :
- similarité ≥ 0.92 : fusion automatique avec un concept existant,
- similarité < 0.80 : nouveau concept,
- entre les deux : arbitrage par un appel `claude -p` dédié.

C'est cette étape qui permet de repérer qu'un même concept est traité dans plusieurs livres différents — l'intérêt principal du GraphRAG par rapport à une recherche vectorielle seule.

### Orchestrateur : `/ragpipeline`
`/ragpipeline <chemin_vers_livre.pdf> [--from ETAPE] [--force]`

Enchaîne automatiquement les 7 étapes ci-dessus sur un livre source. `--from` permet de reprendre le pipeline à une étape donnée (utile après une interruption), `--force` de relancer une étape déjà marquée comme terminée.

## Outils d'analyse et d'exploration (`tools/`)

Scripts hors-pipeline, en lecture seule (aucun ne modifie `rag_data/`), pour explorer ou auditer le corpus déjà ingéré :

- **`analyze_embedder_candidates.py`** — mesure la longueur réelle du texte qui serait embeddé (par type de contenu et au niveau des chunks) pour plusieurs modèles d'embedding candidats, afin de choisir un couple tokenizer/modèle sur données mesurées plutôt que sur des specs génériques.
- **`analyze_retrieval_coverage.py`** — pour une requête donnée, compare la recherche vectorielle brute (top-k plat) à `retrieval.retrieve()` (section + expansion par concept via le graphe), et mesure si tout le contenu associé (formules, images, code) d'une section est effectivement remonté.
- **`rag_query.py`** — interroge le RAG avec `retrieval.retrieve()` et affiche le résultat de façon lisible (regroupé par document puis section), pour une exploration manuelle rapide.
- **`graph_quality_report.py`** — rapport de qualité du graphe de concepts Kuzu : métriques structurelles (volumes, alias, concepts orphelins/hubs) et échantillons à auditer pour estimer la précision de la résolution d'entités entre livres.

(`graphe_lot.py`, qui pilote l'exécution par lots de `/rag-graphe`, n'est pas un outil d'exploration autonome — il fait partie du skill lui-même, voir `.claude/skills/rag-graphe/scripts/`.)

## Organisation des données

```
corpusdedepart/     PDF sources (livres d'origine) — jamais versionné (volumineux, droits d'auteur)
corpuscondense/     cours condensés en français, PDF finaux — jamais versionné
rag_data/           toutes les données générées par le pipeline (chunks, embeddings,
                    base vectorielle Chroma, graphe Kuzu) — jamais versionné
.claude/skills/     le code du pipeline (ce qui est versionné dans ce dépôt)
.claude/hooks/      garde-fous exécutés automatiquement par Claude Code (voir plus bas)
```

`corpusdedepart/`, `corpuscondense/` et `rag_data/` sont exclus du dépôt via `.gitignore` : ce sont des données volumineuses, potentiellement soumises au droit d'auteur (livres sources) ou régénérables à partir du code (base vectorielle, graphe). Aucun des trois n'existe donc après un `git clone`.

- `corpuscondense/` et `rag_data/` sont créés automatiquement par le pipeline à la première exécution (`/cours-condense` puis les étapes suivantes) — rien à faire.
- `corpusdedepart/` n'est en revanche créé par aucun script : c'est l'emplacement conventionnel où déposer ses PDF sources (les livres complets à condenser). À créer manuellement (`mkdir corpusdedepart`) avant la première utilisation, puis y placer ses PDF avant de lancer `/cours-condense <chemin_vers_livre.pdf>` — ou, à défaut, passer directement le chemin du PDF où qu'il se trouve, sans utiliser ce dossier.

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
4. Déposer le(s) livre(s) source(s) (PDF) dans `corpusdedepart/` (à créer si besoin, voir plus haut).
5. Lancer le pipeline directement dans la conversation Claude Code, par exemple :
   ```
   /ragpipeline corpusdedepart/mon_livre.pdf
   ```
   ou étape par étape avec `/cours-condense`, `/rag-extraction`, etc. (voir « Les étapes en détail » plus haut).

Le venv Python (`.venv-rag/`) et Tesseract n'ont besoin d'être ni activés ni référencés manuellement : les scripts de chaque skill appellent directement `.venv-rag/Scripts/python.exe`.

## Points d'attention techniques

- **Exécution en arrière-plan du pipeline RAG : bloquée automatiquement, pas seulement déconseillée.** Le hook `.claude/hooks/block_rag_background.py` refuse toute commande `run_in_background` référençant un script des étapes 1 à 7 — Claude ne peut donc pas violer cette règle par erreur. Pourquoi cette règle existe : plusieurs étapes appellent `claude -p` en interne, et des invocations concurrentes provoqueraient des timeouts ou des corruptions de la base partagée (Chroma, Kuzu) si le hook ne les empêchait pas.
- **Délégation de la lecture de `pivot.md` à un sous-agent : bloquée automatiquement, même logique.** Le hook `.claude/hooks/block_rag_extraction_subagent_read.py` refuse toute délégation à l'outil Agent de la lecture ou de la vérification de `pivot.md` (sortie de `/rag-extraction`) et de son rapport qualité/fidélité — même contrainte de déterminisme que ci-dessus.
- `rag_data/` centralise l'état de tout le corpus (base vectorielle et graphe uniques, communs à tous les documents) — ne jamais le supprimer, même partiellement.
