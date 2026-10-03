---
name: create-content
description: >-
  Rédige un contenu web pédagogique, unique et agréable à lire à partir d'une
  expression (ex. "comment fonctionne une régression logistique ?"), en
  s'appuyant à 100% sur le RAG (texte, formules, images, code de tous les
  livres ingérés) — jamais sur les connaissances générales du modèle pour les
  faits. Produit un article Markdown + frontmatter SEO + images prêtes à
  publier dans rag_data/content/<slug>/. Usage: /create-content <expression>.
---

# /create-content — Générateur de contenus web basé sur le RAG

`/create-content <expression>` transforme la connaissance du corpus en UN
article web qui donne envie d'être lu jusqu'au bout — pas un résumé de
chunks, pas un cours recopié. Le RAG fournit **tous les faits** ; ton travail
est éditorial : choisir un angle, construire une progression, faire comprendre.

Ce skill n'écrit **jamais** dans les bases (`rag_data/db/`), ni dans
`rag_data/work/`, ni dans le code du pipeline. Sa seule sortie est
`rag_data/content/<slug>/`. Ne supprime jamais rien sous `rag_data/`.

## Règles d'or (non négociables)

1. **Le dossier est la seule source de faits.** Définitions, propriétés,
   hypothèses, chiffres, noms, exemples : tout vient du dossier. Tu peux
   apporter des *analogies, transitions, questions d'accroche, mises en
   forme* (ce qui ne prétend pas être un fait du domaine) — jamais une
   affirmation technique absente du dossier. Ce que le dossier ne couvre
   pas est signalé à l'utilisateur dans le rapport final, **jamais** comblé
   de mémoire et jamais mentionné dans l'article.
2. **Code : verbatim.** Un bloc de code de l'article reprend à l'identique
   celui d'un chunk (lignes élidées par `...` tolérées). Tu peux le
   *commenter autour*, pas le réécrire. `verify.py` le contrôle.
   **Une ligne élidée ne doit jamais être une ligne qui définit une variable
   réutilisée plus loin** dans le même bloc ou un bloc suivant — si la
   garder allonge trop le bloc, raccourcis le commentaire autour, jamais la
   définition. `verify.py` avertit (heuristique, pas infaillible) quand une
   variable semble utilisée sans définition visible.
3. **Formules : LaTeX du dossier**, puis expliquées en français clair
   (chaque symbole nommé, sens intuitif de l'ensemble). Une formule que tu
   dérives toi-même (ex. simplification) est signalée par un avertissement.
4. **Images : uniquement celles du dossier**, avec la légende que *toi* tu
   écris à partir de leur description (`[DESCRIPTION DU PIPELINE]`) — la
   description est une aide de lecture, pas du texte à recopier. Chemins
   `images/<fichier>` tels que fournis par `gather.py`. Tu peux regarder
   une image avec l'outil Read si sa description ne suffit pas à l'utiliser
   correctement.
5. **Aucune fuite de la mécanique interne** dans l'article : jamais
   « chunk », « dossier », « RAG », « selon le livre X, page Y », ni
   identifiant `[Sxxx]` visible. La traçabilité passe par des commentaires
   HTML `<!--src:S012-->` (invisibles, stripables au moment de publier).
6. **Plusieurs livres = un seul discours.** Fusionne les apports (un livre
   donne l'intuition, un autre la preuve, un autre le code). Si deux livres
   notent différemment la même chose (β vs w, σ vs sigmoïde…), choisis UNE
   notation, tiens-la tout du long, et mentionne l'autre une fois
   (« on la trouve aussi notée… »). Si deux livres se contredisent, ne
   tranche pas : ne garde que ce qui est cohérent et signale-le à
   l'utilisateur dans le rapport.

## Exécution

Toujours en foreground, jamais via `run_in_background`, jamais de
sous-agent (`Agent`) pour lire le dossier ou rédiger : le tout se fait dans
ce tour, et un seul processus à la fois sur les bases (comme le reste du
pipeline RAG). `<racine_projet>` = le dossier contenant `.claude/`.

### Étape 1 — Planifier les sous-requêtes puis constituer le dossier

Une seule requête ne remonte jamais tout. À partir de l'expression, écris
5 à 8 **sous-requêtes** en français, chacune visant une facette différente
du sujet — adapte-les, ne les recopie pas mécaniquement :

| Facette | Exemple pour « régression logistique » |
|---|---|
| Définition / à quoi ça sert | « qu'est-ce que la régression logistique » |
| Intuition / idée centrale | « pourquoi une fonction sigmoïde pour modéliser une probabilité » |
| Formule / mathématiques | « formule du modèle logistique, log-vraisemblance, log-odds » |
| Apprentissage / optimisation | « comment estimer les coefficients, maximum de vraisemblance, descente de gradient » |
| Code / mise en pratique | « exemple de code Python de régression logistique avec scikit-learn » |
| Interprétation | « interpréter les coefficients, odds ratio » |
| Hypothèses, limites, pièges | « limites de la régression logistique, séparation parfaite, régularisation » |
| Voisins / comparaisons | « différence entre régression linéaire et logistique, lien avec LDA/SVM » |

Puis :

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/create-content/scripts/gather.py" "<expression>" --query "<sous-requête 1>" --query "<sous-requête 2>" ...
```

Options : `--search-top-k`, `--max-sections`, `--result-cap`,
`--relevance-margin` (mêmes défauts que `tools/retrieval.py`). Le script
fusionne et dédoublonne, écrit `rag_data/content/<slug>/dossier.md`,
`dossier.json` et copie les images dans `images/`.

**Relaie intégralement la sortie brute du script à l'utilisateur**, sans la
résumer. Codes de sortie : `3` = corpus sans rien sur ce sujet → dis-le
franchement et arrête-toi (n'écris aucun article de mémoire).

**Couverture** : si le rapport signale une `LACUNE` (aucun code, aucune
formule, aucune image) alors que le sujet en appelle, relance `gather.py`
avec une sous-requête ciblée sur ce manque (et `--result-cap` plus élevé)
avant de conclure qu'il n'y a rien. Une seule relance de rattrapage, pas
plus : au-delà, c'est une lacune réelle du corpus.

**Diversité et matière distinctive** (ce que le script mesure, à toi d'en
décider) : le rapport indique combien de livres couvrent vraiment le sujet
(`book_coverage`) et combien de chunks portent un `★ signal distinctif`
(chiffre précis, benchmark, entité nommée — visible dans `dossier.md`). Ce
sont des faits repérés par des livres source non indexés par les moteurs de
recherche : c'est ce qui peut rendre l'article non-commodity aux yeux de
Google, à condition de les mettre en avant plutôt que de les noyer.

- `RISQUE COMMODITY` (un seul livre pertinent) : relance `gather.py` avec des
  sous-requêtes visant explicitement d'autres livres du corpus si plausible ;
  sinon accepte-le et compense par la structure (fil narratif propre, angle
  contrariant) plutôt que par la seule matière.
- `SUGGESTION` de scission (dossier très riche, plusieurs sous-thèmes) :
  envisage sérieusement un article principal + des articles satellites
  (Étape 3) plutôt qu'un seul article-fleuve — voir la section Longueur.

### Étape 2 — Lire le dossier en entier, puis concevoir

Lis `dossier.md` **en entier** (pas d'échantillon). Avant d'écrire une
ligne, décide — et garde-le en tête, pas dans l'article :

- **L'angle** : la question que le lecteur se pose vraiment derrière
  l'expression, et l'idée-force unique de l'article (« une régression
  linéaire, c'est trouver la droite qui se trompe le moins — et tout le
  reste en découle »). Un article bluffant a *une* idée directrice, pas
  une liste de rubriques.
- **Le lecteur** : curieux intelligent, non spécialiste. Il ne connaît ni
  le jargon ni la notation ; chaque terme technique est défini à sa
  première apparition, avec des mots simples.
- **L'inventaire** : liste mentalement chaque formule, chaque image, chaque
  bloc de code du dossier et *où* il servira dans la progression. Ce qui est
  dans le dossier et pertinent doit apparaître (couverture contrôlée par
  `verify.py`) ; ce qui est redondant ou hors sujet peut être écarté
  consciemment. Priorise consciemment les chunks marqués `★ signal
  distinctif` : ce sont eux qui portent le fil narratif le plus différenciant
  (un exemple concret, un chiffre de benchmark, un cas d'échec précis) plutôt
  qu'une définition que dix autres sources en ligne répètent déjà.
- **Le fil conducteur** : choisis, si le dossier le permet, UN exemple ou cas
  concret distinctif qui traverse l'article de bout en bout (accroche,
  intuition, code, résultats) plutôt qu'un exemple différent par section —
  c'est ce qui distingue le plus un article d'une liste de définitions.
- **La progression** : du concret vers l'abstrait, puis retour au concret.

### Étape 3 — Rédiger `rag_data/content/<slug>/article.md`

**Frontmatter obligatoire** :

```yaml
---
title: "Titre qui promet un bénéfice (≤ 65 car.)"
description: "Méta-description SEO, 120-160 caractères, donne envie de cliquer."
slug: "slug-court-en-minuscules"
keywords: [mot-clé principal, variantes, termes voisins]
reading_time: 9            # minutes, ~200 mots/min
level: débutant            # débutant | intermédiaire | avancé
related: [concepts voisins du dossier, pistes de liens internes]
---
```

**Longueur : au service de la pédagogie, jamais d'un chiffre.** Aucune borne
haute ou basse n'est imposée (`verify.py` indique le nombre de mots à titre
purement informatif). Ne sacrifie jamais, pour gagner des mots, une mise en
situation réelle avant un bloc de code, l'explication d'une convention
(notation `In [n]:`/`Out[n]:`, symbole d'une formule), ou une ligne de code
nécessaire à la compréhension — c'est exactement ce genre de coupe qui a
produit un bloc de code utilisant une variable jamais définie dans une
version précédente de ce skill. Si le dossier est trop riche pour une seule
page sans perdre en clarté (souvent signalé par une `SUGGESTION` de scission
à l'Étape 1), scinde en un article principal + 1-2 articles satellites
plutôt que de tout compresser.

**Architecture de l'article** (une trame, pas un moule — adapte-la au
sujet, garde l'esprit) :

1. **Accroche (2-4 phrases, sans titre H2)** : une situation concrète, une
   question ou un paradoxe qui ancre le sujet dans le réel du lecteur. Pas
   de « Dans cet article nous allons voir ». Promesse claire de ce qu'il
   saura faire/comprendre à la fin.
2. **`## L'essentiel en 30 secondes`** : 3-5 puces qui donnent la réponse
   complète, pour le lecteur pressé (et pour les extraits Google).
3. **`## L'intuition`** : le concept expliqué *sans aucune formule*, avec
   une analogie ou un mini-exemple chiffré du dossier. L'image la plus
   parlante du dossier vient ici si elle en existe une.
4. **`## Sous le capot`** : la formalisation. Formule(s) du dossier, chacune
   suivie d'un « lecture pas à pas » : ce que dit chaque terme, ce que
   l'ensemble veut dire. Progressive : une idée par bloc.
5. **`## Le voir tourner`** : le code du dossier, verbatim, précédé de ce
   qu'on va observer et suivi de ce qu'il faut en retenir (annotations
   *autour* du code, jamais dedans). Sorties/graphes du dossier ici.
6. **`## Lire les résultats`** : interprétation (coefficients, métriques,
   graphiques) — si le dossier la couvre.
7. **`## Pièges et limites`** : hypothèses, erreurs classiques, quand le
   modèle échoue — si le dossier les couvre.
8. **`## Aller plus loin`** : 2-4 concepts voisins du dossier
   (`related_concepts`), une phrase chacun sur leur lien avec le sujet.
9. **`## À retenir`** : 3-5 puces, formulées autrement que le résumé du
   début.
10. Éventuel **`## Questions fréquentes`** (2-4 Q/R courtes) si le dossier
    s'y prête — utile pour le SEO.

**Ce qui fait la différence (checklist de style)** :

- **Une idée par paragraphe, ≤ 4 phrases, ≤ 100 mots.** Écran mobile.
- **Titres H2/H3 informatifs** (« Pourquoi on élève les erreurs au carré »
  plutôt que « Fonction de coût »).
- **Voix** : « tu », toujours — c'est la voix imposée du projet, non
  négociable (`verify.py` bloque tout vouvoiement). Ton chaleureux et précis
  d'un bon prof qui parle à un adulte intelligent. Pas de jargon gratuit, pas
  de condescendance, pas de superlatifs creux (« puissant », « incroyable »),
  pas de « il est important de noter que ».
- **Concret d'abord** : chaque notion abstraite est ancrée dans un exemple
  chiffré ou une image avant sa définition formelle.
- **Rythme** : alterne prose, formule, image, code, encadré. Jamais plus
  de ~300 mots de prose d'affilée sans un élément visuel ou une puce.
- **Encadrés** : utilise des blockquotes signalées pour les temps forts —
  `> **💡 Intuition** …`, `> **⚠️ Piège** …`, `> **📌 À retenir** …` —
  avec parcimonie (3-5 dans tout l'article).
- **Images** : `![Légende descriptive utile, pas « figure 1 »](images/b1_xxx.png)`
  suivie d'une phrase qui dit *quoi regarder* dedans. Une image sans
  commentaire sur ce qu'elle montre est une image décorative : proscrit.
- **Formules** : display en `$$ … $$` sur leur propre ligne ; symboles
  inline en `$ … $`. Toujours introduites (« l'erreur d'un point se
  mesure ainsi ») et expliquées après.
- **Citations de traçabilité** : `<!--src:S012,S015-->` en fin de paragraphe
  ou juste après un bloc s'appuyant sur ces éléments du dossier. Chaque
  affirmation factuelle doit avoir sa source ; c'est ce qui permet à
  `verify.py` et à toi de prouver que rien n'est inventé.
- **Unicité** : ne reprends pas le plan ni les phrases d'un livre. Tu
  reformules et recomposes ; le seul verbatim autorisé, ce sont le code et
  les formules. Ce n'est jamais un copier-coller d'un chunk, même
  légèrement retouché.
- **Pas d'affirmation non sourcée pour faire joli.** Une belle phrase
  fausse ou invérifiable est pire qu'une phrase simple.
- **Code en contexte** : jamais un jeu de données ou un extrait qui
  « apparaît » sans qu'une question réelle, posée au lecteur, précède le
  premier bloc de code d'une section. La première fois qu'un extrait montre
  `In [n]:`/`Out[n]:`, explique cette convention en une phrase — ne suppose
  jamais que le lecteur connaît Jupyter.

### Étape 4 — Vérifier (obligatoire, boucle jusqu'à OK)

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/create-content/scripts/verify.py" "<slug>"
```

Contrôle mécanique : code fidèle, images valides, citations valides,
absence de fuites (« chunk », marqueurs internes), structure/frontmatter,
voix (tutoiement imposé, bloquant), puis avertissements de couverture
(formule/image/code du dossier non utilisés), de lisibilité (paragraphes
trop longs, paragraphe sans citation), de variable de code potentiellement
non définie (élision suspecte), de légende d'image possiblement incohérente
avec la description du dossier, et de matière distinctive citée (signal de
risque commodity). Ces deux derniers sont des heuristiques imparfaites :
elles avertissent, jamais ne bloquent — un faux positif se justifie en une
ligne comme les autres avertissements.
Relaie la sortie brute. Code `2` : corrige **l'article** (jamais le script,
jamais le dossier) et relance ; ne déclare pas le contenu terminé tant que
le résultat n'est pas `OK`. Pour chaque avertissement restant, soit tu
corriges, soit tu le justifies en une ligne dans le rapport (ex. « image
S014 écartée : redondante avec S011 »).

### Étape 5 — Relecture éditoriale (ce que le script ne sait pas juger)

Relis l'article comme un lecteur, puis comme un fact-checker :

- **Test de l'accroche** : donnerait-elle envie de continuer ? Sinon, réécris.
- **Test de la progression** : chaque section fait-elle avancer, sans
  saut ni redite ? Un terme est-il utilisé avant d'être défini ?
- **Test du fact-check** : reprends 5 affirmations techniques au hasard,
  retrouve chacune dans le dossier via son `<!--src:-->`. Une seule
  introuvable = tu la corriges ou la supprimes.
- **Test de la notation** : une seule notation, cohérente partout.
- **Test du « et alors ? »** : chaque section répond-elle à une question
  que le lecteur se pose ? Sinon, coupe.
- **Test du code** : en lisant uniquement un bloc de code et le texte qui
  l'entoure (pas le dossier), peux-tu dire d'où vient chaque variable
  utilisée ? Une variable apparue sans avoir été définie est un bug à
  corriger avant de continuer, pas un détail.
- **Test de la légende** : la légende que tu as écrite décrit-elle vraiment
  ce que l'image montre, ou ce que raconte le paragraphe voisin ? Une
  légende qui « colle » à la narration plutôt qu'à l'image est une
  invention déguisée.

Corrige, puis relance `verify.py` si tu as modifié quoi que ce soit.

## Rapport final à l'utilisateur

Bref, factuel, en français :

- chemin de l'article (`rag_data/content/<slug>/article.md`) ;
- titre, description SEO, durée de lecture, niveau ;
- **Couverture** : livres utilisés, nombre de formules/images/blocs de
  code repris sur ceux disponibles ;
- **Diversité des sources** : `book_coverage` (livres pertinents sur le
  total), taux de matière distinctive citée (donné par `verify.py`) ;
- **Lacunes du corpus** : ce que le sujet appelait mais que le RAG ne
  contenait pas (le contenu a été écrit *sans* — rien n'a été inventé) ;
  contradictions ou différences de notation entre livres ;
- **Décisions éditoriales** notables (angle retenu, fil conducteur choisi,
  éléments écartés, scission ou non en articles satellites) ;
- **Verdict commodity / non-commodity** (ton jugement, appuyé sur les deux
  chiffres ci-dessus — jamais un verdict binaire tranché par le script) ;
- résultat de `verify.py` (relayé tel quel plus haut, ici seulement le verdict).

Ne colle pas l'article dans la réponse : l'utilisateur l'ouvre dans
`article.md`.

## Suite prévue (hors périmètre de ce skill)

Le format de sortie (Markdown + frontmatter + `images/`) est volontairement
agnostique : la publication vers le CMS via son serveur MCP sera une étape
distincte, branchée plus tard, qui consommera `article.md` et `images/` —
c'est pourquoi les commentaires `<!--src:...-->` sont à stripper à ce
moment-là, pas avant.
