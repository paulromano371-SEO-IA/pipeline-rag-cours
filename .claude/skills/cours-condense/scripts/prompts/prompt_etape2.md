## Étape 2 — Génération des illustrations

Pour chaque chapitre de `plan_cours.json`, écris et exécute un script Python (sauvegardé dans `<nom_du_livre>/scripts/illustrations_chapitreN.py`) 
qui génère et enregistre dans `<nom_du_livre>/illustrations/` les illustrations (schémas de concepts, courbes mathématiques réelles, fonctions de coût, architectures, etc.) 
nécessaires pour soutenir visuellement les explications et analogies du livre.

Consignes impératives :

0. Génère **exactement une illustration par concept clé** listé dans `concepts_cles_visuels` pour ce chapitre — ni plus, ni moins.

1. Ne copie ni ne reproduit jamais une figure existante du livre : recrée chaque schéma sous forme vectorielle originale, dans l'esprit pédagogique de l'auteur.

2. Charte couleur stricte : bleu profond `#1a365d` pour les traces principaux, orange chaud `#c25e00` pour l'accentuation ou les courbes secondaires.

3. Lisibilité : légendes claires (`plt.legend()`), titres et noms d'axes en français.

4. Propreté du code : `matplotlib.use('Agg')`, `plt.close()` après sauvegarde en `dpi=300`, format PNG.

5. Anti-chevauchement et dimensionnement — utilise `.claude/skills/cours-condense/scripts/illustration_utils.py` (`suggested_figsize`, `verify_no_overlap`) : 
   positionne tous les éléments, calcule un figsize de départ avec `suggested_figsize`, dessine, vérifie avec `verify_no_overlap`, et si un chevauchement est détecté, 
   augmente figsize et/ou repositionne, puis revérifie — ne sauvegarde le PNG qu'une fois la vérification passée. Marge minimale entre deux éléments distincts : 0.03 
   en coordonnées de figure (déjà appliquée par `verify_no_overlap`).

6. Simplicité : privilégie des schémas simples et immédiatement lisibles (boîtes, flèches, peu d'éléments par illustration) plutôt que des compositions denses ou trop chargées. L'illustration sert la compréhension immédiate d'un seul concept, pas la démonstration graphique — en cas de doute entre une version riche et une version épurée, choisis l'épurée.

Nomme chaque fichier de manière prévisible, ex. `illustrations/ch03_architecture_transformer.png`, pour pouvoir le référencer facilement à l'étape 3.

**Vérification obligatoire et mécanique, une fois toutes les illustrations du livre générées :**

```bash
   "<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/cours-condense/scripts/verifier_illustrations.py" "<nom_du_livre>" "<nom_du_livre>/plan_cours.json"
```

Ce script ne corrige rien : il vérifie, par chapitre, que le nombre d'images générées correspond exactement au nombre de `concepts_cles_visuels` (règle 0), que chaque fichier respecte la convention de nommage, que le script `illustrations_chapitreN.py` correspondant respecte la propreté de code exigée (règle 4 — ou délègue correctement à `DiagramBuilder.save()`, qui l'assure déjà), et qu'aucune couleur codée en dur n'échappe à la charte stricte `#1a365d`/`#c25e00` (règle 2). Corrige les anomalies signalées et ré-exécute jusqu'à `"pret": true` .

**Résumé** : liste les illustrations générées (chemin + chapitre associé).

---