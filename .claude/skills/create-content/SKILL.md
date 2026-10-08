---
name: create-content
description: >-
  Rédige un contenu web pédagogique, unique et agréable à lire à partir d'une
  expression (ex. "comment fonctionne une régression logistique ?"), en
  s'appuyant à 100% sur le RAG (texte, formules, images, code de tous les
  livres ingérés) — jamais sur les connaissances générales du modèle pour les
  faits. Tient compte des contenus déjà écrits pour ne rien redire d'un autre
  article, et construit un plan propre à l'intention de l'expression. Produit
  un article Markdown + frontmatter SEO + images prêtes à publier dans
  rag_data/content/<slug>/. Usage: /create-content <expression>.
---

# /create-content — Générateur de contenus web basé sur le RAG

`/create-content <expression>` transforme la connaissance du corpus en UN
article web qui donne envie d'être lu jusqu'au bout — pas un résumé de
chunks, pas un cours recopié, et **pas une redite d'un article déjà écrit**.
Le RAG fournit **tous les faits** ; ton travail est éditorial : comprendre ce
que le lecteur veut vraiment (l'intention), choisir un angle que les autres
contenus n'ont pas pris, construire un plan qui y répond, faire comprendre.

Ce skill n'écrit **jamais** dans les bases (`rag_data/db/`), ni dans
`rag_data/work/`, ni dans le code du pipeline. Sa seule sortie est
`rag_data/content/<slug>/`. Ne supprime jamais rien sous `rag_data/` et ne
modifie **jamais** un autre contenu de `rag_data/content/` : les articles
existants sont en lecture seule.

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
   « chunk », « dossier », « RAG », ni identifiant `[Sxxx]` visible. La
   traçabilité passe par des commentaires HTML `<!--src:S012-->` (invisibles,
   stripables au moment de publier).
   **Aucune référence aux livres sources**, sous aucune forme : ni « dans ce
   livre », « le livre X explique que… », « selon l'auteur », « les auteurs »,
   « cet ouvrage », « ce chapitre », « chapitre 3 », « page 42 », « figure 3.2 »,
   « listing 8.9 », ni même les mots livre / ouvrage / manuel / auteur /
   chapitre. Le lecteur ne sait pas qu'il existe des livres : c'est *toi* qui
   parles, les faits sont dits à la première personne du savoir, sans
   attribution (« la sigmoïde écrase tout nombre dans [0, 1] », pas « le livre
   montre que la sigmoïde… »). `verify.py` bloque ces formulations.
   **Aucune référence aux autres contenus du site** (voir règle 7).
6. **Plusieurs livres = un seul discours.** Fusionne les apports (un livre
   donne l'intuition, un autre la preuve, un autre le code). Si deux livres
   notent différemment la même chose (β vs w, σ vs sigmoïde…), choisis UNE
   notation, tiens-la tout du long, et mentionne l'autre une fois
   (« on la trouve aussi notée… »). Si deux livres se contredisent, ne
   tranche pas : ne garde que ce qui est cohérent et signale-le à
   l'utilisateur dans le rapport.
7. **Zéro duplication avec les contenus existants.** Aucune phrase, aucun
   argument, aucun exemple, aucune analogie, aucun enchaînement déjà présent
   dans un article voisin ne se redit — même reformulé. Si un voisin a déjà
   expliqué quelque chose dont tu as besoin pour être compris, tu ne le
   réexpliques pas, **et tu ne renvoies pas non plus à l'article voisin dans le
   texte** : jamais son titre, jamais « dans notre article sur… », « l'article
   cité plus haut », « voir aussi… ». Nomme la notion en une proposition si
   le fil l'exige (« la sigmoïde, cette courbe en S… »), reformule-la sous un
   angle neuf, ou coupe le passage. Les liens vers les voisins se décident à
   la publication : note-les dans `plan.md` et dans le champ `related` du
   frontmatter (slug ou titre du voisin), jamais dans le corps de l'article.
   `verify.py` bloque le titre d'un autre contenu et les formules « notre
   article / l'article cité ». **Hors périmètre de cette règle** :
   les images, les formules et les blocs de code, qui peuvent servir un
   argument différent d'un article à l'autre (le texte qui les entoure, lui,
   doit être neuf). Un tableau est du texte : il ne se reprend pas à
   l'identique.
8. **Rien n'est bloquant côté voisins.** S'il n'y a aucun voisin (aucun
   contenu existant proche du sujet, ou aucun contenu du tout), tu pars d'une
   page blanche : c'est un cas normal, pas une erreur. De même, la détection
   de doublons de `verify.py` n'avertit que, elle ne fait jamais échouer.
9. **Les facettes viennent de l'intention, pas des voisins ni de ta mémoire.**
   Le catalogue de l'étape 1 est contraignant et se remplit *avant* de lire un
   voisin. Les voisins servent à ne pas redire (lien posé à la publication, pas dans le texte), jamais à
   choisir les sujets, et jamais à dériver vers d'autres méthodes ou notions
   pour combler ce qu'ils ont déjà couvert.
10. **Une lacune se vérifie.** « Non remonté par mes requêtes » n'est pas
    « absent du corpus » : seule la recherche directe de `check_gap.py` autorise
    la seconde formule dans le rapport final.

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

`<racine_projet>` = le dossier contenant `.claude/`. **Relaie intégralement la
sortie brute** de chaque script à l'utilisateur, sans la résumer.

### Étape 0 — Cadrer l'intention

Avant toute recherche, détermine ce que l'expression *demande vraiment* et
écris-le dans `rag_data/content/<slug>/plan.md` (section « Cadrage ») :

- **Intention** — une famille parmi : *définition* (« qu'est-ce que… »),
  *décision* (« quand utiliser… », « faut-il… »), *procédure* (« comment
  utiliser / faire / installer… »), *comparaison* (« différence entre… »,
  « X ou Y »), *causalité* (« pourquoi… »), *optimisation / diagnostic*
  (« comment améliorer… », « pourquoi ça échoue »), *panorama* (« quels
  sont… »). Une expression peut mêler deux familles : retiens la dominante.
- **La question du lecteur**, dans ses mots, et **la promesse** de l'article :
  ce qu'il saura faire ou décider à la fin.
- **Hors périmètre** : ce que cet article ne traitera volontairement pas
  (parce que ce n'est pas la question posée, ou parce qu'un voisin le fait).

Le slug est celui que `gather.py` calcule (`slugify_expression`). Crée le
dossier `rag_data/content/<slug>/` s'il n'existe pas.

### Étape 1 — Facettes déduites de l'intention (avant de regarder les voisins)

Une seule requête ne remonte jamais tout. À partir de l'**intention retenue à
l'étape 0** — et d'elle seule, **sans avoir lu aucun voisin** — dresse la liste
des facettes à traiter. Le catalogue ci-dessous est **contraignant** : toutes
les facettes de ta famille d'intention figurent dans le tableau de `plan.md`.

| Intention | Facettes du catalogue (une ligne chacune dans `plan.md`) |
|---|---|
| Définition | nature / ce que c'est · à quoi ça sert · composants et fonctionnement · place dans son écosystème · exemple concret · idées reçues |
| Décision (« quand ») | critères de décision · signaux et seuils chiffrés · contre-indications · alternatives · coût / bénéfice · cas limites |
| Procédure (« comment ») | prérequis · étapes dans l'ordre · commande ou code · paramètres clés · vérifier le résultat · erreurs fréquentes |
| Comparaison | axes de comparaison · forces et faiblesses de chaque option · cas d'usage de chacune · critère de choix |
| Causalité (« pourquoi ») | mécanisme · cause racine · preuves et chiffres · conséquences · exceptions |
| Optimisation / diagnostic | symptôme et mesure · cause probable · levier d'action · effet attendu · effets de bord |
| Panorama | critère de classement · chaque élément (un par sous-requête) · comment choisir |

Dans `plan.md`, section **« Facettes »**, écris le tableau complet :
*facette du catalogue → sous-requête prévue → voisin qui la couvre (rempli à
l'étape 2) → décision*. Règles :

- **Aucune facette du catalogue ne disparaît en silence.** Elle reçoit soit une
  sous-requête, soit un retrait écrit (« couverte par <slug du voisin> » si un
  voisin la couvre complètement, ou « hors périmètre » avec la raison).
  `verify.py` avertit si une ligne manque ou n'a ni requête ni retrait.
- **Tu peux ajouter** une facette que le sujet appelle spécifiquement, mais
  elle s'ajoute au catalogue, elle ne le remplace jamais.
- **Chaque sous-requête s'écrit avec le vocabulaire de l'intention** (« à quoi
  sert », « composants », « idée reçue », « seuils », « étapes »…) appliqué
  au sujet. Elle ne vient pas de ce que *toi* tu sais du domaine : tu ne
  connais pas le contenu du corpus ; tu l'interroges avec les facettes, pas avec
  la liste des notions que tu t'attends à y trouver.
- **Pas de dérive vers les sujets voisins.** Chaque H2 final porte une facette
  de l'intention *appliquée au sujet lui-même*. Une autre méthode ou notion n'a
  sa place que comme contraste court au service d'une facette (« place dans son
  écosystème », « alternatives »), jamais comme section à part entière pour
  remplacer une facette que les voisins ont déjà prise : dans ce cas on la
  laisse au voisin.

### Étape 2 — Voisins, croisement avec les facettes, constitution du dossier

#### 2a. Repérer les contenus voisins

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/create-content/scripts/scan_existing.py" "<expression>"
```

Le script parcourt `rag_data/content/`, mesure la proximité de chaque
article avec l'expression (chunks cités en commun avec ce que le RAG remonte
pour elle, sections communes, similarité sémantique) et écrit
`rag_data/content/<slug>/voisins.json`. Un article sur un autre sujet (RAG
quand tu traites d'inférence) est ignoré : inutile de le lire.

- **`AUCUN VOISIN`** : tu n'as rien à éviter. Passe à 2c avec le tableau tel
  quel ; note « aucun voisin » dans `plan.md`.
- **Des voisins** : lis **chacun en entier** (`article_path`) et consigne dans
  `plan.md` (section « Voisins ») : son intention et son angle, son plan
  (H2), la liste de ses **arguments** (une ligne chacun : l'idée, l'exemple,
  l'analogie, le chiffre), ses tournures d'accroche et de conclusion. C'est
  ta liste d'interdits : tout ce qui y figure ne se redit pas.

Seuils : `--threshold-chunks`, `--threshold-sections`, `--threshold-semantic`,
`--max-neighbors` (les chiffres affichés servent à les recaler si un article
évident est manqué ou un article hors sujet est retenu). Si, à la lecture, un
article ignoré par le script te paraît tout de même proche, lis-le aussi.

#### 2b. Croiser chaque facette avec les voisins

Complète le tableau ligne par ligne. **Les voisins décident de ce que tu ne
redis pas, jamais des sujets que tu traites :**

- facette **couverte complètement** par un voisin → colonne décision :
  « couverte par <slug> » (lien à poser à la publication via `related`, rien
  dans le texte de l'article) ; pas de sous-requête ;
- facette **effleurée** par un voisin (un seul angle) → sous-requête sur
  l'autre angle ;
- facette **non couverte** → sous-requête telle que prévue à l'étape 1.

Ne conclus pas « couverte » sans l'avoir vérifié dans l'article voisin lu à
l'étape 2a : un voisin qui traite l'essentiel d'une facette (« à quoi ça
sert », « idées reçues »…) n'en traite pas forcément tous les angles.

#### 2c. Collecte

Chaque sous-requête est **étiquetée de sa facette** (`<facette>::<requête>`) :

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/create-content/scripts/gather.py" "<expression>" --query "nature::<sous-requête 1>" --query "usage::<sous-requête 2>" ...
```

Options : `--search-top-k`, `--max-sections`, `--result-cap`,
`--relevance-margin` (mêmes défauts que `tools/retrieval.py`), `--voisins`
(par défaut `voisins.json` du slug ; **ne passe pas de chemin relatif** : il est
résolu depuis le répertoire courant et le marquage des voisins serait perdu).
Le script fusionne et dédoublonne, écrit `dossier.md`, `dossier.json` et copie
les images dans `images/`. Il rapporte la **couverture par facette** (chunks
trouvés / dont neufs), signale une requête sans facette ou une facette sans
résultat, **marque `⚠ DÉJÀ EXPLOITÉ par : <slug>`** les chunks qu'un voisin a
déjà cités, indique pour chaque sous-requête combien de chunks sont **neufs**,
et chiffre le recouvrement avec chaque voisin.

Codes de sortie : `3` = corpus sans rien sur ce sujet → dis-le franchement et
arrête-toi (n'écris aucun article de mémoire).

**Facette sans résultat** : reformule la requête avec les mots de la facette
(une relance). Si rien ne remonte toujours, vérifie par recherche directe
avant de parler de lacune (voir ci-dessous).

**Vérifier une lacune avant de l'annoncer.** « Le dossier ne contient pas X »
et « le corpus ne contient pas X » sont deux affirmations différentes : la
recherche sémantique ne remonte qu'un nombre limité de chunks, avec des
requêtes formulées par *toi*. Avant de dire dans le rapport qu'un point est
absent du corpus, lance :

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/create-content/scripts/check_gap.py" "<sujet>" "<terme 1>" "<terme 2|variante>"
```

Il cherche les termes en clair dans tous les chunks de tous les livres. S'il
répond **PRESENT et rattaché au sujet**, ce n'est pas une lacune : relance
`gather.py` avec une requête reprenant ces mots. Sans cette vérification, la
formule du rapport est « non remonté par mes requêtes », jamais « absent du
corpus ».

**Recouvrement** : un `RECOUVREMENT ELEVE` n'est pas un blocage. Il te dit que
tes sous-requêtes retombent sur ce que les voisins ont déjà pris : reprends-les
sur des facettes non couvertes (une relance), ou accepte un article plus court
qui laisse l'essentiel aux voisins (sans les citer).

**Couverture** : si le rapport signale une `LACUNE` (aucun code, aucune
formule, aucune image) alors que le sujet en appelle, relance `gather.py`
avec une sous-requête ciblée sur ce manque (et `--result-cap` plus élevé).
Une seule relance de rattrapage, pas plus : au-delà, vérifie avec
`check_gap.py` avant de conclure qu'il n'y a rien.

**Diversité et matière distinctive** : le rapport indique combien de livres
couvrent vraiment le sujet (`book_coverage`) et combien de chunks portent un
`★ signal distinctif` (chiffre précis, benchmark, entité nommée — visible dans
`dossier.md`). Ce sont des faits repérés par des livres source non indexés par
les moteurs de recherche : c'est ce qui peut rendre l'article non-commodity
aux yeux de Google, à condition de les mettre en avant plutôt que de les
noyer.

- `RISQUE COMMODITY` (un seul livre pertinent) : relance `gather.py` avec des
  sous-requêtes visant explicitement d'autres livres du corpus si plausible ;
  sinon accepte-le et compense par la structure (fil narratif propre, angle
  contrariant) plutôt que par la seule matière.
- `SUGGESTION` de scission (dossier très riche, plusieurs sous-thèmes) :
  envisage sérieusement un article principal + des articles satellites
  plutôt qu'un seul article-fleuve — voir la section Longueur.

### Étape 3 — Lire le dossier en entier, puis arrêter le plan

Lis `dossier.md` **en entier** (pas d'échantillon). Avant d'écrire une
ligne, décide — et consigne le plan dans `plan.md` (section « Plan ») :

- **L'angle** : la question que le lecteur se pose vraiment derrière
  l'expression (étape 0), et l'idée-force unique de l'article. Un article
  bluffant a *une* idée directrice, pas une liste de rubriques.
- **Le lecteur** : curieux intelligent, non spécialiste. Il ne connaît ni
  le jargon ni la notation ; chaque terme technique est défini à sa
  première apparition, avec des mots simples.
- **L'inventaire** : liste mentalement chaque formule, chaque image, chaque
  bloc de code du dossier et *où* il servira dans la progression. Ce qui est
  dans le dossier et pertinent doit apparaître (couverture contrôlée par
  `verify.py`) ; ce qui est redondant ou hors sujet peut être écarté
  consciemment. Priorise les chunks marqués `★ signal distinctif`. **N'utilise
  pas comme matière de texte** un chunk `⚠ DÉJÀ EXPLOITÉ` : son contenu a déjà
  été dit ailleurs (images, formules et code restent utilisables).
- **Le fil conducteur** : choisis, si le dossier le permet, UN exemple ou cas
  concret distinctif qui traverse l'article de bout en bout — et différent de
  celui des voisins.
- **Le plan (H2/H3)** — **propre à cet article** :
  - il se déduit de l'**intention** (étape 0) et des **facettes retenues**
    (étapes 1-2), pas d'une trame toute faite : chaque H2 répond à une question
    que le lecteur se pose, dans l'ordre où il se la pose ;
  - il **n'est pas celui d'un voisin** : aucun H2 identique à un H2 voisin et
    un enchaînement différent (renommer les titres ne suffit pas : change le
    découpage) ;
  - pour chaque H2, note dans `plan.md` : la facette qu'il porte, la question
    à laquelle il répond, les chunks `Sxxx` prévus et la ou les images/formules/
    blocs de code qu'il accueille.

  Le plan est **libre** hors des blocs fixes ci-dessous. Des squelettes qui
  *peuvent* inspirer (jamais à suivre tels quels) :
  - *définition* : le problème que ça résout → ce que c'est, composant par
    composant → ça en action → ce que ça n'est pas / ses limites ;
  - *décision* : le dilemme du lecteur → les critères → les signaux chiffrés →
    les cas où l'on s'en abstient → que faire à la place → trancher ;
  - *procédure* : le résultat visé → ce qu'il faut avoir → les étapes → vérifier
    que ça marche → les pièges de chaque étape ;
  - *comparaison* : ce qui oppose les options → axe par axe → qui choisir pour
    quel besoin ;
  - *causalité* : le phénomène observé → le mécanisme → la preuve → les
    exceptions.

### Étape 4 — Rédiger `rag_data/content/<slug>/article.md`

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

### Étape 5 — Sommaire puis vérification (obligatoire, boucle jusqu'à OK)

D'abord, écris (ou remets à jour) le sommaire :

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/create-content/scripts/toc.py" "<slug>"
```

Il insère juste avant `## L'essentiel en 30 secondes` la liste de liens
d'ancre vers tous les H2 (à relancer après tout changement de titre ou de plan).
Puis :

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/create-content/scripts/verify.py" "<slug>"
```

Contrôle mécanique : code fidèle, images valides, citations valides,
tableau des facettes de `plan.md` complet pour le catalogue de l'intention
(avertissements : facette absente, sans requête ni retrait justifié, requête
non étiquetée),
absence de fuites (« chunk », marqueurs internes), **absence de référence aux
livres sources ou à d'autres articles** (bloquant), structure/frontmatter,
**blocs fixes** (H2 « L'essentiel en 30 secondes » présent, sommaire juste
avant et fidèle aux H2), voix (tutoiement imposé, bloquant), puis
avertissements de couverture, de lisibilité, de gras (section sans gras,
densité, gras trop longs ou trop rapprochés), de variable de code
potentiellement non définie, de légende d'image, de matière distinctive, et
de **duplication avec les autres contenus** : passages de ≥ 10 mots identiques,
phrases quasi identiques, titres H2 identiques (reportés pour les voisins).
Les doublons sont des **avertissements, jamais un blocage** : ils te montrent ce
qui a fuit malgré tout. Pour chacun, réécris le passage avec un autre
argument, ou supprime-le (ou laisse la notion au voisin, sans le citer), puis
relance. Sont exclus de ce contrôle : images, formules, blocs de code, sommaire.
Code `2` : corrige **l'article** (jamais le script, jamais le dossier) et
relance ; ne déclare pas le contenu terminé tant que le résultat n'est pas
`OK`. Pour chaque avertissement restant, soit tu corriges, soit tu le justifies
en une ligne dans le rapport.

### Étape 6 — Relecture éditoriale (ce que le script ne sait pas juger)

Relis l'article comme un lecteur, puis comme un fact-checker :

- **Test de l'intention** : l'article répond-il à la question posée par
  l'expression, et pas seulement au sujet ? Un lecteur qui tape cette
  expression trouve-t-il sa réponse dans « L'essentiel en 30 secondes » puis
  dans le détail ?
- **Test du voisin** : un lecteur qui a lu chaque voisin apprend-il ici quelque
  chose de neuf, dans un ordre et avec des exemples différents ? Sinon, coupe.
  Relis aussi une dernière fois : aucune mention d'un livre, d'un auteur, d'un
  chapitre, d'un autre article ni du titre d'un voisin.
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
  l'entoure, peux-tu dire d'où vient chaque variable utilisée ? Une variable
  apparue sans avoir été définie est un bug à corriger.
- **Test de la légende** : la légende décrit-elle vraiment ce que l'image
  montre, ou ce que raconte le paragraphe voisin ?

Corrige, puis relance `toc.py` et `verify.py` si tu as modifié quoi que ce soit.

### Étape 7 — Image de couverture (image mise en avant WordPress)

Une fois le titre définitif :

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/create-content/scripts/make_cover.py" "<slug>"
```

Génère `rag_data/content/<slug>/<slug>.png` (1200×630) par script, sans IA :
titre de l'article + premier mot-clé du frontmatter sur un fond dont la teinte
dérive du slug. Aucune mention de livre. Le fichier est **hors de `images/`**
(ce dossier ne contient que les images du corps). Relance-le si le titre
change. `/publish-content` l'uploade comme image mise en avant.

## Rapport final à l'utilisateur

Bref, factuel, en français :

- chemin de l'article (`rag_data/content/<slug>/article.md`) et du plan
  (`plan.md`) ;
- titre, description SEO, durée de lecture, niveau ;
- **Intention retenue** et angle ;
- **Voisins** : lesquels (ou « aucun voisin, page blanche »), ce qu'ils couvrent,
  ce que cet article fait de différent, liens internes suggérés pour la publication (`related`), doublons restants et
  pourquoi on les garde ;
- **Plan** : les H2, et en quoi il diffère de ceux des voisins ;
- **Couverture** : livres utilisés, nombre de formules/images/blocs de
  code repris sur ceux disponibles ;
- **Diversité des sources** : `book_coverage` (livres pertinents sur le
  total), taux de matière distinctive citée (donné par `verify.py`) ;
- **Facettes** : le tableau de `plan.md` en une ligne par facette du
  catalogue (traitée / couverte par un voisin / hors périmètre + raison) ;
- **Lacunes du corpus** : ce que le sujet appelait et qui n'a pas été trouvé,
  en séparant **« absent du corpus »** (confirmé par `check_gap.py`, sortie
  relayée) de **« non remonté par mes requêtes »** (non vérifié) ;
  contradictions ou différences de notation entre livres ;
- **Décisions éditoriales** notables (fil conducteur choisi, éléments écartés,
  scission ou non en articles satellites) ;
- **Verdict commodity / non-commodity** (ton jugement, appuyé sur les deux
  chiffres ci-dessus — jamais un verdict binaire tranché par le script) ;
- résultat de `verify.py` (relayé tel quel plus haut, ici seulement le verdict).

Ne colle pas l'article dans la réponse : l'utilisateur l'ouvre dans
`article.md`.

## Suite : publication

`/publish-content <slug>` consomme `article.md`, `images/` et `<slug>.png`. Les commentaires
`<!--src:...-->` sont retirés à ce moment-là, et chaque H2 reçoit un `id`
identique à l'ancre du sommaire (même fonction, `scripts/toc.py`) : le sommaire
est cliquable sur le site.
