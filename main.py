import os
import httpx
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
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

# --- دالة لتوليد بطاقة البيلد كصورة متكاملة باستخدام Pillow ---
async def generate_build_card_image(char_data):
    char_name = char_data.get("name", "Character")
    char_level = char_data.get("level", 1)
    
    # تفاصيل السلاح (Light Cone)
    equip = char_data.get("equip", {}) or char_data.get("equipment", {})
    lc_name = equip.get("name", "None") if isinstance(equip, dict) else "None"
    lc_level = equip.get("level", "-") if isinstance(equip, dict) else "-"

    # تفاصيل الريليكس (Relics)
    relics = char_data.get("relics", []) or char_data.get("relicList", [])

    # إنشاء خلفية بطاقة داكنة وأنيقة (أبعاد 800x450)
    card = Image.new("RGBA", (800, 450), (18, 20, 29, 255))
    draw = ImageDraw.Draw(card)

    # رسم إطار داخلي للبطاقة
    draw.rectangle([10, 10, 790, 440], outline=(60, 68, 88, 255), width=2)
    draw.rectangle([15, 15, 785, 435], outline=(35, 42, 58, 255), width=1)

    # إعداد الخطوط
    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
        font_sub = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
        font_bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
    except Exception:
        font_title = ImageFont.load_default()
        font_sub = ImageFont.load_default()
        font_bold = ImageFont.load_default()

    # كتابة الهيدر واسم الشخصية
    draw.text((30, 35), f"CHARACTER BUILD: {char_name.upper()}", font=font_title, fill=(240, 200, 110, 255))
    draw.text((30, 75), f"Level: {char_level} / 80", font=font_sub, fill=(200, 210, 225, 255))

    # قسم السلاح (Light Cone Box)
    draw.rectangle([30, 120, 380, 200], fill=(28, 34, 48, 255), outline=(70, 80, 105, 255), width=1)
    draw.text((45, 130), "LIGHT CONE (WEAPON)", font=font_bold, fill=(120, 180, 255, 255))
    draw.text((45, 160), f"{lc_name[:22]}", font=font_sub, fill=(255, 255, 255, 255))
    draw.text((300, 160), f"Lvl {lc_level}", font=font_sub, fill=(180, 220, 180, 255))

    # قسم الريليكس (Relics Section)
    draw.rectangle([30, 220, 770, 410], fill=(24, 29, 40, 255), outline=(50, 60, 80, 255), width=1)
    draw.text((45, 232), "EQUIPPED RELICS & STATS", font=font_bold, fill=(255, 180, 100, 255))

    y_pos = 270
    col_x = 45
    if relics:
        for idx, r in enumerate(relics[:6], 1):
            r_name = r.get("name", f"Relic Piece #{idx}")
            r_lvl = r.get("level", 0)
            
            draw.text((col_x, y_pos), f"• {r_name[:20]}", font=font_sub, fill=(220, 225, 235, 255))
            draw.text((col_x + 250, y_pos), f"+{r_lvl}", font=font_bold, fill=(100, 230, 150, 255))
            
            y_pos += 38
            if idx == 3:
                col_x = 420
                y_pos = 270
    else:
        draw.text((45, 280), "No Relics Equipped / Data Hidden", font=font_sub, fill=(170, 175, 185, 255))

    # حفظ الصورة في Memory Buffer
    buf = BytesIO()
    card.save(buf, format="PNG")
    buf.seek(0)
    return buf

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
                f"👇 **اختر الشخصية لعرض بطاقة البيلد المصورة:**"
            )

            await update.message.reply_text(response_msg, reply_markup=reply_markup, parse_mode='Markdown')

        except Exception as e:
            await update.message.reply_text("❌ حدث خطأ أثناء جلب البيانات من السيرفر.")

# --- المعالج عند ضغط زر الشخصية ---
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data_parts = query.data.split("_")
    if data_parts[0] == "hsr":
        uid = data_parts[1]
        char_idx = int(data_parts[2])

        await query.edit_message_text("🎨 جاري توليد صورة بطاقة البيلد...")

        url = f"https://api.mihomo.me/sr_info_parsed/{uid}?lang=en"

        async with httpx.AsyncClient() as client:
            try:
                res = await client.get(url, timeout=12)
                if res.status_code == 200:
                    data = res.json()
                    avatars = data.get("characters", []) or data.get("avatar_list", [])
                    
                    if char_idx < len(avatars):
                        char_data = avatars[char_idx]
                        
                        # توليد الصورة مباشرة بدون الاعتماد على مواقع خارجية
                        image_buf = await generate_build_card_image(char_data)
                        
                        # إرسال الصورة صافية
                        await query.message.reply_photo(photo=image_buf)
                        return

                await query.message.reply_text("❌ تعذر تحميل صورة الكارت. جرب مرة أخرى.")
            except Exception as e:
                await query.message.reply_text("❌ حدث خطأ أثناء تجهيز الصورة.")

# --- تشغيل البوت ---
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("hsr", hsr_check))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("🚀 البوت يعمل الآن بنجاح مع توليد الصور!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
