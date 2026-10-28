## Étape 3 — Rédaction du cours condensé en français (contenu)

Rédige toi-même le contenu français de chaque chapitre (traduction adaptée, condensation, mise en \section/\subsection) — c'est un travail de compréhension et de style, pas une règle mécanique automatisable. Le squelette initial de `course.tex` est déjà en place (règle 1 ci-dessous, via `initialiser_course_tex.py`) ; pour rédiger le contenu chapitre par chapitre, utilise un script Python `<nom_du_livre>/scripts/ecrire_course_tex.py` qui ouvre `course.tex` **en mode append (`'a'`)** et y écrit le texte que tu lui fournis, dans l'ordre du plan — cela évite toute troncature en cas d'exécution partielle. 
Ce même script appelle `extraire_code_exact.py` lorsqu'un chapitre a `contient_code: true`, pour insérer le code source sans jamais le retaper à la main.

Règles impératives :

0. Objectif : cours vivant, chaleureux, détaillé, intuitif (physique/géométrique), fidèle au style de l'auteur, en français, sans rigidité académique, mathématiquement rigoureux, code intact. 
   Longueur non fixée : découle de la règle 3.
   
   **Ce que "chaleureux/intuitif" veut dire concrètement** (cette règle a déjà été violée par le passé en produisant un enchaînement de fiches de type glossaire — ne pas reproduire) :
   
   - Une section ne doit jamais enchaîner plus de 2-3 items consécutifs au format « **Terme en gras** : définition courte » sans phrase de liaison entre eux. Si le plan ou la source suggère une longue énumération de concepts, choisis les 2-3 plus importants pour un développement en prose et regroupe le reste en une phrase de synthèse plutôt que d'aligner une fiche par concept.
   - Quand le livre développe une idée en paragraphes suivis (le cas le plus fréquent dans la plupart des livres techniques), la traduction reste en paragraphes suivis : la mise en liste ne s'applique qu'aux passages qui sont déjà des listes dans le livre source. Ne convertis jamais un raisonnement continu de l'auteur en une liste à puces sous prétexte de condenser — condense la prose en prose, pas en énumération.
   - Conserve les exemples filés, les analogies et les mises en situation de l'auteur (pas seulement leur conclusion) : c'est ce qui rend un cours intuitif plutôt qu'une suite de définitions correctes mais désincarnées.
   - Respecte l'ordre pédagogique de l'auteur : si le livre introduit une notion par une image, une anecdote ou une question avant de la formaliser, conserve cet ordre en français — ne fais jamais remonter la définition formelle avant l'intuition qui la prépare, même si cela semblerait plus « logique » dans un plan condensé.
   - Termine chaque section ou chapitre (hors le Résumé de fin de chapitre, règle 11) par une phrase de transition qui annonce ce qui suit, à la manière de l'auteur — jamais une liste de puces en guise de conclusion de section.

1. Structure : initialise `course.tex` avec le squelette figé (`\documentclass[a4paper]{report}`, `\title`, `\author`, `\date`, puis `\maketitle`, `\tableofcontents`, `\newpage` avant le 1er `\chapter`) via :
   
   ```bash
   "<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/cours-condense/scripts/initialiser_course_tex.py" "<nom_du_livre>" "<titre français fidèle au sujet du livre>"
   ```
   
   plutôt que de le retaper à la main : un bloc à zéro variation près (seul le titre change) retapé manuellement s'est déjà corrompu une fois cette session (une correction d'accents mal ciblée a changé `a4paper` en `à4paper` directement dans le fichier, faisant planter la compilation).

2. Traduction adaptée, jamais mot-à-mot : français fluide, adapte les analogies de l'auteur, italique pour termes anglais sans équivalent (*overfitting*, *dropout*, *embedding*). Cet italique inline, au fil de la phrase, est la **seule** forme d'ancrage terminologique dans le corps du texte : n'extrais jamais ces termes dans un encart ou une liste de définitions séparée du raisonnement qui les introduit.

3. Cohérence terminologique : pour tout terme récurrent, fixe sa traduction dès la 1ère occurrence et réutilise-la à l'identique partout. 
    Consigne ces choix dans `<nom_du_livre>/glossaire.json` (anglais → traduction), consulte-le avant toute traduction. 
    Pour "agentic AI" : toujours "IA agentique", jamais "IA agentielle" (ou l'équivalent pertinent pour le livre en cours si le sujet diffère).
   
    Quand une variante fautive connue existe pour un terme (comme "IA agentielle" ci-dessus), consigne-la explicitement dans `glossaire.json` sous la forme `{"agentic AI": {"traduction": "IA agentique", "interdits": ["IA agentielle"]}}` plutôt que la forme simple `{"terme": "traduction"}` — cela rend la cohérence vérifiable mécaniquement (voir ci-dessous) plutôt que de dépendre uniquement de la vigilance en cours de rédaction.
   
    **Vérification mécanique :**
   
   ```bash
   "<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/cours-condense/scripts/verifier_glossaire.py" "<nom_du_livre>/glossaire.json" "<nom_du_livre>/course.tex"
   ```
   
    Ce script signale toute occurrence d'une variante listée dans `interdits`. Il ne peut rien détecter sur un terme sans `interdits` renseigné : chaque fois qu'une confusion de traduction est identifiée en te relisant, ajoute-la à `interdits` pour ce terme afin qu'elle soit automatiquement détectée dans le reste du cours.

4. Plan strict : Introduction puis un chapitre par entrée du plan, ordre chronologique, sans chapitre inventé.

5. Compression : élague uniquement démonstrations secondaires, exemples redondants et digressions ; garde un seul exemple travaillé par notion, conserve intégralement le nécessaire à la compréhension de chaque notion essentielle de `plan_cours.json`. 
   N'élague jamais pour une longueur cible : elle résulte de l'élagage, jamais un objectif.
   
   **Ne jamais abstraire un exemple concret et nommé du livre** (entreprise, personne, chiffre, cas réel, incident cité par l'auteur) en une formulation générique lors de la condensation — un exemple qui perd son nom perd aussi son pouvoir d'ancrage intuitif. Si l'exemple doit être élagué faute de place, élague-le dans son intégralité (garde-en un autre, concret, à sa place) plutôt que de le vider de ses détails concrets pour le raccourcir.

6. **CODE — reproduction intégrale, sans exception.** Si `contient_code` est vrai pour ce chapitre : repère dans le PDF source (via `structure.json`, champ `probable_code`) les pages concernées, puis extrais le code avec :
   
   ```python
      from extraire_code_exact import extraire_code_pages
      code = extraire_code_pages(chemin_pdf, page_debut, page_fin, bbox=...)
   ```
   
    Ce script préserve déjà la position x/y de chaque caractère (extraction en flux continu reconstruisant les mots par proximité), regroupe les caractères de même ligne physique, 
    et n'insère un espace que si l'écart dépasse nettement la largeur d'un caractère. **Vérifie néanmoins le résultat à l'œil** avant de l'insérer : aucune traduction, correction ou simplification 
    même en cas d'erreur apparente ; jamais de troncature ("...") ; la compression ne s'applique **jamais** au code.
   
    Chaque `lstlisting` est immédiatement suivi, avant la section suivante, d'une à trois phrases qui en tirent la conséquence pratique ou le résultat obtenu — jamais un bloc de code laissé sans suite. Ne te contente pas d'introduire le code avant ("voici comment...") sans rien en dire après. Vérifiable mécaniquement (voir `verifier_structure_course_tex.py` en fin de règle 7 ci-dessous), au même titre que la couverture `contient_code` par chapitre.

7. Insère les images PNG générées en flottant LaTeX, jamais en insertion brute :
   
   ```latex
      \begin{figure}[!htbp]
       \centering
       \includegraphics[width=0.85\linewidth]{illustrations/...}
       \caption{...}
       \label{fig:...}
      \end{figure}
   ```
   
   `[!htbp]` laisse LaTeX choisir l'endroit optimal plutôt que de forcer une position qui pousse le reste de la page en blanc.
   
    Proximité : place chaque image juste après le paragraphe expliquant le concept illustré — jamais entre deux `lstlisting` du même exemple continu (règle 6). Si le concept tombe en plein milieu du code, repousse l'image après, ou place-la avant — jamais dedans.

**Vérification mécanique :**

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/cours-condense/scripts/verifier_structure_course_tex.py" "<nom_du_livre>/course.tex" "<nom_du_livre>/plan_cours.json"
```

Ce script ne corrige rien : il vérifie que chaque `lstlisting` est bien suivi d'une phrase d'explication (règle 6), que chaque chapitre `contient_code: true` a effectivement du code et inversement (règle 6), que chaque `\includegraphics` est dans un `figure`/`caption`/`label` complet (règle 7), et que chaque chapitre se termine par un `\section*{Résumé}` (règle 11 ci-dessous). Corrige les anomalies signalées et ré-exécute jusqu'à `"pret": true`.
La clé `figures_entre_listings_a_verifier` du rapport est indicative seulement (jamais bloquante, comme `accents_homographes_suspects` à l'étape 5) : distinguer une figure qui coupe un exemple continu d'une figure qui sépare légitimement deux exemples déjà expliqués exige de comprendre le rapport entre les deux blocs de code, ce qu'aucun motif syntaxique ne capture de manière fiable — juge chaque occurrence signalée au cas par cas.

8. Aucun repère de citation (`[1]`, `[2]`).

9. **Relecture stylistique active, chapitre par chapitre — avant de passer au chapitre suivant.** Une fois un chapitre rédigé, relis-le en te posant une seule question, phrase par phrase : *ce texte explique-t-il et relie-t-il les idées entre elles, ou se contente-t-il de les juxtaposer ?* Concrètement :
   
   - repère tout passage qui ressemble à une fiche de glossaire (voir règle 0) et réécris-le en prose reliée par des transitions ;
   - vérifie qu'un lecteur qui ne connaît pas encore la notion comprendrait *pourquoi* elle compte, pas seulement *ce qu'elle est* ;
   - ne passe au chapitre suivant qu'une fois ce test satisfait pour le chapitre courant — ce n'est pas une relecture finale globale en fin de livre, c'est un filtre appliqué à chaque chapitre au moment où il vient d'être écrit, tant que son contenu est encore présent à l'esprit.
   
   Cette étape ne se substitue pas au format de compte-rendu (section "Format de compte-rendu" ci-dessus) : la confirmation chapitre par chapitre affichée doit mentionner explicitement que cette relecture a été faite, pas seulement que le contenu prévu au plan a été couvert.

10. Ne compile pas le PDF à cette étape.

11. **Résumé de fin de chapitre.** Chaque chapitre se termine par une section non numérotée `\section*{Résumé}` de 3 à 5 puces courtes reprenant les points clés du chapitre. C'est la **seule** section du chapitre où le format liste à puces est utilisé sans restriction — le corps du chapitre, lui, reste soumis à la règle 0 (pas plus de 2-3 items enchaînés, jamais de liste à la place d'un raisonnement continu de l'auteur). Le Résumé ne remplace jamais la relecture stylistique de la règle 9 : c'est une synthèse écrite en plus du corps du chapitre, pas un substitut à un corps qui resterait trop listé. Présence vérifiée mécaniquement par `verifier_structure_course_tex.py` (voir règle 7 ci-dessus) — ne dépend plus d'une relecture manuelle à l'étape 6.
