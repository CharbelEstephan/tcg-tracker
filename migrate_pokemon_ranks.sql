-- Adds ranked-play tracking for the Pokemon log: a rank recorded on each
-- match (only meaningful when the event is "Ranked"), plus a reference list
-- of rank names that self-populates as you type, like the other lists.
-- Non-destructive. Run this once against your Neon database.

ALTER TABLE matches_pokemon ADD COLUMN IF NOT EXISTS rank TEXT;

CREATE TABLE IF NOT EXISTS pokemon_ranks (
    id   SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
