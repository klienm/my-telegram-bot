import os
import re
import httpx
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

BOT_TOKEN = "8975704106:AAFZQq6zBx6cSYYR2nnEB6o4N2VvgbiAI20"

# --- قاعدة بيانات معايير البيلدات المستوحاة من Prydwen ---
CHARACTER_BUILD_STANDARDS = {
    "boothill": {
        "ideal_main": {"feet": ["speed"], "rope": ["break effect"]},
        "priority_subs": ["break effect", "speed"],
        "min_break_for_ss": 200
    },
    "silver wolf": {
        "ideal_main": {"body": ["effect hit rate"], "feet": ["speed"], "rope": ["energy regeneration rate", "break effect"]},
        "priority_subs": ["effect hit rate", "speed"],
        "min_ehr_for_ss": 97
    },
}

# --- سيرفر HTTP أساسي لضمان استجابة Render ---
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

threading.Thread(target=run_dummy_server, daemon=True).start()

async def fetch_image(client, url):
    try:
        res = await client.get(url, timeout=5)
        if res.status_code == 200:
            return Image.open(BytesIO(res.content)).convert("RGBA")
    except Exception:
        pass
    return None

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

# --- دالة تقييم البيلد تلقائياً بناءً على معايير الشخصية ---
def evaluate_build(char_name, relics):
    c_key = char_name.lower()
    standards = CHARACTER_BUILD_STANDARDS.get(c_key, None)
    
    if not standards or not relics:
        return "S"  
    
    score = 0
    
    for idx, r in enumerate(relics):
        main_stat = r.get("main_affix", {}) or r.get("mainstat", {})
        m_name = str(main_stat.get("name", "") or main_stat.get("type", "")).lower()
        
        if idx == 1 and "feet" in standards.get("ideal_main", {}):
            if any(ideal in m_name for ideal in standards["ideal_main"]["feet"]):
                score += 45
        elif idx == 4 and "rope" in standards.get("ideal_main", {}):
            if any(ideal in m_name for ideal in standards["ideal_main"]["rope"]):
                score += 45

    sub_matches = 0
    for r in relics:
        substats = r.get("sub_affix", []) or r.get("sub_affix_list", []) or r.get("substats", [])
        for sub in substats:
            s_name = str(sub.get("name", "") or sub.get("type", "")).lower()
            if any(p in s_name for p in standards.get("priority_subs", [])):
                sub_matches += 1

    if sub_matches >= 8:
        score += 35
    elif sub_matches >= 5:
        score += 25
    elif sub_matches >= 3:
        score += 15

    if score >= 80:
        return "SS"
    elif score >= 65:
        return "S"
    elif score >= 45:
        return "A"
    elif score >= 30:
        return "B"
    else:
        return "C"

# --- دالة رسم بطاقة الشخصية ---
async def create_character_card(client, char_data, player_data):
    char_name = char_data.get("name", "Character")
    char_level = char_data.get("level", 1)
    icon_path = char_data.get("icon", "")
    
    equip = char_data.get("light_cone", {})
    lc_name = equip.get("name", "None") if isinstance(equip, dict) else "None"
    lc_level = equip.get("level", "-") if isinstance(equip, dict) else "-"
    lc_icon = equip.get("icon", "") if isinstance(equip, dict) else ""

    relics = char_data.get("relics", []) or char_data.get("relicList", [])
    tier_rating = evaluate_build(char_name, relics)

    card = Image.new("RGBA", (1100, 750), (18, 20, 28, 255))
    draw = ImageDraw.Draw(card)

    draw.rectangle([10, 10, 1090, 740], outline=(65, 80, 110, 255), width=2)
    
    # --- القسم الأيسر ---
    draw.rectangle([20, 20, 420, 730], fill=(24, 28, 38, 255), outline=(45, 60, 85, 255))

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
        font_tier = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
    except Exception:
        font_title = font_sub = font_bold = font_small = font_tier = ImageFont.load_default()

    draw.text((40, 305), char_name.upper(), font=font_title, fill=(255, 215, 100, 255))
    draw.text((40, 335), f"Level: {char_level} / 80", font=font_sub, fill=(200, 210, 230, 255))

    draw.rectangle([35, 370, 405, 470], fill=(20, 24, 34, 255), outline=(50, 70, 95, 255))
    draw.text((50, 380), "LIGHT CONE", font=font_bold, fill=(100, 180, 255, 255))
    draw.text((50, 405), f"{lc_name[:25]}", font=font_bold, fill=(255, 255, 255, 255))
    draw.text((50, 435), f"Lvl: {lc_level} / 80", font=font_small, fill=(150, 220, 150, 255))

    if lc_icon:
        lc_img_url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/{lc_icon}"
        lc_img = await fetch_image(client, lc_img_url)
        if lc_img:
            lc_img = lc_img.resize((75, 75))
            card.paste(lc_img, (315, 385), lc_img)

    draw.rectangle([35, 490, 405, 610], fill=(20, 24, 34, 255), outline=(50, 70, 95, 255))
    draw.text((50, 500), "PLAYER INFO", font=font_bold, fill=(255, 165, 80, 255))
    
    p_name = player_data.get("nickname", "Unknown")
    p_uid = player_data.get("uid", "-")
    p_level = player_data.get("level", "-")
    p_eq = player_data.get("world_level", "-")

    draw.text((50, 525), f"Name: {p_name}", font=font_bold, fill=(255, 255, 255, 255))
    draw.text((50, 550), f"UID: {p_uid}", font=font_small, fill=(200, 210, 230, 255))
    draw.text((50, 570), f"Trailblaze Level: {p_level}", font=font_small, fill=(200, 210, 230, 255))
    draw.text((50, 590), f"Equilibrium Level: {p_eq}", font=font_small, fill=(200, 210, 230, 255))

    # --- القسم الأيمن ---
    draw.rectangle([440, 20, 1070, 730], fill=(20, 24, 34, 255), outline=(45, 60, 85, 255))
    draw.text((460, 35), "EQUIPPED RELICS & STATS", font=font_title, fill=(255, 165, 80, 255))

    # صندوق تقييم البيلد (Tier Score Badge)
    draw.rectangle([980, 25, 1055, 65], fill=(30, 40, 60, 255), outline=(100, 180, 255, 255))
    draw.text((995, 33), tier_rating, font=font_tier, fill=(255, 215, 100, 255))

    if relics:
        for idx, r in enumerate(relics[:6]):
            col = idx % 2
            row = idx // 2
            
            box_x1 = 455 + (col * 305)
            box_y1 = 75 + (row * 125)
            box_x2 = box_x1 + 295
            box_y2 = box_y1 + 115
            
            r_name = r.get("name", f"Relic #{idx+1}")
            r_lvl = r.get("level", 0)
            r_icon = r.get("icon", "")
            
            draw.rectangle([box_x1, box_y1, box_x2, box_y2], fill=(26, 31, 43, 255), outline=(55, 75, 100, 255))
            
            if r_icon:
                r_img_url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/{r_icon}"
                r_img = await fetch_image(client, r_img_url)
                if r_img:
                    r_img = r_img.resize((55, 55))
                    card.paste(r_img, (box_x1 + 10, box_y1 + 10), r_img)

            draw.text((box_x1 + 75, box_y1 + 10), f"{r_name[:16]}", font=font_bold, fill=(230, 235, 245, 255))
            draw.text((box_x2 - 25, box_y1 + 10), f"+{r_lvl}", font=font_bold, fill=(100, 230, 150, 255))

            main_stat = r.get("main_affix", {}) or r.get("mainstat", {})
            m_name = main_stat.get("name", "") or main_stat.get("type", "")
            m_display = main_stat.get("display", "") 
            
            if not m_display:
                m_display = format_stat_value(m_name, main_stat.get("value", ""), is_planar=(idx in [4, 5]))
                
            if m_name:
                draw.text((box_x1 + 75, box_y1 + 30), f"Main: {m_name} ({m_display})", font=font_small, fill=(255, 215, 100, 255))

            substats = r.get("sub_affix", []) or r.get("sub_affix_list", []) or r.get("substats", [])
            
            for i, sub in enumerate(substats[:4]): 
                s_name = sub.get("name", "") or sub.get("type", "") or sub.get("field", "")
                s_display = sub.get("display", "")
                
                if not s_display:
                    s_display = format_stat_value(s_name, sub.get("value", ""))
                
                if s_name and s_display:
                    short_name = str(s_name).replace("_", " ")[:10]
                    stat_text = f"{short_name}: {s_display}"
                    
                    sub_col_x = box_x1 + 75 if i % 2 == 0 else box_x1 + 185 
                    sub_row_y = box_y1 + 55 if i < 2 else box_y1 + 75 
                    
                    draw.text((sub_col_x, sub_row_y), stat_text, font=font_small, fill=(170, 185, 205, 255))
            
            if not substats:
                draw.text((box_x1 + 75, box_y1 + 55), "No Substats recorded", font=font_small, fill=(170, 170, 170, 255))
    else:
        draw.text((470, 120), "No Relics Equipped", font=font_sub, fill=(170, 170, 170, 255))

    # صندوق تأثيرات الأطقم
    effects_y = 455
    draw.rectangle([455, effects_y, 1055, 715], fill=(22, 27, 37, 255), outline=(50, 70, 95, 255))
    draw.text((470, effects_y + 10), "ACTIVE SET EFFECTS", font=font_title, fill=(255, 165, 80, 255))
    
    relic_sets = char_data.get("relic_sets", [])
    set_y = effects_y + 45
    
    for r_set in relic_sets:
        s_name = r_set.get("name", "Unknown Set")
        s_num = r_set.get("num", 2)
        raw_desc = r_set.get("desc", "")
        
        clean_desc = re.sub(r'<[^>]+>', '', str(raw_desc)).replace("\n", " ")
        
        draw.text((470, set_y), f"[{s_num}-Pc] {s_name}", font=font_bold, fill=(100, 230, 150, 255))
        set_y += 20
        
        words = clean_desc.split(" ")
        line = ""
        for word in words:
            test_line = line + word + " "
            try:
                text_width = draw.textlength(test_line, font=font_small)
            except AttributeError:
                text_width = len(test_line) * 6
                
            if text_width < 560:
                line = test_line
            else:
                draw.text((470, set_y), line, font=font_small, fill=(200, 210, 230, 255))
                set_y += 15
                line = word + " "
        
        if line:
            draw.text((470, set_y), line, font=font_small, fill=(200, 210, 230, 255))
            set_y += 20
            
        if set_y > 685:
            break

    buf = BytesIO()
    card.save(buf, format="PNG")
    buf.seek(0)
    return buf

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 **أهلاً بك!**\n\n"
        "أدخل الـ UID لعرض قائمة شخصياتك مع تقييم البيلدات التلقائي:\n"
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
                f"👤 **اللاعب:** {nickname}\n👇 **اختر الشخصية لتوليد البطاقة مع التقييم:**", 
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

        await query.edit_message_text("🎨 جاري تحليل البيلد وتقييمه ورسم البطاقة...")

        url = f"https://api.mihomo.me/sr_info_parsed/{uid}?lang=en"

        async with httpx.AsyncClient() as client:
            try:
                res = await client.get(url, timeout=15)
                if res.status_code == 200:
                    data = res.json()
                    player_data = data.get("player", {})
                    avatars = data.get("characters", []) or data.get("avatar_list", [])
                    
                    if char_idx < len(avatars):
                        char_data = avatars[char_idx]
                        card_buf = await create_character_card(client, char_data, player_data)
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

    print("🚀 البوت يعمل بنجاح مع نظام التقييم!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
