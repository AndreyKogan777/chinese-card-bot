import asyncio
import logging
from datetime import date

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BotCommand,
)
from aiogram.enums import ParseMode

from config import BOT_TOKEN
from services import analyze_business_card
from payments import create_payment_link, PACKAGE
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

BTN_BALANCE = "📊 Баланс"
BTN_BUY = "💳 Купить пакет"
BTN_HELP = "❓ Помощь"

main_menu_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_BALANCE), KeyboardButton(text=BTN_BUY)],
        [KeyboardButton(text=BTN_HELP)],
    ],
    resize_keyboard=True,
    is_persistent=True,
)


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


async def send_balance(message: Message):
    get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    stats = get_user_stats(message.from_user.id)
    paid_balance, total_requests, last_free_date = stats
    free_used_today = last_free_date == str(date.today())
    free_status = "уже использован сегодня" if free_used_today else "доступен ✅"

    await message.answer(
        f"📊 Ваша статистика:\n\n"
        f"🎁 Бесплатный запрос сегодня: {free_status}\n"
        f"💰 Платный баланс: {paid_balance} запрос(ов)\n"
        f"📈 Всего запросов за всё время: {total_requests}",
        reply_markup=main_menu_keyboard,
    )


async def send_buy_offer(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"Купить {PACKAGE['requests']} запросов — {PACKAGE['price_rub']} руб.",
            callback_data="buy_package"
        )]
    ])
    await message.answer(
        f"Доступен пакет: {PACKAGE['requests']} запросов за {PACKAGE['price_rub']} руб.",
        reply_markup=keyboard,
    )


async def send_help(message: Message):
    await message.answer(
        "❓ Как пользоваться ботом:\n\n"
        "1. Отправьте мне фото визитки китайской компании\n"
        "2. Я распознаю текст и подготовлю аналитический отчёт о надёжности компании\n"
        "3. Анализ занимает 30-60 секунд\n\n"
        f"🎁 Первый запрос каждый день — бесплатно\n"
        f"💳 Пакет из {PACKAGE['requests']} запросов стоит {PACKAGE['price_rub']} руб.\n\n"
        "Доступные команды и кнопки:\n"
        f"{BTN_BALANCE} — узнать баланс и статистику\n"
        f"{BTN_BUY} — купить пакет запросов\n"
        f"{BTN_HELP} — эта справка",
        reply_markup=main_menu_keyboard,
    )


@dp.message(CommandStart())
async def cmd_start(message: Message):
    get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    await message.answer(
        "Здравствуйте! Отправьте мне фото визитки китайской компании — "
        "я распознаю текст и подготовлю аналитический отчёт о надёжности "
        "этой компании и её представителя.\n\n"
        "🎁 Первый запрос каждый день — бесплатно.\n"
        f"💳 Команда /buy — купить пакет из {PACKAGE['requests']} запросов за {PACKAGE['price_rub']} руб.\n"
        "⏳ Анализ занимает 30-60 секунд.\n\n"
        "Используйте кнопки меню ниже в любой момент 👇",
        reply_markup=main_menu_keyboard,
    )


@dp.message(Command("balance"))
async def cmd_balance(message: Message):
    await send_balance(message)


@dp.message(F.text == BTN_BALANCE)
async def button_balance(message: Message):
    await send_balance(message)


@dp.message(Command("buy"))
async def cmd_buy(message: Message):
    await send_buy_offer(message)


@dp.message(F.text == BTN_BUY)
async def button_buy(message: Message):
    await send_buy_offer(message)


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await send_help(message)


@dp.message(F.text == BTN_HELP)
async def button_help(message: Message):
    await send_help(message)


@dp.callback_query(F.data == "buy_package")
async def process_buy_callback(callback: CallbackQuery):
    user_id = callback.from_user.id

    try:
        payment_url = create_payment_link(user_id)
    except Exception as e:
        await callback.message.answer(f"❌ Не удалось создать ссылку на оплату: {e}")
        await callback.answer()
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оплатить", url=payment_url)]
    ])
    await callback.message.answer(
        "Нажмите кнопку ниже для оплаты. После успешной оплаты запросы "
        "будут автоматически зачислены на ваш баланс.",
        reply_markup=keyboard,
    )
    await callback.answer()


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
            "Бесплатный лимит обновится завтра, либо купите пакет запросов кнопкой "
            f"«{BTN_BUY}» или командой /buy.",
            reply_markup=main_menu_keyboard,
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
        try:
            await message.answer(part, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await message.answer(part)

    await message.answer(
        "Готово! Отправьте следующую визитку, когда будете готовы 👇",
        reply_markup=main_menu_keyboard,
    )


@dp.message(F.text)
async def handle_text(message: Message):
    await message.answer(
        "Пожалуйста, отправьте фото визитки для анализа, либо воспользуйтесь кнопками меню ниже.",
        reply_markup=main_menu_keyboard,
    )


import threading
import os
from webhook_server import app as flask_app


def run_flask():
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)


async def set_bot_commands():
    await bot.set_my_commands([
        BotCommand(command="start", description="Начало работы с ботом"),
        BotCommand(command="balance", description="Мой баланс и статистика"),
        BotCommand(command="buy", description="Купить пакет запросов"),
        BotCommand(command="help", description="Как пользоваться ботом"),
    ])


async def main():
    init_db()
    await set_bot_commands()
    await dp.start_polling(bot)


if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(main())
