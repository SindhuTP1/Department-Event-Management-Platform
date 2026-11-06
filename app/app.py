"""Department Event Management Platform - Flask + PostgreSQL."""
import os
import threading

import psycopg2
import psycopg2.extras
from flask import Flask, flash, jsonify, redirect, render_template_string, request, url_for

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")

# ---- Configuration (from ConfigMap / Secret env vars) ----
DEPT_NAME = os.getenv("DEPT_NAME", "Department of Computer Science")
EVENT_CATEGORIES = [c.strip() for c in os.getenv("EVENT_CATEGORIES", "Seminar,Workshop,Student Event").split(",") if c.strip()]
MAX_REGISTRATIONS = int(os.getenv("MAX_REGISTRATIONS", "50"))
REGISTRATION_OPEN = os.getenv("REGISTRATION_OPEN", "true").strip().lower() in ("1", "true", "yes", "on")
WELCOME_BANNER = os.getenv("WELCOME_BANNER", "Welcome to the Department Event Portal!")

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("POSTGRES_DB", "eventsdb"),
    "user": os.getenv("POSTGRES_USER", "eventuser"),
    "password": os.getenv("POSTGRES_PASSWORD", "eventpass"),
    "connect_timeout": 5,
}

_schema_ready = False
_schema_lock = threading.Lock()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id          SERIAL PRIMARY KEY,
    title       VARCHAR(200) NOT NULL,
    category    VARCHAR(100) NOT NULL,
    description TEXT,
    event_date  DATE NOT NULL,
    venue       VARCHAR(200),
    seat_limit  INTEGER NOT NULL CHECK (seat_limit > 0),
    created_at  TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS registrations (
    id            SERIAL PRIMARY KEY,
    event_id      INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    student_name  VARCHAR(150) NOT NULL,
    student_email VARCHAR(200) NOT NULL,
    registered_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (event_id, student_email)
);
"""


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def ensure_schema():
    """Create tables once per process. An advisory lock stops replicas racing."""
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        conn = get_conn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(424242)")
                cur.execute(SCHEMA_SQL)
            _schema_ready = True
        finally:
            conn.close()


def query(sql, params=None, fetch=True):
    ensure_schema()
    conn = get_conn()
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return cur.fetchall() if fetch else None
    finally:
        conn.close()


LIST_SQL = """
SELECT e.*, COUNT(r.id) AS registered
FROM events e LEFT JOIN registrations r ON r.event_id = e.id
GROUP BY e.id ORDER BY e.event_date, e.id
"""

PAGE = """
<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ dept }} - Events</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:960px;margin:0 auto;padding:16px;background:#f5f7fa;color:#222}
 header{background:#1f4e79;color:#fff;padding:16px;border-radius:8px}
 .banner{background:#fff3cd;padding:10px;border-radius:6px;margin:12px 0}
 .card{background:#fff;padding:14px;border-radius:8px;margin:10px 0;box-shadow:0 1px 3px #0002}
 .tag{background:#e1ecf7;color:#1f4e79;padding:2px 8px;border-radius:10px;font-size:12px}
 .full{color:#b00020;font-weight:bold} .flash{background:#d4edda;padding:8px;border-radius:6px;margin:6px 0}
 input,select,textarea{padding:6px;margin:4px 0;width:100%;box-sizing:border-box}
 button{background:#1f4e79;color:#fff;border:0;padding:8px 14px;border-radius:6px;cursor:pointer}
 button:disabled{background:#999} .row{display:flex;gap:8px;flex-wrap:wrap} .row>*{flex:1;min-width:140px}
 a{color:#1f4e79}
</style></head><body>
<header><h1>{{ dept }}</h1><div>Event Management Platform &middot; served by {{ host }}</div></header>
<div class="banner">{{ banner }}</div>
{% with msgs = get_flashed_messages() %}{% for m in msgs %}<div class="flash">{{ m }}</div>{% endfor %}{% endwith %}
<p><a href="{{ url_for('index') }}">All events</a> | <a href="{{ url_for('add_event') }}">Add event</a>
 | Registration: <b>{{ 'OPEN' if reg_open else 'CLOSED' }}</b></p>
{{ body|safe }}
</body></html>
"""

INDEX_BODY = """
<h2>Upcoming events</h2>
{% if not events %}<p>No events yet. <a href="{{ url_for('add_event') }}">Add one</a>.</p>{% endif %}
{% for e in events %}
<div class="card">
  <h3>{{ e.title }} <span class="tag">{{ e.category }}</span></h3>
  <div>{{ e.event_date }} &middot; {{ e.venue or 'TBA' }}</div>
  <p>{{ e.description or '' }}</p>
  {% set left = e.seat_limit - e.registered %}
  <div>Seats: {{ e.registered }} / {{ e.seat_limit }}
    {% if left <= 0 %}<span class="full">FULL</span>{% else %}({{ left }} left){% endif %}</div>
  {% if reg_open and left > 0 %}
  <form method="post" action="{{ url_for('register', event_id=e.id) }}" class="row">
    <input name="name" placeholder="Your name" required>
    <input name="email" type="email" placeholder="Email" required>
    <button type="submit">Register</button>
  </form>
  {% endif %}
</div>
{% endfor %}
"""

ADD_BODY = """
<h2>Add an event</h2>
<form method="post" class="card">
  <label>Title <input name="title" required maxlength="200"></label>
  <label>Category <select name="category">{% for c in categories %}<option>{{ c }}</option>{% endfor %}</select></label>
  <label>Date <input name="event_date" type="date" required></label>
  <label>Venue <input name="venue" maxlength="200"></label>
  <label>Seat limit (max {{ max_reg }}) <input name="seat_limit" type="number" min="1" max="{{ max_reg }}" value="{{ max_reg }}" required></label>
  <label>Description <textarea name="description" rows="3"></textarea></label>
  <button type="submit">Create event</button>
</form>
"""


def render(body_tpl, **ctx):
    body = render_template_string(body_tpl, reg_open=REGISTRATION_OPEN, **ctx)
    return render_template_string(PAGE, body=body, dept=DEPT_NAME, banner=WELCOME_BANNER,
                                  reg_open=REGISTRATION_OPEN, host=os.getenv("HOSTNAME", "local"))


@app.route("/")
def index():
    return render(INDEX_BODY, events=query(LIST_SQL))


@app.route("/events/add", methods=["GET", "POST"])
def add_event():
    if request.method == "POST":
        f = request.form
        try:
            seat_limit = int(f.get("seat_limit", MAX_REGISTRATIONS))
        except ValueError:
            seat_limit = MAX_REGISTRATIONS
        seat_limit = max(1, min(seat_limit, MAX_REGISTRATIONS))
        category = f.get("category") if f.get("category") in EVENT_CATEGORIES else EVENT_CATEGORIES[0]
        if not f.get("title", "").strip() or not f.get("event_date"):
            flash("Title and date are required.")
            return redirect(url_for("add_event"))
        query("INSERT INTO events (title, category, description, event_date, venue, seat_limit) "
              "VALUES (%s,%s,%s,%s,%s,%s)",
              (f["title"].strip(), category, f.get("description"), f["event_date"], f.get("venue"), seat_limit),
              fetch=False)
        flash(f"Event '{f['title'].strip()}' created.")
        return redirect(url_for("index"))
    return render(ADD_BODY, categories=EVENT_CATEGORIES, max_reg=MAX_REGISTRATIONS)


def do_register(event_id, name, email):
    """Returns (ok, message). Row lock on the event enforces the seat limit under concurrency."""
    if not REGISTRATION_OPEN:
        return False, "Registration is currently closed."
    if not name or not email:
        return False, "Name and email are required."
    ensure_schema()
    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT title, seat_limit FROM events WHERE id=%s FOR UPDATE", (event_id,))
            row = cur.fetchone()
            if not row:
                return False, "Event not found."
            title, seat_limit = row
            cur.execute("SELECT COUNT(*) FROM registrations WHERE event_id=%s", (event_id,))
            if cur.fetchone()[0] >= seat_limit:
                return False, f"Sorry, '{title}' is full."
            cur.execute("INSERT INTO registrations (event_id, student_name, student_email) VALUES (%s,%s,%s) "
                        "ON CONFLICT (event_id, student_email) DO NOTHING", (event_id, name, email))
            if cur.rowcount == 0:
                return False, f"{email} is already registered for '{title}'."
            return True, f"{name} registered for '{title}'."
    finally:
        conn.close()


@app.route("/events/<int:event_id>/register", methods=["POST"])
def register(event_id):
    ok, msg = do_register(event_id, request.form.get("name", "").strip(), request.form.get("email", "").strip().lower())
    flash(msg)
    return redirect(url_for("index"))


# ---- JSON API ----
@app.route("/api/events")
def api_events():
    rows = query(LIST_SQL)
    for r in rows:
        r["event_date"] = r["event_date"].isoformat()
        r["created_at"] = r["created_at"].isoformat()
    return jsonify(rows)


@app.route("/api/events/<int:event_id>/register", methods=["POST"])
def api_register(event_id):
    data = request.get_json(silent=True) or {}
    ok, msg = do_register(event_id, str(data.get("name", "")).strip(), str(data.get("email", "")).strip().lower())
    return jsonify({"ok": ok, "message": msg}), (201 if ok else 409)


@app.route("/api/config")
def api_config():
    return jsonify({"DEPT_NAME": DEPT_NAME, "EVENT_CATEGORIES": EVENT_CATEGORIES,
                    "MAX_REGISTRATIONS": MAX_REGISTRATIONS, "REGISTRATION_OPEN": REGISTRATION_OPEN,
                    "WELCOME_BANNER": WELCOME_BANNER})


@app.route("/health")
def health():
    try:
        ensure_schema()
        conn = get_conn()
        conn.close()
        return jsonify({"status": "ok", "db": "up"}), 200
    except Exception as exc:  # noqa: BLE001
        return jsonify({"status": "error", "db": str(exc)}), 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
