---
name: rag-extraction
description: >-
  Convertit un cours condensé (PDF, normalement dans corpuscondense/) en
  markdown pivot structure (titres/code/prose distingués) avec extraction
  d'images natives et réparation automatique des ligatures cassées.
  Usage: /rag-extraction <chemin_vers_pdf_condense>.
  Deuxieme étape du pipeline RAG, après /cours-condense.
---

# /rag-extraction — Etape 2 du pipeline RAG

Convertit un PDF de cours condensé (normalement dans `<racine_projet>/corpuscondense/`,
sortie de `/cours-condense`) en markdown pivot exploitable par les étapes
suivantes (`/rag-images`, `/rag-chunking`, `/rag-index`, `/rag-concepts`,
`/rag-graphe`).

Script déterministe (PyMuPDF), aucune rédaction ni jugement de contenu par
Claude — un seul appel `claude -p` headless interne si des ligatures cassées
sont détectées (réparation automatique).

## Détection des formules mathématiques en texte natif

Une formule composée en LaTeX (fraction, exposant, indice) et extraite comme
texte natif se retrouve dans un ordre de lecture incorrect — les fragments
empilés visuellement (numérateur/dénominateur...) ne suivent pas un ordre
linéaire gauche-à-droite/haut-en-bas.

Détection des zones candidates : toute ligne dont **toutes** les polices
appartiennent à la famille TeX Computer Modern mathématique
(`CMMI`/`CMSY`/`CMEX`/`CMR`) et dont **au moins une** est strictement
mathématique (`CMMI`/`CMSY`/`CMEX`, jamais utilisée pour du texte de corps)
est une candidate — fiable à 100% pour repérer de la police mathématique
(tout PDF de `corpuscondense/` est compilé par `/cours-condense` avec le
même préambule fixe, Computer Modern par défaut, aucun package de police
alternatif), mais **pas fiable à distinguer une vraie formule d'affichage
isolée d'un simple indice/exposant d'une formule INLINE** (ex. `$C_n^p$` au
milieu d'une phrase) qui atterrit, par accident de mise en page, seul sur sa
propre ligne PDF — vérifié empiriquement (une identité binomiale citée en
pleine phrase de prose génère plusieurs fausses zones "isolées").

Les fragments d'une même candidate sont regroupés par contiguïté dans
l'ordre de lecture (aucun élément non-formule intercalé) plutôt que par
simple proximité spatiale — une pure proximité fusionnait à tort des
mentions isolées de la même variable dans des phrases différentes (vérifié,
corrigé). Un rectangle fusionné trop large (> 300pt) est abandonné au profit
du texte brut plutôt que traité comme formule, pour ne jamais capturer de
prose intercalée (vérifié empiriquement : un rectangle touchant les deux
marges de page capture les phrases entières entre deux mentions isolées).

### Résolution : `course.tex` (vérité terrain) plutôt que rasterisation + OCR

Le dossier de travail de `/cours-condense` pour ce même document
(`rag_data/courscondense/<slug>/`, où `<slug>` = le nom du PDF condensé sans
extension — voir `_rag_lib/paths.py:courscondense_dir_for_condense_pdf`)
contient `course.tex`, qui porte déjà chaque formule en LaTeX exact. Quand ce
fichier existe, chaque zone candidate est **appariée individuellement** à
une formule d'affichage de `course.tex` (`\[...\]`, `$$...$$`,
`align`/`equation`/...) par similarité de son contexte de prose immédiat
(phrase avant/après, comparée via `difflib.SequenceMatcher` — voir
`scripts/tex_source.py:extract_display_segments` et
`scripts/convert.py:match_formula_runs_to_tex`), **jamais par un simple
comptage global** : un comptage global se ferait justement tromper par les
faux positifs décrits ci-dessus (indices/exposants de formules inline).

- Zone appariée avec succès : le LaTeX exact de `course.tex` est recopié tel
  quel dans `pivot.md` (aucune image, aucun risque d'hallucination — c'est
  la source de vérité).
- Zone non appariée **quand `course.tex` est disponible** (aucune formule de
  `course.tex` ne correspond au contexte voisin — cas des faux positifs
  ci-dessus, ou formule légitime trop large pour `_FORMULA_MAX_WIDTH`) :
  redescendue en simple texte de prose (ordre de lecture reconstruit du
  mieux possible, jamais parfaitement fidèle), **jamais rasterisée** —
  contrairement au comportement d'avant cette évolution. Ce choix délibéré
  évite de produire une image rognée trompeuse pour ce qui est, avec une
  confiance élevée, un faux positif (vérifié empiriquement — voir la
  section précédente). Ce n'est pas vérifiable comme "identique" par
  `fidelity_check.py`, mais c'est une dégradation connue et acceptée,
  jamais une perte silencieuse de contenu : cette formule reste présente,
  juste sans garantie d'ordre/fidélité exacte.
- Zone non appariée **quand `course.tex` est absent** : seul cas où le
  comportement historique s'applique encore — rasterisée et sauvegardée
  comme image native (`images/page_NNN_formula_NN.png`), référencée par
  `![Illustration](...)` dans `pivot.md` ; c'est `/rag-images`
  (description + OCR/pix2tex, avec ses propres garde-fous) qui la traite
  ensuite.

Dans les deux cas, une zone non appariée ne dégrade jamais les zones déjà
correctement appariées ailleurs dans le même document — sauf bug
d'alignement (voir `match_formula_runs_to_tex` : l'appariement glouton
regarde désormais plusieurs formules source en avance du pointeur courant,
justement pour qu'une seule formule sans run correspondant ne désynchronise
jamais l'appariement de toutes celles qui suivent).

Le compte-rendu de fin d'exécution (voir plus bas) affiche
`<nb appariées>/<nb zones détectées>` : un reliquat non nul n'est jamais une
anomalie à corriger en soi — c'est souvent des faux positifs (bénins,
redescendus en prose comme ci-dessus) ou une formule légitime trop large
(> 300pt, abandonnée au profit du texte brut, comportement inchangé).

## Illustrations reprises depuis leur fichier source

Même principe pour les images natives embarquées dans le PDF
(`\includegraphics{illustrations/...}`) : quand `illustrations/` (généré par
`/cours-condense`, mêmes fichiers que ceux visibles dans le dossier de
travail du livre) est disponible et que son nombre de fichiers référencés
correspond exactement au nombre d'images natives détectées dans le PDF
(comptage fiable ici, contrairement aux formules — une image native est une
unité bien définie des deux côtés), chaque image est copiée depuis son PNG
d'origine sous son nom sémantique (`ch01_union.png`...) plutôt que
ré-extraite du PDF sous un nom opaque (`page_NNN_img_NN.ext`). En cas de
mismatch, comportement legacy inchangé (extraction native, nom opaque).

## Contrôle de fidélité (`scripts/fidelity_check.py`)

Une fois `pivot.md` écrit, et seulement si le dossier de travail de
`/cours-condense` est encore disponible, un contrôle compare `pivot.md` à la
vérité terrain (`course.tex` + `illustrations/`) sur trois volets, dans cet
ordre :

1. **Illustrations** : chaque fichier de `illustrations/` doit être à la fois
   référencé dans `pivot.md` et présent dans `images/` — toute absence dans
   l'un ou l'autre est **bloquante**. Indépendamment de `course.tex`, toute
   référence `![Illustration](images/...)` de `pivot.md` pointant vers un
   fichier introuvable sur disque est également signalée (référence cassée),
   même sans dossier de travail disponible.
2. **Code** : chaque bloc `lstlisting` de `course.tex` doit se retrouver,
   par appariement global (chaque bloc source cherche sa meilleure
   correspondance parmi tous les blocs de `pivot.md`, jamais un simple
   pointeur séquentiel — voir le commentaire de `check_code_blocks` sur la
   fragilité d'un alignement par fenêtre locale), à au moins 95% de
   similarité (`difflib.SequenceMatcher`, espaces de fin de ligne ignorés,
   indentation et casse comprises). En dessous, **bloquant**.
3. **Formules** : chaque formule d'affichage de `course.tex` doit apparaître
   **verbatim** dans `pivot.md`. Une absence est **toujours indicative,
   jamais bloquante** : dès que `course.tex` est disponible (seul cas où ce
   contrôle s'exécute), `convert.py` ne rasterise plus jamais une formule
   non appariée (voir section "Résolution : course.tex" ci-dessus) — elle
   est systématiquement redescendue en prose approximative, un contenu
   toujours présent mais dont l'ordre/la fidélité exacte n'est plus
   garantie. Ce n'est donc jamais une perte silencieuse, seulement un point
   à vérifier manuellement si le contenu exact de cette formule importe.

Sans dossier de travail `/cours-condense` disponible, seul le volet
"référence cassée" des illustrations s'applique — le reste est marqué
`skipped`, jamais un échec.

Cette vérification a déjà révélé, lors de sa mise au point, des défauts réels
au-delà de simples artefacts d'extraction :
- l'indentation Python est perdue par défaut (`listings` la rend par un
  déplacement horizontal du curseur, jamais par de vrais caractères espace)
  — corrigée dans `convert.py` (`_reconstruct_code_indentation`) à partir de
  la position x de chaque ligne ;
- les lignes vides internes à un bloc de code (séparateurs PEP8) sont
  perdues de la même façon — corrigées (`_insert_blank_code_lines`) à partir
  de l'écart vertical anormal entre deux lignes consécutives ;
- les apostrophes/guillemets droits du code sont rendus en typographie
  courbe par pdflatex sans le package `upquote` — un artefact du PDF
  lui-même, jamais valide en Python : reconverti systématiquement en droit
  dans le code classifié (`_CURLY_QUOTES`), sans risque de faux positif
  puisque ce caractère n'apparaît jamais légitimement en Python ;
- une illustration présente dans `illustrations/` mais jamais insérée dans
  `course.tex` par `/cours-condense` lui-même (pas un défaut de cette étape)
  reste signalée telle quelle — la corriger revient à `/cours-condense`, pas
  à `/rag-extraction`.

## Execution

Toujours en foreground, bloquant jusqu'a complétion — jamais via
`run_in_background` ni aucun mécanisme async. Lancer cette commande en
arrière-plan, ou en parallèle d'une autre étape du pipeline, risque une
contention entre appels `claude -p` imbriques (réparation de ligatures).

**Interdiction de déléguer à un sous-agent** (outil `Agent`) toute lecture ou
vérification de `pivot.md` ou du rapport qualité — cette lecture doit être
faite directement, dans le même tour de conversation, jamais confiée à un
sous-agent "pour économiser du contexte" : même raison que ci-dessus,
préserver le déterminisme et éviter toute contention entre appels imbriqués.

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-extraction/scripts/run.py" "<racine_projet>/corpuscondense/<nom_du_pdf>.pdf"
```

Options :
- `--force` : relance meme si l'étape est déjà marquée `done`.
- `--pages DEBUT:FIN` : limite à une plage de pages (fin exclue), utile pour tester.

## Idempotence

Sans `--force` : si `status.json` contient déjà `{"extraction": {"status": "done"}}`
**et** que `pivot.md` existe, le script ne fait rien — il affiche
`deja fait (extraction): <chemin>` et s'arrête (code 0), sans re-extraire ni
rien écraser. Avec `--force` : il retraite systématiquement et écrase
`pivot.md`/`images/`/`meta.json`, quel que soit le statut précédent. Le
script ne marque jamais l'étape `failed` : si le PDF source est introuvable,
il s'arrête avant d'écrire `status.json` (rien à nettoyer).

## Sortie

`document_id` est dérivé du nom + du contenu du PDF condense (hash). Ecrit
dans `<racine_projet>/rag_data/work/<document_id>/` — **jamais** un dossier
crée a coté du PDF source (centralisation obligatoire, voir `_rag_lib/paths.py`) :
- `pivot.md` — texte structure
- `images/` — images natives extraites (illustrations du PDF + zones de
  formule détectées en texte, `page_NNN_formula_NN.png`)
- `meta.json` — `{"document_id", "source_pdf"}`, réutilisé par les étapes suivantes
- `status.json` — suivi (`{"extraction": {"status": "done", ...}}`)

Le script affiche le `document_id` calcule : les étapes suivantes acceptent
indifféremment le meme chemin de PDF, ce `document_id`, ou le dossier de
travail complet.

## Critère de sortie exploitable

L'extraction est exploitable pour `/rag-images` (l'étape suivante) dès que `status.json`
contient `{"extraction": {"status": "done"}}` et que `pivot.md` existe —
exactement la condition que le script vérifie lui-même avant de sauter son
propre travail (voir Idempotence ci-dessus). Cette condition ne garantit
toutefois pas l'absence de problèmes qualité : les métadonnées de
`status.json` ne portent que le compte total (`quality_issues`), jamais le
détail par page/type — ce détail n'existe que dans la sortie affichée au
moment de l'exécution (voir compte-rendu ci-dessous), il n'est persisté nulle
part. Un problème bloquant signalé lors d'une exécution passée ne peut donc
être revérifié qu'en relançant le script (`--force`), jamais en relisant
`status.json` après coup.

## Après exécution

Pendant l'exécution, affiche une ligne courte par action significative
(ex. "Extraction lancée", "Ligatures détectées : réparation en cours",
"Pivot écrit").

Au terme de l'exécution, affiche un résumé structuré, dans cet ordre :
1. un titre court ("Extraction terminée — `<nom du document>`")
2. une phrase de synthèse chiffrée : nombre de pages, nombre d'images
   extraites (dont zones de formule détectées), nombre de ligatures réparées,
   nombre de formules reprises depuis `course.tex` sur le total détecté
   (`<nb appariées>/<nb zones>`, voir section "Résolution : course.tex"
   ci-dessus), et si les illustrations proviennent des fichiers source ou de
   l'extraction native du PDF
3. si le script signale des problèmes qualité, un tableau : page, type
   (`encoding`, `control_char_ligature`, `non_textual_noise`,
   `low_text_density`, `low_resolution_image`), description courte
4. une ligne de total : nombre de problèmes bloquants vs indicatifs

**Distinction bloquant/indicatif, fixée ici — jamais laissée à l'appréciation
au moment de la lecture du rapport :**
- **jamais bloquant** : `control_char_ligature` — seul type que le script
  corrige lui-même (réparation de ligatures). Attention : le contrôle
  qualité est calculé *avant* la réparation et n'est jamais recalculé
  après — des occurrences de ce type dans le rapport peuvent donc déjà être
  résolues dans le `pivot.md` final dès que `ligature_repairs > 0`. Vérifie
  directement dans `pivot.md` en cas de doute plutôt que de te fier au compte
  affiché.
- **bloquant par défaut** : `encoding`, `non_textual_noise`,
  `low_text_density`, `low_resolution_image` — le script se contente de les
  signaler, sans jamais les corriger. Une exception cas par cas reste
  possible, mais doit être justifiée explicitement dans le compte-rendu,
  jamais silencieuse.

Si un problème bloquant est détecté : liste-le précisément (page + type +
description) dans le tableau ci-dessus, et **arrête-toi avant de proposer
d'enchaîner sur `/rag-images`** — jamais de correction silencieuse, jamais
de contenu inventé pour combler une page mal extraite.

N'invente jamais de contenu pour combler une page mal extraite.

5. **Contrôle de fidélité** (voir section dédiée ci-dessus) : affiche
   `illustrations <n>/<n>, code <n>/<n>, formules exactes <n>/<n> (+<n> en
   image de repli)` — ou `saute (dossier de travail /cours-condense
   introuvable)` si non applicable. Si des anomalies sont remontées, un
   tableau **distinct** du tableau qualité ci-dessus, marquant chacune
   `BLOQUANT` ou `indicatif` (cette distinction est déjà fixée par
   `fidelity_check.py`, jamais à réévaluer à la lecture).

**Toute anomalie `BLOQUANT` du contrôle de fidélité (illustration manquante,
bloc de code non retrouvé à l'identique, formule ni en texte ni en image)
arrête-toi avant de proposer d'enchaîner sur `/rag-images`, exactement comme
un problème qualité bloquant** — avec une nuance : si l'anomalie est une
illustration présente dans `illustrations/` mais absente de `course.tex`
lui-même (jamais insérée par `/cours-condense`), ce n'est pas un défaut de
cette étape-ci — signale-le comme tel dans le compte-rendu (chapitre/fichier
concerné) plutôt que de chercher une correction côté `/rag-extraction`.

Les anomalies `indicatif` (formule rasterisée en image de repli faute de
correspondance en texte) n'empêchent jamais d'enchaîner — elles signalent
seulement un point qui resterait à vérifier visuellement si besoin.
