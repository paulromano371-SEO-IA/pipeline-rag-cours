## Étape 5 — Contrôle + configuration pré-compilation

1. Exécute d'abord le correcteur automatique d'accents:
   
   ```bash
      "<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/cours-condense/scripts/corriger_accents.py" "<nom_du_livre>/course.tex"
   ```
   
   Ce script combine plusieurs outils complémentaires (dictionnaire français, LanguageTool, analyse grammaticale spaCy, heuristiques a/à et ou/où) pour corriger automatiquement, en boucle jusqu'à stabilisation, la grande majorité des accents manquants — un simple dictionnaire ne suffit pas : il ne voit ni les homographes grammaticaux (a/à, ou/où — les deux formes sont chacune un mot valide), ni les conjugaisons ambiguës (un verbe du 1er groupe au présent et son participe passé masculin singulier sont orthographiquement identiques sans accent, ex. "impressionne"). Le script journalise chaque correction appliquée ; affiche un résumé chiffré par outil dans le compte-rendu de cette étape. Ne modifie jamais le code (`lstlisting`/`\lstinline`/commentaires). Au premier lancement sur un poste donné, télécharge automatiquement ses dépendances (JRE portable, modèle spaCy) — normal, pas une erreur.

2. Noms propres, outils et marques accentués à tort. Exécute le relevé suivant, qui liste les mots de la prose écrits avec un accent alors que le texte anglais source les écrit sans accent :
   
   ```bash
      "<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/cours-condense/scripts/lister_noms_propres.py" "<nom_du_livre>/course.tex" "<nom_du_livre>"
   ```
   
   Pour chaque candidat du rapport (`mot`, `forme_source`, `contexte`), décide, en lisant le contexte :
   
   - **Nom propre, outil, bibliothèque, marque, mot-clé de code** (ex. « Azure », « OpenAI », « SELECT », « FAISS ») : rétablis la forme de la source (`forme_source`), sans accent. Ce mot n'est jamais du français.
   
   - **Mot français normal qui se trouve avoir un cousin anglais** (ex. « Intégration », « Décision », « Stratégies », « Réflexion », « Génération », « Rôle ») : garde l'accent. Ce n'est pas un nom propre.
   
   - **Doute** : ne corrige pas, signale-le dans le compte-rendu avec ta question.
     
     Pour chaque mot rétabli comme nom propre, ajoute une entrée dans `<nom_du_livre>/glossaire.json` avec la forme à garder et la variante accentuée dans `interdits` (ex. `"Azure": {"traduction": "Azure", "interdits": ["Azuré"]}`), pour que `verifier_glossaire.py` la contrôle ensuite. Ne modifie que les mots listés par le relevé, et justifie chaque décision dans le compte-rendu.

3. Exécute ensuite le contrôle qualité, sur le fichier déjà corrigé :
   
   ```bash
      "<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/cours-condense/scripts/controle_qualite.py" "<nom_du_livre>/course.tex"
   ```
   
   Cela vérifie automatiquement :
   
   - absence de "youtube", "vidéo", "timestamp"
   - **absence d'accent manquant sur les mots français courants de la prose** (résiduel après l'étape 1 ci-dessus — voir la règle absolue d'accentuation en tête de ce fichier) ; les blocs `lstlisting` et `\lstinline` sont exclus de ce contrôle
   - absence de repère de citation numérote (`[1]`, `[2]`, etc.)
   - validité syntaxique de chaque bloc `lstlisting` Python via `ast.parse()`
   - un seul `\usepackage{hyperref}` et un seul `\hypersetup{...}` dans tout le fichier (le script supprime lui-même les doublons éventuels — c'est la seule correction automatique de cette étape)

4. Vérifie en plus, par lecture, qu'il ne reste pas de paragraphes explicatifs non traduits en anglais (les blocs `lstlisting` sont exclus, ils gardent leur langue d'origine) — ce point est qualitatif, pas automatisable de manière fiable par regex.

5. Si un problème est détecté (mots interdits, accents manquants, citations, `ast_parse` en échec, paragraphe non traduit) : liste-le précisément (ligne + contexte) et **arrête-toi là, sans compiler**. Le résiduel après l'étape 1 est normalement faible (de l'ordre de la dizaine d'occurrences, principalement des noms de fichiers d'illustrations dans `\includegraphics{...}`, jamais du texte affiché) ; pour ce qui reste, comme pour les mots interdits, certaines occurrences peuvent être de faux positifs légitimes (forme conjuguée correcte sans accent, nom propre technique délibérément conservé en anglais, chemin de fichier) : juge au cas par cas et justifie la décision dans le compte-rendu, plutôt que de corriger ou d'ignorer silencieusement. Le rapport `accents_homographes_suspects` de `controle_qualite.py` est indicatif seulement (jamais bloquant) : c'est une heuristique de secours, moins fiable que ce que `corriger_accents.py` a déjà appliqué en amont.

6. Sinon, ajoute au préambule de `course.tex` (une seule fois) le bloc fixe `hyperref`/`cleveref` — même catégorie que les blocs figés de l'étape 4 : gabarit inséré par script, jamais retapé à la main :
   
   ```bash
      "<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/cours-condense/scripts/appliquer_hyperref_etape5.py" "<nom_du_livre>" "<titre_du_cours>"
   ```
   
   Ce script insère, une seule fois et juste avant `\begin{document}`, `\usepackage[colorlinks=true, ...]{hyperref}`, `\hypersetup{...}` et `\usepackage{cleveref}` (dans cet ordre : `cleveref` doit venir juste après `hyperref`). Idempotent.

---