---
name: ragpipeline
description: Enchaine tout le pipeline RAG sur un livre/support, de bout en bout, en invoquant successivement /cours-condense, /rag-extraction, /rag-nottext, /rag-chunking, /rag-index, /rag-concepts et /rag-graphe. Usage: /ragpipeline <chemin_vers_livre.pdf> [--from ETAPE] [--force]. Declenche aussi sur "fais entrer ce livre dans le RAG", "ingere ce document dans la base de connaissances".
---

# /ragpipeline — Orchestrateur du pipeline RAG complet

Enchaine les 7 etapes du pipeline sur un document source, chacune invoquee
comme une commande separee (pas un agent autonome unique) pour garder
chaque etape dans un contexte frais et borne en tokens :

```
/cours-condense -> /rag-extraction -> /rag-nottext -> /rag-chunking -> /rag-index -> /rag-concepts -> /rag-graphe
```

## Emplacements standardises (a respecter strictement)

```
<racine_projet>/
  corpusdedepart/             PDF sources (langue d'origine) UNIQUEMENT — entree de
                               /cours-condense. Jamais de dossier de travail ici.
  corpuscondense/              cours condenses en francais (PDF finaux) UNIQUEMENT —
                               sortie de /cours-condense, entree de /rag-extraction.
                               Jamais de dossier de travail ici non plus.
  rag_data/                   TOUTES les donnees generees par le pipeline, centralisees.
                               **Ne JAMAIS supprimer ce dossier ni son contenu, meme
                               partiellement, meme en cas d'echec ou de test — pas
                               d'action destructrice sur rag_data/ sans demande
                               explicite de l'utilisateur dans le message en cours.**
    courscondense/<slug>/       dossier de travail de /cours-condense (extraction/,
                                 illustrations/, scripts/, course.tex, glossaire.json,
                                 plan_cours.json, out/) — /cours-condense lui-meme n'est
                                 pas modifie, cette redirection se fait par une
                                 instruction ajoutee a son invocation (voir etape 1)
    work/<document_id>/         fichiers intermediaires PAR DOCUMENT du reste du
                                 pipeline (pivot.md, images/, nottext_meta.json,
                                 chunks.json, concepts.json, status.json, meta.json)
    db/vector/                    base Chroma UNIQUE, commune a tout le corpus
    db/graph/                      graphe Kuzu UNIQUE, commun a tout le corpus
```

**Jamais de dossier de travail cree a cote d'un PDF source ou condense.**
`document_id` (nom + hash du contenu du PDF condense) determine seul
l'emplacement sous `rag_data/work/`, calcule automatiquement par
`/rag-extraction`. Ne recree jamais cette logique a la main : passe toujours
le meme chemin de PDF (ou le `document_id` qu'un script a affiche) a l'etape
suivante.

## Regle d'execution (aucune tache en arriere-plan)

**Chaque commande (`python .../scripts/run.py ...`) s'execute en foreground,
de facon strictement bloquante, jusqu'a completion — jamais via
`run_in_background`, `Popen` detache, ou tout autre mecanisme asynchrone.**
Le script lui-meme est synchrone (y compris ses appels internes `claude -p`,
via `subprocess.run(..., timeout=...)`) ; c'est a l'appelant (toi) de ne pas
briser cette garantie en le lancant en arriere-plan. Lancer deux commandes du
pipeline en parallele, ou l'une en arriere-plan pendant qu'une autre tourne,
risque une contention sur les appels `claude -p` imbriques (verrou de
session/auth) qui peut faire timeout un appel pourtant fonctionnel isolement.
Meme regle que `/cours-condense` : pas de `ScheduleWakeup`, pas d'agent
async, pour aucune etape du pipeline.

**Une etape a la fois, dans l'ordre, sans sauter d'etape.** Si une etape
echoue ou remonte un probleme de qualite serieux, **arrete-toi et rapporte a
l'utilisateur** plutot que d'enchainer sur la suivante avec des donnees
douteuses.

Ne reimplemente jamais la logique d'une etape ici : invoque le skill
correspondant (`Skill` tool, ou directement son script si le skill est deja
charge dans cette conversation) plutot que de reecrire son traitement en
ligne.

## Deroulement

1. **`/cours-condense <corpusdedepart>/<livre>.pdf`** — produit le cours
   condense en francais. Etape la plus longue/couteuse (agent autonome
   complet) ; si elle a deja tourne pour ce livre, ne la relance pas sauf
   demande explicite.

   Le skill est autonome sur ce point (pas besoin d'instruction
   supplementaire de ta part) : il travaille lui-meme sous
   `rag_data/courscondense/<slug_du_livre>/` et publie automatiquement son
   PDF final vers `<racine_projet>/corpuscondense/<slug>.pdf` en derniere
   etape (voir `cours-condense/SKILL.md`, etape 6.5). C'est cette copie dans
   `corpuscondense/` qui sert d'entree a l'etape suivante.
2. **`/rag-extraction <corpuscondense>/<livre>.pdf`** — pivot markdown +
   images, ecrits dans `rag_data/work/<document_id>/`. Note le
   `document_id` affiche : reutilise-le (ou le meme chemin de PDF) pour
   toutes les etapes suivantes.
3. **`/rag-nottext <document_id_ou_pdf>`** — decrit en langage naturel
   chaque image (avec OCR/LaTeX adapte, nommage explicite), bloc de code et
   formule d'affichage deja en LaTeX, puis enrichit `pivot.md` en
   consequence — sans cette etape, ce contenu (en particulier une formule,
   qu'elle soit restee image ou deja en LaTeX) reste invisible a la
   recherche vectorielle et au graphe de concepts, un modele d'embedding
   texte ne rapprochant quasiment jamais une question en francais d'un
   verbatim LaTeX/code/image brut (verifie empiriquement).
4. **`/rag-chunking <document_id_ou_pdf>`** — chunks.json.
5. **`/rag-index <document_id_ou_pdf>`** — indexation vectorielle (base
   partagee `rag_data/db/vector/`).
6. **`/rag-concepts <document_id_ou_pdf>`** — extraction de concepts par chunk.
7. **`/rag-graphe <document_id_ou_pdf>`** — resolution d'entites + graphe
   (base partagee `rag_data/db/graph/`).

## Reprise

Chaque etape verifie elle-meme `rag_data/work/<document_id>/status.json` et
saute son propre travail si deja `done` (sauf `--force`). Pour reprendre un
pipeline interrompu, relance simplement `/ragpipeline` sur le meme fichier
source : les etapes deja faites se sautent d'elles-memes.

`--from ETAPE` (valeurs : `courscondense`, `extraction`, `nottext`, `chunking`,
`index`, `concepts`, `graphe`) force le redemarrage a partir de cette etape
avec `--force`, utile si un changement en amont (ex. nouveau modele
d'embedding) doit se repropager sans tout refaire depuis `courscondense`.

## A la fin

Resume a l'utilisateur, pour ce document : nombre de pages/chunks traites,
nombre de concepts extraits, nombre de mentions liees au graphe, et surtout
tout probleme de qualite ou echec rencontre en cours de route. Si
`/rag-graphe` a fusionne des concepts avec des documents deja presents dans
le corpus, signale-le — c'est le signal que la base de connaissances se
densifie.
