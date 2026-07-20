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

# --- دالة جلب صورة من رابط ---
async def fetch_image(client, url):
    try:
        res = await client.get(url, timeout=5)
        if res.status_code == 200:
            return Image.open(BytesIO(res.content)).convert("RGBA")
    except Exception:
        pass
    return None

# --- دالة رسم بطاقة البيلد المصورة ---
async def create_character_card(client, char_data):
    char_name = char_data.get("name", "Character")
    char_level = char_data.get("level", 1)
    icon_path = char_data.get("icon", "")
    
    # تفاصيل السلاح
    equip = char_data.get("equip", {}) or char_data.get("equipment", {})
    lc_name = equip.get("name", "None") if isinstance(equip, dict) else "None"
    lc_level = equip.get("level", "-") if isinstance(equip, dict) else "-"
    lc_icon = equip.get("icon", "") if isinstance(equip, dict) else ""

    # تفاصيل الريليكس
    relics = char_data.get("relics", []) or char_data.get("relicList", [])

    # خلفية البطاقة
    card = Image.new("RGBA", (900, 500), (15, 18, 26, 255))
    draw = ImageDraw.Draw(card)

    # إطارات وحواف
    draw.rectangle([10, 10, 890, 490], outline=(60, 70, 95, 255), width=2)
    draw.rectangle([20, 20, 320, 480], fill=(22, 27, 38, 255), outline=(40, 50, 70, 255))

    # جلب وصورة الشخصية
    if icon_path:
        img_url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/{icon_path}"
        avatar_img = await fetch_image(client, img_url)
        if avatar_img:
            avatar_img = avatar_img.resize((260, 260))
            card.paste(avatar_img, (40, 30), avatar_img)

    # النصوص الأساسية
    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
        font_sub = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
        font_bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
    except Exception:
        font_title = font_sub = font_bold = ImageFont.load_default()

    # اسم الشخصية والمستوى
    draw.text((40, 310), char_name.upper(), font=font_title, fill=(245, 200, 100, 255))
    draw.text((40, 350), f"Level: {char_level} / 80", font=font_sub, fill=(200, 210, 225, 255))

    # قسم السلاح (Light Cone)
    draw.rectangle([340, 20, 880, 140], fill=(22, 28, 42, 255), outline=(50, 65, 90, 255))
    draw.text((360, 30), "LIGHT CONE (WEAPON)", font=font_bold, fill=(100, 180, 255, 255))
    draw.text((360, 65), f"{lc_name}", font=font_title, fill=(255, 255, 255, 255))
    draw.text((360, 100), f"Level: {lc_level} / 80", font=font_sub, fill=(150, 220, 150, 255))

    # قسم الريليكس (Relics)
    draw.rectangle([340, 155, 880, 480], fill=(20, 24, 35, 255), outline=(45, 55, 75, 255))
    draw.text((360, 170), "EQUIPPED RELICS", font=font_bold, fill=(255, 170, 90, 255))

    y_offset = 210
    if relics:
        for idx, r in enumerate(relics[:6], 1):
            r_name = r.get("name", f"Relic #{idx}")
            r_lvl = r.get("level", 0)
            
            draw.text((360, y_offset), f"• {r_name[:28]}", font=font_sub, fill=(220, 225, 235, 255))
            draw.text((780, y_offset), f"+{r_lvl}", font=font_bold, fill=(100, 230, 150, 255))
            y_offset += 42
    else:
        draw.text((360, 220), "No Relics Equipped", font=font_sub, fill=(170, 170, 170, 255))

    buf = BytesIO()
    card.save(buf, format="PNG")
    buf.seek(0)
    return buf

# --- أمر البداية ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 **أهلاً بك!**\n\n"
        "عرض بطاقات البيلد المصورة لـ Honkai: Star Rail!\n\n"
        "🔹 `/hsr <UID>` - لفحص الحساب واختيار الشخصية"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

# --- أمر HSR ---
async def hsr_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ يرجى كتابة الـ UID بعد الأمر!\nمثال: `/hsr 800000000`", parse_mode='Markdown')
        return

    uid = context.args[0]
    await update.message.reply_text("⏳ جاري جلب البيانات...")

    url = f"https://api.mihomo.me/sr_info_parsed/{uid}?lang=en"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, timeout=12)
            if response.status_code != 200:
                await update.message.reply_text("❌ لم يتم العثور على الحساب.")
                return

            data = response.json()
            player = data.get("player", {})
            nickname = player.get("nickname", "Player")
            avatars = data.get("characters", []) or data.get("avatar_list", [])

            if not avatars:
                await update.message.reply_text("⚠️ لا توجد شخصيات معروضة بالحساب.")
                return

            keyboard = []
            for idx, char in enumerate(avatars):
                char_name = char.get("name", f"شخصية #{idx + 1}")
                keyboard.append([InlineKeyboardButton(f"⚔️ {char_name}", callback_data=f"hsr_{uid}_{idx}")])

            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(f"👤 **اللاعب:** {nickname}\n👇 **اختر الشخصية لتوليد البطاقة:**", reply_markup=reply_markup, parse_mode='Markdown')

        except Exception:
            await update.message.reply_text("❌ حدث خطأ أثناء جلب البيانات.")

# --- عند ضغط زر الشخصية ---
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data_parts = query.data.split("_")
    if data_parts[0] == "hsr":
        uid = data_parts[1]
        char_idx = int(data_parts[2])

        await query.edit_message_text("🎨 جاري تصميم ورسم بطاقة البيلد...")

        url = f"https://api.mihomo.me/sr_info_parsed/{uid}?lang=en"

        async with httpx.AsyncClient() as client:
            try:
                res = await client.get(url, timeout=12)
                if res.status_code == 200:
                    data = res.json()
                    avatars = data.get("characters", []) or data.get("avatar_list", [])
                    
                    if char_idx < len(avatars):
                        char_data = avatars[char_idx]
                        
                        # توليد الصورة مرسومة بالكامل
                        card_buf = await create_character_card(client, char_data)
                        
                        await query.message.reply_photo(photo=card_buf)
                        return

                await query.message.reply_text("❌ تعذر إنشاء الصورة.")
            except Exception:
                await query.message.reply_text("❌ حدث خطأ أثناء تجهيز البطاقة.")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("hsr", hsr_check))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("🚀 البوت يعمل الآن بنجاح مع توليد الصور المباشرة!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
