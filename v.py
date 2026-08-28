import asyncio
import hashlib
import html
import json
import logging
import os
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "30"))
DB_NAME = os.getenv("DB_NAME", "tracker.db")
WAIT_SOURCE = 1

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class GameInfo:
    title: str
    description: str = "Нет описания."
    version: str = "Не указана"
    developer: str = "Не указан"
    publisher: str = "Не указан"
    release_date: str = "Не указана"
    genres: str = "Не указаны"
    platforms: str = "Не указаны"
    requirements: str = "Не указаны"
    source_url: str = ""
    image_url: str = ""


def now() -> str:
    return datetime.now().strftime("%d.%m.%Y %H:%M")


def conn() -> sqlite3.Connection:
    return sqlite3.connect(DB_NAME)


def init_db() -> None:
    with conn() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL,
            url TEXT NOT NULL, source_type TEXT NOT NULL, last_hash TEXT,
            last_check TEXT, metadata_json TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        names = {x[1] for x in db.execute("PRAGMA table_info(subscriptions)")}
        if "metadata_json" not in names:
            db.execute("ALTER TABLE subscriptions ADD COLUMN metadata_json TEXT")
        db.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            chat_id INTEGER NOT NULL,
            registered_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")


def subscriptions(user_id: Optional[int] = None) -> list[tuple]:
    query = "SELECT id,user_id,name,url,source_type,last_hash,last_check,metadata_json FROM subscriptions"
    args: tuple = () if user_id is None else (user_id,)
    if user_id is not None:
        query += " WHERE user_id=?"
    with conn() as db:
        return db.execute(query + " ORDER BY id DESC", args).fetchall()


def get_subscription(sub_id: int) -> Optional[tuple]:
    with conn() as db:
        return db.execute("SELECT id,user_id,name,url,source_type,last_hash,last_check,metadata_json FROM subscriptions WHERE id=?", (sub_id,)).fetchone()


def save_new(user_id: int, kind: str, source: str, info: GameInfo, content: str) -> int:
    with conn() as db:
        cursor = db.execute("INSERT INTO subscriptions (user_id,name,url,source_type,last_hash,last_check,metadata_json) VALUES (?,?,?,?,?,?,?)", (user_id, info.title, source, kind, digest(content), now(), json.dumps(asdict(info))))
        return cursor.lastrowid


def update_sub(sub_id: int, info: GameInfo, content: str) -> None:
    with conn() as db:
        db.execute("UPDATE subscriptions SET name=?,last_hash=?,last_check=?,metadata_json=? WHERE id=?", (info.title, digest(content), now(), json.dumps(asdict(info)), sub_id))


def delete_sub(sub_id: int) -> bool:
    with conn() as db:
        return db.execute("DELETE FROM subscriptions WHERE id=?", (sub_id,)).rowcount > 0


def register_user(user_id: int, chat_id: int) -> None:
    with conn() as db:
        db.execute(
            "INSERT INTO users (user_id, chat_id) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET chat_id=excluded.chat_id",
            (user_id, chat_id),
        )


def user_chats() -> list[int]:
    with conn() as db:
        return [row[0] for row in db.execute("SELECT chat_id FROM users")]


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def styled_button(text: str, *, style: str, callback_data: Optional[str] = None, url: Optional[str] = None) -> InlineKeyboardButton:
    """Создаёт кнопку с цветом Bot API 9.4+ через совместимый api_kwargs."""
    kwargs = {"callback_data": callback_data, "url": url}
    return InlineKeyboardButton(text, api_kwargs={"style": style}, **kwargs)


def menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [styled_button("Добавить игру", style="success", callback_data="add")],
        [InlineKeyboardButton("Мои подписки", callback_data="list"), InlineKeyboardButton("Проверить", callback_data="check")],
        [InlineKeyboardButton("Справка", callback_data="help")],
    ])


def sources_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Steam", callback_data="source:steam"), InlineKeyboardButton("itch.io", callback_data="source:itch")],
        [InlineKeyboardButton("Epic Games", callback_data="source:epic"), InlineKeyboardButton("RSS", callback_data="source:rss")],
        [InlineKeyboardButton("Веб-страница", callback_data="source:web")],
        [InlineKeyboardButton("Назад", callback_data="menu")],
    ])


async def get_url(session: aiohttp.ClientSession, url: str) -> Optional[str]:
    try:
        async with session.get(url, headers={"User-Agent": "GameUpdateTracker/1.0"}, timeout=aiohttp.ClientTimeout(total=20)) as response:
            return await response.text() if response.status == 200 else None
    except aiohttp.ClientError:
        return None


def clean(value: str, length: int = 800) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return value if len(value) <= length else value[:length - 3] + "..."


def normalized_version(value: str) -> Optional[str]:
    """Возвращает только реально определённую версию для сравнения."""
    value = clean(value, 100)
    if not value or value.casefold() in {"не указана", "unknown", "n/a", "none", "-"}:
        return None
    return value.casefold()


async def steam(session: aiohttp.ClientSession, app_id: str) -> tuple[GameInfo, str]:
    if not app_id.isdigit():
        raise ValueError("Steam AppID должен состоять из цифр.")
    raw = await get_url(session, f"https://store.steampowered.com/api/appdetails?appids={app_id}&l=russian")
    game = json.loads(raw or "{}").get(app_id, {})
    if not game.get("success"):
        raise ValueError("Игра с таким AppID не найдена в Steam.")
    data = game["data"]
    minimum = BeautifulSoup(data.get("pc_requirements", {}).get("minimum", ""), "html.parser").get_text(" ", strip=True)
    info = GameInfo(
        title=data.get("name", f"Steam {app_id}"),
        description=BeautifulSoup(data.get("short_description", ""), "html.parser").get_text(" ", strip=True) or "Нет описания.",
        developer=", ".join(data.get("developers", [])) or "Не указан",
        publisher=", ".join(data.get("publishers", [])) or "Не указан",
        release_date=data.get("release_date", {}).get("date", "Не указана"),
        genres=", ".join(x["description"] for x in data.get("genres", [])) or "Не указаны",
        platforms=", ".join(x for x, enabled in data.get("platforms", {}).items() if enabled) or "Не указаны",
        requirements=minimum or "Не указаны",
        source_url=f"https://store.steampowered.com/app/{app_id}",
        image_url=data.get("header_image", ""),
    )
    news = await get_url(session, f"https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/?appid={app_id}&count=3&maxlength=1000")
    return info, raw + (news or "")


async def webpage(session: aiohttp.ClientSession, url: str, kind: str) -> tuple[GameInfo, str]:
    if not re.match(r"^https?://", url):
        raise ValueError("Ссылка должна начинаться с http:// или https://.")
    raw = await get_url(session, f"{url.rstrip('/')}/devlog.rss" if kind == "itch" else url)
    if not raw and kind == "itch":
        raw = await get_url(session, url)
    if not raw:
        raise ValueError("Источник недоступен.")
    soup = BeautifulSoup(raw, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else url
    meta = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
    image_meta = soup.find("meta", attrs={"property": "og:image"})
    description = meta.get("content", "") if meta else soup.get_text(" ", strip=True)
    version = re.search(r"(?:version|версия)\s*[:v]?\s*([\w.\-]+)", soup.get_text(" ", strip=True), re.I)
    return GameInfo(title=clean(title, 120), description=clean(description), version=version.group(1) if version else "Не указана", source_url=url, image_url=image_meta.get("content", "") if image_meta else ""), raw


async def info_for(session: aiohttp.ClientSession, kind: str, source: str) -> tuple[GameInfo, str]:
    if kind == "steam":
        return await steam(session, source)
    if kind == "epic" and not source.startswith("http"):
        source = f"https://store.epicgames.com/ru/p/{source}"
    return await webpage(session, source, kind)


def row_info(row: tuple) -> GameInfo:
    try:
        return GameInfo(**json.loads(row[7] or "{}"))
    except (json.JSONDecodeError, TypeError):
        return GameInfo(title=row[2], source_url=row[3])


def card(info: GameInfo, status: str, checked: str) -> str:
    fields = [("Статус", status), ("Проверено", checked), ("Версия", info.version), ("Разработчик", info.developer), ("Издатель", info.publisher), ("Дата выхода", info.release_date), ("Жанры", info.genres), ("Платформы", info.platforms), ("Характеристики", info.requirements)]
    body = "\n".join(f"<b>{name}</b>: {html.escape(clean(value, 650))}" for name, value in fields)
    return f"<b>{html.escape(clean(info.title, 120))}</b>\n<blockquote>{html.escape(clean(info.description, 700))}</blockquote>\n{body}\n\n<a href=\"{html.escape(info.source_url, quote=True)}\">Открыть источник</a>"


def card_keyboard(info: GameInfo, sub_id: Optional[int] = None, can_delete: bool = False) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton("Открыть страницу игры", url=info.source_url)]]
    if sub_id is not None and can_delete:
        buttons.append([styled_button("Удалить", style="danger", callback_data=f"delete:{sub_id}")])
    buttons.append([InlineKeyboardButton("К общему списку", callback_data="list")])
    return InlineKeyboardMarkup(buttons)


async def send_card(message, info: GameInfo, status: str, checked: str, keyboard: InlineKeyboardMarkup) -> None:
    text = card(info, status, checked)
    if info.image_url:
        try:
            await message.reply_photo(info.image_url, caption=html.escape(clean(info.title, 200)), parse_mode="HTML")
        except Exception as error:
            logger.warning("Could not send card image: %s", error)
    await message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=keyboard)


async def send_notification(bot, chat_id: int, info: GameInfo, status: str) -> None:
    text = card(info, status, now())
    keyboard = card_keyboard(info)
    if info.image_url:
        try:
            await bot.send_photo(chat_id, info.image_url, caption=html.escape(clean(info.title, 200)), parse_mode="HTML")
        except Exception as error:
            logger.warning("Could not send notification image: %s", error)
    await bot.send_message(chat_id, text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=keyboard)


def updates_summary(changes: list[tuple[int, str, str, str, str]]) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"Обновления игр за {now()}", ""]
    buttons = []
    for sub_id, title, previous_version, current_version, checked in changes:
        lines.append(
            f"<b>{html.escape(clean(title, 90))}</b>\n"
            f"Версия: {html.escape(previous_version)} → {html.escape(current_version)}\n"
            f"Дата: {html.escape(checked)}"
        )
        buttons.append([InlineKeyboardButton(clean(title, 45), callback_data=f"view:{sub_id}")])
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    register_user(update.effective_user.id, update.effective_chat.id)
    await update.message.reply_text("Панель отслеживания обновлений", reply_markup=menu())


async def show_list(query, user_id: int) -> None:
    rows = subscriptions()
    if not rows:
        await query.edit_message_text("Подписок пока нет.", reply_markup=menu())
        return
    buttons = [[InlineKeyboardButton(clean(row[2], 45), callback_data=f"view:{row[0]}")] for row in rows]
    buttons.append([InlineKeyboardButton("Главное меню", callback_data="menu")])
    await query.edit_message_text("Общий каталог игр и источников.", reply_markup=InlineKeyboardMarkup(buttons))


async def show_card(query, sub_id: int, user_id: int) -> None:
    row = get_subscription(sub_id)
    if not row:
        await query.edit_message_text("Подписка не найдена.", reply_markup=menu())
        return
    info = row_info(row)
    keys = card_keyboard(info, sub_id, can_delete=True)
    await query.edit_message_text("Карточка игры отправлена отдельным сообщением.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("К общему списку", callback_data="list")]]))
    await send_card(query.message, info, "Отслеживается", row[6] or "Ещё не проверялось", keys)


async def inspect(row: tuple, bot, notify: bool) -> tuple[bool, str, str]:
    sub_id, user_id, _, source, kind, old_hash, _, _ = row
    previous_info = row_info(row)
    async with aiohttp.ClientSession() as session:
        info, content = await info_for(session, kind, source)
    previous_version = normalized_version(previous_info.version)
    current_version = normalized_version(info.version)
    changed = bool(previous_version and current_version and previous_version != current_version)
    update_sub(sub_id, info, content)
    return changed, previous_info.version, info.version


async def check_user(user_id: int, bot) -> int:
    changed_rows = await check_all(bot)
    if changed_rows:
        text, keyboard = updates_summary(changed_rows)
        await broadcast_summary(bot, text, keyboard)
    return len(changed_rows)


check_lock = asyncio.Lock()


async def check_all(bot) -> list[tuple[int, str, str, str, str]]:
    """Один общий проход: каждую игру проверяем один раз, затем рассылаем итог."""
    if check_lock.locked():
        logger.info("Check skipped: another check is running")
        return []
    changed_rows = []
    async with check_lock:
        for row in subscriptions():
            try:
                changed, previous_version, current_version = await inspect(row, bot, False)
                if changed:
                    changed_rows.append((row[0], row[2], previous_version, current_version, now()))
            except Exception as error:
                logger.warning("Check failed for %s: %s", row[0], error)
            await asyncio.sleep(0.3)
    return changed_rows


async def broadcast_summary(bot, text: str, keyboard: InlineKeyboardMarkup) -> None:
    """Рассылает одну сводку всем пользователям общего каталога."""
    for chat_id in user_chats():
        try:
            await bot.send_message(
                chat_id,
                text,
                parse_mode="HTML",
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
        except Exception as error:
            logger.warning("Could not send update summary to %s: %s", chat_id, error)
        await asyncio.sleep(0.2)


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    register_user(update.effective_user.id, update.effective_chat.id)
    data = query.data
    if data == "menu":
        await query.edit_message_text("Панель отслеживания обновлений", reply_markup=menu())
    elif data == "add":
        await query.edit_message_text("Выберите источник.", reply_markup=sources_menu())
    elif data.startswith("source:"):
        kind = data.split(":", 1)[1]
        context.user_data["kind"] = kind
        prompts = {"steam": "Введите Steam AppID, например 730.", "epic": "Введите Epic slug или ссылку на игру.", "itch": "Введите ссылку на игру itch.io.", "rss": "Введите ссылку на RSS/Atom-ленту.", "web": "Введите ссылку на страницу."}
        await query.edit_message_text(prompts[kind], reply_markup=InlineKeyboardMarkup([[styled_button("Отмена", style="danger", callback_data="menu")]]))
        return WAIT_SOURCE
    elif data == "list":
        await show_list(query, update.effective_user.id)
    elif data == "check":
        await query.edit_message_text("Выполняется проверка.")
        changed = await check_user(update.effective_user.id, context.bot)
        await query.edit_message_text(f"Проверка завершена. Обновлений: {changed}.", reply_markup=menu())
    elif data.startswith("view:"):
        await show_card(query, int(data.split(":", 1)[1]), update.effective_user.id)
    elif data.startswith("delete:"):
        sub_id = int(data.split(":", 1)[1])
        row = get_subscription(sub_id)
        if not row:
            await query.answer("Игра уже удалена.", show_alert=True)
            return ConversationHandler.END
        delete_sub(sub_id)
        await show_list(query, update.effective_user.id)
    elif data == "help":
        await query.edit_message_text("Добавьте игру через меню. Steam использует AppID из ссылки магазина. Для остальных источников вставьте ссылку. Бот показывает последнюю полученную карточку и уведомляет при изменении содержимого.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="menu")]]))
    return ConversationHandler.END


async def receive_source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    register_user(update.effective_user.id, update.effective_chat.id)
    kind = context.user_data.pop("kind", None)
    if not kind:
        await update.message.reply_text("Добавление отменено.", reply_markup=menu())
        return ConversationHandler.END
    loading = await update.message.reply_text("Получаю информацию.")
    try:
        async with aiohttp.ClientSession() as session:
            info, content = await info_for(session, kind, update.message.text.strip())
        sub_id = save_new(update.effective_user.id, kind, update.message.text.strip(), info, content)
        await loading.delete()
        await send_card(update.message, info, "Добавлено в общий каталог", now(), card_keyboard(info, sub_id, can_delete=True))
    except (ValueError, json.JSONDecodeError) as error:
        await loading.edit_text(f"Не удалось добавить источник: {html.escape(str(error))}", reply_markup=menu())
    return ConversationHandler.END


async def scheduled_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    changes = await check_all(context.bot)
    if not changes:
        return
    text, keyboard = updates_summary(changes)
    await broadcast_summary(context.bot, text, keyboard)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN в .env.")
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(callback, pattern=r"^(add|source:.*)$")], states={WAIT_SOURCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_source)]}, fallbacks=[CallbackQueryHandler(callback, pattern="^menu$")]))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CallbackQueryHandler(callback))
    app.job_queue.run_repeating(scheduled_check, interval=CHECK_INTERVAL_MINUTES * 60, first=15)
    app.run_polling()


if __name__ == "__main__":
    main()