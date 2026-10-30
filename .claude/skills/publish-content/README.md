# /publish-content — Publication d'un article vers WordPress

Ce document est la version lisible par un humain de `SKILL.md`, la consigne lue par Claude Code. Il est regénéré à partir de ce fichier.

## Ce que fait la commande

`/publish-content <slug>` envoie sur WordPress, via l'API REST, un article déjà produit par `/create-content` (dossier `rag_data/content/work/<slug>/`). `<slug>` est le nom de ce dossier, identique au `slug` du frontmatter de `article.md`.

Si la commande est lancée sans slug, ou avec un slug qui ne correspond à aucun dossier, le script liste uniquement les articles non encore publiés, sous forme de liste numérotée. Il n'y a pas de devinette : on choisit l'article dans la liste.

```bash
".venv-rag/Scripts/python.exe" ".claude/skills/publish-content/scripts/publish_to_wordpress.py" --list
```

`<racine_projet>` désigne le dossier qui contient `.claude/`, et non `.claude/skills/publish-content/`. Il se localise en remontant depuis l'emplacement de `SKILL.md` jusqu'au premier dossier parent contenant `.claude/`. Aucun chemin fixe n'est utilisé, pour que le projet fonctionne à n'importe quel emplacement.

## Déroulement interne

Toute la logique se trouve dans `scripts/publish_to_wordpress.py`.

1. Lecture de `article.md` (frontmatter et corps) et de `dossier.json`.
2. Traitement des images référencées dans l'article.
3. Conversion du reste de l'article en HTML (blocs image Gutenberg), avec suppression des commentaires de traçabilité `<!--src:...-->`.
4. Création du post WordPress en brouillon.

## Champs renseignés pour chaque image

| Champ WordPress | Source |
|---|---|
| **Texte alternatif** | Le texte alt écrit dans l'article, déjà pensé pour l'accessibilité. |
| **Légende** | La phrase qui suit l'image dans l'article (« quoi regarder dedans »). Elle est retirée du corps pour ne pas être dupliquée. |
| **Description** | La description technique détaillée du pipeline RAG (`dossier.json`). |

Aucun de ces trois champs ne mentionne la source (pdf, section). Elle n'est jamais publiée.

### Image déjà présente dans la médiathèque

Avant tout upload, le script cherche si l'image existe déjà dans la médiathèque WordPress, par nom de fichier : `<nom>.png`, `<nom>-1.png` (suffixe ajouté par WordPress en cas de doublon) et `<nom>-scaled.png`. Le nom seul suffit : aucun téléchargement ni comparaison de contenu.

- Nom trouvé : pas de re-upload. Le média existant est réutilisé (son ID et son URL vont dans le bloc image). Ses métadonnées WordPress existantes ne sont pas écrasées, et les champs de l'article restent propres au bloc de ce post.
- Nom absent : l'image est uploadée avec ses trois champs remplis.

Piège : une image modifiée en local sous le même nom n'est pas ré-uploadée. Dans ce cas, utiliser `--no-check-existing`.

## Image mise en avant

L'image mise en avant est `<slug>.png`, générée par `/create-content` (étape 6). Elle est uploadée avec ses trois champs toujours remplis, jamais de légende vide :

- Texte alternatif : titre de l'article.
- Légende : titre de l'article.
- Description : description SEO du frontmatter, à défaut le titre.

Cette image n'est jamais une image du corps : le thème l'affiche au-dessus du contenu, ce qui dupliquerait celle-ci. Si `<slug>.png` est absent, l'article est créé sans image mise en avant, avec un avertissement.

## Formules LaTeX

Les formules (`$...$` et `$$...$$`) sont protégées du rendu Markdown, pour que les antislashs et les underscores ne soient pas altérés. Le shortcode `[latexpage]` est ajouté en tête du post si l'article contient au moins une formule. C'est ce qu'attend le plugin WordPress QuickLaTeX, installé sur le site, pour les rendre.

Le script distingue les vraies formules des montants en euros ou en dollars à la française (« 1 000 $ ») grâce à une règle simple : un `$` de formule n'a jamais d'espace collé à l'intérieur (`$X$`, jamais `$ X $` ni `X $`).

## Prérequis

Un fichier `.env` doit se trouver **dans ce même dossier** : `.claude/skills/publish-content/scripts/`, à côté de `publish_to_wordpress.py` et de `.env.example`. C'est une copie de `.env.example`, jamais commise. Il contient :

| Variable | Contenu |
|---|---|
| `WP_URL` | Adresse du site WordPress. |
| `WP_USER` | Identifiant WordPress. |
| `WP_APP_PASSWORD` | Mot de passe d'application WordPress, créé dans Utilisateurs > Profil > Mots de passe d'application. Ce n'est pas le mot de passe de connexion. |

Le script cherche ce fichier à partir de son propre emplacement (`ENV_PATH`, calculé dynamiquement). S'il manque, le script s'arrête en expliquant quoi renseigner.

## Interpréteur Python

Tous les scripts de ce skill se lancent **uniquement** avec `"<racine_projet>/.venv-rag/Scripts/python.exe"`, jamais avec `python` ni `py` (Python système). Un hook `PreToolUse` sur `Bash` et `PowerShell` (`.claude/hooks/block_system_python.py`, voir `.claude/settings.json`) refuse toute autre commande. Voir `/ragpipeline`, section « Interpréteur Python ».

## Exécution

L'exécution se fait toujours en foreground, de façon bloquante jusqu'à la fin. Il n'y a jamais de `run_in_background` ni de mécanisme asynchrone, et jamais deux commandes du pipeline en parallèle. Un hook `PreToolUse` sur `Bash` (`.claude/hooks/block_rag_background.py`, voir `.claude/settings.json`) refuse tout `run_in_background: true` sur une commande qui référence un script d'un skill du projet. Voir `/ragpipeline`, section « Pourquoi le foreground ».

Il est interdit de déléguer à un sous-agent (outil `Agent`) la lecture ou la vérification des sorties de ce skill. Cette vérification se fait directement, dans le même tour de conversation.

### 1. Prévisualiser (obligatoire)

```bash
".venv-rag/Scripts/python.exe" ".claude/skills/publish-content/scripts/publish_to_wordpress.py" "<slug>" --dry-run
```

Le dry-run affiche, pour chaque image, le texte alternatif, la légende et la description qui seraient envoyés, ainsi qu'un résumé du post. Il n'écrit rien sur le site. La détection des images déjà présentes reste active en lecture seule : il indique quelles images seraient réutilisées et lesquelles seraient uploadées, avec le décompte final. Relire ce résumé avant de continuer. `--no-check-existing` désactive la détection et force l'upload de toutes les images.

### 2. Publier en brouillon

Une fois le dry-run confirmé :

```bash
".venv-rag/Scripts/python.exe" ".claude/skills/publish-content/scripts/publish_to_wordpress.py" "<slug>"
```

Le script crée toujours l'article en **brouillon**. Aucune option ne le publie en ligne directement, par choix délibéré : la mise en ligne se fait à la main, dans WordPress.

### Options

| Option | Effet |
|---|---|
| `--list` | Liste les articles non publiés, puis s'arrête. |
| `--dry-run` | N'écrit rien sur WordPress. Les images déjà présentes sont tout de même détectées, en lecture seule. |
| `--no-check-existing` | Ne cherche pas les images déjà présentes dans la médiathèque. Toutes sont uploadées. |

### Sortie et erreurs

Toute la sortie brute du script est relayée telle quelle (codes HTTP, IDs des médias, lien du post créé). En cas d'erreur HTTP (code autre que 200 ou 201), le script affiche le corps de la réponse WordPress et s'arrête. Ne pas relancer aveuglément : diagnostiquer avec l'utilisateur (identifiants, permissions, plugin manquant).

## Fichiers écrits

### Trace de publication : `rag_data/content/published/<slug>.json`

Écrite après chaque création réussie (hors dry-run). Elle contient six champs :

| Champ | Contenu |
|---|---|
| `folder` | Nom du dossier. |
| `title` | Titre de l'article. |
| `wp_post_id` | Identifiant du post WordPress. |
| `wp_status` | Statut WordPress, toujours `draft`. |
| `url` | Lien du brouillon. |
| `date` | Date de création du post WordPress, au format `AAAAMMJJ-HHMMSS`. |

Le fichier est écrasé si l'article est republié. C'est aussi lui qui sert à `--list` pour savoir si un article est déjà publié.

### Index : `rag_data/content/INDEX.md`

La colonne « Article publié » de la ligne du slug passe à « Oui ».

## Dossier `scripts/`

| Fichier | Rôle |
|---|---|
| `publish_to_wordpress.py` | Tout le script : lecture de l'article, upload des médias, conversion en HTML, création du brouillon, traces. |
| `.env.example` | Modèle du fichier `.env`, à copier. |
| `.env` | Identifiants WordPress. Local, jamais commis. |
| `__pycache__/` | Cache Python généré. Ne rien y modifier. |
