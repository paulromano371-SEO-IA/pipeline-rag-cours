### Étape 4 — Rédiger `rag_data/content/work/<slug>/article.md`

**Frontmatter obligatoire** :

```yaml
---
title: "Titre qui promet un bénéfice (≤ 65 car.)"
description: "Méta-description SEO, 120-160 caractères, donne envie de cliquer."
slug: "slug-complet-iso-avec-l-expression"   # celui de gather.py, jamais raccourci
keywords: [mot-clé principal, variantes, termes voisins]
reading_time: 9            # minutes, ~200 mots/min
level: débutant            # débutant | intermédiaire | avancé
related: [concepts voisins du dossier, slugs/titres des contenus voisins à lier à la publication]
---
```

**Longueur : au service de la pédagogie, jamais d'un chiffre.** Aucune borne
haute ou basse n'est imposée (`verify.py` indique le nombre de mots à titre
purement informatif). Ne sacrifie jamais, pour gagner des mots, une mise en
situation réelle avant un bloc de code, l'explication d'une convention
(notation `In [n]:`/`Out[n]:`, symbole d'une formule), ou une ligne de code
nécessaire à la compréhension. Si le dossier est trop riche pour une seule
page sans perdre en clarté (souvent signalé par une `SUGGESTION` de scission),
scinde en un article principal + 1-2 articles satellites plutôt que de tout
compresser.

**Ordre de l'article — blocs fixes, communs à tous les contenus :**

1. `# Titre` (H1, unique).
2. **Accroche (2-4 phrases, sans H2)** : une situation concrète, une question
   ou un paradoxe qui ancre le sujet dans le réel du lecteur, **différente de
   celle des voisins**. Pas de « Dans cet article nous allons voir ». Promesse
   claire de ce qu'il saura faire/comprendre à la fin.
3. **Sommaire cliquable** — placé **juste avant** `## L'essentiel en 30
   secondes`. Tu ne l'écris pas à la main : il se génère (voir Étape 5) et
   reprend **tous les H2**, y compris « L'essentiel en 30 secondes ».
4. **`## L'essentiel en 30 secondes`** — **obligatoire sur tous les contenus** :
   3-5 puces qui donnent la réponse complète à la question de l'intention,
   pour le lecteur pressé (et pour les extraits Google). **Chaque puce porte
   une expression-clé en gras** (une seule, 8 mots maximum) : lues seules, les
   expressions en gras de cette liste résument déjà la réponse.
5. **Les sections du plan arrêté à l'étape 3** (H2/H3 informatifs).

Aucune autre section n'est imposée. « Pièges et limites », « Aller plus loin »,
« À retenir », « Questions fréquentes » n'existent que si ton plan les justifie
par l'intention, avec un titre et un contenu propres à l'article (pas des
rubriques de remplissage recopiées d'un article à l'autre). Un « À retenir »,
s'il existe, est formulé autrement que « L'essentiel ». Tes sections répondent
à l'intention : si l'expression est « quand utiliser », l'article aide à
décider ; si c'est « comment », il guide l'action pas à pas.

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
- **Gras de lecture en diagonale** : le gras permet de retrouver le fil de
  l'article en ne lisant que les passages en gras, dans l'ordre. Il va sur
  quatre choses seulement : (1) le **terme défini**, à sa première apparition,
  là où il est expliqué ; (2) l'**idée-clé** d'un paragraphe (2 à 8 mots qui
  portent sa conclusion) ; (3) un **chiffre ou seuil décisif avec son sens**
  (cherche-les d'abord dans les chunks `★ signal distinctif`) ; (4) une
  **condition ou limite** qui change la décision. Jamais une phrase entière, un
  mot générique, un titre, une légende, du code, ni une expression déjà mise en
  gras plus haut. Règles : 8 mots maximum par expression ; au moins 15 mots
  entre deux gras d'un même paragraphe, donc jamais deux gras dans des phrases
  qui se suivent ; 2 gras maximum par paragraphe (1 pour un paragraphe court) ;
  hors « L'essentiel », jamais deux puces consécutives en gras ; environ 1 gras
  pour 80 à 120 mots de prose, et au moins 1 par section H2. Les étiquettes
  d'encadré (`> **💡 Intuition**`) ne comptent pas. `verify.py` signale les
  écarts (avertissements, jamais un blocage).
- **Encadrés** : utilise des blockquotes signalées pour les temps forts —
  `> **💡 Intuition** …`, `> **⚠️ Piège** …`, `> **📌 À retenir** …` —
  avec parcimonie (3-5 dans tout l'article).
- **Images** : `![Légende descriptive utile, pas « figure 1 »](images/b1_xxx.png)`
  suivie d'une phrase qui dit *quoi regarder* dedans. Une image sans
  commentaire sur ce qu'elle montre est une image décorative : proscrit.
  Une image déjà utilisée par un voisin reste permise : écris-lui une légende
  et un commentaire propres à l'argument que *cet* article lui fait porter.
- **Formules** : display en `$$ … $$` sur leur propre ligne ; symboles
  inline en `$ … $`. Toujours introduites et expliquées après.
- **Citations de traçabilité** : `<!--src:S012,S015-->` en fin de paragraphe
  ou juste après un bloc s'appuyant sur ces éléments du dossier. Chaque
  affirmation factuelle doit avoir sa source.
- **Unicité** : ne reprends ni le plan, ni les phrases d'un livre, ni ceux d'un
  voisin. Tu reformules et recomposes ; le seul verbatim autorisé, ce sont le
  code et les formules. Pas de copier-coller d'un chunk, même retouché.
- **Pas d'affirmation non sourcée pour faire joli.** Une belle phrase
  fausse ou invérifiable est pire qu'une phrase simple.
- **Code en contexte** : jamais un jeu de données ou un extrait qui
  « apparaît » sans qu'une question réelle, posée au lecteur, précède le
  premier bloc de code d'une section. La première fois qu'un extrait montre
  `In [n]:`/`Out[n]:`, explique cette convention en une phrase.

#### Passe de gras (dernière passe de l'étape 4)

Une fois le texte arrêté, repasse **section par section** : pour chaque
paragraphe, cherche lequel des quatre cas du « Gras de lecture en diagonale »
s'y applique, garde le plus utile (parfois aucun : un paragraphe de transition
n'en a pas besoin) et pose le gras. Termine par le **test du survol** : relis
uniquement les expressions en gras, dans l'ordre. Raconte-t-elle l'argument de
l'article, de la question à la réponse ? Sinon, déplace ou remplace-les — ne
les multiplie pas.
