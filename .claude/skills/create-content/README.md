# create-content : rédaction d'un article à partir du RAG

Ce document explique comment fonctionne la commande `/create-content`, comment l'utiliser, quelles sont ses étapes et à quoi sert chaque script.

Le fichier `SKILL.md`, à côté de celui-ci, est la consigne lue par Claude Code. Ce README est la documentation pour un humain. En cas de désaccord, le code et `SKILL.md` font foi.

---

## 1. Ce que fait la commande

À partir d'une **expression** (par exemple « comment fonctionne une régression logistique ? »), la commande produit **un article web** pédagogique, prêt à relire et à publier. Elle ne fait pas un résumé de morceaux de texte, ni un cours recopié, et ne redit pas un article déjà écrit.

Le RAG fournit **tous les faits** : texte, formules, images et code des pdf ingérés. Le travail éditorial consiste à comprendre l'intention du lecteur, choisir un angle que les autres contenus n'ont pas pris, construire un plan qui y répond, puis faire comprendre.

La commande produit :

- un fichier `article.md` avec son frontmatter SEO (titre, description, mots-clés, durée de lecture, niveau, liens internes suggérés) ;
- une image de couverture `<slug>.png` (1200 × 630) ;
- un plan (`plan.md`) et un dossier de faits (`dossier.md`) qui justifient chaque affirmation de l'article.

Ce que le corpus ne couvre pas est signalé dans le rapport, **jamais comblé** de mémoire.

La commande **ne modifie aucun autre contenu** de `rag_data/content/work/` et ne publie rien. La publication est une étape distincte : `/publish-content <slug>`.

### Règles d'or

Les règles complètes sont dans `SKILL.md`. En résumé :

1. **Le dossier est la seule source de faits.** Analogies, transitions et mises en forme sont permises ; les affirmations techniques absentes du dossier ne le sont pas.
2. **Code verbatim.** Un bloc de code reprend à l'identique un morceau du corpus (lignes élidées par `...` tolérées). `verify.py` le contrôle.
3. **Formules en LaTeX du dossier**, expliquées en français clair.
4. **Images uniquement celles du dossier**, avec une légende écrite par le modèle à partir de leur description.
5. **Aucune fuite de la mécanique** : ni « chunk », ni « RAG », ni identifiant `[Sxxx]`, ni référence aux pdf sources (« le pdf X », « chapitre 3 », « figure 3.2 »). La traçabilité passe par des commentaires HTML `<!--src:S012-->`, retirés à la publication.
6. **Plusieurs pdf = un seul discours.** Une seule notation est gardée ; les contradictions sont signalées.
7. **Zéro duplication avec les contenus existants.** Aucune phrase, argument ou exemple d'un voisin ne se redit, et l'article ne renvoie jamais vers un voisin dans le corps du texte. Les liens internes se décident à la publication (champ `related`).
8. **Rien n'est bloquant côté voisins.** Sans voisin, on part d'une page blanche.
9. **Les facettes viennent de l'intention**, pas des voisins ni de la mémoire du modèle.
10. **Une lacune se vérifie.** « Non remonté par mes requêtes » n'est pas « absent du corpus » : seule la recherche directe de `check_gap.py` permet la seconde formule.

---

## 2. Utilisation

```text
/create-content <expression>
```

Depuis la racine du projet, chaque étape peut aussi se lancer à la main avec l'interpréteur du projet :

```bash
".venv-rag/Scripts/python.exe" ".claude/skills/create-content/scripts/etape0_cadrage.py" "<expression>"
```

Remplacer le nom du script par celui de l'étape voulue (voir section 5), dans l'ordre.

### Règles d'exécution

- Toujours en **premier plan**, sans `run_in_background`, et jamais deux étapes en parallèle.
- Chaque commande d'étape passe `timeout: 600000` (600 s, le maximum en premier plan). Au-delà de 120 s, l'outil basculerait la commande en arrière-plan, ce qui est interdit.
- L'interpréteur est toujours `.venv-rag/Scripts/python.exe`, jamais `python` ni `py`. Un hook `PreToolUse` l'impose (`.claude/hooks/block_system_python.py`).
- Un hook (`.claude/hooks/block_rag_background.py`) refuse aussi tout `run_in_background: true` sur un script de skill.
- Aucun sous-agent pour lire ou vérifier les sorties.

Chaque script affiche une ligne `[étape N] OK` ou `[étape N] ÉCHEC — …`. Si la ligne commence par `ÉCHEC`, **on s'arrête** : l'étape suivante ne se lance pas.

### Sans argument : choisir une expression recommandée

Tapée **sans argument**, la commande ne lance aucune étape. Elle propose des expressions prises dans `rag_data/expressions/EXPRESSIONS.md`, lu en lecture seule et jamais modifié :

1. seules les lignes dont la colonne « Contenu consistant probable ? » vaut **Opportunité** ou **Très probable** sont gardées ;
2. la colonne **Question** de ces lignes est reprise, les doublons sont retirés ;
3. les questions dont le slug est très proche (similarité ≥ 0,9) d'un dossier déjà présent dans `rag_data/content/work/` sont écartées ;
4. la commande affiche deux listes numérotées, « Opportunité » puis « Très probable », avec une numérotation continue ;
5. elle termine par la question « Quel numéro voulez-vous traiter, ou quelle série (--serie A,B) ? » ;
6. le numéro choisi donne la question, qui devient l'expression de la commande.

Script utilisé, à lancer depuis la racine du projet :

```bash
".venv-rag/Scripts/python.exe" ".claude/skills/create-content/scripts/lib/choix_expression.py"
```

Avec un numéro, le même script affiche la question correspondante :

```bash
".venv-rag/Scripts/python.exe" ".claude/skills/create-content/scripts/lib/choix_expression.py" <numéro>
```

Les avertissements (lignes ignorées, questions écartées) vont sur la sortie d'erreur et n'altèrent pas la liste.

### Mode série : `--serie A,B`

Permet de traiter plusieurs expressions d'affilée.

- **Condition** : `--serie A,B` n'est accepté que **après** l'affichage des listes par la commande sans argument, dans la même conversation. Sinon, la commande ne lance rien et demande de lancer d'abord `/create-content`.
- **Forme** : `/create-content --serie A,B` traite les questions de **A à B inclus**, dans l'ordre de la liste. Les numéros sont ceux de la liste complète. Par exemple, `--serie 1,9` traite les questions 1 à 9.
- **Liste figée** : la liste de la série est calculée une fois, au début. Un article créé pendant la série ne décale pas la numérotation.
- **Déroulé** : chaque question est traitée comme si elle avait été lancée seule. Les étapes 0 à 8 s'enchaînent, avec la sortie brute de chaque étape, puis le rapport final de l'article. Une ligne `## Article k/K — <question>` marque le début de chaque article.
- **Arrêt** : si une étape 0 à 7 affiche `ÉCHEC`, la série s'arrête sur cet article. Les articles déjà terminés restent en place. Un échec de l'étape 8 n'arrête pas la série.
- **Voisins** : un article terminé devient un voisin du suivant. Chaque article évite donc de redire les précédents.
- **Bilan** : à la fin, une ligne par article (numéro, slug, verdict).
- **Reprise** : après une interruption, relancer `--serie <article à reprendre>,B` avec le même B. Les étapes déjà faites sont sautées (voir section 4).

Script utilisé pour afficher la liste de la série :

```bash
".venv-rag/Scripts/python.exe" ".claude/skills/create-content/scripts/lib/choix_expression.py" --serie A,B
```

Ce script ne lance **aucun article** : il affiche seulement la liste à traiter. Les articles sont ensuite lancés par Claude, selon `SKILL.md`.

---

## 3. Mémoire et cache des appels au modèle

Tous les appels passent par `call_claude` de `scripts/commun.py`, avec un modèle et un effort fixes :

- **modèle** : `claude-haiku-5-5` ;
- **effort** : `medium` ;
- **prompt système** : passé par fichier (`--system-prompt-file`). Sur Windows, la ligne de commande est limitée à environ 32 000 caractères, et les voisins complets la dépassent.

Pour profiter du cache de prompt, le `system` est identique d'une étape à l'autre chaque fois que c'est possible. Le cache ne se partage que sur le début de la requête :

- étapes 3, 4 et 5 partagent le même `system` (le dossier) ;
- étapes 2a et 2b partagent le même `system` (les voisins) ;
- étapes 0 et 1 partagent la même tête (l'expression et le slug) ;
- les consignes et le plan vont après ce préfixe, dans le message utilisateur.

L'étape 2c a son propre `system`. Chaque appel écrit ses tokens dans `usage.jsonl`, et l'étape 8 les totalise dans `totaltoken.jsonl`.

---

## 4. Reprise après interruption

Les étapes 0, 1, 2a, 2b, 2c, 3 et 4 vérifient d'abord si leur résultat existe déjà (section de `plan.md`, `voisins.json`, `dossier.md`/`dossier.json`, `article.md`). Si oui, elles n'appellent pas le modèle et affichent `[étape N] DÉJÀ FAIT — …`, avec le code de sortie 0.

Pour refaire une étape, ajouter `--force` à la fin de la commande. Les étapes suivantes ne sont pas invalidées automatiquement : il faut alors les relancer aussi, avec `--force`.

L'étape 5 n'a pas de reprise : elle relance `toc.py` et `verify.py` à chaque lancement, et n'appelle le modèle que si `verify.py` signale des erreurs. Les étapes 6, 7 et 8 ne sont pas concernées : elles sont sans appel au modèle et idempotentes.

---

## 5. Les étapes

Le pipeline comporte les étapes 0 à 8, dans l'ordre. Le texte envoyé au modèle de chaque étape se trouve dans `scripts/prompts/`.

| Étape | Script | Rôle |
| ----- | ------ | ---- |
| 0 | `etape0_cadrage.py` | Calcule le slug, crée `rag_data/content/work/<slug>/` et écrit la section « Cadrage » de `plan.md` (intention, question du lecteur, promesse, hors périmètre). Appel modèle. |
| 1 | `etape1_facettes.py` | Déduit de l'intention le catalogue des facettes et écrit la section « Facettes » de `plan.md`, avec des sous-requêtes au format `étiquette::mots-clés`. Appel modèle. |
| 2a | `etape2a_voisins.py` | Lance `scan_existing.py` (écrit `voisins.json`), puis consigne chaque voisin, lu en entier, dans « Voisins » de `plan.md`. Sans voisin, écrit « Aucun voisin ». Appel modèle. |
| 2b | `etape2b_croisement.py` | Remplit les colonnes « Voisin qui la couvre » et « Décision » de « Facettes » à partir des voisins. Appel modèle. |
| 2c | `etape2c_collecte.py` | Lance `gather.py` avec les sous-requêtes, vérifie les lacunes avec `check_gap.py`, fait au plus une relance. Écrit `dossier.md` et `dossier.json`, sans modifier `plan.md`. Appel modèle. |
| 3 | `etape3_plan.py` | Lit `dossier.md` en entier et écrit la section « Plan » : une ligne par H2, avec la facette, la question du lecteur, les chunks `Sxxx` et les images, formules ou blocs de code prévus. Appel modèle. |
| 4 | `etape4_redaction.py` | Rédige `article.md` (frontmatter SEO puis corps) à partir de `plan.md` et `dossier.md`. Le sommaire n'est pas écrit ici. Appel modèle. |
| 5 | `etape5_verification.py` | Lance `toc.py` (sommaire) puis `verify.py`. Si `verify.py` signale des erreurs, le modèle corrige l'article, puis on relance, jusqu'à 10 tours. Appel modèle si correction. |
| 6 | `etape6_couverture.py` | Écrit `<slug>.png` (1200 × 630), image mise en avant utilisée par `/publish-content`. Sans appel modèle. |
| 7 | `etape7_indexfileupdate.py` | Ajoute ou met à jour la ligne du contenu dans `rag_data/content/INDEX.md`. Une ligne existante pour le même slug est remplacée, jamais dupliquée. Sans appel modèle. |
| 8 | `etape8_totaltokens.py` | Totalise les tokens de `usage.jsonl` dans `totaltoken.jsonl`. Sans appel modèle. |

### Étape 2c : code de sortie 3

Si le corpus n'a rien sur le sujet, l'étape renvoie le code de sortie 3 : la commande s'arrête et le dit franchement. Aucun article n'est écrit de mémoire.

### Étape 5 : contrôles de `verify.py`

`verify.py` contrôle notamment le code verbatim, les formules, les références aux pdf et aux autres contenus, et la matière distinctive citée. Les doublons ne font qu'avertir, jamais échouer.

- `[étape 5] OK` : l'article est conforme. Le rapport indique le nombre d'avertissements restants, et justifie chacun en une ligne.
- `[étape 5] ÉCHEC` : l'article n'est pas terminé. Ne pas le déclarer terminé.

### Étape 7 : index

La colonne « Article publié » vaut « Non » à la création, et passe à « Oui » quand `/publish-content` a écrit sa trace `rag_data/content/published/<slug>.json`.

### Étape 8 : totaux de tokens

Lit `usage.jsonl` (seules les lignes `source: "api"` comptent) et écrit `totaltoken.jsonl`, réécrit à chaque lancement : une seule ligne, avec `article`, `appels_api` et le total de chacun des 4 types de tokens (`input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`). Un échec de cette étape n'invalide pas l'article.

---

## 6. Rapport final

Après l'étape 5, la commande rédige un rapport bref et factuel :

- chemin de l'article (`rag_data/content/work/<slug>/article.md`) et du plan (`plan.md`) ;
- titre, description SEO, durée de lecture, niveau ;
- intention retenue et angle ;
- voisins : lesquels (ou « aucun voisin, page blanche »), ce qu'ils couvrent, ce que cet article fait de différent, liens internes suggérés pour la publication (`related`), doublons restants et pourquoi on les garde ;
- plan : les H2, et en quoi il diffère de ceux des voisins ;
- couverture : pdf utilisés, nombre de formules, images et blocs de code repris ;
- diversité des sources : `book_coverage` et taux de matière distinctive citée ;
- facettes : traitée, couverte par un voisin, ou hors périmètre, avec la raison ;
- lacunes du corpus, en séparant « absent du corpus » (confirmé par `check_gap.py`) de « non remonté par mes requêtes » (non vérifié) ;
- décisions éditoriales notables ;
- verdict commodity ou non-commodity, jugé et non tranché par le script ;
- résultat de `verify.py`.

L'article n'est pas recopié dans la réponse : on l'ouvre dans `article.md`.

---

## 7. Codes de sortie

| Code | Signification |
| ---- | ------------- |
| 0 | OK, ou `DÉJÀ FAIT`. |
| 2 | Échec. À lire, et on s'arrête. |
| 3 | Uniquement à l'étape 2c : le corpus n'a rien sur le sujet. |

Un appel au modèle qui échoue est retenté jusqu'à 3 essais.

---

## 8. Fichiers produits

Dans `rag_data/content/work/<slug>/` :

| Fichier | Contenu |
| ------- | ------- |
| `plan.md` | Sections « Cadrage », « Facettes », « Voisins » et « Plan ». |
| `voisins.json` | Voisins repérés à l'étape 2a. |
| `dossier.md` et `dossier.json` | Faits du corpus, avec leurs identifiants `Sxxx`. |
| `article.md` | L'article, avec frontmatter SEO et commentaires `<!--src:-->`. |
| `<slug>.png` | Image de couverture. |
| `usage.jsonl` | Une ligne par appel au modèle. |
| `totaltoken.jsonl` | Une seule ligne, totaux des appels et des tokens. |

Et `rag_data/content/INDEX.md`, avec une ligne par contenu, le plus récent en premier. L'en-tête « Contenus créés : N » est recalculé à chaque écriture.

| Colonne | D'où elle vient | Signification |
| ------- | --------------- | ------------- |
| **Mis à jour** | Heure de l'étape 7 | Date et heure (AAAA-MM-JJ HH:MM) de la dernière génération. Ce n'est pas la date de publication. C'est aussi la clé de tri. |
| **Slug** | Nom du dossier `work/<slug>/` | Identifiant du contenu, clé qui permet de remplacer une ligne existante. |
| **Titre** | `title` du frontmatter | Titre SEO de l'article. |
| **Niveau** | `level` du frontmatter | `débutant` ou `intermédiaire`. |
| **Lecture (min)** | `reading_time` du frontmatter | Durée de lecture estimée, en minutes. |
| **Article publié** | Présence de `published/<slug>.json` | « Non » à la création, « Oui » dès que la trace existe. Une régénération garde « Oui ». |

La colonne « Article publié » reflète la trace locale écrite par `/publish-content`. Elle ne vérifie pas l'état réel de l'article sur WordPress.

---

## 9. Dossier `scripts/`

| Élément | Rôle |
| ------- | ---- |
| `etape0_cadrage.py` à `etape8_totaltokens.py` | Les étapes du pipeline, décrites ci-dessus. |
| `commun.py` | Fonctions partagées : appel au modèle avec 3 essais, reprise et `--force`, lecture des sections de `plan.md`, mise à jour de `INDEX.md`. |
| `prompts/` | Textes envoyés au modèle, un par étape appelant le modèle. |
| `lib/gather.py` | Recherche dans le RAG, à partir des sous-requêtes de l'étape 2c. |
| `lib/slugs.py` | `slugify_expression` : transforme une expression en slug. Module léger, sans torch. |
| `lib/choix_expression.py` | Commande sans argument : liste les expressions, affiche la question choisie, gère `--serie A,B`. |
| `lib/check_gap.py` | Recherche directe pour confirmer ou infirmer une lacune. |
| `lib/scan_existing.py` | Repère les contenus existants proches du sujet (étape 2a). |
| `lib/verify.py` | Contrôles de l'article : code, formules, références interdites, doublons, matière distinctive. |
| `lib/toc.py` | Génère le sommaire et les ancres des H2. Réutilisé par `/publish-content`. |
| `lib/make_cover.py` | Génère l'image de couverture. |

---

## 10. Ce que la commande n'est pas

- Elle **n'écrit jamais** dans `rag_data/db/`, ni dans `rag_data/work/` (le corpus), ni dans le code du pipeline.
- Elle **ne supprime rien** sous `rag_data/`.
- Elle **ne modifie jamais** un autre contenu de `rag_data/content/work/`.
- Elle **ne publie rien** sur WordPress. La publication est `/publish-content`.

Après création, `/publish-content <slug>` consomme `article.md`, `images/` et `<slug>.png`. Les commentaires `<!--src:...-->` sont retirés à ce moment-là, et chaque H2 reçoit un `id` identique à l'ancre du sommaire : le sommaire est cliquable sur le site.
