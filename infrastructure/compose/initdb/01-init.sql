-- Email Agent database initialization
-- This runs automatically on first PostgreSQL startup

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Set timezone
SET TIMEZONE = 'UTC';

-- The tables are created by Alembic/SQLAlchemy on app startup
-- This file is for any raw SQL that needs to run before the app starts

-- Create indexes for common queries (Alembic will also create these)
-- These are here as documentation of the expected schema

-- Users table indexes
-- CREATE INDEX IF NOT EXISTS ix_users_keycloak_subject ON users(keycloak_subject);

-- Sessions table indexes
-- CREATE INDEX IF NOT EXISTS ix_sessions_user_id ON sessions(user_id);
-- CREATE INDEX IF NOT EXISTS ix_sessions_expires_at ON sessions(expires_at);

-- Email connections indexes
-- CREATE INDEX IF NOT EXISTS ix_email_connections_user_id ON email_connections(user_id);
-- CREATE INDEX IF NOT EXISTS ix_email_connections_status ON email_connections(status);

-- Agent runs indexes
-- CREATE INDEX IF NOT EXISTS ix_agent_runs_user_id ON agent_runs(user_id);
-- CREATE INDEX IF NOT EXISTS ix_agent_runs_thread_id ON agent_runs(thread_id);

-- Agent events indexes
-- CREATE INDEX IF NOT EXISTS ix_agent_events_run_id_seq ON agent_events(run_id, seq);

-- Proposals indexes
-- CREATE INDEX IF NOT EXISTS ix_approval_proposals_user_status ON approval_proposals(user_id, status);
-- CREATE INDEX IF NOT EXISTS ix_approval_proposals_connection_status ON approval_proposals(connection_id, status);
-- CREATE INDEX IF NOT EXISTS ix_approval_proposals_action_digest ON approval_proposals(action_digest);

-- Execution ledger indexes
-- CREATE INDEX IF NOT EXISTS ix_operation_executions_connection_digest ON operation_executions(connection_id, action_digest);

-- Audit events indexes
-- CREATE INDEX IF NOT EXISTS ix_audit_events_user_created ON audit_events(user_id, created_at);
-- CREATE INDEX IF NOT EXISTS ix_audit_events_connection_created ON audit_events(connection_id, created_at);