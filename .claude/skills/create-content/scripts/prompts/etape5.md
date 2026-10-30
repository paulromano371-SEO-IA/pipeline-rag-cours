### Étape 5 — Sommaire puis vérification (obligatoire, boucle jusqu'à OK)

Le script écrit d'abord le sommaire (`toc.py`) : il insère juste avant
`## L'essentiel en 30 secondes` la liste de liens d'ancre vers tous les H2
(à relancer après tout changement de titre ou de plan). Puis il lance
`verify.py`.

Contrôle mécanique : code fidèle, images valides, citations valides,
tableau des facettes de `plan.md` complet pour le catalogue de l'intention
(avertissements : facette absente, sans requête ni retrait justifié, requête
non étiquetée),
absence de fuites (« chunk », marqueurs internes), **absence de référence aux
livres sources ou à d'autres articles** (bloquant), structure/frontmatter,
**blocs fixes** (H2 « L'essentiel en 30 secondes » présent, sommaire juste
avant et fidèle aux H2), voix (tutoiement imposé, bloquant), puis
avertissements de couverture, de lisibilité, de gras (section sans gras,
densité, gras trop longs ou trop rapprochés), de variable de code
potentiellement non définie, de légende d'image, de matière distinctive, et
de **duplication avec les autres contenus** : passages de ≥ 10 mots identiques,
phrases quasi identiques, titres H2 identiques (reportés pour les voisins).
Les doublons sont des **avertissements, jamais un blocage** : ils te montrent ce
qui a fuit malgré tout. Pour chacun, réécris le passage avec un autre
argument, ou supprime-le (ou laisse la notion au voisin, sans le citer), puis
relance. Sont exclus de ce contrôle : images, formules, blocs de code, sommaire.
Code `2` : corrige **l'article** (jamais le script, jamais le dossier) et
relance ; ne déclare pas le contenu terminé tant que le résultat n'est pas
`OK`. Pour chaque avertissement restant, soit tu corriges, soit tu le justifies
en une ligne dans le rapport.
