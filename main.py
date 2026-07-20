import os
import asyncio
import httpx
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = "8975704106:AAEQGsSOQWGmqx_TUId8pLv9oA9xnYo9kCo"

# --- سيرفر وهمي لإبقاء الخطة المجانية شغالة على Render ---
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Running Live!")

def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    server.serve_forever()

# تشغيل السيرفر الوهمي في Thread خلفي
threading.Thread(target=run_dummy_server, daemon=True).start()

# --- أوامر البوت ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 **أهلاً بك!**\n\n"
        "أنا جاهز لعرض إحصائيات بيلداتك بضغطة زر:\n\n"
        "🔹 `/genshin <UID>` - لفحص حساب قنشن امباكت\n"
        "🔹 `/hsr <UID>` - لفحص حساب هونكاي ستار ريل\n\n"
        "⚠️ *تنبيه:* تأكد أن خيار **إظهار تفاصيل الشخصيات (Show Character Details)** مفعل في ملفك الشخصي داخل اللعبة."
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def genshin_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ يرجى كتابة الـ UID بعد الأمر!\nمثال: `/genshin 700000000`", parse_mode='Markdown')
        return

    uid = context.args[0]
    await update.message.reply_text("⏳ جاري جلب البيانات من السيرفر...")

    url = f"https://enka.network/api/uid/{uid}"
    headers = {"User-Agent": "Mozilla/5.0"}

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers, timeout=10)
            if response.status_code != 200:
                await update.message.reply_text("❌ لم يتم العثور على الحساب. تأكد من صحة الـ UID أو أن السيرفر مشغول.")
                return

            data = response.json()
            player_info = data.get("playerInfo", {})
            
            nickname = player_info.get("nickname", "غير متوفر")
            level = player_info.get("level", "غير متوفر")
            world_level = player_info.get("worldLevel", "غير متوفر")
            
            avatar_info_list = data.get("avatarInfoList", [])
            show_count = len(avatar_info_list)

            response_msg = (
                f"⚔️ **Genshin Impact Profile**\n\n"
                f"👤 **الاسم:** {nickname}\n"
                f"📊 **المستوى:** {level} (عالم {world_level})\n"
                f"👥 **الشخصيات المتاحة للفحص:** {show_count}\n"
            )
            
            if show_count == 0:
                response_msg += "\n⚠️ *تنبيه:* لا توجد شخصيات معروضة. تأكد من تفعيل 'Show Character Details' داخل اللعبة."

            await update.message.reply_text(response_msg, parse_mode='Markdown')

        except Exception as e:
            await update.message.reply_text("❌ حدث خطأ أثناء الاتصال بالسيرفر.")

async def hsr_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ يرجى كتابة الـ UID بعد الأمر!\nمثال: `/hsr 800000000`", parse_mode='Markdown')
        return

    uid = context.args[0]
    await update.message.reply_text("⏳ جاري جلب البيانات من السيرفر...")

    url = f"https://enka.network/api/hsr/uid/{uid}"
    headers = {"User-Agent": "Mozilla/5.0"}

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers, timeout=10)
            if response.status_code != 200:
                await update.message.reply_text("❌ لم يتم العثور على الحساب. تأكد من الـ UID أو أن السيرفر مشغول.")
                return

            data = response.json()
            
            detail_info = data.get("detailInfo") or data.get("detailHeader") or {}
            
            nickname = detail_info.get("nickname", "غير متوفر")
            level = detail_info.get("level", "غير متوفر")
            
            avatar_list = detail_info.get("avatarDetailList") or data.get("avatarDetailList") or []
            show_count = len(avatar_list)

            response_msg = (
                f"🚀 **Honkai: Star Rail Profile**\n\n"
                f"👤 **الاسم:** {nickname}\n"
                f"📊 **المستوى:** {level}\n"
                f"👥 **الشخصيات المتاحة للفحص:** {show_count}\n"
            )
            
            if show_count == 0:
                response_msg += "\n⚠️ *تنبيه:* لا توجد شخصيات معروضة. تأكد من تفعيل إظهار التفاصيل داخل اللعبة (Show Character Details)."

            await update.message.reply_text(response_msg, parse_mode='Markdown')

        except Exception as e:
            await update.message.reply_text("❌ حدث خطأ أثناء الاتصال بالسيرفر.")

# --- تشغيل التطبيق القياسي ---
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("genshin", genshin_check))
    app.add_handler(CommandHandler("hsr", hsr_check))

    print("🚀 البوت يعمل الآن بنجاح!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
