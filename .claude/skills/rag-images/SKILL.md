---
name: rag-images
description: >-
  Nomme explicitement, decrit en langage naturel et OCRise (LaTeX pour les
  formules, texte pour le reste) chaque image extraite par /rag-extraction,
  puis enrichit le pivot markdown pour que ce contenu devienne recherchable
  en aval. Usage: /rag-images <pdf_condense_ou_document_id>. Troisieme etape
  du pipeline RAG, entre /rag-extraction et /rag-chunking.
---

# /rag-images — Etape 3 du pipeline RAG

Traite chaque image du dossier `images/` d'un document deja passe par
`/rag-extraction` : classification deterministe (formule mathematique vs
image generale), description en langage naturel, OCR adapte au type detecte,
renommage explicite du fichier, puis mise a jour de `pivot.md` pour que la
description et la transcription deviennent du texte normal — chunke et
indexe comme le reste par `/rag-chunking` et `/rag-index`.

Sans cette etape, une image reste une simple reference de fichier dans
`pivot.md` (`![Illustration](images/...)`) : son contenu — en particulier
une formule mathematique rendue en image plutot qu'en texte — est invisible
a la recherche vectorielle et au graphe de concepts.

**Note** : depuis que `/rag-extraction` reprend les formules directement
depuis `course.tex` quand ce fichier est disponible (voir sa section
"Résolution : course.tex" — appariement individuel par contexte de prose,
pas un simple comptage), la plupart des formules d'affichage arrivent déjà
en `pivot.md` comme texte LaTeX exact, jamais comme image : cette étape n'a
plus, dans ce cas, que les vraies images (schemas/courbes) et les rares
formules non appariees (faux positifs du detecteur PDF, ou formule trop
large) a traiter — un nombre d'images `formule` bien plus bas qu'avant cette
evolution, ce qui est attendu, pas un signe d'echec de cette etape.

## Execution

Toujours en foreground, bloquant jusqu'a completion — jamais via
`run_in_background` ni aucun mecanisme async : le script appelle `claude -p`
en interne (description d'image) et une contention entre appels imbriques
peut faire timeout un appel pourtant fonctionnel isolement, meme raison que
`/rag-extraction`.

**Interdiction de deleguer a un sous-agent** (outil `Agent`) la lecture ou la
verification des images, de `images_meta.json` ou du `pivot.md` enrichi —
cette verification doit etre faite directement, dans le meme tour de
conversation, jamais confiee a un sous-agent "pour economiser du contexte".

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-images/scripts/run.py" "<pdf_condense_ou_document_id_ou_dossier_de_travail>"
```

Le script accepte indifferemment : le meme chemin de PDF condense passe a
`/rag-extraction`, le `document_id` qu'il a affiche, ou directement le
dossier `rag_data/work/<document_id>/`.

Options :
- `--force` : retraite meme si l'etape est deja marquee `done`.
- `--model NAME` : modele Claude a utiliser pour la description d'image
  (defaut : celui de l'installation `claude` locale).

## Prerequis

`rag_data/work/<document_id>/pivot.md` doit exister (produit par
`/rag-extraction`). Si absent, le script echoue explicitement — relance
`/rag-extraction` d'abord.

## Idempotence

Si `images_meta.json` existe deja et que l'etape `images` est marquee `done`
dans `status.json`, le script ne refait rien et affiche
`deja fait (images): <chemin>` — relance avec `--force` pour retraiter.
Un traitement partiel (certaines images en erreur) marque l'etape `failed`
dans `status.json` : `--force` la relance entierement (pas de reprise
image par image).

**`--force` sur un document deja enrichi restaure d'abord l'etat d'origine**
(noms de fichiers `page_NNN_img_NN.ext` et `pivot.md` sans enrichissement),
a partir de `images_meta.json` du run precedent, avant de retraiter — jamais
un ré-enrichissement empile sur un pivot deja mute (qui dupliquerait les
paragraphes explicatifs a chaque passage). Si le pivot a ete modifie a la
main depuis le run precedent au point que ce bloc n'y est plus retrouvable
tel quel, le script s'arrete avec une erreur explicite plutot que de
deviner — corrige `pivot.md` a la main ou relance `/rag-extraction --force`
avant de reessayer.

## Traitement par image

1. **Classification deterministe** (`_rag_lib/image_classifier.py`) :
   heuristique sur le texte deja reconnu par un premier passage OCR
   generaliste (densite de symboles mathematiques/lettres grecques, longueur
   moyenne des mots reconnus) — jamais de jugement Claude a cette etape,
   pour rester reproductible.
2. **Description en langage naturel** (`_rag_lib/image_vision.py`) : un appel
   `claude -p` headless avec l'outil `Read` restreint au dossier de l'image
   (`--add-dir`, jamais au projet entier) lit le fichier et retourne 1 a 3
   phrases factuelles en francais, mentionnant explicitement le type de
   contenu (schema, courbe, formule, capture d'ecran...).
3. **OCR adapte au type detecte** :
   - `formule` -> transcription LaTeX via pix2tex (`_rag_lib/ocr_formula.py`,
     modele dedie, pas un LLM) — transcription indicative pour la recherche,
     pas une garantie de LaTeX compilable a l'identique
   - `generale` -> texte tesseract (`_rag_lib/ocr_general.py`), uniquement si
     du texte est effectivement detecte (pas d'OCR force sur une image
     purement graphique)
4. **Renommage explicite** : `images/page_NNN_img_NN.ext` devient
   `images/page_NNN_<slug_de_la_description>.ext` (slug derive des premiers
   mots de la description generee a l'etape 2). Le nom d'origine est conserve
   dans `images_meta.json` (`fichier_original`) — rien n'est perdu en cas de
   desaccord sur le nouveau nom.
5. **Mise a jour de `pivot.md`** : le texte alternatif generique
   `![Illustration](...)` est remplace par la description ; un paragraphe
   reprenant la description complete, puis la transcription LaTeX ou le
   texte OCR si present, est insere juste apres le bloc image (jamais dedans,
   jamais avant) — `/rag-chunking` traite deja un bloc `![...]` comme une
   unite atomique, ce paragraphe devient un bloc de prose normal juste apres,
   chunke avec l'image dans la meme zone du decoupage.

## Sortie

`rag_data/work/<document_id>/images_meta.json` — liste d'objets, un par
image, avec : `fichier_original`, `fichier` (nom apres renommage), `page`,
`categorie` (`formule`/`generale`), `description`, `ocr_text` (texte
generaliste, `null` si non applicable), `latex` (transcription formule,
`null` si non applicable), `erreur` (`null` si succes). `pivot.md` et les
fichiers de `images/` sont mis a jour/renommes en place. `status.json` mis a
jour.

## Apres execution

Rapporte un compte-rendu structure :
1. un titre court ("Images traitees — `<document>`")
2. une phrase de synthese chiffree (nb d'images, nb de formules, nb
   d'images generales, nb d'echecs)
3. un tableau : nom de fichier (apres renommage), categorie detectee,
   longueur de la description, OCR/LaTeX genere ou non
4. si des echecs sont survenus (description ou OCR ayant leve une erreur sur
   une image precise) : liste-les precisement (fichier + message d'erreur) —
   **arrete-toi avant de proposer d'enchainer sur `/rag-chunking`**, jamais de
   description ou de transcription inventee pour combler une image en echec.
   Le traitement des autres images n'est pas bloque par l'echec d'une seule
   (voir script : chaque image est traitee independamment).

N'invente jamais de description ou de transcription pour une image dont le
contenu reste ambigu apres lecture — dans ce cas, la description elle-meme
doit mentionner explicitement l'incertitude (deja demande a Claude dans le
prompt de description), jamais une affirmation inventee sur ce que l'image
representerait.
