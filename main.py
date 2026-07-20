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
        "👋 **أهلاً بك يا بشار!**\n\n"
        "عرض بيلدات وإحصائيات شخصيات Honkai: Star Rail بالصور المباشرة!\n\n"
        "🔹 `/hsr <UID>` - لفحص الحساب واختيار الشخصية"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

# --- أمر Honkai: Star Rail ---
async def hsr_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ يرجى كتابة الـ UID بعد الأمر!\nمثال: `/hsr 701021140`", parse_mode='Markdown')
        return

    uid = context.args[0]
    await update.message.reply_text("⏳ جاري جلب بيانات الحساب...")

    url = f"https://api.mihomo.me/sr_info_parsed/{uid}?lang=en"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, timeout=15)
            if response.status_code != 200:
                await update.message.reply_text("❌ لم يتم العثور على الحساب. تأكد من صحة الـ UID.")
                return

            data = response.json()
            player = data.get("player", {})
            nickname = player.get("nickname", "لاعب")
            level = player.get("level", "-")
            
            avatars = data.get("characters") or data.get("avatar_list") or []

            if not avatars:
                await update.message.reply_text(
                    f"👤 **الاسم:** {nickname}\n📊 **المستوى:** {level}\n\n"
                    "⚠️ *لم يتم العثور على شخصيات معروضة.*\nتأكد من إظهار الشخصيات والتفاصيل داخل اللعبة."
                )
                return

            keyboard = []
            for idx, char in enumerate(avatars):
                char_name = char.get("name", f"شخصية #{idx + 1}")
                keyboard.append([InlineKeyboardButton(f"⚔️ {char_name}", callback_data=f"hsr_{uid}_{idx}")])

            reply_markup = InlineKeyboardMarkup(keyboard)

            response_msg = (
                f"🚀 **Honkai: Star Rail Profile**\n\n"
                f"👤 **الاسم:** {nickname}\n"
                f"📊 **المستوى:** {level}\n"
                f"👥 **الشخصيات المتاحة:** {len(avatars)}\n\n"
                f"👇 **اختر الشخصية لإرسال صورتها وبيلدها:**"
            )

            await update.message.reply_text(response_msg, reply_markup=reply_markup, parse_mode='Markdown')

        except Exception as e:
            await update.message.reply_text("❌ حدث خطأ في الاتصال بالسيرفر.")

# --- المعالج عند ضغط زر الشخصية لإرسال الصورة مباشرة ---
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data_parts = query.data.split("_")
    if data_parts[0] == "hsr":
        uid = data_parts[1]
        char_idx = int(data_parts[2])

        await query.edit_message_text("🎨 جاري سحب صورة الشخصية وتجهيز البيلد...")

        url = f"https://api.mihomo.me/sr_info_parsed/{uid}?lang=en"

        async with httpx.AsyncClient() as client:
            try:
                res = await client.get(url, timeout=15)
                if res.status_code == 200:
                    data = res.json()
                    avatars = data.get("characters") or data.get("avatar_list") or []
                    
                    if char_idx < len(avatars):
                        char = avatars[char_idx]
                        char_name = char.get("name", "غير معروف")
                        char_level = char.get("level", 1)
                        
                        # رابط صورة الشخصية الرسمية (Splash Art) من مستودع اللعبة المباشر
                        icon_path = char.get("icon", "")
                        image_url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/{icon_path}" if icon_path else None

                        # السلاح
                        equip = char.get("equip") or char.get("equipment") or {}
                        lc_name = equip.get("name", "بدون سلاح") if isinstance(equip, dict) else "بدون سلاح"
                        lc_level = equip.get("level", "-") if isinstance(equip, dict) else "-"

                        # الريليكس
                        relics = char.get("relics") or char.get("relicList") or []
                        relics_text = ""
                        if relics:
                            for i, r in enumerate(relics, 1):
                                r_name = r.get("name", f"قطعة #{i}")
                                r_lvl = r.get("level", 0)
                                relics_text += f"\n  🔹 {r_name} (+{r_lvl})"
                        else:
                            relics_text = "\n  ⚠️ لا توجد قطع مجهزة."

                        caption = (
                            f"⚔️ **{char_name} Build**\n\n"
                            f"📈 **المستوى:** {char_level} / 80\n"
                            f"🗡️ **السلاح:** {lc_name} (Lvl {lc_level})\n"
                            f"\n🛡️ **الريليكس:**{relics_text}"
                        )

                        # إرسال الصورة مباشرة مع التفاصيل كـ Caption
                        if image_url:
                            await query.message.reply_photo(photo=image_url, caption=caption, parse_mode='Markdown')
                        else:
                            await query.message.reply_text(caption, parse_mode='Markdown')
                        return

                await query.message.reply_text("❌ تعذر جلب بيانات الشخصية.")
            except Exception as e:
                await query.message.reply_text("❌ حدث خطأ أثناء إرسال الصورة.")

# --- تشغيل البوت ---
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("hsr", hsr_check))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("🚀 البوت يعمل بنجاح!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
