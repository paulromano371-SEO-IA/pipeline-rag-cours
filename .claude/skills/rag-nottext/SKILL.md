---
name: rag-nottext
description: >-
  Décrit en langage naturel (et OCRise/transcrit si pertinent) chaque
  élément non-textuel de pivot.md — images, blocs de code, formules
  mathématiques d'affichage déjà en LaTeX — produit par /rag-extraction,
  puis enrichit le pivot markdown pour que ce contenu devienne recherchable
  en aval par un modèle d'embedding texte. Usage: /rag-nottext
  <pdf_condense_ou_document_id>. Troisième étape du pipeline RAG, entre
  /rag-extraction et /rag-chunking.
---

# /rag-nottext — Étape 3 du pipeline RAG

Traite chaque élément non-textuel de `pivot.md` d'un document déjà passé par
`/rag-extraction` : images (classification formule/générale, description,
OCR adapté, renommage explicite), blocs de code, et formules d'affichage
déjà reprises en LaTeX exact (voir `rag-extraction/SKILL.md` — "Résolution :
course.tex"). Pour chacun, une description en langage naturel est générée
puis insérée juste après dans `pivot.md` — jamais dedans, jamais avant —
pour que ce contenu devienne recherchable par `/rag-chunking` et
`/rag-index`.

**Pourquoi les trois types, sans exception** : un modèle d'embedding texte
ne rapproche quasiment jamais une question en français d'une formule LaTeX,
d'un extrait de code, ou d'une image brute — vérifié empiriquement (test de
similarité : formule seule 0,17, code seul 0,26, image seule 0 ; avec
description accolée : formule 0,71, code 0,86). Ce n'est pas une option,
c'est une nécessité structurelle : un modèle d'embedding texte ne "raisonne"
pas sur du LaTeX/code/pixels, il rapproche des textes qui se ressemblent
statistiquement. Sans cette étape, ce contenu reste invisible à la recherche
vectorielle et au graphe de concepts.

**Description découplée du contenu embeddé, jamais mélangée** : le même
test a aussi montré qu'accoler description et verbatim dans le MÊME texte
embeddé dilue la similarité au lieu de la restaurer (formule : 0,71 → 0,32
diluée ; d'autant plus sévère que le verbatim est long par rapport à la
description). Cette étape ne résout donc pas cela seule : elle insère la
description juste après le bloc, marquée par un commentaire HTML invisible
au rendu (`<!-- rag-nottext:description -->`) ; c'est `/rag-chunking`
(`_rag_lib/chunk.py`) qui, grâce à ce marqueur, fusionne bloc + description
en une seule unité atomique dont le texte STOCKÉ (citation) garde le
verbatim, mais dont le texte EMBEDDÉ ne retient que la description — jamais
le LaTeX/code/légende brut. Ne modifie jamais ce marqueur ni le format
d'insertion sans mettre à jour `_rag_lib/chunk.py` en même temps : les deux
doivent rester strictement synchronisés.

**Portée actuelle** : formules d'affichage isolées (`$$...$$`, `\[...\]`,
environnements `equation`/`align`/...) — pas les formules inline (`$...$`)
mêlées à une phrase, déjà entourées de leur propre contexte de prose
explicatif, donc déjà bien gérée par le modèle d'embedding sans description
supplémentaire.

## Exécution

Toujours en foreground, bloquant jusqu'à complétion — jamais via
`run_in_background` ni aucun mécanisme async : le script appelle `claude -p`
en interne (description d'image, de code, de formule) et une contention
entre appels imbriqués peut faire timeout un appel pourtant fonctionnel
isolément, même raison que `/rag-extraction`.

**Interdiction de déléguer à un sous-agent** (outil `Agent`) la lecture ou la
vérification des images, du code, des formules, de `nottext_meta.json` ou du
`pivot.md` enrichi — cette vérification doit être faite directement, dans le
même tour de conversation, jamais confiée à un sous-agent "pour économiser
du contexte".

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-nottext/scripts/run.py" "<pdf_condense_ou_document_id_ou_dossier_de_travail>"
```

Le script accepte indifféremment : le même chemin de PDF condensé passé à
`/rag-extraction`, le `document_id` qu'il a affiché, ou directement le
dossier `rag_data/work/<document_id>/`.

Options :
- `--force` : retraite même si l'étape est déjà marquée `done`.
- `--model NAME` : modèle Claude à utiliser pour les descriptions (défaut :
  celui de l'installation `claude` locale).
- `--batch-size N` : traite au plus N éléments par invocation puis s'arrête
  — voir "Traitement par lots" ci-dessous. Indispensable sur un document à
  beaucoup d'éléments non-textuels (chaque élément coûte un appel `claude -p`
  séquentiel), sous peine de dépasser la durée d'une seule commande
  foreground.

**État des lieux affiché avant tout traitement** (`_print_status_overview`
dans `run.py`), toujours dans cet ordre :
1. total d'éléments non-textuels du document, par type (image/code/formule) ;
2. combien sont déjà traités, par type ;
3. combien restent à traiter, par type ;
4. puis, juste avant de lancer les appels `claude -p`, la taille du lot qui
   va être traité maintenant (`--- Lot en cours : N element(s) sur M
   restant(s) ---`).

Recalculé à chaque invocation à partir de l'état réel de `pivot.md` (même
mécanisme que `_find_pending_blocks`) — jamais un compteur mémorisé
séparément, qui pourrait diverger de ce que le pivot montre vraiment.

**Progression affichée PENDANT le traitement, pas seulement avant/après**
(`_print_progress` dans `run.py`) : après chaque groupe de blocs de code ou
de formules, et après chaque image, une ligne `>>> Progression : X/Y
traites (...)` est réimprimée avec les compteurs par type mis à jour — sans
attendre la fin du lot entier. Sur un lot de plusieurs dizaines de secondes
à plusieurs minutes (plusieurs appels `claude -p` sequentiels), c'est ce qui
distingue un traitement normal d'un blocage.

**Obligation de relayer TOUTE la sortie du script dans le chat, telle
quelle, à CHAQUE invocation** — jamais un résumé condensé ("Lot terminé,
X/Y traités") à la place. Ça couvre, sans exception : le bloc `=== Etat des
lieux ===` complet, la ligne `--- Lot en cours ---`, chaque ligne
`Traitement ...` / `>>> Progression ...`, et — au dernier lot — le rapport
final (voir "Après exécution"). Colle cette sortie dans un bloc de code
markdown, dans le même message que celui qui annonce le résultat, pour
CHAQUE lot d'un traitement par lots, pas uniquement le premier ou le
dernier. Cette obligation existe parce que l'affichage EST la seule preuve,
pour la personne qui lit le chat, que le script tourne et progresse
réellement — un résumé d'une ligne masque cette preuve, même si le script
lui-même fonctionne parfaitement.

## Traitement par lots (documents volumineux)

Chaque élément coûte un appel `claude -p` séquentiel (jamais parallélisé,
voir "Exécution" ci-dessus) : un document à plusieurs dizaines d'éléments
non-textuels peut dépasser la durée d'une seule commande bloquante. Sur un
document de cette taille, passe `--batch-size N` (ex. 15-20) dès le premier
appel :

- Chaque invocation traite au plus N éléments, insère leurs descriptions
  dans `pivot.md`, ajoute leurs entrées à `nottext_meta.json`, puis
  s'arrête — code de sortie **3**, distinct de 0 (tout terminé, aucun
  échec) et 1 (tout terminé, au moins un échec) : signale explicitement
  "il reste du travail, ce n'est pas une erreur".
- **Relance exactement la même commande** (même `--batch-size`) pour
  continuer : le lot suivant retrouve automatiquement où le précédent s'est
  arrêté en relisant `pivot.md` (quels éléments portent déjà
  `<!-- rag-nottext:description -->` juste après eux) — aucun état séparé à
  transmettre toi-même entre deux appels.
- `status.json` ("nottext") n'est marqué `done` (ou `failed`) qu'au tout
  DERNIER lot, quand plus aucun élément n'est en attente — un lot
  intermédiaire (code 3) ne modifie jamais ce statut.
- **Répète l'invocation automatiquement, sans jamais demander confirmation
  entre deux lots**, jusqu'à un code de sortie 0 ou 1 (traitement réellement
  terminé). Un code de sortie **3** n'est PAS un point d'arrêt ni une
  décision à soumettre — relance immédiatement la même commande dans le
  même tour, encore et encore, tant que 3 est retourné. Le seul cas qui
  justifie de s'arrêter et de demander quoi que ce soit à la personne, c'est
  un `n_errors` non nul dans le rapport final (code de sortie 1, voir
  "Après exécution" — section "Échecs") : la personne n'a pas à valider
  chaque lot intermédiaire, seulement à être informée si des éléments
  finissent réellement en échec.
- Avant d'enchaîner sur `/rag-chunking` ou de produire le compte-rendu
  final, assure-toi seulement que le code de sortie est bien 0 ou 1 (voir
  "Après exécution").
- `--force` sur un traitement par lots interrompu repart intégralement de
  zéro (même comportement qu'un traitement en un seul lot) : jamais de
  reprise partielle combinée à un `--force`.

## Retentatives automatiques sur réponse mal formée

Chaque génération de description (`describe_code`, `describe_formula`,
`describe_image`) retente automatiquement jusqu'à `MAX_FORMAT_RETRIES`
fois (voir `_rag_lib/_claude_code_client.py`, actuellement 2 — donc 3
tentatives au total) si la réponse ne respecte pas le format demandé : JSON
invalide, champ `description` manquant, ou description multi-paragraphe.
Vérifié empiriquement sur un document réel : ce type d'échec est
majoritairement non-déterministe (le même contenu, redemandé à l'identique,
réussit la plupart du temps) plutôt qu'un défaut structurel du contenu
source — un nouvel appel `claude -p` complet (jamais une simple
re-tentative de parsing sur la même réponse) résout la grande majorité des
cas.

**Jamais retenté** en revanche pour un échec d'INFRASTRUCTURE
(`ClaudeCodeCallError`/`ImageVisionError` du process lui-même : délai
dépassé, code de sortie non nul, réponse d'erreur explicite de `claude -p`)
— retenter un timeout avec le même budget de temps n'a pas la même
justification empirique et coûte cher en pratique. Ces échecs-là restent
immédiats, un seul essai.

Un élément qui épuise ses tentatives reste marqué `erreur` exactement comme
avant (rien de nouveau côté comportement de `/rag-nottext` lui-même) — ce
mécanisme réduit simplement la fréquence des échecs à traiter manuellement
ou via une relance de lot, il ne change jamais ce qui se passe pour un échec
qui persiste malgré les retentatives.

## Prérequis

`rag_data/work/<document_id>/pivot.md` doit exister (produit par
`/rag-extraction`). Si absent, le script échoue explicitement — relance
`/rag-extraction` d'abord.

## Idempotence

Si `nottext_meta.json` existe déjà et que l'étape `nottext` est marquée
`done` dans `status.json`, le script ne refait rien et affiche
`déjà fait (nottext): <chemin>` — relance avec `--force` pour retraiter. Un
traitement partiel (certains éléments en erreur) marque l'étape `failed`
dans `status.json` : `--force` la relance entièrement (pas de reprise
élément par élément).

**`--force` sur un document déjà enrichi restaure d'abord l'état d'origine**
(noms de fichiers image `page_NNN_img_NN.ext`, `pivot.md` sans
enrichissement), à partir de `nottext_meta.json` du run précédent, avant de
retraiter — jamais un ré-enrichissement empilé sur un pivot déjà muté (qui
dupliquerait les paragraphes explicatifs à chaque passage). Si le pivot a
été modifié à la main depuis le run précédent au point qu'un bloc enrichi
n'y est plus retrouvable tel quel, le script s'arrête avec une erreur
explicite plutôt que de deviner — corrige `pivot.md` à la main ou relance
`/rag-extraction --force` avant de réessayer.

## Traitement par type d'élément

Les éléments sont détectés via la même segmentation en blocs atomiques que
`/rag-chunking` (`_rag_lib/chunk.py:split_into_blocks`, réutilisée telle
quelle — une seule logique de "qu'est-ce qu'un bloc" à maintenir, jamais
deux parsers divergents) : chaque bloc `image`, `code`, ou `formula`
(formule d'affichage déjà en LaTeX) est candidat au traitement.

**Ordre de traitement volontaire, à l'intérieur d'un lot** : formules,
puis blocs de code, puis images en dernier (voir `_process_batch` dans
`run.py`) — sans rapport avec l'ordre d'apparition dans le document, qui
reste par ailleurs celui de `pivot.md` et de `nottext_meta.json`.

1. **Formule d'affichage** (déjà en LaTeX exact dans `pivot.md`) :
   description en langage naturel via `_rag_lib/formula_description.py`.
   Le LaTeX lui-même n'est jamais modifié.
   **Regroupé par lots de `MAX_GROUP_SIZE` (5, voir
   `_rag_lib/_claude_code_client.py`) dans un SEUL appel `claude -p`**
   (`describe_formula_batch`) — jamais un appel par formule — pour réduire
   le nombre d'allers-retours séquentiels, le vrai goulot d'étranglement
   sur un document à beaucoup de formules (vérifié empiriquement). Un
   échec de format sur la réponse groupée (nombre de descriptions
   incorrect, marqueur manquant/désordonné) invalide TOUT le groupe —
   jamais un appariement partiel/approximatif — et retente le groupe
   entier (voir "Retentatives automatiques"). Un groupe qui épuise ses
   tentatives marque TOUTES ses formules en erreur ; elles restent alors
   "pending" (voir "Traitement par lots") et seront retentées,
   éventuellement regroupées différemment, au prochain lot.
2. **Bloc de code** : même principe via `_rag_lib/code_description.py`
   (`describe_code_batch`, même regroupement par 5, mêmes garanties). Le
   bloc lui-même n'est jamais modifié ; aucun renommage (pas de fichier).
3. **Image** :
   - **Classification déterministe** (`_rag_lib/image_classifier.py`) :
     heuristique sur le texte déjà reconnu par un premier passage OCR
     généraliste (densité de symboles mathématiques/lettres grecques,
     longueur moyenne des mots reconnus) — jamais de jugement Claude à cette
     étape, pour rester reproductible.
   - **Description en langage naturel** (`_rag_lib/image_vision.py`) : un
     appel `claude -p` headless avec l'outil `Read` restreint au dossier de
     l'image (`--add-dir`, jamais au projet entier) lit le fichier et
     retourne 1 à 3 phrases factuelles en français, mentionnant
     explicitement le type de contenu (schéma, courbe, formule, capture
     d'écran...).
   - **OCR adapté au type détecté** :
     - `formule` -> transcription LaTeX via pix2tex
       (`_rag_lib/ocr_formula.py`, modèle dédié, pas un LLM) —
       transcription indicative pour la recherche, pas une garantie de
       LaTeX compilable à l'identique.
     - `générale` -> texte tesseract (`_rag_lib/ocr_general.py`), uniquement
       si du texte est effectivement détecté (pas d'OCR forcé sur une image
       purement graphique).
   - **Renommage explicite** : `images/page_NNN_img_NN.ext` devient
     `images/page_NNN_<slug_de_la_description>.ext` (slug dérivé des
     premiers mots de la description). Le nom d'origine est conservé dans
     `nottext_meta.json` (`fichier_original`) — rien n'est perdu en cas de
     désaccord sur le nouveau nom.
   - **Jamais de regroupement** : chaque image nécessite son propre appel
     `Read` — un appel `claude -p` par image, traitées en dernier (voir
     "Ordre de traitement volontaire" ci-dessus).

## Mise à jour de `pivot.md`

- **Image** : le texte alternatif générique `![Illustration](...)` est
  remplacé par `![<description>](<fichier_renommé>)`.
- **Tous les types** : juste après (jamais dedans, jamais avant), un
  paragraphe est inséré, commençant par le marqueur invisible au rendu
  `<!-- rag-nottext:description -->` suivi de la description complète (même
  paragraphe, aucune ligne vide entre les deux — c'est ce qui permet à
  `/rag-chunking` de reconnaître cette description comme générée, voir
  ci-dessus). Pour une image `formule`, un second paragraphe suit,
  commençant par le marqueur `<!-- rag-nottext:ocr -->` (même principe,
  aucune ligne vide entre le marqueur et son contenu) : `Transcription LaTeX
  (OCR) : $$...$$`. Pour une image `générale` avec OCR positif, même
  marqueur : `Texte détecté dans l'image : ...`. Sans `OCR_MARKER`, ce
  paragraphe serait un bloc de prose isolé aux yeux de `chunk_markdown`, que
  le découpage glouton en chunks pourrait placer dans un chunk différent de
  l'image/description dont il dépend — `OCR_MARKER` garantit qu'il reste
  fusionné dans la même unité atomique (voir `_merge_description_blocks`
  dans `_rag_lib/chunk.py`), sans jamais faire partie du texte embeddé
  (même raisonnement que pour le code/LaTeX verbatim, voir plus haut).

## Sortie

`rag_data/work/<document_id>/nottext_meta.json` — liste d'objets, UN PAR
IDENTIFIANT UNIQUE (jamais une ligne par tentative, voir `_dedupe_entries` :
un élément retenté avec succès dans un lot ultérieur, voir "Traitement par
lots", ne garde que sa DERNIÈRE tentative — un ancien échec résolu ne
compte jamais dans les totaux ni ne pollue le rapport), avec : `type`
(`image_formule`/`image_generale`/`code`/`formule_texte`), `identifiant`
(hash du bloc original, stable même sans nom de fichier),
`fichier_original`/`fichier`/`page` (images uniquement, `null` sinon),
`original` (texte brut du bloc, code/formule uniquement, `null` pour une
image), `description`, `ocr_text` (image générale uniquement), `latex`
(image formule uniquement), `erreur` (`null` si succès). `pivot.md` et les
fichiers de `images/` sont mis à jour/renommés en place. `status.json` mis
à jour (mêmes totaux déjà dédupliqué).

## Après exécution

Le script affiche lui-même, sur ses dernières lignes de sortie, un rapport
Markdown complet et déjà formaté (titre, synthèse chiffrée, tableau
identifiant/fichier/type/longueur-description/transcription, liste des
échecs — voir `_build_report` dans `run.py`) : **relaie ce rapport tel
quel**, jamais une reconstruction manuelle à partir de `nottext_meta.json`
— le format ne laisse aucune place à l'interprétation, ce n'est donc jamais
à toi de le recalculer ni de le reformuler (même principe que
`rag-extraction/scripts/run.py:_build_report`, voir son SKILL.md).

Une décision reste la tienne, le rapport ne la prend jamais à ta place :
un ou plusieurs échecs dans le rapport ⇒ **arrête-toi avant de proposer
d'enchaîner sur `/rag-chunking`**, jamais de description ou de transcription
inventée pour combler un élément en échec — le rapport te dit SI ce cas se
présente (section "Échecs"), l'arrêt lui-même reste ton choix à faire, pas
celui du script.

N'invente jamais de description ou de transcription pour un élément dont le
contenu reste ambigu après lecture — dans ce cas, la description elle-même
doit mentionner explicitement l'incertitude (déjà demandé à Claude dans
chacun des trois prompts), jamais une affirmation inventée sur ce que
l'élément représenterait.
