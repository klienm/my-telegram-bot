import os
import re
import httpx
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageFilter
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

# دالة ذكية لقص الصور بأطراف دائرية لتلائم واجهة الزجاج المقاوم للكسر
def mask_rounded(img, radius):
    mask = Image.new("L", img.size, 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([0, 0, img.size[0], img.size[1]], radius=radius, fill=255)
    rounded_img = Image.new("RGBA", img.size)
    rounded_img.paste(img, (0, 0), mask)
    return rounded_img

# دالة رسم النصوص مع ظلال ناعمة لإبراز الكلمات وزيادة عمق التصميم
def draw_shadow_text(draw, position, text, font, fill, shadow_fill=(0, 0, 0, 240), offset=(1.5, 1.5)):
    x, y = position
    draw.text((x + offset[0], y + offset[1]), text, font=font, fill=shadow_fill)
    draw.text((x, y), text, font=font, fill=fill)

# ذاكرة تخزين مؤقت للأيقونات لتسريع البناء
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

# --- دالة رسم بطاقة الشخصية الحديثة الزجاجية والموحدة ---
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

    # أبعاد الكارد الرسمية
    card = Image.new("RGBA", (1600, 800), (10, 12, 18, 255))
    draw = ImageDraw.Draw(card)

    try:
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
        font_bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
        font_sub = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
    except Exception:
        font_large = font_title = font_bold = font_sub = font_small = ImageFont.load_default()

    # جلب صورة السبلاش آرت لاستخدامها كخلفية وللعرض الأساسي
    splash_img = None
    if char_id:
        portrait_url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/image/character_portrait/{char_id}.png"
        splash_img = await fetch_image(client, portrait_url)

    if not splash_img and icon_path:
        splash_icon = icon_path.replace("icon/character", "image/character_portrait").replace("steps/", "")
        img_url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/{splash_icon}"
        splash_img = await fetch_image(client, img_url)

    # 1. تحليل الصورة واستخراج اللون المسيطر لصناعة خلفية حية
    if splash_img:
        resample_filter = getattr(Image, "Resampling", None)
        box_filter = resample_filter.BOX if resample_filter else getattr(Image, "BOX", Image.NEAREST)
        tiny_img = splash_img.resize((1, 1), box_filter)
        avg_pixel = tiny_img.getpixel((0, 0))
        r, g, b = avg_pixel[0], avg_pixel[1], avg_pixel[2]
        
        # استخراج اللون وحساب درجة تشبعه ثم خفض سطوعه لتباين ممتاز
        max_val = max(r, g, b, 1)
        nr, ng, nb = r / max_val, g / max_val, b / max_val
        bg_color = (int(nr * 35), int(ng * 35), int(nb * 35), 255)
        bg_base = Image.new("RGBA", (1600, 800), bg_color)
        
        # تضبيب الخلفية بنعومة عالية (Glow Blur Effect)
        blurred_splash = resize_cover(splash_img, 1600, 800, focus_y=0.2)
        blurred_splash = blurred_splash.filter(ImageFilter.GaussianBlur(radius=50))
        
        bg_final = Image.blend(bg_base, blurred_splash, 0.35)
        card.paste(bg_final, (0, 0))
    else:
        card.paste(Image.new("RGBA", (1600, 800), (12, 15, 23, 255)), (0, 0))

    # طبقة تباين زجاجية داكنة إضافية لضمان عمق الألوان وحيوية التفاصيل
    tint = Image.new("RGBA", (1600, 800), (10, 12, 18, 120))
    card = Image.alpha_composite(card, tint)
    draw = ImageDraw.Draw(card)

    # تحضير الستات للشخصية
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

    for field, stat in all_stats.items():
        if field not in seen_fields:
            val = stat["value"]
            if val not in ["0", "0%", "0.0%", "0.0"]:
                rendered_stats.append(stat)
                seen_fields.add(field)


    # ==================== القسم الأول (أقصى اليسار): السبلاش آرت بأطراف دائرية ====================
    if splash_img:
        splash_crop = resize_cover(splash_img, 440, 720, focus_y=0.12)
        # تحويل الأطراف إلى دائرية ناعمة لتناسب المظهر الزجاجي الحديث
        splash_rounded = mask_rounded(splash_crop, radius=20)
        card.paste(splash_rounded, (40, 40), splash_rounded)
        
    # تدرج غامق مدمج ناعم داخل إطار الصورة الدائري
    grad_h = 300
    gradient = Image.new("RGBA", (440, grad_h), (0, 0, 0, 0))
    grad_draw = ImageDraw.Draw(gradient)
    for gy in range(grad_h):
        t = gy / grad_h
        alpha = int(245 * (t ** 1.6))
        grad_draw.line([(0, gy), (440, gy)], fill=(8, 10, 16, alpha))
    gradient_rounded = mask_rounded(gradient, radius=20)
    card.paste(gradient_rounded, (40, 760 - grad_h), gradient_rounded)
    
    # تفاصيل الشخصية فوق التدرج الداكن
    name_y = 570
    draw_shadow_text(draw, (75, name_y), char_name.upper(), font_large, (255, 215, 100, 255))
    draw_shadow_text(draw, (75, name_y + 34), f"LEVEL {char_level} / 80", font_bold, (220, 225, 235, 255))
    
    draw.line([(75, name_y + 60), (445, name_y + 60)], fill=(255, 255, 255, 40), width=1)
    
    p_name = player_data.get("nickname", "Unknown")
    p_uid = player_data.get("uid", "-")
    p_level = player_data.get("level", "-")
    p_eq = player_data.get("world_level", "-")
    
    info_y = name_y + 70
    draw_shadow_text(draw, (75, info_y), f"{p_name}  •  UID {p_uid}", font_bold, (255, 255, 255, 255))
    draw_shadow_text(draw, (75, info_y + 22), f"Trailblaze Lv. {p_level}   |   Equilibrium Lv. {p_eq}", font_small, (200, 210, 230, 255))


    # ==================== القسم الثاني (الوسط اليسار): الآثار والمهارات والسلاح زجاجي ====================
    # اللوحة الخلفية الزجاجية مع توهج الحواف (Glassmorphic Card Panel)
    draw.rounded_rectangle([510, 40, 840, 760], radius=20, fill=(12, 15, 23, 130), outline=(255, 255, 255, 15), width=1)
    
    # المهارات والآثار
    skills = char_data.get("skills", []) or []
    skill_y = 55
    for skill in skills[:5]:
        sk_name = skill.get("name", "Skill")
        sk_level = skill.get("level", 1)
        sk_max = skill.get("max_level", 10)
        sk_icon = skill.get("icon", "")
        sk_type = skill.get("type_text", "") or skill.get("tag", "Trace")
        
        if sk_icon:
            sk_img = await get_cached_icon(client, sk_icon, (34, 34))
            if sk_img:
                card.paste(sk_img, (530, skill_y), sk_img)
                
        draw_shadow_text(draw, (575, skill_y - 2), sk_name[:20], font_bold, (255, 255, 255, 255))
        draw_shadow_text(draw, (575, skill_y + 16), f"{sk_type}  •  Lv. {sk_level}/{sk_max}", font_small, (150, 200, 255, 255))
        
        skill_y += 65
        
    # السلاح (موضوع داخل صندوق زجاجي داخلي لعمق أكبر)
    lc_y = 410
    draw.rounded_rectangle([525, lc_y - 15, 825, 745], radius=14, fill=(255, 255, 255, 10))
    
    if lc_icon:
        lc_img = await get_cached_icon(client, lc_icon, (75, 75))
        if lc_img:
            card.paste(lc_img, (540, lc_y), lc_img)
            
    draw_shadow_text(draw, (625, lc_y), f"{lc_name[:22]}", font_bold, (255, 255, 255, 255))
    draw_shadow_text(draw, (625, lc_y + 24), f"Lv. {lc_level} / 80", font_sub, (150, 220, 150, 255))
    
    lc_attrs = equip.get("attributes", []) or []
    lc_stat_y = 490
    for attr in lc_attrs[:3]:
        a_name = attr.get("name", "")
        a_val = attr.get("display", str(attr.get("value", "")))
        a_icon = attr.get("icon", "")
        
        if a_icon:
            a_img = await get_cached_icon(client, a_icon, (20, 20))
            if a_img:
                card.paste(a_img, (540, lc_stat_y), a_img)
                
        draw_shadow_text(draw, (565, lc_stat_y + 1), f"Base {a_name}:", font_small, (170, 185, 205, 255))
        draw_shadow_text(draw, (740, lc_stat_y + 1), str(a_val), font_small, (255, 255, 255, 255))
        lc_stat_y += 26


    # ==================== القسم الثالث (الوسط اليمين): الإحصائيات النشطة وتأثير المجموعات ====================
    # لوحة زجاجية للقسم الثالث
    draw.rounded_rectangle([870, 40, 1200, 760], radius=20, fill=(12, 15, 23, 130), outline=(255, 255, 255, 15), width=1)
    
    stat_y = 55
    for stat in rendered_stats[:8]:
        s_name = stat["name"]
        s_val = stat["value"]
        s_icon = stat["icon"]
        
        if s_icon:
            s_img = await get_cached_icon(client, s_icon, (24, 24))
            if s_img:
                card.paste(s_img, (890, stat_y), s_img)
                
        draw_shadow_text(draw, (925, stat_y + 3), s_name, font_bold, (200, 210, 230, 255))
        
        try:
            val_width = draw.textlength(s_val, font=font_bold)
        except AttributeError:
            val_width = len(s_val) * 8
        draw_shadow_text(draw, (1180 - val_width, stat_y + 3), s_val, font_bold, (255, 215, 100, 255))
        
        draw.line([(890, stat_y + 35), (1180, stat_y + 35)], fill=(255, 255, 255, 15), width=1)
        stat_y += 42
        
    # صندوق تأثير المجموعات (Set Effects) زجاجي مدمج في الأسفل
    draw.rounded_rectangle([885, 405, 1185, 745], radius=14, fill=(255, 255, 255, 10))
    
    relic_sets = char_data.get("relic_sets", [])
    set_y = 425
    for r_set in relic_sets[:2]:
        s_name = r_set.get("name", "Unknown Set")
        s_num = r_set.get("num", 2)
        raw_desc = r_set.get("desc", "")
        clean_desc = re.sub(r'<[^>]+>', '', str(raw_desc)).replace("\n", " ")
        
        draw_shadow_text(draw, (900, set_y), f"[{s_num}-Pc] {s_name}", font_bold, (100, 230, 150, 255))
        set_y += 18
        
        words = clean_desc.split(" ")
        line = ""
        for word in words:
            test_line = line + word + " "
            try:
                text_width = draw.textlength(test_line, font=font_small)
            except AttributeError:
                text_width = len(test_line) * 6
                
            if text_width < 260:
                line = test_line
            else:
                draw_shadow_text(draw, (900, set_y), line, font_small, (180, 195, 215, 255))
                set_y += 14
                line = word + " "
        if line:
            draw_shadow_text(draw, (900, set_y), line, font_small, (180, 195, 215, 255))
            set_y += 20


    # ==================== القسم الرابع (أقصى اليمين): الريليكس الستة ====================
    # لوحة زجاجية للقسم الرابع
    draw.rounded_rectangle([1230, 40, 1560, 760], radius=20, fill=(12, 15, 23, 130), outline=(255, 255, 255, 15), width=1)
    
    for idx, r in enumerate(relics[:6]):
        box_y1 = 50 + (idx * 116)
        box_y2 = box_y1 + 108
        box_x1 = 1242
        box_x2 = 1548
        
        # لوحات زجاجية مصغرة لكل قطعة ريليك لمزيد من البعد والعمق الفني (Depth)
        draw.rounded_rectangle([box_x1, box_y1, box_x2, box_y2], radius=12, fill=(255, 255, 255, 12))
        
        r_name = r.get("name", f"Relic #{idx+1}")
        r_lvl = r.get("level", 0)
        r_icon = r.get("icon", "")
        
        if r_icon:
            r_img = await get_cached_icon(client, r_icon, (52, 52))
            if r_img:
                card.paste(r_img, (box_x1 + 8, box_y1 + 8), r_img)
                
        draw_shadow_text(draw, (box_x1 + 65, box_y1 + 12), f"{r_name[:16]}", font_bold, (230, 235, 245, 255))
        draw_shadow_text(draw, (box_x2 - 38, box_y1 + 12), f"+{r_lvl}", font_bold, (100, 230, 150, 255))
        
        main_stat = r.get("main_affix", {}) or r.get("mainstat", {})
        m_name = main_stat.get("name", "") or main_stat.get("type", "")
        m_display = main_stat.get("display", "")
        if not m_display:
            m_display = format_stat_value(m_name, main_stat.get("value", ""), is_planar=(idx in [4, 5]))
            
        if m_name:
            draw_shadow_text(draw, (box_x1 + 65, box_y1 + 32), f"Main: {m_name} ({m_display})", font_small, (255, 215, 100, 255))
            
        substats = r.get("sub_affix", []) or r.get("sub_affix_list", []) or r.get("substats", [])
        for i, sub in enumerate(substats[:4]):
            s_name = sub.get("name", "") or sub.get("type", "") or sub.get("field", "")
            s_display = sub.get("display", "")
            if not s_display:
                s_display = format_stat_value(s_name, sub.get("value", ""))
                
            if s_name and s_display:
                short_name = str(s_name).replace("_", " ")[:10]
                stat_text = f"{short_name}: {s_display}"
                
                sub_col_x = box_x1 + 65 if i % 2 == 0 else box_x1 + 180
                sub_row_y = box_y1 + 56 if i < 2 else box_y1 + 78
                draw_shadow_text(draw, (sub_col_x, sub_row_y), stat_text, font_small, (170, 185, 205, 255))

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
