import os
import asyncio
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Render bu iki dəyişəni avtomatik verir (webhook rejimi üçün)
PORT = int(os.getenv("PORT", "10000"))
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")

# İstəyə görə: Telegram sorğusunu yoxlamaq üçün gizli söz (yalnız hərf, rəqəm, _ və -)
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Zəhmət olmasa .env faylında TELEGRAM_BOT_TOKEN və GEMINI_API_KEY təyin edin.")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = (
    "Sən 'StarFish' adlı intellektual köməkçisisən. "
    "İstifadəçi ilə hər zaman səlis Azərbaycan dilində ünsiyyət qur."
)

# Yoxlanacaq modellərin prioriteti (biri yüklənibsə, dərhal növbətiyə keçəcək)
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


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name
    welcome_text = (
        f"Salam, {user_name}! 👋\n\n"
        "Mən **StarFish** süni intellekt köməkçisiyəm. "
        "Mənə istənilən sualı verə bilərsən. Səni dinləyirəm!"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📌 **Kömək Bölməsi**\n\n"
        "- Sadəcə sualını yaz və göndər.\n"
        "- /start - Botu yenidən başlatmaq üçün\n"
        "- /help - Bu menyunu görmək üçün"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


def ask_gemini(user_message: str):
    """Modelləri növbə ilə yoxlayır. (cavab, son_xəta) qaytarır."""
    last_error = None
    for model_name in CANDIDATE_MODELS:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=user_message,
                config={"system_instruction": SYSTEM_PROMPT},
            )
            if response.text:
                return response.text, None
        except Exception as e:
            last_error = e
            logging.warning(f"{model_name} xəta verdi, növbəti model yoxlanılır: {e}")
    return None, last_error


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    if not user_message:
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # Sinxron Gemini çağırışını ayrı thread-də işlədirik ki, bot bloklanmasın
    bot_reply, last_error = await asyncio.to_thread(ask_gemini, user_message)

    if not bot_reply:
        logging.error(f"Bütün modellər uğursuz oldu: {last_error}")
        bot_reply = "Bütün modellər hazırda məşğuldur. Zəhmət olmasa bir azdan yenidən cəhd et."

    await update.message.reply_text(bot_reply)


def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    if RENDER_URL:
        # Serverdə: webhook rejimi
        print("🤖 StarFish botu webhook rejimində aktivdir...")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TELEGRAM_TOKEN,
            webhook_url=f"{RENDER_URL}/{TELEGRAM_TOKEN}",
            secret_token=WEBHOOK_SECRET,
            drop_pending_updates=True,
        )
    else:
        # Kompüterdə: əvvəlki kimi polling
        print("🤖 StarFish botu polling rejimində aktivdir...")
        app.run_polling()


if __name__ == "__main__":
    main()