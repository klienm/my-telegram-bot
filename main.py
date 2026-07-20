import os
import httpx
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

BOT_TOKEN = "8975704106:AAFZQq6zBx6cSYYR2nnEB6o4N2VvgbiAI20"

# --- سيرفر HTTP أساسي لضمان استجابة Render وعدم إغلاق التطبيق ---
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Running Live!")

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    print(f"🌐 Dummy server running on port {port}")
    server.serve_forever()

# تشغيل السيرفر في الخلفية
threading.Thread(target=run_dummy_server, daemon=True).start()

# --- دالة جلب الصور من الإنترنت بأمان ---
async def fetch_image(client, url):
    try:
        res = await client.get(url, timeout=5)
        if res.status_code == 200:
            return Image.open(BytesIO(res.content)).convert("RGBA")
    except Exception:
        pass
    return None

# --- دالة لتنسيق الأرقام في حال لم تكن جاهزة ---
def format_stat_value(name, val, is_planar=False):
    try:
        f_val = float(val)
        if is_planar and f_val < 5.0:
            f_val = f_val * 100
        elif any(k in str(name).lower() for k in ["rate", "dmg", "chance", "percent", "boost", "hp_", "atk_", "def_", "%"]) and f_val < 3.0:
            f_val = f_val * 100
        return str(int(round(f_val)))
    except Exception:
        return str(val)

# --- دالة رسم بطاقة الشخصية الشاملة والدقيقة ---
async def create_character_card(client, char_data):
    char_name = char_data.get("name", "Character")
    char_level = char_data.get("level", 1)
    icon_path = char_data.get("icon", "")
    
    equip = char_data.get("equip", {}) or char_data.get("equipment", {})
    lc_name = equip.get("name", "None") if isinstance(equip, dict) else "None"
    lc_level = equip.get("level", "-") if isinstance(equip, dict) else "-"
    lc_icon = equip.get("icon", "") if isinstance(equip, dict) else ""

    relics = char_data.get("relics", []) or char_data.get("relicList", [])

    card = Image.new("RGBA", (1100, 750), (18, 20, 28, 255))
    draw = ImageDraw.Draw(card)

    draw.rectangle([10, 10, 1090, 740], outline=(65, 80, 110, 255), width=2)
    draw.rectangle([20, 20, 420, 730], fill=(24, 28, 38, 255), outline=(45, 60, 85, 255))

    # صورة الشخصية
    if icon_path:
        img_url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/{icon_path}"
        avatar_img = await fetch_image(client, img_url)
        if avatar_img:
            avatar_img = avatar_img.resize((260, 260))
            card.paste(avatar_img, (50, 30), avatar_img)

    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
        font_sub = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        font_bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
    except Exception:
        font_title = font_sub = font_bold = font_small = ImageFont.load_default()

    draw.text((40, 305), char_name.upper(), font=font_title, fill=(255, 215, 100, 255))
    draw.text((40, 335), f"Level: {char_level} / 80", font=font_sub, fill=(200, 210, 230, 255))

    # قسم السلاح (Light Cone) مع الصورة
    draw.rectangle([35, 370, 405, 470], fill=(20, 24, 34, 255), outline=(50, 70, 95, 255))
    draw.text((50, 380), "LIGHT CONE", font=font_bold, fill=(100, 180, 255, 255))
    draw.text((50, 405), f"{lc_name[:25]}", font=font_bold, fill=(255, 255, 255, 255))
    draw.text((50, 435), f"Lvl: {lc_level} / 80", font=font_small, fill=(150, 220, 150, 255))

    # جلب ووضع صورة السلاح (Light Cone)
    if lc_icon:
        lc_img_url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/{lc_icon}"
        lc_img = await fetch_image(client, lc_img_url)
        if lc_img:
            lc_img = lc_img.resize((80, 80)) # تصغير صورة السلاح لتناسب المربع
            card.paste(lc_img, (310, 380), lc_img)

    draw.rectangle([440, 20, 1070, 730], fill=(20, 24, 34, 255), outline=(45, 60, 85, 255))
    draw.text((460, 35), "EQUIPPED RELICS & STATS", font=font_title, fill=(255, 165, 80, 255))

    y_offset = 75
    if relics:
        for idx, r in enumerate(relics[:6], 1):
            r_name = r.get("name", f"Relic #{idx}")
            r_lvl = r.get("level", 0)
            r_icon = r.get("icon", "")
            
            draw.rectangle([455, y_offset, 1055, y_offset + 95], fill=(26, 31, 43, 255), outline=(55, 75, 100, 255))
            
            if r_icon:
                r_img_url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/{r_icon}"
                r_img = await fetch_image(client, r_img_url)
                if r_img:
                    r_img = r_img.resize((70, 70))
                    card.paste(r_img, (470, y_offset + 12), r_img)

            draw.text((550, y_offset + 10), f"{r_name[:32]}", font=font_bold, fill=(230, 235, 245, 255))
            draw.text((980, y_offset + 10), f"+{r_lvl}", font=font_bold, fill=(100, 230, 150, 255))

            main_stat = r.get("main_affix", {}) or r.get("mainstat", {})
            m_name = main_stat.get("name", "") or main_stat.get("type", "")
            m_display = main_stat.get("display", "") 
            
            if not m_display:
                m_display = format_stat_value(m_name, main_stat.get("value", ""), is_planar=(idx in [5, 6]))
                
            if m_name:
                draw.text((550, y_offset + 30), f"Main: {m_name} ({m_display})", font=font_small, fill=(255, 215, 100, 255))

            # --- ترتيب السبستاتس على شكل شبكة 2x2 ---
            substats = r.get("sub_affix", []) or r.get("sub_affix_list", []) or r.get("substats", [])
            
            for i, sub in enumerate(substats[:4]): # نأخذ أول 4 سبستاتس بحد أقصى
                s_name = sub.get("name", "") or sub.get("type", "") or sub.get("field", "")
                s_display = sub.get("display", "")
                
                if not s_display:
                    s_display = format_stat_value(s_name, sub.get("value", ""))
                
                if s_name and s_display:
                    # تنظيف الاسم وتجهيز النص
                    short_name = str(s_name).replace("_", " ")[:12]
                    stat_text = f"{short_name}: {s_display}"
                    
                    # حساب الإحداثيات (الأعمدة والصفوف)
                    col_x = 550 if i % 2 == 0 else 780 # العمود الأول عند 550، الثاني عند 780
                    row_y = y_offset + 55 if i < 2 else y_offset + 75 # الصف الأول عند 55، الثاني عند 75
                    
                    draw.text((col_x, row_y), stat_text, font=font_small, fill=(170, 185, 205, 255))
            
            if not substats:
                draw.text((550, y_offset + 55), "No Substats recorded", font=font_small, fill=(170, 185, 205, 255))
            
            y_offset += 105
    else:
        draw.text((470, 120), "No Relics Equipped in this Slot", font=font_sub, fill=(170, 170, 170, 255))

    buf = BytesIO()
    card.save(buf, format="PNG")
    buf.seek(0)
    return buf

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 **أهلاً بك!**\n\n"
        "أدخل الـ UID لعرض قائمة شخصياتك بدقة:\n"
        "🔹 `/hsr <UID>`"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

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
                f"👤 **اللاعب:** {nickname}\n👇 **اختر الشخصية لتوليد البطاقة المفصلة:**", 
                reply_markup=reply_markup, 
                parse_mode='Markdown'
            )

        except Exception:
            await update.message.reply_text("❌ حدث خطأ في الاتصال بالسيرفر.")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data_parts = query.data.split("_")
    if data_parts[0] == "hsr":
        uid = data_parts[1]
        char_idx = int(data_parts[2])

        await query.edit_message_text("🎨 جاري رسم بطاقة البيلد المفصلة بالقطع والسبستاتس...")

        url = f"https://api.mihomo.me/sr_info_parsed/{uid}?lang=en"

        async with httpx.AsyncClient() as client:
            try:
                res = await client.get(url, timeout=15)
                if res.status_code == 200:
                    data = res.json()
                    avatars = data.get("characters", []) or data.get("avatar_list", [])
                    
                    if char_idx < len(avatars):
                        char_data = avatars[char_idx]
                        card_buf = await create_character_card(client, char_data)
                        await query.message.reply_photo(photo=card_buf)
                        return

                await query.message.reply_text("❌ تعذر إنشاء البطاقة.")
            except Exception as e:
                print(f"Error generating card: {e}")
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
