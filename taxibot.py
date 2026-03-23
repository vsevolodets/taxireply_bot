import asyncio
from datetime import datetime, timedelta, time
from aiogram import Bot, Dispatcher, types
from aiogram.types import Message
from aiogram.filters import BaseFilter

API_TOKEN = '8734868063:AAHFBnYtCjYGvEZ9F65ucBx1l5bPvD4E7Y8'
BOSS_ID = 115023072

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

boss_messages = {}

class BossFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return message.from_user.id == BOSS_ID


def get_mention(user):
    if isinstance(user, types.User):
        return user.first_name
    elif isinstance(user, str):
        return user
    return str(user)


@dp.message(BossFilter())
async def boss_message(message: Message):
    if not message.text:
        return

    if '?' not in message.text or len(message.text.strip()) < 5:
        return

    print(f"[LOG] Сообщение шефа: {message.text} ({message.message_id})")

    mentioned_user = None
    if message.entities:
        for entity in message.entities:
            if entity.type == "text_mention":
                mentioned_user = entity.user
                break
            elif entity.type == "mention":
                username = message.text[entity.offset: entity.offset + entity.length]
                mentioned_user = username
                break

    boss_messages[message.message_id] = {
        "chat_id": message.chat.id,
        "replied": False,
        "mentioned_user": mentioned_user,
        "bot_replies": []
    }

    asyncio.create_task(auto_reply_loop(message.message_id))


@dp.message()
async def any_reply(message: Message):
    if message.reply_to_message:
        replied_id = message.reply_to_message.message_id

        if replied_id in boss_messages:
            print(f"[LOG] Ответ получен: {message.text}")

            data = boss_messages[replied_id]
            data["replied"] = True

            # 🧹 удаляем ВСЕ сообщения бота
            for msg_id in data["bot_replies"]:
                try:
                    await bot.delete_message(
                        chat_id=data["chat_id"],
                        message_id=msg_id
                    )
                except:
                    pass

            boss_messages.pop(replied_id, None)


async def send_and_track(message_id, text):
    data = boss_messages.get(message_id)
    if not data:
        return

    try:
        sent = await bot.send_message(
            chat_id=data["chat_id"],
            text=text,
            reply_to_message_id=message_id
        )
        data["bot_replies"].append(sent.message_id)
    except Exception as e:
        print(f"[ERROR] {e}")


async def auto_reply_loop(message_id):
    data = boss_messages.get(message_id)
    if not data:
        return

    mentioned = data["mentioned_user"]
    is_alexey = mentioned == "@alexey_del"

    # 🔁 обычные напоминания (каждые 4 минут, 10 раз)
    for i in range(10):
        await asyncio.sleep(4 * 60)

        data = boss_messages.get(message_id)
        if not data or data["replied"]:
            return

        text = f"Напоминание {i+1}: ответа нет"
        if mentioned:
            text += f" {get_mention(mentioned)}"

        await send_and_track(message_id, text)

    # 👤 если это Алексей — делаем второй день
    if is_alexey:
        print("[LOG] Запускаем второй день для Алексея")

        # ждём до следующего дня 10:00
        now = datetime.now()
        tomorrow_10 = datetime.combine(
            now.date() + timedelta(days=1),
            time(10, 0)
        )

        wait_seconds = (tomorrow_10 - now).total_seconds()
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)

        # ещё 10 напоминаний каждые 5 минут
        for i in range(10):
            await asyncio.sleep(5 * 60)

            data = boss_messages.get(message_id)
            if not data or data["replied"]:
                return

            text = f"День 2. Напоминание {i+1}: ответа нет {get_mention(mentioned)}"
            await send_and_track(message_id, text)

    boss_messages.pop(message_id, None)


async def main():
    print("[LOG] Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
