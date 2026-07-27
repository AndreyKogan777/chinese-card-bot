import asyncio

_bot = None
_loop = None


def register_bot(bot_instance, loop):
    global _bot, _loop
    _bot = bot_instance
    _loop = loop


def send_message_sync(user_id: int, text: str) -> tuple[bool, str]:
    if _bot is None or _loop is None:
        return False, "Бот ещё не инициализирован"
    try:
        future = asyncio.run_coroutine_threadsafe(
            _bot.send_message(chat_id=user_id, text=text), _loop
        )
        future.result(timeout=15)
        return True, "ok"
    except Exception as e:
        return False, str(e)


def broadcast_sync(user_ids: list, text: str) -> dict:
    sent = 0
    failed = 0
    errors = []
    for uid in user_ids:
        ok, err = send_message_sync(uid, text)
        if ok:
            sent += 1
        else:
            failed += 1
            errors.append(f"{uid}: {err}")
    return {"sent": sent, "failed": failed, "errors": errors[:20]}
