import asyncio
import logging
import sqlite3
import random
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Tuple

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ContentType
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ------------------ КОНФИГ ------------------
BOT_TOKEN = "8756968212:AAGdxXZpt8wkLnCpd02aLI043IHoyJbqt38"   # замените на токен от @BotFather
ADMIN_IDS = [8732825022]             # ваш Telegram ID

PAYMENT_DETAILS = {
    "card": "2200700538676841",
    "name": "Сергей",
    "currency": "₽"
}

VIDEO_PRICE = 2
PREMIUM_PRICE_RUB = 99

DAILY_BONUS_NORMAL = 2
DAILY_BONUS_PREMIUM = 5

DIAMOND_PACKS = {6: 50, 10: 90, 12: 110, 20: 150}

DB_PATH = "bot_database.db"

# ------------------ ИНИЦИАЛИЗАЦИЯ БД ------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Таблица users
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        balance INTEGER DEFAULT 4,
        is_premium INTEGER DEFAULT 0,
        premium_until INTEGER DEFAULT 0,
        banned INTEGER DEFAULT 0,
        payment_id TEXT UNIQUE,
        daily_bonus_last INTEGER DEFAULT 0,
        lang TEXT DEFAULT 'ru'
    )''')
    
    # Добавляем недостающие колонки (без ошибок)
    c.execute("PRAGMA table_info(users)")
    existing = [col[1] for col in c.fetchall()]
    for col, col_type in [("balance", "INTEGER DEFAULT 4"), ("is_premium", "INTEGER DEFAULT 0"),
                          ("premium_until", "INTEGER DEFAULT 0"), ("banned", "INTEGER DEFAULT 0"),
                          ("payment_id", "TEXT UNIQUE"), ("daily_bonus_last", "INTEGER DEFAULT 0"),
                          ("lang", "TEXT DEFAULT 'ru'")]:
        if col not in existing:
            if col == "payment_id":
                c.execute("ALTER TABLE users ADD COLUMN payment_id TEXT")
                c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_id ON users(payment_id) WHERE payment_id IS NOT NULL")
            else:
                c.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")
    
    # Таблица videos
    c.execute('''CREATE TABLE IF NOT EXISTS videos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        file_id TEXT,
        is_vip INTEGER DEFAULT 0,
        category TEXT DEFAULT 'life'
    )''')
    
    # Таблица purchases
    c.execute('''CREATE TABLE IF NOT EXISTS purchases (
        user_id INTEGER,
        video_id INTEGER,
        timestamp INTEGER,
        PRIMARY KEY (user_id, video_id)
    )''')
    
    # Таблица promocodes
    c.execute('''CREATE TABLE IF NOT EXISTS promocodes (
        code TEXT PRIMARY KEY,
        reward INTEGER,
        max_uses INTEGER,
        used_count INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS used_promocodes (
        user_id INTEGER,
        code TEXT,
        PRIMARY KEY (user_id, code)
    )''')
    
    # Таблица логов алмазов
    c.execute('''CREATE TABLE IF NOT EXISTS diamond_logs (
        user_id INTEGER,
        amount INTEGER,
        reason TEXT,
        timestamp INTEGER
    )''')
    
    # Таблица заявок на оплату (без diamonds, теперь сумма в amount_rub и тип)
    c.execute('''CREATE TABLE IF NOT EXISTS pending_payments (
        user_id INTEGER,
        payment_id TEXT,
        amount_rub INTEGER,
        type TEXT,   -- 'diamonds' или 'premium'
        diamonds INTEGER DEFAULT 0,
        timestamp INTEGER,
        status TEXT DEFAULT 'pending'
    )''')
    # Проверяем наличие колонки diamonds
    c.execute("PRAGMA table_info(pending_payments)")
    pending_cols = [col[1] for col in c.fetchall()]
    if "diamonds" not in pending_cols:
        c.execute("ALTER TABLE pending_payments ADD COLUMN diamonds INTEGER DEFAULT 0")
    if "status" not in pending_cols:
        c.execute("ALTER TABLE pending_payments ADD COLUMN status TEXT DEFAULT 'pending'")
    
    conn.commit()
    conn.close()
    
    # Генерация payment_id для старых пользователей
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE payment_id IS NULL")
    rows = c.fetchall()
    for (user_id,) in rows:
        while True:
            new_id = f"#Radion_{random.randint(10000, 999999)}"
            try:
                c.execute("UPDATE users SET payment_id = ? WHERE user_id = ?", (new_id, user_id))
                conn.commit()
                break
            except sqlite3.IntegrityError:
                continue
    conn.close()

init_db()

# ------------------ РАБОТА С БД ------------------
def get_user(user_id: int) -> Optional[Dict]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id, username, balance, is_premium, premium_until, banned, payment_id, daily_bonus_last, lang FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "user_id": row[0], "username": row[1], "balance": row[2],
            "is_premium": row[3], "premium_until": row[4], "banned": row[5],
            "payment_id": row[6], "daily_bonus_last": row[7], "lang": row[8]
        }
    return None

def generate_unique_payment_id() -> str:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    while True:
        new_id = f"#Radion_{random.randint(10000, 999999)}"
        c.execute("SELECT 1 FROM users WHERE payment_id = ?", (new_id,))
        if not c.fetchone():
            conn.close()
            return new_id
        # повторяем

def create_user(user_id: int, username: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    payment_id = generate_unique_payment_id()
    c.execute("INSERT OR IGNORE INTO users (user_id, username, payment_id) VALUES (?, ?, ?)", (user_id, username, payment_id))
    conn.commit()
    conn.close()

def update_balance(user_id: int, delta: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (delta, user_id))
    conn.commit()
    conn.close()

def set_premium(user_id: int, days: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    until = int((datetime.now() + timedelta(days=days)).timestamp())
    c.execute("UPDATE users SET is_premium = 1, premium_until = ? WHERE user_id = ?", (until, user_id))
    conn.commit()
    conn.close()

def set_ban(user_id: int, banned: bool):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET banned = ? WHERE user_id = ?", (1 if banned else 0, user_id))
    conn.commit()
    conn.close()

def get_all_videos(vip_only=False) -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if vip_only:
        c.execute("SELECT id, name, file_id FROM videos WHERE is_vip = 1")
    else:
        c.execute("SELECT id, name, file_id FROM videos")
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "name": r[1], "file_id": r[2]} for r in rows]

def get_random_video() -> Optional[Dict]:
    """Возвращает случайное обычное (не VIP) видео"""
    videos = get_all_videos(vip_only=False)
    if not videos:
        return None
    return random.choice(videos)

def add_video(name: str, file_id: str, is_vip: int = 0):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO videos (name, file_id, is_vip) VALUES (?, ?, ?)", (name, file_id, is_vip))
    conn.commit()
    conn.close()

def remove_video(video_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM videos WHERE id = ?", (video_id,))
    conn.commit()
    conn.close()

def add_purchase(user_id: int, video_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO purchases (user_id, video_id, timestamp) VALUES (?, ?, ?)", (user_id, video_id, int(datetime.now().timestamp())))
    conn.commit()
    conn.close()

def get_daily_bonus_last(user_id: int) -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT daily_bonus_last FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

def set_daily_bonus_last(user_id: int, timestamp: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET daily_bonus_last = ? WHERE user_id = ?", (timestamp, user_id))
    conn.commit()
    conn.close()

def apply_promocode(user_id: int, code: str) -> Tuple[bool, int]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT reward, max_uses, used_count FROM promocodes WHERE code = ?", (code,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False, 0
    reward, max_uses, used_count = row
    if max_uses > 0 and used_count >= max_uses:
        conn.close()
        return False, 0
    c.execute("SELECT 1 FROM used_promocodes WHERE user_id = ? AND code = ?", (user_id, code))
    if c.fetchone():
        conn.close()
        return False, 0
    c.execute("UPDATE promocodes SET used_count = used_count + 1 WHERE code = ?", (code,))
    c.execute("INSERT INTO used_promocodes (user_id, code) VALUES (?, ?)", (user_id, code))
    c.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (reward, user_id))
    conn.commit()
    conn.close()
    return True, reward

def get_all_promocodes():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT code, reward, max_uses, used_count FROM promocodes")
    rows = c.fetchall()
    conn.close()
    return rows

def add_promocode(code: str, reward: int, max_uses: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO promocodes (code, reward, max_uses, used_count) VALUES (?, ?, ?, 0)", (code, reward, max_uses))
    conn.commit()
    conn.close()

def delete_promocode(code: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM promocodes WHERE code = ?", (code,))
    conn.commit()
    conn.close()

def get_stats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    users = c.fetchone()[0]
    c.execute("SELECT SUM(balance) FROM users")
    total_balance = c.fetchone()[0] or 0
    c.execute("SELECT COUNT(*) FROM videos")
    videos = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM purchases")
    purchases = c.fetchone()[0]
    now = int(datetime.now().timestamp())
    c.execute("SELECT COUNT(*) FROM users WHERE is_premium = 1 AND premium_until > ?", (now,))
    premium_users = c.fetchone()[0]
    conn.close()
    return users, total_balance, videos, purchases, premium_users

# ------------------ КЛАВИАТУРЫ ------------------
def get_main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    user = get_user(user_id)
    if not user:
        user = {"balance": 0, "is_premium": 0, "premium_until": 0, "username": "гость"}
    premium_active = user['is_premium'] and user['premium_until'] > int(datetime.now().timestamp())
    premium_status = "✅ ДА" if premium_active else "❌ НЕТ"
    text = f"👋 ПРИВЕТ, {user['username'] or 'гость'}!\n\n🏠 ГЛАВНОЕ МЕНЮ\n\n💎 АЛМАЗЫ: {user['balance']}\n⭐ PREMIUM: {premium_status}\n\n📢 НАШ КАНАЛ: https://t.me/Radion_officiali\n👨‍💼 АДМИН: @scam_lil"
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎬 СМОТРЕТЬ ВИДЕО", callback_data="watch_video"))
    builder.row(InlineKeyboardButton(text="💎 КУПИТЬ АЛМАЗЫ", callback_data="buy_diamonds_menu"))
    builder.row(InlineKeyboardButton(text="⭐ PREMIUM", callback_data="premium_menu"))
    builder.row(InlineKeyboardButton(text="👤 ПРОФИЛЬ", callback_data="profile"))
    builder.row(InlineKeyboardButton(text="🎫 ПРОМОКОД", callback_data="promocode"))
    builder.row(InlineKeyboardButton(text="🎁 ЕЖЕДНЕВНЫЙ БОНУС", callback_data="daily_bonus"))
    builder.row(InlineKeyboardButton(text="📞 ПОДДЕРЖКА", callback_data="support"))
    if user_id in ADMIN_IDS:
        builder.row(InlineKeyboardButton(text="⚙️ АДМИН ПАНЕЛЬ", callback_data="admin_panel"))
    return builder.as_markup()

def get_diamond_packs_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for diamonds, price in DIAMOND_PACKS.items():
        builder.row(InlineKeyboardButton(text=f"💎 {diamonds} алмазов – {price}₽", callback_data=f"buy_diamonds_{diamonds}"))
    builder.row(InlineKeyboardButton(text="◀️ НАЗАД", callback_data="main_menu"))
    return builder.as_markup()

def get_payment_keyboard(payment_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📋 СКОПИРОВАТЬ ID", callback_data=f"copy_id_{payment_id}"))
    builder.row(InlineKeyboardButton(text="✅ Я ОПЛАТИЛ", callback_data="i_paid"))
    builder.row(InlineKeyboardButton(text="❌ ОТМЕНА", callback_data="main_menu"))
    return builder.as_markup()

def get_admin_payment_keyboard(user_id: int, payment_id: str, amount_rub: int, type_payment: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🚫 ЗАБАНИТЬ", callback_data=f"admin_ban_user_{user_id}"))
    builder.row(InlineKeyboardButton(text="✅ ВЫДАТЬ", callback_data=f"admin_approve_{payment_id}_{user_id}_{type_payment}"))
    builder.row(InlineKeyboardButton(text="❌ ОТКАЗАНО", callback_data=f"admin_reject_{payment_id}_{user_id}"))
    return builder.as_markup()

def get_admin_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="➕ ДОБАВИТЬ ВИДЕО", callback_data="admin_add_video"))
    builder.row(InlineKeyboardButton(text="🗑 УДАЛИТЬ ВИДЕО", callback_data="admin_del_video"))
    builder.row(InlineKeyboardButton(text="💎 ВЫДАТЬ АЛМАЗЫ", callback_data="admin_give_diamonds"))
    builder.row(InlineKeyboardButton(text="⭐ ВЫДАТЬ PREMIUM", callback_data="admin_give_premium"))
    builder.row(InlineKeyboardButton(text="🚫 ЗАБАНИТЬ/РАЗБАНИТЬ", callback_data="admin_ban"))
    builder.row(InlineKeyboardButton(text="🎟 ПРОМОКОДЫ", callback_data="admin_promocodes"))
    builder.row(InlineKeyboardButton(text="📊 СТАТИСТИКА", callback_data="admin_stats"))
    builder.row(InlineKeyboardButton(text="📢 РАССЫЛКА", callback_data="admin_broadcast"))
    builder.row(InlineKeyboardButton(text="◀️ НАЗАД", callback_data="main_menu"))
    return builder.as_markup()

# ------------------ FSM ------------------
class AdminStates(StatesGroup):
    waiting_for_video_name = State()
    waiting_for_video_file = State()
    waiting_for_video_is_vip = State()
    waiting_for_diamonds_user = State()
    waiting_for_diamonds_amount = State()
    waiting_for_premium_user = State()
    waiting_for_premium_days = State()
    waiting_for_ban_user = State()
    waiting_for_promo_code = State()
    waiting_for_promo_reward = State()
    waiting_for_promo_uses = State()
    waiting_for_broadcast = State()

class PaymentState(StatesGroup):
    waiting_for_screenshot = State()

# ------------------ БОТ ------------------
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or "radion"
    if not get_user(user_id):
        create_user(user_id, username)
    user = get_user(user_id)
    if user['banned']:
        await message.answer("❌ ВЫ ЗАБЛОКИРОВАНЫ В БОТЕ.")
        return
    await message.answer("🔞 ВНИМАНИЕ! ТОВАР 18+ 🔞\n\nДобро пожаловать в магазин!", reply_markup=get_main_keyboard(user_id))

@dp.callback_query(F.data == "main_menu")
async def main_menu_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = get_user(user_id)
    if user['banned']:
        await callback.answer("❌ ВЫ ЗАБЛОКИРОВАНЫ", show_alert=True)
        return
    await callback.message.edit_text("🔞 ВНИМАНИЕ! ТОВАР 18+ 🔞", reply_markup=get_main_keyboard(user_id))
    await callback.answer()

@dp.callback_query(F.data == "watch_video")
async def watch_video(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = get_user(user_id)
    if user['banned']:
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    video = get_random_video()
    if not video:
        await callback.answer("Видео пока нет в базе.", show_alert=True)
        return
    # Проверка VIP? У нас все видео из обычного списка, но если вдруг попалось VIP – проверим
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT is_vip FROM videos WHERE id = ?", (video['id'],))
    is_vip = c.fetchone()[0]
    conn.close()
    if is_vip == 1:
        premium_active = user['is_premium'] and user['premium_until'] > int(datetime.now().timestamp())
        if not premium_active:
            await callback.answer("❌ Это VIP-видео. Оформите PREMIUM подписку!", show_alert=True)
            return
    if user['balance'] < VIDEO_PRICE:
        await callback.answer(f"❌ Недостаточно алмазов! Нужно {VIDEO_PRICE}💎", show_alert=True)
        return
    # Списываем
    update_balance(user_id, -VIDEO_PRICE)
    add_purchase(user_id, video['id'])
    await callback.message.answer_video(video['file_id'], caption=f"🎬 {video['name']}\n\nСписано {VIDEO_PRICE}💎")
    await callback.answer("✅ ВИДЕО ЗАГРУЖАЕТСЯ...")
    await main_menu_callback(callback)

@dp.callback_query(F.data == "buy_diamonds_menu")
async def buy_diamonds_menu(callback: CallbackQuery):
    user_id = callback.from_user.id
    if get_user(user_id)['banned']:
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await callback.message.edit_text("💎 ВЫБЕРИТЕ КОЛИЧЕСТВО АЛМАЗОВ:", reply_markup=get_diamond_packs_keyboard())
    await callback.answer()

@dp.callback_query(F.data.startswith("buy_diamonds_"))
async def buy_diamonds_pack(callback: CallbackQuery, state: FSMContext):
    diamonds = int(callback.data.split("_")[2])
    price = DIAMOND_PACKS[diamonds]
    user_id = callback.from_user.id
    user = get_user(user_id)
    payment_id = user['payment_id']
    # Сохраняем заявку
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO pending_payments (user_id, payment_id, amount_rub, type, diamonds, timestamp, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (user_id, payment_id, price, 'diamonds', diamonds, int(datetime.now().timestamp()), 'pending'))
    conn.commit()
    conn.close()
    text = (f"🔞 ВНИМАНИЕ! ТОВАР 18+ 🔞\n\n💎 АЛМАЗЫ: {diamonds} шт\n💰 ЦЕНА: {price} ₽\n\n"
            f"💳 РЕКВИЗИТЫ ДЛЯ ОПЛАТЫ:\n{PAYMENT_DETAILS['card']} | {PAYMENT_DETAILS['name']}\n\n"
            f"🆔 ВАШ ID ДЛЯ ОПЛАТЫ: {payment_id}\n📌 ВСТАВЬТЕ ЭТОТ ID В КОММЕНТАРИЙ К ПЕРЕВОДУ!\n\n"
            f"📌 ПОСЛЕ ОПЛАТЫ НАЖМИТЕ «✅ Я ОПЛАТИЛ» И ОТПРАВЬТЕ ЧЕК")
    await callback.message.edit_text(text, reply_markup=get_payment_keyboard(payment_id))
    await callback.answer()

@dp.callback_query(F.data == "premium_menu")
async def premium_menu(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    user = get_user(user_id)
    if user['banned']:
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    payment_id = user['payment_id']
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO pending_payments (user_id, payment_id, amount_rub, type, diamonds, timestamp, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (user_id, payment_id, PREMIUM_PRICE_RUB, 'premium', 0, int(datetime.now().timestamp()), 'pending'))
    conn.commit()
    conn.close()
    text = (f"⭐ PREMIUM ПОДПИСКА (30 ДНЕЙ)\n\n▫️ ДОСТУП К VIP-КОНТЕНТУ В БОТЕ\n▫️ 5 VIP ВИДЕО КАЖДЫЙ ДЕНЬ\n"
            f"▫️ ЕЖЕДНЕВНЫЙ БОНУС {DAILY_BONUS_PREMIUM}💎\n▫️ ПРИОРИТЕТНАЯ ПОДДЕРЖКА\n\n"
            f"💰 ЦЕНА: {PREMIUM_PRICE_RUB} ₽\n\n💳 РЕКВИЗИТЫ ДЛЯ ОПЛАТЫ:\n{PAYMENT_DETAILS['card']} | {PAYMENT_DETAILS['name']}\n\n"
            f"🆔 ВАШ ID ДЛЯ ОПЛАТЫ: {payment_id}\n📌 ВСТАВЬТЕ ЭТОТ ID В КОММЕНТАРИЙ К ПЕРЕВОДУ!\n\n"
            f"📌 ПОСЛЕ ОПЛАТЫ НАЖМИТЕ «✅ Я ОПЛАТИЛ» И ОТПРАВЬТЕ ЧЕК")
    await callback.message.edit_text(text, reply_markup=get_payment_keyboard(payment_id))
    await callback.answer()

@dp.callback_query(F.data == "i_paid")
async def i_paid(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PaymentState.waiting_for_screenshot)
    # Сохраним user_id, чтобы знать, к какой заявке привязать скрин
    await state.update_data(user_id=callback.from_user.id)
    await callback.message.edit_text("📸 Отправьте скриншот чека (фото или файл). После проверки администратор начислит алмазы или премиум.")
    await callback.answer()

@dp.message(PaymentState.waiting_for_screenshot, F.photo | F.document)
async def receive_screenshot(message: Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    # Находим последнюю активную заявку пользователя
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT payment_id, amount_rub, type, diamonds FROM pending_payments WHERE user_id = ? AND status = 'pending' ORDER BY timestamp DESC LIMIT 1", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        await message.answer("❌ Не найдена активная заявка на оплату. Начните сначала через меню покупки.")
        await state.clear()
        return
    payment_id, amount, pay_type, diamonds = row
    # Пересылаем админу
    caption = f"📨 Новая заявка на оплату!\n👤 Пользователь: {user_id} (@{message.from_user.username})\n💰 Сумма: {amount}₽\n📦 Товар: {pay_type}\n🆔 Payment ID: {payment_id}"
    if pay_type == 'diamonds':
        caption += f"\n💎 Алмазов: {diamonds}"
    else:
        caption += f"\n⭐ Премиум подписка"
    admin_ids = ADMIN_IDS
    for admin_id in admin_ids:
        if message.photo:
            await bot.send_photo(admin_id, message.photo[-1].file_id, caption=caption, reply_markup=get_admin_payment_keyboard(user_id, payment_id, amount, pay_type))
        elif message.document:
            await bot.send_document(admin_id, message.document.file_id, caption=caption, reply_markup=get_admin_payment_keyboard(user_id, payment_id, amount, pay_type))
    await message.answer("✅ Чек отправлен администратору. Ожидайте подтверждения.")
    await state.clear()

# ------------------ ОБРАБОТКА ДЕЙСТВИЙ АДМИНА ------------------
@dp.callback_query(F.data.startswith("admin_ban_user_"))
async def admin_ban_user(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_id = int(callback.data.split("_")[3])
    set_ban(user_id, True)
    await callback.message.edit_text(f"✅ Пользователь {user_id} забанен.")
    await callback.answer()
    # Уведомим пользователя, если возможно
    try:
        await bot.send_message(user_id, "❌ Вы были забанены администратором за нарушение правил оплаты.")
    except:
        pass

@dp.callback_query(F.data.startswith("admin_approve_"))
async def admin_approve(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    parts = callback.data.split("_")
    # формат: admin_approve_{payment_id}_{user_id}_{type}
    payment_id = parts[2]  # может содержать подчеркивания? payment_id имеет формат #Radion_цифры, но подчеркивание есть
    # нужно правильно извлечь: admin_approve_#Radion_12345_123456789_diamonds
    # лучше пересобрать: начиная с 2 индекса до предпоследнего?
    # упростим: разобьём с учетом, что payment_id начинается с #Radion_
    # используем срез: найти все части после 'admin_approve_'
    rest = callback.data[len("admin_approve_"):]
    # rest = "#Radion_12345_456789_diamonds"
    # разделим по '_' но с учетом, что в payment_id есть '_'
    parts2 = rest.split('_')
    # payment_id = parts2[0] + '_' + parts2[1]   например #Radion_12345
    payment_id = parts2[0] + '_' + parts2[1]
    user_id = int(parts2[2])
    pay_type = parts2[3]
    # Получаем данные из pending_payments
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT diamonds, amount_rub FROM pending_payments WHERE payment_id = ? AND user_id = ? AND status = 'pending'", (payment_id, user_id))
    row = c.fetchone()
    if not row:
        await callback.answer("Заявка уже обработана или не найдена.", show_alert=True)
        return
    diamonds, amount = row
    if pay_type == 'diamonds':
        update_balance(user_id, diamonds)
        await bot.send_message(user_id, f"✅ Ваша оплата на {amount}₽ подтверждена! Вам начислено {diamonds}💎.")
    else:  # premium
        set_premium(user_id, 30)
        await bot.send_message(user_id, f"✅ Ваша оплата на {amount}₽ подтверждена! PREMIUM подписка активирована на 30 дней.")
    # Обновляем статус
    c.execute("UPDATE pending_payments SET status = 'approved' WHERE payment_id = ? AND user_id = ?", (payment_id, user_id))
    conn.commit()
    conn.close()
    await callback.message.edit_text(f"✅ Выдано пользователю {user_id}.")
    await callback.answer()

@dp.callback_query(F.data.startswith("admin_reject_"))
async def admin_reject(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    rest = callback.data[len("admin_reject_"):]
    parts2 = rest.split('_')
    payment_id = parts2[0] + '_' + parts2[1]
    user_id = int(parts2[2])
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE pending_payments SET status = 'rejected' WHERE payment_id = ? AND user_id = ?", (payment_id, user_id))
    conn.commit()
    conn.close()
    await bot.send_message(user_id, "❌ Ваша оплата отклонена администратором. Проверьте правильность заполнения ID или свяжитесь с поддержкой.")
    await callback.message.edit_text(f"❌ Отказано пользователю {user_id}.")
    await callback.answer()

@dp.callback_query(F.data == "profile")
async def profile_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = get_user(user_id)
    premium_active = user['is_premium'] and user['premium_until'] > int(datetime.now().timestamp())
    premium_text = "✅ ДА" if premium_active else "❌ НЕТ"
    if premium_active:
        until = datetime.fromtimestamp(user['premium_until']).strftime("%d.%m.%Y")
        premium_text += f" (до {until})"
    text = f"👤 ПРОФИЛЬ\n\n🆔 ID: {user_id}\n💎 АЛМАЗЫ: {user['balance']}\n⭐ PREMIUM: {premium_text}\n🔗 ВАШ ID ДЛЯ ОПЛАТЫ: {user['payment_id']}"
    await callback.message.edit_text(text, reply_markup=get_main_keyboard(user_id))
    await callback.answer()

@dp.callback_query(F.data == "daily_bonus")
async def daily_bonus_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = get_user(user_id)
    last = user['daily_bonus_last']
    now = int(datetime.now().timestamp())
    if now - last < 86400:
        wait_seconds = 86400 - (now - last)
        h = wait_seconds // 3600
        m = (wait_seconds % 3600) // 60
        await callback.answer(f"Бонус уже получен. Следующий через {h}ч {m}мин.", show_alert=True)
        return
    premium_active = user['is_premium'] and user['premium_until'] > now
    bonus = DAILY_BONUS_PREMIUM if premium_active else DAILY_BONUS_NORMAL
    update_balance(user_id, bonus)
    set_daily_bonus_last(user_id, now)
    await callback.message.edit_text(f"🎁 ЕЖЕДНЕВНЫЙ БОНУС: +{bonus}💎", reply_markup=get_main_keyboard(user_id))
    await callback.answer(f"Получено {bonus}💎!")

@dp.callback_query(F.data == "promocode")
async def promocode_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_promo_code)  # используем состояние для ввода кода
    await callback.message.edit_text("Введите промокод:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ НАЗАД", callback_data="main_menu")]]))
    await callback.answer()

@dp.message(AdminStates.waiting_for_promo_code)
async def promocode_apply(message: Message, state: FSMContext):
    user_id = message.from_user.id
    code = message.text.strip().upper()
    success, reward = apply_promocode(user_id, code)
    if success:
        await message.answer(f"✅ Промокод активирован! +{reward}💎")
    else:
        await message.answer("❌ Неверный или уже использованный промокод.")
    await state.clear()
    await message.answer("Главное меню:", reply_markup=get_main_keyboard(user_id))

@dp.callback_query(F.data == "support")
async def support_callback(callback: CallbackQuery):
    await callback.message.edit_text("📞 Связь с администратором: @scam_lil", reply_markup=get_main_keyboard(callback.from_user.id))
    await callback.answer()

# ------------------ АДМИН ПАНЕЛЬ ------------------
@dp.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text("⚙️ АДМИН ПАНЕЛЬ:", reply_markup=get_admin_keyboard())
    await callback.answer()

# Добавление видео
@dp.callback_query(F.data == "admin_add_video")
async def admin_add_video_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_for_video_name)
    await callback.message.edit_text("Введите название видео:")
    await callback.answer()

@dp.message(AdminStates.waiting_for_video_name)
async def admin_get_video_name(message: Message, state: FSMContext):
    await state.update_data(video_name=message.text)
    await state.set_state(AdminStates.waiting_for_video_file)
    await message.answer("Отправьте видео файлом:")

@dp.message(AdminStates.waiting_for_video_file, F.video)
async def admin_get_video_file(message: Message, state: FSMContext):
    file_id = message.video.file_id
    await state.update_data(file_id=file_id)
    await state.set_state(AdminStates.waiting_for_video_is_vip)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="ДА", callback_data="vip_yes"), InlineKeyboardButton(text="НЕТ", callback_data="vip_no")]])
    await message.answer("Это VIP-видео?", reply_markup=keyboard)

@dp.callback_query(StateFilter(AdminStates.waiting_for_video_is_vip))
async def admin_get_vip_flag(callback: CallbackQuery, state: FSMContext):
    is_vip = 1 if callback.data == "vip_yes" else 0
    data = await state.get_data()
    add_video(data["video_name"], data["file_id"], is_vip)
    await state.clear()
    await callback.message.edit_text("✅ Видео добавлено!")
    await admin_panel(callback)

# Удаление видео
@dp.callback_query(F.data == "admin_del_video")
async def admin_del_video_start(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    videos = get_all_videos()
    if not videos:
        await callback.message.edit_text("Нет видео для удаления", reply_markup=get_admin_keyboard())
        return
    builder = InlineKeyboardBuilder()
    for v in videos:
        builder.row(InlineKeyboardButton(text=v['name'], callback_data=f"del_video_{v['id']}"))
    builder.row(InlineKeyboardButton(text="◀️ НАЗАД", callback_data="admin_panel"))
    await callback.message.edit_text("Выберите видео для удаления:", reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data.startswith("del_video_"))
async def admin_del_video_confirm(callback: CallbackQuery):
    video_id = int(callback.data.split("_")[2])
    remove_video(video_id)
    await callback.message.edit_text("🗑 Видео удалено")
    await admin_panel(callback)

# Выдача алмазов
@dp.callback_query(F.data == "admin_give_diamonds")
async def admin_give_diamonds_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_for_diamonds_user)
    await callback.message.edit_text("Введите ID пользователя:")
    await callback.answer()

@dp.message(AdminStates.waiting_for_diamonds_user)
async def admin_give_diamonds_user(message: Message, state: FSMContext):
    try:
        user_id = int(message.text)
    except:
        await message.answer("Ошибка: введите число (ID)")
        return
    if not get_user(user_id):
        await message.answer("Пользователь не найден")
        return
    await state.update_data(target_user=user_id)
    await state.set_state(AdminStates.waiting_for_diamonds_amount)
    await message.answer("Введите количество алмазов:")

@dp.message(AdminStates.waiting_for_diamonds_amount)
async def admin_give_diamonds_amount(message: Message, state: FSMContext):
    try:
        amount = int(message.text)
    except:
        await message.answer("Введите число")
        return
    data = await state.get_data()
    user_id = data['target_user']
    update_balance(user_id, amount)
    await state.clear()
    await message.answer(f"✅ Пользователю {user_id} выдано {amount}💎")
    await admin_panel(message)

# Выдача премиума
@dp.callback_query(F.data == "admin_give_premium")
async def admin_give_premium_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_for_premium_user)
    await callback.message.edit_text("Введите ID пользователя:")
    await callback.answer()

@dp.message(AdminStates.waiting_for_premium_user)
async def admin_give_premium_user(message: Message, state: FSMContext):
    try:
        user_id = int(message.text)
    except:
        await message.answer("Ошибка: введите число")
        return
    if not get_user(user_id):
        await message.answer("Пользователь не найден")
        return
    await state.update_data(target_user=user_id)
    await state.set_state(AdminStates.waiting_for_premium_days)
    await message.answer("Введите количество дней премиума:")

@dp.message(AdminStates.waiting_for_premium_days)
async def admin_give_premium_days(message: Message, state: FSMContext):
    try:
        days = int(message.text)
    except:
        await message.answer("Введите число")
        return
    data = await state.get_data()
    user_id = data['target_user']
    set_premium(user_id, days)
    await state.clear()
    await message.answer(f"✅ Пользователю {user_id} выдан PREMIUM на {days} дней")
    await admin_panel(message)

# Бан/разбан
@dp.callback_query(F.data == "admin_ban")
async def admin_ban_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_for_ban_user)
    await callback.message.edit_text("Введите ID пользователя для бана/разбана:")
    await callback.answer()

@dp.message(AdminStates.waiting_for_ban_user)
async def admin_ban_user_general(message: Message, state: FSMContext):
    try:
        user_id = int(message.text)
    except:
        await message.answer("Ошибка: введите число")
        return
    user = get_user(user_id)
    if not user:
        await message.answer("Пользователь не найден")
        return
    new_ban = not user['banned']
    set_ban(user_id, new_ban)
    status = "забанен" if new_ban else "разбанен"
    await message.answer(f"✅ Пользователь {user_id} {status}")
    await state.clear()
    await admin_panel(message)

# Промокоды админ
@dp.callback_query(F.data == "admin_promocodes")
async def admin_promocodes_menu(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    promos = get_all_promocodes()
    text = "🎟 СПИСОК ПРОМОКОДОВ:\n\n"
    if not promos:
        text += "Нет промокодов"
    else:
        for code, reward, max_uses, used in promos:
            text += f"`{code}` → +{reward}💎, лимит {max_uses}, активаций {used}\n"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ ДОБАВИТЬ", callback_data="admin_add_promo")],
        [InlineKeyboardButton(text="❌ УДАЛИТЬ", callback_data="admin_remove_promo")],
        [InlineKeyboardButton(text="◀️ НАЗАД", callback_data="admin_panel")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "admin_add_promo")
async def admin_add_promo_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_for_promo_code)
    await callback.message.edit_text("Введите код промокода (латиница/цифры):")
    await callback.answer()

@dp.message(AdminStates.waiting_for_promo_code)
async def admin_add_promo_code(message: Message, state: FSMContext):
    await state.update_data(promo_code=message.text.strip().upper())
    await state.set_state(AdminStates.waiting_for_promo_reward)
    await message.answer("Введите награду (алмазы):")

@dp.message(AdminStates.waiting_for_promo_reward)
async def admin_add_promo_reward(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Введите число")
        return
    await state.update_data(promo_reward=int(message.text))
    await state.set_state(AdminStates.waiting_for_promo_uses)
    await message.answer("Введите лимит активаций (0 = безлимит):")

@dp.message(AdminStates.waiting_for_promo_uses)
async def admin_add_promo_uses(message: Message, state: FSMContext):
    max_uses = int(message.text) if message.text.isdigit() else 0
    data = await state.get_data()
    add_promocode(data["promo_code"], data["promo_reward"], max_uses)
    await state.clear()
    await message.answer("✅ Промокод добавлен")
    await admin_promocodes_menu(message)

@dp.callback_query(F.data == "admin_remove_promo")
async def admin_remove_promo_start(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    promos = get_all_promocodes()
    if not promos:
        await callback.answer("Нет промокодов", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    for code, _, _, _ in promos:
        builder.row(InlineKeyboardButton(text=code, callback_data=f"rm_promo_{code}"))
    builder.row(InlineKeyboardButton(text="◀️ НАЗАД", callback_data="admin_promocodes"))
    await callback.message.edit_text("Выберите промокод для удаления:", reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data.startswith("rm_promo_"))
async def admin_remove_promo(callback: CallbackQuery):
    code = callback.data.split("_", 2)[2]
    delete_promocode(code)
    await callback.message.edit_text(f"Промокод {code} удалён")
    await admin_promocodes_menu(callback)

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    users, total_balance, videos, purchases, premium_users = get_stats()
    text = f"📊 СТАТИСТИКА БОТА\n\n👥 Пользователей: {users}\n💎 Алмазов в обороте: {total_balance}\n🎬 Видео в базе: {videos}\n🛒 Покупок: {purchases}\n⭐ Активных премиум: {premium_users}"
    await callback.message.edit_text(text, reply_markup=get_admin_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_for_broadcast)
    await callback.message.edit_text("Введите текст рассылки (можно с фото/видео):")
    await callback.answer()

@dp.message(StateFilter(AdminStates.waiting_for_broadcast))
async def admin_broadcast_send(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE banned = 0")
    users = c.fetchall()
    conn.close()
    success = 0
    for (user_id,) in users:
        try:
            if message.text:
                await bot.send_message(user_id, message.text)
            elif message.photo:
                await bot.send_photo(user_id, message.photo[-1].file_id, caption=message.caption)
            elif message.video:
                await bot.send_video(user_id, message.video.file_id, caption=message.caption)
            success += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await state.clear()
    await message.answer(f"📢 Рассылка завершена. Отправлено {success} пользователям.")
    await admin_panel(message)

# ------------------ ЗАПУСК ------------------
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main()
