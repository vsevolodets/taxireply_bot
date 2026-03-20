import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.filters import BaseFilter

API_TOKEN = '8734868063:AAHFBnYtCjYGvEZ9F65ucBx1l5bPvD4E7Y8'
BOSS_ID = 115023072  # Telegram ID шефа

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

boss_messages = {}

class BossFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return message.from_user.id == BOSS_ID

def get_mention(user):
    if isinstance(user, types.User):
        return f"[{user.first_name}](tg://user?id={user.id})"
    return str(user)

@dp.message(BossFilter())
async def boss_message(message: Message):
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
        "bot_replies": [],
        "step": 0
    }
    asyncio.create_task(auto_reply_loop(message.message_id))

@dp.message()
async def any_reply(message: Message):
    if message.reply_to_message:
        replied_id = message.reply_to_message.message_id
        if replied_id in boss_messages:
            print(f"[LOG] На сообщение шефа пришел ответ: {message.text}")
            boss_messages[replied_id]["replied"] = True

async def auto_reply_loop(message_id):
    timings = [4*60, 4*60, 4*60, 60, 60, 60, 4*60]
    texts = [
        "Ответа не было",
        "Напоминаю, ответа не было",
        "Все ещё нет ответа",
        "Напоминаю ещё раз",
        "Напоминаю ещё раз",
        "Напоминаю ещё раз",
        "Штраф 5000 рублей"
    ]
    data = boss_messages.get(message_id)
    if not data:
        return
    for i, delay in enumerate(timings):
        await asyncio.sleep(delay)
        data = boss_messages.get(message_id)
        if not data or data["replied"]:
            break
        for msg_id in data["bot_replies"]:
            try:
                await bot.delete_message(chat_id=data["chat_id"], message_id=msg_id)
            except:
                pass
        data["bot_replies"].clear()
        text = texts[i]
        if data["mentioned_user"]:
            text += " " + get_mention(data["mentioned_user"])
        sent = await bot.send_message(
            chat_id=data["chat_id"],
            text=text,
            reply_to_message_id=message_id,
            parse_mode=ParseMode.MARKDOWN
        )
        data["bot_replies"].append(sent.message_id)
        data["step"] = i + 1
    boss_messages.pop(message_id, None)

async def main():
        print(f"[LOG] Бот запущен, токен: {API_TOKEN[:10]}...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
