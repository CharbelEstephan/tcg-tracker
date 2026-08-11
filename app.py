import os
import hmac
from collections import Counter, defaultdict
from datetime import date, timedelta

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, session, url_for

import options
from pokemon import bp as pokemon_bp

load_dotenv()

app = Flask(__name__)
app.register_blueprint(pokemon_bp)

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise SystemExit(
        "DATABASE_URL is not set. Create a .env file containing one line:\n"
        "DATABASE_URL=<your Neon connection string>"
    )

APP_PASSWORD = os.environ.get("APP_PASSWORD")
if not APP_PASSWORD:
    raise SystemExit(
        "APP_PASSWORD is not set. Add a line to your .env file:\n"
        "APP_PASSWORD=<the shared password you'll log in with>"
    )

SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    raise SystemExit(
        "SECRET_KEY is not set. Add a line to your .env file:\n"
        "SECRET_KEY=<a long random string used to sign session cookies>"
    )
app.secret_key = SECRET_KEY
# "Remember me" logins ride a persistent cookie that lasts this long. A normal
# login stays a session cookie that dies when the browser closes. Either way
# only a signed "authed" flag travels in the cookie — never the password.
app.permanent_session_lifetime = timedelta(days=30)


# Every game the tracker knows about, in one place. Add a game here (one entry)
# and it appears on the home picker automatically. "endpoint" is whatever
# url_for() resolves to — a plain view name, or "blueprint.view" for a
# blueprint route like Pokemon's.
GAMES = [
    {
        "name": "Riftbound",
        "endpoint": "riftbound",
        "tagline": "Log matches, pulls, and box hits.",
    },
    {
        "name": "Pokemon TCG Pocket",
        "endpoint": "pokemon.form",
        "tagline": "Log ranked and casual matches.",
    },
]

# The only endpoints reachable while logged out. Everything else — the home
# picker, every game logger, and any game added later — is gated below.
PUBLIC_ENDPOINTS = {"login", "static"}


@app.before_request
def require_login():
    """A single gate in front of the whole app so no route can be left
    unprotected by accident. The login page and static assets stay open;
    every other request needs a signed-in session or gets bounced to /login."""
    if request.endpoint in PUBLIC_ENDPOINTS:
        return
    if not session.get("authed"):
        return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        supplied = request.form.get("password", "")
        # Constant-time compare so a wrong guess can't be timed character by character.
        if hmac.compare_digest(supplied.encode("utf-8"), APP_PASSWORD.encode("utf-8")):
            session["authed"] = True
            # "Remember me" makes the session cookie persistent (~30 days);
            # otherwise it lasts only until the browser closes.
            session.permanent = bool(request.form.get("remember"))
            return redirect(url_for("home"))
        error = "Incorrect password."
    return render_template("login.html", error=error)


@app.route("/")
def home():
    """Game picker — the front door. Drives its card list straight off GAMES."""
    return render_template("home.html", games=GAMES)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def get_legend_domains():
    """Build a legend -> [domain_1, domain_2] map from what you've logged.

    Every game records a legend next to its domains, on both sides of the
    table, so the mapping is already sitting in your own data. Nothing
    hardcoded means nothing to go stale when a set drops. If a legend has been
    logged with two different pairings (a typo, most likely), the most
    frequent one wins.
    """
    pairs = defaultdict(Counter)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT my_leader,  my_domain_1,  my_domain_2  FROM games
                UNION ALL
                SELECT opp_leader, opp_domain_1, opp_domain_2 FROM games
                """
            )
            for leader, d1, d2 in cur.fetchall():
                if leader and d1:
                    pairs[leader][(d1, d2)] += 1
    return {
        leader: list(c.most_common(1)[0][0])
        for leader, c in pairs.items()
    }


def get_distinct(column):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT DISTINCT {column} FROM games "
                f"WHERE {column} IS NOT NULL AND {column} <> '' ORDER BY {column}"
            )
            return [r[0] for r in cur.fetchall()]


def get_next_series_id():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(MAX(series_id), 0) + 1 FROM games")
            return cur.fetchone()[0]


def get_recent(limit=15):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "SELECT * FROM games ORDER BY played_on DESC, id DESC LIMIT %s",
                (limit,),
            )
            return cur.fetchall()


def get_distinct_openings(column):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT DISTINCT {column} FROM openings "
                f"WHERE {column} IS NOT NULL AND {column} <> '' ORDER BY {column}"
            )
            return [r[0] for r in cur.fetchall()]


def get_recent_openings(limit=15):
    # Total hits is the sum of the ten hit_* columns, built from options so a
    # new hit type only ever needs adding in one place.
    total = " + ".join(col for col, _ in options.HIT_TYPES)
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                f"SELECT *, ({total}) AS total_hits FROM openings "
                "ORDER BY acquired_on DESC, id DESC LIMIT %s",
                (limit,),
            )
            return cur.fetchall()


def get_distinct_boxes(column):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT DISTINCT {column} FROM boxes "
                f"WHERE {column} IS NOT NULL AND {column} <> '' ORDER BY {column}"
            )
            return [r[0] for r in cur.fetchall()]


def get_recent_boxes(limit=15):
    # Same idea as get_recent_openings, but boxes track a subset of hit types,
    # so the total only sums the columns the box tab actually uses.
    total = " + ".join(col for col, _ in options.BOX_HIT_TYPES)
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                f"SELECT *, ({total}) AS total_hits FROM boxes "
                "ORDER BY opened_on DESC, id DESC LIMIT %s",
                (limit,),
            )
            return cur.fetchall()


def get_record():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FILTER (WHERE won), "
                "       COUNT(*) FILTER (WHERE NOT won) FROM games"
            )
            wins, losses = cur.fetchone()
    return wins or 0, losses or 0


def blank_to_none(value):
    value = (value or "").strip()
    return value or None


def collect_card_names(form, named_types):
    """Gather the per-card name inputs for the given named hit types.

    Each named hit type renders one text input per card pulled, all sharing the
    name ``card_<column>``. Returns a list of (rarity_label, card_name) for
    every non-blank name entered, ready to insert one row per card."""
    rows = []
    for col, label in named_types:
        for raw in form.getlist(f"card_{col}"):
            name = (raw or "").strip()
            if name:
                rows.append((label, name))
    return rows


@app.route("/riftbound")
def riftbound():
    wins, losses = get_record()
    total = wins + losses

    # Anything you've typed that isn't in options.py still shows up, so a
    # legend from a brand new set works before you get round to editing it.
    known_legends = set(options.LEGENDS)
    for col in ("my_leader", "opp_leader"):
        known_legends.update(get_distinct(col))

    known_events = sorted(set(options.EVENT_TYPES) | set(get_distinct("event_type")))

    return render_template(
        "index.html",
        legends=sorted(known_legends),
        domains=options.DOMAINS,
        events=known_events,
        locations=get_distinct("location"),
        decks=get_distinct("my_deck"),
        opponents=get_distinct("opponent"),
        legend_domains=get_legend_domains(),
        recent=get_recent(),
        next_series=get_next_series_id(),
        today=date.today().isoformat(),
        wins=wins,
        losses=losses,
        winrate=round(100 * wins / total, 1) if total else None,
        products=options.PRODUCTS,
        hit_types=options.HIT_TYPES,
        named_hit_types=options.NAMED_HIT_TYPES,
        sets=options.SETS + [s for s in get_distinct_openings("set_name")
                             if s not in options.SETS],
        pull_locations=get_distinct_openings("location"),
        pull_openers=get_distinct_openings("opened_by"),
        recent_openings=get_recent_openings(),
        box_hit_types=options.BOX_HIT_TYPES,
        named_box_hit_types=options.NAMED_BOX_HIT_TYPES,
        box_sets=options.SETS + [s for s in get_distinct_boxes("set_name")
                                 if s not in options.SETS],
        box_locations=get_distinct_boxes("location"),
        recent_boxes=get_recent_boxes(),
        carry={k: request.args.get(k, "") for k in
               ["played_on", "series_id", "event_type", "location",
                "my_leader", "my_domain_1", "my_domain_2", "my_deck",
                "opponent", "opp_leader", "opp_domain_1", "opp_domain_2"]},
    )


def clean_domains(d1, d2):
    """Store domains in the order they're printed on the card - domain_1 is
    whatever comes first. The my_colors / opp_colors generated columns
    normalise the pair alphabetically for grouping, so matchup stats stay
    consistent even if two legends print the same pair in opposite orders."""
    d1 = (d1 or "").strip()
    d2 = (d2 or "").strip()
    return (d1, d2 or None)



GAME_COLS = [
    "played_on", "series_id", "game_in_series", "event_type", "location",
    "my_leader", "my_domain_1", "my_domain_2", "my_deck", "opponent",
    "opp_leader", "opp_domain_1", "opp_domain_2", "went_first", "won", "notes",
]


def insert_games(games):
    """Insert one or more game rows (each a dict keyed by GAME_COLS)."""
    placeholders = ", ".join(["%s"] * len(GAME_COLS))
    rows = [tuple(g[c] for c in GAME_COLS) for g in games]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO games ({', '.join(GAME_COLS)}) "
                f"VALUES ({placeholders})",
                rows,
            )


def parse_bool(value):
    """Form tri-state: 'true'/'false' -> bool, anything else -> None."""
    if value not in ("true", "false"):
        return None
    return value == "true"


@app.route("/add", methods=["POST"])
def add():
    f = request.form

    my_d1, my_d2 = clean_domains(f.get("my_domain_1"), f.get("my_domain_2"))
    opp_d1, opp_d2 = clean_domains(f.get("opp_domain_1"), f.get("opp_domain_2"))

    # Fields every game shares, whether it's a single game or a whole series.
    base = dict(
        played_on=f["played_on"],
        event_type=f["event_type"].strip(),
        location=blank_to_none(f.get("location")),
        my_leader=f["my_leader"].strip(),
        my_domain_1=my_d1,
        my_domain_2=my_d2,
        my_deck=blank_to_none(f.get("my_deck")),
        opponent=blank_to_none(f.get("opponent")),
        opp_leader=f["opp_leader"].strip(),
        opp_domain_1=opp_d1,
        opp_domain_2=opp_d2,
        notes=blank_to_none(f.get("notes")),
    )

    if f.get("is_series"):
        # One shared series number for every game the user filled in. Games are
        # numbered 1..3; a game with no win/loss selected just didn't happen
        # (e.g. a 2-0 sweep leaves game 3 blank).
        series_id = get_next_series_id()
        games = []
        for n in (1, 2, 3):
            won = parse_bool(f.get(f"s_result_{n}"))
            if won is None:
                continue
            games.append(dict(
                base,
                series_id=series_id,
                game_in_series=n,
                went_first=parse_bool(f.get(f"s_first_{n}")),
                won=won,
            ))
        if games:
            insert_games(games)
    else:
        insert_games([dict(
            base,
            series_id=None,
            game_in_series=None,
            went_first=parse_bool(f.get("went_first")),
            won=f["won"] == "true",
        )])

    return redirect(url_for("riftbound"))


@app.route("/delete/<int:game_id>", methods=["POST"])
def delete(game_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM games WHERE id = %s", (game_id,))
    return redirect(url_for("riftbound"))


@app.route("/pulls", methods=["POST"])
def add_pull():
    f = request.form

    hit_cols = [col for col, _ in options.HIT_TYPES]
    hit_vals = [int(f.get(col) or 0) for col in hit_cols]

    cols = ["acquired_on", "product", "quantity", "set_name", "is_pity",
            *hit_cols, "location", "opened_by", "notes"]
    vals = [
        f["acquired_on"],
        f["product"].strip(),
        int(f["quantity"]),
        f["set_name"].strip(),
        f.get("is_pity") == "on",
        *hit_vals,
        blank_to_none(f.get("location")),
        blank_to_none(f.get("opened_by")),
        blank_to_none(f.get("notes")),
    ]
    placeholders = ", ".join(["%s"] * len(cols))

    card_rows = collect_card_names(f, options.NAMED_HIT_TYPES)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO openings ({', '.join(cols)}) VALUES ({placeholders}) "
                "RETURNING id",
                vals,
            )
            opening_id = cur.fetchone()[0]
            if card_rows:
                cur.executemany(
                    "INSERT INTO opening_cards (opening_id, rarity, card_name) "
                    "VALUES (%s, %s, %s)",
                    [(opening_id, rarity, name) for rarity, name in card_rows],
                )

    return redirect(url_for("riftbound", _anchor="tab-pulls"))


@app.route("/pulls/delete/<int:opening_id>", methods=["POST"])
def delete_pull(opening_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM openings WHERE id = %s", (opening_id,))
    return redirect(url_for("riftbound", _anchor="tab-pulls"))


@app.route("/boxes", methods=["POST"])
def add_box():
    f = request.form

    hit_cols = [col for col, _ in options.BOX_HIT_TYPES]
    hit_vals = [int(f.get(col) or 0) for col in hit_cols]

    cols = ["opened_on", "quantity", "set_name", *hit_cols, "location", "notes"]
    vals = [
        f["opened_on"],
        int(f["quantity"]),
        f["set_name"].strip(),
        *hit_vals,
        blank_to_none(f.get("location")),
        blank_to_none(f.get("notes")),
    ]
    placeholders = ", ".join(["%s"] * len(cols))

    card_rows = collect_card_names(f, options.NAMED_BOX_HIT_TYPES)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO boxes ({', '.join(cols)}) VALUES ({placeholders}) "
                "RETURNING id",
                vals,
            )
            box_id = cur.fetchone()[0]
            if card_rows:
                cur.executemany(
                    "INSERT INTO box_cards (box_id, rarity, card_name) "
                    "VALUES (%s, %s, %s)",
                    [(box_id, rarity, name) for rarity, name in card_rows],
                )

    return redirect(url_for("riftbound", _anchor="tab-boxes"))


@app.route("/boxes/delete/<int:box_id>", methods=["POST"])
def delete_box(box_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM boxes WHERE id = %s", (box_id,))
    return redirect(url_for("riftbound", _anchor="tab-boxes"))


if __name__ == "__main__":
    app.run(debug=True, port=5001)
