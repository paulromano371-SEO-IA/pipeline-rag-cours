# rag-nottext : description des éléments non textuels

`/rag-nottext` est la troisième étape du pipeline RAG. Elle prend `pivot.md`,
produit par `/rag-extraction`, et ajoute en français une description après
chaque élément non textuel : images, blocs de code et formules d'affichage.

Le bloc lui-même ne change pas, sauf les images, renommées d'après leur
description. La description est insérée **juste après** le bloc, jamais dedans.

---

## 1. Utilisation

Le moteur est `run.py`. Il s'appelle ainsi :

```bash
".venv-rag/Scripts/python.exe" ".claude/skills/rag-nottext/scripts/run.py" "<pdf_condense_ou_document_id_ou_dossier_de_travail>"
```

Options :

- `--force` : retraite même si l'étape est déjà `done`.
- `--model NOM` : modèle Claude pour les descriptions.
- `--batch-size N` : traite au plus N éléments puis s'arrête. À utiliser dès le
  premier appel sur un document volumineux.

Codes de sortie :

| Code | Signification |
|---|---|
| 0 | Tout est terminé et correct (ou déjà fait). |
| 1 | Erreur technique ou prérequis manquant. |
| 2 | Terminé, mais au moins un élément est en échec (verdict bloquant). |
| 3 | Lot partiel : il reste du travail, ce n'est pas une erreur. |

Un code 3 se relance immédiatement avec la même commande, sans demander de
confirmation, jusqu'à 0, 1 ou 2.

---

## 2. Traitement par lots avec `nottext_lot.py`

Pour un document volumineux, chaque lot se lance avec le wrapper :

```bash
".venv-rag/Scripts/python.exe" ".claude/skills/rag-nottext/scripts/nottext_lot.py" "<document_id_ou_pdf>" --batch-size 10 [--force] [--model NOM]
```

- `--force` uniquement sur le tout premier lot.
- Un lot par appel, jamais plusieurs enchaînés dans une même commande.
- La dernière ligne de sortie dit quoi faire : `[LOT n/N]` (relancer la même
  commande), `[FIN]` (étape terminée), `[BLOQUANT]` ou `[ERREUR]` (ne pas
  relancer). C'est cette ligne, et non le code de sortie, qui décide.
- Après chaque lot, la sortie brute du script est collée dans le chat, dans un
  bloc de code, avant de lancer le lot suivant.

Règles de fin :

- Un lot où aucun élément n'aboutit termine le traitement (`[BLOQUANT]`).
- `status.json` n'est marqué `done` (ou `failed`) qu'au dernier lot.
- `--force` sur un traitement interrompu repart de zéro.

---

## 3. Ordre de traitement d'un lot

1. Formules d'affichage, par `formula_description.py` (groupes de 5, un appel `claude -p` par groupe).
2. Blocs de code, par `code_description.py` (groupes de 5).
3. Images, une par une, par `image_vision.py` (description), `ocr_general.py`
   (texte), `image_classifier.py` (formule ou générale) et `ocr_formula.py`
   (LaTeX, si formule).

Puis `run.py` écrit `pivot.md` et `nottext_meta.json`, lance les contrôles et
affiche le rapport final.

---

## 4. Prérequis, idempotence, reprise

- `rag_data/work/<document_id>/pivot.md` doit exister, sinon relance `/rag-extraction`.
- Si l'étape est déjà `done`, rien n'est refait (`déjà fait (nottext)`). Relance avec `--force` pour retraiter.
- `--force` sur un document déjà enrichi restaure d'abord l'état d'origine à
  partir de `nottext_meta.json`, sans empiler d'enrichissement. Si `pivot.md` a
  été modifié à la main au point qu'un bloc n'est plus retrouvable, le script
  s'arrête avec une erreur.

---

## 5. Contrôles et rapport

- **Entrée** (code 1 si échec) : `pivot.md` non vide, `/rag-extraction` sans bloquant, images référencées présentes, CLI `claude` disponible.
- **Sortie** (code 2 si échec) : aucun élément en échec, aucun élément sans description, identifiants uniques, aucune image référencée absente.
- Le rapport Markdown affiché par le script est relayé tel quel, jamais recalculé à la main.
- En cas de verdict bloquant, on s'arrête avant `/rag-chunking`. Aucune description n'est inventée pour combler un échec.

---

## 6. Ce qui est écrit

Dans `rag_data/work/<document_id>/` :

- `pivot.md` : chaque élément est suivi de `<!-- rag-nottext:description -->` puis de sa description, sans ligne vide entre les deux. Les images sont renommées `images/page_NNN_<slug>.ext`.
  - Image `formule` : un second paragraphe `<!-- rag-nottext:ocr -->` donne la transcription LaTeX.
  - Image `générale` avec texte détecté : même marqueur, `Texte détecté dans l'image : ...`.
- `images/` : images renommées.
- `nottext_meta.json` : une entrée par élément unique. Champs : `type` (`image_formule`, `image_generale`, `code`, `formule_texte`), `identifiant`, `fichier_original`, `fichier`, `page`, `original`, `description`, `ocr_text`, `latex`, `erreur`.
- `status.json` : statut de l'étape `nottext`.

Ces marqueurs sont lus par `_rag_lib/chunk.py` (`/rag-chunking`). Ne les modifie
jamais sans mettre à jour `chunk.py` en même temps.

---

## 7. Lister les livres prêts (sans argument)

```bash
".venv-rag/Scripts/python.exe" ".claude/skills/rag-nottext/scripts/lib/lister_a_nottext.py"
```

Lecture seule. Liste les livres de `corpuscondense/` dont l'extraction est
valide (`status` à `done`, `verdict` à `ok`) et dont `nottext` n'est pas encore
`done`. Sortie : liste numérotée, ou `Aucun livre a traiter.`

---

## 8. Arborescence

```text
rag-nottext/
├── README.md                          documentation pour un humain (ce fichier)
├── SKILL.md                           consigne lue par Claude Code
└── scripts/
    ├── run.py                         moteur : état des lieux, lot, pivot, rapport
    ├── nottext_lot.py                 lance un lot et affiche la ligne de bilan
    ├── formula_description.py         description des formules (groupes de 5)
    ├── code_description.py            description des blocs de code (groupes de 5)
    ├── image_classifier.py            formule ou image générale
    ├── image_vision.py                description d'une image par claude -p
    ├── ocr_general.py                 texte d'une image par tesseract
    ├── ocr_formula.py                 transcription LaTeX d'une formule par pix2tex
    ├── lib/
    │   └── lister_a_nottext.py        liste les livres prêts (sans argument)
    └── tessdata/                      données tesseract (fra, eng, osd)
```
