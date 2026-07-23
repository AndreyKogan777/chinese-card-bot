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


def consume_free_request(user_id: int):
    today = str(date.today())
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET last_free_date = ?, total_requests = total_requests + 1 WHERE user_id = ?",
            (today, user_id),
        )
        conn.execute(
            "INSERT INTO requests (user_id, request_type) VALUES (?, 'free')", (user_id,)
        )
        conn.commit()


def consume_paid_request(user_id: int):
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET paid_balance = paid_balance - 1, total_requests = total_requests + 1 WHERE user_id = ?",
            (user_id,),
        )
        conn.execute(
            "INSERT INTO requests (user_id, request_type) VALUES (?, 'paid')", (user_id,)
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

