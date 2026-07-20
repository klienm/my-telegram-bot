import os
import re
import httpx
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN")

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

def resize_cover(img, target_w, target_h, focus_y=0.15):
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w, new_h = max(1, round(src_w * scale)), max(1, round(src_h * scale))
    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    left = (new_w - target_w) // 2
    top = int((new_h - target_h) * focus_y)
    top = max(0, min(top, new_h - target_h))

    return img.crop((left, top, left + target_w, top + target_h))

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

# ذاكرة تخزين مؤقت لتجنب تكرار تحميل الأيقونات وتخفيف العبء عن الشبكة
icon_cache = {}

async def get_cached_icon(client, icon_path, size=None):
    if not icon_path:
        return None
    cache_key = (icon_path, size)
    if cache_key in icon_cache:
        return icon_cache[cache_key]
    
    img_url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/{icon_path}"
    img = await fetch_image(client, img_url)
    if img:
        if size:
            img = img.resize(size, Image.Resampling.LANCZOS)
        icon_cache[cache_key] = img
        return img
    return None

# --- دالة رسم بطاقة الشخصية الجديدة ---
async def create_character_card(client, char_data, player_data):
    char_name = char_data.get("name", "Character")
    char_level = char_data.get("level", 1)
    char_id = str(char_data.get("id", ""))
    icon_path = char_data.get("icon", "")

    equip = char_data.get("light_cone", {}) or {}
    lc_name = equip.get("name", "None")
    lc_level = equip.get("level", "-")
    lc_icon = equip.get("icon", "")

    relics = char_data.get("relics", []) or char_data.get("relicList", []) or []

    # أبعاد الكارد الجديدة لتناسب الأقسام الأربعة بطريقة مرتبة
    card = Image.new("RGBA", (1600, 800), (18, 20, 28, 255))
    draw = ImageDraw.Draw(card)

    # إطار خارجي رفيع للكارد
    draw.rectangle([15, 15, 1585, 785], outline=(65, 80, 110, 255), width=2)

    try:
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
        font_bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
        font_sub = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
    except Exception:
        font_large = font_title = font_bold = font_sub = font_small = ImageFont.load_default()

    # جلب صورة السبلاش آرت مسبقاً لعرضها في القسم الرابع
    splash_img = None
    if char_id:
        portrait_url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/image/character_portrait/{char_id}.png"
        splash_img = await fetch_image(client, portrait_url)

    if not splash_img and icon_path:
        splash_icon = icon_path.replace("icon/character", "image/character_portrait").replace("steps/", "")
        img_url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/{splash_icon}"
        splash_img = await fetch_image(client, img_url)

    if not splash_img and icon_path:
        img_url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/{icon_path}"
        splash_img = await fetch_image(client, img_url)

    # تجميع وحساب الستات (Stats) الكاملة للشخصية
    all_stats = {}
    for stat in char_data.get("attributes", []) + char_data.get("properties", []):
        field = stat.get("field", "")
        name = stat.get("name", "")
        icon = stat.get("icon", "")
        val_str = stat.get("display", "")
        if not val_str:
            val_str = format_stat_value(name, stat.get("value", 0))
        if name:
            all_stats[field] = {
                "name": name,
                "value": val_str,
                "icon": icon
            }

    # تحديد ترتيب عرض الستات في الكارد
    stat_order = [
        "hp", "atk", "def", "spd",
        "crit_rate", "crit_dmg",
        "break_effect", "sp_rate",
        "effect_hit", "effect_res",
        "heal_rate"
    ]

    rendered_stats = []
    seen_fields = set()
    for field in stat_order:
        if field in all_stats:
            rendered_stats.append(all_stats[field])
            seen_fields.add(field)

    # إضافة أي ستات أخرى متبقية (مثل زيادة الضرر العنصري) طالما قيمتها غير صفرية
    for field, stat in all_stats.items():
        if field not in seen_fields:
            val = stat["value"]
            if val not in ["0", "0%", "0.0%", "0.0"]:
                rendered_stats.append(stat)
                seen_fields.add(field)


    # ==================== القسم الأول (أقصى اليسار): الريليكس الستة ====================
    draw.text((25, 35), "EQUIPPED RELICS", font=font_title, fill=(255, 165, 80, 255))
    
    for idx, r in enumerate(relics[:6]):
        box_y1 = 75 + (idx * 116)
        box_y2 = box_y1 + 108
        box_x1 = 20
        box_x2 = 360
        
        draw.rectangle([box_x1, box_y1, box_x2, box_y2], fill=(22, 27, 37, 255), outline=(50, 70, 95, 255))
        
        r_name = r.get("name", f"Relic #{idx+1}")
        r_lvl = r.get("level", 0)
        r_icon = r.get("icon", "")
        
        if r_icon:
            r_img = await get_cached_icon(client, r_icon, (50, 50))
            if r_img:
                card.paste(r_img, (box_x1 + 8, box_y1 + 8), r_img)
                
        draw.text((box_x1 + 65, box_y1 + 10), f"{r_name[:16]}", font=font_bold, fill=(230, 235, 245, 255))
        draw.text((box_x2 - 35, box_y1 + 10), f"+{r_lvl}", font=font_bold, fill=(100, 230, 150, 255))
        
        main_stat = r.get("main_affix", {}) or r.get("mainstat", {})
        m_name = main_stat.get("name", "") or main_stat.get("type", "")
        m_display = main_stat.get("display", "")
        if not m_display:
            m_display = format_stat_value(m_name, main_stat.get("value", ""), is_planar=(idx in [4, 5]))
            
        if m_name:
            draw.text((box_x1 + 65, box_y1 + 28), f"Main: {m_name} ({m_display})", font=font_small, fill=(255, 215, 100, 255))
            
        substats = r.get("sub_affix", []) or r.get("sub_affix_list", []) or r.get("substats", [])
        for i, sub in enumerate(substats[:4]):
            s_name = sub.get("name", "") or sub.get("type", "") or sub.get("field", "")
            s_display = sub.get("display", "")
            if not s_display:
                s_display = format_stat_value(s_name, sub.get("value", ""))
                
            if s_name and s_display:
                short_name = str(s_name).replace("_", " ")[:10]
                stat_text = f"{short_name}: {s_display}"
                
                sub_col_x = box_x1 + 65 if i % 2 == 0 else box_x1 + 185
                sub_row_y = box_y1 + 52 if i < 2 else box_y1 + 72
                draw.text((sub_col_x, sub_row_y), stat_text, font=font_small, fill=(170, 185, 205, 255))


    # ==================== القسم الثاني (الوسط اليسار): الستات وتأثير المجموعات ====================
    draw.text((385, 35), "CHARACTER STATS", font=font_title, fill=(255, 165, 80, 255))
    
    # صندوق الإحصائيات (الستات)
    draw.rectangle([380, 75, 720, 440], fill=(22, 27, 37, 255), outline=(50, 70, 95, 255))
    
    stat_y = 85
    for stat in rendered_stats[:8]:  # عرض أول 8 ستات أساسية لتناسب حجم الصندوق
        s_name = stat["name"]
        s_val = stat["value"]
        s_icon = stat["icon"]
        
        if s_icon:
            s_img = await get_cached_icon(client, s_icon, (24, 24))
            if s_img:
                card.paste(s_img, (390, stat_y), s_img)
                
        draw.text((422, stat_y + 3), s_name, font=font_bold, fill=(200, 210, 230, 255))
        
        try:
            val_width = draw.textlength(s_val, font=font_bold)
        except AttributeError:
            val_width = len(s_val) * 8
        draw.text((710 - val_width, stat_y + 3), s_val, font=font_bold, fill=(255, 215, 100, 255))
        
        draw.line([(390, stat_y + 35), (710, stat_y + 35)], fill=(255, 255, 255, 15), width=1)
        stat_y += 42
        
    # صندوق تأثيرات المجموعات (Relic Sets)
    draw.rectangle([380, 455, 720, 775], fill=(22, 27, 37, 255), outline=(50, 70, 95, 255))
    draw.text((395, 465), "ACTIVE SET EFFECTS", font=font_bold, fill=(255, 165, 80, 255))
    
    relic_sets = char_data.get("relic_sets", [])
    set_y = 495
    for r_set in relic_sets:
        s_name = r_set.get("name", "Unknown Set")
        s_num = r_set.get("num", 2)
        raw_desc = r_set.get("desc", "")
        clean_desc = re.sub(r'<[^>]+>', '', str(raw_desc)).replace("\n", " ")
        
        draw.text((395, set_y), f"[{s_num}-Pc] {s_name}", font=font_bold, fill=(100, 230, 150, 255))
        set_y += 18
        
        words = clean_desc.split(" ")
        line = ""
        for word in words:
            test_line = line + word + " "
            try:
                text_width = draw.textlength(test_line, font=font_small)
            except AttributeError:
                text_width = len(test_line) * 6
                
            if text_width < 310:
                line = test_line
            else:
                draw.text((395, set_y), line, font=font_small, fill=(180, 195, 215, 255))
                set_y += 14
                line = word + " "
        if line:
            draw.text((395, set_y), line, font=font_small, fill=(180, 195, 215, 255))
            set_y += 18
            
        if set_y > 750:
            break


    # ==================== القسم الثالث (الوسط اليمين): المهارات والسلاح ====================
    draw.text((745, 35), "TRACES & EQUIPMENT", font=font_title, fill=(255, 165, 80, 255))
    
    # صندوق المهارات (Traces / Skills)
    draw.rectangle([740, 75, 1080, 440], fill=(22, 27, 37, 255), outline=(50, 70, 95, 255))
    draw.text((755, 85), "CHARACTER SKILLS (TRACES)", font=font_bold, fill=(255, 165, 80, 255))
    
    skills = char_data.get("skills", []) or []
    skill_y = 115
    for skill in skills[:5]:  # عرض حتى 5 مهارات أساسية
        sk_name = skill.get("name", "Skill")
        sk_level = skill.get("level", 1)
        sk_max = skill.get("max_level", 10)
        sk_icon = skill.get("icon", "")
        sk_type = skill.get("type_text", "") or skill.get("tag", "Trace")
        
        if sk_icon:
            sk_img = await get_cached_icon(client, sk_icon, (32, 32))
            if sk_img:
                card.paste(sk_img, (755, skill_y), sk_img)
                
        draw.text((795, skill_y - 2), sk_name[:20], font=font_bold, fill=(255, 255, 255, 255))
        draw.text((795, skill_y + 16), f"{sk_type}  •  Lv. {sk_level}/{sk_max}", font=font_small, fill=(150, 200, 255, 255))
        
        skill_y += 52
        
    # صندوق السلاح (Light Cone)
    draw.rectangle([740, 455, 1080, 775], fill=(22, 27, 37, 255), outline=(50, 70, 95, 255))
    draw.text((755, 465), "EQUIPPED LIGHT CONE", font=font_bold, fill=(255, 165, 80, 255))
    
    if lc_icon:
        lc_img = await get_cached_icon(client, lc_icon, (70, 70))
        if lc_img:
            card.paste(lc_img, (755, 495), lc_img)
            
    draw.text((835, 495), f"{lc_name[:24]}", font=font_bold, fill=(255, 255, 255, 255))
    draw.text((835, 520), f"Lv. {lc_level} / 80", font=font_sub, fill=(150, 220, 150, 255))
    
    # عرض إحصائيات السلاح الأساسية (Base Stats)
    lc_attrs = equip.get("attributes", []) or []
    lc_stat_y = 575
    for attr in lc_attrs[:3]:
        a_name = attr.get("name", "")
        a_val = attr.get("display", str(attr.get("value", "")))
        a_icon = attr.get("icon", "")
        
        if a_icon:
            a_img = await get_cached_icon(client, a_icon, (18, 18))
            if a_img:
                card.paste(a_img, (755, lc_stat_y), a_img)
                
        draw.text((780, lc_stat_y + 1), f"Base {a_name}:", font=font_small, fill=(170, 185, 205, 255))
        draw.text((920, lc_stat_y + 1), str(a_val), font=font_small, fill=(255, 255, 255, 255))
        lc_stat_y += 24


    # ==================== القسم الرابع (أقصى اليمين): السبلاش آرت والأسماء ====================
    RIGHT_X1, RIGHT_Y1, RIGHT_X2, RIGHT_Y2 = 1100, 20, 1580, 780
    RIGHT_W, RIGHT_H = RIGHT_X2 - RIGHT_X1, RIGHT_Y2 - RIGHT_Y1
    
    draw.rectangle([RIGHT_X1, RIGHT_Y1, RIGHT_X2, RIGHT_Y2], fill=(24, 28, 38, 255), outline=(45, 60, 85, 255))
    
    if splash_img:
        splash_img = resize_cover(splash_img, RIGHT_W, RIGHT_H, focus_y=0.12)
        card.paste(splash_img, (RIGHT_X1, RIGHT_Y1), splash_img)
        
    # تدرج غامق أسفل الصورة لتوضيح الأسماء
    grad_h = 320
    gradient = Image.new("RGBA", (RIGHT_W, grad_h), (0, 0, 0, 0))
    grad_draw = ImageDraw.Draw(gradient)
    for gy in range(grad_h):
        t = gy / grad_h
        alpha = int(245 * (t ** 1.5))
        grad_draw.line([(0, gy), (RIGHT_W, gy)], fill=(8, 10, 16, alpha))
    card.paste(gradient, (RIGHT_X1, RIGHT_Y2 - grad_h), gradient)
    
    # اسم الشخصية والمستوى
    name_y = 580
    draw.text((RIGHT_X1 + 25, name_y), char_name.upper(), font=font_large, fill=(255, 215, 100, 255))
    draw.text((RIGHT_X1 + 25, name_y + 32), f"LEVEL {char_level} / 80", font=font_bold, fill=(220, 225, 235, 255))
    
    draw.line([(RIGHT_X1 + 25, name_y + 60), (RIGHT_X2 - 25, name_y + 60)], fill=(255, 255, 255, 60), width=1)
    
    # معلومات اللاعب في الأسفل
    p_name = player_data.get("nickname", "Unknown")
    p_uid = player_data.get("uid", "-")
    p_level = player_data.get("level", "-")
    p_eq = player_data.get("world_level", "-")
    
    info_y = name_y + 70
    draw.text((RIGHT_X1 + 25, info_y), f"{p_name}  •  UID {p_uid}", font=font_bold, fill=(255, 255, 255, 255))
    draw.text((RIGHT_X1 + 25, info_y + 22), f"Trailblaze Lv. {p_level}   |   Equilibrium Lv. {p_eq}", font=font_small, fill=(200, 210, 230, 255))

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
        await update.message.reply_text("❌ يرجى كتابة الـ UID بعد الأمر!\nمثال: `/hsr 701021140`", parse_mode='Markdown', reply_to_message_id=update.message.message_id)
        return

    uid = context.args[0]
    await update.message.reply_text("⏳ جاري جلب بيانات الحساب...", reply_to_message_id=update.message.message_id)

    url = f"https://api.mihomo.me/sr_info_parsed/{uid}?lang=en"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, timeout=15)
            if response.status_code != 200:
                await update.message.reply_text("❌ لم يتم العثور على الحساب. تأكد من صحة الـ UID.", reply_to_message_id=update.message.message_id)
                return

            data = response.json()
            player = data.get("player", {})
            nickname = player.get("nickname", "Player")
            avatars = data.get("characters", []) or data.get("avatar_list", [])

            if not avatars:
                await update.message.reply_text("⚠️ لا توجد شخصيات معروضة في هذا الحساب.", reply_to_message_id=update.message.message_id)
                return

            keyboard = []
            row = []
            for idx, char in enumerate(avatars):
                char_name = char.get("name", f"شخصية #{idx + 1}")
                row.append(InlineKeyboardButton(char_name, callback_data=f"hsr_{uid}_{idx}"))
                if len(row) == 4:
                    keyboard.append(row)
                    row = []
            if row:
                keyboard.append(row)

            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                f"👤 **اللاعب:** {nickname}\n👇 **اختر الشخصية:**",
                reply_markup=reply_markup,
                parse_mode='Markdown',
                reply_to_message_id=update.message.message_id
            )

        except Exception:
            await update.message.reply_text("❌ حدث خطأ في الاتصال بالسيرفر.", reply_to_message_id=update.message.message_id)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data_parts = query.data.split("_")
    if data_parts[0] == "hsr":
        uid = data_parts[1]
        char_idx = int(data_parts[2])

        target_message_id = query.message.message_id

        if query.message.reply_to_message:
            target_message_id = query.message.reply_to_message.message_id

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

                        await query.message.reply_photo(
                            photo=card_buf,
                            reply_to_message_id=target_message_id
                        )
                        return

                await query.message.reply_text("❌ تعذر إنشاء البطاقة.", reply_to_message_id=target_message_id)
            except Exception as e:
                print(f"Error generating card: {e}")
                await query.message.reply_text("❌ حدث خطأ أثناء تجهيز الصورة.", reply_to_message_id=target_message_id)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("hsr", hsr_check))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("🚀 البوت يعمل بنجاح!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
