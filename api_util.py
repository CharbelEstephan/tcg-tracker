"""api_util.py — small helpers shared by the JSON API routes in app.py and
pokemon.py (kept in their own module so neither has to import the other).

The phone app queues logs offline and retries until the server answers, so a
log can arrive more than once. Every API write carries a client-generated
``client_id``; claim_submission() records it in the same transaction as the
insert, so a retry of something already saved is recognised instead of logged
twice.
"""
import datetime as dt

from flask import jsonify


class ApiError(Exception):
    """A bad request body. Routes let it propagate; app.py turns it into a
    400 JSON response."""


def error(message, status=400, **extra):
    return jsonify(error=message, **extra), status


def text(data, key, required=False):
    """Stripped string or None. Required fields raise ApiError when blank."""
    value = data.get(key)
    value = "" if value is None else str(value).strip()
    if required and not value:
        raise ApiError(f"{key} is required")
    return value or None


def date_str(data, key):
    """A required ISO date (YYYY-MM-DD), returned as the same string."""
    value = text(data, key, required=True)
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        raise ApiError(f"{key} must be a YYYY-MM-DD date")
    return value


def count(data, key, minimum=0):
    """A non-negative int count; missing or null means 0."""
    value = data.get(key)
    if value is None or value == "":
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ApiError(f"{key} must be a whole number")
    try:
        value = int(value)
    except ValueError:
        raise ApiError(f"{key} must be a whole number")
    if value < minimum:
        raise ApiError(f"{key} must be at least {minimum}")
    return value


def boolean(data, key, required=False):
    """True/False, or None when missing and not required."""
    value = data.get(key)
    if value is None:
        if required:
            raise ApiError(f"{key} is required")
        return None
    if not isinstance(value, bool):
        raise ApiError(f"{key} must be true or false")
    return value


def claim_submission(cur, client_id, kind):
    """Record client_id for this write. Returns (True, None) when it's new (go
    ahead and insert), or (False, record_id) when it's a retry of something
    already saved, record_id being the row that earlier submission created."""
    cur.execute(
        "INSERT INTO api_submissions (client_id, kind) VALUES (%s, %s) "
        "ON CONFLICT (client_id) DO NOTHING RETURNING client_id",
        (client_id, kind),
    )
    if cur.fetchone() is not None:
        return True, None
    cur.execute("SELECT record_id FROM api_submissions WHERE client_id = %s",
                (client_id,))
    return False, cur.fetchone()[0]


def record_submission(cur, client_id, record_id):
    """Attach the inserted row's id to a claimed submission, so a retry can be
    answered with the same id."""
    cur.execute("UPDATE api_submissions SET record_id = %s WHERE client_id = %s",
                (record_id, client_id))
