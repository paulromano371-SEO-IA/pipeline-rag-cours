---
name: cours-condense
description: >-
  Génère un support de cours condensé en français (PDF via LaTeX) à partir d'un livre
  technique complet en PDF, avec illustrations vectorielles originales et code source
  préservé à l'identique.
  Usage: /cours-condense <chemin_vers_livre.pdf> [checkpoint=false].
  Déclenche aussi sur "condense ce livre en cours", "crée un support de cours à partir de ce PDF".
---

# Cours condensé à partir d'un livre (pipeline en 6 étapes) — Etape 1 du pipeline RAG

Reprend le processus validé précédemment dans NotebookLM (6 prompts = 3 volets x 2 étapes), 
adapté à Claude Code : ici tout s'exécute réellement (Python, pdflatex) au lieu d'être simulé dans un "carnet" NotebookLM, 
et les fichiers sont stockés dans le projet plutôt que dans "Studio".

**Règle d'or de reprise de la version originale : ne jamais fixer de longueur cible a priori.** 
Le plan, la rédaction et la compression découlent uniquement du contenu réellement essentiel du livre — jamais d'un ratio de pages ou d'un nombre de chapitres décidé d'avance.

## Règle absolue : accentuation française correcte

Tout texte français produit par ce skill — titres, prose des chapitres, légendes de figures, résumés de checkpoint, contenu des tableaux — doit comporter les accents et diacritiques corrects (é, è, ê, à, â, ç, ô, î, ï, û, œ...). Le fichier LaTeX généré déclare déjà `\usepackage[utf8]{inputenc}`, `\usepackage[T1]{fontenc}` et `\usepackage[french]{babel}` : rien ne justifie techniquement d'écrire sans accent, et un compilateur qui échoue sur un accent est un bug à corriger dans le préambule, jamais une raison de l'omettre.

**Important** : le registre ou l'orthographe du présent fichier SKILL.md, à quelque moment que ce soit de son histoire, n'a **aucune valeur d'exemple** pour le contenu à produire. Ce fichier est une configuration technique interne destinée à être suivie, jamais un modèle stylistique — n'imite jamais son registre, sa concision ou son orthographe dans le cours généré. Le seul modèle de style pour le cours est la règle 0 de l'étape 3 (ton chaleureux, intuitif, fidèle à l'auteur) et le livre source lui-même.

L'étape 5 (contrôle qualité) vérifie mécaniquement l'absence de mots français courants dépourvus de leurs accents ; un échec à ce contrôle bloque la compilation au même titre qu'un mot interdit ou une citation numérotée.

## Arguments de la commande

`/cours-condense <chemin_vers_livre.pdf> [checkpoint=false]`

- **Lecture intégrale, toujours.** Que le livre fasse 50 ou 1000 pages, tous les chapitres du plan sont lus et traités en entier, sans exception. 
  Ne demande **jamais** à l'utilisateur s'il veut lire le livre en entier ou s'arrêter à certains chapitres — cette question ne se pose pas, la réponse est toujours oui. 
  C'est une instruction permanente de ce skill, pas une option à reconfirmer à chaque lancement.
- **`checkpoint=false`** (optionnel, absent par défaut) : désactive les pauses d'attente aux checkpoints des étapes 1, 2, 3 (voir section suivante). 
  Le résumé de chaque checkpoint reste affiché intégralement (l'utilisateur doit toujours pouvoir suivre ce qui a été fait), mais au lieu d'attendre une validation, 
  enchaîne immédiatement sur l'étape suivante dans le même tour de conversation.
- Sans cet argument (comportement par défaut), les checkpoints restent bloquants comme décrit ci-dessous.

## Règle d'exécution (déterminisme)

Ce pipeline doit se dérouler **de façon identique à chaque lancement**, quel 
que soit le livre traité. Sont interdits, à toutes les étapes, sauf demande 
explicite et ponctuelle de l'utilisateur dans le message qui déclenche la commande :

- **Déléguer à un sous-agent** (outil `Agent`) la lecture des pages extraites, 
  l'analyse du contenu ou la rédaction du cours. C'est toi, dans ce même tour de 
  conversation, qui lis directement chaque lot de pages (`Read`) et qui rédiges 
  — jamais un sous-agent "pour économiser du contexte". Si le volume est important, 
  découpe en lots de lecture plus petits, mais reste le seul lecteur/rédacteur.
- **Toute tâche en arrière-plan ou planifiée** (`ScheduleWakeup`, agents async, etc.) pour une étape du pipeline lui-même.

Chaque étape s'exécute avec le même type d'appels d'un livre à l'autre : 
`Bash` pour lancer les scripts utilitaires, 
`Read` pour lire `structure.json`/les pages/`plan_cours.json`, 
`Write` le script d'écriture en mode append pour produire `course.tex`. 
Si une étape semble justifier une approche différente (ex. déléguer, paralléliser), arrête-toi et demande à l'utilisateur avant de déroger 
— n'improvise jamais silencieusement un changement de stratégie d'exécution.

## Arborescence de travail

Pour un livre `chemin/vers/Mon Livre.pdf`, `initialiser_arborescence.py` (étape 1, règle 1) calcule un slug (minuscules, sans accents, espaces -> `_`, ex. `mon_livre`) et crée le dossier de travail `rag_data/courscondense/<slug>/` à la racine du projet (jamais directement à la racine du projet — cette commande doit pouvoir s'executer seule, sans dépendre d'un orchestrateur, tout en gardant les données générées centralisées).

**`<nom_du_livre>` dans tout ce fichier désigne ce dossier de travail complet (`rag_data/courscondense/<slug>/`), jamais le slug seul.** C'est la valeur `DOSSIER:` affichée par `initialiser_arborescence.py`, pas sa valeur `SLUG:` (voir étape 1, règle 1) — toutes les commandes ci-dessous qui utilisent `<nom_du_livre>/...` comme préfixe de chemin (`"<nom_du_livre>/extraction"`, `"<nom_du_livre>/course.tex"`, etc.) supposent ce chemin complet ; un slug nu casserait ces appels dès le premier script de l'étape 1.

Contenu de ce dossier de travail :

```
<nom_du_livre>/
  extraction/
    structure.json          # sortie de extraire_structure.py
    pages/page_0001.txt ...  # texte brut de chaque page du livre
  illustrations/               # PNG générés (une par concept clé du plan)
  plan_cours.json                # plan condensé (étape 1)
  glossaire.json                  # cohérence terminologique (étape 3)
  course.tex                       # cours LaTeX (étapes 3-4)
  scripts/                          # scripts Python générés pour CE livre
  out/                                # PDF final livré à l'utilisateur
```

Les scripts utilitaires réutilisables (communs à tous les livres) sont dans

`.claude/skills/cours-condense/scripts/` :
- `initialiser_arborescence.py` — slugification du nom du livre + création de l'arborescence de travail (racine du pipeline, avant l'étape 1)
- `extraire_structure.py` — cartographie mécanique du PDF (étape 1)
- `concatener_pages.py` — concatène les pages d'un chapitre en un seul fichier à lire, sans risque d'erreur de padding (étape 1, règle 3)
- `verifier_plan_cours.py` — validation structurelle de `plan_cours.json` (champs, bornes de pages, absence de chevauchement chronologique entre chapitres) (étape 1, avant le calibrage visuel)
- `verifier_illustrations.py` — vérification post-génération des illustrations : une image par concept clé (ni plus ni moins), convention de nommage, propreté du code (`matplotlib.use('Agg')`, `plt.close()`, `dpi=300`, sortie `.png`), et absence de couleur codée en dur hors charte (`#1a365d`/`#c25e00`) (étape 2)
- `initialiser_course_tex.py` — écrit le squelette initial figé de `course.tex` (étape 3, règle 1)
- `verifier_glossaire.py` — vérifie l'absence de variante de traduction interdite listée dans `glossaire.json` (étape 3, règle 3)
- `verifier_structure_course_tex.py` — vérifications structurelles sur `course.tex` rédigé : `\section*{Résumé}` en fin de chapitre (règle 11), cohérence `contient_code` avec la présence effective de `lstlisting` (règle 6), chaque `lstlisting` suivi d'une phrase d'explication (règle 6), structure `figure`/`caption`/`label` (règle 7) (étape 3/4)
- `appliquer_preambule_etape4.py` — insère une seule fois, avant `\begin{document}`, les blocs LaTeX fixes des règles 1, 2, 3, 5 (1ère moitié) et 7 (étape 4)
- `inserer_needspace_listings.py` — insère `\Needspace{(N+5)\baselineskip}` avant chaque `lstlisting` qui en est dépourvu, sauf s'il dépasse une page pleine (étape 4, règle 5, 2ème moitié)
- `appliquer_hyperref_etape5.py` — insère une seule fois, avant `\begin{document}`, le bloc fixe `hyperref`/`cleveref` (étape 5, règle 5)
- `extraire_code_exact.py` — extraction de code à position de caractère exacte (étape 3, règle 4)
- `illustration_utils.py` — vérification anti-chevauchement des illustrations (étape 2)
- `corriger_accents.py` — correction automatique des accents manquants (dictionnaire + LanguageTool + spaCy + heuristiques a/à et ou/où), en boucle jusqu'à stabilisation (étape 5, avant le contrôle qualité) ; corrige aussi les légendes `caption=...` des `lstlisting`, jamais le code
- `controle_qualite.py` — contrôles automatiques pré-compilation (étape 5), y compris les interdictions de mise en forme de l'étape 4 règle 6 (`\mbox`, `\fbox`, `\parbox`, fancyhdr custom)
- `compiler.py` — compilation pdflatex (2 à 5 passes, jusqu'à stabilisation des références croisées) + rapport + livraison (étape 6)

## Points d'arrêt (checkpoints)

Comme dans le processus original où chaque étape était déclenchée séparément par l'utilisateur, marque une pause avec un résumé court après :
1. la création de `plan_cours.json` (étape 1) — le plan conditionne tout le reste
2. la génération des illustrations (étape 2)
3. la rédaction complète de `course.tex` (étapes 3+4)

avant de lancer la compilation finale (étape 6), qui elle s'exécute après l'étape 5 (contrôles) sans repasser par l'utilisateur si les contrôles passent.

Ce comportement bloquant est **le défaut**. Il change dans deux cas, dans cet ordre de priorité :
- si l'invocation contient `checkpoint=false` (voir "Arguments de la commande" ci-dessus) : affiche le résumé complet du checkpoint puis enchaîne automatiquement sur l'étape suivante, sans attendre de réponse ;
- sinon, si l'utilisateur donne dans son message déclencheur l'instruction explicite d'enchaîner tout le pipeline sans pause, respecte-la de la même façon pour ce lancement.

## Format de compte-rendu

Ce format s'applique **quel que soit le livre traité** — il décrit une structure de compte-rendu, jamais un contenu figé.

1. **Pendant le travail** : après chaque action significative (exécution d'un ou plusieurs scripts, lecture d'un lot de fichiers, écriture d'un fichier), 
   affiche une seule ligne courte au fil de l'eau décrivant l'action et son résultat immédiat 
   (ex. "Exécute N commande(s), lu `<fichier>`", "Chapitre X lu intégralement", "Crée `<fichier>`+L-S"). 
   Pas de paragraphe explicatif à chaque étape intermédiaire — la ligne suffit.
2. **À chaque checkpoint** (fin d'étape 1, 2, 3), affiche un résumé structuré comprenant, dans cet ordre :
   - un titre court ("Étape N terminée — <objet>")
   - une ou deux phrases de synthèse sur ce qui a été fait et comment (méthode suivie, fidélité à la source)
   - **pour l'étape 1** : un tableau avec une ligne par chapitre du plan condensé, colonnes = numéro, titre du chapitre, présence de code (oui/non), somme `nb_mentions_figure` du chapitre (calcul mécanique obligatoire, voir étape 1 règle 5), nombre d'illustrations prévues ; suivi d'une ligne de total (nombre de chapitres, total d'illustrations). Si le nombre d'illustrations d'un chapitre parait bas au regard de sa somme `nb_mentions_figure` relativement aux autres chapitres, la justification (pourquoi ce chapitre a moins besoin de visuels malgré ses mentions) doit apparaitre dans les décisions éditoriales notables plus bas, pas rester implicite
   - **pour l'étape 2** : la liste des illustrations générées, groupées par chapitre
   - **pour l'étape 3/4** : confirmation chapitre par chapitre que le contenu prévu au plan a bien été rédigé, que la relecture stylistique (règle 9) a été faite, **et** que le Résumé de fin de chapitre (règle 11) est présent
   - toute **décision éditoriale notable** prise pendant l'étape (exclusion de contenu, simplification, choix de traduction) signalée explicitement, avec sa justification
   - la question de validation avant de poursuivre (sauf `checkpoint=false` ou consigne de tout enchaîner, auquel cas cette ligne est simplement omise et l'étape suivante démarre immédiatement)
3. **Au rendu final** (étape 6) : un résumé chiffré (pages source, pages du cours, ratio indicatif, éléments de contrôle qualité passés) et la liste des problèmes rencontrés en cours de route et de leur résolution.

Le contenu de ces comptes-rendus (nombres, titres, décisions) dépend entièrement du livre traité — seule la structure ci-dessus est fixe.

---

## Étape 1 — Cartographie du livre + plan condensé

1. Slugifie le nom du livre et crée l'arborescence ci-dessus — mécanique, jamais recalculé à la main :
```bash
   python .claude/skills/cours-condense/scripts/initialiser_arborescence.py "<chemin_du_livre.pdf>"
```
   Le script affiche `SLUG: <slug>` et `DOSSIER: <chemin>` : reprends cette valeur **`DOSSIER`** telle quelle comme `<nom_du_livre>` pour toute la suite du pipeline (jamais la valeur `SLUG` seule — voir "Arborescence de travail" ci-dessus), et ne la recalcule jamais toi-même (source d'incohérence si deux calculs manuels divergent en cours de session). Idempotent : n'écrase ni ne supprime rien si l'arborescence existe déjà.
2. Exécute la cartographie mécanique :
```bash
   python .claude/skills/cours-condense/scripts/extraire_structure.py "<chemin_du_livre.pdf>" "<nom_du_livre>/extraction"
```
   Cela donne le nombre total de pages, la table des matières (si présente dans le PDF), et pour chaque page : nombre de mentions "Figure X.Y" et un score de probabilité "contient du code".
3. Lis `structure.json`, puis lis le contenu de `extraction/pages/*.txt` par lots **regroupés par chapitre ou partie du livre, tels que repérés dans la table des matières** — jamais par tranches de lignes ou de pages arbitraires détachées de cette structure. Chaque lot doit correspondre à une unité de sens du livre (un chapitre, ou deux chapitres courts regroupés), pas à un découpage mécanique par nombre de lignes : le découpage doit être identique d'une exécution à l'autre pour un même livre, puisqu'il est entièrement déterminé par sa table des matières, jamais par un choix arbitraire fait en cours de lecture. Objectif de cette lecture : analyser l'intégralité du livre — structure complète (parties, chapitres, sous-sections), et repérage des sections contenant du code Python (croisé le score `probable_code` avec une lecture réelle — le score est un indice, pas une vérité).

   Pour produire chaque lot à lire, une fois les bornes `page_debut`/`page_fin` de l'unité déterminées à partir de la table des matières, utilise :
   ```bash
   python .claude/skills/cours-condense/scripts/concatener_pages.py "<nom_du_livre>" <page_debut> <page_fin>
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
   python .claude/skills/cours-condense/scripts/verifier_plan_cours.py "<nom_du_livre>/plan_cours.json" "<nom_du_livre>/extraction/structure.json"
```
Ce script ne corrige rien : il vérifie que chaque chapitre a ses champs obligatoires du bon type, que chaque `pages_source` est valide (bornes croissantes, dans les limites du livre), et qu'aucun chevauchement de pages ni désordre chronologique n'existe entre deux chapitres consécutifs (la règle "chronologie stricte" de l'étape 1 devient ainsi vérifiable plutôt que laissée à l'auto-discipline). Corrige `plan_cours.json` et ré-exécute jusqu'à `"pret": true` avant de poursuivre. Un trou entre deux chapitres (préface, annexe exclue, index...) n'est pas une anomalie — seul un chevauchement ou un champ invalide l'est.

**Calibrage obligatoire et mécanique (ne pas sauter — une version en simple recommandation en prose de cette règle a déjà échoué une fois : sous-comptage silencieux des concepts visuels sur plusieurs chapitres sans qu'aucune vérification ne le signale) :**
Une fois `plan_cours.json` validé structurellement ci-dessus, exécute :
```bash
   python .claude/skills/cours-condense/scripts/verifier_calibrage_visuels.py "<nom_du_livre>/plan_cours.json" "<nom_du_livre>/extraction/structure.json"
```
Ce script est un contrôle mécanique, pas une correction automatique : il ne modifie rien, il calcule la somme `nb_mentions_figure` par chapitre et signale dans `chapitres_a_verifier` tout chapitre dont le nombre de `concepts_cles_visuels` parait nettement bas par rapport à la densité de figures des autres chapitres du même livre.
Pour chaque chapitre listé dans `chapitres_a_verifier` : retourne relire ce chapitre à la recherche de concepts structurels laissés de côté (schéma d'architecture non repéré, processus à étapes décrit en prose sans figure captionnée dans le livre mais qui reste un "processus à étapes" au sens de la règle ci-dessus, relation entre plusieurs entités, etc.), complète `concepts_cles_visuels` en conséquence, puis **ré-exécute le script** jusqu'à ce que `chapitres_a_verifier` soit vide ou que chaque chapitre restant y figurant soit explicitement justifié (voir format de compte-rendu).
Ce n'est pas une contrainte stricte de conversion 1:1 (les mentions comptent aussi les répétitions du même renvoi et les figures qui sont de simples captures d'écran sans structure, donc le nombre final de concepts visuels reste inférieur au nombre de mentions) — mais un chapitre signalé dont on garde le compte bas doit l'être par un jugement explicite et justifié, jamais par un oubli de vérification.

**Checkpoint** : présente un résumé du plan (nombre de chapitres, titres, total de concepts visuels) **incluant le tableau des sommes `nb_mentions_figure` par chapitre calculé ci-dessus**, et attends une validation avant l'étape 2, sauf consigne contraire.

---

## Étape 2 — Génération des illustrations

Pour chaque chapitre de `plan_cours.json`, écris et exécute un script Python (sauvegardé dans `<nom_du_livre>/scripts/illustrations_chapitreN.py`) 
qui génère et enregistre dans `<nom_du_livre>/illustrations/` les illustrations (schémas de concepts, courbes mathématiques réelles, fonctions de coût, architectures, etc.) 
nécessaires pour soutenir visuellement les explications et analogies du livre.

Consignes impératives :

0. Génère **exactement une illustration par concept clé** listé dans `concepts_cles_visuels` pour ce chapitre — ni plus, ni moins.
1. Ne copie ni ne reproduit jamais une figure existante du livre : recrée chaque schéma sous forme vectorielle originale, dans l'esprit pédagogique de l'auteur.
2. Charte couleur stricte : bleu profond `#1a365d` pour les traces principaux, orange chaud `#c25e00` pour l'accentuation ou les courbes secondaires.
3. Lisibilité : légendes claires (`plt.legend()`), titres et noms d'axes en français.
4. Propreté du code : `matplotlib.use('Agg')`, `plt.close()` après sauvegarde en `dpi=300`, format PNG.
5. Anti-chevauchement et dimensionnement — utilise `.claude/skills/cours-condense/scripts/illustration_utils.py` (`suggested_figsize`, `verify_no_overlap`) : 
   positionne tous les éléments, calcule un figsize de départ avec `suggested_figsize`, dessine, vérifie avec `verify_no_overlap`, et si un chevauchement est détecté, 
   augmente figsize et/ou repositionne, puis revérifie — ne sauvegarde le PNG qu'une fois la vérification passée. Marge minimale entre deux éléments distincts : 0.03 
   en coordonnées de figure (déjà appliquée par `verify_no_overlap`).

6. Simplicité : privilégie des schémas simples et immédiatement lisibles (boîtes, flèches, peu d'éléments par illustration) plutôt que des compositions denses ou trop chargées. L'illustration sert la compréhension immédiate d'un seul concept, pas la démonstration graphique — en cas de doute entre une version riche et une version épurée, choisis l'épurée.

Nomme chaque fichier de manière prévisible, ex. `illustrations/ch03_architecture_transformer.png`, pour pouvoir le référencer facilement à l'étape 3.

**Vérification obligatoire et mécanique, une fois toutes les illustrations du livre générées :**
```bash
   python .claude/skills/cours-condense/scripts/verifier_illustrations.py "<nom_du_livre>" "<nom_du_livre>/plan_cours.json"
```
Ce script ne corrige rien : il vérifie, par chapitre, que le nombre d'images générées correspond exactement au nombre de `concepts_cles_visuels` (règle 0), que chaque fichier respecte la convention de nommage, que le script `illustrations_chapitreN.py` correspondant respecte la propreté de code exigée (règle 4 — ou délègue correctement à `DiagramBuilder.save()`, qui l'assure déjà), et qu'aucune couleur codée en dur n'échappe à la charte stricte `#1a365d`/`#c25e00` (règle 2). Corrige les anomalies signalées et ré-exécute jusqu'à `"pret": true` avant le checkpoint ci-dessous.

**Checkpoint** : liste les illustrations générées (chemin + chapitre associé) et attends une validation avant l'étape 3, sauf consigne contraire.

---

## Étape 3 — Rédaction du cours condensé en français (contenu)

Rédige toi-même le contenu français de chaque chapitre (traduction adaptée, condensation, mise en \section/\subsection) — c'est un travail de compréhension et de style, 
pas une règle mécanique automatisable. Le squelette initial de `course.tex` est déjà en place (règle 1 ci-dessous, via `initialiser_course_tex.py`) ; pour rédiger le contenu chapitre par chapitre, utilise un script Python `<nom_du_livre>/scripts/ecrire_course_tex.py` qui ouvre `course.tex` **en mode append (`'a'`)** 
et y écrit le texte que tu lui fournis, dans l'ordre du plan — cela évite toute troncature en cas d'exécution partielle. 
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
   python .claude/skills/cours-condense/scripts/initialiser_course_tex.py "<nom_du_livre>" "<titre français fidèle au sujet du livre>"
   ```
   plutôt que de le retaper à la main : un bloc à zéro variation près (seul le titre change) retapé manuellement s'est déjà corrompu une fois cette session (une correction d'accents mal ciblée a changé `a4paper` en `à4paper` directement dans le fichier, faisant planter la compilation).

2. Traduction adaptée, jamais mot-à-mot : français fluide, adapte les analogies de l'auteur, italique pour termes anglais sans équivalent (*overfitting*, *dropout*, *embedding*). Cet italique inline, au fil de la phrase, est la **seule** forme d'ancrage terminologique dans le corps du texte : n'extrais jamais ces termes dans un encart ou une liste de définitions séparée du raisonnement qui les introduit.

3. Cohérence terminologique : pour tout terme récurrent, fixe sa traduction dès la 1ère occurrence et réutilise-la à l'identique partout. 
    Consigne ces choix dans `<nom_du_livre>/glossaire.json` (anglais → traduction), consulte-le avant toute traduction. 
	Pour "agentic AI" : toujours "IA agentique", jamais "IA agentielle" (ou l'équivalent pertinent pour le livre en cours si le sujet diffère).

    Quand une variante fautive connue existe pour un terme (comme "IA agentielle" ci-dessus), consigne-la explicitement dans `glossaire.json` sous la forme `{"agentic AI": {"traduction": "IA agentique", "interdits": ["IA agentielle"]}}` plutôt que la forme simple `{"terme": "traduction"}` — cela rend la cohérence vérifiable mécaniquement (voir ci-dessous) plutôt que de dépendre uniquement de la vigilance en cours de rédaction.

    **Vérification mécanique, à exécuter avant le checkpoint de fin d'étape 3/4 :**
    ```bash
    python .claude/skills/cours-condense/scripts/verifier_glossaire.py "<nom_du_livre>/glossaire.json" "<nom_du_livre>/course.tex"
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

**Vérification mécanique, à exécuter avant le checkpoint de fin d'étape 3/4 :**
```bash
python .claude/skills/cours-condense/scripts/verifier_structure_course_tex.py "<nom_du_livre>/course.tex" "<nom_du_livre>/plan_cours.json"
```
Ce script ne corrige rien : il vérifie que chaque `lstlisting` est bien suivi d'une phrase d'explication (règle 6), que chaque chapitre `contient_code: true` a effectivement du code et inversement (règle 6), que chaque `\includegraphics` est dans un `figure`/`caption`/`label` complet (règle 7), et que chaque chapitre se termine par un `\section*{Résumé}` (règle 11 ci-dessous). Corrige les anomalies signalées et ré-exécute jusqu'à `"pret": true`.
La clé `figures_entre_listings_a_verifier` du rapport est indicative seulement (jamais bloquante, comme `accents_homographes_suspects` à l'étape 5) : distinguer une figure qui coupe un exemple continu d'une figure qui sépare légitimement deux exemples déjà expliqués exige de comprendre le rapport entre les deux blocs de code, ce qu'aucun motif syntaxique ne capture de manière fiable — juge chaque occurrence signalée au cas par cas.

8. Aucun repère de citation (`[1]`, `[2]`).

9. **Relecture stylistique active, chapitre par chapitre — avant de passer au chapitre suivant.** Une fois un chapitre rédigé, relis-le en te posant une seule question, phrase par phrase : *ce texte explique-t-il et relie-t-il les idées entre elles, ou se contente-t-il de les juxtaposer ?* Concrètement :
   - repère tout passage qui ressemble à une fiche de glossaire (voir règle 0) et réécris-le en prose reliée par des transitions ;
   - vérifie qu'un lecteur qui ne connaît pas encore la notion comprendrait *pourquoi* elle compte, pas seulement *ce qu'elle est* ;
   - ne passe au chapitre suivant qu'une fois ce test satisfait pour le chapitre courant — ce n'est pas une relecture finale globale en fin de livre, c'est un filtre appliqué à chaque chapitre au moment où il vient d'être écrit, tant que son contenu est encore présent à l'esprit.

   Cette étape ne se substitue pas au format de compte-rendu (section "Format de compte-rendu" ci-dessus) : la confirmation chapitre par chapitre affichée au checkpoint de l'étape 3/4 doit mentionner explicitement que cette relecture a été faite, pas seulement que le contenu prévu au plan a été couvert.

10. Ne compile pas le PDF à cette étape.

11. **Résumé de fin de chapitre.** Chaque chapitre se termine par une section non numérotée `\section*{Résumé}` de 3 à 5 puces courtes reprenant les points clés du chapitre. C'est la **seule** section du chapitre où le format liste à puces est utilisé sans restriction — le corps du chapitre, lui, reste soumis à la règle 0 (pas plus de 2-3 items enchaînés, jamais de liste à la place d'un raisonnement continu de l'auteur). Le Résumé ne remplace jamais la relecture stylistique de la règle 9 : c'est une synthèse écrite en plus du corps du chapitre, pas un substitut à un corps qui resterait trop listé. Présence vérifiée mécaniquement par `verifier_structure_course_tex.py` (voir règle 7 ci-dessus) — ne dépend plus d'une relecture manuelle à l'étape 6.

## Etape 4 — Rédaction du cours condensé (mise en forme LaTeX)

Applique ces règles à `course.tex` déjà crée, **sans creer de 2e version**.
Ne compile pas le PDF à cette étape.

**Blocs fixes (règles 1, 2, 3, 5 première moitié, 7 ci-dessous) : insérés par script, jamais retapés à la main.** Un bloc à zéro variation d'un livre à l'autre, retapé manuellement, s'est déjà corrompu cette session (`a4paper`→`à4paper`, `1a365d`→`1à365d`, via une correction d'accents mal ciblée passée dessus plus tard). Exécute en premier :
```bash
python .claude/skills/cours-condense/scripts/appliquer_preambule_etape4.py "<nom_du_livre>"
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
   python .claude/skills/cours-condense/scripts/inserer_needspace_listings.py "<nom_du_livre>"
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

**Checkpoint** : confirme que `course.tex` est complet (tous les chapitres du plan présents) et attends une validation avant l'étape 5, sauf consigne contraire.

---

## Étape 5 — Contrôle + configuration pré-compilation

1. Exécute d'abord le correcteur automatique d'accents, **avant** le contrôle qualité :
	```bash
	   python .claude/skills/cours-condense/scripts/corriger_accents.py "<nom_du_livre>/course.tex"
	```
   Ce script combine plusieurs outils complémentaires (dictionnaire français, LanguageTool, analyse grammaticale spaCy, heuristiques a/à et ou/où) pour corriger automatiquement, en boucle jusqu'à stabilisation, la grande majorité des accents manquants — un simple dictionnaire ne suffit pas : il ne voit ni les homographes grammaticaux (a/à, ou/où — les deux formes sont chacune un mot valide), ni les conjugaisons ambiguës (un verbe du 1er groupe au présent et son participe passé masculin singulier sont orthographiquement identiques sans accent, ex. "impressionne"). Le script journalise chaque correction appliquée ; affiche un résumé chiffré par outil dans le compte-rendu de cette étape. Ne modifie jamais le code (`lstlisting`/`\lstinline`/commentaires). Au premier lancement sur un poste donné, télécharge automatiquement ses dépendances (JRE portable, modèle spaCy) — normal, pas une erreur.

2. Exécute ensuite le contrôle qualité, sur le fichier déjà corrigé :
	```bash
	   python .claude/skills/cours-condense/scripts/controle_qualite.py "<nom_du_livre>/course.tex"
	```
   Cela vérifie automatiquement :
   - absence de "youtube", "vidéo", "timestamp"
   - **absence d'accent manquant sur les mots français courants de la prose** (résiduel après l'étape 1 ci-dessus — voir la règle absolue d'accentuation en tête de ce fichier) ; les blocs `lstlisting` et `\lstinline` sont exclus de ce contrôle
   - absence de repère de citation numérote (`[1]`, `[2]`, etc.)
   - validité syntaxique de chaque bloc `lstlisting` Python via `ast.parse()`
   - un seul `\usepackage{hyperref}` et un seul `\hypersetup{...}` dans tout le fichier (le script supprime lui-même les doublons éventuels — c'est la seule correction automatique de cette étape)

3. Vérifie en plus, par lecture, qu'il ne reste pas de paragraphes explicatifs non traduits en anglais (les blocs `lstlisting` sont exclus, ils gardent leur langue d'origine) — ce point est qualitatif, pas automatisable de manière fiable par regex.

4. Si un problème est détecté (mots interdits, accents manquants, citations, `ast_parse` en échec, paragraphe non traduit) : liste-le précisément (ligne + contexte) et **arrête-toi là, sans compiler**. Le résiduel après l'étape 1 est normalement faible (de l'ordre de la dizaine d'occurrences, principalement des noms de fichiers d'illustrations dans `\includegraphics{...}`, jamais du texte affiché) ; pour ce qui reste, comme pour les mots interdits, certaines occurrences peuvent être de faux positifs légitimes (forme conjuguée correcte sans accent, nom propre technique délibérément conservé en anglais, chemin de fichier) : juge au cas par cas et justifie la décision dans le compte-rendu, plutôt que de corriger ou d'ignorer silencieusement. Le rapport `accents_homographes_suspects` de `controle_qualite.py` est indicatif seulement (jamais bloquant) : c'est une heuristique de secours, moins fiable que ce que `corriger_accents.py` a déjà appliqué en amont.

5. Sinon, ajoute au préambule de `course.tex` (une seule fois) le bloc fixe `hyperref`/`cleveref` — même catégorie que les blocs figés de l'étape 4 : gabarit inséré par script, jamais retapé à la main :
	```bash
	   python .claude/skills/cours-condense/scripts/appliquer_hyperref_etape5.py "<nom_du_livre>" "<titre_du_cours>"
	```
   Ce script insère, une seule fois et juste avant `\begin{document}`, `\usepackage[colorlinks=true, ...]{hyperref}`, `\hypersetup{...}` et `\usepackage{cleveref}` (dans cet ordre : `cleveref` doit venir juste après `hyperref`). Idempotent.

---

## Étape 6 — Compilation + vérification, décision, livraison

1. Compile :
	```bash
	   python .claude/skills/cours-condense/scripts/compiler.py "<nom_du_livre>/course.tex" "<nom_clair_du_cours>"
	```
   Rapporte : succès/échec de chaque passe, erreurs ou warnings éventuels (le script les extrait automatiquement des logs).

2. Compare la table des matières de `course.tex` à `plan_cours.json` : vérifie qu'un chapitre du PDF existe bien pour chaque entrée du plan, et que chaque concept clé et chaque section source listés dans `plan_cours.json` pour ce chapitre sont couverts par au moins un paragraphe explicatif identifiable dans le chapitre correspondant. (La présence du `\section*{Résumé}` de chaque chapitre — règle 11 de l'étape 3 — est déjà vérifiée mécaniquement par `verifier_structure_course_tex.py` à la fin de l'étape 3/4 ; inutile de la re-vérifier manuellement ici.)

   Si un concept ou une section du plan n'a aucune trace dans le texte final :
   - ne modifie rien automatiquement
   - indique précisément le chapitre et le concept/section manquant
   - précise si l'absence semble venir d'une coupe excessive (au-delà de la simple reformulation/condensation attendue) ou d'un oubli lors de la rédaction
   - rappelle qu'aucun bloc de code ne doit jamais être raccourci, quelle que soit sa longueur

   Si tous les concepts et sections du plan sont couverts, ne fais rien de plus — c'est normal, quelle que soit la longueur obtenue.

3. Compte le nombre de pages du PDF généré (déjà affiché par `compiler.py`, via `pypdf` ou, à défaut, `pymupdf`) et compare-le au nombre de pages du livre source (`structure.json` -> `nb_pages`). 
   Donne ce ratio à titre purement indicatif, sans seuil ni jugement automatique : il varie légitimement selon la densité de code et la richesse du livre source, et ne doit jamais servir de critère pour décider de condenser ou d'étoffer davantage.

4. Le script `compiler.py` a déjà copié le PDF dans `<nom_du_livre>/out/` et supprime les fichiers auxiliaires (`.aux`, `.log`, `.toc`, `.out`), et les logs de chaque passe. 
   Donne un résumé chiffré final : pages du livre source, pages du cours final, ratio obtenu (en %, à titre indicatif), confirmation qu'aucun concept ou section du plan n'est resté sans couverture textuelle, et qu'aucun résidu anglais/YouTube/citation numéroté n'a été détecté à l'étape 5.

5. **Publie le PDF final dans `corpuscondense/`** — cette commande doit pouvoir s'exécuter seule, indépendamment de tout orchestrateur, donc cette publication fait partie du skill lui-même, pas d'une étape externe :
	```bash
	   "<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/cours-condense/scripts/publish_condense.py" "<racine_projet>/<nom_du_livre>/"
	```
   `<nom_du_livre>` inclut déjà `rag_data/courscondense/` (voir "Arborescence de travail" en tête de ce fichier) — ne préfixe pas une deuxième fois avec `rag_data/courscondense/`, sous peine de chemin invalide (`.../rag_data/courscondense/rag_data/courscondense/...`). Le script cherche récursivement `out/*.pdf` (peu importe la profondeur exacte) et copie le plus récent vers `<racine_projet>/corpuscondense/<slug>.pdf`, où `<slug>` est le dernier segment de `<nom_du_livre>` (le nom du dossier de travail lui-même, pas le chemin complet). 
   Rapporte le chemin de destination confirmé par le script. Si le script échoue (PDF introuvable), arrête-toi et signale-le — ne recopie jamais le fichier à la main en contournant le script.
   
 
