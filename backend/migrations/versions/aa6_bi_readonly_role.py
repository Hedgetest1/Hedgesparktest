"""bi_readonly_role — Pro #3 BI Query DB-role-level defense

Creates a NOLOGIN PG role `wishspark_bi_readonly` with SELECT-only
grants on the 3 builder-reachable tables (shop_orders, events,
nudge_events). The app's main role is granted membership so
`SET LOCAL ROLE wishspark_bi_readonly` works within any tx.

Why role-based defense ON TOP OF SET TRANSACTION READ ONLY:
  - SET TRANSACTION READ ONLY is enforced per-transaction. A future
    code regression that drops/reorders that statement loses the
    protection.
  - SET LOCAL ROLE restricts to a role that LACKS write grants. Even
    if SET TRANSACTION READ ONLY were absent, write attempts fail
    with "permission denied" — strictly stronger guarantee.
  - PgBouncer-safe: SET LOCAL is transaction-scoped; the role
    resets at tx end and the connection returns clean to the pool.

Grant model:
  - SELECT on shop_orders, events, nudge_events (only the builder-
    allowlisted tables — any new BI table requires an explicit GRANT
    here)
  - USAGE on public schema (default for SELECT to resolve table names)
  - Membership of the readonly role granted to current_user (typically
    `aiuser` in this env) so the app can SET LOCAL ROLE.

Idempotent via pg_roles probe: re-running this migration is a no-op
when the role already exists (manual provisioning by ops, or staging
that's already been upgraded). Downgrade revokes + drops the role.

Revision ID: aa6_bi_readonly_role
Revises: aa5_bi_saved_queries
"""
from alembic import op


revision = "aa6_bi_readonly_role"
down_revision = "aa5_bi_saved_queries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create role idempotently. PG has no `CREATE ROLE IF NOT EXISTS`
    # so guard with a pg_roles existence check.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = 'wishspark_bi_readonly'
            ) THEN
                CREATE ROLE wishspark_bi_readonly NOLOGIN;
            END IF;
        END
        $$
    """)

    # Schema USAGE so SELECT can resolve table names. Idempotent
    # (re-granting USAGE is a no-op).
    op.execute(
        "GRANT USAGE ON SCHEMA public TO wishspark_bi_readonly"
    )

    # SELECT grants on the 3 BI-builder allowed tables. Idempotent
    # (re-granting SELECT is a no-op). NEW BI tables must extend this
    # list in a follow-up migration — keeping the grant explicit
    # forces a TIER_0 review of any future BI-reachable surface.
    op.execute(
        "GRANT SELECT ON shop_orders TO wishspark_bi_readonly"
    )
    op.execute(
        "GRANT SELECT ON events TO wishspark_bi_readonly"
    )
    op.execute(
        "GRANT SELECT ON nudge_events TO wishspark_bi_readonly"
    )

    # Grant membership to the app role so SET LOCAL ROLE works.
    # current_user inside the migration tx is the user the migration
    # runs as (typically aiuser in dev / a deploy user in prod).
    op.execute("""
        DO $$
        DECLARE
            app_role text := current_user;
        BEGIN
            EXECUTE format(
                'GRANT wishspark_bi_readonly TO %I', app_role
            );
        END
        $$
    """)


def downgrade() -> None:
    # Revoke + drop in reverse order. Idempotent.
    op.execute("""
        DO $$
        DECLARE
            app_role text := current_user;
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = 'wishspark_bi_readonly'
            ) THEN
                EXECUTE format(
                    'REVOKE wishspark_bi_readonly FROM %I', app_role
                );
            END IF;
        END
        $$
    """)
    op.execute(
        "REVOKE SELECT ON nudge_events FROM wishspark_bi_readonly"
    )
    op.execute(
        "REVOKE SELECT ON events FROM wishspark_bi_readonly"
    )
    op.execute(
        "REVOKE SELECT ON shop_orders FROM wishspark_bi_readonly"
    )
    op.execute(
        "REVOKE USAGE ON SCHEMA public FROM wishspark_bi_readonly"
    )
    op.execute("DROP ROLE IF EXISTS wishspark_bi_readonly")
