## Étape 1 — Cartographie du livre + plan condensé

1. Slugifie le nom du livre et crée l'arborescence ci-dessus — mécanique, jamais recalculé à la main :
   
   ```bash
   "<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/cours-condense/scripts/initialiser_arborescence.py" "<chemin_du_livre.pdf>"
   ```
   
   Le script affiche `SLUG: <slug>` et `DOSSIER: <chemin>` : reprends cette valeur **`DOSSIER`** telle quelle comme `<nom_du_livre>` pour toute la suite du pipeline (jamais la valeur `SLUG` seule — voir "Arborescence de travail" ci-dessus), et ne la recalcule jamais toi-même (source d'incohérence si deux calculs manuels divergent en cours de session). Idempotent : n'écrase ni ne supprime rien si l'arborescence existe déjà.

2. Exécute la cartographie mécanique :
   
   ```bash
   "<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/cours-condense/scripts/extraire_structure.py" "<chemin_du_livre.pdf>" "<nom_du_livre>/extraction"
   ```
   
   Cela donne le nombre total de pages, la table des matières (si présente dans le PDF), et pour chaque page : nombre de mentions "Figure X.Y" et un score de probabilité "contient du code".

3. Lis `structure.json`, puis lis le contenu de `extraction/pages/*.txt` par lots **regroupés par chapitre ou partie du livre, tels que repérés dans la table des matières** — jamais par tranches de lignes ou de pages arbitraires détachées de cette structure. Chaque lot doit correspondre à une unité de sens du livre (un chapitre, ou deux chapitres courts regroupés), pas à un découpage mécanique par nombre de lignes : le découpage doit être identique d'une exécution à l'autre pour un même livre, puisqu'il est entièrement déterminé par sa table des matières, jamais par un choix arbitraire fait en cours de lecture. Objectif de cette lecture : analyser l'intégralité du livre — structure complète (parties, chapitres, sous-sections), et repérage des sections contenant du code Python (croisé le score `probable_code` avec une lecture réelle — le score est un indice, pas une vérité).
   
   Pour produire chaque lot à lire, une fois les bornes `page_debut`/`page_fin` de l'unité déterminées à partir de la table des matières, utilise :
   
   ```bash
   "<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/cours-condense/scripts/concatener_pages.py" "<nom_du_livre>" <page_debut> <page_fin>
   ```
   
   plutôt qu'une boucle Bash improvisée (`seq`/`printf`) : le padding sur 4 chiffres y est fixe et ne dépend jamais de la largeur de la plage, contrairement à `seq -w` qui change de comportement dès qu'une plage franchit un palier de dizaine/centaine.

4. Avant toute rédaction, élabore un plan de cours condensé qui respecte **strictement** la chronologie du livre original. Ne fixe **aucun ratio de compression ni nombre de pages cible** a priori : la longueur finale doit découler uniquement du contenu réellement essentiel. Pour chaque grande partie du livre, décide combien de chapitres de synthèse sont nécessaires en fusionnant les sous-sections secondaires ou redondantes autour d'un même concept clé, sans jamais sauter une notion essentielle.

5. Enregistre ce plan dans `<nom_du_livre>/plan_cours.json`. Pour chaque chapitre cible :
   
   - `numero` : rang du chapitre (entier, 1-indexé, dans l'ordre chronologique du plan) — requis : c'est ce numéro que `verifier_illustrations.py` utilise pour nommer/retrouver les illustrations (`chNN_concept.png`, `illustrations_chapitreN.py`) et que `verifier_structure_course_tex.py`/`verifier_calibrage_visuels.py` utilisent pour identifier le chapitre dans leurs rapports — jamais recalculé implicitement par ces scripts si absent
   - `titre` (en français)
   - `sections_source` : les sections du livre original que ce chapitre synthétise
   - `pages_source` : `[page_debut, page_fin]` (1-indexé, inclusif) dans le PDF source couvertes par ce chapitre — requis pour le calibrage mécanique ci-dessous, pas seulement une note informative
   - `contient_code` (true/false) : ce chapitre du livre source comporte-t-il des extraits de code Python à reprendre ?
   - `concepts_cles_visuels` : liste des concepts clés de ce chapitre qui nécessitent réellement un support visuel pour être compris

Un concept mérite un visuel s'il décrit une architecture, un processus à étapes, une comparaison quantitative, une relation structurelle entre plusieurs éléments, ou une fonction/courbe mathématique. Un simple concept défini par une phrase (sans structure spatiale, relationnelle ou comparative) n'en nécessite pas.

N'impose **aucun nombre minimum ou maximum** a priori : liste tous les concepts du chapitre qui remplissent ce critère, qu'il y en ait un seul ou N.

**Validation structurelle obligatoire et mécanique, avant le calibrage visuel ci-dessous :**
Une fois `plan_cours.json` écrit une première fois, exécute :

```bash
   "<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/cours-condense/scripts/verifier_plan_cours.py" "<nom_du_livre>/plan_cours.json" "<nom_du_livre>/extraction/structure.json"
```

Ce script ne corrige rien : il vérifie que chaque chapitre a ses champs obligatoires du bon type, que chaque `pages_source` est valide (bornes croissantes, dans les limites du livre), et qu'aucun chevauchement de pages ni désordre chronologique n'existe entre deux chapitres consécutifs (la règle "chronologie stricte" de l'étape 1 devient ainsi vérifiable plutôt que laissée à l'auto-discipline). Corrige `plan_cours.json` et ré-exécute jusqu'à `"pret": true` avant de poursuivre. Un trou entre deux chapitres (préface, annexe exclue, index...) n'est pas une anomalie — seul un chevauchement ou un champ invalide l'est.

**Calibrage obligatoire et mécanique (ne pas sauter — une version en simple recommandation en prose de cette règle a déjà échoué une fois : sous-comptage silencieux des concepts visuels sur plusieurs chapitres sans qu'aucune vérification ne le signale) :**
Une fois `plan_cours.json` validé structurellement ci-dessus, exécute :

```bash
   "<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/cours-condense/scripts/verifier_calibrage_visuels.py" "<nom_du_livre>/plan_cours.json" "<nom_du_livre>/extraction/structure.json"
```

Ce script est un contrôle mécanique, pas une correction automatique : il ne modifie rien, il calcule la somme `nb_mentions_figure` par chapitre et signale dans `chapitres_a_verifier` tout chapitre dont le nombre de `concepts_cles_visuels` parait nettement bas par rapport à la densité de figures des autres chapitres du même livre.
Pour chaque chapitre listé dans `chapitres_a_verifier` : retourne relire ce chapitre à la recherche de concepts structurels laissés de côté (schéma d'architecture non repéré, processus à étapes décrit en prose sans figure captionnée dans le livre mais qui reste un "processus à étapes" au sens de la règle ci-dessus, relation entre plusieurs entités, etc.), complète `concepts_cles_visuels` en conséquence, puis **ré-exécute le script** jusqu'à ce que `chapitres_a_verifier` soit vide ou que chaque chapitre restant y figurant soit explicitement justifié (voir format de compte-rendu).
Ce n'est pas une contrainte stricte de conversion 1:1 (les mentions comptent aussi les répétitions du même renvoi et les figures qui sont de simples captures d'écran sans structure, donc le nombre final de concepts visuels reste inférieur au nombre de mentions) — mais un chapitre signalé dont on garde le compte bas doit l'être par un jugement explicite et justifié, jamais par un oubli de vérification.

**Résumé** : présente un résumé du plan (nombre de chapitres, titres, total de concepts visuels) **incluant le tableau des sommes `nb_mentions_figure` par chapitre calculé ci-dessus**.

---
