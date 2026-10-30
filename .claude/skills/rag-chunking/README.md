# /rag-chunking : découpage du pivot en chunks embeddables

Ce document explique la commande `/rag-chunking` : ce qu'elle fait, comment
l'utiliser, et ce qu'elle vérifie. Il est destiné à un humain. Le fichier
`SKILL.md`, à côté de celui-ci, est la consigne lue par Claude Code. En cas de
désaccord, le code fait foi.

---

## 1. Ce que fait la commande

`/rag-chunking` découpe le markdown pivot d'un document (`pivot.md`) en
**chunks** prêts à être embeddés. Un chunk fait environ **400 tokens**.

- Le découpage est **déterministe** : aucun appel à un modèle.
- Le comptage des tokens utilise le tokenizer réel du modèle d'embedding
  (`BAAI/bge-m3`, voir `_rag_lib/chunk.py`). Un tokenizer générique n'est pas
  utilisé, car son décompte ne correspond pas à la vraie limite du modèle qui
  embeddera ensuite les chunks.
- C'est la **quatrième étape du pipeline RAG** : elle vient après
  `/rag-nottext` et précède `/rag-index`.

---

## 2. Utilisation

Sans argument, la commande liste les pdfs prêts pour le chunking. Avec un
argument, elle découpe ce pdf.

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/rag-chunking/scripts/run.py" "<pdf_condense_ou_document_id_ou_dossier_de_travail>"
```

`<racine_projet>` est le dossier qui contient `.claude/`.

Le script accepte indifféremment :
- le chemin du PDF condensé, comme pour `/rag-extraction` ;
- le `document_id` affiché par `/rag-extraction` ;
- le dossier `rag_data/work/<document_id>/`.

| Option | Effet |
|---|---|
| `--force` | Réécrit `chunks.json` entièrement, même si l'étape est déjà faite. |
| `--target-tokens N` | Taille cible d'un chunk, en tokens. Défaut : 400. |
| `--overlap-blocks N` | Nombre de blocs reportés du chunk précédent. Défaut : 1. |
| `--keep-structural` | Conserve les sections structurelles au lieu de les exclure. |

### `--keep-structural`

À réserver à un pdf dont une section de ce nom porte du **vrai contenu**. Le
choix est enregistré dans `status.json` et respecté par les contrôles de
`/rag-index` et `/rag-concepts`.

---

## 3. Lister les pdfs (sans argument)

Le script `scripts/lib/lister_a_chunker.py` affiche les pdfs de
`corpuscondense/` prêts pour `/rag-chunking` :
- l'étape `nottext` est à `done` dans `status.json` ;
- l'étape `chunking` n'est pas encore `done`.

Cette lecture ne modifie rien. Ensuite, on choisit :
- **un numéro** : la liste est relancée pour avoir l'état à jour, puis on prend
  le pdf correspondant ;
- **un nom** : recherche insensible à la casse, dans `corpuscondense/`.
  Zéro ou plusieurs correspondances : la commande demande de préciser.
- **`--all`** : les pdfs sont traités un par un, dans l'ordre, avec un tableau
  récapitulatif à la fin.

---

## 4. Prérequis

- `rag_data/work/<document_id>/pivot.md`, produit par `/rag-extraction`. S'il
  est absent, le script échoue : il faut relancer `/rag-extraction` d'abord.
- L'interpréteur `.venv-rag/Scripts/python.exe`. Jamais `python` ni `py` : un
  hook refuse ces appels.

---

## 5. Exécution

- Toujours au **premier plan**, en bloquant jusqu'à la fin. Un hook refuse tout
  lancement en arrière-plan.
- Jamais deux commandes du pipeline en parallèle.
- Pas de sous-agent pour lire les sorties ou rédiger le compte-rendu : ceux-ci
  sont produits dans le même tour que l'exécution du script.

---

## 6. Étapes du découpage

### 6.1 Contrôles d'entrée

Avant de découper, `_rag_lib/checks.py` vérifie :
- `pivot.md` existe et n'est pas vide ;
- `/rag-extraction` est terminé sans bloquant ;
- `/rag-nottext` est terminé sans échec ;
- **aucun bloc code, image ou formule n'est sans description**
  (marqueur `DESCRIPTION_MARKER`). Un bloc sans description dégraderait
  l'embedding, car son texte brut serait embeddé tel quel.

En cas d'échec, le script sort avec le code `1`.

### 6.2 Fusion des descriptions

Un bloc code, image ou formule suivi de sa description générée par
`/rag-nottext` est fusionné avec elle, en une seule unité atomique :

- `text` (utilisé pour la citation) garde le verbatim **et** la description ;
- `embed_text` (utilisé par `/rag-index`) ne garde **que** la description.

Le LaTeX ou le code brut n'est pas embeddé, car il dilue la similarité avec une
question en français. Un bloc pas encore traité par `/rag-nottext` (ou dont le
traitement a échoué) n'a pas de description : `embed_text` retombe alors sur le
texte brut du bloc.

### 6.3 Exclusion des sections structurelles

Certaines sections ne sont pas du contenu de cours : table des matières,
sommaire, index, bibliographie, annexes, glossaire, références, remerciements.
Elles sont reconnues sur le **titre** de la section, accents et casse ignorés
(liste dans `_rag_lib/structural.py`).

La section est retirée, titre compris, jusqu'au prochain titre de niveau égal
ou supérieur. Cette exclusion se fait **avant** le découpage : aucun chunk n'est
produit, donc rien n'est embeddé, indexé, envoyé à l'extraction de concepts ni
relié au graphe. Un découpage glouton pourrait en effet mêler la fin de la table
des matières au début du chapitre suivant, et le filtre ne le verrait pas.

`pivot.md` n'est pas modifié. Le script annonce les sections exclues et les
enregistre dans `status.json` (`structural_sections_excluded`,
`structural_blocks_excluded`).

Mesures sur les deux premiers pdfs : ISLR passe de 227 à 211 chunks, Bratanic
de 136 à 117. Aucun bloc code, image ou formule n'est perdu.

### 6.4 Découpage et recouvrement

- Un bloc atomique (code, image, formule) qui dépasse `--target-tokens` reste
  **entier**, dans son propre chunk, même si celui-ci dépasse la cible.
- Seul un bloc de prose peut être scindé, sur des frontières de phrase.
- `--overlap-blocks N` reporte les N derniers blocs d'un chunk vers le suivant.
- **Les titres ne sont jamais reportés.** Si le dernier bloc d'un chunk est un
  titre, `--overlap-blocks 1` peut donc ne rien reporter dans ce cas précis.

### 6.5 Écriture

`chunks.json` est écrit **en une seule fois**. Un `--force` répété ne peut donc
jamais accumuler ni dupliquer du contenu. Chaque chunk contient : index, texte,
texte embeddé, fil d'ariane des titres, nombre de tokens, présence de code.

---

## 7. Idempotence

Sans `--force`, si `status.json` contient `{"chunking": {"status": "done"}}` et
que `chunks.json` existe, le script affiche `deja fait (chunking): <chemin>`, ne
fait rien et sort avec le code `0`.

---

## 8. Contrôles de sortie

### Bloquants (code `2`)

Ils arrêtent le pipeline : l'étape est marquée `failed` et le verdict est écrit
dans `status.json`.

- `chunk_markdown` produit une liste vide ;
- `chunks.json` est invalide : mauvaise forme, index dupliqués ;
- le nombre de chunks ne correspond pas au contenu du fichier ;
- un bloc de code est coupé ou non refermé dans un chunk ;
- un chunk porte le fil d'ariane d'une section structurelle (sauf avec
  `--keep-structural`).

**Dans ce cas, on s'arrête. On ne propose jamais d'enchaîner sur `/rag-index`.**

### Indicatifs (sans blocage)

- Un chunk contient des points de conduite « . . . . » hors d'une section
  reconnue : une table des matières est probablement sous un titre non reconnu.
  Dans ce cas, on s'arrête là aussi et on ne propose pas `/rag-index`.
- La plupart des chunks ont un fil d'ariane vide : l'extraction n'a probablement
  pas détecté les titres. La recherche fonctionne, mais avec moins de contexte.

---

## 9. Codes de sortie

| Code | Signification |
|---|---|
| `0` | Terminé, ou déjà fait. |
| `1` | Échec d'entrée (contrôles de la section 6.1). |
| `2` | Bloquant (voir la section 8). L'étape est marquée `failed`. |

---

## 10. Sorties

Dans `rag_data/work/<document_id>/` :

| Fichier | Contenu |
|---|---|
| `chunks.json` | Liste des chunks : index, texte, texte embeddé, fil d'ariane, nombre de tokens, présence de code. |
| `status.json` | Verdict de l'étape, sections exclues, choix `--keep-structural`. |

Ces fichiers restent dans le dossier de travail centralisé, jamais à côté du PDF
source.

### Critère d'exploitabilité

`/rag-index` et `/rag-concepts` vérifient uniquement l'existence de
`chunks.json`. Ils ne relisent pas `status.json`. Le découpage est donc
exploitable dès que `chunks.json` existe. Les deux fichiers restent synchronisés
en pratique, car `chunks.json` est écrit juste avant le marquage `done`, et
jamais après un échec.

---

## 11. Compte-rendu

Le script affiche, dans cet ordre :

1. un titre court : « Chunking terminé — `<nom du document>` » ;
2. une phrase de synthèse chiffrée : nombre de chunks, nombre moyen de tokens par
   chunk, nombre de chunks contenant du code, proportion de chunks dont le fil
   d'ariane est vide.

La distinction bloquant / indicatif doit être reportée telle quelle.

---

## 12. Quand s'arrêter

- **Bloquant (code `2`)** : ne pas proposer `/rag-index`.
- **Indicatif** : le signaler, sans bloquer la suite.

---

## 13. Fichiers du skill

| Fichier | Rôle |
|---|---|
| `scripts/run.py` | Point d'entrée : contrôles, découpage, écriture de `chunks.json`, verdict, résumé. |
| `scripts/lib/lister_a_chunker.py` | Liste les pdfs prêts pour le chunking (lecture seule). |

Le découpage lui-même est dans `_rag_lib/` :
- `chunk.py` : segmentation en blocs, fusion des descriptions, exclusion des
  sections structurelles, découpage, recouvrement, comptage des tokens ;
- `structural.py` : liste des sections structurelles à exclure ;
- `checks.py` : contrôles d'entrée et de sortie ;
- `paths.py` : emplacement des dossiers de travail ;
- `status.py` : lecture et écriture de `status.json`.

---

## 14. Ce que la commande n'est pas

- Elle **n'appelle aucun modèle**. Le tokenizer sert seulement à compter.
- Elle **ne modifie pas** `pivot.md`.
- Elle **n'embedde rien** : c'est le rôle de `/rag-index`.
- Elle **ne supprime rien** sous `rag_data/`.
