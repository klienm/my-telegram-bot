"""
بوت تيليجرام مدموج مع Gemini AI - مبني للنشر على Render (Flask + Webhook).

ليش Webhook مو Polling؟
    Render خدمة ويب لازم تستمع على بورت وترد على طلبات HTTP.
    الـ Polling (البوت يسأل تيليجرام كل شوي) ما يناسب هالبيئة، فاستخدمنا
    Webhook: تيليجرام نفسه يبعت الرسائل الجديدة كـ POST request لسيرفرنا.

فكرة العمل:
- قاعدة بيانات SQLite فيها:
    1) جدول knowledge: المعلومات الشخصية / معلومات المشروع تضيفها يدوياً.
    2) جدول settings: أسلوب الرد العام (تون، لهجة... إلخ).
- كل رسالة توصل، الكود يجيب المعلومات + الأسلوب، يبعتهم مع سؤال المستخدم
  لـ Gemini، وياخذ الرد ويرسله كـ reply على رسالة المستخدم.
- فيه معالجة أخطاء وretry logic حتى لو صار خطأ مؤقت (بإنترنت، Gemini،
  تيليجرام...) البوت يكمل شغل بدل ما يطيح.

المكتبات المطلوبة (requirements.txt):
    python-telegram-bot
    Flask
    pillow
    aiohttp
    google-generativeai
    gunicorn

متغيرات البيئة المطلوبة على Render (Settings -> Environment):
    BOT_TOKEN       -> توكن بوت تيليجرام
    GEMINI_API_KEY  -> مفتاح Gemini API
    WEBHOOK_URL     -> رابط السيرفس على Render (اختياري، Render يعطيه تلقائياً
                       عبر RENDER_EXTERNAL_URL، بس تقدر تحدده يدوياً لو حبيت)

أوامر Render:
    Build Command : pip install -r requirements.txt
    Start Command : gunicorn main:app --workers 1 --threads 4 --timeout 120

⚠️ ملاحظة مهمة جداً عن قاعدة البيانات:
    Render (بالخطة المجانية أو العادية بدون Persistent Disk) يمسح أي ملفات
    محلية (زي database.db) عند كل إعادة تشغيل/نشر. يعني المعلومات الي
    بتضيفها ممكن تروح. الحلول:
      1) تفعيل "Persistent Disk" من إعدادات Render (بخدمة مدفوعة).
      2) استخدام قاعدة بيانات خارجية (Render Postgres، أو أي DB مستضافة
         زي Supabase/Turso) بدل SQLite المحلي.
    لو حابب، أقدر أعدلك الكود يشتغل مباشرة مع Postgres بدل SQLite.
"""

import os
import time
import asyncio
import logging
import threading

from flask import Flask, request
import google.generativeai as genai
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
import sqlite3

# ---------------------------------------------------------------------------
# الإعدادات العامة
# ---------------------------------------------------------------------------

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
# Render يوفر هذا المتغير تلقائياً برابط السيرفس، أو حدده يدوياً
WEBHOOK_URL = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")
DB_PATH = os.environ.get("DB_PATH", "database.db")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
PORT = int(os.environ.get("PORT", 10000))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

if not BOT_TOKEN:
    raise RuntimeError("لازم تحط BOT_TOKEN في environment variables")
if not GEMINI_API_KEY:
    raise RuntimeError("لازم تحط GEMINI_API_KEY في environment variables")
if not WEBHOOK_URL:
    logger.warning(
        "WEBHOOK_URL/RENDER_EXTERNAL_URL مو موجود - لازم تحطه يدوياً "
        "وإلا البوت ما رح يوصله شي من تيليجرام."
    )

genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel(GEMINI_MODEL)


# ---------------------------------------------------------------------------
# قاعدة البيانات
# ---------------------------------------------------------------------------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    cursor.execute("SELECT value FROM settings WHERE key = 'reply_style'")
    if cursor.fetchone() is None:
        cursor.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            ("reply_style", "رد بأسلوب ودود ومباشر باللهجة العامية."),
        )

    conn.commit()
    conn.close()


def get_all_knowledge() -> str:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT content FROM knowledge")
    rows = cursor.fetchall()
    conn.close()
    return "\n".join(row[0] for row in rows)


def get_reply_style() -> str:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = 'reply_style'")
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else ""


def add_knowledge(text: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO knowledge (content) VALUES (?)", (text,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# التكامل مع Gemini (مع retry حتى يكون البوت "متكيف" مع مشاكل الشبكة/الـ API)
# ---------------------------------------------------------------------------

def ask_gemini(user_question: str, max_retries: int = 2) -> str:
    knowledge = get_all_knowledge()
    style = get_reply_style()

    prompt = f"""
أنت مساعد ذكي تجاوب المستخدمين اعتماداً فقط على المعلومات التالية.
إذا السؤال خارج نطاق هالمعلومات، اعتذر بلطف وقول إنك ما عندك معلومات كافية.

# أسلوب الرد المطلوب:
{style}

# المعلومات المتاحة:
{knowledge if knowledge else "لا توجد معلومات مخزنة حالياً."}

# سؤال المستخدم:
{user_question}

اكتب الرد المناسب مباشرة بدون مقدمات زيادة.
"""

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            response = gemini_model.generate_content(prompt)
            if response and response.text:
                return response.text.strip()
            raise ValueError("رد فاضي من Gemini")
        except Exception as e:
            last_error = e
            wait = 1.5 * (attempt + 1)
            logger.warning(f"محاولة {attempt + 1} فشلت مع Gemini: {e} - إعادة محاولة بعد {wait}s")
            time.sleep(wait)

    logger.error(f"فشلت كل محاولات Gemini: {last_error}")
    return "صار عندي ضغط شوي هلق، جرب تسألني بعد شوي 🙏"


# ---------------------------------------------------------------------------
# معالجات تيليجرام
# ---------------------------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلاً! اسألني أي شي وبجاوبك اعتماداً على المعلومات المتوفرة عندي."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.message.text:
            return

        user_question = update.message.text
        answer = ask_gemini(user_question)

        # quote=True يخلي الرد يظهر "reply" على رسالة المستخدم نفسها
        await update.message.reply_text(answer, quote=True)

    except Exception as e:
        logger.error(f"خطأ بمعالجة الرسالة: {e}")
        try:
            await update.message.reply_text("صار خطأ بسيط، جرب كمان مرة 🙏")
        except Exception:
            pass  # حتى لو فشل إرسال رسالة الخطأ، ما نوقع البوت


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """يمسك أي استثناء ما انلحق عالمعالجات فوق حتى ما يطيح التطبيق."""
    logger.error(f"استثناء غير متوقع: {context.error}")


# ---------------------------------------------------------------------------
# بناء تطبيق Telegram وربطه بحلقة أحداث (event loop) بالخلفية
# ---------------------------------------------------------------------------

application = Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("start", start_command))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
application.add_error_handler(error_handler)

bg_loop = asyncio.new_event_loop()


def _run_background_loop(loop: asyncio.AbstractEventLoop):
    asyncio.set_event_loop(loop)
    loop.run_forever()


bg_thread = threading.Thread(target=_run_background_loop, args=(bg_loop,), daemon=True)
bg_thread.start()


async def _startup():
    await application.initialize()
    await application.start()
    if WEBHOOK_URL:
        full_url = WEBHOOK_URL.rstrip("/") + "/webhook"
        await application.bot.set_webhook(url=full_url)
        logger.info(f"تم تسجيل الـ webhook: {full_url}")


# نشغل الإقلاع مرة وحدة عند تحميل الموديول (يشتغل مع gunicorn أيضاً)
asyncio.run_coroutine_threadsafe(_startup(), bg_loop).result()
init_db()


# ---------------------------------------------------------------------------
# سيرفر Flask
# ---------------------------------------------------------------------------

app = Flask(__name__)


@app.route("/", methods=["GET"])
def health_check():
    # Render بيستخدم هالمسار للتأكد إن السيرفس شغال
    return "Bot is running", 200


@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, application.bot)
        asyncio.run_coroutine_threadsafe(application.process_update(update), bg_loop)
    except Exception as e:
        logger.error(f"خطأ بمعالجة الـ webhook: {e}")
    # نرجع 200 دايماً حتى لو صار خطأ، حتى تيليجرام ما يعيد يحاول عبطالة
    return "OK", 200


if __name__ == "__main__":
    # للتجربة المحلية فقط - على Render رح يشغلها gunicorn
    app.run(host="0.0.0.0", port=PORT)
