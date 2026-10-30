---
name: cours-condense
description: >-
  Génère un support de cours condensé en français (PDF via LaTeX) à partir d'un pdf
  technique complet en PDF, avec illustrations vectorielles originales et code source
  préservé à l'identique.
  Usage: /cours-condense <chemin_vers_livre.pdf>
  Déclenche aussi sur "condense ce pdf en cours", "crée un support de cours à partir de ce PDF".
---

# cours-condense — Cours condensé à partir d'un pdf

Pipeline en étapes numérotées. Chaque étape est un script `scripts/EtapeX.py`
lancé par cette fiche, dans l'ordre, sans étape implicite. Les étapes qui appellent
Claude (`claude -p`) ont leur prompt dans `scripts/prompts/`. Le socle commun est `scripts/lib/commun.py` ; les outils partagés sont dans `scripts/lib/`.

**Règle d'or de reprise de la version originale : ne jamais fixer de longueur cible a priori.** 
Le plan, la rédaction et la compression découlent uniquement du contenu réellement essentiel du pdf — jamais d'un ratio de pages ou d'un nombre de chapitres décidé d'avance.

## Règle absolue : accentuation française correcte

Tout texte français produit par ce skill — titres, prose des chapitres, légendes de
figures, résumés de compte-rendu, contenu des tableaux doit comporter les accents et diacritiques corrects (é, è, ê, à, â, ç, ô, î, ï, û...).

**Exception : jamais le caractère ligature « œ »/« Œ »** — on écrit toujours « oe » séparés (noeud, coeur, oeuvre, soeur), car la police du PDF l'encode comme une ligature que `/rag-extraction` relit en caractère erroné (« n÷ud »).

Le fichier LaTeX généré déclare déjà `\usepackage[utf8]{inputenc}`, `\usepackage[T1]{fontenc}` et `\usepackage[french]{babel}` : rien ne justifie techniquement d'écrire sans accent, et un compilateur qui échoue sur un accent est un bug à corriger dans le préambule, jamais une raison de l'omettre.

**Important** : le registre ou l'orthographe du présent fichier SKILL.md, à quelque moment que ce soit de son histoire, n'a **aucune valeur d'exemple** pour le contenu à produire. Ce fichier est une configuration technique interne destinée à être suivie, jamais un modèle stylistique, donc n'imite jamais son registre, sa concision ou son orthographe dans le cours généré.

## Arguments

`/cours-condense <chemin_vers_livre.pdf>`

- **Lecture intégrale, toujours.** Que le pdf fasse 50 ou 1000 pages, tous les chapitres du plan sont lus et traités en entier, sans exception. Ne demande **jamais** à l'utilisateur s'il veut lire le pdf en entier ou s'arrêter à certains chapitres, cette question ne se pose pas, la réponse est toujours oui. C'est une instruction permanente de ce skill, pas une option à reconfirmer à chaque lancement.

### Sans argument : choix d'un pdf à condenser ( aucun appel `claude -p` )

`/cours-condense` lancé **sans argument** ne démarre aucune étape. Il exécute uniquement :

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/cours-condense/scripts/Etape0_lister_non_condenses.py"
```

Le script liste, numérotés, les noms des livres de `corpusdedepart/` absents de `corpuscondense/` (tri par nom de fichier, sans chemin). Présente cette liste simplement dans la réponse du chat, en numérotée, sans le format de compte-rendu de ce skill, puis attends la réponse de l'utilisateur.

- Si l'utilisateur répond par un numéro : relance le même script (sans argument) pour retrouver la liste à jour, prends le nom de ce numéro, et lance le pipeline complet sur `corpusdedepart/<nom>`, exactement comme si son chemin avait été donné en argument (Étape 0 puis suite).
- Si le numéro n'existe pas dans la liste : signale-le et redemande, sans rien lancer.
- Si `NB_NON_CONDENSES: 0` : indique qu'il n'y a plus aucun pdf à condenser, et ne lance rien.

## Interpréteur Python

Tout script de ce skill se lance **uniquement** avec
`"<racine_projet>/.venv-rag/Scripts/python.exe"`, jamais avec `python`/`py`
(Python système). Techniquement imposé par un hook `PreToolUse` sur `Bash` et
`PowerShell` (`.claude/hooks/block_system_python.py`, voir
`.claude/settings.json`), qui refuse toute commande lançant un script de
`.claude/skills/*/scripts/` ou `tools/` avec `python`/`py`.

## Exécution

Toujours en foreground, bloquant jusqu'à complétion — jamais via
`run_in_background` ni aucun mécanisme async, et jamais deux commandes du
pipeline en parallèle. Techniquement imposé par un hook `PreToolUse` sur `Bash`
(`.claude/hooks/block_rag_background.py`, voir `.claude/settings.json`), qui
refuse tout `run_in_background: true` sur une commande référençant un script
d'un skill du projet. 

**Interdiction de déléguer à un sous-agent** (outil `Agent`) la lecture ou la
vérification des sorties de ce skill : elle se fait directement, dans le même
tour de conversation, jamais « pour économiser du contexte ».

## Règle d'exécution (déterminisme)

Ce pipeline doit se dérouler **de façon identique à chaque lancement**, quel 
que soit le pdf traité. Sont interdits, à toutes les étapes, sauf demande 
explicite et ponctuelle de l'utilisateur dans le message qui déclenche la commande :

- **Déléguer à un sous-agent** (outil `Agent`) la lecture des pages extraites, 
  l'analyse du contenu ou la rédaction du cours. C'est toi, dans ce même tour de 
  conversation, qui lis directement chaque lot de pages (`Read`) et qui rédiges 
  — jamais un sous-agent "pour économiser du contexte". Si le volume est important, 
  découpe en lots de lecture plus petits, mais reste le seul lecteur/rédacteur.
- **Toute tâche en arrière-plan ou planifiée** (`ScheduleWakeup`, agents async, etc.) pour une étape du pipeline lui-même.

Chaque étape s'exécute avec le même type d'appels d'un pdf à l'autre : 
`Bash` pour lancer les scripts utilitaires, 
`Read` pour lire `structure.json`/les pages/`plan_cours.json`, 
`Write` le script d'écriture en mode append pour produire `course.tex`. 

— n'improvise jamais silencieusement un changement de stratégie d'exécution.

## Arborescence de travail

Pour un pdf `chemin/vers/Mon Pdf.pdf`, `initialiser_arborescence.py` calcule un slug (minuscules, sans accents, espaces -> `_`, ex. `mon_livre`) et crée le dossier de travail `rag_data/courscondense/<slug>/` à la racine du projet (jamais directement à la racine du projet — cette commande doit pouvoir s'executer seule, sans dépendre d'un orchestrateur, tout en gardant les données générées centralisées).

**`<nom_du_livre>` dans tout ce fichier désigne ce dossier de travail complet (`rag_data/courscondense/<slug>/`), jamais le slug seul.** C'est la valeur `DOSSIER:` affichée par `initialiser_arborescence.py`, pas sa valeur `SLUG:`  — toutes les commandes ci-dessous qui utilisent `<nom_du_livre>/...` comme préfixe de chemin (`"<nom_du_livre>/extraction"`, `"<nom_du_livre>/course.tex"`, etc.) supposent ce chemin complet ; un slug nu casserait ces appels dès le premier script de l'étape 1.

Contenu de ce dossier de travail :

```
<nom_du_livre>/
  extraction/
    structure.json          # sortie de extraire_structure.py
    pages/page_0001.txt ...  # texte brut de chaque page du pdf
  illustrations/               # PNG générés (une par concept clé du plan)
  plan_cours.json                # plan condensé (étape 1)
  glossaire.json                  # cohérence terminologique (étape 3)
  course.tex                       # cours LaTeX (étapes 3-4)
  scripts/                          # scripts Python générés pour CE pdf
  out/                                # PDF final livré à l'utilisateur 
```

## Correction automatique

Déclenchement : si une étape sort en erreur (code ≠ 0), il faut faire corriger par le modèle.

## Format de compte-rendu

Ce format s'applique **quel que soit le pdf traité** — il décrit une structure de compte-rendu, jamais un contenu figé.

1. **Pendant le travail** : après chaque action significative (exécution d'un ou plusieurs scripts, lecture d'un lot de fichiers, écriture d'un fichier), affiche une seule ligne courte au fil de l'eau décrivant l'action et son résultat immédiat (ex. « Exécute N commande(s), lu `<fichier>` », « Chapitre X lu intégralement », « Crée `<fichier>`+L-S »). Pas de paragraphe explicatif à chaque étape intermédiaire — la ligne suffit.
2. **À la fin de chaque étape** (sans pause ni question de validation), affiche un résumé structuré comprenant, dans cet ordre :
   - un titre court (« Étape N terminée — <objet> ») ;
   - une ou deux phrases de synthèse sur ce qui a été fait et comment (méthode suivie, fidélité à la source) ;
3. **Au rendu final** (étape 6) : Afficher le message COMPILATION REUSSIE !!!!

Le contenu de ces comptes-rendus (nombres, titres, décisions) dépend entièrement du pdf traité — seule la structure ci-dessus est fixe.

---

## Étape 0 — Vérification de non-doublon avant toute autre étape ( aucun appel `claude -p` )

Cette étape est lancée **avant** l'étape 1, sans exception : même en première exécution ou en relance. L'étape 1 et les suivantes ne démarrent que si elle renvoie un code `1`.

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/cours-condense/scripts/Etape0_verifier_deja_condense.py" "<chemin_du_livre.pdf>"
```

Lecture seule : ce script ne crée, ne modifie et ne supprime rien.

- Code `0` (`DEJA_CONDENSE`) : **arrêt immédiat**. N'exécute aucune autre étape et n'écris aucun fichier. Affiche exactement ce message, en remplaçant `<PDF>` par la valeur `PDF_PUBLIE` affichée par le script (ou, si elle vaut `absent`, par la valeur `PDF_COMPILE`) :
  
  > Ce pdf a déjà été condensé (`<PDF>`). /cours-condense s'arrête ici ; aucune étape n'a été lancée.
  
  Si le script affiche une ligne `INCOHERENCE:`, reporte-la aussi dans ta réponse, sans la corriger.

- Code `1` (`NON_CONDENSE`) : passer à l'étape 1.

- Code `2` (erreur : fichier source introuvable, slug vide) : arrêt, rapporte l'erreur telle quelle.

## Étape 1

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/cours-condense/scripts/Etape1.py" "<chemin_du_livre.pdf>"
```

Affiche dans la session principale ce que fait le script Etape1.py.

Si `Etape1.py` signale des erreurs, il fait corriger par le modèle en session principale. Avant d'appliquer la correction par le modèle, sauvegarder `plan_cours.json`. Continuez ainsi jusqu'a ce qu'il n'y ait plus d'erreurs retournées.

## Étape 2

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/cours-condense/scripts/Etape2.py" "<chemin_du_livre.pdf>"
```

Affiche dans la session principale ce que fait le script Etape2.py.

Si `Etape2.py` signale des erreurs, il fait corriger par le modèle en session principale.  Continuez ainsi jusqu'a ce qu'il n'y ait plus d'erreurs retournées.

## Étape 3

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/cours-condense/scripts/Etape3.py" "<chemin_du_livre.pdf>"
```

Affiche dans la session principale ce que fait le script Etape3.py.

Si `Etape3.py` signale des erreurs, il fait corriger par le modèle en session principale. Avant d'appliquer la correction par le modèle, sauvegarder les chapitres. Continuez ainsi jusqu'a ce qu'il n'y ait plus d'erreurs retournées.

## Étape 4

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/cours-condense/scripts/Etape4.py" "<chemin_du_livre.pdf>"
```

Affiche dans la session principale ce que fait le script Etape4.py.

Si `Etape4.py` signale des erreurs, il fait corriger par le modèle en session principale. Avant d'appliquer la correction par le modèle, sauvegarder course.tex. Continuez ainsi jusqu'a ce qu'il n'y ait plus d'erreurs retournées.

## Étape 5

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/cours-condense/scripts/Etape5.py" "<chemin_du_livre.pdf>"
```

Affiche dans la session principale ce que fait le script Etape5.py.

Si `Etape5.py` signale des erreurs, il fait corriger par le modèle en session principale. Avant d'appliquer la correction par le modèle, sauvegarder course.tex. Continuez ainsi jusqu'a ce qu'il n'y ait plus d'erreurs retournées.

## Étape 6

```bash
"<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/cours-condense/scripts/Etape6.py" "<chemin_du_livre.pdf>"
```

Affiche dans la session principale ce que fait le script Etape6.py.

Si `Etape6.py` signale des erreurs, il fait corriger par le modèle en session principale. Avant d'appliquer la correction par le modèle, sauvegarder course.tex. Continuez ainsi jusqu'a ce qu'il n'y ait plus d'erreurs retournées.

**Au rendu final** (étape 6) : Afficher le message COMPILATION REUSSIE !!!!
