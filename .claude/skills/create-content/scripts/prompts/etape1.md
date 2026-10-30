### Étape 1 — Facettes déduites de l'intention (avant de regarder les voisins)

Une seule requête ne remonte jamais tout. À partir de l'**intention retenue à
l'étape 0** — et d'elle seule, **sans avoir lu aucun voisin** — dresse la liste
des facettes à traiter. Le catalogue ci-dessous est **contraignant** : toutes
les facettes de ta famille d'intention figurent dans le tableau de `plan.md`.

| Intention | Facettes du catalogue (une ligne chacune dans `plan.md`) |
|---|---|
| Définition | nature / ce que c'est · à quoi ça sert · composants et fonctionnement · place dans son écosystème · exemple concret · idées reçues |
| Décision (« quand ») | critères de décision · signaux et seuils chiffrés · contre-indications · alternatives · coût / bénéfice · cas limites |
| Procédure (« comment ») | prérequis · étapes dans l'ordre · commande ou code · paramètres clés · vérifier le résultat · erreurs fréquentes |
| Comparaison | axes de comparaison · forces et faiblesses de chaque option · cas d'usage de chacune · critère de choix |
| Causalité (« pourquoi ») | mécanisme · cause racine · preuves et chiffres · conséquences · exceptions |
| Optimisation / diagnostic | symptôme et mesure · cause probable · levier d'action · effet attendu · effets de bord |
| Panorama | critère de classement · chaque élément (un par sous-requête) · comment choisir |

Dans `plan.md`, section **« Facettes »**, écris le tableau complet :
*facette du catalogue → sous-requête prévue → voisin qui la couvre (rempli à
l'étape 2) → décision*. Règles :

- **Aucune facette du catalogue ne disparaît en silence.** Elle reçoit soit une
  sous-requête, soit un retrait écrit (« couverte par <slug du voisin> » si un
  voisin la couvre complètement, ou « hors périmètre » avec la raison).
  `verify.py` avertit si une ligne manque ou n'a ni requête ni retrait.
- **Tu peux ajouter** une facette que le sujet appelle spécifiquement, mais
  elle s'ajoute au catalogue, elle ne le remplace jamais.
- **Chaque sous-requête s'écrit avec le vocabulaire de l'intention** (« à quoi
  sert », « composants », « idée reçue », « seuils », « étapes »…) appliqué
  au sujet. Elle ne vient pas de ce que *toi* tu sais du domaine : tu ne
  connais pas le contenu du corpus ; tu l'interroges avec les facettes, pas avec
  la liste des notions que tu t'attends à y trouver.
- **Pas de dérive vers les sujets voisins.** Chaque H2 final porte une facette
  de l'intention *appliquée au sujet lui-même*. Une autre méthode ou notion n'a
  sa place que comme contraste court au service d'une facette (« place dans son
  écosystème », « alternatives »), jamais comme section à part entière pour
  remplacer une facette que les voisins ont déjà prise : dans ce cas on la
  laisse au voisin.
