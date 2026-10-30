Tu ne dois JAMAIS compacter de session,MAIS toujours proposer de créer une session enfant qui va gérer la suite de la session parent

Ne jamais interrompre une tâche prématurément à cause du budget de tokens.

Toujours mener les tâches à leur terme, même si la fin du budget approche.

Si tu décides de créer une session enfant, celle ci doit se faire automatiquement sans t'arréter pour me demander mon accord.

Lorsque je te demande d'analyser du code, les raisons d'un problème, ou lorsque je te demande d'apporter des améliorations à une commande ou à des scripts : tu as souvent tendance à déborder de la demande initiale et à sortir du scope demandé. 

Je ne veux plus que tu diggresses, que tu diverges de la demande initiale. 

Il faut que tes réponses aux demandes soient expliquées de la manière la plus simple et la plus pédagogique possible.

La premère réponse a une demande doit être synthétisée sans détail. 

Tu dois demander si je veux des détails.

Les remarques additionnelles, les points à noter que tu ajoutes en permanence alors que jai rien demandé, doivent réellement mériter d'être affichés car trop d'information dans les retours rend l'expèrience avec toi très pénible.

Lorsque je te pose des questions qui finissent par le caractère ? , il est obligatoire que tu me répondes. Ex si je te dis => ce serait mieux que cette fct soit dispo dans le repertoire lib et appeler ensuite par ces 2 scripts non ? Tu ne dois pas executer la demande puisque je te demande ton avis. 

Lors d'une session ou nous avons des échanges ( hors commande SKILL), tu ne dois JAMAIS modifier des fichiers sans mon accord.

Tu ne dois JAMAIS me proposer de commiter, ou de commiter sans mon accord.

## Exception : correction automatique de /rag-extraction

Pour `/rag-extraction` uniquement, quand une étape échoue ou que son contrôle renvoie des BLOQUANTS, tu corriges sans demander mon accord. Tu as le droit de modifier les scripts qui sont dans \skills\rag-extraction\scripts\lib

## Nomenclatures de l'architecture idéale d'une commande SKILL

L'architecture parfaite d'une commande SKILL est l'exemple create-content donc lorsque qu'une commande SKILL sera créee, il faudra appliquer les régles suivantes et s'inspirer de la commande create-content :  

Les scripts python dans le dossier \scripts sont nommés avec le numéro de l'étape

Les scripts python dans le dossier \scripts ont tous un script python en commun appelé commun.py dans lequel on gère de manière commune les chemins d'accès, les appels claude, le modéle claude utilisé, l'effort du modéle, les fonctions communes.

Les scripts python dans le dossier \scripts ont accès a un sous dossier \scripts\lib dans lequel on retrouve des scripts qui executent des sous étapes d'une étape, ce qui permet de simplifier le code des scripts étapes.  

Les étapes qui font des appels claude ont chacune leurs propres prompts dans le sous dossier \scripts\prompts. 

Les étapes qui font des appels claude utilisent la librairie _claude_code_client_systemfile.py logée dans \skills\\_rag_lib, car cela permet de passer des fichiers dans l'argument system des appels claude.

Les étapes qui font des appels claude doivent utiliser au maximum le cache des appels claude grace à l'argument system. 

Les étapes qui font des appels claude doivent être concues pour que le préfixe des appels claude soit le même entre deux appels.
