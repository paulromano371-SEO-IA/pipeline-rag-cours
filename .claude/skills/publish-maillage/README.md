# /publish-maillage : application du maillage interne sur WordPress

Ce document explique à quoi sert la commande `/publish-maillage`, comment l'utiliser et ce qu'elle modifie. Le fichier `SKILL.md`, à côté de celui-ci, est la consigne lue par Claude Code. Ce README est la documentation pour un humain. En cas de désaccord, le code fait foi.

---

## 1. À quoi ça sert

`/publish-maillage` prend les liens proposés par `/maillage` et les insère dans l'article **déjà en ligne** sur WordPress, via l'API REST.

Chaque lien inséré a cette forme :

```html
<a href="/blog/maths/le-coefficient-de-determination/" title="ancre">ancre</a>
```

Le `href` est **relatif** : le domaine est retiré.

**Le statut du post ne change pas.** Un article publié reste publié, un brouillon reste brouillon.

---

## 2. Prérequis

- Un rapport `/maillage` pour l'article : `rag_data/maillage/work/<slug>/<slug>.json`.
- Le fichier `.env` de `/publish-content`, dans `.claude/skills/publish-content/scripts/`, avec `WP_URL`, `WP_USER` et `WP_APP_PASSWORD`.
- Un article **publié** sur WordPress. Un article non publié est refusé, et rien n'est fait.

L'interpréteur à utiliser est `.venv-rag/Scripts/python.exe`, à la racine du projet. Jamais `python` ni `py`.

---

## 3. Utilisation

Le paramètre `<slug|numéro|--all>` désigne l'article. Le `<slug>` est le nom d'un dossier de `rag_data/content/`. Le `<numéro>` désigne le n-ième dossier, dans l'ordre alphabétique, avec la même numérotation que `/maillage`. `--all` traite tous les articles publiés qui ont un rapport et pas encore de liens posés. Les articles déjà traités sont ignorés. Un slug ou un numéro qui désigne un article **non publié** est refusé, et rien n'est fait.

### Lister les articles prêts

Sans argument, ou avec un slug inconnu, le script lance `--list`. Il affiche les articles publiés sur WordPress dont le maillage est généré et **pas encore posé**. Pour chacun, il donne le numéro de `/maillage`, le slug et le nombre de liens.

```bash
".venv-rag/Scripts/python.exe" ".claude/skills/publish-maillage/scripts/publish_maillage.py" --list
```

### Prévisualiser (obligatoire)

Le dry-run lit le post en ligne, en lecture seule, et n'écrit rien. Il affiche pour chaque lien le HTML qui serait inséré, avec son contexte avant et après. Il affiche aussi les liens ignorés et le motif de chaque ignorance, puis le bilan.

```bash
".venv-rag/Scripts/python.exe" ".claude/skills/publish-maillage/scripts/publish_maillage.py" "<slug|numéro|--all>" --dry-run
```

Relire ce résultat avant de confirmer.

### Poser les liens

Une fois le dry-run validé :

```bash
".venv-rag/Scripts/python.exe" ".claude/skills/publish-maillage/scripts/publish_maillage.py" "<slug|numéro|--all>"
```

### Traiter plusieurs articles avec `--all`

`--all` ne doit pas être lancé d'un seul coup. La procédure recommandée est article par article :

1. lister avec `--list` ;
2. lancer le dry-run sur le **premier** slug ;
3. obtenir la confirmation pour **cet article** ;
4. poser les liens sur ce même slug ;
5. passer au suivant, et recommencer.

Une erreur HTTP arrête la boucle.

### Options

| Option | Effet |
|---|---|
| `--list` | Liste les articles prêts, puis s'arrête. |
| `--dry-run` | Lit le post, affiche ce qui serait inséré, n'écrit rien. |
| `--all` | Traite tous les articles prêts. |

---

## 4. Ce que fait le script

1. **Lecture du rapport.** Pour chaque lien, le script lit l'ancre, l'URL cible et la phrase de contexte. Le post WordPress est retrouvé par son slug, c'est-à-dire le dernier segment de l'URL du rapport.

2. **Recherche de l'ancre** dans le **texte visible** du post. La recherche ne tient pas compte de la casse ni des accents, et porte sur des mots entiers. Le texte d'origine de la page est conservé. Si l'ancre apparaît plusieurs fois, le script retient l'occurrence dont le paragraphe ressemble le plus à la phrase de contexte du rapport.

3. **Insertion du lien.** Quand l'ancre contient des balises, par exemple `mémoire <strong>paramétrique</strong>`, les balises sont retirées de l'ancre. Ainsi, tous les liens ont le même rendu. Une mise en forme qui entoure plus que l'ancre, comme `<strong>La recherche hybride</strong>`, est conservée. Une mise en forme qui n'enveloppe que l'ancre, comme `<em>retriever</em>`, est aussi retirée.

4. **Ce qui est ignoré.** Le script ne devine jamais. Il ignore le lien, et l'indique, dans trois cas : l'ancre est introuvable ; le paragraphe ne correspond pas au contexte ; le post pointe **déjà** vers cette page. Un seul lien est posé par URL cible. Le script peut donc être relancé sans créer de doublon.

5. **Emplacements interdits.** Le script n'insère jamais dans : un lien existant, un titre (`h1` à `h6`), un bloc de code, une légende d'image, une formule LaTeX, un commentaire ou un attribut HTML.

6. **Écriture** (sans `--dry-run`). Le script :
   - sauvegarde le contenu d'origine dans `rag_data/maillage/backup/<slug-wp>-<date>.html` ;
   - met à jour le contenu du post, **sans toucher au statut** ;
   - relit le contenu enregistré pour vérifier que chaque lien y est ;
   - écrit la trace dans `rag_data/maillage/published/<slug>.json` ;
   - régénère `rag_data/maillage/INDEX.md` avec `etape3_links.py --index`, pour que la colonne « Liens publiés » passe à Oui.

---

## 5. Fichiers écrits

| Fichier | Contenu |
|---|---|
| `rag_data/maillage/backup/<slug-wp>-<date>.html` | Contenu d'origine du post, avant la pose. |
| `rag_data/maillage/published/<slug>.json` | Trace de la pose : liens, statut (posé, ou ignoré avec son motif), sauvegarde associée. |
| `rag_data/maillage/INDEX.md` | Colonne « Liens publiés » régénérée. |

Le post WordPress lui-même est modifié. C'est la seule modification externe.

---

## 6. Annuler

Deux solutions :

- coller le contenu du fichier de sauvegarde dans l'éditeur du post, en mode HTML ;
- demander la restauration via l'API.

---

## 7. Erreurs

- Une erreur HTTP affiche le corps de la réponse WordPress, puis le script s'arrête. Il ne faut pas le relancer aveuglément : il faut diagnostiquer avec l'utilisateur.
- Un fichier `.env` absent ou incomplet arrête le script.
- Un rapport absent arrête le traitement de l'article.

---

## 8. Ce que la commande n'est pas

- Elle **ne change pas le statut** du post.
- Elle **n'insère jamais** à un emplacement interdit (voir la section 4, point 5).
- Elle **ne rédige rien** : les ancres viennent du rapport de `/maillage`.
- Elle **ne supprime rien**, ni sur WordPress, ni sous `rag_data/`.
- Elle **ne publie pas** d'article : c'est le rôle de `/publish-content`.

---

## 9. Dossier `scripts/`

| Fichier | Rôle |
|---|---|
| `publish_maillage.py` | Tout le script : lecture du rapport, recherche des ancres, insertion, sauvegarde, vérification, traces. |
| `__pycache__/` | Cache Python généré. Ne rien y modifier. |
