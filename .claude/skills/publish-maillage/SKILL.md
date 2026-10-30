---
name: publish-maillage
description: >-
  Applique sur WordPress le maillage interne proposé par /maillage : insère
  dans l'article source déjà en ligne les liens relatifs
  (<a href="/blog/maths/..." title="ancre">ancre</a>) sur les ancres du rapport
  rag_data/maillage/work/<slug>/<slug>.json. Dry-run obligatoire d'abord, sauvegarde du
  contenu d'origine avant écriture, statut du post inchangé. Usage :
  /publish-maillage <slug>, /publish-maillage <numéro> ou /publish-maillage --all.
---

# /publish-maillage — Application du maillage sur WordPress

`/publish-maillage <slug|numéro|--all>` prend le rapport produit par `/maillage`
(`rag_data/maillage/work/<slug>/<slug>.json`) et insère ses liens dans l'article **en ligne**
sur WordPress (API REST). `<slug>` est le nom d'un dossier de `rag_data/content/` ;
un numéro désigne le n-ième dossier dans l'ordre alphabétique (même numérotation
que `/maillage`) ; `--all` traite tous les articles **publiés sur WordPress** qui ont un rapport **et pas encore de liens posés** (ceux déjà publiés sont ignorés). Un slug ou un numéro désignant un article **non publié** sur WordPress est refusé : rien n'est fait. Sans
argument (ou slug inconnu), lance le script avec `--list` : il affiche les articles
**publiés sur WordPress** (`rag_data/content/published/<slug>.json` avec `"wp_status": "publish"`)
dont le maillage est généré (`work/<slug>/<slug>.md` et `.json` présents) mais **pas encore
posé** (ni `maillage/published/<slug>.json` avec un lien posé) : numéro de `/maillage`,
slug, nombre de liens. **Recopie cette liste intégralement dans ta
réponse**, **sans liste Markdown numérotée** (le rendu la renumérote 1, 2, 3… et masque les vrais numéros de `/maillage`) : une ligne par article au format `n°6 : slug`, puis demande lequel traiter plutôt que de deviner.

`<racine_projet>` = le dossier qui contient `.claude/` (remonte depuis ce
SKILL.md jusqu'au premier parent qui le contient ; ne suppose aucun chemin fixe).

Prérequis : un rapport `/maillage` pour l'article, et le `.env` de
`/publish-content` (`.claude/skills/publish-content/scripts/.env` : `WP_URL`,
`WP_USER`, `WP_APP_PASSWORD`).

## Ce que fait le script

`scripts/publish_maillage.py` :

1. Lit le rapport JSON (ancre, URL cible, phrase de contexte de chaque lien) et
   retrouve le post WordPress par son slug (dernier segment de l'URL du rapport).
2. Pour chaque lien, cherche l'ancre dans le **texte visible** du post, sans tenir
   compte de la casse ni des accents, en mots entiers ; le texte d'origine de la
   page est conservé. Parmi plusieurs occurrences, il retient celle dont le
   paragraphe ressemble le plus à la phrase de contexte du rapport.
3. Insère `<a href="<chemin relatif>" title="<texte de l'ancre>">…</a>`. Le href
   est **relatif** : le domaine est retiré (`/blog/maths/le-coefficient-de-determination/`).
4. **Ne devine jamais** : le lien est ignoré et signalé quand l'ancre est
   introuvable, quand le paragraphe ne correspond pas au contexte, ou quand le post pointe **déjà** vers cette page (un seul
   lien par URL cible ; le script est donc relançable sans doublon).
   L'ancre est **toujours aplatie** (rendu identique pour tous les liens) : les
   balises qu'elle contient sont retirées, avec leurs jumelles hors de l'ancre
   (ex. `mémoire <strong>paramétrique</strong>`), ainsi qu'une mise en forme
   `<strong>/<em>/<b>/<i>` qui n'enveloppe que l'ancre (`<em>retriever</em>`).
   Signalé « balises de l'ancre retirees » au dry-run. Une mise en forme qui
   entoure plus que l'ancre (`<strong>La recherche hybride</strong>`) est conservée.
   Après écriture de la trace, régénère `rag_data/maillage/INDEX.md` : la colonne
   « Liens publiés » de l'article passe à Oui (`etape3_links.py --index`).
5. N'insère **jamais** dans : un lien existant, un titre (h1–h6), un bloc de
   code, une légende d'image, une formule LaTeX, un commentaire ou un attribut
   HTML.
6. Sans `--dry-run` : sauvegarde le contenu d'origine dans
   `rag_data/maillage/backup/<slug-wp>-<date>.html`, met à jour le contenu du
   post (le **statut n'est pas modifié**), relit le contenu enregistré pour
   vérifier que chaque lien y est, et écrit la trace dans
   `rag_data/maillage/published/<slug>.json`.

Pour annuler : recoller le contenu du fichier de sauvegarde dans l'éditeur du
post (mode HTML), ou demander de le restaurer via l'API.

## Interpréteur Python

Tout script de ce skill se lance **uniquement** avec
`"<racine_projet>/.venv-rag/Scripts/python.exe"`, jamais avec `python`/`py`
(Python système). Techniquement imposé par un hook `PreToolUse` sur `Bash` et
`PowerShell` (`.claude/hooks/block_system_python.py`, voir
`.claude/settings.json`). Voir `/ragpipeline`, section « Interpréteur Python ».

## Exécution

Toujours en foreground, bloquant jusqu'à complétion — jamais via
`run_in_background` ni aucun mécanisme async, et jamais deux commandes du
pipeline en parallèle (hook `.claude/hooks/block_rag_background.py`). Voir
`/ragpipeline`, section « Pourquoi le foreground ».

**Interdiction de déléguer à un sous-agent** (outil `Agent`) la lecture ou la
vérification des sorties de ce skill : elle se fait directement, dans le même
tour de conversation.

Commence **toujours** par `--dry-run` : il lit le post en ligne (lecture seule),
affiche pour chaque lien le HTML inséré avec son contexte avant/après, les liens
ignorés et pourquoi, puis le bilan. N'écrit rien.

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/publish-maillage/scripts/publish_maillage.py" "<slug|numéro|--all>" --dry-run
```

**Recopie intégralement la sortie brute du script dans le texte de ta réponse**
(pas seulement dans le bloc Bash replié), puis demande confirmation à
l'utilisateur. Le post en ligne étant modifié directement, ne lance jamais la
commande sans `--dry-run` avant son feu vert. Une fois confirmé :

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/publish-maillage/scripts/publish_maillage.py" "<slug|numéro|--all>"
```

Relaie là encore la sortie complète (codes HTTP, chemin de la sauvegarde, bilan).
En cas d'erreur HTTP, le script affiche le corps de la réponse WordPress et
s'arrête : ne relance pas aveuglément, diagnostique avec l'utilisateur.

## Cas `--all` : un article à la fois

Quand l'utilisateur demande `--all`, ne lance **jamais** le script sur tout le lot
d'un coup. Procède article par article, dans l'ordre de la liste :

1. Établis la liste des articles à traiter avec `--list` (ou relève les
   articles qui ont un rapport et pas encore de liens posés). Recopie-la dans ta réponse.
2. Pour le **premier** article de la liste, lance le dry-run **sur ce seul slug** :
   ```bash
   "<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/publish-maillage/scripts/publish_maillage.py" "<slug>" --dry-run
   ```
   Recopie la sortie brute dans ta réponse, puis demande confirmation pour **cet article seulement**.
3. Si l'utilisateur dit non, passe à l'article suivant sans rien écrire (ou arrête si l'utilisateur le demande).
4. Si l'utilisateur confirme, lance la pose **sur ce même slug** (sans `--dry-run`),
   relaie la sortie complète, puis passe à l'article suivant : retour à l'étape 2.
5. Ne traite jamais deux articles en parallèle, et ne passe pas à l'article suivant
   sans confirmation explicite pour chaque article.
6. En cas d'erreur HTTP sur un article, arrête la boucle et diagnostique avec l'utilisateur
   avant de continuer.
