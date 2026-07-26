# File Explorer — back + front

Outil de chargement, nettoyage et validation de fichiers CSV / XLSX piloté par
une configuration **déclarative en YAML** : règles par colonne, mapping via table
de correspondance (TCO), rapport d'erreurs cellule par cellule. Depuis la v12,
configs, computed et TCO se rangent dans une **bibliothèque versionnée**
(PostgreSQL ou SQLite) et se composent en **flux** réutilisables : un id de
flux + un fichier = un traitement complet, tracé et rejouable. La v13 ajoute un
**module EDI** : lecture, contrôle et mise à plat de fichiers EDIFACT, qui
rebranchent ensuite sur toute la machinerie de nettoyage ci-dessus. La v14 ferme
la boucle : lignes ajoutables et supprimables à la main, puis **enregistrement en
base** du résultat validé — avec un refus explicite, et distinguant les problèmes
de données des problèmes de squelette.

Ce dépôt est la **refonte full-stack** de l'outil d'origine (une application
Streamlit monolithique). La logique métier — déjà découplée de l'UI dans la
version Streamlit — a été conservée et portée telle quelle. Tout le reste a été
reconstruit : une API REST d'un côté, une interface React de l'autre.

---

## Ce qui a changé

| Avant (Streamlit) | Après |
|---|---|
| UI et métier dans le même processus | **Backend FastAPI** + **frontend React** séparés |
| État dans `st.session_state` | Sessions serveur (store en mémoire, TTL 1 h) |
| Pas d'API : non intégrable | **API REST** documentée, réutilisable par n'importe quel client |
| Rendu serveur, rechargements complets | SPA réactive, appels asynchrones |
| Coloration via `pandas.Styler` | Statut renvoyé par cellule, coloration côté client |
| — | **Tests** API de bout en bout (`pytest`) |
| — | Packaging **Docker** (`docker compose up`) |
| Configs / computed / TCO : fichiers à ré-uploader | **Bibliothèque persistée** (Postgres/SQLite), versionnée, adressable par id |
| Rien de rejouable | **Flux** nommés + **runs** tracés qui figent les versions exactes utilisées |
| — | **Module EDI** : inspection, contrôle, pivot, génération, conversion, modèles versionnés |
| Flux linéaire figé | **Graphe de briques** : sources, transformations, sorties — et un flux servi comme API |
| Pont EDI↔plat par coïncidence de noms | **Mapping** explicite et réversible, avec expressions et contraintes |
| Session impossible sans fichier | **Schéma d'abord** : colonnes à la main ou depuis une config |
| Nettoyage cellule par cellule seulement | **Lignes** ajoutables, duplicables, supprimables (logiquement, réversible) |
| La donnée propre finit en fichier à ranger | **Tables en base** : replace / append / upsert, schéma issu de la config, écritures tracées |

Deux **bugs de la version d'origine** ont été trouvés grâce aux tests et corrigés
(détails plus bas).

---

## Architecture

```
file-explorer/
├── backend/                  API REST (FastAPI)
│   ├── app/
│   │   ├── main.py           routes HTTP fichiers/sessions (couche fine)
│   │   ├── store_routes.py   routes HTTP bibliothèque : artefacts, flux, runs
│   │   ├── edi_routes.py     routes HTTP EDI : inspect, validate, pivot, generate, convert
│   │   ├── dataset_routes.py routes HTTP tables : preflight, write, lecture, journal
│   │   ├── edi_models.py     contrat du modèle EDI (segments, variantes, champs)
│   │   ├── models.py         contrat de données : domaine (FileConfig…) + API
│   │   ├── store_models.py   contrat de données de la bibliothèque (Pydantic)
│   │   ├── session.py        store de sessions en mémoire (DataFrames côté serveur)
│   │   ├── db.py             moteur SQLAlchemy + init (Alembic d'abord, create_all sinon)
│   │   ├── db_models.py      tables : artefacts, versions, flux, runs, datasets
│   │   ├── repository.py     accès données (requêtes, versionnement append-only)
│   │   └── services/         logique métier portée depuis l'app Streamlit
│   │       ├── file_service.py        chargement CSV/XLSX, détection encodage
│   │       ├── function_service.py    fonctions atomiques validation/nettoyage
│   │       ├── process_service.py     pipeline complet (header→clean→validate→report)
│   │       ├── config_service.py      sérialisation YAML + matching colonnes
│   │       ├── tco_service.py         lookup table de correspondance
│   │       ├── pipeline_engine.py     moteur one-shot réutilisé par /pipeline et les runs
│   │       ├── store_service.py       validation des artefacts avant enregistrement
│   │       ├── edi_lexer.py           découpage EDIFACT (UNA, échappement, rendu)
│   │       ├── edi_service.py         enveloppes, contrôle, pivot, génération, inférence
│   │       ├── edi_kb.py              base de connaissances (segments, qualifiants, doc)
│   │       └── dataset_service.py     preflight (squelette vs données), payload, schéma
│   ├── alembic/              migrations de schéma (SQLite et PostgreSQL)
│   ├── tests/                170 tests (api, store, edi, datasets, blank, mapping, graphs)
│   └── requirements.txt
├── frontend/                 SPA (React + TypeScript + Vite)
│   └── src/
│       ├── App.tsx           orchestration de l'état et des flux
│       ├── lib/              types (miroir du backend), client API, icônes
│       ├── components/       Sidebar, SchemaPanel, FieldEditor, DataTable,
│       │                     ReportPanel, YamlPanel, ComputedPanel, FlowsPanel,
│       │                     EdiPanel (inspect, transform, generate, convert, models, doc),
│       │                     DatasetPanel (écriture en base, verdict, source brute)
│       └── styles.css        système de design (tokens, typographie)
├── samples/                  fichiers d'exemple prêts à charger
│   └── edi/                  2 fichiers EDIFACT + leurs modèles YAML
└── docker-compose.yml        frontend + backend + PostgreSQL 16
```

Le frontend ne contient **aucune logique métier** : il lit le contrat de données,
appelle l'API, affiche les résultats. Toute décision (qu'est-ce qui est valide,
qu'est-ce qui a été nettoyé) est prise par le backend — exactement la même règle
que celle qui régissait les composants Streamlit.

---

## Démarrer

### Option 1 — Docker (le plus simple)

```bash
docker compose up --build
# frontend : http://localhost:8080
# backend  : http://localhost:8000  (docs interactives : /docs)
# postgres : la bibliothèque (configs, flux, runs) survit aux redémarrages (volume dbdata)
```

### Option 2 — en local

**Backend**
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload          # http://localhost:8000
```
Sans autre configuration, la bibliothèque s'appuie sur un fichier **SQLite**
(`file_explorer.db`) créé au premier démarrage — zéro dépendance. Pour pointer
vers PostgreSQL : `DATABASE_URL=postgresql+psycopg2://user:pass@hote:5432/base`
(la variable `FX_DB_URL` est prioritaire si les deux sont définies, utile pour
les tests) 

**Frontend** (autre terminal)
```bash
cd frontend
npm install
npm run dev                            # http://localhost:5173
```
Le serveur de dev proxifie `/api` vers `localhost:8000`, donc rien à configurer.

### Essayer tout de suite

Le dossier `samples/` contient de quoi tester le flux complet :
1. Charger `samples/clients.csv`.
2. (Optionnel) Charger `samples/tco.csv` comme table de correspondance.
3. Importer `samples/config.yaml` pour pré-remplir toutes les règles…
   …ou les définir à la main dans l'onglet **Schema**.
4. Onglet **Data** → *Run validation*. Lignes en erreur en rouge, valeurs
   nettoyées en vert. Le détail est dans l'onglet **Report**.

Et pour l'EDI, `samples/edi/` :
1. Onglet **EDI** → *Inspect* → ouvrir `orders_d96a.edi` (aucune extension
   requise, le format est détecté au contenu). L'arbre décodé s'affiche,
   segment par segment, en clair.
2. *Models* → *Load a .yaml* → `model_orders_d96a.yaml` → *Save to library*.
3. *Transform* → le même fichier + ce modèle → **Validate** (2 messages,
   3 lignes, 0 erreur), puis **Pivot** en `flat` ou `linked`.
4. **Open in Data** : le tableau aplati devient un fichier de travail ordinaire
   — les règles par colonne, le TCO et le rapport s'appliquent dessus.

---

## Mode édition (v11)

L'onglet **Data** permet désormais de corriger le fichier *dans* l'outil, sans
aller-retour Excel :

1. Activer **✎ Edit cells** dans la barre d'outils du tableau.
2. Cliquer une cellule, taper la correction — `Entrée` enregistre, `Échap`
   annule. La cellule passe en orange (`EDITED`) : les couleurs de la dernière
   validation sont périmées tant qu'on n'a pas revalidé.
3. **Re-validate** relance le pipeline sur les valeurs corrigées ;
   **Discard all edits** restaure le fichier tel que chargé (le traitement de
   header est ré-appliqué).

Les corrections s'appliquent à la **table de travail** (valeurs sources, avant
nettoyage et renommage) : c'est donc bien la donnée corrigée qui traverse le
pipeline, exactement comme si le fichier d'origine avait été corrigé. Les
colonnes calculées sont en lecture seule (elles se recalculent). Côté API :
`POST /api/files/{sid}/cells` avec `{edits: [{index, column, value}]}` — l'index
de ligne stable est renvoyé par `/files`, `/process` et `/rows` (champ `index`),
et `column` est le nom **source** de la colonne. Ré-appliquer un traitement de
header reconstruit la table de travail et annule donc les corrections.

---

## Bibliothèque & flux (v12)

Jusqu'ici, tout était éphémère : la config vivait dans un YAML à ré-uploader,
les computed dans un JSON à côté, le rapport disparaissait avec la session.
La v12 rend ces objets **persistants et adressables par id**.

### Les concepts

**Artefact** — une config, un jeu de colonnes calculées ou une TCO enregistrée
en base (`kind` : `config` | `computed` | `tco`). Un artefact porte un nom et
des **versions numérotées**. Le corps de chaque version est validé par les
services métier existants *avant* enregistrement : impossible de stocker une
config YAML invalide ou une expression calculée refusée par la liste blanche.

**Versionnement immuable** — on ne modifie jamais une version : « modifier »,
c'est ajouter la version *n+1*. La v1 reste intacte pour toujours. C'est ce qui
rend les runs **reproductibles** : un résultat pointe vers des versions
précises, qui ne peuvent plus changer sous lui. « Repartir d'un modèle », c'est
charger une version existante dans l'éditeur puis l'enregistrer comme nouvel
artefact ou nouvelle version.

**Flux** — la composition qui donne sa valeur au tout : *une config + une TCO +
des computed sous un seul id*. Chaque référence est soit **épinglée** à une
version précise (production : rien ne bouge sans décision), soit en mode
**dernière version** (itération rapide : le flux suit les mises à jour de
l'artefact). Un flux n'est pas un moteur de workflow — pas de branches, pas de
conditions : une composition, rien d'autre.

**Run** — l'exécution d'un flux sur un fichier. `POST /api/flows/{id}/run` avec
le fichier en multipart, et c'est tout : plus de YAML, plus de TCO, plus de
JSON à joindre. Le run enregistre **les ids de versions exactes** résolus au
moment de l'exécution (même en mode « dernière version »), les stats, le
rapport d'erreurs groupé, et le fichier de sortie — retéléchargeable plus tard
par `GET /api/runs/{id}/export`. Un run en erreur de validation est persisté
aussi : l'échec tracé fait partie de l'historique.

### L'API de la bibliothèque

| Méthode | Route | Rôle |
|---|---|---|
| `GET`  | `/api/artefacts/{kind}` | liste (nom, versions, dates) — `?include_archived=1` pour tout voir |
| `POST` | `/api/artefacts/{kind}` | crée l'artefact avec sa v1 (corps validé) |
| `GET`  | `/api/artefacts/{kind}/{id}` | détail + liste des versions |
| `POST` | `/api/artefacts/{kind}/{id}/versions` | ajoute une version (immuable ensuite) |
| `GET`  | `/api/artefacts/{kind}/{id}/versions/{no}` | corps exact d'une version |
| `GET`  | `/api/artefacts/config/{id}/versions/{no}/yaml` | la version rendue en YAML (pour l'éditeur) |
| `DELETE` | `/api/artefacts/{kind}/{id}` | archive (soft delete — l'historique des runs reste lisible) |
| `GET` / `POST` | `/api/flows` | liste / crée un flux (références épinglées ou « latest ») |
| `GET` / `PATCH` / `DELETE` | `/api/flows/{id}` | détail / re-pointage / archivage |
| `POST` | `/api/flows/{id}/run` | **le** point d'entrée : fichier en entrée → run persisté |
| `GET`  | `/api/runs?flow_id=` | historique des exécutions |
| `GET`  | `/api/runs/{id}` | détail d'un run : versions figées, stats, rapport |
| `GET`  | `/api/runs/{id}/export` | retélécharge le fichier de sortie du run |

Le YAML et les endpoints « par fichier » existants ne bougent pas : la
bibliothèque **s'ajoute**, elle ne remplace rien. L'ancien `/api/pipeline`
(tout en multipart) et le nouveau `/flows/{id}/run` partagent le même moteur
(`pipeline_engine.py`) : un seul chemin de code à maintenir.

### Côté interface

Un onglet **Flows** (accessible aussi depuis l'écran d'accueil, sans fichier
chargé) permet de composer un flux à partir de la bibliothèque, de le lancer
sur un fichier, et de consulter l'historique des runs avec leurs exports. Les
onglets **YAML** et **Computed** gagnent chacun un bloc *bibliothèque* :
enregistrer la config courante (nouvel artefact ou nouvelle version), ou
charger une version stockée dans l'éditeur. La TCO s'enregistre depuis
l'onglet Flows (upload direct en bibliothèque).

---

## Éditer les lignes, puis enregistrer en base (v14)

La boucle que l'outil ferme ici : le pipeline signale des problèmes → on revient
sur les données pour les corriger à la main → on relance → on enregistre le
résultat dans une table. Et si ce n'est pas corrigeable ligne à ligne, on le dit
franchement au lieu de faire semblant.

### Ajouter et supprimer des lignes

Le mode éditable savait corriger une cellule, pas jeter une ligne. C'était la
moitié manquante : « filtrer les erreurs → supprimer le lot » est un geste de
nettoyage plus courant que la correction unitaire.

- **Suppression logique, jamais physique.** La ligne quitte la table active mais
  reste dans `work_df` ; seul son index part dans `sess.deleted`. Trois raisons :
  `/cells/reset` restaure tout, « 12 lignes supprimées » est une information
  reportable, et l'annulation est gratuite.
- **Ajout** d'une ligne vide ou **duplication** d'une ligne existante. Le nouvel
  index vient d'un compteur monotone **jamais réutilisé** : c'est l'index stable
  sur lequel reposent l'overlay d'édition, le rapport et l'écriture en base, une
  collision y serait fatale.
- **Suppression du lot filtré** — puissant et assumé : tu filtres sur les
  erreurs, tu vides en un clic. Réversible, donc défendable.

Comme les éditions de cellules, ces opérations vivent dans la session et
disparaissent si la table est reconstruite (changement de feuille, de ligne
d'en-tête). Même règle qu'avant, annoncée.

| Méthode | Route | Rôle |
|---|---|---|
| `GET` | `/api/files/{sid}/preview` | La table active — éditions appliquées, supprimées exclues |
| `POST` | `/api/files/{sid}/rows/add` | Ligne vide, ou duplication de `copy_from` |
| `POST` | `/api/files/{sid}/rows/delete` | Par index, ou `all_filtered` sur les filtres/statuts courants |
| `POST` | `/api/files/{sid}/rows/restore` | Annuler des suppressions |

### Les deux natures d'échec

C'est le cœur du module. Une écriture peut échouer pour deux raisons qui se
ressemblent et n'ont rien à voir :

- **Les données sont fausses.** Un SIRET malformé, une date illisible, un
  identifiant vide. Problèmes *par ligne*. On les corrige à la main dans l'onglet
  Data, on relance, on écrit. L'unité, c'est la ligne.
- **Le squelette est faux.** Mauvais délimiteur, en-tête sur la mauvaise ligne,
  config qui vise une autre feuille, colonnes que la table ne connaît pas. Là,
  *aucune* correction ligne à ligne n'aide : toutes les lignes sont « fausses »
  parce que la forme l'est. On retourne à la source, on regarde ce que le fichier
  contient vraiment, on corrige la configuration.

Annoncer « 3000 erreurs » quand la vérité est « ton en-tête est décalé d'une
ligne », c'est exactement le mode d'échec que ce module existe pour éviter. Le
preflight classe donc chaque problème en `config` ou `data`, et le verdict porte
un `blocked_by` qui dit lequel des deux bloque — la config d'abord, puisqu'un
squelette cassé rend toutes les plaintes sur les données inaudibles.

Et l'interface propose l'action correspondante : **« Corriger dans Data »** pour
un problème de données, **« Voir la source brute »** pour un problème de
squelette. Cette dernière (`GET /api/files/{sid}/source`) montre le fichier
**tel qu'il a été lu** — avant traitement d'en-tête, renommage et éditions. Des
colonnes nommées `Unnamed: 3`, et tu sais immédiatement que c'est la ligne
d'en-tête ou le délimiteur qu'il faut reprendre.

### Pourquoi pas de DDL à l'exécution

Créer une vraie table SQL depuis un CSV arbitraire, c'est du DDL à l'exécution,
et c'est là que ce genre d'outil pourrit : noms de colonnes avec accents, espaces
ou mots réservés → il faut un assainisseur → les colonnes de la table ne
correspondent plus à celles du fichier ; le fichier v2 a une colonne en plus →
`ALTER` ou refus ? ; et deux autorités de schéma dans la même base, Alembic *et*
le DDL runtime.

Choix retenu : **deux tables physiques**, `datasets` (métadonnées et schéma) et
`dataset_rows` (une ligne = un JSONB). Zéro DDL à l'exécution, Alembic reste seul
maître du schéma, colonnes arbitraires gratuites, et ça marche aussi sur SQLite.
Sur PostgreSQL, un index GIN rend les recherches dans le JSONB rapides. Si un
jour tu veux du SQL naturel pour un outil externe, une vue typée par dataset est
une option bon marché à ajouter — pas un prérequis.

**Et ta config *est* le schéma.** `identifiant: true` donne la clé, les types de
champs donnent les types de colonnes : tout ce dont un schéma a besoin est déjà
déclaré. Le dataset l'enregistre à la première écriture, ce qui transforme la
dérive de schéma en **événement explicite et rattrapable** — un `append` dont les
colonnes ont changé est refusé avec un message, au lieu de corrompre en silence.
Seul `replace` a le droit de redéfinir.

### Modes d'écriture

| Mode | Effet | Rejouable ? |
|---|---|---|
| `replace` | Vide la table puis réécrit tout | Oui, idempotent |
| `append` | Ajoute à la suite | Non, duplique |
| `upsert` | Met à jour sur la clé, insère le reste | Oui |

**La clé est apposée sur chaque ligne quel que soit le mode.** Ce n'est pas un
détail : le `key_hash` d'une ligne est ce qu'une fusion ultérieure retrouve. Une
table remplie par `replace` sans clé estampillée ne peut plus être fusionnée —
la deuxième écriture ne trouve rien et double la table. Le mode décide si la clé
*sert à apparier*, pas si elle est *enregistrée*. Et si la requête ne la précise
pas, celle que le dataset a mémorisée s'applique.

Le mode miroir — supprimer les lignes de la table absentes du fichier — est
volontairement écarté : trop destructeur pour être proposé à la légère.

**Politique sur les lignes en erreur** : `reject` (écrit les propres, écarte les
autres — le défaut), `block` (refuse tant qu'une ligne est fausse), `all` (écrit
tout). Le défaut est `reject` parce qu'une table qui mélange valide et invalide
empoisonne tout ce qui la lira ensuite.

### Ordre de lecture

Une clé primaire en UUID aléatoire et un `bulk_insert` qui partage un seul
horodatage ne donnent **aucun ordre exploitable** : les lignes ressortent
mélangées. Chaque ligne porte donc un `ordinal` explicite, attribué à
l'insertion. Un `append` se pose après l'existant, un `upsert` **conserve la
position** de la ligne qu'il met à jour.

### Traçabilité

Chaque écriture est enregistrée — table, mode, clé, lignes entrées / insérées /
mises à jour / écartées / supprimées, horodatage. **Les refus aussi** : « pourquoi
ça n'est pas passé » est exactement la question posée une semaine plus tard.
C'est le prolongement direct de la philosophie des runs de la v12.

| Méthode | Route | Rôle |
|---|---|---|
| `GET` | `/api/datasets` | Les tables |
| `GET` | `/api/datasets/{id}/rows` | Lecture paginée, dans l'ordre d'écriture |
| `GET` | `/api/datasets/{id}/writes` | Journal des écritures et des refus |
| `DELETE` | `/api/datasets/{id}` | Archiver |
| `POST` | `/api/files/{sid}/datasets/preflight` | Le verdict, sans rien toucher |
| `POST` | `/api/files/{sid}/datasets/write` | Écrire (preflight compris) |
| `GET` | `/api/files/{sid}/source` | Le fichier tel qu'il a été lu |

### Postgres plutôt que DuckDB

Question posée, réponse assumée : ce module est un **système d'enregistrement**,
pas un moteur d'analyse. On écrit, on réécrit, on fusionne sur une clé, et
d'autres outils lisent la même donnée pendant qu'on travaille — le métier de
PostgreSQL, déjà présent dans la stack, déjà gouverné par Alembic.

DuckDB est excellent, mais pour l'autre moitié du monde : agréger des dizaines de
millions de lignes en local, lire du Parquet sans le charger. Moteur analytique
embarqué, mono-écrivain, orienté fichier. Le jour où il faudra du tableau de bord
sur gros volume, ce sera le bon choix ; l'ajouter aujourd'hui, ce serait un
second moteur et un second dialecte pour un besoin qui n'est pas celui-là.

### Limites assumées

- **Mono-opérateur.** Dernier écrivain gagne. Pas de verrou, pas de fusion
  concurrente — autant le dire plutôt que faire semblant.
- Les gros volumes passent par des insertions par lots (1000 lignes). Au-delà de
  quelques centaines de milliers de lignes, `COPY` serait le bon outil.
- Les suppressions et ajouts de lignes vivent dans la session, comme les
  éditions : ils ne survivent pas à une reconstruction de la table.

---

## L'écran de l'opératrice, vérifié pour de vrai (v36)

Le cas d'usage RH a été monté entièrement — environnement, configuration
imposée, table de correspondance, compte opératrice — puis **son identité a été
empruntée et son écran regardé**.

Le profil faisait bien son travail : les sept entrées de modules avaient disparu.
Mais il restait *Import YAML*, *Columns by hand*, *From a saved config*,
*Create session* — des gestes qu'une opératrice n'a pas le droit de terminer.
Elle en aurait fait l'effort pour rencontrer un refus à l'enregistrement.

**Offrir un geste que quelqu'un ne peut pas terminer est pire que le cacher.**
L'interface demande donc maintenant ce que la personne peut faire
(`capabilities`, renvoyé par `/api/auth/state`) et ne propose que cela :

- la réutilisation d'une configuration disparaît quand l'environnement en impose
  une, ou quand la personne n'a pas `config.write` ;
- le départ « depuis un schéma » disparaît dans les mêmes conditions.

Son écran se réduit alors à : choisir le format, déposer un fichier, et charger
une table de correspondance. C'est-à-dire l'outil du premier jour — ce qui était
l'objectif.

---

## Un flux servi comme une vraie API (v35)

Les flux étaient déjà appelables en HTTP — mais à `/api/graphs/{uuid}/call`, une
adresse que personne ne peut lire, retenir ni documenter. L'idée à retenir des
plateformes d'intégration n'est pas la plateforme : c'est qu'une API **se
déclare** avant d'être consommée.

Trois choses, et **aucun nouveau stockage** pour aucune d'elles.

**L'adresse est celle de l'artefact.** `/api/run/rh/commandes-partenaire` se
résout par environnement et par nom — un couple déjà unique. Un registre de
publication serait une chose de plus à maintenir en accord avec la réalité.

**Le contrat vient du graphe.** Les paramètres portaient déjà un nom, une valeur
par défaut et leur caractère obligatoire ; ils portent maintenant un **type**,
une description et un exemple. Cela suffit à produire un document OpenAPI —
donc il est **généré**, jamais écrit à côté et laissé diverger.

**La version s'épingle dans l'appel.** `?version=3` sert cette version pour
toujours, quoi qu'il advienne du flux ensuite.

### Le contrat est vérifié à la porte

Un type déclaré sert à refuser tôt : un appelant qui envoie du texte là où un
nombre est attendu l'apprend **à l'entrée**, pas trois briques plus loin dans un
message sur une expression échouée. Un paramètre inconnu est **nommé** plutôt
qu'ignoré — l'ignorer en silence, c'est la façon dont on passe un après-midi à
se demander pourquoi son paramètre n'a aucun effet.

Et un appel de production passe par **le même runner et le même journal** que
l'éditeur : il apparaît dans le tableau d'exploitation et se rejoue. C'est ce qui
maintient identiques « ce qui a été testé » et « ce qui est servi ».

### La brique `http` : la boucle est fermée

Un flux pouvait lire une API, contrôler la donnée… et n'avait nulle part où la
pousser. La brique `http` poste les lignes vers un endpoint, en une requête ou
par lots.

- **Les lots sont rapportés, pas masqués.** Un échec au quatrième lot sur dix
  signifie que six sont passés ; prétendre que l'appel est atomique rendrait ça
  impossible à raisonner. L'erreur nomme le lot et ce qui avait déjà été accepté.
- **Aucun réessai.** Un réessai silencieux rend un flux non raisonnable — le
  doublon vient-il de nous ? Rejouer est un acte explicite, et le tableau
  d'exploitation le propose déjà.

### Endpoints

| Méthode | Route | Rôle |
|---|---|---|
| `GET` | `/api/run/{env}` | Les flux appelables, avec adresse et contrat |
| `GET` | `/api/run/{env}/{nom}/openapi.json` | La spécification, générée |
| `POST` | `/api/run/{env}/{nom}` | Appeler le flux (`?version=` pour épingler) |

---

## Ce que le premier lancement a révélé (v34)

L'interface n'avait jamais tourné. Un navigateur Chromium livré par un paquet
npm (`@sparticuz/chromium`, piloté par `playwright-core`) a permis de la lancer
et de la regarder pour de vrai. Trois défauts en sont sortis, dont deux
bloquants — et aucun n'était détectable par la compilation.

**Les modules sans fichier n'avaient pas de porte d'entrée.** Tant qu'aucun
fichier n'était chargé, l'écran d'accueil ne proposait que *Flux* et *EDI* :
Canevas, Tables, Mapping, Exploitation et **Administration** étaient
inatteignables autrement qu'en tapant l'URL. Chaque module accessible sans
fichier a maintenant son entrée, et la liste passe par le même `gate()` que la
barre d'onglets.

**Le bouton Administration était invisible pendant l'installation.** En
installation neuve, l'identité vaut `null` — donc `me?.setup_mode` était faux et
le bouton ne s'affichait pas, précisément au moment où il est indispensable. La
porte d'entrée transmet désormais une identité d'installation marquée
superadmin, ce que le serveur fait déjà de son côté.

**La barre latérale de chargement occupait un tiers de l'écran** même en
Administration, en Exploitation ou sur le canevas — des modules qui ne touchent
jamais un fichier. Ils prennent la pleine largeur.

### Ce qui reste à polir

- **L'interface mélange le français et l'anglais** : la barre latérale et les
  panneaux anciens sont en anglais, les écrans récents en français.
- Les polices viennent de Google Fonts : derrière un proxy d'entreprise, la
  typographie retombera sur les polices système.

---

## La console d'administration (v33)

### Une vue d'ensemble, pas cinq écrans

Une console faite uniquement d'écrans séparés oblige son utilisateur à garder
l'image en tête : combien de personnes dans RH, est-ce qu'ADV a une
configuration imposée, quel environnement personne n'a encore rejoint. L'onglet
**Aperçu** assemble cette image une fois — les autres onglets deviennent des
endroits où l'on va **changer** quelque chose, plus des endroits où l'on va
**découvrir** l'état.

Par environnement : l'adresse, les modules (restreints ou tous), les membres par
rôle, le contenu (configs, tables, clés), et les exécutions en erreur. Plus, en
dépliant : la liste des modules avec ce qui est masqué barré, et les membres avec
l'origine de leur rôle.

Un environnement **que personne n'a rejoint apparaît quand même** — c'est
précisément celui qu'on veut voir dans une console.

### La politique, affichée telle quelle

Un tableau croisé droits × rôles, alimenté par la même table que celle qui garde
les routes. Un nom de rôle n'a pas à être interprété : on lit ce qu'il autorise.

### L'adresse comme lieu

L'environnement et l'onglet vivent désormais dans l'URL : `/?env=rh#report`. Ce
n'est pas décoratif — un administrateur veut donner à quelqu'un **l'adresse d'un
endroit**, pas une suite de clics à reproduire. L'état est lu au démarrage, et
réécrit à chaque changement ; le bouton retour du navigateur continue de
fonctionner. Aucune bibliothèque de routage n'a été ajoutée pour ça.

### Endpoint

| Méthode | Route | Rôle |
|---|---|---|
| `GET` | `/api/admin/overview` | Toute l'image : environnements, membres, comptes, politique, SSO |

Réservé à l'administrateur général.

---

## La brique `config` : rejouer une configuration (v32)

Une brique qui applique une configuration stockée aux données qui la traversent
— **exactement comme appuyer sur *Valider* dans l'interface**. Les valeurs à
nettoyer sont nettoyées, celles à vérifier sont vérifiées, et ce sont les
**valeurs nettoyées** qui poursuivent leur chemin.

### C'est ça qui rend les configurations composables

L'enchaînement n'est donc pas un mécanisme à part : c'est deux briques à la
suite. Une **adaptation** remet en forme le fichier d'un partenaire, puis le
**contrat** juge le résultat. Trois se composent aussi bien que deux.

La règle : **le dernier de la chaîne juge, ceux d'avant préparent.** Une
adaptation a le droit de modifier des valeurs ; un contrat constate.

```yaml
nodes:
  - {id: src,   type: api,    config: {url: "…"}}
  - {id: adapt, type: config, config: {name: "adaptation-partenaireA"}}
  - {id: ctl,   type: config, config: {name: "contrat", on_error: block}}
  - {id: bdd,   type: dataset_write, config: {name: salaries}}
```

Et le jour où le partenaire corrige ses en-têtes, on retire la brique
d'adaptation. Le contrat n'a jamais bougé.

### Un bug que cette brique a mis au jour

Le moteur de nettoyage **saute silencieusement** une colonne déclarée mais
absente. Conséquence : un fichier partenaire, passé directement dans le contrat,
ressortait **sans aucune erreur** — alors qu'aucune de ses colonnes ne
correspondait. Rien n'avait été vérifié, et rien ne le disait.

La brique refuse donc désormais une configuration dont des colonnes déclarées
sont absentes, en les nommant. `require_columns: false` permet de tolérer des
colonnes optionnelles — mais c'est un choix explicite, et elles restent
signalées.

### Options

| Clé | Rôle |
|---|---|
| `config_id` / `name` | La configuration, par identifiant **ou par nom** (un flux écrit à la main se lit mieux) |
| `version` | Épingler une version |
| `tco_id` | La table de correspondance, pour un `tco_replace` |
| `computed` | `{colonne: expression}` dérivées après le nettoyage |
| `on_error` | `keep` (défaut, garder et signaler) · `drop` · `block` |
| `require_columns` | `true` par défaut |

Le défaut `keep` est délibéré : garder et signaler est sûr, écarter en silence
est la façon dont un flux perd discrètement un dixième de son entrée.

---

## La filiation des configurations (v31)

Dériver une configuration plutôt que la réécrire : on part du contrat, on
l'ajuste pour une source, on enregistre **sous un autre nom**. L'ancêtre n'est
jamais touché — un nom différent est un artefact différent, donc il n'existe
aucun chemin pour l'écraser, même par erreur.

Ce qui manquait n'était pas le geste (il existait déjà) mais **le lien**. Une
bibliothèque de trente configurations est illisible sans lui : savoir que
`contrat-partenaireA` descend de `contrat` fait la différence entre une famille
et un tas.

### Ce qui est enregistré, et pourquoi

`derived_from` **et** `derived_from_version`. La version exacte compte : un
ancêtre continue d'évoluer, et « dérivé de la v1 » est un fait qui reste vrai.
La filiation montre donc aussi que l'ancêtre est passé en v3 depuis — ce qui est
l'information utile pour décider de reprendre les changements ou non.

### Trois refus délibérés

- **Dériver d'un autre type est refusé.** Tirer une config d'un mapping
  produirait un objet que personne ne sait interpréter.
- **Un ancêtre inconnu est refusé à la création**, plutôt que d'enregistrer un
  lien mort.
- **Un ancêtre supprimé est signalé**, pas caché : « dérivé de quelque chose qui
  n'existe plus » mérite d'être su, donc la chaîne le dit au lieu de s'arrêter
  en silence.

Et la remontée est bornée : un cycle devrait être impossible — on ne dérive que
d'un artefact déjà existant — mais une ligne corrompue ne doit pas faire tourner
la boucle indéfiniment.

### Endpoint

| Méthode | Route | Rôle |
|---|---|---|
| `GET` | `/api/artefacts/{kind}/{id}/lineage` | Ascendants et descendants |

La création accepte `derived_from` et `derived_from_version`. Aucun droit
nouveau : dériver une configuration a exactement le même pouvoir que l'écrire,
c'est donc `config.write` — niveau éditeur.

---

## Administration et bac à sable (v30)

### Deux défauts trouvés en auditant l'interface

**`shows()` n'était appliqué qu'à un onglet sur treize.** La fonction qui masque
les modules selon le profil d'environnement existait, mais tout le travail de la
v23 était **décoratif** : les treize onglets s'affichaient quoi qu'il arrive. Le
masquage passe désormais par un helper `gate()` utilisé pour chacun — un onglet
ajouté plus tard ne peut plus oublier.

**Il n'existait aucun panneau d'administration.** Les routes `/api/admin/*`,
`/api/environments/*/profile` et `/api/keys` n'avaient pas d'interface : aucun
moyen de créer un compte, d'attribuer un rôle, de créer un environnement ou de
configurer le SSO sans passer par l'API.

### Le bac à sable

Concevoir des rôles à l'aveugle, c'est se retrouver avec un opérateur qui ne
peut pas travailler, ou un lecteur qui peut. **Essayer est la seule vérification
fiable**, donc :

- **Un compte et ses rôles en un appel.** Créer un utilisateur de test prenait
  trois allers-retours (créer, puis une appartenance par environnement) ; pour
  itérer sur une conception, cette friction est la différence entre vérifier et
  supposer. Un compte existant est mis à jour plutôt que refusé, parce
  qu'itérer signifie changer le même rôle plusieurs fois.
- **Un environnement qui n'existe pas est créé de fait** par l'appartenance :
  rien à préparer pour essayer.
- **Emprunter une identité** pour voir ce qu'un rôle montre réellement.

### L'emprunt d'identité, et ses trois limites

C'est une fonctionnalité sensible, donc bornée :

- **Superadmin uniquement**, et **jamais sur un autre superadmin** : emprunter
  une identité ne doit pas être un chemin vers plus de pouvoir qu'on n'en a.
- **Enregistré sur la session**, pas déduit : chaque requête sait qu'elle agit
  pour le compte de quelqu'un, et l'interface ne peut pas oublier de le dire.
  Un bandeau orange le rappelle en permanence, avec un bouton pour reprendre son
  identité.
- **Une heure**, parce qu'une session empruntée oubliée est indiscernable d'une
  usurpation dans un journal.

### La liste des modules

L'écran *Environnements & modules* affiche les treize modules en cases à cocher.
Ce qui n'est pas coché n'apparaît pas pour les personnes travaillant là — et un
bouton remet tout à zéro. L'onglet **Admin** lui-même n'est jamais masqué par un
profil : s'enfermer dehors demanderait une base de données pour en sortir.

### Ce que l'interface sait maintenant demander

`/api/auth/state` renvoie les **capacités par environnement**. L'interface
demande ce que la personne peut faire au lieu de le déduire d'un nom de rôle —
la même table de politique côté serveur, lue par l'écran.

---

## Droits par table et validation ligne à ligne (v29)

### Les tables ne sont pas interchangeables

Un rôle d'environnement dit ce qu'on peut faire *en général* ; il ne peut pas
dire ce qu'on peut faire *sur cette table-là*. Or un référentiel métier
maintenu par une équipe et un brouillon que quelqu'un s'est fabriqué ne peuvent
pas raisonnablement partager une seule règle.

Chaque table porte donc ses propres droits — `read`, `write`, `manage` —
accordés à **une personne** ou à **un rôle**. Le second passe à l'échelle, le
premier gère l'exception que tout déploiement réel finit par rencontrer.

L'ordre de résolution, et pourquoi chaque règle existe :

1. **Le propriétaire garde `manage`** — construire une table et en perdre
   l'accès serait absurde.
2. **Un admin d'environnement a `manage`** — sinon une table peut survivre à
   tous ceux capables de l'administrer.
3. **Les autorisations explicites**, par personne puis par rôle.
4. **Le défaut** : une table personnelle est privée, une table métier est
   lisible par l'environnement. C'est un **drapeau posé à la création**, pas une
   convention — le choix est délibéré.

La liste des tables est filtrée côté serveur : une table qu'on ne peut pas lire
n'apparaît pas. Les noms sont rarement neutres (`licenciements_2026`).

### Trois droits distincts, et non un seul

- **Créer** une table (`dataset.create`, niveau éditeur) n'est pas **remplir**
  une table (`dataset.write`, niveau opérateur). Une équipe RH alimente les
  tables que le métier a définies ; elle n'en invente pas.
- **Partager** demande `manage` : sinon quiconque a le droit d'écrire pourrait
  élargir l'accès indéfiniment.

### Valider en masse ou une par une

L'écriture accepte `include_rows` / `exclude_rows` (index de lignes). On revoit
dans Data, on coche ce qu'on approuve, et seul cela part — une par une, ou tout
d'un coup.

**La sélection s'applique après la validation** : cocher une ligne en erreur ne
la blanchit pas. Approuver, c'est choisir parmi ce qui est valide, jamais passer
outre une règle.

### Endpoints

| Méthode | Route | Rôle |
|---|---|---|
| `GET` | `/api/datasets` | Les tables accessibles, avec `my_permission` |
| `GET` | `/api/datasets/{id}/grants` | Qui a quoi sur cette table |
| `POST` | `/api/datasets/{id}/grants` | Partager (à une personne ou à un rôle) |
| `DELETE` | `/api/datasets/{id}/grants/{sujet}` | Retirer un accès |

L'écriture accepte `managed: true` à la création pour déclarer une table métier.

---

## Charger un fichier dans une table existante (v27)

Le pipeline savait nettoyer et contrôler n'importe quoi — mais seulement contre
une configuration écrite pour ce fichier-là. Charger la liste d'un partenaire
dans une table de correspondance obligeait donc à renommer les colonnes dans
Excel d'abord, et se passait de tout contrôle.

### Deux couches, et il ne faut pas les confondre

**La configuration d'entrée décrit le traitement** : nettoyer le fichier,
appliquer les règles métier, dériver des colonnes à partir d'autres colonnes.
C'est le même objet configuration que partout ailleurs — en ligne, ou pris dans
la bibliothèque — parce qu'un fichier partenaire mérite le même soin qu'il
finisse dans une table ou dans un export.

**Le schéma de la cible a le dernier mot** : forme, types, colonnes
obligatoires, clé. Il ne sait exprimer ni un format de date, ni un remplacement
TCO, ni une colonne calculée — il ne peut donc **jamais** être la spécification.
C'est la garantie que ce que le traitement a produit rentre encore. Une
contrainte de base de données est un filet de sécurité, pas une conception.

Les deux s'enchaînent : la config transforme, la cible vérifie. Le rapport
distingue les deux étapes (`entrée` / `cible`), parce que ce sont deux problèmes
différents à corriger : une règle métier qui échoue veut dire que le fichier est
faux, la cible qui refuse veut dire que le traitement ne rentre pas.

Une colonne calculée devient mappable comme n'importe quelle autre — c'est ainsi
qu'un `TARGET_LABEL` peut valoir `CONCAT([Prenom], ' ', [Nom])`. Les expressions
se voient entre elles dans l'ordre de déclaration, donc l'une peut s'appuyer sur
la précédente.

Aucun moteur n'a été écrit : le nettoyage passe par le moteur de nettoyage, les
expressions par le moteur d'expressions, les contrôles par le moteur de
validation. Un endroit de plus où on les atteint.

### Le mapping est proposé, pas exigé

Les en-têtes sont rapprochés sur une forme repliée — accents, casse et
séparateurs ignorés — pour que « Source value » rencontre `SOURCE_VALUE` sans
que personne ne tape quoi que ce soit. Ce qui n'a **pas** été rapproché est
signalé (`unmatched_target`, `unused_file`) plutôt que chargé en blanc : on
complète avant, pas après.

### Ce qui est refusé, et pourquoi

- **Une colonne absente du fichier** : l'erreur nomme la colonne *du fichier*,
  celle que la personne a tapée et peut aller vérifier — pas la colonne cible.
- **Un chargement partiel** en mode `block` : rien n'est écrit. Une table
  partagée à moitié chargée est pire que pas chargée, puisque personne ne peut
  dire quelle moitié.
- **Une colonne confidentielle** ne peut pas être versée dans une table
  partagée par ce chemin : le chargement ne doit pas être un contournement de
  la marque posée par la configuration du fichier.
- **Les clés en double** sont annoncées **avant** l'écrasement — une
  correspondance qui change toute seule se remarque trop tard.

### Le chargement d'un TCO crée une version

Jamais une modification en place : l'exécution qui utilisait la table
précédente reste reproductible, comme pour tout artefact.

`rules` reste disponible pour un ajustement ponctuel sur une colonne cible,
quand cela ne mérite pas une configuration entière.

### Endpoints

| Méthode | Route | Rôle |
|---|---|---|
| `GET` | `/api/targets` | Ce dans quoi on peut charger, avec le schéma imposé |
| `POST` | `/api/targets/{kind}/{id}/suggest` | La correspondance de colonnes proposée |
| `POST` | `/api/targets/{kind}/{id}/preview` | Ce qui atterrirait, et ce qui cloche |
| `POST` | `/api/targets/{kind}/{id}/load` | Charger |

Cibles : `tco` (schéma fixe) et `dataset` (schéma enregistré). Compléter demande
la capacité `tco.append` — niveau opérateur, cohérent avec le cas RH.

---

## Les capacités : qui a le droit de quoi (v26)

`editor` voulait dire deux métiers différents — *éditer une configuration* et
*traiter un fichier*. Une équipe RH doit faire le second sans pouvoir faire le
premier : elle exécute des contrôles, elle ne réécrit pas les règles contre
lesquelles ses fichiers sont vérifiés.

### Des capacités nommées, dans une table lisible

Semer des `require_role("editor")` dans les routes a deux défauts. La politique
devient illisible — répondre à « que peut faire un opérateur ? » demande de
fouiller le code en espérant n'avoir rien manqué. Et le sens dérive, jusqu'à ce
qu'un rôle recouvre deux métiers.

Chaque route déclare donc **ce qu'elle fait** (`config.write`, `file.process`,
`tco.append`), et une seule table dit quel rôle minimum chacune exige. Changer
qui peut modifier les configurations est une ligne à éditer, pas une chasse à
travers les endpoints. Cette table est relisible par quelqu'un qui ne lira
jamais le code, et exposée par l'API pour un écran de réglages.

L'échelle reste à **quatre barreaux** : `viewer` → `operator` → `editor` →
`admin`. Quatre niveaux compréhensibles valent mieux que vingt cases que
personne ne configure correctement — la finesse est dans les capacités, pas
dans les rôles.

### Le découpage, et pourquoi

| Capacité | Rôle mini | Raison |
|---|---|---|
| `file.process`, `file.edit_cells` | operator | Faire le travail |
| `tco.append` | **operator** | Compléter une correspondance manquante fait partie du travail |
| `tco.replace` | editor | Remplacer la table entière relève de la conception |
| `config.write`, `mapping.write`, `flow.write` | editor | Concevoir le traitement |
| `flow.run`, `flow.replay` | **operator** | Exécuter n'est pas concevoir |
| `config.delete`, `dataset.delete` | **admin** | Un artefact partagé survit à qui l'a écrit |
| `members.manage`, `keys.create`, `env.profile` | admin | Administrer |

Deux nuances qui font tout le cas RH : **compléter** une table de correspondance
est du niveau opérateur — c'est le geste dont dépend tout le parcours de la v23 —
alors que la **remplacer** est du niveau éditeur. Et **exécuter** un flux
qu'un autre a construit reste opérateur.

### Deux règles de sûreté

**Une capacité inconnue est refusée**, jamais accordée : une faute de frappe
dans une garde doit échouer fermé.

**Détenir une clé n'est pas une capacité.** Ça dépend de la liste des
détenteurs, pas d'un rôle — donc être administrateur n'implique jamais de
pouvoir lire les rémunérations. Les deux axes restent séparés délibérément.

### Le contournement, fermé

Garder la création d'artefact ne suffisait pas : **ajouter une version, c'est
modifier**. Un opérateur pouvait réécrire une configuration en lui empilant une
version au lieu d'en créer une. Les deux portes sont désormais gardées par la
même règle.

---

## Colonnes confidentielles (v25)

Deux mécanismes, et il importe de savoir lequel fait quoi.

### La propagation porte l'essentiel

Un salaire peut être parfaitement chiffré et fuir quand même : par une colonne
calculée `[salaire] * 12`, par une moyenne par personne, par une ligne de
rapport qui cite la valeur fautive, par un fichier généré. Donc **une colonne
dérivée d'une colonne confidentielle devient confidentielle**, automatiquement,
et la marque suit les chaînes : `b` depuis `a`, `c` depuis `b`.

Cette moitié fonctionne **sans aucune cryptographie** et vaut déjà à elle seule.

### Pourquoi chiffrer quand même

L'adversaire ici n'est pas un vol de sauvegarde : c'est un utilisateur légitime
de l'application, membre de l'environnement, qui ne doit pas lire une colonne.

Ce qui justifie le chiffrement, c'est le **nombre de sorties** qu'a une valeur :
aperçu, rapport, export, suggestion de TCO, instantané de rejeu, journal, table,
pivot, fichier produit par une brique, réponse JSON d'un flux exposé en API. Dix
chemins — et le onzième qu'on ajoutera. Masquer correctement partout est une
discipline qui finit par céder ; ce projet en porte la preuve, puisqu'en v22 les
secrets masqués dans le champ message ressortaient par les métadonnées du nœud.

Avec le chiffrement, **le chemin qu'on oublie crache du charabia**, pas un
salaire. Il échoue fermé au lieu d'échouer ouvert.

### Ce que ça ne protège pas

Le serveur déchiffre pour nettoyer, calculer et contrôler : il détient donc la
capacité de lire. Ce dispositif protège contre un utilisateur autorisé sur
l'environnement mais pas sur la colonne, contre un chemin de sortie oublié, et
contre un vol de base **si** la clé-maîtresse vit ailleurs. Il ne protège pas
contre quelqu'un qui contrôle le serveur.

### La clé-maîtresse vit hors base

`FX_MASTER_KEY`, dans l'environnement ou un coffre. Les clés de données sont
stockées **enveloppées** par elle : un dump de la table `crypto_keys` ne sert à
rien seul. Sans clé-maîtresse, la création de clé **refuse** au lieu de dégrader
silencieusement — l'option est grisée dans l'interface plutôt que de promettre
une protection que l'installation ne peut pas tenir.

### Les détenteurs sont la liste d'accès

Pas un rôle de plus : savoir **qui, nommément**, peut lire les rémunérations se
raisonne mieux qu'une permission diluée dans une hiérarchie, et s'explique en
une phrase à qui le demande.

- La créatrice en est la première détentrice — une clé que personne ne détient
  ne fait que détruire de la donnée.
- **Seul un détenteur ajoute un détenteur.** Passer par un administrateur
  permettrait à quelqu'un de s'accorder l'accès à une colonne qu'on ne lui a
  jamais confiée.
- Un superadmin n'a **pas** de passe-droit implicite : sinon la liste ne serait
  qu'une décoration.
- Le **dernier détenteur ne peut pas être retiré** : le facteur bus, refusé
  d'avance plutôt que découvert trop tard.

### Dévoiler est un événement, pas un mode

Sur de la paie, la question n'est pas seulement qui *peut* regarder, mais qui *a*
regardé. Un dévoilement est donc tracé — qui, quelles colonnes, quel contexte,
combien de lignes — et rend les valeurs **pour cet appel**, sans basculer un
interrupteur. Un mode resté ouvert reste ouvert.

Le journal est lisible par les admins de l'environnement : une piste d'audit que
seul son sujet peut lire n'est pas une piste d'audit.

### Détruire une clé détruit la donnée

C'est une fonctionnalité — elle répond à une demande d'effacement en une action
— et c'est pourquoi elle exige le **nom de la clé** en confirmation. Ensuite,
toute configuration qui en dépend **refuse de s'exécuter** avec un message
explicite, au lieu d'écrire du charabia.

### Contraintes qui découlent du chiffrement

- Une colonne confidentielle **ne peut pas servir de clé de fusion** : le
  chiffrement est aléatoire, donc deux chiffrés de la même valeur diffèrent et
  la même personne ne se retrouverait jamais elle-même. Refusé explicitement.
- La liste des valeurs non mappées **cesse de citer** les valeurs
  confidentielles ; les comptes restent, puisqu'ils restent utiles.
- Une valeur vide reste vide : masquer l'*absence* d'une valeur n'apporte rien
  et casserait tous les contrôles de nullité.

### Endpoints

| Méthode | Route | Rôle |
|---|---|---|
| `GET` | `/api/keys/status` | Le chiffrement est-il possible ici |
| `GET` `POST` | `/api/keys` | Lister / créer une clé |
| `POST` `DELETE` | `/api/keys/{id}/holders[/{user}]` | Gérer les détenteurs |
| `DELETE` | `/api/keys/{id}?confirm=nom` | Détruire la clé et ce qu'elle protège |
| `POST` | `/api/keys/reveal` | Déchiffrer pour cet appel, et le tracer |
| `GET` | `/api/keys/reveals` | Qui a regardé quoi |

Une colonne devient confidentielle par `sensitive: <nom-de-clé>` dans la
configuration du champ.

---

## Identité : comptes internes et SSO (v24)

### Une identité, plusieurs façons de la prouver

L'erreur classique serait de construire l'authentification interne, puis de
greffer le SSO à côté : deux chemins de connexion, deux façons d'attribuer les
rôles, et une faille dans celui qu'on teste le moins.

Ici un **utilisateur existe une fois**. Un mot de passe et une identité SSO sont
deux *preuves* rattachées au même compte, et les rôles vivent au même endroit
dans les deux cas. Quelqu'un qui se connecte en SSO avec une adresse déjà connue
**récupère son compte et ses droits** au lieu d'en créer un doublon vide.

### Ce qui change vraiment : `?env=` cesse d'être déclaratif

Jusqu'ici, l'environnement était un paramètre d'URL : taper `?env=adv` suffisait
à lire le matériel d'un autre service. Cacher des onglets évitait les erreurs de
manipulation, **pas les accès**.

Désormais une garde résout l'environnement depuis la **session**, vérifie
l'appartenance, et **refuse** sinon — plutôt que de retomber silencieusement sur
l'environnement par défaut, ce qui masquerait la maladresse et, pire, la
tentative.

### Rôles, par environnement

`viewer` → `operator` → `editor` → `admin`, en échelle. Par environnement et non
globalement, parce que c'est ainsi que le travail s'organise : la même personne
peut traiter des fichiers dans RH et seulement lire dans ADV. Un rôle global
imposerait partout le plus grossier des deux.

Une échelle de quatre niveaux compréhensibles plutôt qu'une grille de vingt
cases que personne ne configure correctement.

### Pas de trou d'amorçage

Tant qu'aucun compte n'existe, l'application reste **ouverte**, et le premier
compte créé devient administrateur général. Elle se ferme d'elle-même à
l'instant où ce compte existe.

Livrer verrouillé derrière un mot de passe par défaut serait pire : les mots de
passe par défaut survivent au déploiement qui les a posés.

### L'entreprise gère elle-même

**Administration déléguée.** L'admin d'un environnement gère les membres du
sien, sans pouvoir toucher aux autres. Sinon chaque demande d'accès remonte au
détenteur du compte superadmin, qui devient le goulot d'étranglement dès la
troisième équipe.

**Le SSO configuré depuis l'application**, pas depuis des variables
d'environnement : un client branche son propre IdP sans redéploiement, et l'URL
de découverte remplit les points d'entrée pour qu'il colle une adresse au lieu
de quatre.

**Les groupes de l'annuaire deviennent des droits ici.** Une règle
`{group: RH-SAISIE, environment: rh, role: editor}` suffit. Les accès accordés
ainsi sont marqués `from_sso` et **réappliqués à chaque connexion** : retirer
quelqu'un d'un groupe lui retire réellement l'accès. C'est le mode d'échec qui
rend une intégration SSO dangereuse plutôt qu'utile — un droit qui reste après
la révocation.

Corollaire assumé : un droit venu du SSO **n'est pas modifiable à la main**
(409). La connexion suivante annulerait la modification en silence ; autant le
refuser franchement et renvoyer vers l'annuaire.

### Sessions

Jeton opaque, aléatoire, dont seul le **hachage** est stocké : un vol de la table
ne se rejoue pas en connexion, même raisonnement que pour les mots de passe. Le
serveur détient le sens du jeton, donc **révoquer est une suppression**, pas une
attente d'expiration.

### Limites, à connaître avant de déployer

- **La signature des `id_token` OIDC n'est pas vérifiée.** Le jeton n'est accepté
  que lorsqu'il vient directement du *token endpoint* en TLS — le seul chemin
  qui s'en dispense légitimement. Un déploiement acceptant des jetons d'ailleurs
  doit d'abord valider la signature via le JWKS de l'émetteur (rotation de clés
  et dérive d'horloge comprises). C'est signalé dans le code, à l'endroit exact.
- Pas de MFA, pas de politique d'expiration de mot de passe, pas de
  réinitialisation en libre-service.
- Le profil d'environnement (v23) **cadre** l'interface ; c'est cette couche-ci
  qui **contrôle** l'accès. Les deux se composent : ce qu'on voit = ce que
  l'environnement propose ∩ ce que la personne a le droit de faire.

---

## Déployer à une équipe : les profils d'environnement (v23)

Cloisonner la bibliothèque (v20) décidait ce qu'un environnement **possède**. Un
profil décide ce qu'il **expose** : quels modules apparaissent, quelle
configuration est imposée, ce qui reste modifiable, quels boutons sont offerts.

C'est la différence entre « un outil pour soi » et « un outil qu'on déploie ».
Une équipe data veut tous les modules et la main sur la config ; une équipe RH
veut un écran, une configuration qu'elle ne peut pas casser, et deux boutons.
**C'est la même application — seul le profil change.**

### Créer un environnement en quelques clics

Trois gabarits :

| Gabarit | Modules | Config | TCO |
|---|---|---|---|
| `complet` | tous | libre | modifiable |
| `controle_simple` | `data`, `report`, `tco` | **imposée** | modifiable |
| `consultation` | `data`, `report` | **imposée** | verrouillé |

`controle_simple` est le profil RH/ADV : déposer un fichier, le contrôler contre
une configuration imposée, lire le rapport, compléter la table de
correspondance. La liste de modules est **volontairement courte** — ce qui n'est
pas affiché ne peut pas être cassé, et une personne à qui on demande d'ignorer
neuf onglets finira par en cliquer un.

Deux refus délibérés : un gabarit qui verrouille **exige** la configuration
qu'il épingle, et verrouiller sans configuration est rejeté — sinon on livre un
cul-de-sac. Et un environnement **sans** profil continue de tout montrer : ceux
créés avant les profils fonctionnent à l'identique.

### Le chemin de l'erreur de mapping

C'est le cas d'usage réel. Une valeur absente de la table de correspondance
n'est **pas** une erreur de donnée : le fichier est bon, la table est
incomplète. Le rapport le distingue, et propose les lignes à compléter.

Le **type** est pré-rempli depuis la configuration du champ — il y est déjà
déclaré, et le demander une seconde fois est la façon dont deux sources de
vérité commencent à diverger. Seul le libellé cible reste à saisir : la seule
chose que la machine ne peut pas deviner.

L'ajout crée une **nouvelle version** du TCO, jamais une modification en place :
l'exécution qui utilisait la version précédente reste reproductible. Une ligne
sans cible est refusée — un mapping vers rien est précisément l'erreur qu'on
corrige.

La table de correspondance accepte désormais une colonne **`TYPE`** optionnelle,
ce qui permet à une seule table de servir plusieurs champs (`CIVILITE` et `PAYS`
dans le même fichier) au lieu d'une table par champ. Absente, la table
s'applique à tout : les TCO existants sont inchangés.

### Les boutons d'action

Un profil porte une liste de boutons `{label, graph_id, params, confirm}`.
Chacun appelle un flux via `POST /api/graphs/{id}/call` — la route qui existait
déjà. Aucune machinerie supplémentaire : un bouton **est** un appel de flux.

### Endpoints

| Méthode | Route | Rôle |
|---|---|---|
| `GET` | `/api/environments/templates` | Les gabarits offerts |
| `POST` | `/api/environments` | Créer depuis un gabarit |
| `GET` `POST` | `/api/environments/{nom}/profile` | Lire / modifier le profil |
| `DELETE` | `/api/environments/{nom}/profile` | Revenir à « tout afficher » (les artefacts sont intacts) |
| `POST` | `/api/environments/tco/suggest` | Les valeurs non mappées, en lignes à compléter |
| `POST` | `/api/environments/tco/append` | Enrichir la table (nouvelle version) |

### Limite assumée

Un profil **cadre** l'interface, il ne l'**authentifie** pas : quelqu'un qui
appelle l'API directement n'est pas contraint par les modules cachés. C'est un
outil de cadrage d'usage, pas un contrôle d'accès — l'authentification reste
hors périmètre, comme le multi-utilisateur depuis la v12.

---

## Points de connexion et tableau d'exploitation (v22)

Deux moitiés d'une même question d'exploitant : **ce qu'une exécution utilise**,
et **ce qu'elle est devenue**.

### Les points de connexion

Une variable réutilisable — une URL de base, un seuil, une clé. Ce qui la rend
utile plutôt que simplement pratique, c'est sa **portée** : le même nom se
résout différemment selon l'endroit où il est lu, **le plus spécifique
l'emportant** :

```
brique  →  flux  →  environnement  →  global
```

`api_base` peut donc être globale par défaut, redéfinie pour l'environnement RH,
redéfinie encore pour un flux qui tape sur un bac à sable, et une dernière fois
pour une seule brique. **Rien n'a besoin d'être renommé pour être spécialisé.**

Une portée étroite doit dire ce qu'elle restreint : une variable de flux sans
flux est refusée, sinon elle se comporterait comme une globale sans le dire.
Symétriquement, demander la vue « niveau flux » ne montre **pas** la surcharge
d'une brique — sinon on croirait qu'elle s'applique partout.

Les variables se substituent dans les configs exactement comme les paramètres,
donc **aucune brique n'a la moindre notion de « variable »**. Un paramètre
d'appel l'emporte sur une variable stockée : un argument explicite est plus
spécifique que n'importe quelle valeur enregistrée.

**Les secrets** (`secret: true`) sont masqués partout où on les relit :
listings, messages, erreurs, métadonnées d'étape et instantanés. Le masquage est
**récursif** — masquer le seul champ évident ne suffit pas, puisqu'une clé
substituée dans une URL revient dans les métadonnées du nœud, c'est-à-dire dans
le journal même censé aider à déboguer.

### Le tableau d'exploitation

Toute exécution est journalisée : celles lancées depuis l'éditeur comprises. Un
tableau qui ne verrait que les exécutions planifiées manquerait précisément
celles qu'on est en train de déboguer.

La ligne est écrite **avant** le démarrage, en statut `running` : un flux qui
bloque ou meurt en route reste visible plutôt que de disparaître sans trace. Et
**un échec est conservé comme un succès** — un tableau qui ne listerait que les
réussites serait pire que rien, puisque les exécutions qu'on cherche sont celles
qui ont échoué.

Chaque run donne : statut, durée, lignes produites, brique fautive, paramètres
utilisés, **messages** émis par les briques `log`, et le détail par étape (type,
durée, volume, métadonnées).

### Le rejeu, deux sens différents

C'est la distinction qui compte, et elle impose de capturer ce que les **sources**
ont produit :

- **`same_data`** — les sources sont réalimentées par l'instantané : l'API n'est
  **pas** rappelée, le trigger n'est pas revérifié, et l'exécution qui a échoué
  est reproduite à l'identique. C'est ce qu'on veut pour valider un correctif
  contre la charge utile qui l'a cassé, surtout quand celle-ci n'existe plus en
  amont.
- **`refetch`** — les sources sont réexécutées pour de vrai : l'API est
  rappelée, le trigger réévalué. C'est ce qu'on veut quand la panne était
  passagère ou que la donnée amont a été corrigée depuis.

Seules les **sources** sont capturées : tout ce qui est en aval est du calcul
pur, et le recalculer est tout l'intérêt d'un rejeu. Un rejeu est lui-même
journalisé, et pointe le run dont il descend.

### Endpoints

| Méthode | Route | Rôle |
|---|---|---|
| `GET` | `/api/variables?env=&graph_id=` | Ce qui pourrait s'appliquer, du plus général au plus spécifique |
| `GET` | `/api/variables/resolved` | La cascade aplatie : ce qu'une brique verrait |
| `POST` | `/api/variables` | Créer / mettre à jour |
| `GET` | `/api/ops/runs?status=error` | Le tableau d'exploitation |
| `GET` | `/api/ops/runs/{id}` | Détail : étapes, messages, erreur |
| `POST` | `/api/ops/runs/{id}/replay` | `same_data` ou `refetch` |

La brique **`log`** émet un message dans le journal et laisse passer les données
intactes : `{rows}` y est rempli avec ce qui a réellement transité.

---

## Fonctions, environnements, session→flux (v19–v21)

Trois ajouts qui touchent la même couche — la bibliothèque d'artefacts — et qui
partagent donc une seule migration.

### v19 — Le langage d'expression, étendu par ses utilisateurs

Une fonction est **une expression nommée avec des paramètres**, stockée comme
artefact versionné :

```yaml
name: prix_ttc
params: [montant, taux]
expr: "ROUND(NUM([montant]) * (1 + NUM([taux])), 2)"
```

Écrite une fois, elle est appelable partout où une expression est évaluée —
colonnes calculées, liens de mapping, briques `compute` et `filter`. Les endroits
qui évaluent des expressions n'ont eu **aucune modification à subir** : une
fonction *est* une expression.

Deux propriétés délibérées :

- **Les paramètres masquent les colonnes, ils ne fusionnent pas avec elles.**
  Dans un corps de fonction, `[montant]` est l'argument, jamais une colonne
  homonyme de la table appelante. Sans cette isolation, une fonction se
  comporterait différemment selon l'endroit où on l'appelle, ce qui ruinerait la
  réutilisation.
- **Les fonctions se composent, jusqu'à une profondeur.** Un cycle (`A` appelle
  `B` qui appelle `A`) échoue proprement au lieu de faire exploser la pile.

Une fonction qui ne compile pas est **refusée à l'enregistrement** : sinon toutes
les expressions de l'environnement se mettraient à échouer d'un coup.

### v20 — Les environnements

Une colonne `environment` sur les artefacts **et** sur les tables, plus un
sélecteur global. Un environnement RH et un environnement ADV peuvent chacun
avoir leur config « clients » sans se connaître : l'unicité passe de
`(kind, name)` à `(kind, name, environment)`.

**La règle de sûreté qui gouverne tout :** une requête qui *oublie* la portée
montre **moins**, jamais plus. Sans paramètre, on voit l'environnement `default`
— pas l'union de tous. Voir l'ensemble demande un `env=*` explicite. Une erreur
d'étourderie ne peut donc pas exposer les données d'un autre service.

Tout ce qui existait déjà atterrit dans `default` : une installation en cours
continue de fonctionner à l'identique.

### v21 — Ce qu'on a fait à la main devient un flux

Un bouton **« Save as flow »** dans Data : les gestes deviennent un graphe
exécutable, sauvé dans la bibliothèque.

La partie honnête de cette fonctionnalité est ce qu'elle **refuse** de reprendre.
Une validation et une colonne calculée sont de la **logique** : elles décrivent
un traitement et se rejouent sur n'importe quel fichier. Une cellule corrigée à
la main et une ligne supprimée sont de la **donnée** — vraies de ce fichier-là et
d'aucun autre. Les matérialiser en étapes de flux produirait un flux qui
corromprait silencieusement le fichier suivant. Elles sont donc **signalées comme
non reprises**, avec leur nombre, plutôt qu'intégrées.

Le flux généré part d'une source `session` — pratique pour le mettre au point.
Un paramètre permet de le faire partir d'une table à la place, ce qui le rend
durable : une session disparaît, une table non.

### Endpoints

| Méthode | Route | Rôle |
|---|---|---|
| `GET` | `/api/environments` | Les environnements existants |
| `GET` | `/api/artefacts/{kind}?env=rh` | Bibliothèque d'un environnement (`*` = tous) |
| `POST` | `/api/artefacts/function` | Une fonction (validée avant stockage) |
| `POST` | `/api/files/{sid}/to-flow` | La session courante, matérialisée en flux |

---

## Le canevas et les briques relationnelles (v18)

### Ce qui manquait vraiment

Tout ce que le moteur savait faire était **ligne à ligne** : `compute` transforme
une ligne, `filter` en garde une, `mapping` la renomme. Or l'essentiel du travail
réel est *relationnel* — et ces trois briques comblent le trou :

| Brique | Ce qu'elle fait | Ce qui la distingue |
|---|---|---|
| `aggregate` | Plusieurs lignes → une (somme, moyenne, min, max, comptage, distinct) | Change la **forme** du flux : c'est visible dans le graphe |
| `join` | Deux flux → un, sur des clés communes | `left`/`right` **nomment** leurs parents : redessiner deux arêtes ne change pas le sens |
| `lookup` | Enrichit depuis un référentiel | Ne change **jamais** le nombre de lignes ; une clé en double est signalée, pas multipliée |

`aggregate` sans `by` produit une ligne de totaux — le cas « chiffre global ».

### Un piège YAML désamorcé

En YAML 1.1, `on`, `off`, `yes` et `no` sont des **booléens**. Un `on: [client]`
parfaitement naturel dans une jointure arrivait donc sous la clé `True` et se
faisait rejeter. Les clés de config sont désormais normalisées : `on:` fonctionne,
`keys:` reste l'alias sans ambiguïté.

### Le canevas

Un onglet **Canvas** : palette à gauche (regroupée par rôle, code couleur
source/transformation/sortie), surface au centre, inspecteur à droite.

- **Poser** une brique : un clic dans la palette.
- **Relier** : le bouton `›` d'une brique, puis un clic sur la cible. Cliquer le
  point au milieu d'un câble le supprime.
- **Déplacer** : glisser, aimanté sur une grille de 10 px.
- **Configurer** : JSON dans l'inspecteur, appliqué en direct — un brouillon
  invalide est ignoré jusqu'à ce qu'il reparse, donc taper ne détruit jamais la
  config.
- **Exécuter** : chaque nœud affiche ensuite son volume et sa durée ; le nœud qui
  a échoué est mis en évidence, puisque le runner le nomme.
- **Basculer en YAML** à tout moment : le canevas est une *vue* du graphe, jamais
  une seconde source de vérité — les deux ne peuvent donc pas diverger.

Les positions sont enregistrées dans la config sous `_xy` : le runner les ignore,
mais un flux rouvert retrouve la disposition qu'on lui avait donnée.

---

## Flux visuels : des briques câblées (v17)

Un flux n'est plus un enregistrement à forme fixe (une config + un TCO + du
computed) mais un **graphe** : des nœuds qui font chacun une chose, des arêtes
qui disent qui alimente qui.

### Un graphe est un document, donc un artefact

Pas de nouvelle table, pas de migration : le graphe est stocké comme
`kind: graph` et hérite du versionnement immuable, de l'épinglage et de
l'archivage. Surtout, **un nœud peut être un graphe** — un flux qui a fait ses
preuves devient une brique dans un flux plus grand, avec ses propres paramètres.

### Deux propriétés qui font que ça compose

**Un seul format de câble.** Chaque brique consomme et produit les mêmes records
pivot que le reste de l'application. Une brique ne sait jamais quel type de
brique l'a alimentée — c'est précisément ce qui permet d'enchaîner n'importe
laquelle après n'importe quelle autre.

**Un graphe est un nœud.** Le nœud `graph` exécute un autre flux avec sa propre
portée de paramètres (isolation nécessaire : sans elle, deux flux imbriqués
utilisant le même nom de paramètre se marcheraient dessus, et la réutilisation
serait un piège au lieu d'une fonctionnalité).

### La palette

| Rôle | Briques |
|---|---|
| Sources | `api`, `dataset`, `session`, `inline` |
| Transformations | `mapping`, `compute`, `filter`, `validate`, `graph` |
| Sorties | `response`, `dataset_write`, `file` |

Ajouter une brique, c'est écrire **une fonction** de signature
`(node, inputs, ctx) -> records` et l'enregistrer. La traversée n'est jamais
touchée : c'est ce qui empêche le moteur de graphe de développer un cas
particulier par intégration.

### Un flux est déjà une API

C'est la conséquence, pas une fonctionnalité séparée : un graphe qui déclare des
`params` nommés et produit une sortie **est** une API. `POST
/api/graphs/{id}/call` fournit ces paramètres depuis le corps de la requête et
renvoie les records en JSON. Aucune machinerie de publication, aucun second
chemin d'exécution — la route est une coquille fine sur le runner de l'éditeur,
ce qui garantit que **ce qui est testé est exactement ce qui est servi**.

```yaml
name: commandes-partenaire
params: [{name: pays, default: "FR"}]
nodes:
  - {id: appel,  type: api,     config: {url: "https://…", path: "data.orders"}}
  - {id: tva,    type: compute, config: {columns: {ttc: "ROUND(NUM([montant]) * 1.2, 2)"}}}
  - {id: garder, type: filter,  config: {where: "IF([pays] == '{pays}', '1', '0')"}}
  - {id: forme,  type: mapping, config: {mapping_id: "…"}}
  - {id: ranger, type: dataset_write, config: {name: commandes, mode: replace}}
edges: [{from: appel, to: tva}, {from: tva, to: garder},
        {from: garder, to: forme}, {from: forme, to: ranger}]
```

### Ce qui est refusé à l'enregistrement

Un graphe qui ne peut pas tourner est rejeté **quand on le sauve**, pas quand on
l'appelle en production : cycles (l'erreur nomme les nœuds concernés), arêtes
pendantes, identifiants dupliqués, sortie ambiguë, type de brique inconnu.

### Ce qui rend un flux débogable

Chaque exécution renvoie une **trace par nœud** : durée, nombre de records, et
les métadonnées propres à la brique (`kept`/`dropped` pour un filtre, `written`
pour une écriture, `checks` pour un mapping). Et une brique qui échoue est
signalée **avec son identifiant** — un flux de dix nœuds qui échoue doit dire
*où*, sinon le déboguer revient à tout relire.

### Endpoints

| Méthode | Route | Rôle |
|---|---|---|
| `GET` | `/api/graphs/bricks` | La palette disponible |
| `POST` | `/api/graphs/validate` | Ordre d'exécution et cohérence, sans exécuter |
| `POST` | `/api/graphs/run` | Exécute, renvoie aperçu + trace |
| `POST` | `/api/graphs/{id}/call` | Le flux servi comme endpoint |

### Limites assumées

- La brique `api` est volontairement simple : pas de réessai (un réessai
  silencieux rendrait un flux non déterministe), pas de pagination (elle demande
  son propre contrat).
- Plusieurs parents alimentant un nœud **concatènent**. Une jointure est une
  autre opération et mérite sa brique, plutôt que d'être une surprise implicite.
- Imbrication limitée à 5 niveaux, 200 000 lignes par source.
- **L'éditeur visuel (canevas) n'est pas encore fait** : les flux se décrivent en
  YAML et s'exécutent par l'API. Le moteur, la palette et la validation sont
  prêts à être pilotés par un canevas.

---

## Mapping et pivot central (v16)

La brique qui rend tout le reste composable : un **format pivot canonique** au
centre, et des **mappings** explicites entre ce pivot et chaque source.

### Pourquoi un pivot central

Sans lui, relier N sources demande N×N ponts : CSV→EDI, EDI→CSV, EDI→EDI,
base→EDI… Avec lui, chaque source sait seulement se traduire **vers** et
**depuis** une forme unique : 2N traducteurs, jamais N². C'est le patron
*hub-and-spoke* (modèle canonique), déjà utilisé en interne par la conversion
EDI→EDI de la v13, remonté ici au niveau de toute l'application.

La forme canonique est `{"head": {...}, "items": [{...}]}` — la même que
`edi_service` produisait déjà, choisie pour que le moteur EDI n'ait besoin
d'aucun adaptateur. C'est volontairement **un mélange des deux mondes** : des
champs nommés et adressables (le côté objet, ce qui permet de dire « ce champ =
ce segment »), qui se matérialisent en lignes plates (le côté SQL, ce que le
pipeline de nettoyage et les tables savent déjà consommer).

### Le mapping remplace le pont par coïncidence de noms

Avant, une colonne alimentait un champ EDI **si elle portait le même nom**.
Implicite et fragile. Un `mapping` (nouveau `kind`, versionné comme les autres)
énonce la correspondance, et comme chaque lien est déclaré une seule fois pour
les deux sens, le même artefact pilote source→pivot **et** pivot→source.

### Un champ a une origine et des contraintes

C'est l'unification qui fait tenir l'ensemble. Trois mécanismes existaient
séparément : le lien de mapping (« ce champ vient de là »), la colonne calculée
(« ce champ vient d'une expression »), la règle de config (« ce champ doit
respecter ça »). Trois objets, trois écrans — pour une seule notion.

```yaml
name: partenaire
source_kind: flat
links:
  - {pivot: numero_commande, source: NoCmd, scope: head}     # lu
  - pivot: reference
    expr: "CONCAT([NoCmd], '-', [Client])"                   # calculé
    scope: head
  - pivot: code_article
    source: EAN
    scope: item
    rules: {type: string, regex: "^\\d{13}$"}                # contraint
```

`source` **ou** `expr`, jamais les deux : le modèle refuse à l'enregistrement.
`scope` dit si la valeur vit sur l'en-tête ou se répète par ligne. `rules` porte
exactement les contraintes d'une config de nettoyage.

**Aucun moteur n'a été écrit pour ça.** Les expressions passent par le
`ComputeService` des colonnes calculées — même mini-langage, même syntaxe
`[Colonne]`, mêmes fonctions. Les contraintes passent par
`ProcessService.apply_field_configs`, le moteur qui valide les fichiers. Un
regex signifie la même chose des deux côtés, et corriger l'un corrige l'autre :
ce n'est pas trois systèmes cohérents entre eux, c'est **un seul système atteint
depuis trois endroits**.

Conséquence assumée : le mapping hérite aussi des *limites* du moteur. Un
`type: integer` seul ne rejette pas une valeur non numérique — ni dans un
fichier, ni dans un mapping. Pour un contrôle strict, ajouter un `regex`. Le
comportement identique des deux côtés est vérifié par test.

Les expressions se voient entre elles dans l'ordre de déclaration : un champ
peut s'appuyer sur un précédent. Une expression cassée dégrade proprement —
champ vide et erreur remontée, jamais un 500.

### Les deux modes de l'interface

- **Liste** — une ligne par lien : champ pivot, origine (menu des sources, ou
  expression), portée, contraintes dépliables. Rapide quand on sait ce qu'on veut.
- **Relier à la main** — deux colonnes, on clique une source puis un champ pivot
  et la paire se crée. Les sources déjà reliées sont cochées, celles qui restent
  sont comptées. Le clic-clic plutôt que le glisser : ça marche au doigt, et
  reste lisible à trente champs.

Un bouton **Suggérer** propose le mapping identité (chaque champ relié à
lui-même, portée devinée selon que la colonne est constante ou variable). Un
point de départ à corriger, comme l'inférence de modèle EDI.

### Endpoints

| Méthode | Route | Rôle |
|---|---|---|
| `POST` | `/api/mapping/suggest` | Mapping de départ depuis une source |
| `POST` | `/api/pivot/from/{flat\|edi}` | Toute source → objet pivot (`preview` ou `session`) |
| `POST` | `/api/pivot/convert` | source → pivot → cible, tout↔tout |

`/api/pivot/from/...` avec `target=session` est le « bouton objet » : le pivot
devient une table de travail ordinaire, sur laquelle tout le nettoyage
s'applique. Les artefacts `mapping` passent par les routes de bibliothèque
habituelles.

### Ce que ça prépare

Ce découpage n'est pas gratuit pour la suite :

- un champ qui peut venir d'une **expression** peut venir d'un **appel API** :
  c'est une origine de plus dans la même liste, pas une refonte ;
- un mapping est déjà **versionné et paramétrable** — une brique réutilisable,
  c'est un mapping plus des variables, et le moteur d'expressions accepte déjà
  un dictionnaire de variables ;
- le pivot est déjà le **connecteur** entre briques : ce qui sort de l'une entre
  dans l'autre parce que toutes parlent la même forme.

---

## Module EDI (v13)

Un volet dédié aux fichiers EDIFACT : les inspecter, les contrôler, les mettre à
plat pour les traiter comme n'importe quel CSV, les régénérer, et convertir d'un
format partenaire vers un autre.

### Le principe

Un fichier EDI est une **enveloppe** (`UNB` … `UNZ`) contenant un ou plusieurs
**messages** (`UNH` … `UNT`). Chaque message se lit en trois zones : un
**en-tête** (numéro de document, dates, partenaires), une **boucle de détail**
répétée par article, un **résumé** avec les totaux de contrôle. Un fichier porte
donc couramment plusieurs *heads*, chacun avec ses *items* — c'est la structure
que tout le module prend en charge.

Chaque **segment** commence par un tag de trois lettres, suivi d'éléments
eux-mêmes découpés en composants. Dans `NAD+BY+5412345000013::9`, le tag est
`NAD`, `BY` est le **qualifiant** qui donne son sens au segment (acheteur), et le
troisième élément porte un GLN et la liste de codes dont il vient. Le même tag
dit autre chose selon son qualifiant : c'est pourquoi un modèle déclare une
variante par valeur de qualifiant.

Les séparateurs (`:` composant, `+` élément, `.` décimale, `?` libération, `'`
fin de segment) sont **redéfinissables** par le segment de service `UNA` en tête
de fichier. Le lexer le lit systématiquement au lieu de supposer les valeurs par
défaut.

### Détection par le contenu, pas par l'extension

Les fichiers EDI n'ont généralement pas d'extension. Le format est déduit des
premiers octets : `UNA` ou `UNB` → EDIFACT, `ISA` en 106 caractères → X12.

**EDIFACT est implémenté ; X12 est détecté et refusé explicitement** (422 avec un
message clair) plutôt que mal interprété. Le lexer est paramétré par ses
séparateurs, donc l'abstraction est prête pour X12 — mais tant que ce n'est pas
écrit et testé, l'outil le dit.

### Le contrôle, à deux niveaux

**Syntaxique, sans aucun modèle.** L'EDI porte ses propres contrôles d'intégrité
et ils sont gratuits : `UNT` annonce le nombre de segments du message, `UNZ` le
nombre de messages de l'interchange, et les références de `UNB` et `UNZ` doivent
correspondre. Un compteur faux signale un fichier tronqué ou trafiqué. Codes
émis : `COMPTEUR_UNT`, `COMPTEUR_UNZ`, `REF_INTERCHANGE`, `REF_MESSAGE`,
`UNT_MANQUANT`, `UNZ_MANQUANT`, `UNZ_ORPHELIN`, `UNT_ORPHELIN`,
`SEGMENT_HORS_MESSAGE`, `VIDE`.

**Sémantique, avec un modèle.** Segments obligatoires présents, cardinalités
respectées, qualifiants connus, codes dans la liste autorisée, nombres et dates
valides : `SEGMENT_MANQUANT`, `TROP_D_OCCURRENCES`, `VARIANTE_MANQUANTE`,
`QUALIFIANT_INCONNU`, `SEGMENT_INATTENDU`, `CODE_INTERDIT`, `NOMBRE_INVALIDE`,
`DATE_INVALIDE`, `TYPE_MESSAGE`. Chaque erreur est positionnée — message n°,
zone, tag, position du segment, chemin de l'élément — dans le même esprit que le
rapport cellule par cellule du pipeline CSV.

### Le modèle EDI est un artefact versionné

Nouveau `kind` : **`edi_model`**, à côté de `config`, `computed` et `tco`. Il
hérite donc de toute la couche v12 sans une ligne de code supplémentaire :
versions immuables, épinglage, archivage.

Un modèle est un YAML déclaratif qui décrit les trois zones et, pour chaque
segment, quels chemins `élément.composant` (**1-based**) deviennent quels champs
nommés — les mêmes noms que les colonnes du pivot et que ceux relus par le
générateur :

```yaml
name: ORDERS D96A (exemple)
message_type: ORDERS
directory: D96A
header:
  - tag: BGM
    status: M                 # M = obligatoire, C = conditionnel
    fields:
      - {name: type_document, path: "1.1", codes: ["220", "221"]}
      - {name: numero_commande, path: "2.1"}
  - tag: NAD
    status: M
    max: 5
    qualifier: "1.1"          # chemin du discriminant
    when:
      "BY":                   # qualifiants toujours entre guillemets
        label: Acheteur
        required: true        # cette variante-là doit être présente
        fields:
          - {name: acheteur_id, path: "2.1"}
      "SU":
        label: Fournisseur
        fields:
          - {name: fournisseur_id, path: "2.1"}
items:
  loop_start: LIN             # le tag qui ouvre chaque article
  segments:
    - tag: QTY
      status: M
      qualifier: "1.1"
      when:
        "21":
          required: true
          fields:
            - {name: quantite_commandee, path: "1.2", type: number}
```

`status: M` porte sur le **segment** (« au moins une variante doit apparaître »),
`required: true` sur une **variante précise**. La distinction compte : sans elle,
le générateur émet des segments vides comme `NAD+DP'`.

### Le pivot : deux modes, au choix

- **`flat`** — une ligne par article, les champs de l'en-tête **répétés** sur
  chaque ligne. Une seule table, directement exploitable.
- **`linked`** — deux tables jointes sur `message_no` : les en-têtes d'un côté,
  les articles de l'autre. Pas de duplication.

Les segments répétés au niveau en-tête (plusieurs `NAD` : acheteur, fournisseur,
lieu de livraison…) deviennent des **colonnes distinctes** — une par variante de
qualifiant — plutôt que de multiplier les lignes.

Sorties : aperçu, CSV, Excel (multi-feuilles en mode `linked`), ou **session de
travail**. Cette dernière est le vrai point d'accroche : le tableau aplati
devient un fichier ordinaire de l'application, sur lequel les règles par colonne,
les colonnes calculées, le TCO et le rapport s'appliquent **sans code
spécifique**. L'EDI est une source de plus, pas un monde parallèle.

### La génération, et l'aller-retour d'or

Le chemin inverse : un CSV/XLSX plat dont les en-têtes correspondent aux noms de
champs du modèle redevient de l'EDIFACT. Les lignes sont regroupées en messages
(par `message_no`, par une colonne au choix, ou en un seul message), les
caractères réservés présents dans les données sont échappés, `UNT`, `UNZ` et le
`CNT+2` du résumé sont recalculés.

Le test qui garde l'ensemble honnête :

```
parse(generate(pivot(parse(fichier)))) == parse(fichier)
```

Il a payé immédiatement — il a révélé trois bugs réels du générateur : des
segments qualifiés vides émis à tort (`NAD+DP'`, `DTM+2'`), les compteurs `UNT`
faussés en conséquence, et l'identifiant d'expéditeur `GLN:14` sur-échappé en
`GLN?:14` au lieu d'être découpé en composants.

### EDI → EDI : toujours par le pivot interne

Une conversion lit avec le modèle source et écrit avec le modèle cible, en
passant par la forme interne. Avec N modèles on couvre N×N conversions ; en
mapping direct format-à-format il en faudrait N². Quand les deux modèles ne
nomment pas leurs champs pareil, un mapping JSON fait le pont :
`{"champ_cible": "champ_source"}`.

### Composer un modèle depuis un fichier

*Models* → *Infer from a sample file* : le fichier est parsé, `UNH` donne le type
de message et la version du répertoire, les segments réellement présents sont
observés (ordre, répétitions, qualifiants, formats de date), et un **squelette
pré-rempli** est proposé dans l'éditeur. Un segment vu dans *tous* les messages
est marqué `M`, les autres `C` ; les `max` reprennent le maximum observé.

C'est un point de départ, pas une spécification : l'inférence ne peut pas
deviner ce qui est obligatoire *en théorie*, seulement ce qui était présent dans
l'échantillon. Les limites sont listées en clair dans `notes`, au-dessus de
l'éditeur.

### La doc, dans l'application

L'onglet **Doc** n'est pas un fichier du dépôt : il est alimenté par
`GET /api/edi/kb` — anatomie d'un interchange, table des séparateurs, segments
courants avec leurs éléments, qualifiants, formats de date. La même base de
connaissances sert au **viewer** : dans *Inspect*, chaque segment est affiché
décodé en clair (`NAD` → « Nom et adresse », `BY` → « Acheteur »). La doc et
l'outil ne font qu'un — ce qui est documenté est ce qui est réellement reconnu.

### Endpoints

| Méthode | Route | Rôle |
|---|---|---|
| `GET` | `/api/edi/kb` | Base de connaissances (doc + viewer) |
| `POST` | `/api/edi/inspect` | Détection, arbre décodé, contrôle syntaxique |
| `POST` | `/api/edi/models/infer` | Squelette de modèle depuis un fichier |
| `POST` | `/api/edi/validate` | Contrôle contre un modèle |
| `POST` | `/api/edi/pivot` | EDI → tables (`flat`\|`linked`) × (`preview`\|`csv`\|`xlsx`\|`session`) |
| `POST` | `/api/edi/generate` | CSV/XLSX plat → EDIFACT |
| `POST` | `/api/edi/convert` | EDI → EDI via le pivot interne |
| `GET` | `/api/edi/models/{id}/versions/{n}/yaml` | Modèle stocké rendu en YAML |

Le modèle se passe soit en YAML inline (`model_yaml`), soit par référence à la
bibliothèque (`model_id` + `model_version` optionnel pour épingler).

### Limites assumées

- **Le répertoire UN/EDIFACT complet n'est pas embarqué.** Des centaines de
  types de messages × des dizaines de versions (D93A → D21B) : des sociétés
  entières vivent de ça. Le module fournit un moteur générique, une base des
  segments courants, deux modèles d'exemple et l'inférence pour créer les tiens.
- **La base de codes est minimale** — les qualifiants les plus courants de
  `NAD`, `DTM`, `QTY`, `BGM`, `PRI`, `RFF`, `MOA`, `CNT`. Un code inconnu est
  signalé, pas rejeté.
- **X12 est détecté, pas traité.**
- Les groupes de segments imbriqués profonds (SG multi-niveaux) sont aplatis sur
  trois zones : en-tête / boucle d'articles / résumé. Cela couvre ORDERS et
  DESADV ; un message très hiérarchisé demanderait un modèle plus riche.

---

## Les deux bugs corrigés

Les tests ont révélé deux défauts dans le code métier d'origine.

**1. Reformatage de date cassé.** L'orchestrateur de nettoyage appelle chaque
fonction avec la valeur **toujours en dernier argument** (`fn(*params, valeur)`).
Or `clean_format` déclarait sa signature comme `(fmt_source, valeur, fmt_target)` :
la valeur était passée à la place du format cible. Résultat : une date était
remplacée par la **chaîne de format littérale** (`"%Y-%m-%d"` au lieu de
`1990-02-01`). Corrigé en réalignant la signature sur la convention de
l'orchestrateur : `clean_format(fmt_source, fmt_target, item)`.

**2. Séparateur de milliers par espace ignoré.** Le détecteur de séparateurs ne
reconnaissait que `.` et `,`. La notation française courante `"1 234,50"`
n'était donc jamais nettoyée correctement (l'espace restait). `parse_number`
retire désormais les espaces normaux **et insécables** (`\u00a0`, `\u202f`)
lorsque le nettoyage du séparateur de milliers est activé.

Les deux corrections sont couvertes par des tests
(`test_number_cleaning_marks_cleaned`, `test_date_reformat`).

**3. (v11) Plantage sur identifiant vide.** Dans la refonte cette fois : quand
une colonne *identifiant* contenait une cellule vide, `/process` et
`/api/pipeline` renvoyaient `sequence item 0: expected str instance, float
found` — sur une série pandas en dtype `string`, `astype(str)` laisse les
valeurs manquantes en NaN (float), qui faisaient exploser le `join` des ids du
rapport. C'était précisément le cas du jeu d'exemple `samples/` (SIRET vide,
mis là pour illustrer `nullable: false`). Corrigé par une normalisation
explicite NaN → `""` avant concaténation, couvert par
`test_report_id_with_empty_identifier_value`.

---

## API

Base : `/api`. Documentation interactive auto-générée sur `/docs`.

| Méthode | Route | Rôle |
|---|---|---|
| `GET`  | `/api/health` | état du service |
| `GET`  | `/api/presets` | regex prédéfinies, formats date, types, encodages |
| `POST` | `/api/files` | upload d'un CSV/XLSX → `session_id` + aperçu |
| `POST` | `/api/files/{sid}/header` | applique le traitement de structure |
| `POST` | `/api/files/{sid}/tco` | charge une table de correspondance |
| `POST` | `/api/files/{sid}/process` | lance le pipeline → données + statut par cellule + stats + rapport |
| `POST` | `/api/files/{sid}/cells` | corrige des cellules à la main dans la table de travail (mode édition) |
| `POST` | `/api/files/{sid}/cells/reset` | annule toutes les corrections manuelles (restaure le fichier chargé) |
| `DELETE` | `/api/files/{sid}` | libère la session |
| `POST` | `/api/config/export` | config courante → YAML |
| `POST` | `/api/config/import` | YAML → config (+ matching aux colonnes) |
| `POST` | `/api/expression/check` | valide une expression de colonne calculée |
| `POST` | `/api/pipeline` | one-shot **par fichiers** : tout en multipart, résultat direct |

Les endpoints de la **bibliothèque** (artefacts par id, flux, runs) sont
détaillés dans la section « Bibliothèque & flux » plus haut.

Statuts renvoyés par cellule : `OK`, `CLEANED`, `ERROR`, `MAPPING_OK`,
`MAPPING_KO`, `NO_TCO`, `COMPUTED` — plus `EDITED` côté interface pour une
cellule corrigée à la main en attente de revalidation.

---

## Colonnes calculées

Un mini-langage sûr permet de dériver de nouvelles colonnes à partir des
valeurs **nettoyées**. On référence une colonne avec `[crochets]` et le texte
entre `"guillemets"` :

```
CONCAT([nom], " ", [prenom])
IF([civilite] == "M", "Monsieur", "Madame")
UPPER(TRIM([code]))
DEFAULT([email], "non renseigné")
```

Fonctions disponibles : `IF`, `CONCAT`, `UPPER`, `LOWER`, `TITLE`, `TRIM`,
`LEFT`, `RIGHT`, `SUBSTRING`, `SPLIT`, `CONTAINS`, `STARTSWITH`, `ENDSWITH`,
`REGEX_EXTRACT`, `REPLACE`, `BETWEEN`, `ROUND`, `COALESCE`, `DEFAULT`,
`DATEDIFF`, `NUM`, `STR`, `LEN`, et `LOOKUP` (résout une valeur via la table
TCO chargée). Les colonnes calculées peuvent être exportées/réimportées dans
un fichier JSON séparé (onglet Computed).

Les expressions sont analysées avec `ast` et validées contre une liste blanche
stricte (types de nœuds + fonctions autorisées) **avant** évaluation : pas
d'`eval` de code arbitraire, pas d'accès attribut, pas d'import. Une tentative
comme `__import__("os")...` est rejetée. Une cellule qui échoue à
l'évaluation renvoie `#ERR` plutôt que de casser la requête.

---

## Performance sur fichiers larges

Le rapport est généré de façon **vectorisée** (listes par colonne plutôt
qu'accès cellule par cellule) et ne transporte que les cellules à problème —
les cellules OK sont résumées dans les stats. Sur un fichier de
400 colonnes × 1000 lignes, le traitement est passé d'environ **66 s à ~5 s**.
Côté interface, le tableau **virtualise les lignes** (seules les lignes visibles
à l'écran sont rendues), trie par colonne de façon typée, permet de réordonner
les colonnes par glisser-déposer et de filtrer par recherche.

---

## Tests

```bash
cd backend && python -m pytest -v
```

La suite (324 tests) couvre l'upload, la validation (longueur, regex, type),
le nettoyage (nombres, dates), le mapping TCO, l'aller-retour YAML, le
traitement du header, les filtres (groupes OU compris), le pipeline one-shot,
le mode édition (correction, colonne renommée, reset, régressions) et la
bibliothèque : migrations, versionnement immuable, garde-fous de kind,
archivage, flux épinglé vs « latest », runs figés et ré-exportables, runs en
échec persistés. Les 25 tests EDI couvrent la détection de format, le lexer
(séparateurs `UNA` exotiques, échappement, aller-retour), les compteurs
d'enveloppe corrompus, le contrôle par modèle (variante manquante, code
interdit, nombre et date invalides, segment inattendu, mauvais type de
message), les deux modes de pivot, la reprise dans le pipeline de validation,
l'**aller-retour d'or** (§ module EDI), la conversion avec mapping,
l'inférence de modèle et les artefacts `edi_model` en bibliothèque. Les 28 tests
datasets couvrent l'édition de lignes (ajout, duplication, suppression, lot
filtré, restauration), le verdict du preflight et surtout sa distinction
squelette / données, les trois modes d'écriture, leur **composition** (une table
créée en `replace` reste fusionnable), l'ordre de lecture déterministe et le
report des types déclarés dans le schéma.

Par défaut les tests tournent sur un **SQLite temporaire** (aucun service à
lancer). La même suite passe telle quelle sur PostgreSQL :

```bash
FX_DB_URL=postgresql+psycopg2://app:app@localhost:5432/fx_test python -m pytest
```

---

## Notes de production

Deux natures de données, deux stockages. Les **sessions interactives**
(l'exploration, l'édition à la main) restent en mémoire et mono-processus —
volatiles par nature. Pour un déploiement multi-workers, remplacer le `dict`
de `session.py` par Redis (DataFrames sérialisés en parquet) derrière la même
interface `get` / `create` ; rien d'autre ne change. Les **artefacts durables**
(configs, computed, TCO, flux, runs) vivent en base : SQLite par défaut,
PostgreSQL dès que `DATABASE_URL` est définie — le schéma est géré par Alembic
au démarrage.

Deux points de plomberie qui se paient cher s'ils sont laissés au hasard, et qui
ont été trouvés en auditant le livrable plutôt qu'en relisant le code :

- **`alembic/env.py` résout l'URL comme l'application** (`FX_DB_URL`, puis
  `DATABASE_URL`, puis le repli SQLite). Au démarrage, l'app pilote Alembic
  depuis Python et lui injecte l'URL ; mais en ligne de commande
  (`alembic upgrade head`, `alembic revision --autogenerate`) c'est `env.py` qui
  décide. S'il faisait confiance à `alembic.ini`, toute migration lancée à la
  main viserait le SQLite de repli pendant que l'app tourne sur PostgreSQL — et
  un `--autogenerate` sur la base vide qui en résulte produirait une migration
  supprimant toutes les tables.
- **Chaque route mutante valide explicitement sa transaction** via
  `db.commit(s)`, avant de construire sa réponse. `get_session` valide aussi,
  mais au démontage de la dépendance FastAPI, c'est-à-dire *après* le retour de
  la route : un client qui écrit puis relit immédiatement — ce que fait
  l'interface — pouvait recevoir « écrit » puis se voir servir l'état
  précédent.

Limites assumées à ce stade : pas de comptes utilisateurs ni de permissions
(le multi-utilisateur — owner, partage, rôles — est la marche suivante, celle
de l'offre « pro ») ; le fichier de sortie d'un run est stocké en base encodé
en base64 — suffisant pour des fichiers de travail, à déplacer vers un stockage
objet (S3/minio, l'URI seule en base) si les volumes grossissent ; le rapport
persisté d'un run est le rapport *groupé* (une ligne par règle en échec avec
les ids concernés), pas le rapport long cellule par cellule. Le CORS est
ouvert (`*`) : à restreindre selon l'origine du front en production.
