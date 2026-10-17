# Auto-planning - guide de staging PostgreSQL

## 1. Portee et regles de securite

Ce document prepare la validation PostgreSQL de l'auto-planning. Il ne
declenche aucun deploiement et ne doit jamais etre execute contre la base de
production.

Regles non negociables :

- service web staging et base PostgreSQL staging independants ;
- aucune URL, sauvegarde, variable de groupe ou disque de production partage ;
- uniquement des personnes, clients, adresses et interventions fictifs ;
- SMTP, IMAP, archivage BCC et ordonnanceur d'emails desactives ;
- aucune suppression automatique de table ;
- un retour au commit precedent ne restaure jamais les donnees PostgreSQL ;
- toute restauration se fait d'abord dans une nouvelle base staging vide ;
- aucune etape ci-dessous ne doit etre executee avant autorisation explicite.

Les environnements Render permettent de separer les ressources de staging et
de production. Les groupes de variables peuvent aussi etre limites a un
environnement. Voir [Projects and environments](https://render.com/docs/projects)
et [Environment variables and secrets](https://render.com/docs/configure-environment-variables).

## 2. Architecture staging recommandee

Creer manuellement dans Render, dans cet ordre :

1. Un environnement de projet nomme `Staging`, distinct de `Production`.
2. Une base PostgreSQL neuve nommee, par exemple,
   `luxmann_planner_staging`.
3. Un service web nomme `luxmann-planner-staging`, dans le meme environnement
   et la meme region que la base.
4. Lier uniquement l'URL interne de cette nouvelle base au service staging.
5. Ne rattacher aucun groupe d'environnement ni disque de production.

Render recommande l'URL interne lorsque le service et PostgreSQL sont dans le
meme compte et la meme region. La region actuelle du projet est Frankfurt ; le
staging doit donc rester a Frankfurt pour eviter latence et trafic externe.
Voir [Create and connect a Render Postgres database](https://render.com/docs/postgresql-creating-connecting).

Configuration du service web :

| Champ | Valeur staging attendue |
| --- | --- |
| Runtime | Python 3 |
| Branche | `feature/auto-planning` apres autorisation de push |
| Build command | `pip install -r requirements.txt` |
| Start command | `gunicorn --workers 1 --timeout 120 app:app` |
| Health check | Laisser vide pour la premiere recette ; ne pas utiliser une route qui redirige vers login |
| Auto-deploy | Desactive pendant la recette initiale |
| Region | Frankfurt, identique a PostgreSQL staging |

Ne pas reutiliser directement `render.yaml` sans verifier chaque ressource :
le fichier actuel nomme le service de production et declare un disque de
10 Go. La recette auto-planning n'a pas besoin de ce disque. Un disque staging
n'est utile que si l'on teste aussi le stockage durable des documents.

## 3. Variables d'environnement

Les noms ci-dessous sont necessaires ou utiles. Les valeurs secretes ne sont
volontairement pas fournies dans ce document.

| Variable | Staging | Remarque |
| --- | --- | --- |
| `APP_ENV` | `staging` | Garde obligatoire du banc PostgreSQL. |
| `DATABASE_URL` | URL interne de la **nouvelle** base staging | Ne jamais copier l'URL production. |
| `SECRET_KEY` | valeur generee propre au staging | Ne pas reutiliser la cle production. |
| `PYTHON_VERSION` | meme version que le service valide | Actuellement `3.12.7` dans `render.yaml`. |
| `STORAGE_ROOT` | chemin staging ou valeur par defaut | Aucun disque production partage. |
| `MAX_UPLOAD_MB` | valeur de test | Sans importance pour l'auto-planning. |
| `AUTO_PLANNING_STAGING_CONFIRM` | `STAGING_ONLY_I_UNDERSTAND` | A activer uniquement pendant le banc staging. |
| `AUTO_PLANNING_PRODUCTION_DATABASE_SHA256` | empreinte optionnelle | Defense supplementaire ; jamais l'URL elle-meme. |
| `RENDER_SERVICE_NAME` | fourni par Render | Doit contenir `staging` ou `test`. |

Variables a laisser absentes ou vides pour cette recette :

- `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_REPLY_TO` ;
- `IMAP_HOST`, `IMAP_USER`, `IMAP_PASSWORD`, `EMAIL_ARCHIVE_BCC` ;
- `EMAIL_SCHEDULER_SECRET` ;
- `ORS_API_KEY`, sauf test explicite et separe de la cartographie.

Le script de test retire aussi ces variables de son propre processus avant
d'importer `app.py`. Il ne peut donc ni envoyer un email ni archiver un message.

## 4. Controles anti-production avant toute connexion

Faire valider cette liste par deux personnes si possible :

- [ ] le projet/environnement Render affiche clairement `Staging` ;
- [ ] le service affiche `luxmann-planner-staging` ;
- [ ] le nom de base contient `staging` ou `test` ;
- [ ] l'hote et l'identifiant de base different de ceux de production ;
- [ ] aucun Environment Group production n'est rattache ;
- [ ] aucun disque production n'est rattache ;
- [ ] toutes les variables email sont absentes ;
- [ ] aucune donnee reelle n'a ete importee ;
- [ ] l'auto-deploy est desactive pendant la recette ;
- [ ] une sauvegarde staging exploitable existe si le plan choisi la permet.

Verification SQL en lecture seule dans la console staging :

```sql
SELECT current_database(), current_user, version();
SELECT COUNT(*) FROM shifts;
SELECT COUNT(*) FROM clients;
SELECT COUNT(*) FROM workers;
```

Sur une base neuve, seuls les enregistrements techniques crees par
`init_db()` doivent apparaitre. Si un nom de client ou d'employe reel est
visible, arreter immediatement la recette sans executer le banc.

Le fichier `tests/postgres_auto_planning_staging.py` ajoute quatre verrous :

1. `APP_ENV` doit etre exactement `staging` ;
2. une phrase de confirmation exacte est obligatoire ;
3. le nom d'hote/base, et le nom du service Render s'il existe, doivent
   contenir `staging` ou `test` ;
4. si une empreinte production est fournie, l'URL staging doit etre differente.

### Points de securite preexistants a traiter avant exposition publique

- Une base vide recoit actuellement le compte bootstrap `admin` avec le mot de
  passe historique `admin123`. Le changement immediat via
  `/change_password` est une condition bloquante avant de communiquer l'URL
  staging ou d'y conserver quoi que ce soit, meme fictif.
- La protection CSRF ajoutee pour l'auto-planning couvre ses nouvelles routes
  POST. L'application historique ne possede toujours pas de protection CSRF
  globale ; notamment certaines routes anciennes de planning restent une
  dette de securite distincte. Ne pas etendre cette phase de preparation en
  refonte silencieuse : planifier et faire valider ce chantier separement.
- `shift_locks` n'a pas de cle etrangere vers `shifts`. Les suppressions par
  la route applicative sont auditees et transactionnelles, mais une suppression
  SQL manuelle pourrait encore produire un verrou orphelin.

## 5. Donnees fictives

La methode recommandee est le banc PostgreSQL fourni avec le projet. Chaque
test cree ses propres lignes avec un prefixe aleatoire `APSTG_<run>_...` :

- deux employes fictifs ;
- deux clients fictifs, sans telephone et avec adresse generique ;
- huit semaines d'interventions historiques ;
- brouillons et verrous strictement limites a ce prefixe.

Par defaut, le banc retire uniquement ses propres lignes a la fin de chaque
test. Il ne supprime aucune table et ne touche aucune ligne sans son prefixe.
Pour inspecter les donnees dans l'interface apres le test, definir
temporairement `AUTO_PLANNING_STAGING_KEEP_FIXTURES=1`. Les noms affiches
permettent alors de les retrouver sans ambiguite.

Alternative manuelle via l'interface :

1. Ajouter `APSTG Aida` et `APSTG Bea` comme employes.
2. Ajouter `APSTG Bureau Alpha` et `APSTG Restaurant Beta` comme clients.
3. Utiliser uniquement `example.invalid` pour une eventuelle adresse email.
4. Creer huit lundis consecutifs pour le premier client, puis quatre lundis
   alternes pour le second.
5. Ajouter une absence fictive sur la semaine cible.
6. Ne saisir aucune adresse, personne ou facture issue de production.

## 6. Execution du banc PostgreSQL

Le fichier n'est volontairement pas nomme `test_*.py`. Il n'est donc jamais
importe par la suite SQLite standard.

Validation locale a executer d'abord depuis la racine du depot :

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m py_compile app.py tests\test_auto_planning.py tests\postgres_auto_planning_staging.py
```

Etat de cette preparation : **71 tests SQLite reussis** et compilation Python
reussie. Le banc PostgreSQL n'a pas ete lance. Un essai sans les variables de
confirmation s'arrete avant l'import de `app.py`, comme prevu.

Dans un shell du service staging, apres la checklist :

```powershell
$env:APP_ENV = "staging"
$env:AUTO_PLANNING_STAGING_CONFIRM = "STAGING_ONLY_I_UNDERSTAND"
python tests/postgres_auto_planning_staging.py
```

En local, utiliser exactement la meme commande apres avoir affecte
`DATABASE_URL` a l'URL externe de la base staging. Ne jamais placer l'URL dans
un fichier versionne, une commande partagee ou une capture d'ecran.

Le banc couvre :

1. creation idempotente des trois tables auto-planning ;
2. generation a partir de huit semaines sans ecriture prematuree ;
3. modification individuelle du brouillon ;
4. acceptation multiple ;
5. insertion dans `shifts` ;
6. seconde application sans doublon ;
7. deux applications concurrentes ;
8. concurrence application/modification manuelle ;
9. verrouillage, refus du deplacement et de la suppression, deverrouillage ;
10. rollback total si la seconde insertion echoue ;
11. lecture de `/week` pendant le verrou transactionnel d'application ;
12. permissions, CSRF et verrou consultatif PostgreSQL entre connexions.

Resultat attendu : tous les tests `OK`, aucun SMTP/IMAP appele, et uniquement
des lignes `APSTG_...` temporaires.

## 7. Verification manuelle apres le banc

- Generer un brouillon depuis `/plan/generate` avec 8 semaines.
- Modifier un employe, un client, une date et une plage `HH:MM-HH:MM`.
- Accepter deux propositions et n'en cocher qu'une a l'application.
- Verifier dans `/week` qu'une seule intervention a ete ajoutee.
- Rejouer l'application : HTTP 409, aucune ligne supplementaire.
- Verrouiller une intervention puis tenter glisser-deposer, edition et
  suppression : les trois doivent etre refuses.
- Deverrouiller puis verifier que le deplacement redevient possible.
- Ajouter une absence apres generation : l'application doit etre bloquee.
- Ouvrir `/week` dans un second navigateur pendant une application.
- Ouvrir `/month`, `/invoices`, une facture, le composeur email et les PDF en
  lecture ; aucun envoi ne doit etre possible puisque SMTP est absent.

## 8. Points impossibles a certifier avec SQLite

La suite locale valide la logique applicative, mais les points suivants
doivent rester marques `a verifier` jusqu'au banc PostgreSQL :

- traduction SQL de `_PgCursor` pour `PRAGMA`, `SERIAL`, placeholders,
  `ON CONFLICT` et `RETURNING id` ;
- verrou consultatif transactionnel entre plusieurs connexions ou processus
  Gunicorn, et pas seulement le verrou Python d'un processus ;
- comportement de `LOCK TABLE shifts IN EXCLUSIVE MODE` : serialisation des
  ecritures tout en laissant les lectures `/week` continuer ;
- valeur de `rowcount` lors de deux mises a jour concurrentes d'un batch ;
- rollback complet apres une erreur PostgreSQL au milieu de plusieurs INSERT ;
- detection des deadlocks, delais de connexion et redemarrage du service ;
- execution idempotente de `init_db()` sur une base PostgreSQL neuve ;
- concurrence au demarrage si le service est un jour augmente au-dela du
  worker Gunicorn unique actuellement configure.

Le banc opt-in couvre la traduction lors de la creation du schema, les verrous
consultatifs, le verrou de table avec lecture concurrente, `rowcount`, le
rollback et l'idempotence du schema. Les deadlocks provoques, les coupures
reseau, le redemarrage Render, le demarrage multi-worker et une restauration
reelle restent des controles operateur separes.

## 9. Sauvegarde staging

Les bases PostgreSQL payantes Render disposent de mecanismes de sauvegarde et
de restauration ; une base gratuite n'offre pas ces garanties et expire selon
les limites du plan gratuit. Voir [PostgreSQL backups](https://render.com/docs/postgresql-backups)
et [Free instances](https://render.com/docs/free).

Avant une recette qui conserve des donnees utiles :

1. creer un logical export Render ou une sauvegarde equivalente ;
2. telecharger/verifier le fichier dans un emplacement non versionne ;
3. noter heure UTC, nom exact de la base, commit et operateur ;
4. verifier que l'archive est lisible avant de continuer.

Exemple avec les outils PostgreSQL, valeurs volontairement remplacees :

```powershell
pg_dump --format=custom --no-owner --no-acl --dbname="<STAGING_EXTERNAL_URL>" --file="<CHEMIN_SUR>/luxmann-staging-before-auto-planning.dump"
Get-FileHash "<CHEMIN_SUR>/luxmann-staging-before-auto-planning.dump" -Algorithm SHA256
pg_restore --list "<CHEMIN_SUR>/luxmann-staging-before-auto-planning.dump"
```

Ne pas stocker le dump dans Git et ne pas utiliser un dump de production pour
le staging.

## 10. Restauration et retour arriere

### Code

1. Suspendre les tests/ecritures staging.
2. Redeployer le commit staging precedent ou faire un revert dans une branche
   de correction.
3. Verifier `/week`, `/month` et les routes de lecture.

Cette operation ne restaure aucune donnee PostgreSQL.

### Donnees

1. Creer une **nouvelle base staging vide**.
2. Restaurer l'archive dans cette nouvelle base avec arret sur erreur.
3. Executer les controles de comptage et les smoke tests.
4. Reconfigurer uniquement le service staging vers cette base apres validation.
5. Conserver l'ancienne base jusqu'a acceptation complete du retour arriere.

```powershell
pg_restore --exit-on-error --no-owner --no-acl --dbname="<NEW_EMPTY_STAGING_URL>" "<CHEMIN_SUR>/luxmann-staging-before-auto-planning.dump"
```

Ne pas employer `--clean`, ne pas supprimer automatiquement les tables
existantes et ne jamais restaurer directement sur production pendant cette
phase.

Les migrations auto-planning sont additives. Si le code est retire, les
tables peuvent rester en place jusqu'a une migration ulterieure explicitement
revue ; les supprimer dans l'urgence augmenterait le risque.

## 11. Ressources estimees

Deux options, a choisir manuellement plus tard :

- **Recette jetable** : service web gratuit + PostgreSQL gratuit, cout direct
  nul, mais demarrage a froid, base limitee/expiree et absence de sauvegardes.
  Adaptation uniquement a des donnees entierement jetables.
- **Staging fiable temporaire** : petite instance web et plus petite base
  PostgreSQL payante, dans la meme region. Prevoir environ **13 USD/mois** au
  total selon les tarifs Render affiches au moment de la creation, facture au
  prorata, hors disque et trafic supplementaires. Verifier le prix final dans
  l'interface avant validation.

Un seul worker web, 512 Mo de RAM et la plus petite base suffisent pour cette
recette fonctionnelle. Aucun disque persistant n'est necessaire pour les tests
auto-planning seuls. Si un disque de 10 Go est ajoute pour tester aussi les
documents, son cout vient en plus. Sources : [Render pricing](https://render.com/pricing)
et [small-business hosting cost guide](https://render.com/articles/how-much-does-cloud-application-hosting-cost-for-small-businesses).

## 12. Etapes qui exigent l'intervention du proprietaire Render

Rien de cette liste n'est execute dans la preparation locale :

1. autoriser le push de `feature/auto-planning` ;
2. creer l'environnement/projet staging ;
3. choisir explicitement le plan gratuit ou payant ;
4. creer la base `staging` et verifier la region ;
5. creer le service web staging et desactiver l'auto-deploy ;
6. saisir/generer les variables staging sans reutiliser les secrets production ;
7. confirmer visuellement l'URL de base avant le premier demarrage ;
8. autoriser l'execution du banc PostgreSQL ;
9. examiner les resultats et les lignes fictives ;
10. autoriser separement toute etape ulterieure vers production.

## 13. Porte de sortie

Apres cette preparation, s'arreter. Ne creer ni service ni base, ne pousser
aucun commit, ne deployer aucun code et ne lancer aucun test PostgreSQL tant
que le proprietaire n'a pas donne une nouvelle autorisation explicite.
