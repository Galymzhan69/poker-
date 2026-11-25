import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).with_name("poker.db")

def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with connect() as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            display_name TEXT,
            points INTEGER NOT NULL DEFAULT 20,
            games INTEGER NOT NULL DEFAULT 0,
            wins INTEGER NOT NULL DEFAULT 0,
            is_banned INTEGER NOT NULL DEFAULT 0,
            joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """)
        con.execute("""
        CREATE TABLE IF NOT EXISTS point_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """)

def ensure_user(user):
    with connect() as con:
        con.execute("""
        INSERT OR IGNORE INTO users(user_id, username, display_name)
        VALUES (?, ?, ?)
        """, (user.id, user.username, user.full_name))
        con.execute("""
        UPDATE users SET username=?, display_name=? WHERE user_id=?
        """, (user.username, user.full_name, user.id))

def get_user(user_id):
    with connect() as con:
        return con.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

def is_banned(user_id):
    row = get_user(user_id)
    return bool(row and row["is_banned"])

def change_points(admin_id, user_id, amount, note="admin adjustment"):
    with connect() as con:
        row = con.execute("SELECT points FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return False, "Пользователь не найден."
        new_value = max(0, row["points"] + amount)
        actual = new_value - row["points"]
        con.execute("UPDATE users SET points=? WHERE user_id=?", (new_value, user_id))
        con.execute(
            "INSERT INTO point_log(admin_id,user_id,amount,note) VALUES(?,?,?,?)",
            (admin_id, user_id, actual, note)
        )
        return True, f"Баланс игровых очков: {new_value}"

def set_ban(user_id, value):
    with connect() as con:
        cur = con.execute("UPDATE users SET is_banned=? WHERE user_id=?", (int(value), user_id))
        return cur.rowcount > 0

def top_users(limit=10):
    with connect() as con:
        return con.execute("""
        SELECT display_name, points, games, wins
        FROM users ORDER BY wins DESC, points DESC LIMIT ?
        """, (limit,)).fetchall()

def user_count():
    with connect() as con:
        return con.execute("SELECT COUNT(*) FROM users").fetchone()[0]

def add_game_result(user_ids, winner_ids):
    with connect() as con:
        for uid in user_ids:
            con.execute("UPDATE users SET games=games+1 WHERE user_id=?", (uid,))
        for uid in winner_ids:
            con.execute("UPDATE users SET wins=wins+1 WHERE user_id=?", (uid,))
