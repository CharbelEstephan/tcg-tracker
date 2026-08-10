-- Seeds the Pokemon TCG Pocket ranked ladder into pokemon_ranks so the rank
-- autocomplete is populated from the first game. Idempotent (ON CONFLICT).
-- Master Ball is the final tier (leaderboard cutoffs) — log a more specific
-- label like "Master Ball (Top 5,000)" by just typing it; it self-adds.

INSERT INTO pokemon_ranks (name) VALUES
    ('Beginner 1'), ('Beginner 2'), ('Beginner 3'), ('Beginner 4'),
    ('Poké Ball 1'), ('Poké Ball 2'), ('Poké Ball 3'), ('Poké Ball 4'),
    ('Great Ball 1'), ('Great Ball 2'), ('Great Ball 3'), ('Great Ball 4'),
    ('Ultra Ball 1'), ('Ultra Ball 2'), ('Ultra Ball 3'), ('Ultra Ball 4'),
    ('Master Ball')
ON CONFLICT (name) DO NOTHING;
