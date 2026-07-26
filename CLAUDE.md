# File Explorer — contexte projet

Outil de chargement, contrôle, nettoyage et transformation de données
(CSV/XLSX/EDIFACT), avec bibliothèque d'artefacts versionnés, flux visuels,
identité et droits. Backend FastAPI + pandas, frontend React/TS/Vite,
PostgreSQL (SQLite en repli).

**Le README à la racine est la documentation de référence** : chaque version y
est expliquée avec le *pourquoi* des décisions, pas seulement le quoi. Le lire
avant de proposer une refonte.

## Lancer et vérifier

```bash
# tests — TOUJOURS les deux bases avant de livrer
cd backend && python -m pytest -q
FX_DB_URL=postgresql+psycopg2://app:app@127.0.0.1:5432/fx_test python -m pytest -q

cd frontend && npm run build      # TypeScript strict : doit passer

docker compose up                 # front :8080, back :8000
```

- `FX_MASTER_KEY` (hors base) est requise pour les colonnes confidentielles.
  Sans elle, la création de clé **refuse** au lieu de dégrader.
- `FX_DB_URL` > `DATABASE_URL` > SQLite. `alembic/env.py` résout l'URL comme
  l'application (une migration en ligne de commande doit viser la même base).
- pip : `--break-system-packages`.

## Invariants d'architecture — ne pas les casser

1. **Un seul moteur, atteint depuis plusieurs endroits.** Le moteur
   d'expressions sert les colonnes calculées, les mappings, les briques, les
   fonctions utilisateur. Le moteur de validation sert les fichiers, les
   mappings, les cibles de chargement, la brique `config`. Ne jamais en écrire
   un second : un regex doit vouloir dire la même chose partout.
2. **Les artefacts sont immuables et versionnés.** On ajoute une version, on
   n'écrase jamais. Un nom différent = un artefact différent.
3. **Le pivot est le format de câble.** Toute source se traduit vers/depuis
   `{head, items}`. N sources = 2N traducteurs, jamais N².
4. **L'environnement vient du jeton, pas de l'URL.** `?env=` ne fait que
   proposer ; l'appartenance décide. Une requête qui oublie la portée montre
   **moins**, jamais plus.
5. **Échouer fermé.** Une capacité inconnue est refusée. Une clé absente bloque.
   Un chargement partiel n'écrit rien.

## Règles apprises à la dure (des bugs réels)

- **Masquer les secrets en profondeur.** Masquer le champ évident ne suffit
  pas : la valeur revient par les métadonnées du nœud.
- **La sélection de lignes s'applique après la validation.** Cocher une ligne en
  erreur ne la blanchit pas.
- **Garder la création ne suffit pas** : `add_version` est aussi une
  modification et doit porter la même garde.
- **Une colonne déclarée mais absente est sautée en silence** par le moteur de
  nettoyage. D'où `require_columns` dans la brique `config` : un contrat qui ne
  trouve rien n'a rien vérifié — un faux vert est pire qu'un rouge.
- **Ordre de lecture** : la clé primaire est un UUID aléatoire et un
  `bulk_insert` partage un horodatage. Toujours un `ordinal` explicite.
- **Lecture après écriture** : `get_session` valide au démontage de la
  dépendance, donc *après* la réponse. Chaque route mutante appelle
  `db.commit(s)` explicitement.
- **YAML 1.1** : `on`, `off`, `yes`, `no` sont des booléens. Les clés de config
  de nœud sont normalisées.
- **Ne pas proposer un geste que la personne ne peut pas terminer.** L'interface
  lit `capabilities` depuis `/api/auth/state`.

## Méthode de travail attendue

- **Auditer avant d'adopter.** Si des fichiers apparaissent sans avoir été
  écrits dans la session, les lire ligne à ligne et le signaler. C'est arrivé
  plusieurs fois et ils étaient souvent bien conçus mais **non branchés** (pas
  dans `KINDS`, aucune route, aucun test).
- **Un test qui échoue est une information, pas un obstacle.** Vérifier si le
  test a tort ou si le code a tort — dans ce projet, c'était environ moitié-moitié.
- **Les commentaires expliquent le pourquoi**, pas le quoi. Les décisions et les
  refus délibérés se documentent à l'endroit du code concerné.
- **Tests en anglais, interface et messages d'erreur en français.**
- Ne pas commiter sur la branche principale sans relecture.

## État réel — à connaître

**Ce qui est solide** : 324 tests verts sur SQLite et PostgreSQL, migrations
rejouées sur base vierge, build TypeScript strict.

**Ce qui bloque une mise en production** :

1. **Les sessions de travail vivent dans la mémoire du process**
   (`app/session.py`, `SessionStore` = un `dict`, TTL 1 h). Conséquence :
   `uvicorn --workers 2` casse tout, un redémarrage perd le travail en cours,
   pas d'horizontalité. **C'est le chantier numéro un** et il contredit tout le
   travail multi-utilisateur.
2. **Le frontend n'a été exécuté que quelques fois** (v34–v36) et n'a **jamais
   été utilisé par une vraie personne**. Zéro test frontend.
3. Pas de limite de taille d'upload, pas de healthcheck, pas de logs
   structurés, pas de CI.
4. **La signature des `id_token` OIDC n'est pas vérifiée** — signalé dans le
   code à l'endroit exact. Acceptable seulement parce que le jeton vient du
   *token endpoint* en TLS.
5. L'interface **mélange français et anglais** (barre latérale et panneaux
   anciens en anglais).

## Regarder le frontend

Aucun navigateur n'est installé par défaut. Chemin qui fonctionne :
`npm i playwright-core @sparticuz/chromium`, décompresser
`bin/chromium.br` (brotli), lancer avec `--no-sandbox`. Les captures se
regardent ensuite comme des images. C'est ainsi que trois bugs invisibles à la
compilation ont été trouvés.

## Une navigation, une page d'accueil

- La connexion est **obligatoire**. Sur une installation neuve, l'écran demande
  de créer le compte administrateur puis de se connecter : pas de mot de passe
  par défaut, mais pas d'entrée libre non plus.
- Une session démarre sur `#home`, pas dans l'atelier fichier — celui-ci est un
  module parmi d'autres, et un environnement peut ne pas l'exposer. Les cartes
  affichées viennent du profil (`shows()`), jamais d'une liste figée.
- **Une seule navigation**, au même endroit, qu'un fichier soit chargé ou non.
  La version précédente avait huit boutons en bas de l'état vide et une barre
  d'onglets ailleurs.

## Prochaines étapes envisagées

Fait : **une table s'ouvre comme une session** (`POST /api/datasets/{id}/open`).
Plutôt que de construire un second système de navigation avec ses propres
filtres, son tri et sa pagination, la table *devient* une session — et tout ce
qui existe s'applique : les filtres de la vue Data, le mode éditable, le
rapport, l'export, et la réécriture dans une table. Consulter, corriger et
réenregistrer sont les trois mêmes gestes que pour un fichier.

Bornes : plafond de lignes (une session vit en mémoire) et cellules chiffrées
masquées — les déchiffrer là contournerait la liste des détenteurs.

Par ordre discuté : source SQL en lecture seule avec aperçu
limité (paramètres liés, jamais de substitution textuelle), disposition
automatique et compteurs de lignes sur les arêtes du canevas, pagination de la
brique `api`. La **brique code** a été écartée : elle casserait l'isolation du
moteur d'expressions et rendrait un flux illisible depuis son graphe.
