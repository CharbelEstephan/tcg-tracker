"""tweets.py — compose and post the auto-tweet for a Riftbound pack pull.

Kept free of any database or Flask imports so the composing logic is pure and
easy to preview/test: app.py fetches the rows and hands them in.
"""
import os

import options

# Rarity rank = position in HIT_TYPES (later in the list = rarer). Drives both
# "headline the rarest hit" and the per-type drought counts.
_RANK = {col: i for i, (col, _) in enumerate(options.HIT_TYPES)}

# Original labels (as stored on opening_cards). _LABEL is the singular display
# form — only "Signature Spells" needs fixing; the rest are already singular
# and pluralize with a plain "s".
_ORIG_LABEL = dict(options.HIT_TYPES)
_LABEL = dict(options.HIT_TYPES)
_LABEL["hit_sig_spell"] = "Signature Spell"


def _plural_pull(n):
    return "pull" if n == 1 else "pulls"


def _hit_label(col, n):
    """Singular for one, +s for more (works for every hit label once
    Signature Spells is singularized)."""
    lbl = _LABEL[col]
    return lbl if (n or 0) == 1 else lbl + "s"

# A "hit" worth tweeting about excludes the guaranteed Rare/Leader.
NOTABLE_COLS = [col for col, _ in options.TOTAL_HIT_TYPES]

# The tiers that trigger the "BIG HIT!!" line.
BIG_HIT_COLS = {"hit_overnumber", "hit_signature", "hit_ultimate", "hit_nn_chase"}

TWEET_LIMIT = 280


def _pluralize(word):
    if word.endswith(("x", "s", "ch", "sh")):
        return word + "es"
    return word + "s"


def _packs_since(rows, cur_idx, cols):
    """Packs (summed quantity) opened since the most recent opening *before*
    cur_idx that had any of `cols` > 0, counting through the current opening.
    Returns (packs, first_ever)."""
    prior = None
    for i in range(cur_idx - 1, -1, -1):
        if any((rows[i][c] or 0) > 0 for c in cols):
            prior = i
            break
    start = 0 if prior is None else prior + 1
    packs = sum((rows[i]["quantity"] or 0) for i in range(start, cur_idx + 1))
    return packs, prior is None


def _cap(text):
    """Keep tweets under the limit: first drop the "Pulled:" summary line, then
    hard-truncate as a last resort."""
    if len(text) <= TWEET_LIMIT:
        return text
    kept = [ln for ln in text.split("\n") if not ln.startswith("Pulled:")]
    text = "\n".join(kept)
    if len(text) > TWEET_LIMIT:
        text = text[:TWEET_LIMIT - 1].rstrip() + "…"
    return text


def compose_pull_tweet(rows, card_rows):
    """Build the tweet text for the most recent opening.

    rows: openings oldest -> newest (the last is the just-logged pull), each a
    mapping with quantity, set_name, product, location and every hit_* column.
    card_rows: [(rarity_label, card_name), ...] captured for that pull.
    """
    cur = rows[-1]
    idx = len(rows) - 1
    qty = cur["quantity"] or 0
    product = cur["product"] or "pack"
    pack_word = product if qty == 1 else _pluralize(product)
    where = f" from {cur['location']}" if cur.get("location") else ""

    # Each element is its own paragraph, joined with a blank line so the tweet
    # reads in sections rather than one stacked column.
    parts = [f"{cur['set_name']} pulls! Ripped {qty} {pack_word}{where}."]

    notable = sorted((c for c in NOTABLE_COLS if (cur[c] or 0) > 0),
                     key=lambda c: _RANK[c], reverse=True)

    if not notable:
        packs, first = _packs_since(rows, idx, NOTABLE_COLS)
        parts.append(f"{packs} {_plural_pull(packs)} in and still no hit."
                     if first else
                     f"It's been {packs} {_plural_pull(packs)} since our last hit.")
    else:
        head = notable[0]
        label = _LABEL[head]
        packs, first = _packs_since(rows, idx, [head])
        when = "ever!" if first else f"in {packs} {_plural_pull(packs)}!"
        cards = ", ".join(name for (lbl, name) in card_rows
                          if lbl == _ORIG_LABEL[head])

        if head in BIG_HIT_COLS:
            who = f"We pulled {cards}" if cards else f"A {label}"
            story = f"BIG HIT!! {who} — our first {label} {when}"
        else:
            named = f" ({cards})" if cards else ""
            story = f"Our first {label}{named} {when}"

        # With more than one hit, lead with the full list; a lone hit is
        # already named in the story line, so skip the redundant summary.
        if len(notable) > 1:
            summary = ", ".join(f"{cur[c]} {_hit_label(c, cur[c])}" for c in notable)
            story = f"Pulled: {summary}.\n{story}"
        parts.append(story)

    # Running tally of packs that included a leader in this set (leaders come
    # ~one per pack, so hit_leader summed across the set is the leader-pack
    # count). Shown whenever this pull had a leader.
    if (cur.get("hit_leader") or 0) > 0:
        leader_packs = sum((r["hit_leader"] or 0) for r in rows
                           if r["set_name"] == cur["set_name"])
        noun = "pack" if leader_packs == 1 else "packs"
        parts.append(f"That's now {leader_packs} {noun} with a leader "
                     f"in {cur['set_name']}.")

    return _cap("\n\n".join(parts))


def tweet_enabled():
    return os.environ.get("TWEET_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on")


def post_tweet(text):
    """Post to X via OAuth 1.0a. Returns (ok, info). Never raises — the caller
    logs the result and carries on so a failed tweet can't break logging."""
    if not tweet_enabled():
        return False, "TWEET_ENABLED is off"
    keys = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        return False, "missing env vars: " + ", ".join(missing)
    try:
        import tweepy
        client = tweepy.Client(
            consumer_key=os.environ["X_API_KEY"],
            consumer_secret=os.environ["X_API_SECRET"],
            access_token=os.environ["X_ACCESS_TOKEN"],
            access_token_secret=os.environ["X_ACCESS_SECRET"],
        )
        resp = client.create_tweet(text=text)
        return True, getattr(resp, "data", None)
    except Exception as e:  # noqa: BLE001 - report any failure, never raise
        return False, str(e)
