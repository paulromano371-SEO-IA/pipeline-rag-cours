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

Script entièrement déterministe (PyMuPDF), aucune rédaction ni jugement de
contenu par Claude, et aucun appel `claude -p` interne — la réparation des
ligatures cassées (`ligature_repair.py`) et le recollage des césures de fin
de ligne (`convert.py`, voir plus bas) s'appuient tous deux sur un
dictionnaire français hors ligne (`pyspellchecker`), jamais sur une
inférence LLM.

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
correctement appariées ailleurs dans le même document : `match_formula_runs_to_tex`
apparie chaque run à sa meilleure correspondance parmi **toutes** les
formules source encore disponibles (recherche globale, comme
`fidelity_check.check_code_blocks`), jamais via un pointeur séquentiel —
même à fenêtre glissante, un pointeur reste vulnérable à se bloquer dès que
plus de formules consécutives que la taille de la fenêtre n'ont aucun run
correspondant (bug trouvé et corrigé en revue de code). La recherche
globale n'a structurellement pas ce mode d'échec.

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
  à `/rag-extraction` ;
- le `.strip()` nu (et `\s` en regex) traite U+001C-U+001F comme des espaces
  (propriété Unicode) — supprimait silencieusement une ligature tombant en
  tête/fin de texte ("fidélité" → "délité" en tête de titre) — corrigé
  (`_STRIP_CHARS` explicite dans `convert.py`) ;
- une fin de phrase de prose avec un seul mot long en `\texttt{...}` pouvait
  dépasser le seuil de classification "code" et fusionner à tort avec le
  bloc de code suivant — corrigé (seuil `_CODE_LINE_MIN_RATIO` relevé de 0.6
  à 0.9, une vraie ligne de code étant toujours à ratio 1.0) ;
- un commentaire Python de fin de ligne (`# ...`), rendu dans une police
  différente, atterrissait comme une ligne PyMuPDF séparée au lieu de rester
  en fin de ligne de code — corrigé (`_merge_code_trailing_comments`,
  fusion par proximité verticale) ;
- une césure automatique de pdflatex en fin de ligne ("ex-" / "ploration")
  restait non recollée — corrigé (`_join_wrapped_lines`, vérification par
  dictionnaire français, sans risque pour un vrai mot composé comme
  "auto-encodeur") ;
- un titre contenant des maths inline (`$f$`, `$k$`) était signalé absent à
  tort : `pivot.md` était déjà correct (pdflatex ne rend jamais les `$`),
  mais `_normalize_heading` ne les retirait pas côté `course.tex` — corrigé
  (`fidelity_check.py`, retrait des `$`, même principe que `~`) ;
- `\og`...`\fg{}` (guillemets français) subissaient le même défaut de police
  que les ligatures mais n'étaient jamais résolus (hors du candidat
  ff/fi/fl/ffi/ffl) — pas qu'un problème de titre, du texte réel corrompu en
  pleine prose (43 paires observées sur un document) — corrigé
  (`infer_symbol_pair_mapping` dans `ligature_repair.py` : appariement par
  alternance stricte ouverture/fermeture, sans dictionnaire ni référence à
  `course.tex`). Une fois les guillemets restitués, `_normalize_heading` ne
  les retirait pas non plus côté `pivot.md` — corrigé au même endroit que
  pour `$` ;
- un titre contenant une commande imbriquée (`\emph{medv}`) était tronqué à
  la première accolade fermante rencontrée par `extract_headings` (regex
  `[^}]*}`, pas de gestion de l'imbrication) — perte réelle de contenu dans
  la vérité terrain elle-même, pas un défaut de normalisation — corrigé
  (`tex_source.py`, compteur de profondeur d'accolades) ;
- un bloc de code coupé par un saut de page ne se refusionnait pas quand une
  illustration flottante (D'UN AUTRE CHAPITRE, placée là par l'algorithme de
  mise en page de LaTeX) occupait à elle seule toute une page intermédiaire
  entre les deux moitiés — corrigé (`_looks_like_page_furniture` dans
  `convert.py`, reconnaît illustration/légende/numéro de page isolé comme du
  mobilier traversable, jamais un vrai paragraphe de prose). Cas plus
  retors : DEUX coupures de page consécutives (une classe Python coupée
  page N/N+1, suivie d'un paragraphe puis d'un second bloc coupé page
  N+1/N+2 via une page de mobilier) — le reliquat de la première fusion
  n'était jamais réexaminé pour une seconde fusion, silencieusement —
  corrigé (`_merge_cross_page_code_fences` traite les pages comme une file,
  pas un index figé : tout reliquat est réinjecté en tête pour réexamen) ;
- un guillemet droit collé à son contenu (`` `` ``/`` '' ``, convention
  anglaise de citation LaTeX — SANS espace, contrairement à `\og`/`\fg{}`)
  n'était jamais résolu par `infer_symbol_pair_mapping` (qui exigeait un
  espace des deux côtés) — corrigé (`find_symbol_candidate_occurrences`
  élargi à une occurrence touchant un mot d'UN SEUL côté ; le caractère de
  remplacement — `"` identique aux deux bouts, ou « / » — dépend alors de
  si la paire touche ou non un mot, voir `_pair_touches_word`) ;
- un symbole de police employé SEUL, jamais en paire (tiret de séparation
  dans une légende `\caption{...}`, puce de liste en début de ligne — même
  glyphe pour les deux usages selon le contexte) restait un caractère de
  contrôle invisible faute de mécanisme pour lui — corrigé
  (`remove_singleton_symbols` : supprimé, jamais un caractère deviné,
  avec absorption d'au plus un espace adjacent SANS jamais traverser un
  saut de ligne). Premier essai erroné : un nettoyage d'espacement par
  regex globale sur tout le texte écrasait aussi l'indentation Python à
  l'intérieur des blocs de code (2 blocs sur 16 tombaient à 88-92% de
  similarité avec `course.tex`) — corrigé en un nettoyage caractère par
  caractère, strictement local à l'endroit de la suppression. Le reliquat
  éventuel après ces trois mécanismes (ligature en plein mot jamais
  reconnue par le dictionnaire, ou symbole sans paire valide) est
  désormais un type de problème qualité à part entière
  (`unresolved_control_char` dans `quality.py`, jamais bloquant, jamais une
  perte silencieuse).

## Execution

Toujours en foreground, bloquant jusqu'a complétion — jamais via
`run_in_background` ni aucun mécanisme async, par cohérence avec les autres
étapes du pipeline (`/cours-condense`, `/rag-concepts`, `/rag-graphe`...) qui
font, elles, des appels `claude -p` internes et risquent une contention si
lancées en arrière-plan ou en parallèle d'une autre étape. `/rag-extraction`
elle-même n'a plus d'appel `claude -p` interne (la réparation des ligatures
est un dictionnaire hors ligne, voir `ligature_repair.py`), mais garde la
même règle pour rester prévisible d'une étape à l'autre. Techniquement
imposé par un hook `PreToolUse` sur `Bash`
(`.claude/hooks/block_rag_background.py`, voir `.claude/settings.json`), qui
refuse tout `run_in_background: true` sur une commande référençant un script
du pipeline RAG.

**Interdiction de déléguer à un sous-agent** (outil `Agent`) toute lecture ou
vérification de `pivot.md` ou du rapport qualité — cette lecture doit être
faite directement, dans le même tour de conversation, jamais confiée à un
sous-agent "pour économiser du contexte" : même raison que ci-dessus,
préserver le déterminisme et éviter toute contention entre appels imbriqués.
Techniquement imposé par un hook `PreToolUse` sur `Agent`
(`.claude/hooks/block_rag_extraction_subagent_read.py`, voir
`.claude/settings.json`), qui refuse tout appel `Agent` dont le prompt
référence `pivot.md` ou `rag_data/work/` — même mécanisme que le hook
`run_in_background` ci-dessus.

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-extraction/scripts/run.py" "<racine_projet>/corpuscondense/<nom_du_pdf>.pdf"
```

Options :
- `--force` : relance meme si l'étape est déjà marquée `done`.
- `--pages DEBUT:FIN` : limite à une plage de pages (fin exclue), utile pour tester.

## Idempotence

Sans `--force` : si `status.json` contient déjà `{"extraction": {"status": "done"}}`
**et** que `pivot.md` existe, le script ne fait rien — il affiche
`deja fait (extraction): <chemin>` et s'arrête, sans re-extraire ni rien
écraser. Avec `--force` : il retraite systématiquement et écrase
`pivot.md`/`images/`/`meta.json`, quel que soit le statut précédent. Le
script ne marque jamais l'étape `failed` : si le PDF source est introuvable,
il s'arrête avant d'écrire `status.json` (rien à nettoyer, code de sortie
`1`).

**Code de sortie** : `0` si aucun problème bloquant (qualité ou fidélité),
`2` si au moins un `BLOQUANT` figure dans le rapport — y compris sur le
chemin "déjà fait" ci-dessus, reconstruit à partir de `quality_blocking_issues`/
`fidelity_blocking_issues` de `status.json` (voir "Sortie"). Ce code ne
dispense jamais de lire le rapport : il ne dit que "bloquant ou non", jamais
lequel des deux volets ni le détail par ligne.

## Sortie

`document_id` est dérivé du nom + du contenu du PDF condense (hash). Ecrit
dans `<racine_projet>/rag_data/work/<document_id>/` — **jamais** un dossier
crée a coté du PDF source (centralisation obligatoire, voir `_rag_lib/paths.py`) :
- `pivot.md` — texte structure
- `images/` — images natives extraites (illustrations du PDF + zones de
  formule détectées en texte, `page_NNN_formula_NN.png`)
- `meta.json` — `{"document_id", "source_pdf"}`, réutilisé par les étapes suivantes
- `status.json` — suivi (`{"extraction": {"status": "done", "metadata": {...}}}`),
  `metadata` incluant notamment `quality_blocking_issues`,
  `fidelity_blocking_issues`, `ligature_repairs`, `symbol_pairs_repaired` et
  `singleton_symbols_removed` (comptes, pas le détail par ligne)

Le script affiche le `document_id` calcule : les étapes suivantes acceptent
indifféremment le meme chemin de PDF, ce `document_id`, ou le dossier de
travail complet.

## Critère de sortie exploitable

L'extraction est exploitable pour `/rag-images` (l'étape suivante) dès que `status.json`
contient `{"extraction": {"status": "done"}}` et que `pivot.md` existe —
exactement la condition que le script vérifie lui-même avant de sauter son
propre travail (voir Idempotence ci-dessus). Cette condition ne garantit
toutefois pas l'absence de problèmes bloquants : `status.json` porte
`quality_blocking_issues`/`fidelity_blocking_issues` (comptes, voir "Sortie"
et le code de sortie ci-dessus), qui disent SI une exécution passée avait un
bloquant sans avoir à relancer le script — mais jamais LEQUEL ni son détail
par page/type, qui n'existent que dans la sortie affichée au moment de
l'exécution (voir compte-rendu ci-dessous) et ne sont pas persistés. Revoir
ce détail exige donc toujours de relancer le script (`--force`).

## Après exécution

Avant de lancer la commande, annonce en une phrase ce que tu vas faire.

Le script produit lui-même, sur sa dernière ligne de sortie, un rapport
Markdown complet et déjà formaté (titre, synthèse chiffrée, tableaux
qualité/fidélité avec `BLOQUANT`/`indicatif` déjà tranché pour chaque ligne
— voir `_build_report` dans `run.py`, et `blocking: bool` sur
`QualityIssue`/`FidelityIssue`) : **relaie ce rapport tel quel**, jamais une
reconstruction manuelle à partir de nombres relus dans la sortie — la
distinction bloquant/indicatif, l'ordre des sections et le format ne
laissent aucune place à l'interprétation, ce n'est donc jamais à toi de les
recalculer ni de les reformuler.

Deux décisions restent les tiennes, le rapport ne les prend jamais à ta
place :

1. **Un `BLOQUANT` (qualité ou fidélité) détecté ⇒ arrête-toi avant de
   proposer d'enchaîner sur `/rag-images`** — jamais de correction
   silencieuse, jamais de contenu inventé pour combler une page mal
   extraite. Le code de sortie (voir "Idempotence") dit déjà SI ce cas se
   présente, sans lire le rapport — mais l'arrêt lui-même, la décision
   d'une exception cas par cas (possible, mais doit être justifiée
   explicitement dans ta réponse, jamais silencieuse), restent les tiennes.
2. Une illustration présente dans `illustrations/` mais jamais insérée dans
   `course.tex` lui-même (défaut de `/cours-condense`, pas de
   `/rag-extraction`) est **déjà détectée et reclassée automatiquement en
   indicatif** par `fidelity_check.py` (`check_illustrations`, comparaison
   directe aux `\includegraphics` de `course.tex`) — son détail nomme déjà
   explicitement `/cours-condense` comme responsable. Rien à déduire
   toi-même pour ce cas précis ; le principe (certaines anomalies relèvent
   d'une étape amont) peut néanmoins resservir face à un nouveau type
   d'anomalie non encore reclassé automatiquement.
