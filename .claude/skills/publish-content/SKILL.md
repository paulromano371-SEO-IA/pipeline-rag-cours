---
name: publish-content
description: >-
  Publie sur WordPress un article généré par /create-content
  (rag_data/content/<slug>/article.md + images/) : uploade chaque image avec
  son Texte alternatif, sa Légende et sa Description renseignés à partir du
  pipeline RAG, puis crée l'article en brouillon. Usage : /publish-content
  <slug> (ex. /publish-content
  comment-passer-en-python-de-la-programmation-par-fonction-a).
---

# /publish-content — Publication d'un article vers WordPress

`/publish-content <slug>` envoie sur WordPress, via l'API REST, un article
déjà produit par `/create-content` (dossier `rag_data/content/<slug>/`).
`<slug>` est le nom de ce dossier (identique au `slug` du frontmatter de
`article.md`). Si l'utilisateur invoque la commande sans `<slug>` (ou avec un
slug qui ne correspond à aucun dossier), liste le contenu de
`rag_data/content/` sous forme de liste **numérotée** (`1.`, `2.`, ...,
jamais à puces) et demande-lui lequel publier plutôt que de deviner.

`<racine_projet>` (utilisé partout ci-dessous) = le dossier qui contient
`.claude/` — **pas** `.claude/skills/publish-content/`. Localise-le en
remontant depuis l'emplacement de ce fichier SKILL.md jusqu'au premier
dossier parent qui contient `.claude/` ; ne suppose jamais un chemin fixe,
ce projet doit s'installer à n'importe quel endroit sur la machine de
n'importe quel utilisateur.

## Ce que fait la commande

Toute la logique est dans `scripts/publish_to_wordpress.py` de cette skill :

1. Lit `article.md` (frontmatter + corps) et `dossier.json` du dossier.
2. Pour chaque image référencée dans l'article :
   - **Texte alternatif** = le texte alt écrit dans l'article (déjà pensé
     pour l'accessibilité).
   - **Légende** (affichée sous l'image sur le site) = la phrase qui suit
     l'image dans l'article (« quoi regarder dedans ») — retirée du corps de
     l'article pour ne pas être dupliquée.
   - **Description** = la description technique détaillée du pipeline RAG
     (`dossier.json`). **Aucun champ (alt, légende, description) ne mentionne
     la source** (livre, section) : elle n'est jamais publiée.
   - **Vérifie d'abord si l'image est déjà dans la médiathèque WP** : recherche
     par nom de fichier (`<nom>.png`, `<nom>-1.png` ajouté par WP en cas de
     doublon, `<nom>-scaled.png`). Le nom seul suffit : aucun téléchargement
     ni comparaison de contenu. Nom trouvé → **pas de re-upload** : le média
     existant est réutilisé (son ID et son URL vont dans le bloc image ; ses
     métadonnées WP existantes ne sont pas écrasées, l'alt/légende de
     l'article restent propres au bloc de ce post). Une image modifiée en
     local sous le même nom n'est donc pas ré-uploadée : utiliser
     `--no-check-existing` dans ce cas.
   - Sinon, uploade l'image sur WordPress avec ces trois champs déjà remplis.
3. Convertit le reste de l'article en HTML (blocs image Gutenberg pointant
   vers les images uploadées), retire les commentaires de traçabilité
   `<!--src:...-->`.
4. Crée le post WordPress **en brouillon** (titre, contenu, extrait =
   description SEO du frontmatter, slug). Pas d'image mise en avant :
   la plupart des thèmes WordPress l'affichent au-dessus du contenu, ce
   qui dupliquerait visuellement la première image de l'article, déjà
   à sa place dans le corps du texte.

**Formules LaTeX** : préservées telles quelles ($...$ et $$...$$, protégées
du rendu Markdown pour que backslashes et underscores ne soient pas
altérés), avec le shortcode `[latexpage]` ajouté en tête du post si
l'article en contient un peu — c'est ce qu'attend le plugin WP QuickLaTeX
(installé sur le site) pour les rendre. Le script distingue les vraies
formules des montants en euros/dollars à la française (« 1 000 $ ») grâce à
une règle simple de ce corpus : un `$` de formule n'a jamais d'espace collé
à l'intérieur (`$X$`, jamais `$ X $` ni `X $`).

## Prérequis

Fichier `.env` dans **ce même dossier** (`.claude/skills/publish-content/scripts/`,
à côté de `publish_to_wordpress.py` et de `.env.example`) — copie de
`.env.example`, jamais commis — avec `WP_URL`, `WP_USER`, `WP_APP_PASSWORD`
(mot de passe d'application WordPress, créé dans Utilisateurs > Profil >
Mots de passe d'application — pas le mot de passe de connexion). Le script
le cherche à cet emplacement précis (`ENV_PATH` = le dossier du script
lui-même, calculé dynamiquement, jamais en dur) — peu importe où le projet
est installé. S'il manque, le script s'arrête en expliquant quoi renseigner.

## Exécution

Toujours en foreground.

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/publish-content/scripts/publish_to_wordpress.py" "<slug>" --dry-run
```

Commence **toujours** par `--dry-run` : affiche pour chaque image le texte
alternatif/légende/description qui seraient envoyés, et un résumé du post,
sans rien écrire sur le site (la détection des images déjà présentes reste
active en lecture seule : le dry-run indique quelles images seraient
réutilisées et lesquelles seraient uploadées, avec le décompte final).
Relis-le avec l'utilisateur avant d'enchaîner. `--no-check-existing`
désactive la détection et force l'upload de toutes les images.

Une fois confirmé par l'utilisateur, relance sans `--dry-run` :

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/publish-content/scripts/publish_to_wordpress.py" "<slug>"
```

Le script crée toujours l'article en **brouillon** — il n'existe aucune
option pour le publier en ligne directement, par choix délibéré de
l'utilisateur : la mise en ligne se fait à la main, dans WordPress.

**Relaie intégralement la sortie brute du script** (codes HTTP, IDs media,
lien du post créé). En cas d'erreur HTTP (code hors 200/201), le script
affiche le corps de la réponse WordPress et s'arrête : ne relance pas
aveuglément, diagnostique avec l'utilisateur (identifiants, permissions,
plugin manquant...).
