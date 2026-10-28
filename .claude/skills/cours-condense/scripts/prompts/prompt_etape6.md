## Étape 6 — Compilation + vérification, décision, livraison

1. Compile :
   
   ```bash
      "<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/cours-condense/scripts/compiler.py" "<nom_du_livre>/course.tex" "<nom_clair_du_cours>"
   ```
   
   Rapporte : succès/échec de chaque passe, erreurs ou warnings éventuels (le script les extrait automatiquement des logs).

2. Compare la table des matières de `course.tex` à `plan_cours.json` : vérifie qu'un chapitre du PDF existe bien pour chaque entrée du plan, et que chaque concept clé et chaque section source listés dans `plan_cours.json` pour ce chapitre sont couverts par au moins un paragraphe explicatif identifiable dans le chapitre correspondant. (La présence du `\section*{Résumé}` de chaque chapitre — règle 11 de l'étape 3 — est déjà vérifiée mécaniquement par `verifier_structure_course_tex.py` à la fin de l'étape 3/4 ; inutile de la re-vérifier manuellement ici.)
   
   Si un concept ou une section du plan n'a aucune trace dans le texte final :
   
   - ne modifie rien automatiquement
   - indique précisément le chapitre et le concept/section manquant
   - précise si l'absence semble venir d'une coupe excessive (au-delà de la simple reformulation/condensation attendue) ou d'un oubli lors de la rédaction
   - rappelle qu'aucun bloc de code ne doit jamais être raccourci, quelle que soit sa longueur
   
   Si tous les concepts et sections du plan sont couverts, ne fais rien de plus — c'est normal, quelle que soit la longueur obtenue.

3. Compte le nombre de pages du PDF généré (déjà affiché par `compiler.py`, via `pypdf` ou, à défaut, `pymupdf`) et compare-le au nombre de pages du livre source (`structure.json` -> `nb_pages`). 
   Donne ce ratio à titre purement indicatif, sans seuil ni jugement automatique : il varie légitimement selon la densité de code et la richesse du livre source, et ne doit jamais servir de critère pour décider de condenser ou d'étoffer davantage.

4. Le script `compiler.py` a déjà copié le PDF dans `<nom_du_livre>/out/` et supprime les fichiers auxiliaires (`.aux`, `.log`, `.toc`, `.out`), et les logs de chaque passe. 
   Donne un résumé chiffré final : pages du livre source, pages du cours final, ratio obtenu (en %, à titre indicatif), confirmation qu'aucun concept ou section du plan n'est resté sans couverture textuelle, et qu'aucun résidu anglais/YouTube/citation numéroté n'a été détecté à l'étape 5.

5. **Publie le PDF final dans `corpuscondense/`** — cette commande doit pouvoir s'exécuter seule, indépendamment de tout orchestrateur, donc cette publication fait partie du skill lui-même, pas d'une étape externe :
   
   ```bash
      "<racine_projet>/.venv-rag/Scripts/python.exe" "<racine_projet>/.claude/skills/cours-condense/scripts/publish_condense.py" "<racine_projet>/<nom_du_livre>/"
   ```
   
   `<nom_du_livre>` inclut déjà `rag_data/courscondense/` (voir "Arborescence de travail" en tête de ce fichier) — ne préfixe pas une deuxième fois avec `rag_data/courscondense/`, sous peine de chemin invalide (`.../rag_data/courscondense/rag_data/courscondense/...`). Le script cherche récursivement `out/*.pdf` (peu importe la profondeur exacte) et copie le plus récent vers `<racine_projet>/corpuscondense/<slug>.pdf`, où `<slug>` est le dernier segment de `<nom_du_livre>` (le nom du dossier de travail lui-même, pas le chemin complet). 
   Rapporte le chemin de destination confirmé par le script. Si le script échoue (PDF introuvable), arrête-toi et signale-le — ne recopie jamais le fichier à la main en contournant le script.
