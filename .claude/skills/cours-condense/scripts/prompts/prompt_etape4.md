## Etape 4 — Mise en forme LaTeX

Applique ces règles à `course.tex` déjà crée, **sans creer de 2e version**.
Ne compile pas le PDF à cette étape.

**Blocs fixes (règles 1, 2, 3, 5 première moitié, 7 ci-dessous) : insérés par script, jamais retapés à la main.** Un bloc à zéro variation d'un livre à l'autre, retapé manuellement, s'est déjà corrompu cette session (`a4paper`→`à4paper`, `1a365d`→`1à365d`, via une correction d'accents mal ciblée passée dessus plus tard). Exécute en premier :

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/cours-condense/scripts/appliquer_preambule_etape4.py" "<nom_du_livre>"
```

Ce script insère, une seule fois et juste avant `\begin{document}`, l'intégralité des blocs LaTeX fixes des règles 1, 2, 3, 5 (première moitié) et 7 ci-dessous (listings, ligatures/langue/encadrés, tableaux, anti-coupures section/subsection, marges/titres de chapitre). Idempotent : ne fait rien s'il a déjà tourné. Le contenu exact de chaque bloc reste documenté ci-dessous pour référence, mais ne le retape jamais toi-même dans `course.tex`.

1. **Code (listings, pas minted).** Bloc fixe (voir script ci-dessus) définissant le style `pythonstyle` (couleurs `coursebleu`/`courseorange`, police, numérotation). Utilise `lstlisting`, caption FR (code inchangé), label par bloc (ex "Listing 3.1"). Variable : `\lstinline{nom_variable}`.

2. **Ligatures, langue et encadrés.** Bloc fixe (voir script ci-dessus) chargeant `inputenc`/`fontenc`/`babel[french]`/`caption` et définissant `conceptbox`/`warningbox`/`rememberbox`. `babel[french]` est indispensable : sans lui, `\og`/`\fg{}` (guillemets français) provoquent une erreur de compilation fatale. `caption` est requis si un tableau hors flottant utilise `\captionof{table}{...}`.
   
   **Usage des encadrés (`conceptbox`/`warningbox`/`rememberbox`) : au plus 1 à 2 par chapitre**, réservés à une idée réellement centrale ou un piège fréquent que l'auteur souligne lui-même — jamais utilisés pour empiler des définitions à la chaîne (un encadré par concept transformerait le chapitre en fiches, ce que la règle 0 de l'étape 3 interdit déjà pour le corps du texte).

3. **Tableaux (tabularx, hors code).** Bloc fixe (voir script ci-dessus) chargeant `tabularx` et le type de colonne `Y`.
   Structure : 1ère colonne (intitulé, courte) fixe modérée ; colonnes
   suivantes en `Y`, partageant la largeur restante à parts égales, avec
   retour a la ligne.
   
   Tableau à 3 colonnes (intitulè + 2 contenu) :
   
   ```latex
   \begin{tabularx}{\textwidth}{|p{0.18\textwidth}|Y|Y|}
   ```
   
   Ne mets jamais deux colonnes fixes (c, l, r) ou plus avant une colonne
   longue : une seule Y n'absorbe pas la largeur manquante des colonnes
   rigides voisines, d'ou écrasement et débordement.

4. Schémas d'architectures en TikZ natif (`\usepackage{tikz}`).

5. **Anti-coupures de page.** Bloc fixe (voir script ci-dessus) pour la partie section/subsection : `needspace`/`etoolbox`/`placeins`, `\raggedbottom`, et les `\pretocmd` sur `\section`/`\subsection`. Needspace a 10/7 `\baselineskip` : garantit ~4 lignes de contenu après le
   titre, pour qu'il ne reste jamais seul en bas de page. `\FloatBarrier`
   (placeins) empèche tout flottant de dépasser sa section.
   
   **Deuxième moitié (par lstlisting) — script séparé, à exécuter juste après `appliquer_preambule_etape4.py` :**
   
   ```bash
   "<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/cours-condense/scripts/inserer_needspace_listings.py" "<nom_du_livre>"
   ```
   
   Ce script compte lui-même les lignes réelles de chaque `lstlisting` et insère `\Needspace{(N+5)\baselineskip}` juste avant, sauf s'il dépasse une page pleine (coupure alors inévitable, pas un défaut). C'est cette moitié de la règle, purement mécanique mais dépourvue de script jusqu'ici, dont l'omission a produit des blocs de code coupés en deux pages dans une version antérieure du cours — ne t'y substitue plus manuellement, ce script s'en charge pour chacun des `lstlisting` du fichier en une seule exécution.
   
   ```latex
   \Needspace{(N+5)\baselineskip}
   ```
   
   où N = nombre de lignes (+5 légende/label/marges). Un bloc tenant sur une
   page ne démarre jamais à quelques lignes de sa fin : entier sur place, ou
   repousse en début de la suivante.
   
   Exception : si N+5 dépasse les lignes utiles d'une page (45-55 en
   `\small`), pas de `\Needspace` — bloc déjà plus long qu'une page, coupure
   inévitable, pas un defaut.

6. Titres/en-têtes : jamais de `\mbox`, `\fbox`, `\parbox` à largeur fixe, ni
   de conteneur bloquant le retour a la ligne d'un titre/en-tête/pied de
   page. Pas d'en-tête/pied personnalise (fancyhdr) reproduisant le titre du
   chapitre, sauf demande explicite. Ces interdictions sont vérifiées mécaniquement par `controle_qualite.py` à l'étape 5 (clé `mise_en_forme_interdite` du rapport) — un pattern-matching pur, sans jugement.

7. **Marges et mise en page.** Bloc fixe (voir script `appliquer_preambule_etape4.py` en tête de cette étape) chargeant `geometry`/`titlesec` et réglant `\titleformat`/`\titlespacing` du chapitre.
   Marges identiques partout : `\titlespacing` neutralise l'espace que la
   classe ajoute par défaut au-dessus d'un titre de chapitre en début de
   page. Si `\section` délimite les chapitres (pas `\chapter`), applique-lui
   le même réglage.

**Résumé** : confirme que `course.tex` est complet (tous les chapitres du plan présents).

---