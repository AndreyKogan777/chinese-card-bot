import asyncio
import logging
from datetime import date

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message

from config import BOT_TOKEN
from services import analyze_business_card
from database import (
    init_db,
    get_or_create_user,
    can_use_free_request,
    get_paid_balance,
    consume_free_request,
    consume_paid_request,
    get_user_stats,
)

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def split_message(text: str, limit: int = 4000):
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:]
    parts.append(text)
    return parts


@dp.message(CommandStart())
async def cmd_start(message: Message):
    get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    await message.answer(
        "Здравствуйте! Отправьте мне фото визитки китайской компании — "
        "я распознаю текст и подготовлю аналитический отчёт о надёжности "
        "этой компании и её представителя.\n\n"
        "🎁 Первый запрос каждый день — бесплатно.\n"
        "⏳ Анализ занимает 30-60 секунд."
    )


@dp.message(Command("balance"))
async def cmd_balance(message: Message):
    get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    stats = get_user_stats(message.from_user.id)
    paid_balance, total_requests, last_free_date = stats
    free_used_today = last_free_date == str(date.today())
    free_status = "уже использован сегодня" if free_used_today else "доступен"

    await message.answer(
        f"📊 Ваша статистика:\n\n"
        f"🎁 Бесплатный запрос сегодня: {free_status}\n"
        f"💰 Платный баланс: {paid_balance} запрос(ов)\n"
        f"📈 Всего запросов за всё время: {total_requests}"
    )


@dp.message(F.photo)
async def handle_photo(message: Message):
    user_id = message.from_user.id
    get_or_create_user(user_id, message.from_user.username, message.from_user.full_name)

    if can_use_free_request(user_id):
        request_type = "free"
    elif get_paid_balance(user_id) > 0:
        request_type = "paid"
    else:
        await message.answer(
            "⚠️ Ваш бесплатный запрос на сегодня уже использован, а платный баланс пуст.\n\n"
            "Бесплатный лимит обновится завтра. Покупка платных пакетов запросов "
            "будет доступна в следующей версии бота — совсем скоро!"
        )
        return

    status_msg = await message.answer("🔍 Распознаю визитку и собираю данные о компании...")

    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    file_bytes_io = await bot.download_file(file.file_path)
    image_bytes = file_bytes_io.read()

    try:
        report = analyze_business_card(image_bytes)
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка при анализе визитки: {e}")
        return

    if request_type == "free":
        consume_free_request(user_id)
    else:
        consume_paid_request(user_id)

    await status_msg.delete()
    for part in split_message(report):
        await message.answer(part)


@dp.message(F.text)
async def handle_text(message: Message):
    await message.answer("Пожалуйста, отправьте фото визитки для анализа.")


async def main():
    init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
