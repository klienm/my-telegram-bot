import os
import asyncio
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from io import BytesIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from enka_network import EnkaNetwork

BOT_TOKEN = "8975704106:AAEQGsSOQWGmqx_TUId8pLv9oA9xnYo9kCo"

# تهيئة مكتبة Enka
enka = EnkaNetwork()

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
        "عرض بطاقات البيلد المصورة المنسقة لـ Honkai: Star Rail!\n\n"
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

    try:
        # جلب البيانات عبر مكتبة Enka الرسمية
        async with enka:
            data = await enka.fetch_hsr(uid)
            
            if not data or not data.characters:
                await update.message.reply_text(
                    f"👤 **الاسم:** {data.player.nickname if data.player else 'غير متوفر'}\n\n"
                    "⚠️ *لا توجد شخصيات معروضة.* تأكد من تفعيل 'Show Character Details' داخل اللعبة."
                )
                return

            keyboard = []
            for idx, char in enumerate(data.characters):
                button_text = f"⚔️ {char.name}"
                keyboard.append([InlineKeyboardButton(button_text, callback_data=f"hsr_{uid}_{idx}")])

            reply_markup = InlineKeyboardMarkup(keyboard)

            response_msg = (
                f"🚀 **Honkai: Star Rail Profile**\n\n"
                f"👤 **الاسم:** {data.player.nickname}\n"
                f"📊 **المستوى:** {data.player.level}\n"
                f"👥 **الشخصيات المتاحة:** {len(data.characters)}\n\n"
                f"👇 **اختر الشخصية لعرض كارت البيلد المصور:**"
            )

            await update.message.reply_text(response_msg, reply_markup=reply_markup, parse_mode='Markdown')

    except Exception as e:
        await update.message.reply_text("❌ لم يتم العثور على الحساب أو السيرفر مشغول. تأكد من الـ UID.")

# --- المعالج عند ضغط زر الشخصية (يولّد الكارت الجاهز) ---
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data_parts = query.data.split("_")
    if data_parts[0] == "hsr":
        uid = data_parts[1]
        char_idx = int(data_parts[2])

        await query.edit_message_text("🎨 جاري سحب وتوليد كارت البيلد الجاهز...")

        try:
            async with enka:
                data = await enka.fetch_hsr(uid)
                if data and char_idx < len(data.characters):
                    char = data.characters[char_idx]
                    
                    # توليد بطاقة البيلد الأصلية باستخدام Enka Card Engine
                    card_image = await char.build_card()
                    
                    buf = BytesIO()
                    card_image.save(buf, format="PNG")
                    buf.seek(0)

                    # إرسال صورة البطاقة الجاهزة بالكامل
                    await query.message.reply_photo(photo=buf)
                    return

            await query.message.reply_text("❌ تعذر توليد صورة البطاقة.")
        except Exception as e:
            await query.message.reply_text("❌ حدث خطأ أثناء تجهيز كارت البيلد.")

# --- تشغيل البوت ---
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("hsr", hsr_check))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("🚀 البوت يعمل بنجاح مع كروت Enka!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
