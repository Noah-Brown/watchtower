"""initial schema

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE harness (
        slug            text PRIMARY KEY,
        display_name    text NOT NULL,
        pricing_json    jsonb NOT NULL DEFAULT '{}',
        adapter_version text
    );

    CREATE TABLE project (
        slug               text PRIMARY KEY,
        name               text NOT NULL,
        objective          text,
        phase              text,
        repo_paths         text[] NOT NULL DEFAULT '{}',
        owner              text,
        budget_usd_daily   numeric,
        budget_usd_monthly numeric,
        color              text
    );

    CREATE TABLE agent_session (
        id                 uuid PRIMARY KEY,
        harness            text NOT NULL REFERENCES harness(slug),
        harness_session_id text NOT NULL,
        project_slug       text REFERENCES project(slug),
        host               text,
        model              text,
        started_at         timestamptz NOT NULL,
        ended_at           timestamptz,
        status             text NOT NULL DEFAULT 'running',
        last_heartbeat     timestamptz,
        parent_id          uuid REFERENCES agent_session(id),
        cwd                text,
        branch             text,
        UNIQUE (harness, harness_session_id)
    );

    CREATE TABLE event (
        id         uuid PRIMARY KEY,
        session_id uuid NOT NULL REFERENCES agent_session(id),
        ts         timestamptz NOT NULL,
        seq        bigint NOT NULL,
        type       text NOT NULL,
        payload    jsonb NOT NULL DEFAULT '{}',
        UNIQUE (session_id, seq)
    );
    CREATE INDEX event_session_ts ON event (session_id, ts);
    CREATE INDEX event_type_ts ON event (type, ts);

    CREATE TABLE usage_ledger (
        id           uuid PRIMARY KEY,
        session_id   uuid NOT NULL REFERENCES agent_session(id),
        project_slug text REFERENCES project(slug),
        harness      text NOT NULL,
        model        text,
        ts           timestamptz NOT NULL,
        input        bigint NOT NULL DEFAULT 0,
        output       bigint NOT NULL DEFAULT 0,
        cache_read   bigint NOT NULL DEFAULT 0,
        cache_write  bigint NOT NULL DEFAULT 0,
        cost_usd     numeric
    );
    CREATE INDEX usage_ledger_project_ts ON usage_ledger (project_slug, ts);

    CREATE TABLE decision (
        id             uuid PRIMARY KEY,
        project_slug   text REFERENCES project(slug),
        session_id     uuid REFERENCES agent_session(id),
        kind           text NOT NULL,
        title          text NOT NULL,
        context        text,
        options        jsonb NOT NULL DEFAULT '[]',
        recommendation text,
        urgency        text NOT NULL DEFAULT 'normal',
        status         text NOT NULL DEFAULT 'open',
        answer         text,
        created_at     timestamptz NOT NULL DEFAULT now(),
        answered_at    timestamptz
    );
    CREATE INDEX decision_status ON decision (status, created_at);

    CREATE TABLE app (
        slug         text PRIMARY KEY,
        name         text NOT NULL,
        project_slug text REFERENCES project(slug),
        env          text NOT NULL DEFAULT 'prod',
        url          text,
        probe_config jsonb NOT NULL DEFAULT '{}',
        owner        text
    );

    CREATE TABLE app_health_sample (
        id          uuid PRIMARY KEY,
        app_slug    text NOT NULL REFERENCES app(slug),
        ts          timestamptz NOT NULL,
        ok          boolean NOT NULL,
        latency_ms  integer,
        status_code integer,
        error_rate  numeric,
        detail      jsonb
    );
    CREATE INDEX app_health_app_ts ON app_health_sample (app_slug, ts);

    CREATE TABLE deployment (
        id                   uuid PRIMARY KEY,
        app_slug             text NOT NULL REFERENCES app(slug),
        ref                  text NOT NULL,
        requested_by_session uuid REFERENCES agent_session(id),
        requested_at         timestamptz NOT NULL DEFAULT now(),
        approved_at          timestamptz,
        approved_by          text,
        deployed_at          timestamptz,
        status               text NOT NULL DEFAULT 'requested',
        notes                text
    );

    CREATE TABLE intake_item (
        id           uuid PRIMARY KEY,
        source       text NOT NULL DEFAULT 'manual',
        submitted_by text,
        title        text NOT NULL,
        body         text,
        status       text NOT NULL DEFAULT 'new',
        project_slug text REFERENCES project(slug),
        priority     text,
        created_at   timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE project_state (
        project_slug       text PRIMARY KEY REFERENCES project(slug),
        updated_at         timestamptz NOT NULL DEFAULT now(),
        updated_by_session uuid,
        summary_md         text,
        in_flight          text[] NOT NULL DEFAULT '{}',
        blockers           text[] NOT NULL DEFAULT '{}',
        next_up            text[] NOT NULL DEFAULT '{}'
    );
    """)


def downgrade():
    op.execute("""
    DROP TABLE project_state, intake_item, deployment, app_health_sample, app,
               decision, usage_ledger, event, agent_session, project, harness;
    """)
