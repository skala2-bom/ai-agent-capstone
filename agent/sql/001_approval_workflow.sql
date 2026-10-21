-- Only the agent-owned schema. No business tables or backend migrations.
CREATE TABLE IF NOT EXISTS agent.workflow_command (
    command_id text PRIMARY KEY,
    task_id text NOT NULL,
    thread_id text NOT NULL,
    kind text NOT NULL,
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'PENDING',
    error text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS workflow_command_pending
    ON agent.workflow_command (status, created_at);
CREATE TABLE IF NOT EXISTS agent.approval_binding (
    proposal_id text PRIMARY KEY,
    task_id text NOT NULL,
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'SENDING',
    approval_id text UNIQUE,
    error text
);
CREATE TABLE IF NOT EXISTS agent.execution_attempt (
    approval_id text PRIMARY KEY,
    task_id text NOT NULL,
    proposal_id text NOT NULL,
    status text NOT NULL DEFAULT 'IN_FLIGHT',
    result jsonb,
    error text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent.workflow_outbox (
    id bigserial PRIMARY KEY,
    event_key text UNIQUE NOT NULL,
    task_id text NOT NULL,
    payload jsonb NOT NULL,
    delivered boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS workflow_outbox_pending
    ON agent.workflow_outbox (task_id, id) WHERE NOT delivered;
