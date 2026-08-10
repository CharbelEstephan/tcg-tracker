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
                   redirect, url_for, flash)

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


@bp.route("/pokemon", methods=["GET"])
def form():
    return render_template(
        "pokemon.html",
        energies=ENERGY_TYPES,
        lists=load_lists(),
        today=dt.date.today().isoformat(),
        pending_new=None,
        carry={k: request.args.get(k, "") for k in STICKY},
        recent=get_recent(),
        rank_breakdown=get_rank_breakdown(),
    )


@bp.route("/pokemon", methods=["POST"])
def submit():
    f = request.form
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

    # New values found and not yet confirmed -> bounce with a confirm banner.
    # request.form still holds every field, so the form re-renders as typed.
    if pending_new and not confirm_new:
        return render_template(
            "pokemon.html", energies=ENERGY_TYPES, lists=lists,
            today=dt.date.today().isoformat(), pending_new=pending_new,
            carry={k: "" for k in STICKY}, recent=get_recent(),
            rank_breakdown=get_rank_breakdown(),
        ), 200

    def energy(name):
        return (f.get(name) or "").strip() or None

    row = (
        f.get("played_at") or None,
        resolved["my_deck_kind"], energy("my_energy_1"),
        energy("my_energy_2"), energy("my_energy_3"),
        resolved["opp_deck_kind"], energy("opp_energy_1"),
        energy("opp_energy_2"), energy("opp_energy_3"),
        resolved["event"], resolved["set_name"],
        f.get("result"),
        _int(f.get("my_points")), _int(f.get("opp_points")),
        f.get("went_first") == "first",
        resolved["mvp_card"],
        _int(f.get("total_turns")),
        f.get("surrendered") == "on",
        resolved["location"],
        resolved["rank"],
    )

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO matches_pokemon
              (played_at, my_deck_kind, my_energy_1, my_energy_2, my_energy_3,
               opp_deck_kind, opp_energy_1, opp_energy_2, opp_energy_3,
               event, set_name, result, my_points, opp_points,
               went_first, mvp_card, total_turns, surrendered, location, rank)
            VALUES (COALESCE(%s, CURRENT_DATE), %s,%s,%s,%s, %s,%s,%s,%s,
                    %s,%s, %s,%s,%s, %s,%s,%s,%s,%s, %s)
            """,
            row,
        )
    flash("Match logged.", "ok")
    # Carry the sticky context/deck fields back into the fresh form so a run of
    # matches with the same deck doesn't need retyping.
    carry_out = {
        "played_at":    f.get("played_at") or "",
        "event":        resolved["event"] or "",
        "set_name":     resolved["set_name"] or "",
        "location":     resolved["location"] or "",
        "rank":         resolved["rank"] or "",
        "my_deck_kind": resolved["my_deck_kind"] or "",
        "my_energy_1":  f.get("my_energy_1") or "",
        "my_energy_2":  f.get("my_energy_2") or "",
        "my_energy_3":  f.get("my_energy_3") or "",
    }
    return redirect(url_for("pokemon.form",
                            **{k: v for k, v in carry_out.items() if v}))


@bp.route("/pokemon/delete/<int:match_id>", methods=["POST"])
def delete(match_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM matches_pokemon WHERE id = %s", (match_id,))
    return redirect(url_for("pokemon.form"))
