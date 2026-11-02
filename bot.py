import os
import json
import time
import asyncio
import logging
from collections import defaultdict

import tornado.web
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from google import genai
from google.genai import types

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")  # Render-də avtomatik olur
PORT = int(os.getenv("PORT", "8080"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")  # istəyə görə

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("TELEGRAM_BOT_TOKEN və GEMINI_API_KEY təyin edilməlidir.")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
# httpx URL-ləri loqlayır, bu da Telegram tokenini göstərərdi
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("starfish")

# Hər model üçün maksimum gözləmə: 20 saniyə (millisaniyə ilə)
client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options=types.HttpOptions(timeout=20000),
)

# Əvvəlki kodda işləyən model siyahısı (biri məşğuldursa növbətiyə keçir)
CANDIDATE_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemma-4-31b-it",
    "gemma-4-26b-a4b-it",
]

SYSTEM_INSTRUCTION = (
    "Sən 'StarFish' adlı Telegram köməkçisisən. İstifadəçi ilə hər zaman səlis "
    "Azərbaycan dilində danış (istifadəçi başqa dildə yazıb xahiş etməsə).\n"
    "Qaydalar:\n"
    "- Sənin internetə çıxışın yoxdur, real vaxtda axtarış edə bilmirsən. "
    "Aktual xəbərləri və ya sənin bilmədiyin faktları bilmirsənsə, bunu açıq de, uydurma.\n"
    "- Bu söhbətdə yalnız son bir neçə mesajı xatırlayırsan. İstifadəçi /reset yazsa, söhbət sıfırlanır.\n"
    "- Cavabların aydın və qısa olsun, lazım olmayanda uzatma.\n"
    "- Əmin olmadığın şeyi əmin kimi təqdim etmə."
)

# Hər söhbət üçün saxlanan maksimum mesaj sayı (10 sual-cavab cütü)
MAX_HISTORY = 20
TELEGRAM_LIMIT = 4000  # Telegram mesajı 4096 simvoldan uzun ola bilməz

# Yaddaş RAM-dadır: servis yenidən başlayanda silinir
chat_histories: dict[int, list[dict]] = {}
chat_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    await update.message.reply_text(
        f"Salam, {name}! 👋\n\n"
        "Mən StarFish süni intellekt köməkçisiyəm. Mənə istənilən sualı verə bilərsən. "
        "Səni dinləyirəm!"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📌 Kömək\n\n"
        "- Sadəcə sualını yaz və göndər.\n"
        "- Son mesajlarımızı xatırlayıram.\n"
        "- /reset - söhbəti sıfırla\n"
        "- /start - salamlaşma\n"
        "- /help - bu menyu"
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_histories.pop(update.effective_chat.id, None)
    await update.message.reply_text("Söhbət sıfırlandı. Yenidən başlayaq! ✨")


def generate_reply(contents: list[dict]) -> str | None:
    """Modelləri növbə ilə yoxlayır. Bloklayıcıdır, to_thread ilə çağırılır."""
    for model_name in CANDIDATE_MODELS:
        started = time.perf_counter()
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=contents,
                config={"system_instruction": SYSTEM_INSTRUCTION},
            )
            if response.text:
                logger.info(
                    "%s cavab verdi (%.1f san)", model_name, time.perf_counter() - started
                )
                return response.text
        except Exception as e:
            logger.warning(
                "%s xəta verdi (%.1f san), növbəti model yoxlanılır: %s",
                model_name,
                time.perf_counter() - started,
                e,
            )
    return None


def split_text(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    return [text[i:i + limit] for i in range(0, len(text), limit)] or [""]


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    if not user_message:
        return

    chat_id = update.effective_chat.id

    # Eyni istifadəçinin mesajları sıra ilə işlənsin (yaddaş qarışmasın)
    async with chat_locks[chat_id]:
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        history = chat_histories.get(chat_id, [])
        contents = history + [{"role": "user", "parts": [{"text": user_message}]}]

        reply = await asyncio.to_thread(generate_reply, contents)

        if not reply:
            await update.message.reply_text(
                "Hazırda cavab verə bilmirəm, modellər məşğuldur. Bir az sonra yenidən yaz."
            )
            return

        # Yalnız uğurlu cavabdan sonra yadda saxla
        contents.append({"role": "model", "parts": [{"text": reply}]})
        chat_histories[chat_id] = contents[-MAX_HISTORY:]

    for chunk in split_text(reply):
        await update.message.reply_text(chunk)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Gözlənilməz xəta", exc_info=context.error)


class HealthHandler(tornado.web.RequestHandler):
    """UptimeRobot üçün: servis oyaq olanda 200 OK qaytarır."""

    def get(self):
        self.write("OK")

    def head(self):
        self.set_status(200)


class TelegramWebhookHandler(tornado.web.RequestHandler):
    """Telegram-dan gələn yenilikləri qəbul edib botun növbəsinə qoyur."""

    def initialize(self, ptb_app):
        self.ptb_app = ptb_app

    async def post(self):
        if WEBHOOK_SECRET:
            token = self.request.headers.get("X-Telegram-Bot-Api-Secret-Token")
            if token != WEBHOOK_SECRET:
                self.set_status(403)
                return
        try:
            update = Update.de_json(json.loads(self.request.body), self.ptb_app.bot)
        except Exception:
            logger.warning("Etibarsız webhook sorğusu")
            self.set_status(400)
            return
        await self.ptb_app.update_queue.put(update)
        self.set_status(200)


async def run_webhook_server(ptb_app):
    async with ptb_app:
        await ptb_app.start()
        await ptb_app.bot.set_webhook(
            url=f"{RENDER_URL}/telegram",
            secret_token=WEBHOOK_SECRET or None,
            drop_pending_updates=True,
        )
        web = tornado.web.Application(
            [
                (r"/health", HealthHandler),
                (r"/telegram", TelegramWebhookHandler, {"ptb_app": ptb_app}),
            ]
        )
        web.listen(PORT, address="0.0.0.0")
        print("🤖 StarFish botu webhook rejimində aktivdir...")
        try:
            await asyncio.Event().wait()
        finally:
            await ptb_app.stop()


def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    if RENDER_URL:
        asyncio.run(run_webhook_server(app))
    else:
        print("🤖 StarFish botu polling rejimində aktivdir...")
        app.run_polling()


if __name__ == "__main__":
    main()