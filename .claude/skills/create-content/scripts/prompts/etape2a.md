#### 2a. Repérer les contenus voisins

Le script parcourt `rag_data/content/work/`, mesure la proximité de chaque
article avec l'expression (chunks cités en commun avec ce que le RAG remonte
pour elle, sections communes, similarité sémantique) et écrit
`rag_data/content/work/<slug>/voisins.json`. Un article sur un autre sujet (RAG
quand tu traites d'inférence) est ignoré : inutile de le lire.

- **`AUCUN VOISIN`** : tu n'as rien à éviter. Passe à 2c avec le tableau tel
  quel ; note « aucun voisin » dans `plan.md`.
- **Des voisins** : lis **chacun en entier** (`article_path`) et consigne dans
  `plan.md` (section « Voisins ») : son intention et son angle, son plan
  (H2), la liste de ses **arguments** (une ligne chacun : l'idée, l'exemple,
  l'analogie, le chiffre), ses tournures d'accroche et de conclusion. C'est
  ta liste d'interdits : tout ce qui y figure ne se redit pas.

Seuils : `--threshold-chunks`, `--threshold-sections`, `--threshold-semantic`,
`--max-neighbors` (les chiffres affichés servent à les recaler si un article
évident est manqué ou un article hors sujet est retenu). Si, à la lecture, un
article ignoré par le script te paraît tout de même proche, lis-le aussi.
