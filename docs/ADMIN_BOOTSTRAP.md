# Bootstrapping the admin account

This is the exact, safe procedure for creating the platform's admin identity,
`admin@amazai.co`, as a full-capability Owner. There is no separate "admin"
role to invent: an Owner already holds every capability in the RBAC matrix
(`services/amazai/directory.py`, `CAPABILITIES`), so "full admin" is simply an
**ACTIVE Owner** membership plus the owner allowlist.

## No password is ever stored

`admin@amazai.co`'s password is set by the human, in Auth0, at first login.

- The bootstrap script reads no password and writes no password.
- No password appears in this repo, its tests, its fixtures, its scripts, or
  its git history. A suite-level guard test enforces that
  (`tests/test_bootstrap_admin.py`).
- The owner allowlist environment variables below are **not** credentials.
  They name who the Owner is; they do not authenticate anyone. Authentication
  is Auth0's job.

The human logs in as `admin@amazai.co` with the password they set in Auth0.

## The three steps

### 1. Create the user in Auth0

Create `admin@amazai.co` in the Auth0 tenant the console signs into
(`VITE_AUTH0_DOMAIN`). The human sets and owns the password there. Note the
user's Auth0 `sub` if the dashboard shows it; it is the only identifier that
cannot be re-registered, so it is worth capturing. It is fine not to have it
yet -- see the allowlist note below.

### 2. Set the owner allowlist (deploy-time configuration, never a password)

The API's own check is `identity.assert_owner`, which reads these from the
Lambda environment (see the root `.env.example`):

```
OWNER_EMAILS=admin@amazai.co
OWNER_SUBJECTS=auth0|<admin-sub-once-known>
```

and the console build reads (see `web/.env.example`):

```
VITE_OWNER_EMAIL=admin@amazai.co
```

Email is the primary key while the Auth0 `sub` is still unknown. A `sub` cannot
be re-registered, so fill `OWNER_SUBJECTS` / `VITE_OWNER_SUB` once you know it;
the subject wins over email when both are set. Leaving all of these blank opens
the surface to any authenticated user, which is only appropriate for a local
dev run.

### 3. Seat the ACTIVE Owner row

Run the bootstrap script. It writes the ACTIVE Owner `MEMBER#` row plus one
append-only admin-audit row recording the bootstrap:

```bash
OWNER_ID=<caller-sub> python3 scripts/bootstrap_admin.py --subject <admin-auth0-sub>
```

If you do not yet know the admin's `sub`, omit `--subject`; the script keys on
the email as a documented placeholder subject and a later re-run supplies the
real `sub`.

The script is **idempotent**: if the Owner row already exists it reports that
and exits 0 without writing a duplicate. Use `--dry-run` to preview.

## Single-tenant seam

Today `store.Store` stamps `ownerId` == the caller and `orgId` == the subject
(`docs/architecture/03-data-model.md`), so one org == one owner. The bootstrap
therefore seats `admin@amazai.co` as the Owner of **its own** org. A true
cross-account admin, one identity governing *other* owners' orgs, is the
deferred multi-tenant follow-up. It is deliberately not built here; the
`(subject, role, scope, scope_id)` tuple is modelled now so that it is a
feature later rather than a migration.
