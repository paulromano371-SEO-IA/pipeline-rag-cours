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
