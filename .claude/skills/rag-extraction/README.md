# rag-extraction : conversion d'un cours condensé en markdown pivot

Ce document explique comment fonctionne la commande `/rag-extraction`, comment l'utiliser, quelles sont ses étapes et à quoi sert chaque script.

Le fichier `SKILL.md`, à côté de celui-ci, est la consigne lue par Claude Code. Ce
README est la documentation pour un humain. En cas de désaccord, le code fait foi.

---

## 1. Ce que fait la commande

`/rag-extraction <pdf_condense>` convertit un PDF de cours condensé, produit par
`/cours-condense`, en **markdown pivot** : un fichier `pivot.md` où les titres, le code et
la prose sont distingués. Les images natives sont extraites dans un dossier `images/`.

C'est la **deuxième étape du pipeline RAG**, après `/cours-condense`. Les étapes suivantes lisent le pivot :

`/rag-nottext` → `/rag-chunking` → `/rag-index` → `/rag-concepts` → `/rag-graphe`

La commande est **quasi entièrement déterministe**. Elle s'appuie sur PyMuPDF. Aucun appel externe à un modèle n'est fait à l'intérieur des scripts mais le modèle de la session principale est utilisé ( cf 2.5) , et aucun contenu n'est rédigé. La réparation des ligatures et le recollage des césures utilisent un dictionnaire français hors ligne (`pyspellchecker`), jamais une inférence.

### Utilisation

- `/rag-extraction` : liste les livres à extraire et attend ton choix. Le choix se fait par numéro, ou par nom (correspondance dans `corpusdedepart/`, insensible à la casse).
- `/rag-extraction --all` : traite les livres de la liste un par un, dans l'ordre, puis affiche un tableau récapitulatif (verdict et `document_id` par livre).
- `/rag-extraction <chemin_vers_pdf_condense>` : traite un seul PDF.

Options de l'étape 1 :

- `--force` : refait l'extraction même si elle est déjà faite.
- `--pages DEBUT:FIN` : limite à une plage de pages (fin exclue), pour tester.

Règles d'exécution :

- Interpréteur : toujours `<racine_projet>/.venv-rag/Scripts/python.exe`, jamais `python` ni `py`. Imposé par le hook `.claude/hooks/block_system_python.py`.
- Premier plan uniquement, une commande du pipeline à la fois. Imposé par le hook `.claude/hooks/block_rag_background.py`.
- Pas de sous-agent pour lire, analyser ou corriger les sorties : c'est le modèle, dans la session principale, qui le fait.
- Ne jamais supprimer rien sous `rag_data/`.

---

## 2. La commande est scindée en trois scripts

La conversion se fait en trois scripts, lancés **l'un après l'autre**, dans cet ordre. Chacun
fait une seule chose et transmet son résultat au suivant par un fichier JSON.

### 2.1 `etape1_extraction.py` : produire le texte pivot

- **Rôle** : transformer le PDF condensé en `pivot.md`.
- **Entrée** : le PDF condensé, dans `corpuscondense/`.
- **Ce qu'il fait** : lit chaque page, classe les lignes (titres, code, prose), traite les
  formules, copie les illustrations, répare les ligatures, puis écrit le pivot.
- **Ce qu'il écrit** : `pivot.md`, `images/`, `meta.json`, et `etape1_extraction.json`
  (compteurs et problèmes de qualité, relus par l'étape 2).
- **Cas particulier** : si le livre est déjà extrait avec succès, il s'arrête en disant
  « déjà fait ». Il ne refait rien sauf avec `--force`.

### 2.2 `etape2_verification.py` : contrôler la fidélité

- **Rôle** : vérifier que `pivot.md` correspond bien au cours d'origine.
- **Entrée** : le même PDF. Il lit `pivot.md` et `etape1_extraction.json`.
- **Ce qu'il fait** : compare `pivot.md` à `course.tex` et au dossier `illustrations/`.
  Si des titres sont bloquants, il les corrige d'après `course.tex`, après avoir sauvegardé
  l'original dans `pivot.avant_correction.md`, puis il revérifie.
- **Ce qu'il écrit** : `etape2_verification.json` (résultats et corrections de titres),
  relu par l'étape 3.

### 2.3 `etape3_statut.py` : rendre le verdict

- **Rôle** : décider si l'extraction est exploitable, et l'enregistrer.
- **Entrée** : le même PDF. Il lit les deux JSON des étapes 1 et 2.
- **Ce qu'il fait** : compte les problèmes bloquants (qualité et fidélité), décide du
  verdict, écrit l'état dans `status.json` et affiche le rapport final.
- **Ce qu'il écrit** : `status.json` (étape `extraction` en `done`, avec le verdict).
- **Codes de sortie** : `0` = OK, `2` = BLOQUANT (à corriger avant la suite), `1` = ÉCHEC
  (sortie invalide).

### 2.4 Le cas sans argument : `lib/lister_a_extraire.py`

Lancé sans PDF, il ne fait pas partie des trois étapes. Il liste les livres qui restent à
extraire, sans rien écrire. Un livre apparaît tant que son extraction n'est pas réussie :
pivot ou `status.json` manquant, étape `extraction` pas en `done`, ou verdict différent de `ok`.

Le condensé traité est `corpuscondense/<slug>.pdf`, où `<slug>` est le nom du PDF source sans extension, passé par `_slugify_livre` (`_rag_lib/paths.py`).

---

### 2.5 Correction par le modèle

Les scripts signalent les problèmes, mais ne corrigent pas le contenu du pivot. C'est le modèle, dans la session principale, qui applique les corrections. Avant chaque correction, `pivot.md` est sauvegardé (copie dans le scratchpad). Les scripts de correction sont écrits dans `rag_data/work/<document_id>/scripts/`, sans limite de nombre.

- Chaque sortie d'étape est relayée en entier, sans reformulation ni troncature. Le rapport de l'étape 3 est affiché tel quel.
- Étape 1 ou 2 en erreur : le modèle corrige, puis relance l'étape en échec, jusqu'à ce qu'il n'y ait plus d'erreur.
- Étape 2 en `ÉCHEC` avec « etape1_extraction.json absent » : le livre a été extrait avant cette version. Relancer l'étape 1 avec `--force`.
- Étape 3 en `BLOQUANT` (code `2`) : le modèle corrige sans demander ton accord, uniquement les défauts signalés dans le rapport, puis relance l'étape 2, puis l'étape 3. Il continue jusqu'à ce que `pivot.md` soit `OK`.
- Étape 3 en `OK` : l'extraction est exploitable, `/rag-nottext` peut démarrer. Le message EXTRACTION REUSSIE !!!! est affiché.

---

## 3. Arborescence de la commande

```
.claude/skills/rag-extraction/
├── SKILL.md                          consigne lue par Claude Code (ce qu'il doit faire, dans quel ordre)
├── README.md                         ce document, pour un humain
└── scripts/
    ├── commun.py                     chemins et lecture/écriture des JSON partagés par les trois étapes
    ├── etape1_extraction.py          étape 1 : PDF condensé vers pivot.md
    ├── etape2_verification.py        étape 2 : contrôle de fidélité et correction des titres
    ├── etape3_statut.py              étape 3 : verdict, status.json, rapport final
    └── lib/                          modules appelés par les étapes
        ├── convert.py                conversion PDF vers pivot (lignes, code, formules, images, pages)
        ├── recollage_code.py         recollage des blocs de code coupés par un saut de page
        ├── ligature_repair.py        réparation des ligatures et des symboles de police
        ├── tex_source.py             lecture de course.tex (formules, titres, code, illustrations)
        ├── fidelity_check.py         contrôle de fidélité entre pivot.md et course.tex
        ├── correction_titres.py      correction des titres d'après course.tex
        ├── rapport.py                mise en forme du rapport final
        └── lister_a_extraire.py      liste des livres restant à extraire (sans argument)
```

Les modules partagés avec d'autres skills (chemins, `status.json`, contrôles d'entrée et de sortie, qualité, garde-fou de l'interpréteur) sont dans .claude/skills/_rag_lib/`, en dehors de ce dossier.

Les dossiers `__pycache__/` sont générés automatiquement par Python. Ils ne contiennent aucun code à maintenir.

---

## 4. Sorties

Dans `rag_data/work/<document_id>/` :

- `pivot.md` : texte structuré (titres, code, prose, images)
- `images/` : images natives et zones de formule
- `meta.json` : `document_id`, `source_pdf`
- `status.json` : suivi de l'étape `extraction`, avec les comptes de bloquants
- `etape1_extraction.json`, `etape2_verification.json` : résultats échangés entre les étapes
- `pivot.avant_correction.md` : présent seulement si l'étape 2 a corrigé des titres
- `scripts/` : scripts de correction écrits par le modèle, le cas échéant
