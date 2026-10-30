#### 2c. Collecte

Chaque sous-requête est **étiquetée de sa facette** (`<facette>::<requête>`).

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
absent du corpus, vérifie-le : le script lance `check_gap.py` pour chaque lacune que tu déclares.

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
