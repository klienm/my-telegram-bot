import os
import httpx
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

BOT_TOKEN = "8975704106:AAEQGsSOQWGmqx_TUId8pLv9oA9xnYo9kCo"

# --- سيرفر وهمي لإبقاء الخدمة تعمل على Render 24/7 ---
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
        "عرض كروت وبيلدات الشخصيات المصورة لـ Honkai: Star Rail!\n\n"
        "🔹 `/hsr <UID>` - لفحص الحساب واختيار الشخصية\n\n"
        "⚠️ *تنبيه:* تأكد من تفعيل **Show Character Details** داخل اللعبة."
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

# --- أمر Honkai: Star Rail ---
async def hsr_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ يرجى كتابة الـ UID بعد الأمر!\nمثال: `/hsr 800000000`", parse_mode='Markdown')
        return

    uid = context.args[0]
    await update.message.reply_text("⏳ جاري جلب بيانات الحساب والشخصيات...")

    # استخدام Mihomo API لجلب بيانات ستار ريل بدقة
    url = f"https://api.mihomo.me/sr_info_parsed/{uid}?lang=en"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, timeout=12)
            if response.status_code != 200:
                await update.message.reply_text("❌ لم يتم العثور على الحساب. تأكد من صحة الـ UID.")
                return

            data = response.json()
            player = data.get("player", {})
            nickname = player.get("nickname", "غير متوفر")
            level = player.get("level", "غير متوفر")
            avatars = data.get("characters", []) or data.get("avatar_list", [])

            if not avatars:
                await update.message.reply_text(
                    f"👤 **الاسم:** {nickname}\n📊 **المستوى:** {level}\n\n"
                    "⚠️ *لا توجد شخصيات معروضة.* تأكد من تفعيل 'Show Character Details' داخل اللعبة."
                )
                return

            # إنشاء أزرار تفاعلية باسم كل شخصية معروضة
            keyboard = []
            for idx, char in enumerate(avatars):
                char_name = char.get("name", f"شخصية #{idx + 1}")
                button_text = f"⚔️ {char_name}"
                keyboard.append([InlineKeyboardButton(button_text, callback_data=f"hsr_{uid}_{idx}")])

            reply_markup = InlineKeyboardMarkup(keyboard)

            response_msg = (
                f"🚀 **Honkai: Star Rail Profile**\n\n"
                f"👤 **الاسم:** {nickname}\n"
                f"📊 **المستوى:** {level}\n"
                f"👥 **الشخصيات المتاحة:** {len(avatars)}\n\n"
                f"👇 **اختر الشخصية لعرض بطاقة البيلد المصورة بالكامل:**"
            )

            await update.message.reply_text(response_msg, reply_markup=reply_markup, parse_mode='Markdown')

        except Exception as e:
            await update.message.reply_text("❌ حدث خطأ أثناء جلب البيانات من السيرفر.")

# --- المعالج عند ضغط زر الشخصية (يرسل صورة البطاقة فقط) ---
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data_parts = query.data.split("_")
    if data_parts[0] == "hsr":
        uid = data_parts[1]
        char_idx = int(data_parts[2])

        await query.edit_message_text("🎨 جاري سحب صورة بطاقة البيلد...")

        # استخدام خدمة توليد كروت Mihomo/Enka المباشرة
        card_generator_url = f"https://api.mihomo.me/sr_info_parsed/{uid}?lang=en"

        async with httpx.AsyncClient() as client:
            try:
                res = await client.get(card_generator_url, timeout=12)
                if res.status_code == 200:
                    data = res.json()
                    avatars = data.get("characters", []) or data.get("avatar_list", [])
                    
                    if char_idx < len(avatars):
                        char_data = avatars[char_idx]
                        
                        # جلب صورة كارت البيلد الجاهز من API أو صورة البيلد المجمعة
                        # يتم استخدام رابط كارت الشخصية المباشر
                        char_id = char_data.get("id", "")
                        
                        # رابط الصورة الجاهزة للبيلد (Card)
                        card_img_url = f"https://raw.githubusercontent.com/Mar-Base/Mihomo.me/main/assets/image/character/{char_id}.png"

                        # إرسال الصورة صافية بدون نص تحتي
                        try:
                            await query.message.reply_photo(photo=card_img_url)
                            return
                        except Exception:
                            pass

                await query.message.reply_text("❌ تعذر تحميل صورة الكارت. جرب مرة أخرى.")
            except Exception as e:
                await query.message.reply_text("❌ حدث خطأ أثناء تجهيز الصورة.")

# --- تشغيل البوت ---
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("hsr", hsr_check))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("🚀 البوت يعمل الآن بنجاح!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
