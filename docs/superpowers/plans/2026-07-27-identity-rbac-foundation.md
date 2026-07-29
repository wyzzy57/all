# VisiOX Identity and RBAC Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a production-shaped single-organization login, user, group, session, resource grant, allocation policy, audit, and frontend account foundation without yet enforcing ownership on existing business resources.

**Architecture:** Store identities and authorization metadata in PostgreSQL, use Argon2id for passwords, signed short-lived JWT access tokens, and rotating opaque refresh tokens stored as hashes. Expose shared FastAPI authentication and authorization dependencies, then drive a Pinia session store, router guards, login page, administrator pages, and the bottom-left account menu.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, argon2-cffi, PyJWT, PostgreSQL/SQLite tests, Vue 3, Pinia, Vue Router, Element Plus, Vitest.

---

## File Map

**Backend create:**

- `packages/visiox-db/src/visiox_db/models/identity.py`: identity, session, grant, allocation, and audit ORM models.
- `apps/api-service/src/visiox_api/dependencies/database.py`: shared SQLAlchemy session dependency for new modules.
- `apps/api-service/src/visiox_api/dependencies/auth.py`: current-user and administrator dependencies.
- `apps/api-service/src/visiox_api/schemas/identity.py`: stable request and response schemas.
- `apps/api-service/src/visiox_api/services/security.py`: password, access token, and refresh token primitives.
- `apps/api-service/src/visiox_api/services/authorization.py`: permission merge and resource checks.
- `apps/api-service/src/visiox_api/services/audit.py`: append-only audit helper.
- `apps/api-service/src/visiox_api/services/bootstrap_admin.py`: default organization and initial administrator bootstrap.
- `apps/api-service/src/visiox_api/routes/auth.py`: login, refresh, logout, and current session routes.
- `apps/api-service/src/visiox_api/routes/account.py`: profile and password routes.
- `apps/api-service/src/visiox_api/routes/admin_users.py`: administrator user APIs.
- `apps/api-service/src/visiox_api/routes/admin_groups.py`: administrator group and membership APIs.
- `apps/api-service/src/visiox_api/routes/admin_authorization.py`: grant, allocation, and audit APIs.
- `infra/migrations/versions/20260727_0001_identity_rbac_foundation.py`: additive identity schema.

**Backend modify:**

- `pyproject.toml`: add password and JWT libraries.
- `.env.example`: document authentication and bootstrap secrets.
- `packages/visiox-common/src/visiox_common/settings.py`: authentication settings and secret-file readers.
- `packages/visiox-db/src/visiox_db/models/__init__.py`: export identity models.
- `apps/api-service/src/visiox_api/main.py`: bootstrap identities and register routers.

**Frontend create:**

- `apps/frontend/src/stores/auth.ts`: session state and actions.
- `apps/frontend/src/components/account/UserAccountMenu.vue`: bottom-left user entry.
- `apps/frontend/src/views/auth/LoginView.vue`: login form.
- `apps/frontend/src/views/account/AccountView.vue`: profile and password form.
- `apps/frontend/src/views/admin/UserManagementView.vue`: user list and editor.
- `apps/frontend/src/views/admin/GroupManagementView.vue`: group membership editor.
- `apps/frontend/src/views/admin/AuthorizationView.vue`: grant and allocation shell.

**Frontend modify:**

- `apps/frontend/src/api/client.ts`: bearer tokens, one-time refresh, identity types, and identity APIs.
- `apps/frontend/src/router/index.ts`: login, account, admin routes, and guards.
- `apps/frontend/src/App.vue`: authenticated shell and bottom-left account menu.
- `apps/frontend/src/styles.css`: account menu and authentication shell tokens.

**Tests create:**

- `tests/unit/test_auth_security.py`
- `tests/unit/test_authorization.py`
- `tests/integration/test_auth_api.py`
- `tests/integration/test_admin_identity_api.py`
- `apps/frontend/tests/auth-store.spec.ts`
- `apps/frontend/tests/login-view.spec.ts`
- `apps/frontend/tests/account-menu.spec.ts`
- `apps/frontend/tests/admin-identity-views.spec.ts`

**Tests modify:**

- `tests/integration/test_migrations.py`
- `tests/integration/conftest.py`
- `apps/frontend/tests/router.spec.ts`
- `apps/frontend/tests/app-layout.spec.ts`

### Task 1: Add Authentication Dependencies and Settings

**Files:**

- Modify: `pyproject.toml`
- Modify: `.env.example`
- Modify: `packages/visiox-common/src/visiox_common/settings.py`
- Test: `tests/unit/test_settings.py`

- [ ] **Step 1: Write failing settings tests**

Add tests that construct `Settings(_env_file=None, auth_jwt_secret_file=tmp_path / "jwt.key", bootstrap_admin_password_file=tmp_path / "admin-password")`, write a 32-byte JWT secret and a non-empty administrator password, and assert both reader methods return their values. Add failure assertions for missing files, JWT secrets shorter than 32 bytes, and empty bootstrap passwords.

```python
def test_auth_secret_files_are_validated(tmp_path) -> None:
    jwt_file = tmp_path / "jwt.key"
    password_file = tmp_path / "admin-password"
    jwt_file.write_bytes(b"j" * 32)
    password_file.write_text("ChangeMe-2026!", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        auth_jwt_secret_file=jwt_file,
        bootstrap_admin_password_file=password_file,
    )
    assert settings.read_auth_jwt_secret() == b"j" * 32
    assert settings.read_bootstrap_admin_password() == "ChangeMe-2026!"
```

- [ ] **Step 2: Run the test and confirm failure**

Run: `python -m pytest tests/unit/test_settings.py -q`

Expected: failure because authentication settings and readers do not exist.

- [ ] **Step 3: Add bounded dependencies and settings**

Add:

```toml
"argon2-cffi>=23.1,<26.0",
"PyJWT>=2.9,<3.0",
```

Add settings with these exact defaults:

```python
auth_jwt_secret_file: Path = Path("/run/secrets/visiox-auth-jwt-secret")
auth_access_token_minutes: int = Field(default=15, ge=5, le=60)
auth_refresh_token_days: int = Field(default=7, ge=1, le=30)
auth_refresh_cookie_name: str = "visiox_refresh"
auth_cookie_secure: bool = True
bootstrap_admin_username: str = "admin"
bootstrap_admin_email: str = "admin@localhost"
bootstrap_admin_password_file: Path = Path("/run/secrets/visiox-bootstrap-admin-password")
```

Implement `read_auth_jwt_secret()` with an exact minimum of 32 bytes and `read_bootstrap_admin_password()` with a stripped non-empty UTF-8 value. Document matching `VISIOX_*` variables and secret-file mounts in `.env.example`.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/unit/test_settings.py -q`

Expected: all settings tests pass.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .env.example packages/visiox-common/src/visiox_common/settings.py tests/unit/test_settings.py
git commit -m "feat: add identity security settings"
```

### Task 2: Add Identity ORM Models and Migration

**Files:**

- Create: `packages/visiox-db/src/visiox_db/models/identity.py`
- Modify: `packages/visiox-db/src/visiox_db/models/__init__.py`
- Create: `infra/migrations/versions/20260727_0001_identity_rbac_foundation.py`
- Modify: `tests/integration/test_migrations.py`
- Test: `tests/integration/test_admin_identity_api.py`

- [ ] **Step 1: Add a failing migration assertion**

Extend the migration test to assert that upgrading to head creates:

```python
expected_tables = {
    "organizations",
    "users",
    "user_groups",
    "user_group_memberships",
    "user_sessions",
    "resource_grants",
    "resource_allocation_policies",
    "audit_logs",
}
assert expected_tables <= set(inspector.get_table_names())
```

- [ ] **Step 2: Run the migration test and confirm failure**

Run: `python -m pytest tests/integration/test_migrations.py -q`

Expected: failure listing the missing identity tables.

- [ ] **Step 3: Implement ORM models**

Use `IdMixin` and `TimestampMixin`. Define string constants for roles, statuses, principal types, and permission names. Use JSON lists for permissions and JSON metadata for audit details. Add these uniqueness rules:

```python
UniqueConstraint("organization_id", "username", name="uq_users_org_username")
UniqueConstraint("organization_id", "email", name="uq_users_org_email")
UniqueConstraint("organization_id", "name", name="uq_user_groups_org_name")
UniqueConstraint("group_id", "user_id", name="uq_user_group_membership")
UniqueConstraint(
    "organization_id",
    "resource_type",
    "resource_id",
    "principal_type",
    "principal_id",
    name="uq_resource_grant_principal",
)
```

Store only `refresh_token_hash`, never the raw refresh token. Index user status, session expiry, grant resource identity, allocation principal, and audit creation time.

- [ ] **Step 4: Create the additive Alembic migration**

Set `down_revision = "20260724_0002"`. Create tables in foreign-key order and remove them in reverse order. The migration must not alter existing business tables in this phase.

- [ ] **Step 5: Run migration and model tests**

Run: `python -m pytest tests/integration/test_migrations.py tests/unit/test_db_session.py -q`

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add packages/visiox-db/src/visiox_db/models/identity.py packages/visiox-db/src/visiox_db/models/__init__.py infra/migrations/versions/20260727_0001_identity_rbac_foundation.py tests/integration/test_migrations.py
git commit -m "feat: add identity and authorization schema"
```

### Task 3: Implement Password and Token Primitives

**Files:**

- Create: `apps/api-service/src/visiox_api/services/security.py`
- Create: `tests/unit/test_auth_security.py`

- [ ] **Step 1: Write failing security tests**

Cover password hash/verify, wrong password rejection, access token expiry and claims, opaque refresh token length, and deterministic SHA-256 refresh-token hashing.

```python
def test_password_and_access_token_round_trip() -> None:
    password_hash = hash_password("S3cure-password")
    assert verify_password(password_hash, "S3cure-password") is True
    assert verify_password(password_hash, "wrong") is False
    token = issue_access_token(
        secret=b"s" * 32,
        user_id="user-1",
        organization_id="org-1",
        role="member",
        lifetime=timedelta(minutes=15),
    )
    claims = decode_access_token(token, secret=b"s" * 32)
    assert claims["sub"] == "user-1"
    assert claims["org"] == "org-1"
```

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/unit/test_auth_security.py -q`

Expected: import failure because the security service does not exist.

- [ ] **Step 3: Implement security primitives**

Use `argon2.PasswordHasher()` and map verification mismatch to `False`. Issue HS256 JWTs with `sub`, `org`, `role`, `iat`, `exp`, `jti`, issuer `visiox`, and audience `visiox-api`. Generate refresh tokens using `secrets.token_urlsafe(48)` and hash them with SHA-256.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/unit/test_auth_security.py -q`

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add apps/api-service/src/visiox_api/services/security.py tests/unit/test_auth_security.py
git commit -m "feat: add password and session token security"
```

### Task 4: Bootstrap the Default Organization and Administrator

**Files:**

- Create: `apps/api-service/src/visiox_api/services/bootstrap_admin.py`
- Modify: `apps/api-service/src/visiox_api/main.py`
- Test: `tests/integration/test_auth_api.py`

- [ ] **Step 1: Write failing idempotency tests**

Create a migrated SQLite database, call the bootstrap function twice, and assert one organization and one administrator exist. Assert the administrator has `must_change_password=True` and a valid password hash.

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/integration/test_auth_api.py -q`

Expected: bootstrap import failure.

- [ ] **Step 3: Implement transactional bootstrap**

Use organization slug `default` and the configured username/email. If the user already exists, do not overwrite role, status, or password. If no bootstrap password file is available in local tests, tests must inject settings; production startup must fail with an actionable message rather than create a known password.

- [ ] **Step 4: Call bootstrap during FastAPI lifespan**

Run it before base-model seeding with a fresh database session. Log only the administrator username and whether creation occurred.

- [ ] **Step 5: Run focused tests**

Run: `python -m pytest tests/integration/test_auth_api.py -q`

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add apps/api-service/src/visiox_api/services/bootstrap_admin.py apps/api-service/src/visiox_api/main.py tests/integration/test_auth_api.py
git commit -m "feat: bootstrap the initial administrator"
```

### Task 5: Implement Login, Refresh, Logout, and Current-User APIs

**Files:**

- Create: `apps/api-service/src/visiox_api/dependencies/database.py`
- Create: `apps/api-service/src/visiox_api/dependencies/auth.py`
- Create: `apps/api-service/src/visiox_api/schemas/identity.py`
- Create: `apps/api-service/src/visiox_api/routes/auth.py`
- Modify: `apps/api-service/src/visiox_api/main.py`
- Test: `tests/integration/test_auth_api.py`

- [ ] **Step 1: Write failing API tests**

Test successful login, wrong password, disabled user, current-user response, access-token rejection, refresh rotation, reuse of a revoked refresh token, and logout. The successful response must be:

```json
{
  "access_token": "signed-token",
  "token_type": "bearer",
  "expires_in": 900,
  "user": {
    "id": "user-id",
    "username": "admin",
    "display_name": "Administrator",
    "email": "admin@localhost",
    "role": "admin",
    "status": "active",
    "must_change_password": true
  }
}
```

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/integration/test_auth_api.py -q`

Expected: 404 responses for missing auth routes.

- [ ] **Step 3: Implement shared dependencies and schemas**

`get_current_user` must parse `Authorization: Bearer`, decode claims, load the user from PostgreSQL, and reject disabled/deleted users. `require_admin` must return 403 for members.

- [ ] **Step 4: Implement rotating refresh sessions**

On login, store a hashed opaque token and set the raw token as an HttpOnly cookie. On refresh, revoke the old session and create a new one in one transaction. On logout, revoke the presented session and delete the cookie.

- [ ] **Step 5: Register the router and run tests**

Run: `python -m pytest tests/integration/test_auth_api.py -q`

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add apps/api-service/src/visiox_api/dependencies apps/api-service/src/visiox_api/schemas/identity.py apps/api-service/src/visiox_api/routes/auth.py apps/api-service/src/visiox_api/main.py tests/integration/test_auth_api.py
git commit -m "feat: add authenticated browser sessions"
```

### Task 6: Implement Resource Authorization and Audit Services

**Files:**

- Create: `apps/api-service/src/visiox_api/services/authorization.py`
- Create: `apps/api-service/src/visiox_api/services/audit.py`
- Create: `tests/unit/test_authorization.py`

- [ ] **Step 1: Write the permission matrix tests**

Cover administrator bypass, resource owner permissions, direct user grants, group grants, organization-public grants, expired grants, union of multiple grants, and denial.

```python
def test_group_and_public_permissions_are_merged(session) -> None:
    permissions = resolve_permissions(
        session,
        actor=member,
        resource_type="dataset",
        resource_id="dataset-1",
        owner_user_id="owner-1",
    )
    assert permissions == {"view", "use"}
```

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/unit/test_authorization.py -q`

Expected: authorization import failure.

- [ ] **Step 3: Implement permission resolution**

Return all permissions for administrators and owners. Otherwise union active user, group, and organization grants, excluding expired grants. Implement `assert_permission(session: Session, actor: User, resource_type: str, resource_id: str, owner_user_id: str, permission: str) -> None` that raises a domain exception containing `code`, `resource_type`, `resource_id`, and `permission`.

- [ ] **Step 4: Implement append-only audit writes**

Expose `record_audit(session, actor, action, resource_type, resource_id, result, request_id, metadata)`. Recursively redact keys matching password, token, secret, cookie, private_key, and authorization.

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/unit/test_authorization.py -q`

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add apps/api-service/src/visiox_api/services/authorization.py apps/api-service/src/visiox_api/services/audit.py tests/unit/test_authorization.py
git commit -m "feat: add object authorization and audit services"
```

### Task 7: Add Administrator User, Group, Grant, and Allocation APIs

**Files:**

- Create: `apps/api-service/src/visiox_api/routes/admin_users.py`
- Create: `apps/api-service/src/visiox_api/routes/admin_groups.py`
- Create: `apps/api-service/src/visiox_api/routes/admin_authorization.py`
- Create: `apps/api-service/src/visiox_api/routes/account.py`
- Modify: `apps/api-service/src/visiox_api/main.py`
- Test: `tests/integration/test_admin_identity_api.py`

- [ ] **Step 1: Write failing administrator API tests**

Test admin-only access, user creation, duplicate username/email conflict, disable/enable, role change, password reset, soft deletion, group CRUD, membership replacement, grant upsert/revoke, allocation upsert/revoke, profile update, and password change.

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/integration/test_admin_identity_api.py -q`

Expected: 404 responses for administrator and account routes.

- [ ] **Step 3: Implement user and account routes**

Never return password hashes. User creation accepts an explicit temporary password or generates a cryptographically random one returned exactly once. Set `must_change_password=True`. Disabling or deleting a user revokes all active sessions.

- [ ] **Step 4: Implement groups, grants, and allocation routes**

Membership replacement must validate all users belong to the default organization. Grant permissions must be a subset of `view`, `use`, `edit`, `delete`, `manage`, and `invoke`. Allocation limits must be non-negative and require an existing resource pool ID.

- [ ] **Step 5: Record audits and run tests**

Run: `python -m pytest tests/integration/test_admin_identity_api.py tests/integration/test_auth_api.py -q`

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add apps/api-service/src/visiox_api/routes/admin_users.py apps/api-service/src/visiox_api/routes/admin_groups.py apps/api-service/src/visiox_api/routes/admin_authorization.py apps/api-service/src/visiox_api/routes/account.py apps/api-service/src/visiox_api/main.py tests/integration/test_admin_identity_api.py
git commit -m "feat: add identity administration APIs"
```

### Task 8: Add the Frontend Authentication Store and API Transport

**Files:**

- Create: `apps/frontend/src/stores/auth.ts`
- Modify: `apps/frontend/src/api/client.ts`
- Create: `apps/frontend/tests/auth-store.spec.ts`
- Modify: `apps/frontend/tests/api-client.spec.ts`

- [ ] **Step 1: Write failing store and transport tests**

Test login state, current-user bootstrap, logout, refresh success, one refresh attempt after 401, replay of the original request, and final logout when refresh fails.

- [ ] **Step 2: Run and confirm failure**

Run from `apps/frontend`: `npm test -- --run tests/auth-store.spec.ts tests/api-client.spec.ts`

Expected: store import and identity API failures.

- [ ] **Step 3: Implement identity API types and requests**

Add `AuthenticatedUser`, `LoginResponse`, `UserRecord`, `UserGroupRecord`, `ResourceGrantRecord`, and `ResourceAllocationRecord`. Keep the access token in module memory and attach it as a bearer header. Add a single shared refresh promise so simultaneous 401 responses do not rotate the refresh token multiple times.

- [ ] **Step 4: Implement the Pinia store**

State: `user`, `accessToken`, `expiresAt`, `initialized`, and `loading`. Actions: `initialize`, `login`, `refresh`, `logout`, `updateProfile`, and `changePassword`. Computed values: `isAuthenticated` and `isAdmin`.

- [ ] **Step 5: Run focused tests**

Run from `apps/frontend`: `npm test -- --run tests/auth-store.spec.ts tests/api-client.spec.ts`

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add apps/frontend/src/stores/auth.ts apps/frontend/src/api/client.ts apps/frontend/tests/auth-store.spec.ts apps/frontend/tests/api-client.spec.ts
git commit -m "feat: add frontend authentication session"
```

### Task 9: Add Login and Router Guards

**Files:**

- Create: `apps/frontend/src/views/auth/LoginView.vue`
- Modify: `apps/frontend/src/router/index.ts`
- Create: `apps/frontend/tests/login-view.spec.ts`
- Modify: `apps/frontend/tests/router.spec.ts`

- [ ] **Step 1: Write failing view and guard tests**

Test login submission, visible error, redirect to the original path, authenticated users leaving `/login`, members denied from `/admin/*`, and administrators admitted.

- [ ] **Step 2: Run and confirm failure**

Run from `apps/frontend`: `npm test -- --run tests/login-view.spec.ts tests/router.spec.ts`

Expected: missing login route and guard behavior.

- [ ] **Step 3: Implement the login page**

Use a centered, restrained form with username and password fields, loading state, inline error, submit button, and VisiOX brand. Do not include account self-registration. Disable submit while either field is empty.

- [ ] **Step 4: Implement route metadata and guards**

All business routes require authentication. `/login` is public. `/admin/*` uses `requiresAdmin`. Initialize the auth store once before resolving a guarded route. Preserve the destination as `redirect` query data.

- [ ] **Step 5: Run tests**

Run from `apps/frontend`: `npm test -- --run tests/login-view.spec.ts tests/router.spec.ts`

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add apps/frontend/src/views/auth/LoginView.vue apps/frontend/src/router/index.ts apps/frontend/tests/login-view.spec.ts apps/frontend/tests/router.spec.ts
git commit -m "feat: add login and protected routes"
```

### Task 10: Add Account Menu and Identity Management Views

**Files:**

- Create: `apps/frontend/src/components/account/UserAccountMenu.vue`
- Create: `apps/frontend/src/views/account/AccountView.vue`
- Create: `apps/frontend/src/views/admin/UserManagementView.vue`
- Create: `apps/frontend/src/views/admin/GroupManagementView.vue`
- Create: `apps/frontend/src/views/admin/AuthorizationView.vue`
- Modify: `apps/frontend/src/App.vue`
- Modify: `apps/frontend/src/styles.css`
- Create: `apps/frontend/tests/account-menu.spec.ts`
- Create: `apps/frontend/tests/admin-identity-views.spec.ts`
- Modify: `apps/frontend/tests/app-layout.spec.ts`

- [ ] **Step 1: Write failing component tests**

Assert the account entry is pinned to the sidebar bottom, shows avatar/name/role when expanded, shows only the avatar when collapsed, includes profile/logout for members, and includes user/group/resource administration only for administrators.

- [ ] **Step 2: Run and confirm failure**

Run from `apps/frontend`: `npm test -- --run tests/account-menu.spec.ts tests/admin-identity-views.spec.ts tests/app-layout.spec.ts`

Expected: missing components and routes.

- [ ] **Step 3: Implement the account menu and account page**

Use Element Plus dropdown/menu primitives with keyboard access. Keep the existing white content canvas and gray sidebar gutter. The account page separates profile editing from password change and displays the forced-password-change banner until completed.

- [ ] **Step 4: Implement administrator views**

User view: search, status/role filters, create dialog, enable/disable, reset password, and soft delete. Group view: group list, member count, create/edit dialog, and member selector. Authorization view: an initial functional table for grant/allocation APIs; resource-specific assignment UI is expanded in Phase 2 and Phase 3.

- [ ] **Step 5: Run frontend tests and type checking**

Run from `apps/frontend`:

```bash
npm test -- --run tests/account-menu.spec.ts tests/admin-identity-views.spec.ts tests/app-layout.spec.ts
npm run typecheck
```

Expected: all commands pass.

- [ ] **Step 6: Commit**

```bash
git add apps/frontend/src/components/account apps/frontend/src/views/account apps/frontend/src/views/admin apps/frontend/src/App.vue apps/frontend/src/styles.css apps/frontend/tests/account-menu.spec.ts apps/frontend/tests/admin-identity-views.spec.ts apps/frontend/tests/app-layout.spec.ts
git commit -m "feat: add account and identity administration UI"
```

### Task 11: Add a Shared Authenticated Integration Fixture

**Files:**

- Modify: `tests/integration/conftest.py`
- Modify: `tests/integration/test_auth_api.py`
- Modify: `tests/integration/test_admin_identity_api.py`

- [ ] **Step 1: Add a failing fixture consumer**

Use an `authenticated_client` fixture that returns a `TestClient`, administrator bearer headers, member bearer headers, the migrated session factory, and the corresponding user IDs.

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/integration/test_auth_api.py tests/integration/test_admin_identity_api.py -q`

Expected: fixture not found.

- [ ] **Step 3: Implement the fixture**

Create isolated Settings secret files under `tmp_path`, upgrade Alembic to head, bootstrap identities, override the shared database dependency, log in through the real auth route, and return both header sets. Do not bypass token validation in integration tests.

- [ ] **Step 4: Migrate identity tests to the fixture**

Remove duplicate database and login setup while preserving explicit refresh-cookie assertions.

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/integration/test_auth_api.py tests/integration/test_admin_identity_api.py -q`

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add tests/integration/conftest.py tests/integration/test_auth_api.py tests/integration/test_admin_identity_api.py
git commit -m "test: add authenticated API fixtures"
```

### Task 12: Verify Phase 1 End to End

**Files:**

- Modify only if verification exposes defects in files already listed above.

- [ ] **Step 1: Run backend identity tests**

Run:

```bash
python -m pytest tests/unit/test_settings.py tests/unit/test_auth_security.py tests/unit/test_authorization.py tests/integration/test_migrations.py tests/integration/test_auth_api.py tests/integration/test_admin_identity_api.py -q
```

Expected: all pass.

- [ ] **Step 2: Run the existing backend regression suite**

Run: `python -m pytest tests/unit tests/integration -q`

Expected: all pass. Existing route tests may continue to instantiate route-only FastAPI apps without authentication until object-level enforcement starts in Phase 3.

- [ ] **Step 3: Run frontend tests and build**

Run from `apps/frontend`:

```bash
npm test -- --run
npm run typecheck
npm run build
```

Expected: all pass.

- [ ] **Step 4: Run manual browser acceptance**

Verify: initial administrator login, forced password change, member creation, group membership, member login, administrator route denial for the member, sidebar collapse behavior, account update, logout, refresh after page reload, and disabled-user session rejection.

- [ ] **Step 5: Record migration evidence**

Capture the table list and identity row counts before and after a clean bootstrap and a second idempotent bootstrap. Store the command output in the task notes, not in source control.

- [ ] **Step 6: Commit final fixes**

```bash
git add pyproject.toml .env.example packages/visiox-common packages/visiox-db apps/api-service apps/frontend infra/migrations tests
git commit -m "test: verify identity and RBAC foundation"
```
