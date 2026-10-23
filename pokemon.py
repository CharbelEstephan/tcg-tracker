"""pokemon.py — Pokemon TCG Pocket logging for the TCG Tracker.

Drop into the existing tracker app and register it:

    from pokemon import bp as pokemon_bp
    app.register_blueprint(pokemon_bp)

The one thing to adapt to your app is get_conn() below.
"""
import os
import datetime as dt

import psycopg2
import psycopg2.extras
from flask import (Blueprint, render_template, request,
                   redirect, url_for, flash, jsonify)

import api_util

bp = Blueprint("pokemon", __name__)

# Fields that stay the same across a run of matches (same deck, same event,
# same night) so they're carried back into the form after each submit.
STICKY = ["played_at", "event", "set_name", "location", "rank",
          "my_deck_kind", "my_energy_1", "my_energy_2", "my_energy_3"]

# Fixed, closed set — rendered as dropdowns, not a managed list.
ENERGY_TYPES = ["Grass", "Fire", "Water", "Lightning",
                "Psychic", "Fighting", "Darkness", "Metal"]

# The event value that unlocks the rank field. Compared case-insensitively.
RANKED_EVENT = "ranked"

# Form field -> reference table. Both deck fields share one list. The event
# field must be resolved before rank (dict order), since rank is only kept
# when the resolved event is "Ranked".
FIELD_TABLE = {
    "my_deck_kind":  "pokemon_deck_kinds",
    "opp_deck_kind": "pokemon_deck_kinds",
    "mvp_card":      "pokemon_mvp_cards",
    "event":         "pokemon_events",
    "set_name":      "pokemon_sets",
    "location":      "pokemon_locations",
    "rank":          "pokemon_ranks",
}

# Which reference list feeds each field's autocomplete in the template.
LIST_TABLES = {
    "deck_kind": "pokemon_deck_kinds",
    "mvp_card":  "pokemon_mvp_cards",
    "event":     "pokemon_events",
    "set":       "pokemon_sets",
    "location":  "pokemon_locations",
    "rank":      "pokemon_ranks",
}

FIELD_LABELS = {
    "my_deck_kind":  "Your deck",
    "opp_deck_kind": "Opponent deck",
    "mvp_card":      "MVP card",
    "event":         "Event",
    "set_name":      "Set",
    "location":      "Location",
    "rank":          "Rank",
}


def get_conn():
    """Same Neon database as the main app, read from the environment so the
    connection string lives only in .env (never in source or git history)."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is not set. Add it to your .env file "
            "(the main app loads it via python-dotenv on startup)."
        )
    return psycopg2.connect(database_url)


def load_lists():
    out = {}
    with get_conn() as conn, conn.cursor() as cur:
        for key, table in LIST_TABLES.items():
            cur.execute(f"SELECT name FROM {table} ORDER BY name")
            out[key] = [r[0] for r in cur.fetchall()]
    return out


def _canon(value, known):
    """Existing canonical name (case-insensitive) -> str;
    empty -> ''; genuinely new -> None."""
    v = (value or "").strip()
    if not v:
        return ""
    for name in known:
        if name.lower() == v.lower():
            return name
    return None


def _add_list_item(table, name):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {table} (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
            (name,),
        )


def _int(v):
    v = (v or "").strip()
    return int(v) if v.lstrip("-").isdigit() else None


def get_recent(limit=15):
    with get_conn() as conn, \
            conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(
            "SELECT * FROM matches_pokemon "
            "ORDER BY played_at DESC, id DESC LIMIT %s",
            (limit,),
        )
        return cur.fetchall()


def get_rank_breakdown():
    """Games logged at each rank, grouped by set -> [(rank, games), ...],
    so you can look back on how long a climb took each set."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(NULLIF(set_name, ''), '(no set)') AS s,
                   rank, COUNT(*) AS games
            FROM matches_pokemon
            WHERE rank IS NOT NULL AND rank <> ''
            GROUP BY s, rank
            ORDER BY s, COUNT(*) DESC, rank
            """
        )
        out = {}
        for set_name, rank, games in cur.fetchall():
            out.setdefault(set_name, []).append((rank, games))
        return out


def get_deck_energies():
    """Deck kind -> [energy_1, energy_2, energy_3], learned from what you've
    logged on both sides of the table. Picking a deck you've recorded before
    can then pre-fill its energies. If a deck has been logged with different
    energy sets (a typo, usually), the most frequent one wins."""
    from collections import defaultdict, Counter
    combos = defaultdict(Counter)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT my_deck_kind,  my_energy_1,  my_energy_2,  my_energy_3
            FROM matches_pokemon
            UNION ALL
            SELECT opp_deck_kind, opp_energy_1, opp_energy_2, opp_energy_3
            FROM matches_pokemon
            """
        )
        for deck, e1, e2, e3 in cur.fetchall():
            if deck and e1:
                combos[deck][(e1, e2, e3)] += 1
    return {
        deck: [e or "" for e in c.most_common(1)[0][0]]
        for deck, c in combos.items()
    }


def get_stats():
    """Top-line record for the stats strip: games, W/L/D, and win rate."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*),
                   COUNT(*) FILTER (WHERE result = 'win'),
                   COUNT(*) FILTER (WHERE result = 'loss'),
                   COUNT(*) FILTER (WHERE result = 'draw')
            FROM matches_pokemon
            """
        )
        games, wins, losses, draws = cur.fetchone()
    decisive = wins + losses
    return {
        "games": games, "wins": wins, "losses": losses, "draws": draws,
        "winrate": round(100 * wins / decisive, 1) if decisive else None,
    }


def get_coin_summary():
    """Running heads/tails totals for you vs opponents across every game, so
    you can see how the coin has treated each side over time."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(SUM(my_heads), 0),  COALESCE(SUM(my_tails), 0),
                   COALESCE(SUM(opp_heads), 0), COALESCE(SUM(opp_tails), 0)
            FROM matches_pokemon
            """
        )
        mh, mt, oh, ot = cur.fetchone()

    def side(heads, tails):
        total = heads + tails
        return {
            "heads": heads, "tails": tails, "total": total,
            "heads_pct": round(100 * heads / total, 1) if total else None,
        }

    return {"me": side(mh, mt), "opp": side(oh, ot)}


# Every match column written by the form, in the order _resolve_and_build
# produces them. Insert and update both build from this one list.
MATCH_COLS = [
    "played_at", "my_deck_kind", "my_energy_1", "my_energy_2", "my_energy_3",
    "opp_deck_kind", "opp_energy_1", "opp_energy_2", "opp_energy_3",
    "event", "set_name", "result", "my_points", "opp_points",
    "went_first", "mvp_card", "total_turns", "surrendered", "location", "rank",
    "my_heads", "my_tails", "opp_heads", "opp_tails",
]


def _resolve_and_build(f):
    """Validate a submitted match form. Returns (row, pending_new).

    When new reference values (a deck/event/etc. not in its list) still need
    confirming, row is None and pending_new lists them for the confirm banner.
    Otherwise pending_new is empty and row maps every MATCH_COLS column, ready
    for either an insert or an update."""
    lists = load_lists()
    list_by_field = {field: lists[
        next(k for k, t in LIST_TABLES.items() if t == table)
    ] for field, table in FIELD_TABLE.items()}

    confirm_new = f.get("confirm_new") == "1"
    resolved, pending_new = {}, []

    for field, table in FIELD_TABLE.items():
        # Rank only applies to Ranked events. A hidden rank input can still
        # post a stale value, so ignore it (and skip its new-value prompt)
        # unless the resolved event is "Ranked".
        if field == "rank" and (resolved.get("event") or "").lower() != RANKED_EVENT:
            resolved["rank"] = None
            continue
        raw = (f.get(field) or "").strip()
        canon = _canon(raw, list_by_field[field])
        if canon == "" or canon:
            resolved[field] = canon or None
        else:  # None -> new value
            if confirm_new:
                _add_list_item(table, raw)
                resolved[field] = raw
            else:
                pending_new.append((FIELD_LABELS[field], raw))
                resolved[field] = raw

    if pending_new and not confirm_new:
        return None, pending_new

    def energy(name):
        return (f.get(name) or "").strip() or None

    def count(name):
        # Coin-flip counts: blank or junk -> 0, never negative.
        return max(_int(f.get(name)) or 0, 0)

    row = {
        "played_at":    f.get("played_at") or dt.date.today().isoformat(),
        "my_deck_kind": resolved["my_deck_kind"],
        "my_energy_1":  energy("my_energy_1"),
        "my_energy_2":  energy("my_energy_2"),
        "my_energy_3":  energy("my_energy_3"),
        "opp_deck_kind": resolved["opp_deck_kind"],
        "opp_energy_1": energy("opp_energy_1"),
        "opp_energy_2": energy("opp_energy_2"),
        "opp_energy_3": energy("opp_energy_3"),
        "event":        resolved["event"],
        "set_name":     resolved["set_name"],
        "result":       f.get("result"),
        "my_points":    _int(f.get("my_points")),
        "opp_points":   _int(f.get("opp_points")),
        "went_first":   f.get("went_first") == "first",
        "mvp_card":     resolved["mvp_card"],
        "total_turns":  _int(f.get("total_turns")),
        "surrendered":  f.get("surrendered") == "on",
        "location":     resolved["location"],
        "rank":         resolved["rank"],
        "my_heads":     count("my_heads"),
        "my_tails":     count("my_tails"),
        "opp_heads":    count("opp_heads"),
        "opp_tails":    count("opp_tails"),
    }
    return row, []


def _form_initial(rowdata):
    """A DB match row -> string-valued dict the form template can prefill from
    (dates as ISO, ints as text, the two booleans as their form tokens)."""
    d = dict(rowdata)
    out = {}
    for k, v in d.items():
        if v is None:
            out[k] = ""
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = str(v)
    out["went_first"] = "first" if d.get("went_first") else "second"
    out["surrendered"] = "on" if d.get("surrendered") else ""
    return out


def _render_form(**over):
    """Render the logging page with every field the template expects. Callers
    override only what differs (pending_new, carry, edit_id, initial)."""
    ctx = dict(
        energies=ENERGY_TYPES,
        lists=load_lists(),
        today=dt.date.today().isoformat(),
        pending_new=None,
        carry={k: "" for k in STICKY},
        recent=get_recent(),
        rank_breakdown=get_rank_breakdown(),
        deck_energies=get_deck_energies(),
        coin=get_coin_summary(),
        stats=get_stats(),
        edit_id=None,
        initial={},
    )
    ctx.update(over)
    return render_template("pokemon.html", **ctx)


@bp.route("/pokemon", methods=["GET"])
def form():
    return _render_form(carry={k: request.args.get(k, "") for k in STICKY})


def _insert_match(cur, row):
    """Insert one match row (keyed by MATCH_COLS); returns its id."""
    placeholders = ", ".join(["%s"] * len(MATCH_COLS))
    cur.execute(
        f"INSERT INTO matches_pokemon ({', '.join(MATCH_COLS)}) "
        f"VALUES ({placeholders}) RETURNING id",
        [row[c] for c in MATCH_COLS],
    )
    return cur.fetchone()[0]


@bp.route("/pokemon", methods=["POST"])
def submit():
    f = request.form
    row, pending_new = _resolve_and_build(f)
    if pending_new:
        return _render_form(pending_new=pending_new), 200

    with get_conn() as conn, conn.cursor() as cur:
        _insert_match(cur, row)
    flash("Match logged.", "ok")
    # Carry the sticky context/deck fields back into the fresh form so a run of
    # matches with the same deck doesn't need retyping.
    carry_out = {
        "played_at":    f.get("played_at") or "",
        "event":        row["event"] or "",
        "set_name":     row["set_name"] or "",
        "location":     row["location"] or "",
        "rank":         row["rank"] or "",
        "my_deck_kind": row["my_deck_kind"] or "",
        "my_energy_1":  f.get("my_energy_1") or "",
        "my_energy_2":  f.get("my_energy_2") or "",
        "my_energy_3":  f.get("my_energy_3") or "",
    }
    return redirect(url_for("pokemon.form",
                            **{k: v for k, v in carry_out.items() if v}))


@bp.route("/pokemon/edit/<int:match_id>", methods=["GET", "POST"])
def edit(match_id):
    if request.method == "POST":
        row, pending_new = _resolve_and_build(request.form)
        if pending_new:
            return _render_form(edit_id=match_id, pending_new=pending_new), 200
        set_clause = ", ".join(f"{c} = %s" for c in MATCH_COLS)
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE matches_pokemon SET {set_clause} WHERE id = %s",
                [row[c] for c in MATCH_COLS] + [match_id],
            )
        flash("Match updated.", "ok")
        return redirect(url_for("pokemon.form"))

    with get_conn() as conn, \
            conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT * FROM matches_pokemon WHERE id = %s", (match_id,))
        rowdata = cur.fetchone()
    if rowdata is None:
        return redirect(url_for("pokemon.form"))
    return _render_form(edit_id=match_id, initial=_form_initial(rowdata))


# Phone-app fields passed through to _resolve_and_build as text, exactly as the
# web form would post them.
_API_TEXT_FIELDS = [
    "my_deck_kind", "my_energy_1", "my_energy_2", "my_energy_3",
    "opp_deck_kind", "opp_energy_1", "opp_energy_2", "opp_energy_3",
    "event", "set_name", "mvp_card", "location", "rank",
]
_API_INT_FIELDS = ["my_points", "opp_points", "total_turns",
                   "my_heads", "my_tails", "opp_heads", "opp_tails"]


@bp.route("/api/pokemon/matches", methods=["POST"])
def api_add_match():
    """JSON twin of submit() for the phone app. Translates the body into the
    form shape _resolve_and_build expects, so validation and new-list-value
    handling stay identical. A deck/event/etc. not in its list yet gets a 409
    listing them; resend with "confirm_new": true to add them and log."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise api_util.ApiError("expected a JSON object body")
    client_id = api_util.text(data, "client_id", required=True)
    result = api_util.text(data, "result", required=True)
    if result not in ("win", "loss", "draw"):
        raise api_util.ApiError("result must be win, loss or draw")

    # The web form marks these required in the browser; the table is NOT NULL.
    for k in ("my_deck_kind", "my_energy_1", "opp_deck_kind", "opp_energy_1"):
        api_util.text(data, k, required=True)
    form = {k: api_util.text(data, k) or "" for k in _API_TEXT_FIELDS}
    for k in ("my_energy_1", "my_energy_2", "my_energy_3",
              "opp_energy_1", "opp_energy_2", "opp_energy_3"):
        if form[k] and form[k] not in ENERGY_TYPES:
            raise api_util.ApiError(
                f"{k} must be one of: " + ", ".join(ENERGY_TYPES))
    for k in _API_INT_FIELDS:
        present = data.get(k) not in (None, "")
        form[k] = str(api_util.count(data, k)) if present else ""
    form.update(
        played_at=api_util.date_str(data, "played_at"),
        result=result,
        went_first="first" if api_util.boolean(data, "went_first") else "second",
        surrendered="on" if api_util.boolean(data, "surrendered") else "",
        confirm_new="1" if api_util.boolean(data, "confirm_new") else "",
    )

    row, pending_new = _resolve_and_build(form)
    if pending_new:
        return api_util.error(
            "new_values", 409,
            pending=[{"field": label, "value": value}
                     for label, value in pending_new],
        )

    with get_conn() as conn, conn.cursor() as cur:
        is_new, record_id = api_util.claim_submission(cur, client_id,
                                                      "pokemon_match")
        if not is_new:
            return jsonify(id=record_id, duplicate=True)
        match_id = _insert_match(cur, row)
        api_util.record_submission(cur, client_id, match_id)
    return jsonify(id=match_id, duplicate=False), 201


@bp.route("/pokemon/delete/<int:match_id>", methods=["POST"])
def delete(match_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM matches_pokemon WHERE id = %s", (match_id,))
    return redirect(url_for("pokemon.form"))
