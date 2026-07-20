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

# --- دالة جلب الصور من الإنترنت ---
async def fetch_image(client, url):
    try:
        res = await client.get(url, timeout=6)
        if res.status_code == 200:
            return Image.open(BytesIO(res.content)).convert("RGBA")
    except Exception:
        pass
    return None

# --- دالة رسم بطاقة الشخصية الشاملة ---
async def create_character_card(client, char_data):
    char_name = char_data.get("name", "Character")
    char_level = char_data.get("level", 1)
    icon_path = char_data.get("icon", "")
    
    # تفاصيل السلاح
    equip = char_data.get("equip", {}) or char_data.get("equipment", {})
    lc_name = equip.get("name", "None") if isinstance(equip, dict) else "None"
    lc_level = equip.get("level", "-") if isinstance(equip, dict) else "-"

    # تفاصيل الإحصائيات (Stats)
    stats_list = char_data.get("attributes", []) or char_data.get("stats", [])

    # تفاصيل الريليكس
    relics = char_data.get("relics", []) or char_data.get("relicList", [])

    # بناء خلفية البطاقة (عالية الدقة ومنظمة)
    card = Image.new("RGBA", (1000, 600), (18, 20, 28, 255))
    draw = ImageDraw.Draw(card)

    # إطار رئيسي
    draw.rectangle([10, 10, 990, 590], outline=(65, 80, 110, 255), width=2)
    
    # قسم الشخصية (يسار)
    draw.rectangle([20, 20, 360, 580], fill=(24, 28, 38, 255), outline=(45, 60, 85, 255))

    if icon_path:
        img_url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/{icon_path}"
        avatar_img = await fetch_image(client, img_url)
        if avatar_img:
            avatar_img = avatar_img.resize((300, 300))
            card.paste(avatar_img, (40, 30), avatar_img)

    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
        font_sub = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
        font_bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
    except Exception:
        font_title = font_sub = font_bold = ImageFont.load_default()

    # اسم الشخصية والمستوى
    draw.text((40, 340), char_name.upper(), font=font_title, fill=(255, 215, 100, 255))
    draw.text((40, 380), f"Level: {char_level} / 80", font=font_sub, fill=(200, 210, 230, 255))

    # قسم السلاح (يمين علوي)
    draw.rectangle([380, 20, 980, 130], fill=(22, 28, 42, 255), outline=(50, 70, 95, 255))
    draw.text((400, 30), "LIGHT CONE (WEAPON)", font=font_bold, fill=(100, 180, 255, 255))
    draw.text((400, 60), f"{lc_name}", font=font_title, fill=(255, 255, 255, 255))
    draw.text((400, 95), f"Level: {lc_level} / 80", font=font_sub, fill=(150, 220, 150, 255))

    # قسم الريليكس والعتاد (يمين سفلي)
    draw.rectangle([380, 145, 980, 580], fill=(20, 24, 34, 255), outline=(45, 60, 85, 255))
    draw.text((400, 160), "EQUIPPED RELICS", font=font_bold, fill=(255, 165, 80, 255))

    y_offset = 200
    if relics:
        for idx, r in enumerate(relics[:6], 1):
            r_name = r.get("name", f"Relic #{idx}")
            r_lvl = r.get("level", 0)
            draw.text((400, y_offset), f"• {r_name[:32]}", font=font_sub, fill=(220, 225, 235, 255))
            draw.text((900, y_offset), f"+{r_lvl}", font=font_bold, fill=(100, 230, 150, 255))
            y_offset += 55
    else:
        draw.text((400, 220), "No Relics Equipped", font=font_sub, fill=(170, 170, 170, 255))

    buf = BytesIO()
    card.save(buf, format="PNG")
    buf.seek(0)
    return buf

# --- أمر البداية ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 **أهلاً بك يا بشار!**\n\n"
        "أدخل الـ UID لعرض قائمة شخصياتك:\n"
        "🔹 `/hsr <UID>`"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

# --- أمر فحص الحساب ---
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
            nickname = player.get("nickname", "Player")
            avatars = data.get("characters", []) or data.get("avatar_list", [])

            if not avatars:
                await update.message.reply_text("⚠️ لا توجد شخصيات معروضة في هذا الحساب.")
                return

            keyboard = []
            for idx, char in enumerate(avatars):
                char_name = char.get("name", f"شخصية #{idx + 1}")
                keyboard.append([InlineKeyboardButton(f"⚔️ {char_name}", callback_data=f"hsr_{uid}_{idx}")])

            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                f"👤 **اللاعب:** {nickname}\n👇 **اختر الشخصية لتوليد البطاقة المصورة:**", 
                reply_markup=reply_markup, 
                parse_mode='Markdown'
            )

        except Exception:
            await update.message.reply_text("❌ حدث خطأ في الاتصال بالسيرفر.")

# --- المعالج عند ضغط زر الشخصية (إرسال صورة صريحة بدون نص) ---
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data_parts = query.data.split("_")
    if data_parts[0] == "hsr":
        uid = data_parts[1]
        char_idx = int(data_parts[2])

        await query.edit_message_text("🎨 جاري رسم بطاقة البيلد المصورة...")

        url = f"https://api.mihomo.me/sr_info_parsed/{uid}?lang=en"

        async with httpx.AsyncClient() as client:
            try:
                res = await client.get(url, timeout=15)
                if res.status_code == 200:
                    data = res.json()
                    avatars = data.get("characters", []) or data.get("avatar_list", [])
                    
                    if char_idx < len(avatars):
                        char_data = avatars[char_idx]
                        
                        # توليد الصورة كملف بايتس
                        card_buf = await create_character_card(client, char_data)
                        
                        # إرسال الصورة فقط بدون أي نص (Caption) تححتها
                        await query.message.reply_photo(photo=card_buf)
                        return

                await query.message.reply_text("❌ تعذر إنشاء البطاقة.")
            except Exception:
                await query.message.reply_text("❌ حدث خطأ أثناء تجهيز الصورة.")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("hsr", hsr_check))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("🚀 البوت يعمل بنجاح!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
