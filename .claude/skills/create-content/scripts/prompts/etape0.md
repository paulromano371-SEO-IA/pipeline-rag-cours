### Étape 0 — Cadrer l'intention

Avant toute recherche, détermine ce que l'expression *demande vraiment* et
écris-le dans `rag_data/content/work/<slug>/plan.md` (section « Cadrage ») :

- **Intention** — une famille parmi : *définition* (« qu'est-ce que… »),
  *décision* (« quand utiliser… », « faut-il… »), *procédure* (« comment
  utiliser / faire / installer… »), *comparaison* (« différence entre… »,
  « X ou Y »), *causalité* (« pourquoi… »), *optimisation / diagnostic*
  (« comment améliorer… », « pourquoi ça échoue »), *panorama* (« quels
  sont… »). Une expression peut mêler deux familles : retiens la dominante.
- **La question du lecteur**, dans ses mots, et **la promesse** de l'article :
  ce qu'il saura faire ou décider à la fin.
- **Hors périmètre** : ce que cet article ne traitera volontairement pas
  (parce que ce n'est pas la question posée, ou parce qu'un voisin le fait).

Le slug est celui que `gather.py` calcule (`slugify_expression`). Crée le
dossier `rag_data/content/work/<slug>/` s'il n'existe pas.
