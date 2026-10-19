import os
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Zəhmət olmasa .env faylında TELEGRAM_BOT_TOKEN və GEMINI_API_KEY təyin edin.")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

client = genai.Client(api_key=GEMINI_API_KEY)

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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    if not user_message:
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    bot_reply = None
    last_error = None

    # Modelləri növbə ilə yoxlayırıq:
    for model_name in CANDIDATE_MODELS:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=user_message,
                config={
                    "system_instruction": (
                        "Sən 'StarFish' adlı intellektual köməkçisisən. İstifadəçi ilə hər zaman səlis Azərbaycan dilində ünsiyyət qur."
                    )
                }
            )
            if response.text:
                bot_reply = response.text
                break
        except Exception as e:
            last_error = e
            logging.warning(f"{model_name} xəta verdi, növbəti model yoxlanılır: {e}")
            continue

    if not bot_reply:
        bot_reply = f"Bütün modellər hazırda məşğuldur. Son xəta: {last_error}"

    await update.message.reply_text(bot_reply)

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 StarFish botu aktivdir...")
    app.run_polling()

if __name__ == "__main__":
    main()