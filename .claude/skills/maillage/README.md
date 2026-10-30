# /maillage — Propositions de maillage interne

`/maillage` propose, pour chaque article de `rag_data/content/work/`, les liens internes possibles vers les autres articles. Le `SKILL.md` à côté de ce fichier est la consigne lue par Claude Code ; ce README en est la documentation pour un humain.

## 1. Ce que fait la commande

Pour un article source, la commande cherche dans son texte des **ancres** (un passage qui peut servir de lien, par exemple « évaluer un RAG ») et propose, pour chacune, la page cible, une **preuve** (une citation exacte de la cible montrant que son sujet central est celui de l'ancre), la section et la phrase de contexte.

Le résultat est un rapport lisible (`.md`) et un fichier exploitable par un script (`.json`), dans `rag_data/maillage/work/<slug>/`.

La commande **ne modifie aucun article** et **ne touche pas à WordPress**. Appliquer les liens est une étape distincte, faite par `/publish-maillage`.

## 2. Utilisation

| Commande | Effet |
|---|---|
| `/maillage` | Liste numérotée des articles publiés sans rapport de maillage. |
| `/maillage <slug>` | Traite un article. |
| `/maillage <numéro>` | Traite l'article à ce numéro (ordre alphabétique de tous les dossiers de `work/`). |
| `/maillage --all` | Traite tous les articles publiés sans rapport. |

Un article **non publié** n'est jamais traité par `--all` ni listé sans argument. Le filtre « publié » lit `rag_data/content/published/<slug>.json` et exige `"wp_status": "publish"`.

Avant tout calcul, la commande affiche les **paires** candidates de la source (étape 2), sans appel au modèle. Si ces paires changent après un recalcul, elle les réaffiche. Elle enchaîne ensuite sur le calcul des liens.

## 3. Les étapes

Les trois scripts s'enchaînent dans cet ordre. Chacun est **incrémental** : relancé sans rien de nouveau, il répond « déjà à jour ».

### Étape 1 : `etape1_build_index.py` (index et graphe de concepts)

Pour chaque article de `work/` :

1. **Découpage** en chunks, en retirant le frontmatter et les marqueurs `<!--src:...-->`.
2. **Index vectoriel** dans ChromaDB (collection `articles`).
3. **Extraction des concepts** par chunk, avec le modèle via `claude -p`. Résultat mis en cache par hash du texte.
4. **Graphe de concepts** dans Kuzu. La résolution des entités reprend celle de `/rag-graphe`, avec deux écarts assumés : une fusion automatique à partir d'une similarité de 0,92, même entre articles (tous traitent d'IA et de ML) ; et les arbitrages « même concept ou non » regroupés par **lots de 20 paires** par appel (accord de 98 % avec le jugement paire par paire, mesuré par `--calibrate N`).

Un contrôle d'intégrité du graphe est fait à la fin.

Options : `--status` (avancement, ne modifie rien), `--only <dossiers>`, `--calibrate N`, `--batch-size N`.

### Étape 2 : `etape2_pairs.py` (choix des cibles)

Pour chaque source, on retient les cibles qui traitent **en profondeur** les concepts que la source mentionne. Une notion citée une fois ne suffit pas.

- Un concept est **central** dans une cible s'il figure dans au moins 3 de ses chunks, ou dans au moins 2 s'il est dans son titre ou dans un H2.
- Le score est pondéré par la rareté du concept (idf).
- Les doublons non fusionnés sont rattrapés par similarité d'embedding.

Résultat dans `rag_data/maillage/pairs/`, dont `centralite.json` qui montre ce que le graphe pense de chaque article.

Options : `--top N` (20 cibles par source par défaut), `--min-score X`, `--only <sources>`.

### Étape 3 : `etape3_links.py` (propositions de liens)

C'est elle qui produit les liens. Pour chaque paire retenue :

1. **Proposition** : un appel `claude -p` lit la source et la cible **en entier** et propose au plus 2 ancres, chacune accompagnée d'une citation exacte de la cible.
2. **Vérification technique** : le code contrôle que l'ancre et la citation existent mot pour mot dans les textes.
3. **Critique** : un second appel, indépendant, vérifie que l'ancre **nomme le sujet** de la page cible, et non une composante, un outil ou un chapitre.
4. **Arbitrage** : entre pages sœurs, le code tranche selon l'adéquation, puis selon le score de la paire.
5. **Quotas** : un seul lien par URL cible ; 2 liens maximum par paragraphe ; jamais deux fois la même ancre.

Règles de forme :

- **jamais d'ancre dans un titre** (`#` à `######`) ni dans le **Sommaire** (règle SEO) ;
- les blocs **FAQ** ne sont jamais exclus ;
- « À retenir » et « L'essentiel en 30 secondes » sont du texte comme un autre ;
- l'ancre est un groupe nominal ou un infinitif avec son complément, qui nomme le sujet (« évaluer un RAG »).

**Zéro lien est une réponse normale** : le rappel est volontairement modeste, la précision visée est haute.

Une cible n'est proposée que si elle a une **URL WordPress** ; les cibles non publiées sont retirées avant tout appel au modèle (fonction `cibles_publiees()`).

Options : `<slug> | <numéro> | --all`, `--show-pairs` (affiche les paires sans appel au modèle), `--max-targets N`, `--offline` (n'appelle pas l'API WordPress : aucune paire n'est alors proposée, mode de test seulement), `--index` (régénère `INDEX.md` seul).

## 4. Codes de sortie

| Code | Signification |
|---|---|
| 0 | Terminé. |
| 3 | **Travail partiel, pas une erreur.** Le script s'arrête avant la limite de 10 minutes et sauvegarde sa progression. Relancer **exactement la même commande** tant que le code vaut 3. |
| 2 | Bloquant : lire la sortie. |

Le code 3 concerne `etape1_build_index.py` et `etape3_links.py`.

## 5. Lancer les scripts à la main

Il faut toujours utiliser l'interpréteur du projet, jamais `python` ni `py` (un hook les refuse). Depuis la racine du projet :

```bash
".venv-rag/Scripts/python.exe" ".claude/skills/maillage/scripts/etape1_build_index.py"
".venv-rag/Scripts/python.exe" ".claude/skills/maillage/scripts/etape2_pairs.py"
".venv-rag/Scripts/python.exe" ".claude/skills/maillage/scripts/etape3_links.py" <slug|numéro>
".venv-rag/Scripts/python.exe" ".claude/skills/maillage/scripts/etape3_links.py" --all
```

Les scripts tournent en **premier plan**, sans `run_in_background` : un hook refuse les lancements en arrière-plan, et deux commandes du pipeline ne tournent jamais en parallèle.

Après modification ou ajout d'un article, relancer `etape1_build_index.py` : seuls les articles modifiés sont recalculés.

## 6. Fichiers de sortie

Dans `rag_data/maillage/` :

| Fichier | Contenu |
|---|---|
| `work/<slug>/<slug>.md` | **Rapport lisible** : liens proposés (ancre, adéquation, raison, citation, section, contexte), puis cibles lues sans lien et pourquoi. |
| `work/<slug>/<slug>.json` | Mêmes liens pour un script : `target`, `target_url`, `anchor`, `section`, `paragraph_index`, `context`, `adequation`, `preuve_cible`, `reason`, plus `cibles_examinees` (propositions rejetées, pour auditer la grille). |
| `work/<slug>/usage.jsonl` | Une ligne par appel `claude -p` lié à cet article : réussi ou non, coût, tokens, cache. |
| `work/<slug>/totaltoken.jsonl` | Une seule ligne, réécrite à chaque appel : nombre d'appels et totaux de tokens pour cet article. |
| `usage.jsonl` et `totaltoken.jsonl` (racine) | Idem pour les appels partagés entre articles (arbitrages du graphe). |
| `INDEX.md` | Tableau de tous les articles, une ligne par dossier `work/<slug>/`. |
| `pairs/` | Paires candidates par source, et `centralite.json`. |
| `db/`, `work/` | Bases et caches. **Ne rien supprimer à la main.** |

Pour le coût total d'un article, additionner `total_cost_usd` des lignes `"source": "api"` de son `usage.jsonl`.

### Le fichier `INDEX.md`

Il est écrit par `write_index()` de `etape3_links.py`, à trois moments : à la fin de chaque rapport de `/maillage` (donc après chaque article d'un `--all`), avec `etape3_links.py --index`, et après une publication `/publish-maillage`.

| Colonne | Règle |
|---|---|
| Fichiers d'indexation | Oui si `work/<slug>/chunks.json` et `state.json` existent. |
| Fichiers de maillage | Oui si `work/<slug>/<slug>.md` et `<slug>.json` existent. |
| Liens publiés | Oui si `published/<slug>.json` contient au moins un lien au statut `pose`. Écrit par `/publish-maillage`. |

Ces trois colonnes sont des contrôles de présence sur disque. « Non » sur « Liens publiés » signifie seulement qu'aucun lien n'est encore posé sur WordPress. Un article peut donc afficher « Oui, Oui, Non » : analysé, pas encore publié.

## 7. Ce que la commande n'est pas

- Elle **n'écrit rien** dans `rag_data/content/work/` : les articles restent intacts.
- Elle **ne publie rien** sur WordPress ; l'API ne sert qu'à lire les URL.
- Elle **ne partage aucun fichier** avec le RAG des pdf : ses bases sont dans `rag_data/maillage/db/`. Les bibliothèques de `_rag_lib` et `entity_resolution.py` de `/rag-graphe` sont seulement lues.

Pour appliquer les liens : `/publish-maillage`.
