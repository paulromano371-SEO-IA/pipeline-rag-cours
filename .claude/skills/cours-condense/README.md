# cours-condense : génération d'un cours condensé à partir d'un pdf

Transforme un pdf technique complet (PDF) en support de cours condensé en français, compilé en PDF via LaTeX.

## Utilisation

- `/cours-condense <chemin_vers_livre.pdf>` : lance le pipeline complet sur ce pdf.

- `/cours-condense` (sans argument) : liste les livres de `corpusdedepart/` qui n'ont pas encore été condensés. Voir la section « Hors flux principal ».

- Lecture intégrale : tous les chapitres du plan sont lus en entier, quelle que soit la taille du pdf. Il n'y a aucune question à ce sujet.

Les scripts se lancent avec `.venv-rag/Scripts/python.exe` (et non le Python système). Chaque étape est lancée en premier plan (foreground), jamais en arrière-plan ni en parallèle.

## Principe

Le pipeline comporte 7 étapes, lancées dans l'ordre : l'étape 0, puis les étapes 1 à 6.

Chaque étape a deux niveaux :

1. **Le script `EtapeN.py`** (le chef d'orchestre). Il lance un appel `claude -p` avec le prompt de l'étape (`scripts/prompts/prompt_etapeN.md`), attend le résultat, puis le vérifie. Si la vérification échoue, il relance l'appel en indiquant le motif du refus.
2. **Les scripts utilitaires**. Pendant son travail, Claude les lance dans l'ordre indiqué par le prompt de l'étape.

Une étape a besoin des fichiers produits par la précédente. Si `EtapeN.py` renvoie une erreur, la fiche `SKILL.md` demande de corriger le problème par le modèle, puis de relancer l'étape.

| Étape | Script principal                   | Rôle                                                         |
| ----- | ---------------------------------- | ------------------------------------------------------------ |
| 0     | `Etape0_verifier_deja_condense.py` | Vérifie que le pdf n'a pas déjà été condensé (lecture seule) |
| 1     | `Etape1.py`                        | Établit le plan du cours (`plan_cours.json`)                 |
| 2     | `Etape2.py`                        | Crée les illustrations                                       |
| 3     | `Etape3.py`                        | Rédige le cours (`course.tex`)                               |
| 4     | `Etape4.py`                        | Met en forme le LaTeX                                        |
| 5     | `Etape5.py`                        | Contrôle la qualité avant compilation                        |
| 6     | `Etape6.py`                        | Compile le PDF et le publie dans `corpuscondense/`           |

Les données de travail d'un pdf sont rangées dans `rag_data/courscondense/<slug>/`. Le `<slug>` est le nom du pdf en minuscules, sans accents, avec des `_` à la place des espaces et de la ponctuation.

## Détail par étape

Codes de sortie des scripts `EtapeN.py` : `0` réussi ; `1` échec après plusieurs essais ; `2` mauvais usage ou fichier attendu absent.

### Étape 0 : vérification

**`Etape0_verifier_deja_condense.py`** : indique si le pdf a déjà été condensé.

- Entrée : le chemin du PDF source.
- Fait : regarde si `corpuscondense/<slug>.pdf` existe, s'il y a un PDF dans `rag_data/courscondense/<slug>/out/`, et l'état de l'étape 6 dans `status.json`.
- Écrit : rien.
- Codes : `0` déjà condensé (arrêt) ; `1` pas encore condensé (on continue) ; `2` erreur.

### Étape 1 : plan du cours

**`Etape1.py`** : orchestre l'étape.

- Entrée : le chemin du PDF.
- Fait : lance Claude avec `prompt_etape1.md`, puis vérifie qu'un `plan_cours.json` valide existe, avec une liste `chapitres` non vide.
- Écrit (via Claude) : `plan_cours.json`.

Scripts lancés par Claude, dans l'ordre :

1. **`initialiser_arborescence.py`** : crée le dossier de travail et ses sous-dossiers `extraction/pages`, `illustrations`, `scripts` et `out`. N'écrit aucun fichier de contenu.
2. **`extraire_structure.py`** : lit le PDF.
   - Entrée : le PDF et le dossier `extraction`.
   - Écrit : `extraction/structure.json` (table des matières, nombre de pages, mentions de figures, pages probablement de code) et un fichier `extraction/pages/page_NNNN.txt` par page.
3. **`concatener_pages.py`** : assemble les pages d'un lot en un seul fichier, avec un marqueur `=== FIN PAGE N ===` après chaque page.
   - Entrée : le dossier du pdf, la page de début et la page de fin.
   - Écrit : `extraction/lot_<debut>_<fin>.txt`.
   - Code `1` si la plage de pages est invalide.
4. **`verifier_plan_cours.py`** : contrôle la structure du plan (champs obligatoires, pages valides, chapitres dans l'ordre, sans chevauchement).
   - Entrée : `plan_cours.json` et `structure.json`.
   - Ne modifie rien ; affiche un rapport JSON (`pret`, `anomalies`).
5. **`verifier_calibrage_visuels.py`** : compare, pour chaque chapitre, le nombre de concepts visuels au nombre de mentions de figures de ses pages.
   - Entrée : `plan_cours.json` et `structure.json`.
   - Ne modifie rien ; affiche un rapport JSON.

### Étape 2 : illustrations

**`Etape2.py`** : orchestre l'étape.

- Entrée : le chemin du PDF. Exige `plan_cours.json`.
- Fait : lance Claude avec `prompt_etape2.md`, puis relance `verifier_illustrations.py`, qui doit renvoyer `"pret": true`.
- Écrit (via Claude) : les illustrations PNG dans `illustrations/`, et un script `scripts/illustrations_chapitreN.py` par chapitre.

Scripts utilisés, dans l'ordre :

1. **`illustration_utils.py`** : boîte à outils importée par les scripts d'illustration. Elle calcule la taille de la figure (`suggested_figsize`) et vérifie qu'aucun élément ne chevauche un autre (`verify_no_overlap`). Ne crée aucun fichier.
2. **`verifier_illustrations.py`** : contrôle les illustrations.
   - Entrée : le dossier du pdf et `plan_cours.json`.
   - Vérifie : une illustration par concept clé, le nom `chNN_concept.png`, et les seules couleurs autorisées (`#1a365d`, `#c25e00`) dans les scripts.
   - Ne modifie rien ; affiche un rapport JSON.

### Étape 3 : rédaction

**`Etape3.py`** : orchestre l'étape.

- Entrée : le chemin du PDF. Exige `plan_cours.json`.
- Fait : lance Claude avec `prompt_etape3.md`, puis relance `verifier_glossaire.py` et `verifier_structure_course_tex.py`. Les deux doivent renvoyer `"pret": true`.
- Écrit (via Claude) : `course.tex` et `glossaire.json`. Claude écrit aussi, dans le dossier du pdf, un script `scripts/ecrire_course_tex.py` qui ajoute le texte de chaque chapitre à `course.tex`.

Scripts utilisés, dans l'ordre :

1. **`initialiser_course_tex.py`** : écrit le squelette de `course.tex` (classe, titre, `\maketitle`, `\tableofcontents`, `\newpage`).
   - Entrée : le dossier du pdf et le titre du cours.
   - Refuse d'écraser un `course.tex` existant, sauf avec `--force`.
2. **`extraire_code_exact.py`** : extrait le code source d'un PDF en gardant les espaces et les lignes vides.
   - Entrée : le PDF, les pages, et une zone facultative.
   - Appelé par le script d'écriture quand un chapitre contient du code. Il ne crée aucun fichier : il renvoie le code à son appelant.
3. **`verifier_glossaire.py`** : vérifie qu'aucune variante interdite d'un terme du glossaire n'apparaît dans `course.tex`. Affiche un rapport JSON.
4. **`verifier_structure_course_tex.py`** : contrôle la structure de `course.tex`.
   - Vérifie : une section Résumé par chapitre, une phrase d'explication après chaque bloc de code, une légende et une étiquette pour chaque figure, et la présence de code là où le plan l'indique.
   - Ne modifie rien ; affiche un rapport JSON.

### Étape 4 : mise en forme

**`Etape4.py`** : orchestre l'étape.

- Entrée : le chemin du PDF. Exige `course.tex`.
- Fait : lance Claude avec `prompt_etape4.md`, vérifie que le bloc de mise en forme et `\begin{document}` / `\end{document}` sont présents, puis relance les deux scripts ci-dessous.
- Écrit (via Claude) : modifications de `course.tex`.

Scripts utilisés, dans l'ordre :

1. **`appliquer_preambule_etape4.py`** : insère dans `course.tex`, avant `\begin{document}`, le bloc fixe de mise en forme (paquets, couleurs, style des blocs de code, marges, titres de chapitre). Ne duplique jamais ce bloc.
2. **`inserer_needspace_listings.py`** : ajoute `\Needspace` avant chaque bloc de code qui n'en a pas, pour limiter les coupures de code entre deux pages. Modifie `course.tex`. Sans effet si la commande est déjà présente.

Relancés par `Etape4.py` pour validation :

- **`normaliser_listings_etape4.py`** : remplace dans les blocs de code les caractères Unicode que `listings` ne sait pas lire, et retire les accents des chemins d'images quand le fichier existe sans accent. Modifie `course.tex`. Sans effet si déjà appliqué.

### Étape 5 : contrôle qualité

**`Etape5.py`** : orchestre l'étape.

- Entrée : le chemin du PDF. Exige `course.tex`.
- Fait : lance Claude avec `prompt_etape5.md`, puis vérifie que `controle_qualite.py` renvoie `"pret_a_compiler": true` et que `\usepackage{cleveref}` est présent.
- Écrit (via Claude) : corrections dans `course.tex`.

Scripts utilisés, dans l'ordre :

1. **`corriger_accents.py`** : ajoute les accents manquants dans `course.tex`, en boucle, uniquement quand la correction est sûre. Il s'appuie sur un dictionnaire, des règles grammaticales et une analyse syntaxique. Modifie `course.tex`.
2. **`lister_noms_propres.py`** : liste les mots capitalisés accentués de `course.tex` et indique s'ils s'écrivent sans accent dans le texte source anglais. Ne corrige rien : Claude juge ensuite chaque cas. Affiche un rapport JSON.
3. **`controle_qualite.py`** : contrôle `course.tex` (accents manquants, mots interdits, citations, syntaxe Python des blocs de code).
   - Supprime les doublons de `hyperref` et `hypersetup`, car cette correction est sans ambiguïté.
   - Affiche un rapport JSON (`pret_a_compiler`).
4. **`appliquer_hyperref_etape5.py`** : insère dans `course.tex`, avant `\begin{document}`, le bloc `hyperref` / `cleveref` avec le titre du cours. Sans effet si déjà présent.

### Étape 6 : compilation et publication

**`Etape6.py`** : orchestre l'étape.

- Entrée : le chemin du PDF. Exige `course.tex`.
- Fait : lance Claude avec `prompt_etape6.md`, puis vérifie qu'un PDF existe dans `out/` et dans `corpuscondense/<slug>.pdf`.
- Au rendu final, affiche `COMPILATION REUSSIE !!!!`.
- Écrit (via Claude) : rien de plus que ce que font les scripts ci-dessous.

Scripts utilisés, dans l'ordre :

1. **`compiler.py`** : compile `course.tex` avec `pdflatex`. Il relance la compilation si LaTeX le demande, dans la limite de 5 passes.
   - Entrée : le chemin de `course.tex` et le nom du PDF final.
   - Écrit : `out/<nom>.pdf`. Supprime les fichiers auxiliaires (`.aux`, `.log`, `.toc`, `.out`).
2. **`publish_condense.py`** : copie le PDF le plus récent de `out/` vers `corpuscondense/<slug>.pdf`.
   - Entrée : le dossier du pdf.
   - Code `1` si aucun PDF n'est trouvé.

## Hors flux principal

- **Sans argument** : `Etape0_lister_non_condenses.py` est le seul script lancé. Il affiche `NB_NON_CONDENSES: <nombre>`, puis la liste numérotée des livres de `corpusdedepart/` absents de `corpuscondense/`. Il ne lit aucun argument obligatoire, ne crée ni ne modifie rien, et renvoie `2` en cas de mauvais usage.
- **Fichiers présents mais non appelés par le flux** (vérifié par recherche dans le code) :
  - `verifier_deja_condense.py` (à la racine de `scripts/`) : doublon de `Etape0_verifier_deja_condense.py`.
  - `lib/calibrage.py`, `lib/concatener_pages.py`, `lib/illustration_utils.py`, `lib/extraire_code_exact.py` : les étapes utilisent les versions placées dans `scripts/`.
  - Dans `lib/commun.py` : `lancer_avec_verrou`, `call_claude_session` et `assembler_course` ne sont appelés par aucune étape.

## Arborescence

```
cours-condense/
├── SKILL.md                                # fiche de la commande (consignes pour Claude)
├── README.md                               # ce fichier
└── scripts/
    ├── Etape0_lister_non_condenses.py      # sans argument : liste des livres à condenser
    ├── Etape0_verifier_deja_condense.py    # étape 0 : le pdf est-il déjà condensé ?
    ├── Etape1.py                           # étape 1 : plan du cours
    ├── Etape2.py                           # étape 2 : illustrations
    ├── Etape3.py                           # étape 3 : rédaction
    ├── Etape4.py                           # étape 4 : mise en forme
    ├── Etape5.py                           # étape 5 : contrôle qualité
    ├── Etape6.py                           # étape 6 : compilation et publication
    ├── initialiser_arborescence.py         # étape 1 : crée le dossier de travail
    ├── extraire_structure.py               # étape 1 : extrait structure.json et les pages
    ├── concatener_pages.py                 # étape 1 : assemble un lot de pages
    ├── verifier_plan_cours.py              # étape 1 : contrôle du plan
    ├── verifier_calibrage_visuels.py       # étape 1 : contrôle du nombre d'illustrations
    ├── illustration_utils.py               # étape 2 : aide au dessin et à la mise en page
    ├── verifier_illustrations.py           # étape 2 : contrôle des illustrations
    ├── initialiser_course_tex.py           # étape 3 : squelette de course.tex
    ├── verifier_glossaire.py               # étape 3 : contrôle du glossaire
    ├── extraire_code_exact.py              # étape 3 : extraction du code depuis le PDF
    ├── verifier_structure_course_tex.py    # étape 3 : contrôle de la structure de course.tex
    ├── appliquer_preambule_etape4.py       # étape 4 : bloc fixe de mise en forme
    ├── inserer_needspace_listings.py       # étape 4 : \Needspace avant les blocs de code
    ├── normaliser_listings_etape4.py       # étape 4 : caractères des blocs de code
    ├── corriger_accents.py                 # étape 5 : correction des accents
    ├── lister_noms_propres.py              # étape 5 : relevé des noms propres accentués
    ├── controle_qualite.py                 # étape 5 : contrôle qualité de course.tex
    ├── appliquer_hyperref_etape5.py        # étape 5 : bloc hyperref / cleveref
    ├── compiler.py                         # étape 6 : compilation pdflatex
    ├── publish_condense.py                 # étape 6 : copie vers corpuscondense/
    ├── verifier_deja_condense.py           # non appelé (doublon, hors flux)
    ├── prompts/
    │   ├── prompt_etape1.md                # consignes de Claude pour l'étape 1
    │   ├── prompt_etape2.md                # consignes de Claude pour l'étape 2
    │   ├── prompt_etape3.md                # consignes de Claude pour l'étape 3
    │   ├── prompt_etape4.md                # consignes de Claude pour l'étape 4
    │   ├── prompt_etape5.md                # consignes de Claude pour l'étape 5
    │   └── prompt_etape6.md                # consignes de Claude pour l'étape 6
    └── lib/
        ├── commun.py                       # socle commun : appels claude, modèle, chemins, vérifications
        ├── session_claude.py               # client claude -p à session (importé par commun.py)
        ├── slug.py                         # calcul du slug du pdf
        ├── calibrage.py                    # borne du nombre de visuels (non appelé)
        ├── concatener_pages.py             # copie de concatener_pages.py (non appelé)
        ├── extraire_code_exact.py          # copie de extraire_code_exact.py (non appelé)
        └── illustration_utils.py           # copie de illustration_utils.py (non appelé)
```
