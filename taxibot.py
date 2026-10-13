import asyncio
import html
import os
import sqlite3
import time as systime
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.filters import BaseFilter, Command
from aiogram.types import Message

API_TOKEN = os.environ["BOT_TOKEN"]
BOSS_ID = int(os.getenv("BOSS_ID", "115023072"))
RENT_CHAT_ID = int(os.getenv("RENT_CHAT_ID", "-1002734746695"))  # чат отдела аренды для отчета
DB_PATH = os.getenv("DB_PATH", "taxibot.db")

MSK = ZoneInfo("Europe/Moscow")
ALEXEY = "@alexey_del"
FINE_AMOUNT = 5000

DAY1_COUNT, DAY1_INTERVAL = 10, 4 * 60  # 10-е напоминание = штраф
DAY2_COUNT, DAY2_INTERVAL = 10, 5 * 60
DAY2_START = time(10, 0)
REPORT_TIME = time(10, 0)  # 1 число каждого месяца, МСК

TRACKED_TYPES = {
    "text", "photo", "video", "document", "audio", "voice", "video_note",
    "animation", "sticker", "poll", "location", "contact",
}
MONTHS = [
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]

bot = Bot(token=API_TOKEN, parse_mode="HTML")
dp = Dispatcher()
tasks = {}


# ---------- хранилище ----------

db = sqlite3.connect(DB_PATH)
db.row_factory = sqlite3.Row
db.executescript("""
CREATE TABLE IF NOT EXISTS tracked (
    chat_id INTEGER,
    message_id INTEGER,
    chat_title TEXT,
    user_id INTEGER,
    user_name TEXT,
    created_at REAL,
    day INTEGER DEFAULT 1,
    step INTEGER DEFAULT 0,
    next_at REAL,
    reminder_id INTEGER,
    fine_id INTEGER,
    PRIMARY KEY (chat_id, message_id)
);
CREATE TABLE IF NOT EXISTS fines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    chat_title TEXT,
    message_id INTEGER,
    fine_message_id INTEGER,
    user_id INTEGER,
    user_name TEXT,
    amount INTEGER,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
""")


def now_msk():
    return datetime.now(MSK)


def month_key(dt):
    return f"{dt.year:04d}-{dt.month:02d}"


def prev_month(dt):
    return dt.replace(day=1) - timedelta(days=1)


# при первом запуске считаем, что за прошлый месяц отчет уже был
db.execute(
    "INSERT OR IGNORE INTO meta (key, value) VALUES ('last_report', ?)",
    (month_key(prev_month(now_msk())),),
)
db.commit()


def get_tracked(chat_id, message_id):
    return db.execute(
        "SELECT * FROM tracked WHERE chat_id = ? AND message_id = ?",
        (chat_id, message_id),
    ).fetchone()


def update_tracked(row, **fields):
    sets = ", ".join(f"{k} = ?" for k in fields)
    db.execute(
        f"UPDATE tracked SET {sets} WHERE chat_id = ? AND message_id = ?",
        (*fields.values(), row["chat_id"], row["message_id"]),
    )
    db.commit()


def delete_tracked(row):
    db.execute(
        "DELETE FROM tracked WHERE chat_id = ? AND message_id = ?",
        (row["chat_id"], row["message_id"]),
    )
    db.commit()


# ---------- утилиты ----------

class BossFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return message.from_user is not None and message.from_user.id == BOSS_ID


def user_html(user_id, user_name):
    if not user_name:
        return ""
    if user_id and not user_name.startswith("@"):
        return f'<a href="tg://user?id={user_id}">{html.escape(user_name)}</a>'
    return html.escape(user_name)


def user_from_tg(user):
    name = f"@{user.username}" if user.username else user.full_name
    return user.id, name


def extract_mention(message: Message):
    text = message.text or message.caption or ""
    for entity in message.entities or message.caption_entities or []:
        if entity.type == "text_mention" and entity.user:
            return user_from_tg(entity.user)
        if entity.type == "mention":
            return None, entity.extract_from(text)
    return None, None


def fmt_money(amount):
    return f"{amount:,}".replace(",", " ") + " ₽"


def message_link(chat_id, message_id):
    s = str(chat_id)
    if s.startswith("-100"):
        return f"https://t.me/c/{s[4:]}/{message_id}"
    return None


async def safe_delete(chat_id, message_id):
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        print(f"[ERROR] Не удалось удалить: {e}")


async def safe_send(chat_id, text, reply_to=None):
    try:
        return await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_to_message_id=reply_to,
            allow_sending_without_reply=True,
        )
    except Exception as e:
        print(f"[ERROR] Ошибка отправки: {e}")
        return None


# ---------- напоминания и штраф ----------

def start_tracking(chat_id, message_id):
    key = (chat_id, message_id)
    if key not in tasks or tasks[key].done():
        tasks[key] = asyncio.create_task(track_loop(chat_id, message_id))


def is_alexey(row):
    return (row["user_name"] or "").lower() == ALEXEY


def day2_first_at(row):
    created = datetime.fromtimestamp(row["created_at"], MSK)
    start = datetime.combine(created.date() + timedelta(days=1), DAY2_START, MSK)
    return max(start.timestamp(), systime.time()) + DAY2_INTERVAL


async def send_reminder(row, text):
    await safe_delete(row["chat_id"], row["reminder_id"])
    sent = await safe_send(row["chat_id"], text, reply_to=row["message_id"])
    update_tracked(row, reminder_id=sent.message_id if sent else None)


async def send_fine(row, n):
    await safe_delete(row["chat_id"], row["reminder_id"])

    if row["user_name"]:
        text = (
            f"Напоминание {n}: ответа нет {user_html(row['user_id'], row['user_name'])}\n"
            f"Назначен штраф {fmt_money(FINE_AMOUNT)}"
        )
    else:
        text = (
            f"Напоминание {n}: ответа нет.\n"
            f"Назначен штраф {fmt_money(FINE_AMOUNT)}\n"
            f"Ответьте на это сообщение, отметив сотрудника через @"
        )

    sent = await safe_send(row["chat_id"], text, reply_to=row["message_id"])
    fine_message_id = sent.message_id if sent else None

    db.execute(
        "INSERT INTO fines (chat_id, chat_title, message_id, fine_message_id,"
        " user_id, user_name, amount, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (row["chat_id"], row["chat_title"], row["message_id"], fine_message_id,
         row["user_id"], row["user_name"], FINE_AMOUNT, systime.time()),
    )
    db.commit()
    update_tracked(row, reminder_id=None, fine_id=fine_message_id)
    print(f"[LOG] Штраф: {row['user_name']} ({row['chat_id']}/{row['message_id']})")


async def do_step(row):
    day, n = row["day"], row["step"] + 1
    mention = user_html(row["user_id"], row["user_name"])

    if day == 1:
        if n < DAY1_COUNT:
            await send_reminder(row, f"Напоминание {n}: ответа нет {mention}".strip())
            update_tracked(row, step=n, next_at=systime.time() + DAY1_INTERVAL)
            return

        await send_fine(row, n)
        if is_alexey(row):
            print("[LOG] Запускаем второй день для Алексея")
            update_tracked(row, day=2, step=0, next_at=day2_first_at(row))
        else:
            delete_tracked(row)
        return

    await send_reminder(row, f"День 2. Напоминание {n}: ответа нет {mention}".strip())
    if n < DAY2_COUNT:
        update_tracked(row, step=n, next_at=systime.time() + DAY2_INTERVAL)
    else:
        delete_tracked(row)


async def track_loop(chat_id, message_id):
    while True:
        row = get_tracked(chat_id, message_id)
        if not row:
            return

        delay = row["next_at"] - systime.time()
        if delay > 0:
            await asyncio.sleep(delay)

        row = get_tracked(chat_id, message_id)
        if not row:
            return

        try:
            await do_step(row)
        except Exception as e:
            print(f"[ERROR] Шаг напоминания: {e}")
            await asyncio.sleep(60)


# ---------- отчет ----------

def collect_fines(month_dt):
    start = month_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=32)).replace(day=1)
    return db.execute(
        "SELECT * FROM fines WHERE created_at >= ? AND created_at < ? ORDER BY created_at",
        (start.timestamp(), end.timestamp()),
    ).fetchall()


def build_report(month_dt, note=""):
    rows = collect_fines(month_dt)
    header = f"<b>Штрафы за {MONTHS[month_dt.month - 1]} {month_dt.year}{note}</b>"
    if not rows:
        return [header, "Штрафов нет"]

    by_user = {}
    unassigned = []
    for r in rows:
        if not r["user_name"]:
            unassigned.append(r)
            continue
        item = by_user.setdefault(
            r["user_name"].lower(),
            {"user_id": r["user_id"], "name": r["user_name"], "count": 0, "sum": 0},
        )
        item["count"] += 1
        item["sum"] += r["amount"]

    lines = [header, ""]
    for item in sorted(by_user.values(), key=lambda x: -x["sum"]):
        lines.append(
            f"{user_html(item['user_id'], item['name'])} — "
            f"{item['count']} шт. — {fmt_money(item['sum'])}"
        )

    if unassigned:
        lines += ["", "<b>Не закреплены за сотрудником:</b>"]
        for r in unassigned:
            date = datetime.fromtimestamp(r["created_at"], MSK).strftime("%d.%m %H:%M")
            link = message_link(r["chat_id"], r["fine_message_id"])
            where = html.escape(r["chat_title"] or "")
            if link:
                where = f'<a href="{link}">{where or "сообщение"}</a>'
            lines.append(f"{date} — {where} — {fmt_money(r['amount'])}")

    lines += ["", f"<b>Итого: {len(rows)} шт. на {fmt_money(sum(r['amount'] for r in rows))}</b>"]
    return lines


async def send_lines(chat_id, lines):
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > 4000:
            await bot.send_message(chat_id, chunk, disable_web_page_preview=True)
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        await bot.send_message(chat_id, chunk, disable_web_page_preview=True)


async def monthly_report_loop():
    while True:
        now = now_msk()
        month = prev_month(now)
        report_at = datetime.combine(now.date().replace(day=1), REPORT_TIME, MSK)
        last = db.execute("SELECT value FROM meta WHERE key = 'last_report'").fetchone()[0]

        if now >= report_at and last != month_key(month):
            if not RENT_CHAT_ID:
                print("[ERROR] RENT_CHAT_ID не задан, отчет по штрафам не отправлен")
            else:
                try:
                    await send_lines(RENT_CHAT_ID, build_report(month))
                    db.execute(
                        "UPDATE meta SET value = ? WHERE key = 'last_report'",
                        (month_key(month),),
                    )
                    db.commit()
                    print(f"[LOG] Отчет за {month_key(month)} отправлен")
                except Exception as e:
                    print(f"[ERROR] Отчет по штрафам: {e}")

        await asyncio.sleep(3600)


# ---------- обработчики ----------

@dp.message(Command("chatid"), BossFilter())
async def cmd_chatid(message: Message):
    await message.reply(f"ID этого чата: <code>{message.chat.id}</code>")


@dp.message(Command("fines"), BossFilter())
async def cmd_fines(message: Message):
    await send_lines(message.chat.id, build_report(now_msk(), " (на сегодня)"))


async def assign_fine(message: Message):
    fine = db.execute(
        "SELECT * FROM fines WHERE chat_id = ? AND fine_message_id = ?",
        (message.chat.id, message.reply_to_message.message_id),
    ).fetchone()
    if not fine:
        return

    user_id, user_name = extract_mention(message)
    if not user_name:
        await message.reply("Отметьте сотрудника через @, чтобы закрепить штраф")
        return

    db.execute(
        "UPDATE fines SET user_id = ?, user_name = ? WHERE id = ?",
        (user_id, user_name, fine["id"]),
    )
    db.commit()
    await message.reply(
        f"Штраф {fmt_money(fine['amount'])} закреплен за {user_html(user_id, user_name)}"
    )


@dp.message(BossFilter())
async def boss_message(message: Message):
    if message.chat.type not in ("group", "supergroup"):
        return
    if message.content_type not in TRACKED_TYPES:
        return
    if (message.text or "").startswith("/"):
        return

    reply_to = message.reply_to_message
    if reply_to and reply_to.from_user and reply_to.from_user.id == bot.id:
        await assign_fine(message)
        return

    print(f"[LOG] Сообщение шефа: {message.text or message.caption} ({message.message_id})")

    user_id, user_name = extract_mention(message)
    # нет упоминания, но шеф ответил сотруднику — адресат он
    if not user_name and reply_to and reply_to.from_user and not reply_to.from_user.is_bot:
        user_id, user_name = user_from_tg(reply_to.from_user)

    db.execute(
        "INSERT OR REPLACE INTO tracked (chat_id, message_id, chat_title, user_id,"
        " user_name, created_at, next_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (message.chat.id, message.message_id, message.chat.title, user_id, user_name,
         systime.time(), systime.time() + DAY1_INTERVAL),
    )
    db.commit()
    start_tracking(message.chat.id, message.message_id)


@dp.message()
async def any_reply(message: Message):
    reply_to = message.reply_to_message
    if not reply_to or not message.from_user or message.from_user.is_bot:
        return
    if message.from_user.id == BOSS_ID:
        return

    # ответ на сообщение шефа, на напоминание или на сообщение о штрафе
    row = db.execute(
        "SELECT * FROM tracked WHERE chat_id = ?"
        " AND (message_id = ? OR reminder_id = ? OR fine_id = ?)",
        (message.chat.id, reply_to.message_id, reply_to.message_id, reply_to.message_id),
    ).fetchone()
    if not row:
        return

    print(f"[LOG] Ответ получен: {message.text or message.caption}")
    await safe_delete(row["chat_id"], row["reminder_id"])
    delete_tracked(row)


async def main():
    print("[LOG] Бот запущен")
    for row in db.execute("SELECT chat_id, message_id FROM tracked").fetchall():
        start_tracking(row["chat_id"], row["message_id"])
    asyncio.create_task(monthly_report_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
