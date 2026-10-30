---
name: create-content
description: >-
  Rédige un contenu web pédagogique, unique et agréable à lire à partir d'une
  expression (ex. "comment fonctionne une régression logistique ?"), en
  s'appuyant à 100% sur le RAG (texte, formules, images, code de tous les
  pdf ingérés) — jamais sur les connaissances générales du modèle pour les
  faits. Tient compte des contenus déjà écrits pour ne rien redire d'un autre
  article, et construit un plan propre à l'intention de l'expression. Produit
  un article Markdown + frontmatter SEO + images prêtes à publier dans
  rag_data/content/work/<slug>/. Usage: /create-content <expression>.
---

# /create-content — Générateur de contenus web basé sur le RAG

`/create-content <expression>` transforme la connaissance du corpus en UN
article web qui donne envie d'être lu jusqu'au bout — pas un résumé de
chunks, pas un cours recopié, et **pas une redite d'un article déjà écrit**.
Le RAG fournit **tous les faits** ; ton travail est éditorial : comprendre ce
que le lecteur veut vraiment (l'intention), choisir un angle que les autres
contenus n'ont pas pris, construire un plan qui y répond, faire comprendre.

## Sans argument : choisir une expression recommandée

Si la commande est tapée **sans argument**, ne lance aucune étape. Lance (timeout 600000) :

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/create-content/scripts/lib/choix_expression.py"
```

Relaie la sortie brute, y compris la dernière ligne « Quel numéro voulez-vous traiter, ou quelle série (--serie A,B) ? » qui s'affiche à chaque fois : deux listes numérotées, « Opportunité » puis « Très probable », issues de la colonne **Question** de `rag_data/expressions/EXPRESSIONS.md` (seules les lignes dont « Contenu consistant probable ? » vaut l'une de ces deux valeurs ; doublons retirés ; questions dont le slug est très proche d'un dossier déjà présent dans `rag_data/content/work/` écartées, seuil 0,9). Puis demande le numéro choisi.

Quand l'utilisateur répond par un numéro N, lance :

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/create-content/scripts/lib/choix_expression.py" N
```

La ligne affichée est l'expression : poursuis le skill normalement, avec cette question comme `<expression>` (entre guillemets). `EXPRESSIONS.md` n'est **jamais** modifié par ce skill.

### Mode série : `/create-content --serie A,B`

`--serie A,B` n'est accepté **qu'après** l'affichage des deux listes numérotées par `/create-content` sans argument, **dans cette même conversation**. Si les listes n'ont pas été affichées avant, ne lance aucune étape : dis à l'utilisateur de lancer d'abord `/create-content` pour afficher les listes, puis s'arrête.

Si l'utilisateur tape `--serie A,B` après l'affichage des listes (A et B deux numéros de la liste, A ≤ B), ne lance aucune étape. Les questions de A à B inclus sont traitées dans l'ordre. Lance (timeout 600000) :

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/create-content/scripts/lib/choix_expression.py" --serie A,B
```

où `A,B` est la valeur tapée par l'utilisateur. Relaie la sortie brute : la liste des questions à traiter, avec leur numéro de la liste complète. Cette liste est figée : elle ne se recalcule pas en cours de série.

Puis traite **chaque question de la liste, dans l'ordre, une à une**, comme si elle avait été lancée seule en argument :

- Pour chaque question, déroule **intégralement** les étapes 0 à 8 dans l'ordre, avec la même règle de foreground (`timeout: 600000`, jamais deux commandes en parallèle). Relaie la sortie brute de **chaque** étape, comme pour une commande seule.
- Écris le **rapport final** de cet article (section « Rapport final à l'utilisateur ») avant de passer à la question suivante. Annonce le début de chaque article par une ligne : `## Article k/K — <question>` (k = numéro de la liste complète).
- Si une étape affiche `ÉCHEC` (étapes 0 à 7 ; un échec de l'étape 8 n'arrête pas), arrête **la série** sur cet article, relaie la ligne d'échec, et n'entame pas les suivants. Les articles déjà terminés restent en place.
- Un article terminé fait partie des voisins du suivant : les étapes 2a à 2c le prennent en compte automatiquement, donc chaque article évite de redire les précédents.
- Pour reprendre une série interrompue, relancer `--serie <numéro de l'article à reprendre>,B` avec le même B : les étapes déjà faites sont sautées (reprise habituelle).
- À la fin de la série, donne un bilan en une ligne par article (numéro, slug, verdict) ; le détail reste dans les rapports déjà affichés.

Les sous-agents restent interdits : chaque article se déroule dans ce même tour.

Ce skill n'écrit **jamais** dans les bases (`rag_data/db/`), ni dans
`rag_data/work/` (corpus), ni dans le code du pipeline. Ses sorties sont
`rag_data/content/work/<slug>/` (tous les slugs) et la ligne du contenu dans
`rag_data/content/INDEX.md`, mise à jour à chaque nouvel article (étape 7). Ne supprime jamais rien sous `rag_data/` et ne modifie **jamais** un autre contenu de
`rag_data/content/work/` : les articles existants sont en lecture seule.

## Règles d'or (non négociables)

1. **Le dossier est la seule source de faits.** Définitions, propriétés,
   hypothèses, chiffres, noms, exemples : tout vient du dossier. Tu peux
   apporter des *analogies, transitions, questions d'accroche, mises en
   forme* (ce qui ne prétend pas être un fait du domaine) — jamais une
   affirmation technique absente du dossier. Ce que le dossier ne couvre
   pas est signalé à l'utilisateur dans le rapport final, **jamais** comblé
   de mémoire et jamais mentionné dans l'article.
2. **Code : verbatim.** Un bloc de code de l'article reprend à l'identique
   celui d'un chunk (lignes élidées par `...` tolérées). Tu peux le
   *commenter autour*, pas le réécrire. `verify.py` le contrôle.
   **Une ligne élidée ne doit jamais être une ligne qui définit une variable
   réutilisée plus loin** dans le même bloc ou un bloc suivant — si la
   garder allonge trop le bloc, raccourcis le commentaire autour, jamais la
   définition. `verify.py` avertit (heuristique, pas infaillible) quand une
   variable semble utilisée sans définition visible.
3. **Formules : LaTeX du dossier**, puis expliquées en français clair
   (chaque symbole nommé, sens intuitif de l'ensemble). Une formule que tu
   dérives toi-même (ex. simplification) est signalée par un avertissement.
4. **Images : uniquement celles du dossier**, avec la légende que *toi* tu
   écris à partir de leur description (`[DESCRIPTION DU PIPELINE]`) — la
   description est une aide de lecture, pas du texte à recopier. Chemins
   `images/<fichier>` tels que fournis par `gather.py`. Tu peux regarder
   une image avec l'outil Read si sa description ne suffit pas à l'utiliser
   correctement.
5. **Aucune fuite de la mécanique interne** dans l'article : jamais
   « chunk », « dossier », « RAG », ni identifiant `[Sxxx]` visible. La
   traçabilité passe par des commentaires HTML `<!--src:S012-->` (invisibles,
   stripables au moment de publier).
   **Aucune référence aux pdf sources**, sous aucune forme : ni « dans ce
   pdf », « le pdf X explique que… », « selon l'auteur », « les auteurs »,
   « cet ouvrage », « ce chapitre », « chapitre 3 », « page 42 », « figure 3.2 »,
   « listing 8.9 », ni même les mots pdf / ouvrage / manuel / auteur /
   chapitre. Le lecteur ne sait pas qu'il existe des pdf : c'est *toi* qui
   parles, les faits sont dits à la première personne du savoir, sans
   attribution (« la sigmoïde écrase tout nombre dans [0, 1] », pas « le pdf
   montre que la sigmoïde… »). `verify.py` bloque ces formulations.
   **Aucune référence aux autres contenus du site** (voir règle 7).
6. **Plusieurs pdf = un seul discours.** Fusionne les apports (un pdf
   donne l'intuition, un autre la preuve, un autre le code). Si deux pdf
   notent différemment la même chose (β vs w, σ vs sigmoïde…), choisis UNE
   notation, tiens-la tout du long, et mentionne l'autre une fois
   (« on la trouve aussi notée… »). Si deux pdf se contredisent, ne
   tranche pas : ne garde que ce qui est cohérent et signale-le à
   l'utilisateur dans le rapport.
7. **Zéro duplication avec les contenus existants.** Aucune phrase, aucun
   argument, aucun exemple, aucune analogie, aucun enchaînement déjà présent
   dans un article voisin ne se redit — même reformulé. Si un voisin a déjà
   expliqué quelque chose dont tu as besoin pour être compris, tu ne le
   réexpliques pas, **et tu ne renvoies pas non plus à l'article voisin dans le
   texte** : jamais son titre, jamais « dans notre article sur… », « l'article
   cité plus haut », « voir aussi… ». Nomme la notion en une proposition si
   le fil l'exige (« la sigmoïde, cette courbe en S… »), reformule-la sous un
   angle neuf, ou coupe le passage. Les liens vers les voisins se décident à
   la publication : note-les dans `plan.md` et dans le champ `related` du
   frontmatter (slug ou titre du voisin), jamais dans le corps de l'article.
   `verify.py` bloque le titre d'un autre contenu et les formules « notre
   article / l'article cité ». **Hors périmètre de cette règle** :
   les images, les formules et les blocs de code, qui peuvent servir un
   argument différent d'un article à l'autre (le texte qui les entoure, lui,
   doit être neuf). Un tableau est du texte : il ne se reprend pas à
   l'identique.
8. **Rien n'est bloquant côté voisins.** S'il n'y a aucun voisin (aucun
   contenu existant proche du sujet, ou aucun contenu du tout), tu pars d'une
   page blanche : c'est un cas normal, pas une erreur. De même, la détection
   de doublons de `verify.py` n'avertit que, elle ne fait jamais échouer.
9. **Les facettes viennent de l'intention, pas des voisins ni de ta mémoire.**
   Le catalogue de l'étape 1 est contraignant et se remplit *avant* de lire un
   voisin. Les voisins servent à ne pas redire (lien posé à la publication, pas dans le texte), jamais à choisir les sujets, et jamais à dériver vers d'autres méthodes ou notions pour combler ce qu'ils ont déjà couvert.
10. **Une lacune se vérifie.** « Non remonté par mes requêtes » n'est pas
    « absent du corpus » : seule la recherche directe de `check_gap.py` autorise
    la seconde formule dans le rapport final.

## Interpréteur Python

Tout script de ce skill se lance **uniquement** avec
`"<racine_projet>/.venv-rag/Scripts/python.exe"`, jamais avec `python`/`py`
(Python système). Techniquement imposé par un hook `PreToolUse` sur `Bash` et
`PowerShell` (`.claude/hooks/block_system_python.py`, voir
`.claude/settings.json`), qui refuse toute commande lançant un script de
`.claude/skills/*/scripts/` ou `tools/` avec `python`/`py`. Voir `/ragpipeline`,
section « Interpréteur Python ». `<racine_projet>` = le dossier contenant
`.claude/`.

## Exécution

Toujours en foreground, bloquant jusqu'à complétion — jamais via
`run_in_background` ni aucun mécanisme async, et jamais deux commandes du
pipeline en parallèle. **Chaque commande d'étape est lancée avec
`timeout: 600000`** (600 s, le maximum en foreground) : le délai par défaut de
120 s fait basculer la commande en arrière-plan, ce qui est interdit. Techniquement imposé par un hook `PreToolUse` sur `Bash`
(`.claude/hooks/block_rag_background.py`, voir `.claude/settings.json`), qui
refuse tout `run_in_background: true` sur une commande référençant un script
d'un skill du projet. Voir `/ragpipeline`, section « Pourquoi le foreground ».

**Interdiction de déléguer à un sous-agent** (outil `Agent`) la lecture ou la
vérification des sorties de ce skill : elle se fait directement, dans le même
tour de conversation, jamais « pour économiser du contexte ».

`<racine_projet>` = le dossier contenant `.claude/`. **Relaie intégralement la
sortie brute** de chaque script à l'utilisateur, sans la résumer.

Les étapes s'enchaînent dans l'ordre, chacune par un script de `scripts/`. Le texte de chaque étape est dans `scripts/prompts/`. Chaque appel au modèle est journalisé dans `rag_data/content/work/<slug>/usage.jsonl`.

Chaque script affiche une ligne `[étape N] OK` ou `[étape N] ÉCHEC — …`. Relaie cette ligne à l'utilisateur. Si elle commence par `ÉCHEC`, arrête là : ne lance pas l'étape suivante.

**Reprise.** Les étapes 0, 1, 2a, 2b, 2c, 3 et 4 vérifient d'abord si leur résultat existe déjà dans `rag_data/content/work/<slug>/` (section de `plan.md`, `voisins.json`, `dossier.md`/`dossier.json`, `article.md`). Si oui, elles n'appellent pas le modèle et affichent `[étape N] DÉJÀ FAIT — …` (code de sortie 0) : relaie la ligne et passe à l'étape suivante. Pour refaire une étape, ajoute `--force` à la commande (à la fin) ; ses résultats en aval ne sont pas invalidés automatiquement, relance alors aussi les étapes suivantes avec `--force`. L'étape 5 n'a pas de reprise : elle relance `toc.py` et `verify.py` à chaque lancement, et n'appelle le modèle que si `verify.py` signale des erreurs. Les étapes 7 et 8 ne sont pas concernées : elles sont sans appel modèle et idempotentes.

### Étape 0 — Cadrer l'intention (appel claude)

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/create-content/scripts/etape0_cadrage.py" "<expression>"
```

Le script calcule le slug, crée `rag_data/content/work/<slug>/` si besoin, et écrit la section « Cadrage » de `plan.md` : intention, question du lecteur, promesse, hors périmètre.

### Étape 1 — Facettes déduites de l'intention (appel claude)

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/create-content/scripts/etape1_facettes.py" "<expression>"
```

Le script écrit la section « Facettes » de `plan.md` : le catalogue de l'intention, puis un tableau complet, une ligne par facette. Chaque sous-requête est au format `étiquette::mots-clés`. La colonne « Voisin » reste vide à ce stade.

### Étape 2a — Repérer et consigner les voisins (appel claude)

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/create-content/scripts/etape2a_voisins.py" "<expression>"
```

Le script lance `scan_existing.py` (qui écrit `voisins.json`), puis consigne chaque voisin, lu en entier, dans la section « Voisins » de `plan.md`. Sans voisin, il écrit « Aucun voisin ».

### Étape 2b — Croiser chaque facette avec les voisins (appel claude)

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/create-content/scripts/etape2b_croisement.py" "<expression>"
```

Le script remplit les colonnes « Voisin qui la couvre » et « Décision » de la section « Facettes » de `plan.md`, à partir des voisins lus à l'étape 2a.

### Étape 2c — Collecte du dossier (appel claude)

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/create-content/scripts/etape2c_collecte.py" "<expression>"
```

Le script lance `gather.py` avec les sous-requêtes de la section « Facettes », vérifie les lacunes avec `check_gap.py`, et fait au plus une relance. Il écrit `dossier.md` et `dossier.json`, et ne modifie pas `plan.md`.

**Code de sortie 3** (`corpus sans rien sur ce sujet`) : arrête, et dis-le franchement. N'écris aucun article de mémoire.

### Étape 3 — Lire le dossier en entier, puis arrêter le plan (appel claude)

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/create-content/scripts/etape3_plan.py" "<expression>"
```

Le script lit `dossier.md` en entier et écrit la section « Plan » de `plan.md` : un tableau, une ligne par H2, avec la facette, la question du lecteur, les chunks `Sxxx` et les images, formules ou blocs de code prévus.

### Étape 4 — Rédiger `rag_data/content/work/<slug>/article.md`(appel claude)

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/create-content/scripts/etape4_redaction.py" "<expression>"
```

Le script rédige `article.md` (frontmatter SEO, puis corps) à partir de `plan.md` et de `dossier.md`. Il ne rédige pas le sommaire : il est généré à l'étape 5.

### Étape 5 — Sommaire puis vérification (boucle jusqu'à OK) (appel claude)

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/create-content/scripts/etape5_verification.py" "<expression>"
```

Le script lance `toc.py` (sommaire) puis `verify.py`. Si `verify.py` signale des erreurs, il fait corriger l'article par le modèle, puis relance, jusqu'à 10 tours au maximum.

- `[étape 5] OK` : le contenu est conforme. Le rapport final indique le nombre d'avertissements restants, et justifie chacun en une ligne.
- `[étape 5] ÉCHEC` : le contenu n'est pas terminé. Relaie la ligne et arrête. Ne déclare pas le contenu terminé.

### Étape 6 — Image de couverture (image mise en avant WordPress) (aucun appel claude)

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/create-content/scripts/etape6_couverture.py" "<expression>"
```

Le script écrit `rag_data/content/work/<slug>/<slug>.png` (1200×630), sans appel au modèle. `/publish-content` l'utilise comme image mise en avant.

### Étape 7 — Mise à jour de l'index (`rag_data/content/INDEX.md`) (aucun appel claude)

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/create-content/scripts/etape7_indexfileupdate.py" "<expression>"
```

Le script ajoute ou met à jour la ligne du contenu dans `rag_data/content/INDEX.md` (date, slug, titre, niveau, durée de lecture, article publié) ; une ligne existante pour le même slug est remplacée, jamais dupliquée. Aucun appel au modèle. La colonne « Article publié » vaut « Non » à la création ; elle passe à « Oui » quand `/publish-content` a écrit sa trace `rag_data/content/published/<slug>.json`.

## Rapport final à l'utilisateur

Bref, factuel, en français :

- chemin de l'article (`rag_data/content/work/<slug>/article.md`) et du plan
  (`plan.md`) ;
- titre, description SEO, durée de lecture, niveau ;
- **Intention retenue** et angle ;
- **Voisins** : lesquels (ou « aucun voisin, page blanche »), ce qu'ils couvrent,
  ce que cet article fait de différent, liens internes suggérés pour la publication (`related`), doublons restants et
  pourquoi on les garde ;
- **Plan** : les H2, et en quoi il diffère de ceux des voisins ;
- **Couverture** : pdf utilisés, nombre de formules/images/blocs de
  code repris sur ceux disponibles ;
- **Diversité des sources** : `book_coverage` (pdf pertinents sur le
  total), taux de matière distinctive citée (donné par `verify.py`) ;
- **Facettes** : le tableau de `plan.md` en une ligne par facette du
  catalogue (traitée / couverte par un voisin / hors périmètre + raison) ;
- **Lacunes du corpus** : ce que le sujet appelait et qui n'a pas été trouvé,
  en séparant **« absent du corpus »** (confirmé par `check_gap.py`, sortie
  relayée) de **« non remonté par mes requêtes »** (non vérifié) ;
  contradictions ou différences de notation entre pdf ;
- **Décisions éditoriales** notables (fil conducteur choisi, éléments écartés,
  scission ou non en articles satellites) ;
- **Verdict commodity / non-commodity** (ton jugement, appuyé sur les deux
  chiffres ci-dessus — jamais un verdict binaire tranché par le script) ;
- résultat de `verify.py` (relayé tel quel plus haut, ici seulement le verdict).

Ne colle pas l'article dans la réponse : l'utilisateur l'ouvre dans
`article.md`.

### Étape 8 — Totaux de tokens (`totaltoken.jsonl`) (aucun appel claude)

À lancer **après** le « Rapport final à l'utilisateur ».

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/create-content/scripts/etape8_totaltokens.py" "<expression>"
```

Le script n'appelle pas le modèle. Il lit `rag_data/content/work/<slug>/usage.jsonl` (seules les lignes `source: "api"` comptent) et écrit `rag_data/content/work/<slug>/totaltoken.jsonl`, réécrit à chaque lancement : **une seule ligne**, avec `article`, `appels_api` (nombre total d'appels) et le total de chacun des 4 types de tokens : `input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`. Rien d'autre (pas de coût, pas de détail par étape). Le script affiche « Totaux des N appels API : » suivi d'un tableau markdown à 2 colonnes (Type de tokens / Quantité ; lignes `input`, `cache_creation`, `cache_read`, `output`). **Recopie ce tableau dans le texte de ta réponse, en markdown brut et jamais dans un bloc de code**, pour que Claude Desktop l'affiche rendu comme un vrai tableau. Ne le reformate pas, n'ajoute aucune colonne. Relaie ensuite la ligne `[étape 8] OK` ou `[étape 8] ÉCHEC — …`. Un échec de l'étape 8 n'invalide pas l'article.

## Suite : publication

`/publish-content <slug>` consomme `article.md`, `images/` et `<slug>.png`. Les commentaires
`<!--src:...-->` sont retirés à ce moment-là, et chaque H2 reçoit un `id`
identique à l'ancre du sommaire (même fonction, `scripts/lib/toc.py`) : le sommaire
est cliquable sur le site.
