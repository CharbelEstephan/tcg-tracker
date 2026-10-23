import os
import hmac
from collections import Counter, defaultdict
from datetime import date, timedelta

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from flask import (Flask, flash, jsonify, redirect, render_template, request,
                   session, url_for)
from itsdangerous import BadSignature, URLSafeTimedSerializer

import api_util
import options
import pokemon
import tweets
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
# "ping" is the keep-alive heartbeat, left open so it stays cheap and can't
# bounce to /login. "api_login" is how the phone app gets its token.
PUBLIC_ENDPOINTS = {"login", "static", "ping", "api_login"}

# The phone app's bearer tokens: signed with SECRET_KEY like the session
# cookie, so rotating SECRET_KEY signs every phone out too.
API_TOKEN_MAX_AGE = 180 * 24 * 3600  # seconds; the app logs in again after this
_api_tokens = URLSafeTimedSerializer(SECRET_KEY, salt="api-token")


def api_token_ok():
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return False
    try:
        _api_tokens.loads(header[len("Bearer "):], max_age=API_TOKEN_MAX_AGE)
    except BadSignature:  # also covers an expired token
        return False
    return True


@app.before_request
def require_login():
    """A single gate in front of the whole app so no route can be left
    unprotected by accident. The login page and static assets stay open;
    every other request needs a signed-in session or gets bounced to /login.
    /api/ routes are the phone app's: they need a bearer token instead, and
    answer 401 JSON rather than redirecting."""
    if request.endpoint in PUBLIC_ENDPOINTS:
        return
    if request.path.startswith("/api/"):
        if not api_token_ok():
            return api_util.error("unauthorized", 401)
        return
    if not session.get("authed"):
        return redirect(url_for("login"))


@app.errorhandler(api_util.ApiError)
def api_bad_request(e):
    return api_util.error(str(e))


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


@app.route("/ping")
def ping():
    """Keep-alive heartbeat. The log-game pages poll this every few minutes so
    Render's free tier doesn't spin the service down mid-game (a Riftbound
    match runs longer than the ~15-minute idle window). Deliberately trivial —
    no DB, no template — so it's near-instant and cheap to hit."""
    return "ok", 200


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def get_legend_domains():
    """Build a legend -> [domain_1, domain_2] map from what you've logged.

    Every game records a legend next to its domains, on both sides of the
    table, so the mapping is already sitting in your own data. New sets are
    seeded from options.LEGEND_DOMAINS so their legends auto-fill before
    they've been logged; once you've actually logged a legend, your own data
    wins. If a legend has been logged with two different pairings (a typo, most
    likely), the most frequent one wins.
    """
    # Start from the seed so a just-released set works immediately.
    mapping = {leg: list(doms) for leg, doms in options.LEGEND_DOMAINS.items()}

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

    # Logged data overrides the seed.
    for leader, c in pairs.items():
        mapping[leader] = list(c.most_common(1)[0][0])
    return mapping


def get_distinct(column):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT DISTINCT {column} FROM games "
                f"WHERE {column} IS NOT NULL AND {column} <> '' ORDER BY {column}"
            )
            return [r[0] for r in cur.fetchall()]


def _next_series_id(cur):
    cur.execute("SELECT COALESCE(MAX(series_id), 0) + 1 FROM games")
    return cur.fetchone()[0]


def get_next_series_id():
    with get_conn() as conn:
        with conn.cursor() as cur:
            return _next_series_id(cur)


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
    # Total hits sums the notable hit_* columns (Rare and Leader are guaranteed,
    # so they're excluded), built from options so the set lives in one place.
    total = " + ".join(col for col, _ in options.TOTAL_HIT_TYPES)
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


def get_known_legends():
    # Anything you've typed that isn't in options.py still shows up, so a
    # legend from a brand new set works before you get round to editing it.
    known = set(options.LEGENDS)
    for col in ("my_leader", "opp_leader"):
        known.update(get_distinct(col))
    return sorted(known)


def get_known_events():
    return sorted(set(options.EVENT_TYPES) | set(get_distinct("event_type")))


def get_pull_sets():
    return options.SETS + [s for s in get_distinct_openings("set_name")
                           if s not in options.SETS]


def get_box_sets():
    return options.SETS + [s for s in get_distinct_boxes("set_name")
                           if s not in options.SETS]


@app.route("/riftbound")
def riftbound():
    wins, losses = get_record()
    total = wins + losses

    return render_template(
        "index.html",
        legends=get_known_legends(),
        domains=options.DOMAINS,
        events=get_known_events(),
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
        sets=get_pull_sets(),
        pull_locations=get_distinct_openings("location"),
        pull_openers=get_distinct_openings("opened_by"),
        recent_openings=get_recent_openings(),
        box_hit_types=options.BOX_HIT_TYPES,
        named_box_hit_types=options.NAMED_BOX_HIT_TYPES,
        box_sets=get_box_sets(),
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


def _insert_games(cur, games):
    """Insert one or more game rows (each a dict keyed by GAME_COLS) on an
    open cursor, so the API can do it in the same transaction as its
    duplicate-submission check."""
    placeholders = ", ".join(["%s"] * len(GAME_COLS))
    ids = []
    for g in games:
        cur.execute(
            f"INSERT INTO games ({', '.join(GAME_COLS)}) "
            f"VALUES ({placeholders}) RETURNING id",
            [g[c] for c in GAME_COLS],
        )
        ids.append(cur.fetchone()[0])
    return ids


def insert_games(games):
    with get_conn() as conn:
        with conn.cursor() as cur:
            _insert_games(cur, games)


def _insert_with_cards(cur, table, row, card_table, fk, card_rows):
    """Insert one openings/boxes row (a column -> value dict) plus its named
    cards [(rarity_label, card_name), ...]. Returns the new row's id."""
    cols = list(row)
    placeholders = ", ".join(["%s"] * len(cols))
    cur.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        "RETURNING id",
        [row[c] for c in cols],
    )
    new_id = cur.fetchone()[0]
    if card_rows:
        cur.executemany(
            f"INSERT INTO {card_table} ({fk}, rarity, card_name) "
            "VALUES (%s, %s, %s)",
            [(new_id, rarity, name) for rarity, name in card_rows],
        )
    return new_id


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

    # Carry the date + "Me" context (event, location, legend, domains, deck)
    # back into the fresh form so a run of games logged back-to-back doesn't
    # need retyping. The date rides along, so this stays scoped to the same day:
    # opening Riftbound fresh from Home carries nothing and defaults to today.
    # Opponent fields are intentionally dropped — they change every game.
    carry_out = {
        "played_on":   base["played_on"],
        "event_type":  base["event_type"],
        "location":    base["location"] or "",
        "my_leader":   base["my_leader"],
        "my_domain_1": base["my_domain_1"] or "",
        "my_domain_2": base["my_domain_2"] or "",
        "my_deck":     base["my_deck"] or "",
    }
    return redirect(url_for("riftbound",
                            **{k: v for k, v in carry_out.items() if v}))


@app.route("/delete/<int:game_id>", methods=["POST"])
def delete(game_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM games WHERE id = %s", (game_id,))
    return redirect(url_for("riftbound"))


def _openings_ordered_for_tweet():
    cols = ["id", "quantity", "set_name", "product", "location"] + \
           [c for c, _ in options.HIT_TYPES]
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(f"SELECT {', '.join(cols)} FROM openings "
                        "ORDER BY acquired_on ASC, id ASC")
            return cur.fetchall()


def _cards_for_opening(opening_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT rarity, card_name FROM opening_cards "
                        "WHERE opening_id = %s", (opening_id,))
            return [(r[0], r[1]) for r in cur.fetchall()]


def maybe_tweet_pull(opening_id):
    """Compose and post the pull tweet. Never raises and never blocks logging.
    Returns (ok, info) so the caller can surface the result to the user."""
    try:
        rows = _openings_ordered_for_tweet()
        ids = [r["id"] for r in rows]
        if opening_id not in ids:
            return False, "opening not found"
        idx = ids.index(opening_id)
        text = tweets.compose_pull_tweet(rows[:idx + 1],
                                         _cards_for_opening(opening_id))
        ok, info = tweets.post_tweet(text)
        # Log a failure at WARNING so the reason shows in Render — gunicorn
        # drops app.logger INFO lines, which is why skips looked invisible.
        log = app.logger.info if ok else app.logger.warning
        log("Pull tweet for opening %s: %s (%s)", opening_id,
            "posted" if ok else "not sent", info)
        return ok, info
    except Exception as e:  # noqa: BLE001 - report any failure, never raise
        app.logger.exception("Pull tweet failed for opening %s", opening_id)
        return False, str(e)


@app.route("/pulls", methods=["POST"])
def add_pull():
    f = request.form

    row = {
        "acquired_on": f["acquired_on"],
        "product":     f["product"].strip(),
        "quantity":    int(f["quantity"]),
        "set_name":    f["set_name"].strip(),
        "is_pity":     f.get("is_pity") == "on",
        **{col: int(f.get(col) or 0) for col, _ in options.HIT_TYPES},
        "location":    blank_to_none(f.get("location")),
        "opened_by":   blank_to_none(f.get("opened_by")),
        "notes":       blank_to_none(f.get("notes")),
    }
    card_rows = collect_card_names(f, options.NAMED_HIT_TYPES)

    with get_conn() as conn:
        with conn.cursor() as cur:
            opening_id = _insert_with_cards(cur, "openings", row,
                                            "opening_cards", "opening_id",
                                            card_rows)

    ok, info = maybe_tweet_pull(opening_id)
    # Only surface a tweet banner when the feature is actually turned on, so a
    # tracker running with tweets off stays quiet.
    if tweets.tweet_enabled():
        if ok:
            flash("Tweet posted.", "tweet_ok")
        else:
            flash(f"Tweet not sent: {info}", "tweet_err")
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

    row = {
        "opened_on": f["opened_on"],
        "quantity":  int(f["quantity"]),
        "set_name":  f["set_name"].strip(),
        **{col: int(f.get(col) or 0) for col, _ in options.BOX_HIT_TYPES},
        "location":  blank_to_none(f.get("location")),
        "notes":     blank_to_none(f.get("notes")),
    }
    card_rows = collect_card_names(f, options.NAMED_BOX_HIT_TYPES)

    with get_conn() as conn:
        with conn.cursor() as cur:
            _insert_with_cards(cur, "boxes", row, "box_cards", "box_id",
                               card_rows)

    return redirect(url_for("riftbound", _anchor="tab-boxes"))


@app.route("/boxes/delete/<int:box_id>", methods=["POST"])
def delete_box(box_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM boxes WHERE id = %s", (box_id,))
    return redirect(url_for("riftbound", _anchor="tab-boxes"))


# --- JSON API for the phone app ---------------------------------------------
#
# Everything under /api/ is for the iPhone app: bearer-token auth (see
# require_login) and JSON in/out instead of forms and redirects. Writes reuse
# the website's insert helpers, so both land identical rows in the same tables.
# Each write carries a client_id so the app can safely retry it (see api_util).


def json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise api_util.ApiError("expected a JSON object body")
    return data


@app.route("/api/login", methods=["POST"])
def api_login():
    supplied = str(json_body().get("password") or "")
    # Constant-time compare, same as the web login.
    if not hmac.compare_digest(supplied.encode("utf-8"), APP_PASSWORD.encode("utf-8")):
        return api_util.error("incorrect password", 401)
    return jsonify(token=_api_tokens.dumps({"v": 1}),
                   expires_in=API_TOKEN_MAX_AGE)


def _hit_list(types):
    return [{"col": col, "label": label} for col, label in types]


@app.route("/api/lists")
def api_lists():
    """Every dropdown and auto-fill map the app needs, in one call it can
    cache, so its forms open instantly even before the server wakes up."""
    return jsonify(
        riftbound=dict(
            legends=get_known_legends(),
            legend_domains=get_legend_domains(),
            domains=options.DOMAINS,
            events=get_known_events(),
            locations=get_distinct("location"),
            decks=get_distinct("my_deck"),
            opponents=get_distinct("opponent"),
            products=options.PRODUCTS,
            sets=get_pull_sets(),
            hit_types=_hit_list(options.HIT_TYPES),
            named_hit_types=_hit_list(options.NAMED_HIT_TYPES),
            pull_locations=get_distinct_openings("location"),
            pull_openers=get_distinct_openings("opened_by"),
            box_sets=get_box_sets(),
            box_hit_types=_hit_list(options.BOX_HIT_TYPES),
            named_box_hit_types=_hit_list(options.NAMED_BOX_HIT_TYPES),
            box_locations=get_distinct_boxes("location"),
        ),
        pokemon=dict(
            energies=pokemon.ENERGY_TYPES,
            deck_energies=pokemon.get_deck_energies(),
            lists=pokemon.load_lists(),
        ),
    )


@app.route("/api/riftbound/games", methods=["POST"])
def api_add_games():
    """Log one game, or a best-of-3 series ("series": true) whose games share
    a series number. "games" is [{"won": bool, "went_first": bool|null}, ...]
    in play order."""
    data = json_body()
    client_id = api_util.text(data, "client_id", required=True)
    text = api_util.text
    base = dict(
        played_on=api_util.date_str(data, "played_on"),
        event_type=text(data, "event_type", required=True),
        location=text(data, "location"),
        my_leader=text(data, "my_leader", required=True),
        my_domain_1=text(data, "my_domain_1", required=True),
        my_domain_2=text(data, "my_domain_2"),
        my_deck=text(data, "my_deck"),
        opponent=text(data, "opponent"),
        opp_leader=text(data, "opp_leader", required=True),
        opp_domain_1=text(data, "opp_domain_1", required=True),
        opp_domain_2=text(data, "opp_domain_2"),
        notes=text(data, "notes"),
    )
    series = api_util.boolean(data, "series") or False
    results = data.get("games")
    if not isinstance(results, list) or not results:
        raise api_util.ApiError("games must be a non-empty list")
    if len(results) > (3 if series else 1):
        raise api_util.ApiError("a series has at most 3 games, a single log 1")
    games = []
    for n, g in enumerate(results, start=1):
        if not isinstance(g, dict):
            raise api_util.ApiError("each game must be an object")
        games.append(dict(
            base,
            series_id=None,
            game_in_series=n if series else None,
            went_first=api_util.boolean(g, "went_first"),
            won=api_util.boolean(g, "won", required=True),
        ))

    with get_conn() as conn:
        with conn.cursor() as cur:
            is_new, record_id = api_util.claim_submission(cur, client_id,
                                                          "riftbound_game")
            if not is_new:
                return jsonify(id=record_id, duplicate=True)
            if series:
                series_id = _next_series_id(cur)
                for g in games:
                    g["series_id"] = series_id
            ids = _insert_games(cur, games)
            api_util.record_submission(cur, client_id, ids[0])
    return jsonify(id=ids[0], ids=ids, duplicate=False), 201


def _api_hits(data, types):
    """"hits": {"hit_epic": 2, ...} -> a value for every column in types
    (missing ones are 0). Unknown columns are rejected, not silently dropped."""
    hits = data.get("hits") or {}
    if not isinstance(hits, dict):
        raise api_util.ApiError("hits must be an object")
    allowed = [col for col, _ in types]
    unknown = sorted(set(hits) - set(allowed))
    if unknown:
        raise api_util.ApiError("unknown hit types: " + ", ".join(unknown))
    return {col: api_util.count(hits, col) for col in allowed}


def _api_cards(data, named_types):
    """"cards": [{"type": "hit_alt_art", "name": "..."}] -> the
    [(rarity_label, card_name), ...] rows collect_card_names builds for forms."""
    labels = dict(named_types)
    cards = data.get("cards") or []
    if not isinstance(cards, list):
        raise api_util.ApiError("cards must be a list")
    rows = []
    for c in cards:
        if not isinstance(c, dict) or c.get("type") not in labels:
            raise api_util.ApiError(
                "each card needs a type from: " + ", ".join(labels))
        name = api_util.text(c, "name")
        if name:
            rows.append((labels[c["type"]], name))
    return rows


@app.route("/api/riftbound/pulls", methods=["POST"])
def api_add_pull():
    data = json_body()
    client_id = api_util.text(data, "client_id", required=True)
    row = {
        "acquired_on": api_util.date_str(data, "acquired_on"),
        "product":     api_util.text(data, "product", required=True),
        "quantity":    api_util.count(data, "quantity", minimum=1),
        "set_name":    api_util.text(data, "set_name", required=True),
        "is_pity":     api_util.boolean(data, "is_pity") or False,
        **_api_hits(data, options.HIT_TYPES),
        "location":    api_util.text(data, "location"),
        "opened_by":   api_util.text(data, "opened_by"),
        "notes":       api_util.text(data, "notes"),
    }
    card_rows = _api_cards(data, options.NAMED_HIT_TYPES)

    with get_conn() as conn:
        with conn.cursor() as cur:
            is_new, record_id = api_util.claim_submission(cur, client_id,
                                                          "riftbound_pull")
            if not is_new:
                # Already saved (and tweeted) the first time round.
                return jsonify(id=record_id, duplicate=True, tweet=None)
            opening_id = _insert_with_cards(cur, "openings", row,
                                            "opening_cards", "opening_id",
                                            card_rows)
            api_util.record_submission(cur, client_id, opening_id)

    # Tweet only after the insert has committed, same as the web form.
    ok, info = maybe_tweet_pull(opening_id)
    tweet = {"ok": ok, "info": str(info)} if tweets.tweet_enabled() else None
    return jsonify(id=opening_id, duplicate=False, tweet=tweet), 201


@app.route("/api/riftbound/boxes", methods=["POST"])
def api_add_box():
    data = json_body()
    client_id = api_util.text(data, "client_id", required=True)
    row = {
        "opened_on": api_util.date_str(data, "opened_on"),
        "quantity":  api_util.count(data, "quantity", minimum=1),
        "set_name":  api_util.text(data, "set_name", required=True),
        **_api_hits(data, options.BOX_HIT_TYPES),
        "location":  api_util.text(data, "location"),
        "notes":     api_util.text(data, "notes"),
    }
    card_rows = _api_cards(data, options.NAMED_BOX_HIT_TYPES)

    with get_conn() as conn:
        with conn.cursor() as cur:
            is_new, record_id = api_util.claim_submission(cur, client_id,
                                                          "riftbound_box")
            if not is_new:
                return jsonify(id=record_id, duplicate=True)
            box_id = _insert_with_cards(cur, "boxes", row, "box_cards",
                                        "box_id", card_rows)
            api_util.record_submission(cur, client_id, box_id)
    return jsonify(id=box_id, duplicate=False), 201


if __name__ == "__main__":
    app.run(debug=True, port=5001)
