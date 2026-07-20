import os
import httpx
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

BOT_TOKEN = "8975704106:AAEQGsSOQWGmqx_TUId8pLv9oA9xnYo9kCo"

# --- سيرفر وهمي لإبقاء الخدمة تعمل على Render ---
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Running Live!")

def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- أمر البداية ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 **أهلاً بك!**\n\n"
        "عرض بطاقات البيلد المصورة للعبة Honkai: Star Rail و Genshin Impact!\n\n"
        "🔹 `/hsr <UID>` - لفحص حساب ستار ريل\n"
        "🔹 `/genshin <UID>` - لفحص حساب قنشن\n\n"
        "⚠️ *تنبيه:* تأكد من تفعيل **Show Character Details** داخل اللعبة."
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

# --- أمر Honkai: Star Rail ---
async def hsr_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ يرجى كتابة الـ UID بعد الأمر!\nمثال: `/hsr 800000000`", parse_mode='Markdown')
        return

    uid = context.args[0]
    await update.message.reply_text("⏳ جاري جلب بيانات الحساب للشخصيات...")

    url = f"https://enka.network/api/hsr/uid/{uid}"
    headers = {"User-Agent": "Mozilla/5.0"}

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers, timeout=12)
            if response.status_code != 200:
                await update.message.reply_text("❌ لم يتم العثور على الحساب. تأكد من الـ UID.")
                return

            data = response.json()
            detail_info = data.get("detailInfo") or data.get("detailHeader") or {}
            
            nickname = detail_info.get("nickname", "غير متوفر")
            level = detail_info.get("level", "غير متوفر")
            avatar_list = detail_info.get("avatarDetailList") or data.get("avatarDetailList") or []

            if not avatar_list:
                await update.message.reply_text("⚠️ لا توجد شخصيات معروضة. تأكد من تفعيل 'Show Character Details' داخل اللعبة.")
                return

            keyboard = []
            for idx, avatar in enumerate(avatar_list):
                avatar_id = str(avatar.get("avatarId", idx))
                button_text = f"⚔️ شخصية #{idx + 1}"
                keyboard.append([InlineKeyboardButton(button_text, callback_data=f"hsr_{uid}_{idx}_{avatar_id}")])

            reply_markup = InlineKeyboardMarkup(keyboard)

            response_msg = (
                f"🚀 **Honkai: Star Rail Profile**\n\n"
                f"👤 **الاسم:** {nickname}\n"
                f"📊 **المستوى:** {level}\n\n"
                f"👇 **اختر الشخصية لتوليد صورة البيلد الكاملة:**"
            )

            await update.message.reply_text(response_msg, reply_markup=reply_markup, parse_mode='Markdown')

        except Exception as e:
            await update.message.reply_text("❌ حدث خطأ أثناء الاتصال بالسيرفر.")

# --- المعالج لإنشاء وإرسال بطاقة البيلد كصورة كاملة ---
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data_parts = query.data.split("_")
    if data_parts[0] == "hsr":
        uid = data_parts[1]
        char_idx = data_parts[2]
        avatar_id = data_parts[3]

        await query.edit_message_text("🎨 جاري توليد صورة بطاقة البيلد الكاملة...")

        # خدمة توليد كروت Enka المصورة لـ Star Rail المباشرة
        card_image_url = f"https://enka.network/api/hsr/uid/{uid}/profile" 
        
        # رابط صورة البطاقة التوليدية المباشرة (Enka Card API)
        card_api_url = f"https://cards.enka.network/u/hsr/{uid}/{char_idx}.png"

        try:
            # إرسال البطاقة فقط كصورة بدقة عالية بدون أي كلام تحتي
            await query.message.reply_photo(photo=card_api_url)
        except Exception:
            # رابط احتياطي لتوليد كارت البيلد
            fallback_card = f"https://enka.network/ui/hsr/SpriteOutput/AvatarDrawCard/{avatar_id}.png"
            await query.message.reply_photo(photo=fallback_card)

# --- تشغيل التطبيق ---
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("hsr", hsr_check))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("🚀 البوت يعمل الآن بنجاح!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
