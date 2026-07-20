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
        "عرض إحصائيات وبيلدات الشخصيات أصبح أسهل بالأزرار التفاعلية!\n\n"
        "🔹 `/hsr <UID>` - لفحص حساب هونكاي ستار ريل وتحديد البيلدات\n"
        "🔹 `/genshin <UID>` - لفحص حساب قنشن امباكت\n\n"
        "⚠️ *تنبيه:* تأكد من تفعيل **Show Character Details** داخل اللعبة."
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

# --- أمر Honkai: Star Rail مع الأزرار التفاعلية ---
async def hsr_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ يرجى كتابة الـ UID بعد الأمر!\nمثال: `/hsr 800000000`", parse_mode='Markdown')
        return

    uid = context.args[0]
    await update.message.reply_text("⏳ جاري جلب بيانات الحساب والشخصيات...")

    url = f"https://enka.network/api/hsr/uid/{uid}"
    headers = {"User-Agent": "Mozilla/5.0"}

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers, timeout=12)
            if response.status_code != 200:
                await update.message.reply_text("❌ لم يتم العثور على الحساب. تأكد من الـ UID أو أن السيرفر مشغول.")
                return

            data = response.json()
            detail_info = data.get("detailInfo") or data.get("detailHeader") or {}
            
            nickname = detail_info.get("nickname", "غير متوفر")
            level = detail_info.get("level", "غير متوفر")
            avatar_list = detail_info.get("avatarDetailList") or data.get("avatarDetailList") or []

            if not avatar_list:
                await update.message.reply_text(
                    f"👤 **الاسم:** {nickname}\n📊 **المستوى:** {level}\n\n"
                    "⚠️ *لا توجد شخصيات معروضة.* تأكد من تفعيل 'Show Character Details' داخل اللعبة."
                )
                return

            # إنشاء أزرار تفاعلية لكل شخصية موجودة
            keyboard = []
            for idx, avatar in enumerate(avatar_list):
                # حاول الحصول على المعرف أو الاسم
                avatar_id = str(avatar.get("avatarId", idx))
                button_text = f"⚔️ شخصية #{idx + 1} (ID: {avatar_id})"
                # تخزين البيانات الممررة عند الضغط على الزر
                keyboard.append([InlineKeyboardButton(button_text, callback_data=f"hsr_{uid}_{idx}")])

            reply_markup = InlineKeyboardMarkup(keyboard)

            response_msg = (
                f"🚀 **Honkai: Star Rail Profile**\n\n"
                f"👤 **الاسم:** {nickname}\n"
                f"📊 **المستوى:** {level}\n"
                f"👥 **الشخصيات المتاحة:** {len(avatar_list)}\n\n"
                f"👇 **اختر الشخصية من الأزرار أدناه لعرض البيلد والريليكس:**"
            )

            await update.message.reply_text(response_msg, reply_markup=reply_markup, parse_mode='Markdown')

        except Exception as e:
            await update.message.reply_text("❌ حدث خطأ أثناء جلب البيانات من السيرفر.")

# --- المعالج المباشر عند ضغط زر الشخصية ---
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data_parts = query.data.split("_")
    if data_parts[0] == "hsr":
        uid = data_parts[1]
        char_idx = int(data_parts[2])

        await query.edit_message_text("⏳ جاري تحميل بيلد الشخصية والريليكس...")

        url = f"https://enka.network/api/hsr/uid/{uid}"
        headers = {"User-Agent": "Mozilla/5.0"}

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, headers=headers, timeout=12)
                if response.status_code != 200:
                    await query.message.reply_text("❌ تعذر جلب البيانات.")
                    return

                data = response.json()
                detail_info = data.get("detailInfo") or data.get("detailHeader") or {}
                avatar_list = detail_info.get("avatarDetailList") or data.get("avatarDetailList") or []

                if char_idx >= len(avatar_list):
                    await query.message.reply_text("❌ لم يتم العثور على بيانات الشخصية.")
                    return

                char = avatar_list[char_idx]
                avatar_id = char.get("avatarId", "غير معروف")
                char_level = char.get("level", "1")
                promotion = char.get("promotion", "0")

                # جلب السلاح (Light Cone)
                equipment = char.get("equipment", {})
                equipment_name = equipment.get("tid", "بدون سلاح")
                equipment_level = equipment.get("level", "-")

                # جلب الريليكس (Relics)
                relic_list = char.get("relicList", [])
                relics_text = ""
                
                if relic_list:
                    for i, relic in enumerate(relic_list, 1):
                        relic_level = relic.get("level", 0)
                        relics_text += f"\n  🔹 **قطعة #{i}:** مستوى +{relic_level}"
                else:
                    relics_text = "\n  ⚠️ لا توجد قطع ريليكس مجهزة."

                # رابط صورة رسمية للشخصية من Enka UI
                photo_url = f"https://enka.network/ui/hsr/SpriteOutput/AvatarRoundIcon/{avatar_id}.png"

                build_msg = (
                    f"⚔️ **Honkai: Star Rail Character Build**\n\n"
                    f"🆔 **معرف الشخصية:** {avatar_id}\n"
                    f"📈 **المستوى:** {char_level} (Ascension {promotion})\n"
                    f"🗡️ **السلاح:** {equipment_name} (Lvl {equipment_level})\n"
                    f"\n🛡️ **الريليكس المجهزة (Relics):**{relics_text}"
                )

                # إرسال الصورة الرسمية للشخصية مع التفاصيل
                try:
                    await query.message.reply_photo(photo=photo_url, caption=build_msg, parse_mode='Markdown')
                except Exception:
                    # في حال لم تتوفر الصورة الأيقونية، يُرسل النص بدلاً منها
                    await query.message.reply_text(build_msg, parse_mode='Markdown')

            except Exception as e:
                await query.message.reply_text("❌ حدث خطأ أثناء تجهيز البيلد.")

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
