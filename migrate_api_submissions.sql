-- Adds the duplicate-submission guard for the phone app's JSON API.
--
-- The app saves logs offline and retries until the server answers, so the same
-- log can arrive twice (e.g. the server saved it but the reply was lost). Each
-- API write sends a client-generated client_id; the server records it here in
-- the same transaction as the insert and answers a repeat with the original
-- row's id instead of logging it again. record_id is the row that write
-- created (the first game, for a series); kind says which table it's in.
-- Non-destructive. Run this once against your Neon database.

CREATE TABLE IF NOT EXISTS api_submissions (
    client_id   TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    record_id   INT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
