import os, secrets
from flask import Flask, request, jsonify, send_from_directory, session

app = Flask(__name__, static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

DATABASE_URL = os.environ.get("DATABASE_URL")
DB_PATH      = os.environ.get("DB_PATH", "birthdays.db")


def get_db():
    if DATABASE_URL:
        import psycopg2, psycopg2.extras
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        return conn
    else:
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn


def query(conn, sql, params=()):
    cur = conn.cursor()
    if DATABASE_URL:
        import psycopg2.extras
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql.replace("?", "%s"), params)
    else:
        cur.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def execute(conn, sql, params=()):
    cur = conn.cursor()
    if DATABASE_URL:
        cur.execute(sql.replace("?", "%s"), params)
        conn.commit()
        if cur.description:
            row = cur.fetchone()
            return row[0] if row else None
        return None
    else:
        cur.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def init_db():
    conn = get_db()
    if DATABASE_URL:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS neighbors (
                id           SERIAL PRIMARY KEY,
                display_name TEXT    NOT NULL UNIQUE,
                unit         TEXT    NOT NULL,
                month        INTEGER,
                day          INTEGER,
                year         INTEGER,
                note         TEXT,
                reactions    INTEGER DEFAULT 0,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("ALTER TABLE neighbors ADD COLUMN IF NOT EXISTS reactions INTEGER DEFAULT 0")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS wishes (
                id          SERIAL PRIMARY KEY,
                neighbor_id INTEGER NOT NULL,
                author_name TEXT    NOT NULL,
                message     TEXT    NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    else:
        import sqlite3
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS neighbors (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                display_name TEXT    NOT NULL UNIQUE,
                unit         TEXT    NOT NULL,
                month        INTEGER,
                day          INTEGER,
                year         INTEGER,
                note         TEXT,
                reactions    INTEGER DEFAULT 0,
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS wishes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                neighbor_id INTEGER NOT NULL,
                author_name TEXT    NOT NULL,
                message     TEXT    NOT NULL,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        try:
            cur.execute("ALTER TABLE neighbors ADD COLUMN reactions INTEGER DEFAULT 0")
            conn.commit()
        except Exception:
            pass
    conn.close()


def current_user():
    return session.get("user_id")


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/birthdays")
def list_birthdays():
    conn = get_db()
    rows = query(conn,
        "SELECT id, display_name, unit, month, day, year, note, "
        "COALESCE(reactions, 0) AS reactions "
        "FROM neighbors WHERE month IS NOT NULL ORDER BY month, day")
    conn.close()
    return jsonify(rows)


@app.route("/api/register", methods=["POST"])
def register():
    d     = request.get_json(silent=True) or {}
    name  = (d.get("name") or "").strip()
    unit  = (d.get("unit") or "").strip()
    month = d.get("month")
    day   = d.get("day")
    year  = d.get("year") or None
    note  = (d.get("note") or "").strip()

    if not name:
        return jsonify({"error": "Name is required"}), 400
    if not unit:
        return jsonify({"error": "Unit / house number is required"}), 400
    if not month or not day:
        return jsonify({"error": "Birthday month and day are required"}), 400

    conn = get_db()
    if query(conn, "SELECT 1 FROM neighbors WHERE display_name = ?", (name,)):
        conn.close()
        return jsonify({"error": "That name is already taken — try a variation or add your initial"}), 409

    if DATABASE_URL:
        user_id = execute(conn,
            "INSERT INTO neighbors (display_name, unit, month, day, year, note) "
            "VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (name, unit, month, day, year, note))
    else:
        user_id = execute(conn,
            "INSERT INTO neighbors (display_name, unit, month, day, year, note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (name, unit, month, day, year, note))
    conn.close()

    session["user_id"]   = user_id
    session["user_name"] = name
    return jsonify({"id": user_id, "display_name": name, "unit": unit,
                    "month": month, "day": day, "year": year, "note": note,
                    "reactions": 0}), 201


@app.route("/api/login", methods=["POST"])
def login():
    d    = request.get_json(silent=True) or {}
    name = (d.get("name") or "").strip()

    conn = get_db()
    rows = query(conn, "SELECT * FROM neighbors WHERE display_name = ?", (name,))
    conn.close()

    if not rows:
        return jsonify({"error": "Name not found. Are you registered yet?"}), 401

    row = rows[0]
    session["user_id"]   = row["id"]
    session["user_name"] = row["display_name"]
    return jsonify(row)


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me")
def me():
    uid = current_user()
    if not uid:
        return jsonify({"error": "Not logged in"}), 401
    conn = get_db()
    rows = query(conn, "SELECT * FROM neighbors WHERE id = ?", (uid,))
    conn.close()
    if not rows:
        session.clear()
        return jsonify({"error": "Not found"}), 404
    return jsonify(rows[0])


@app.route("/api/me", methods=["PUT"])
def update_me():
    uid = current_user()
    if not uid:
        return jsonify({"error": "Not logged in"}), 401

    d     = request.get_json(silent=True) or {}
    unit  = (d.get("unit") or "").strip()
    month = d.get("month")
    day   = d.get("day")
    year  = d.get("year") or None
    note  = (d.get("note") or "").strip()

    if not unit:
        return jsonify({"error": "Unit is required"}), 400
    if not month or not day:
        return jsonify({"error": "Birthday month and day are required"}), 400

    conn = get_db()
    execute(conn,
        "UPDATE neighbors SET unit=?, month=?, day=?, year=?, note=? WHERE id=?",
        (unit, month, day, year, note, uid))
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/me", methods=["DELETE"])
def delete_me():
    uid = current_user()
    if not uid:
        return jsonify({"error": "Not logged in"}), 401
    conn = get_db()
    execute(conn, "DELETE FROM neighbors WHERE id = ?", (uid,))
    conn.close()
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/reactions/<int:neighbor_id>", methods=["POST"])
def react(neighbor_id):
    conn = get_db()
    execute(conn,
        "UPDATE neighbors SET reactions = COALESCE(reactions, 0) + 1 WHERE id = ?",
        (neighbor_id,))
    rows = query(conn,
        "SELECT COALESCE(reactions, 0) AS reactions FROM neighbors WHERE id = ?",
        (neighbor_id,))
    conn.close()
    return jsonify({"count": rows[0]["reactions"] if rows else 0})


@app.route("/api/wishes/<int:neighbor_id>")
def get_wishes(neighbor_id):
    conn = get_db()
    rows = query(conn,
        "SELECT author_name, message, created_at FROM wishes WHERE neighbor_id = ? ORDER BY created_at DESC",
        (neighbor_id,))
    conn.close()
    return jsonify(rows)


@app.route("/api/wishes/<int:neighbor_id>", methods=["POST"])
def post_wish(neighbor_id):
    uid = current_user()
    if not uid:
        return jsonify({"error": "Login to leave a wish"}), 401
    d       = request.get_json(silent=True) or {}
    message = (d.get("message") or "").strip()
    if not message:
        return jsonify({"error": "Message is required"}), 400
    conn = get_db()
    rows = query(conn, "SELECT display_name FROM neighbors WHERE id = ?", (uid,))
    author = rows[0]["display_name"] if rows else "Anonymous"
    execute(conn,
        "INSERT INTO wishes (neighbor_id, author_name, message) VALUES (?, ?, ?)",
        (neighbor_id, author, message))
    conn.close()
    return jsonify({"ok": True, "author_name": author, "message": message}), 201


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
