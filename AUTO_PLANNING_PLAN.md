# Plan — Génération automatique du planning hebdomadaire

Statut : **phases 1 à 5 validées ; phase 6 implémentée et vérifiée localement,
en attente de validation**. Rien n'a été migré manuellement en production,
poussé ou déployé. La phase 7 n'a pas commencé.

Décisions prises par le propriétaire — voir section 0.

---

## 0. Décisions arrêtées (à respecter dans toute la suite)

1. **Fenêtre d'analyse** : par défaut **8 semaines** ; choix proposé 2 / 4 / 8.
2. **Seuil de confiance** : **0.70**. Toute proposition sous ce seuil est
   marquée **« à valider manuellement »** mais reste affichée (avec badge).
3. **Semaine cible** : par défaut **la semaine suivante** (lundi `+1`).
4. **Format horaire** : conserver **`HH:MM`** (pas d'arrondi au quart d'heure).
5. **Verrouillage** : solution **`shift_locks`** retenue (table de jointure).
   Zéro impact sur les `SELECT *` existants sur `shifts`.

---

## 1. Préparatifs de la phase 1 (désormais committés)

### 1.1 `app.py` — hunk 1 @ ligne ~2455 (+247 lignes)

Nouveau bloc `AUTO_PLANNING_TRANSLATIONS` (bs / fr / en / de / pt), suivi du
`for … TRANSLATIONS[_lang].update(_values)`. Clés disponibles pour toute
l'UI à venir :

```
auto_plan_generate, auto_plan_title, auto_plan_target_week,
auto_plan_history, auto_plan_include_exceptional,
auto_plan_create_draft, auto_plan_review,
auto_plan_existing, auto_plan_proposals, auto_plan_no_proposals,
auto_plan_confidence, auto_plan_period, auto_plan_last_seen,
auto_plan_alternatives,
auto_plan_accept, auto_plan_reject, auto_plan_save,
auto_plan_lock, auto_plan_locked, auto_plan_unlock,
auto_plan_apply, auto_plan_apply_confirm, auto_plan_applied,
auto_plan_duplicate_skipped,
auto_plan_invalid_week, auto_plan_invalid_time, auto_plan_blocked,
auto_plan_status_proposed, auto_plan_status_accepted,
auto_plan_status_rejected, auto_plan_status_applied,
auto_plan_recurrence_1, auto_plan_recurrence_2,
auto_plan_recurrence_3, auto_plan_recurrence_4,
auto_plan_recurrence_exceptional,
auto_plan_issue_overlap_existing, auto_plan_issue_overlap_batch,
auto_plan_issue_absence, auto_plan_issue_locked,
auto_plan_issue_holiday, auto_plan_issue_contract,
auto_plan_issue_exceptional, auto_plan_issue_no_alternative,
auto_plan_skipped_history, auto_plan_existing_preserved
```

→ **Instruction Codex** : ne pas ré-éditer ce bloc ; ajouter des clés
seulement si l'UI finale en manque.

### 1.2 `app.py` — hunk 2 @ ligne ~5874 (+51 lignes)

Trois `CREATE TABLE IF NOT EXISTS` + deux `CREATE INDEX IF NOT EXISTS` dans
`init_db()`. Schémas finaux :

```sql
CREATE TABLE IF NOT EXISTS shift_locks (
    shift_id INTEGER PRIMARY KEY,
    locked_at TEXT DEFAULT '',
    locked_by TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS shift_proposal_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_week_start TEXT NOT NULL,
    lookback_weeks INTEGER NOT NULL,
    include_exceptional INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    applied_at TEXT DEFAULT '',
    analysis_notes TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS shift_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL,
    target_date TEXT NOT NULL,
    time TEXT NOT NULL,
    client TEXT NOT NULL,
    worker TEXT NOT NULL,
    recurrence_period INTEGER DEFAULT 1,
    confidence REAL DEFAULT 0,
    status TEXT DEFAULT 'proposed',
    issues TEXT DEFAULT '[]',
    source_last_seen TEXT DEFAULT '',
    is_locked INTEGER DEFAULT 0,
    manual_override INTEGER DEFAULT 0,
    alternative_workers TEXT DEFAULT '[]',
    applied_shift_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_shift_proposals_batch
    ON shift_proposals(batch_id);
CREATE INDEX IF NOT EXISTS idx_shift_proposals_target
    ON shift_proposals(target_date, time);
```

→ **Instruction Codex** : laisser ces blocs tels quels. Aucune ALTER à ajouter.

### 1.3 Vérifications compatibilité (réalisées sans toucher au code)

- `shift_locks` : préféré à `shifts.is_locked` → zéro risque pour les
  `SELECT * FROM shifts` qui dépendent de l'ordre positionnel 0..5.
- `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS` : idempotents
  sur SQLite dev et PostgreSQL prod via `_PgCursor._translate()`. Un
  redémarrage Render suffit, aucun script de migration à exécuter à la main.
- Position dans `init_db()` : après `shifts` et `leave_requests`, aucune
  dépendance implicite inversée.
- `INTEGER PRIMARY KEY AUTOINCREMENT` est déjà utilisé par ~15 tables
  existantes → le traducteur SQLite→PG le gère.

### 1.4 Points d'attention à respecter dans le code à venir

1. **Nettoyage `shift_locks` à la suppression d'une intervention** : chaque
   route qui exécute `DELETE FROM shifts WHERE id=?` doit, dans la même
   transaction, exécuter `DELETE FROM shift_locks WHERE shift_id=?`. Audit
   requis : `rg "DELETE FROM shifts" app.py` avant la phase 5.
2. **Colonnes JSON stockées en TEXT** : `analysis_notes` et `issues` sont
   des `TEXT DEFAULT '[]'`. À la lecture, systématiquement `json.loads()`
   dans un `try/except` → fallback `[]` si corruption.
3. **Colonnes `NOT NULL` sans valeur par défaut** : `batch_id, target_date,
   time, client, worker, created_at` sur `shift_proposals` ;
   `target_week_start, lookback_weeks, created_at, created_by` sur
   `shift_proposal_batches`. Les INSERT doivent toujours les fournir.

---

## 2. Modèles / tables du système existant utilisés

| Table existante | Rôle pour la génération |
|---|---|
| `shifts(id, worker, client, date, time, status)` | Source d'historique |
| `workers(name, …)` | Liste des employés disponibles |
| `clients(name, contract_from, contract_to, …)` | Fenêtre contractuelle |
| `absences(worker, date_from, date_to, …)` | Indisponibilités |
| `leave_requests(…, status)` | Congés **approuvés** = bloquants |
| `holidays(date, name)` | Jours fériés Luxembourg (avertissement) |

Helpers réutilisables (**ne pas dupliquer**) :

```
split_workers()        join_workers()        worker_signature()
worker_in_shift()      parse_shift_hours()
duplicate_shift_exists(conn, worker, client, date, time, exclude_id=None)
lux_now()              get_week_start_from_request()
get_worker_colors()    get_all_holidays()
```

Timezone : **Europe/Luxembourg** via `lux_now()` ; semaine commençant lundi.

---

## 3. Architecture (confirmée)

```
shifts (historique)
       │
       ▼
AnalyseurRécurrence (lookback 2 / 4 / 8 sem.)
   clé = (client, worker_signature, dow, start, end)
       │
       ▼
Classificateur (période médiane → 1/2/3/4 sem. ou exceptionnel)
       │
       ▼
Projecteur semaine cible
       │
       ▼
Vérificateur contraintes
   - overlap intra-batch
   - overlap shifts existants (jamais écraser)
   - absences + leave_requests APPROVED
   - shift_locks (shift verrouillé = intouchable)
   - holidays (avertissement)
   - contract_from / contract_to (avertissement)
       │
       ▼
shift_proposals (brouillon, statut proposed)
       │
       ▼  POST explicite « Appliquer la sélection »
       ▼
INSERT dans shifts (jamais UPDATE ni DELETE)
   + éventuel INSERT dans shift_locks si case cochée
```

Aucune publication automatique vers les employés. Aucun email généré.

---

## 4. Instructions techniques précises pour Codex (reprise)

Chaque phase = un commit atomique. Ne pas combiner. Pas de push vers Render
tant que le propriétaire n'a pas donné le feu vert.

### Phase 1 — **TERMINÉE** (non committée)

- Traductions `auto_plan_*` × 5 langues ✅
- Tables `shift_locks`, `shift_proposal_batches`, `shift_proposals` dans
  `init_db()` ✅
- → **Action immédiate** : committer ces changements en un seul commit
  `Auto-planning: schema + i18n (phase 1)` sur la branche `main`. Message
  commit à décrire : nouvelles tables, aucune route, aucune ALTER sur
  tables existantes.

### Phase 2 — Moteur de récurrence pur Python

Ajouter une section `# AUTO-PLANNING — moteur` à `app.py` (bas de fichier
pour isoler). Fonctions à écrire, toutes sans effet de bord HTTP :

```python
def ap_lookback_bounds(target_week_start, weeks):
    # retourne (start_date, end_date) de la fenêtre d'analyse
def ap_fetch_history(conn, start_date, end_date):
    # SELECT * FROM shifts WHERE date >= ? AND date <= ?
def ap_recurrence_key(shift):
    # (client, worker_signature(worker_text), dow, start_hhmm, end_hhmm)
def ap_group_by_key(shifts):
    # dict[key] = list of ISO dates
def ap_period_weeks(dates):
    # médiane des écarts en semaines entre occurrences consécutives
def ap_confidence(occurrences, period_weeks, lookback_weeks):
    # ratio occurrences / occurrences_attendues
def ap_classify(occurrences, period_weeks, confidence):
    # retourne (category: 1|2|3|4|'exceptional', confidence)
def ap_project(groups, target_week_start, include_exceptional, min_confidence=0.70):
    # retourne liste de propositions (date cible, time, client, worker, meta)
```

Tests `tests/test_auto_planning.py` à créer en parallèle :

- recurrence hebdo, bi, tri, mensuel, one-shot
- signature équipe insensible à l'ordre
- confiance 0.70 : filtrage correct des < 0.70 sauf si
  `include_exceptional=True`

### Phase 3 — Vérificateur de contraintes

```python
def ap_check_overlap_batch(proposals): ...
def ap_check_overlap_existing(conn, proposal): ...
def ap_check_shift_locked(conn, shift_id): ...
def ap_check_absence(conn, worker_name, date): ...
def ap_check_leave_approved(conn, worker_name, date): ...
def ap_check_holiday(conn, date): ...
def ap_check_contract_window(conn, client_name, date): ...
def ap_run_checks(conn, proposals, target_week_start):
    # décore chaque proposition avec sa liste 'issues'
```

Issues bloquantes : `overlap_batch`, `overlap_existing`, `absence`,
`leave_approved`, `locked`. Issues avertissement : `holiday`, `contract`.

Tests à ajouter : chaque check isolé + un cas d'intégration qui combine
batch overlap + absence.

### Phase 4 — Routes brouillon (lecture + draft, sans INSERT shifts)

```
GET  /plan/generate              — formulaire (semaine, lookback, options)
POST /plan/generate              — crée batch + proposals ; redirige vers review
GET  /plan/review?batch=<id>     — tableau par jour, actions par ligne
POST /plan/review/<batch>/update — accept / reject / modify / lock
```

Guard : `session.role == "admin"` sur toutes.
Lors du POST generate :
```python
conn = get_conn(); c = conn.cursor()
# Lire semaine cible, lookback, include_exceptional du formulaire
# → ap_fetch_history → ap_group_by_key → ap_project → ap_run_checks
# → INSERT INTO shift_proposal_batches, INSERT INTO shift_proposals × N
```

### Phase 5 — Flux « Appliquer » + verrou

```
POST /plan/review/<batch>/apply
```

- Pour chaque proposition `status='accepted'` :
  1. `duplicate_shift_exists()` → si faux, `INSERT INTO shifts (...)`,
     noter `applied_shift_id`.
  2. Si case « verrouiller » cochée, `INSERT INTO shift_locks(shift_id,
     locked_at, locked_by)`.
  3. `UPDATE shift_proposals SET status='applied',
     applied_shift_id=?, updated_at=?`.
- Marquer `batch.applied_at = lux_now()`.
- Flash : `auto_plan_applied` avec compteurs, `auto_plan_duplicate_skipped`.

### Phase 6 — UI finale + boutons

- Bouton **« ✨ Générer le planning automatiquement »** sur `/` (zone
  admin) et sur `/week` (barre d'actions).
- Page de revue inspirée visuellement de `/week`, grille 7 jours, cards.
- Chaque proposition : badges (statut, confiance, cycle), menu
  Accept / Reject / Edit (inline worker + time) / Lock.
- Responsive mobile (identique au reste de l'app).
- `ne lance pas` d'autre migration ; la phase 1 suffit.

### Phase 7 — Audit de régression final

Avant de valider la feature complète :

- `rg "DELETE FROM shifts" app.py` → chaque occurrence doit aussi
  `DELETE FROM shift_locks`.
- `rg "SELECT \* FROM shifts" app.py` → vérifier que l'ajout de nouvelles
  tables ne change pas la forme de ces lectures (il ne change rien, mais
  double-check).
- Smoke : `/`, `/week`, `/month`, `/clients`, `/invoices`,
  `/invoices/view`, `/invoices/manual`, `/invoices/email` → aucun 500.
- `python -m py_compile app.py` → OK.

---

## 5. Hors scope confirmé

- Aucun service IA/LLM payant.
- Aucune refonte du modèle `shifts.worker` (reste chaîne séparée par
  virgules).
- Aucun envoi d'email / notification employés par cette feature.
- Aucune modification de la facturation, PDFs, rappels, IMAP, file emails,
  tax overview, pretraga klijenata.

---

## 6. Décisions résiduelles reportées (si nécessaire plus tard)

- Multi-mois dans une même proposition (actuellement : clé par semaine).
- Suggestions d'équipes alternatives (champ `alternative_workers` déjà
  prévu dans le schéma, UI reportée après phase 6).
- Historique des batches (page `/plan/history`) — pas en scope.

---

Fin du plan initial. Voir l'état d'implémentation ci-dessous.

---

## 7. État d'implémentation local

Les phases 1 à 5 ont été validées et committées sur la branche
`feature/auto-planning`. La phase 6 est maintenant implémentée et vérifiée
localement ; elle doit encore être validée par le propriétaire avant le
démarrage de la phase 7.

### Phase 5 — modification, validation et application

- Chaque proposition peut être modifiée indépendamment (client, équipe,
  date, heure et statut) sans écrire dans `shifts`.
- Les actions accepter, refuser, remettre en attente, accepter une sélection,
  appliquer une sélection et annuler le brouillon sont disponibles.
- Toute modification est revalidée et les contrôles sont relancés juste avant
  l'application. Les conflits bloquants, absences, employés manquants,
  doublons et interventions verrouillées empêchent l'insertion.
- L'application est atomique et idempotente. Elle sérialise les mutations du
  batch et les vérifications/écritures de `shifts`, puis insère uniquement les
  propositions explicitement acceptées et sélectionnées.
- Les brouillons appliqués ou annulés sont fermés et ne peuvent plus être
  modifiés ni appliqués une seconde fois.
- Les nouvelles routes POST du planning automatique utilisent un jeton CSRF
  lié à la session. Elles vérifient aussi le rôle administrateur ainsi que le
  propriétaire et la session d'origine du brouillon.
- Les interventions réelles peuvent être verrouillées/déverrouillées depuis
  la revue. Une intervention verrouillée ne peut pas être supprimée ; le
  nettoyage de `shift_locks` et la suppression de `shifts` sont réalisés dans
  une même transaction après déverrouillage.
- Aucune migration supplémentaire n'est nécessaire pour cette phase. Aucun
  accès à PostgreSQL de production, push ou déploiement n'est effectué.

Tests locaux : la suite utilise une base SQLite temporaire et couvre les
20 scénarios obligatoires de la phase 5, en plus des tests des phases 2 à 4.

### Phase 6 — intégration visuelle et ergonomique

- Les administrateurs accèdent à la génération depuis le tableau de bord,
  `/week` et `/month`. Le formulaire `/plan/generate` expose la semaine cible,
  les historiques 2 / 4 / 8 semaines et l'option des interventions
  exceptionnelles, avec protection CSRF inchangée.
- `/plan/review` présente le brouillon par jour avec les interventions
  existantes, les verrous, les statuts, la confiance, la récurrence, la durée,
  les avertissements et les conflits bloquants. Le brouillon est explicitement
  signalé comme non confirmé.
- Les formulaires de la phase 5 sont intégrés sans dupliquer leur logique :
  modification individuelle, acceptation, refus, remise en attente, sélection
  multiple, application et annulation du brouillon.
- L'application affiche le nombre sélectionné et les conflits bloquants dans
  une confirmation, puis désactive le bouton afin d'éviter un double clic.
  Seules les propositions acceptées et cochées passent par la transaction de
  la phase 5.
- Les interventions verrouillées sont visibles dans la revue et dans `/week`.
  Elles ne sont pas déplaçables et leurs actions de modification sont masquées,
  en complément des contrôles serveur déjà validés.
- L'interface utilise les cinq langues existantes et s'adapte en quatre
  colonnes sur grand écran, deux sur tablette et une sur téléphone.
- Vérification locale : 69 tests automatisés sur SQLite isolé, compilation
  Python et parcours visuel avec Chromium. Aucun accès PostgreSQL de
  production, aucune migration, aucun push et aucun déploiement.
