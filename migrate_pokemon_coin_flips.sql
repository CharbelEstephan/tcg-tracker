-- Adds per-game coin-flip counts for both players, so you can track heads/tails
-- luck over time (you vs opponents). Counts, not individual flips: how many
-- heads and tails each side got that game. Defaults to 0 so existing rows read
-- as "no flips recorded". Non-destructive. Run once against your Neon database.

ALTER TABLE matches_pokemon ADD COLUMN IF NOT EXISTS my_heads  INTEGER NOT NULL DEFAULT 0;
ALTER TABLE matches_pokemon ADD COLUMN IF NOT EXISTS my_tails  INTEGER NOT NULL DEFAULT 0;
ALTER TABLE matches_pokemon ADD COLUMN IF NOT EXISTS opp_heads INTEGER NOT NULL DEFAULT 0;
ALTER TABLE matches_pokemon ADD COLUMN IF NOT EXISTS opp_tails INTEGER NOT NULL DEFAULT 0;
