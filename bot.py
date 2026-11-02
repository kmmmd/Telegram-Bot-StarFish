import os
import asyncio
import logging
from collections import defaultdict

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

client = genai.Client(api_key=GEMINI_API_KEY)

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
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=contents,
                config={"system_instruction": SYSTEM_INSTRUCTION},
            )
            if response.text:
                return response.text
        except Exception as e:
            logger.warning("%s xəta verdi, növbəti model yoxlanılır: %s", model_name, e)
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


def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    if RENDER_URL:
        print("🤖 StarFish botu webhook rejimində aktivdir...")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path="telegram",
            webhook_url=f"{RENDER_URL}/telegram",
            secret_token=WEBHOOK_SECRET or None,
            drop_pending_updates=True,
        )
    else:
        print("🤖 StarFish botu polling rejimində aktivdir...")
        app.run_polling()


if __name__ == "__main__":
    main()