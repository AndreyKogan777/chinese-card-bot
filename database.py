import os
os.makedirs("/app/data", exist_ok=True)
import sqlite3
from datetime import date
from contextlib import contextmanager

DB_PATH = "/app/data/bot_database.db"


def init_db():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                paid_balance INTEGER DEFAULT 0,
                last_free_date TEXT,
                total_requests INTEGER DEFAULT 0,
                registered_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                request_type TEXT,
                report_text TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deal_id TEXT UNIQUE,
                user_id INTEGER,
                requests_count INTEGER,
                status TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        existing_cols = [row[1] for row in conn.execute("PRAGMA table_info(requests)").fetchall()]
        if "report_text" not in existing_cols:
            conn.execute("ALTER TABLE requests ADD COLUMN report_text TEXT")
        conn.commit()


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def get_or_create_user(user_id: int, username: str, full_name: str):
    with get_connection() as conn:
        cur = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO users (user_id, username, full_name) VALUES (?, ?, ?)",
                (user_id, username, full_name),
            )
            conn.commit()
        else:
            conn.execute(
                "UPDATE users SET username = ?, full_name = ? WHERE user_id = ?",
                (username, full_name, user_id),
            )
            conn.commit()


def can_use_free_request(user_id: int) -> bool:
    today = str(date.today())
    with get_connection() as conn:
        cur = conn.execute(
            "SELECT last_free_date FROM users WHERE user_id = ?", (user_id,)
        )
        row = cur.fetchone()
        last_free_date = row[0] if row else None
        return last_free_date != today


def get_paid_balance(user_id: int) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "SELECT paid_balance FROM users WHERE user_id = ?", (user_id,)
        )
        row = cur.fetchone()
        return row[0] if row else 0


def consume_free_request(user_id: int, report_text: str = None):
    today = str(date.today())
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET last_free_date = ?, total_requests = total_requests + 1 WHERE user_id = ?",
            (today, user_id),
        )
        conn.execute(
            "INSERT INTO requests (user_id, request_type, report_text) VALUES (?, 'free', ?)",
            (user_id, report_text),
        )
        conn.commit()


def consume_paid_request(user_id: int, report_text: str = None):
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET paid_balance = paid_balance - 1, total_requests = total_requests + 1 WHERE user_id = ?",
            (user_id,),
        )
        conn.execute(
            "INSERT INTO requests (user_id, request_type, report_text) VALUES (?, 'paid', ?)",
            (user_id, report_text),
        )
        conn.commit()


def add_paid_requests(user_id: int, count: int):
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET paid_balance = paid_balance + ? WHERE user_id = ?",
            (count, user_id),
        )
        conn.commit()


def get_user_stats(user_id: int):
    with get_connection() as conn:
        cur = conn.execute(
            "SELECT paid_balance, total_requests, last_free_date FROM users WHERE user_id = ?",
            (user_id,),
        )
        return cur.fetchone()


def payment_already_processed(deal_id: str) -> bool:
    with get_connection() as conn:
        cur = conn.execute("SELECT 1 FROM payments WHERE deal_id = ?", (deal_id,))
        return cur.fetchone() is not None


def save_processed_payment(deal_id: str, user_id: int, requests_count: int, status: str = "success"):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO payments (deal_id, user_id, requests_count, status) VALUES (?, ?, ?, ?)",
            (deal_id, user_id, requests_count, status),
        )
        conn.commit()


def admin_get_overview():
    with get_connection() as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_requests = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        today = str(date.today())
        requests_today = conn.execute(
            "SELECT COUNT(*) FROM requests WHERE date(created_at) = ?", (today,)
        ).fetchone()[0]
        new_users_today = conn.execute(
            "SELECT COUNT(*) FROM users WHERE date(registered_at) = ?", (today,)
        ).fetchone()[0]
        total_paid_payments = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(requests_count),0) FROM payments WHERE status = 'success'"
        ).fetchone()
        payments_today = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(requests_count),0) FROM payments WHERE status = 'success' AND date(created_at) = ?",
            (today,),
        ).fetchone()
        return {
            "total_users": total_users,
            "total_requests": total_requests,
            "requests_today": requests_today,
            "new_users_today": new_users_today,
            "total_payments_count": total_paid_payments[0],
            "total_requests_sold": total_paid_payments[1],
            "payments_today_count": payments_today[0],
            "requests_sold_today": payments_today[1],
        }


def admin_get_daily_stats(days: int = 30):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT date(created_at) as day, COUNT(*) as cnt
            FROM requests
            GROUP BY day
            ORDER BY day DESC
            LIMIT ?
            """,
            (days,),
        ).fetchall()
        return rows


def admin_get_users(limit: int = 200, search: str = None):
    with get_connection() as conn:
        if search:
            like = f"%{search}%"
            rows = conn.execute(
                """
                SELECT user_id, username, full_name, paid_balance, total_requests, registered_at
                FROM users
                WHERE CAST(user_id AS TEXT) LIKE ? OR username LIKE ? OR full_name LIKE ?
                ORDER BY registered_at DESC
                LIMIT ?
                """,
                (like, like, like, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT user_id, username, full_name, paid_balance, total_requests, registered_at
                FROM users
                ORDER BY registered_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return rows


def admin_get_requests(limit: int = 200, user_id: int = None):
    with get_connection() as conn:
        if user_id:
            rows = conn.execute(
                """
                SELECT r.id, r.user_id, u.username, u.full_name, r.request_type, r.report_text, r.created_at
                FROM requests r
                LEFT JOIN users u ON u.user_id = r.user_id
                WHERE r.user_id = ?
                ORDER BY r.created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT r.id, r.user_id, u.username, u.full_name, r.request_type, r.report_text, r.created_at
                FROM requests r
                LEFT JOIN users u ON u.user_id = r.user_id
                ORDER BY r.created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return rows


def admin_get_all_user_ids():
    with get_connection() as conn:
        rows = conn.execute("SELECT user_id FROM users").fetchall()
        return [row[0] for row in rows]


def admin_get_request_by_id(request_id: int):
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT r.id, r.user_id, u.username, u.full_name, r.request_type, r.report_text, r.created_at
            FROM requests r
            LEFT JOIN users u ON u.user_id = r.user_id
            WHERE r.id = ?
            """,
            (request_id,),
        ).fetchone()
        return row

