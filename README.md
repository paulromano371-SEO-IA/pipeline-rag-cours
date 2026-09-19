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

Chaque étape est une commande indépendante (un skill Claude Code), invocable seule ou enchaînée automatiquement par l'orchestrateur `/ragpipeline`. Les séparer permet à chaque étape de tourner dans un contexte de conversation frais et borné, plutôt que de tout faire exécuter par un unique agent qui accumulerait du contexte sur un livre entier.

## Les étapes en détail

### 1. `/cours-condense` — Génération du cours condensé — **opérationnel**
`/cours-condense <chemin_vers_livre.pdf> [checkpoint=false]`

Lit le livre source **intégralement** (jamais un extrait) et produit un support de cours condensé en français, compilé en PDF via LaTeX. Le texte est réécrit dans un ton pédagogique fidèle à l'auteur, le code source est repris à l'identique (jamais reformulé), et des illustrations vectorielles originales sont créées pour appuyer les explications. Aucune longueur cible n'est fixée à l'avance : le plan et la compression découlent uniquement du contenu réellement essentiel du livre.

### 2. `/rag-extraction` — Conversion en markdown pivot — **en cours de développement**
`/rag-extraction <chemin_vers_pdf_condense>`

Convertit le PDF condensé (sortie de l'étape 1) en un markdown "pivot" qui distingue proprement titres, code et prose, et extrait les images natives du PDF. Une réparation automatique des ligatures typographiques cassées (ex. "ﬁ", "ﬂ") est appliquée si nécessaire. Traite aussi la détection de formules mathématiques mal ordonnées par l'extraction PDF brute.

### 3. `/rag-nottext` — Description des éléments non-textuels — **en cours de développement**
`/rag-nottext <pdf_condense_ou_document_id>`

Pour chaque image, bloc de code et formule d'affichage (déjà en LaTeX) produits à l'étape 2 : description en langage naturel, OCR/renommage adapté pour les images (classification formule mathématique vs image générale, LaTeX via pix2tex ou texte via tesseract, nom de fichier explicite), puis injection de cette description dans le markdown pivot, juste après l'élément. Un modèle d'embedding texte ne rapproche quasiment jamais une question en français d'un verbatim LaTeX, code ou image brut — vérifié empiriquement — donc sans cette étape, ce contenu reste invisible à la recherche vectorielle.

### 4. `/rag-chunking` — Découpage en chunks — **en cours de développement**
`/rag-chunking <pdf_condense_ou_document_id>`

Découpe le markdown pivot enrichi en chunks d'environ 400 tokens (avec recouvrement), sans jamais couper un bloc de code, une image ou une formule au milieu. Pour un élément décrit par `/rag-nottext`, le texte réellement embeddé (calculé à l'étape suivante) ne retient que sa description en langage naturel — jamais le verbatim brut — pour ne pas diluer la similarité avec une question en français. Étape purement déterministe (basée sur le tokenizer réel du modèle d'embedding, `BAAI/bge-m3`), aucun appel à un modèle de langage.

### 5. `/rag-index` — Indexation vectorielle — **en cours de développement**
`/rag-index <pdf_condense_ou_document_id>`

Calcule les embeddings de chaque chunk (modèle multilingue local `BAAI/bge-m3` via `sentence-transformers`, sans clé API externe) et les indexe dans une base vectorielle Chroma partagée entre tous les documents du corpus. Les chunks de texte "bruité" (motifs mal extraits d'un diagramme) sont filtrés automatiquement.

### 6. `/rag-concepts` — Extraction des concepts — **en cours de développement**
`/rag-concepts <pdf_condense_ou_document_id>`

Pour chaque chunk indexable, un appel `claude -p` headless extrait 3 à 8 concepts significatifs (nom, forme canonique, type). Cette étape prépare la construction du graphe de connaissances de l'étape suivante.

### 7. `/rag-graphe` — Construction du graphe de connaissances (GraphRAG) — **en cours de développement**
`/rag-graphe <pdf_condense_ou_document_id>`

Résout chaque concept extrait en comparant sa similarité d'embedding aux concepts déjà connus du graphe (commun à tout le corpus) :
- similarité ≥ 0.92 : fusion automatique avec un concept existant,
- similarité < 0.80 : nouveau concept,
- entre les deux : arbitrage par un appel `claude -p` dédié.

C'est cette étape qui permet de repérer qu'un même concept est traité dans plusieurs livres différents — l'intérêt principal du GraphRAG par rapport à une recherche vectorielle seule.

### Orchestrateur : `/ragpipeline`
`/ragpipeline <chemin_vers_livre.pdf> [--from ETAPE] [--force]`

Enchaîne automatiquement les 7 étapes ci-dessus sur un livre source. `--from` permet de reprendre le pipeline à une étape donnée (utile après une interruption), `--force` de relancer une étape déjà marquée comme terminée.

## Organisation des données

```
corpusdedepart/     PDF sources (livres d'origine) — jamais versionné (volumineux, droits d'auteur)
corpuscondense/     cours condensés en français, PDF finaux — jamais versionné
rag_data/           toutes les données générées par le pipeline (chunks, embeddings,
                    base vectorielle Chroma, graphe Kuzu) — jamais versionné
.claude/skills/     le code du pipeline (ce qui est versionné dans ce dépôt)
```

`corpusdedepart/`, `corpuscondense/` et `rag_data/` sont exclus du dépôt via `.gitignore` : ce sont des données volumineuses, potentiellement soumises au droit d'auteur (livres sources) ou régénérables à partir du code (base vectorielle, graphe).

## Installation

```bash
python -m venv .venv-rag
.venv-rag/Scripts/python.exe -m pip install -r requirements.txt
```

Dépendances système supplémentaires (non couvertes par pip) :
- **Tesseract OCR** : `winget install --id UB-Mannheim.TesseractOCR` (les modèles de langue français/anglais sont déjà fournis dans `.claude/skills/_rag_lib/tessdata/`)
- **Visual C++ Build Tools** (workload "Desktop development with C++"), nécessaire pour compiler une dépendance de `pix2tex`

## Points d'attention techniques

- Les étapes du pipeline RAG (2 à 7) doivent toujours s'exécuter **au premier plan, jamais en arrière-plan ni en parallèle** : plusieurs d'entre elles appellent `claude -p` en interne, et des invocations concurrentes provoquent des timeouts ou des corruptions de la base partagée (Chroma, Kuzu).
- `rag_data/` centralise l'état de tout le corpus (base vectorielle et graphe uniques, communs à tous les documents) — ne jamais le supprimer, même partiellement.
