import hashlib
import hmac
import json
from functools import wraps

from flask import Flask, request, jsonify, Response, redirect, session

from config import GPL_API_KEY, ADMIN_PASSWORD
from database import (
    add_paid_requests,
    init_db,
    payment_already_processed,
    save_processed_payment,
    admin_get_overview,
    admin_get_daily_stats,
    admin_get_users,
    admin_get_requests,
    admin_get_request_by_id,
    admin_get_all_user_ids,
)
from messaging import send_message_sync, broadcast_sync

app = Flask(__name__)
app.secret_key = ADMIN_PASSWORD or "change-me-secret-key"
init_db()


def build_checksum_string(data: dict) -> str:
    params = dict(data)
    params.pop("checksum", None)
    params.pop("customParams", None)

    sorted_keys = sorted(params.keys(), key=lambda k: k.lower())

    sign_parts = []
    for key in sorted_keys:
        value = params[key]
        if isinstance(value, bool):
            value = int(value)
        elif isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        elif value is None:
            value = ""
        sign_parts.append(f"{key};{value};")
    return "".join(sign_parts)


def calculate_checksum(data: dict) -> str:
    sign_string = build_checksum_string(data)
    signature = hmac.new(
        GPL_API_KEY.encode("utf-8"),
        sign_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest().upper()
    return signature


@app.route("/gpl-webhook", methods=["POST"])
def gpl_webhook():
    data = request.get_json(force=True)

    received_checksum = data.get("checksum", "")
    calculated_checksum = calculate_checksum(data)

    if received_checksum != calculated_checksum:
        return jsonify({"status": "error", "reason": "invalid checksum"}), 400

    notification_type = data.get("notificationType")
    is_success = data.get("isSuccess")
    deal_id = data.get("dealId")
    custom_params = data.get("customParams", {}) or {}

    user_id = custom_params.get("user_id")
    requests_count = custom_params.get("requests_count")

    if notification_type != 1 or is_success is not True:
        return jsonify({"status": "ignored", "reason": "not successful payment"}), 200

    if not deal_id or not user_id or not requests_count:
        return jsonify({"status": "error", "reason": "missing required fields"}), 400

    if payment_already_processed(deal_id):
        return jsonify({"status": "ok", "reason": "already processed"}), 200

    add_paid_requests(int(user_id), int(requests_count))
    save_processed_payment(deal_id, int(user_id), int(requests_count), "success")

    return jsonify({"status": "ok"}), 200


@app.route("/health", methods=["GET"])
def health():
    return "OK v2", 200


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect("/admin/login")
        return f(*args, **kwargs)
    return wrapper


PAGE_STYLE = """
<style>
body { font-family: -apple-system, Arial, sans-serif; background: #0f1220; color: #e8e8f0; margin: 0; padding: 20px; }
h1, h2 { color: #fff; }
a { color: #7aa2ff; text-decoration: none; }
table { width: 100%; border-collapse: collapse; margin-top: 15px; background: #171a2b; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #2a2e45; font-size: 14px; }
th { background: #1f2338; }
tr:hover { background: #1c2036; }
.cards { display: flex; gap: 15px; flex-wrap: wrap; margin-top: 15px; }
.card { background: #171a2b; border-radius: 10px; padding: 15px 20px; min-width: 160px; }
.card .value { font-size: 26px; font-weight: bold; color: #7aa2ff; }
.card .label { font-size: 13px; color: #9aa0c0; }
nav { margin-bottom: 20px; }
nav a { margin-right: 15px; padding: 8px 14px; background: #171a2b; border-radius: 8px; display: inline-block; }
input[type=password], input[type=text] { padding: 8px; border-radius: 6px; border: 1px solid #2a2e45; background: #171a2b; color: #fff; }
button { padding: 8px 16px; border-radius: 6px; border: none; background: #7aa2ff; color: #0f1220; font-weight: bold; cursor: pointer; }
.report-box { background: #171a2b; padding: 15px; border-radius: 8px; white-space: pre-wrap; margin-top: 10px; font-size: 13px; }
.badge-free { color: #7dffb0; }
.badge-paid { color: #ffd27a; }
</style>
"""


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = ""
    if request.method == "POST":
        password = request.form.get("password", "").strip()
        expected = (ADMIN_PASSWORD or "").strip()
        if expected and password == expected:
            session["logged_in"] = True
            return redirect("/admin")
        if not expected:
            error = "<p style='color:#ff7a7a'>ADMIN_PASSWORD не задан на сервере</p>"
        else:
            error = f"<p style='color:#ff7a7a'>Неверный пароль (введено символов: {len(password)}, ожидается: {len(expected)})</p>"
    return Response(f"""
    <html><head>{PAGE_STYLE}</head><body>
    <h1>Вход в админ-панель</h1>
    {error}
    <form method="post">
        <input type="password" name="password" placeholder="Пароль" autofocus>
        <button type="submit">Войти</button>
    </form>
    </body></html>
    """)


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin/login")


@app.route("/admin")
@login_required
def admin_dashboard():
    overview = admin_get_overview()
    daily = admin_get_daily_stats(30)
    daily_rows = "".join(
        f"<tr><td>{day}</td><td>{cnt}</td></tr>" for day, cnt in daily
    )
    return Response(f"""
    <html><head>{PAGE_STYLE}</head><body>
    <nav>
        <a href="/admin">Обзор</a>
        <a href="/admin/users">Пользователи</a>
        <a href="/admin/requests">Все запросы</a>
        <a href="/admin/message">Написать пользователю</a>
        <a href="/admin/logout">Выйти</a>
    </nav>
    <h1>Обзор</h1>
    <div class="cards">
        <div class="card"><div class="value">{overview['total_users']}</div><div class="label">Всего пользователей</div></div>
        <div class="card"><div class="value">{overview['new_users_today']}</div><div class="label">Новых сегодня</div></div>
        <div class="card"><div class="value">{overview['total_requests']}</div><div class="label">Всего запросов</div></div>
        <div class="card"><div class="value">{overview['requests_today']}</div><div class="label">Запросов сегодня</div></div>
        <div class="card"><div class="value">{overview['total_payments_count']}</div><div class="label">Оплат всего</div></div>
        <div class="card"><div class="value">{overview['requests_sold_today']}</div><div class="label">Запросов продано сегодня</div></div>
    </div>
    <h2>Запросы по дням (последние 30 дней)</h2>
    <table>
        <tr><th>Дата</th><th>Количество запросов</th></tr>
        {daily_rows}
    </table>
    </body></html>
    """)


@app.route("/admin/users")
@login_required
def admin_users():
    search = request.args.get("q", "").strip()
    users = admin_get_users(limit=300, search=search or None)
    rows = ""
    for u in users:
        user_id, username, full_name, paid_balance, total_requests, registered_at = u
        uname = f"@{username}" if username else "-"
        rows += (
            f"<tr><td>{user_id}</td><td>{uname}</td><td>{full_name or '-'}</td>"
            f"<td>{paid_balance}</td><td>{total_requests}</td><td>{registered_at}</td>"
            f"<td><a href='/admin/requests?user_id={user_id}'>Запросы</a> | <a href='/admin/message?user_id={user_id}'>Написать</a></td></tr>"
        )
    return Response(f"""
    <html><head>{PAGE_STYLE}</head><body>
    <nav>
        <a href="/admin">Обзор</a>
        <a href="/admin/users">Пользователи</a>
        <a href="/admin/requests">Все запросы</a>
        <a href="/admin/message">Написать пользователю</a>
        <a href="/admin/logout">Выйти</a>
    </nav>
    <h1>Пользователи</h1>
    <form method="get">
        <input type="text" name="q" placeholder="Поиск по имени, username, ID" value="{search}">
        <button type="submit">Найти</button>
    </form>
    <table>
        <tr><th>ID</th><th>Username</th><th>Имя</th><th>Баланс</th><th>Всего запросов</th><th>Регистрация</th><th></th></tr>
        {rows}
    </table>
    </body></html>
    """)


@app.route("/admin/requests")
@login_required
def admin_requests():
    user_id = request.args.get("user_id")
    user_id_int = int(user_id) if user_id else None
    reqs = admin_get_requests(limit=300, user_id=user_id_int)
    rows = ""
    for r in reqs:
        rid, uid, username, full_name, req_type, report_text, created_at = r
        uname = f"@{username}" if username else (full_name or str(uid))
        badge_class = "badge-free" if req_type == "free" else "badge-paid"
        preview = (report_text or "")[:80].replace("\n", " ")
        rows += (
            f"<tr><td>{rid}</td><td>{uid} ({uname})</td>"
            f"<td class='{badge_class}'>{req_type}</td>"
            f"<td>{created_at}</td>"
            f"<td>{preview}...</td>"
            f"<td><a href='/admin/report/{rid}'>Открыть отчёт</a></td></tr>"
        )
    title = f"Запросы пользователя {user_id}" if user_id else "Все запросы"
    return Response(f"""
    <html><head>{PAGE_STYLE}</head><body>
    <nav>
        <a href="/admin">Обзор</a>
        <a href="/admin/users">Пользователи</a>
        <a href="/admin/requests">Все запросы</a>
        <a href="/admin/message">Написать пользователю</a>
        <a href="/admin/logout">Выйти</a>
    </nav>
    <h1>{title}</h1>
    <table>
        <tr><th>ID</th><th>Пользователь</th><th>Тип</th><th>Дата</th><th>Превью отчёта</th><th></th></tr>
        {rows}
    </table>
    </body></html>
    """)


@app.route("/admin/report/<int:request_id>")
@login_required
def admin_report(request_id):
    r = admin_get_request_by_id(request_id)
    if not r:
        return Response("Запрос не найден", status=404)
    rid, uid, username, full_name, req_type, report_text, created_at = r
    uname = f"@{username}" if username else (full_name or str(uid))
    return Response(f"""
    <html><head>{PAGE_STYLE}</head><body>
    <nav>
        <a href="/admin">Обзор</a>
        <a href="/admin/users">Пользователи</a>
        <a href="/admin/requests">Все запросы</a>
        <a href="/admin/message">Написать пользователю</a>
        <a href="/admin/logout">Выйти</a>
    </nav>
    <h1>Отчёт #{rid}</h1>
    <p>Пользователь: {uid} ({uname})</p>
    <p>Тип запроса: {req_type}</p>
    <p>Дата: {created_at}</p>
    <div class="report-box">{report_text or '(текст отчёта отсутствует)'}</div>
    </body></html>
    """)


@app.route("/admin/message", methods=["GET", "POST"])
@login_required
def admin_message():
    result_html = ""
    prefill_user_id = request.args.get("user_id", "")

    if request.method == "POST":
        mode = request.form.get("mode")
        text = request.form.get("text", "").strip()

        if not text:
            result_html = "<p style='color:#ff7a7a'>Текст сообщения не может быть пустым</p>"
        elif mode == "single":
            target_id = request.form.get("user_id", "").strip()
            if not target_id.isdigit():
                result_html = "<p style='color:#ff7a7a'>Некорректный ID пользователя</p>"
            else:
                ok, err = send_message_sync(int(target_id), text)
                if ok:
                    result_html = f"<p style='color:#7dffb0'>Сообщение успешно отправлено пользователю {target_id}</p>"
                else:
                    result_html = f"<p style='color:#ff7a7a'>Ошибка отправки: {err}</p>"
        elif mode == "broadcast":
            user_ids = admin_get_all_user_ids()
            stats = broadcast_sync(user_ids, text)
            errors_html = "<br>".join(stats["errors"]) if stats["errors"] else ""
            result_html = (
                f"<p style='color:#7dffb0'>Рассылка завершена. Отправлено: {stats['sent']}, "
                f"не доставлено: {stats['failed']}</p>"
                f"<p style='color:#ff7a7a; font-size:12px'>{errors_html}</p>"
            )

    return Response(f"""
    <html><head>{PAGE_STYLE}</head><body>
    <nav>
        <a href="/admin">Обзор</a>
        <a href="/admin/users">Пользователи</a>
        <a href="/admin/requests">Все запросы</a>
        <a href="/admin/message">Написать пользователю</a>
        <a href="/admin/logout">Выйти</a>
    </nav>
    <h1>Написать пользователю от имени бота</h1>
    {result_html}

    <h2>Сообщение одному пользователю</h2>
    <form method="post">
        <input type="hidden" name="mode" value="single">
        <p><input type="text" name="user_id" placeholder="ID пользователя" value="{prefill_user_id}" required></p>
        <p><textarea name="text" rows="6" cols="60" placeholder="Текст сообщения" style="padding:8px; border-radius:6px; border:1px solid #2a2e45; background:#171a2b; color:#fff;" required></textarea></p>
        <button type="submit">Отправить</button>
    </form>

    <h2>Рассылка всем пользователям</h2>
    <form method="post" onsubmit="return confirm('Отправить это сообщение ВСЕМ пользователям бота?');">
        <input type="hidden" name="mode" value="broadcast">
        <p><textarea name="text" rows="6" cols="60" placeholder="Текст сообщения для рассылки" style="padding:8px; border-radius:6px; border:1px solid #2a2e45; background:#171a2b; color:#fff;" required></textarea></p>
        <button type="submit">Разослать всем</button>
    </form>
    </body></html>
    """)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

