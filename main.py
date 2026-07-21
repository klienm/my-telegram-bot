import os
import re
import httpx
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops
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

# دالة قص متطورة لدمج صورة الشخصية بسلاسة متناهية مع الخلفية عن طريق تلاشي الحواف اليمنى ودوران الزوايا الأخرى
def mask_rounded_fade(img, radius=24, fade_width=80):
    w, h = img.size
    mask = Image.new("L", (w, h), 255)
    gradient = Image.new("L", (fade_width, 1))
    grad_draw = ImageDraw.Draw(gradient)
    for x in range(fade_width):
        grad_draw.point((x, 0), fill=int(255 * (1 - x / fade_width)))
    gradient = gradient.resize((fade_width, h), Image.Resampling.BILINEAR)
    mask.paste(gradient, (w - fade_width, 0))
    
    rounded_mask = Image.new("L", (w, h), 0)
    rounded_draw = ImageDraw.Draw(rounded_mask)
    rounded_draw.rounded_rectangle([0, 0, w, h], radius=radius, fill=255)
    
    final_mask = ImageChops.multiply(mask, rounded_mask)
    
    rounded_img = Image.new("RGBA", img.size)
    rounded_img.paste(img, (0, 0), final_mask)
    return rounded_img

# دالة رسم النصوص بظلال ناعمة ودقيقة لزيادة وضوح الحروف فوق أي لون خلفية
def draw_shadow_text(draw, position, text, font, fill, shadow_fill=(0, 0, 0, 255), offset=(2, 2)):
    x, y = position
    draw.text((x + offset[0], y + offset[1]), text, font=font, fill=shadow_fill)
    draw.text((x, y), text, font=font, fill=fill)

# مسارات وروابط تحميل خطوط DejaVu عريضة الحาดة لضمان جودة Figma الاحترافية في بايثون
DEJAVU_BOLD_URL = "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/resources/fonts/dejavu-fonts-ttf-2.37/ttf/DejaVuSans-Bold.ttf"
DEJAVU_REG_URL = "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/resources/fonts/dejavu-fonts-ttf-2.37/ttf/DejaVuSans.ttf"

LOCAL_BOLD_PATH = "DejaVuSans-Bold.ttf"
LOCAL_REG_PATH = "DejaVuSans.ttf"

def get_sharp_font(size, bold=True):
    """
    تقوم هذه الدالة بفحص مسارات النظام في لينكس وجلب خطوط DejaVuSans المتجهية الفاخرة.
    في حال غيابها من السيرفر، تقوم بتحميلها وحفظها محلياً لضمان رسم حاد Sharp 100% بدون تشويش.
    """
    sys_bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    sys_reg = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    
    selected_path = sys_bold if bold else sys_reg
    local_path = LOCAL_BOLD_PATH if bold else LOCAL_REG_PATH
    fallback_url = DEJAVU_BOLD_URL if bold else DEJAVU_REG_URL
    
    # فحص مسار نظام لينكس أولاً
    if os.path.exists(selected_path):
        try:
            return ImageFont.truetype(selected_path, size)
        except Exception:
            pass
            
    # فحص المجلد المحلي للمشروع ثانياً
    if os.path.exists(local_path):
        try:
            return ImageFont.truetype(local_path, size)
        except Exception:
            pass
            
    # تحميل الخط برمجياً إذا لم يتوفر على خادم التشغيل
    try:
        print(f"⏳ Font file {local_path} missing. Downloading from source for HD sharp text...")
        urllib.request.urlretrieve(fallback_url, local_path)
        print(f"✅ Cached {local_path} successfully.")
        return ImageFont.truetype(local_path, size)
    except Exception as e:
        print(f"⚠️ Font download failed fallback to default: {e}")
        return ImageFont.load_default()

# ذاكرة التخزين المؤقت للأيقونات
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

# --- دالة رسم بطاقة الشخصية الزجاجية المتصلة ---
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

    # جلب الخطوط بشكل حاد وبمقاسات كبيرة ومثالية تمنع الإجهاد البصري تماماً
    font_large = get_sharp_font(34, bold=True)    # اسم الشخصية
    font_title = get_sharp_font(24, bold=True)    # أسماء السلاح وعناوين المهارات
    font_bold = get_sharp_font(18, bold=True)     # الإحصائيات الرئيسية ونسب الريليكس والمستويات
    font_sub = get_sharp_font(15, bold=False)     # البيانات الفرعية والمستويات الجانبية
    font_small = get_sharp_font(13, bold=False)   # تفاصيل إحصائيات الريليكس الفرعية

    # جلب صورة السبلاش آرت مسبقاً لاستخراج الألوان وعرضها
    splash_img = None
    if char_id:
        portrait_url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/image/character_portrait/{char_id}.png"
        splash_img = await fetch_image(client, portrait_url)

    if not splash_img and icon_path:
        splash_icon = icon_path.replace("icon/character", "image/character_portrait").replace("steps/", "")
        img_url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/{splash_icon}"
        splash_img = await fetch_image(client, img_url)

    # الألوان الافتراضية للبطاقة
    highlight_color = (255, 215, 100, 255)
    subtitle_color = (150, 200, 255, 255)
    bg_color = (10, 12, 18, 255)

    # 1. تحليل الصورة واستخراج ألوان دافئة ومشرقة بالكامل للخلفية وتفاصيل الكارد
    if splash_img:
        resample_filter = getattr(Image, "Resampling", None)
        box_filter = resample_filter.BOX if resample_filter else getattr(Image, "BOX", Image.NEAREST)
        tiny_img = splash_img.resize((1, 1), box_filter)
        avg_pixel = tiny_img.getpixel((0, 0))
        r, g, b = int(avg_pixel[0]), int(avg_pixel[1]), int(avg_pixel[2])
        
        # زيادة سطوع خلفية الكارد المشتقة من ألوان الشخصية (تعديل 18% بدلاً من 8%) لجعلها مفعمة بالحيوية
        bg_color = (int(r * 0.18), int(g * 0.18), int(b * 0.18), 255)
        bg_base = Image.new("RGBA", (1600, 800), bg_color)
        
        # تخفيف شدة التغبيش لـ 30 للحفاظ على عمق الخلفية ووضوح تفاصيل السبلاش آرت
        blurred_splash = resize_cover(splash_img, 1600, 800, focus_y=0.2)
        blurred_splash = blurred_splash.filter(ImageFilter.GaussianBlur(radius=30))
        
        # زيادة نسبة دمج السبلاش آرت الفعلي لـ 55% لإبراز الألوان والجمالية الفنية للخلفية
        bg_final = Image.blend(bg_base, blurred_splash, 0.55)
        card.paste(bg_final, (0, 0))

        # معالجة لون التمييز المشتق المشع
        max_c = max(r, g, b, 1)
        highlight_r = int((r / max_c) * 255)
        highlight_g = int((g / max_c) * 255)
        highlight_b = int((b / max_c) * 255)
        
        # تفتيح درجة اللون المميز برمجياً لتظهر زاهية جداً ومشرقة فوق الخلفية الملونة
        highlight_color = (
            int(highlight_r * 0.75 + 255 * 0.25),
            int(highlight_g * 0.75 + 255 * 0.25),
            int(highlight_b * 0.75 + 255 * 0.25),
            255
        )
        
        # لون العناوين الفرعية والنصوص الجانبية
        subtitle_color = (
            int(highlight_r * 0.5 + 255 * 0.2),
            int(highlight_g * 0.5 + 255 * 0.2),
            int(highlight_b * 0.5 + 255 * 0.2),
            255
        )
    else:
        card.paste(Image.new("RGBA", (1600, 800), bg_color), (0, 0))

    # تخفيف عتامة الطبقة الداكنة (Alpha 95) لجعل الكارد مضيئاً وأكثر إشراقاً ووضوحاً
    tint = Image.new("RGBA", (1600, 800), (8, 10, 16, 95))
    card = Image.alpha_composite(card, tint)
    draw = ImageDraw.Draw(card)

    # تحضير الإحصائيات للشخصية
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


    # ==================== القسم الأول (أقصى اليسار): السبلاش آرت الدائري المتلاشي بسلاسة ====================
    if splash_img:
        splash_crop = resize_cover(splash_img, 440, 720, focus_y=0.12)
        # دمج أطراف دائرية مع تلاشي تدريجي للحافة اليمنى للاندماج مع الخلفية
        splash_styled = mask_rounded_fade(splash_crop, radius=24, fade_width=80)
        card.paste(splash_styled, (40, 40), splash_styled)
        
    # تدرج غامق تحت الاسم لحماية النصوص
    grad_h = 300
    gradient = Image.new("RGBA", (440, grad_h), (0, 0, 0, 0))
    grad_draw = ImageDraw.Draw(gradient)
    for gy in range(grad_h):
        t = gy / grad_h
        alpha = int(245 * (t ** 1.6))
        grad_draw.line([(0, gy), (440, gy)], fill=(8, 10, 16, alpha))
    gradient_styled = mask_rounded_fade(gradient, radius=24, fade_width=80)
    card.paste(gradient_styled, (40, 760 - grad_h), gradient_styled)
    
    # تفاصيل الشخصية والاسم بمقاسات خط ممتازة
    name_y = 570
    draw_shadow_text(draw, (75, name_y), char_name.upper(), font_large, highlight_color)
    draw_shadow_text(draw, (75, name_y + 38), f"LEVEL {char_level} / 80", font_bold, (255, 240, 210, 255))
    
    # معلومات الحساب بالأسفل
    p_name = player_data.get("nickname", "Unknown")
    p_uid = player_data.get("uid", "-")
    p_level = player_data.get("level", "-")
    p_eq = player_data.get("world_level", "-")
    
    info_y = name_y + 80
    draw_shadow_text(draw, (75, info_y), f"{p_name}  •  UID {p_uid}", font_bold, (255, 255, 255, 255))
    draw_shadow_text(draw, (75, info_y + 24), f"Trailblaze Lv. {p_level}   |   Equilibrium Lv. {p_eq}", font_small, subtitle_color)


    # ==================== القسم الثاني (الوسط اليسار): الآثار والمهارات والسلاح (طافية) ====================
    # المهارات والآثار (Traces)
    skills = char_data.get("skills", []) or []
    skill_y = 50
    for skill in skills[:5]:
        sk_name = skill.get("name", "Skill")
        sk_level = skill.get("level", 1)
        sk_max = skill.get("max_level", 10)
        sk_icon = skill.get("icon", "")
        sk_type = skill.get("type_text", "") or skill.get("tag", "Trace")
        
        if sk_icon:
            sk_img = await get_cached_icon(client, sk_icon, (34, 34))
            if sk_img:
                card.paste(sk_img, (510, skill_y), sk_img)
                
        draw_shadow_text(draw, (555, skill_y - 2), sk_name[:20], font_bold, (255, 255, 255, 255))
        draw_shadow_text(draw, (555, skill_y + 18), f"{sk_type}  •  Lv. {sk_level}/{sk_max}", font_small, subtitle_color)
        
        skill_y += 70  # زيادة التباعد ليتناسب مع حجم الخط الكبير

    # السلاح (Light Cone) طافٍ بدون صناديق خلفية
    lc_y = 420
    if lc_icon:
        lc_img = await get_cached_icon(client, lc_icon, (75, 75))
        if lc_img:
            card.paste(lc_img, (510, lc_y), lc_img)
            
    draw_shadow_text(draw, (595, lc_y), f"{lc_name[:22]}", font_bold, (255, 255, 255, 255))
    draw_shadow_text(draw, (595, lc_y + 26), f"Lv. {lc_level} / 80", font_sub, highlight_color)
    
    # عرض إحصائيات السلاح الأساسية
    lc_attrs = equip.get("attributes", []) or []
    lc_stat_y = 505
    for attr in lc_attrs[:3]:
        a_name = attr.get("name", "")
        a_val = attr.get("display", str(attr.get("value", "")))
        a_icon = attr.get("icon", "")
        
        if a_icon:
            a_img = await get_cached_icon(client, a_icon, (20, 20))
            if a_img:
                card.paste(a_img, (510, lc_stat_y), a_img)
                
        draw_shadow_text(draw, (540, lc_stat_y + 1), f"Base {a_name}:", font_small, subtitle_color)
        draw_shadow_text(draw, (720, lc_stat_y + 1), str(a_val), font_small, (255, 255, 255, 255))
        lc_stat_y += 28


    # ==================== القسم الثالث (الوسط اليمين): الإحصائيات النشطة وتأثير المجموعات (طافية) ====================
    stat_y = 50
    for stat in rendered_stats[:8]:
        s_name = stat["name"]
        s_val = stat["value"]
        s_icon = stat["icon"]
        
        if s_icon:
            s_img = await get_cached_icon(client, s_icon, (24, 24))
            if s_img:
                card.paste(s_img, (870, stat_y), s_img)
                
        draw_shadow_text(draw, (905, stat_y + 3), s_name, font_bold, (255, 240, 220, 255))
        
        try:
            val_width = draw.textlength(s_val, font=font_bold)
        except AttributeError:
            val_width = len(s_val) * 8.5
        draw_shadow_text(draw, (1180 - val_width, stat_y + 3), s_val, font_bold, highlight_color)
        
        stat_y += 44  # زيادة المسافة الرأسية بين الإحصائيات لتلافي التداخل

    # تأثير المجموعات (Set Effects) طافٍ
    relic_sets = char_data.get("relic_sets", [])
    set_y = 425
    for r_set in relic_sets[:2]:
        s_name = r_set.get("name", "Unknown Set")
        s_num = r_set.get("num", 2)
        raw_desc = r_set.get("desc", "")
        clean_desc = re.sub(r'<[^>]+>', '', str(raw_desc)).replace("\n", " ")
        
        draw_shadow_text(draw, (870, set_y), f"[{s_num}-Pc] {s_name}", font_bold, highlight_color)
        set_y += 20
        
        words = clean_desc.split(" ")
        line = ""
        for word in words:
            test_line = line + word + " "
            try:
                text_width = draw.textlength(test_line, font=font_small)
            except AttributeError:
                text_width = len(test_line) * 6
                
            if text_width < 290:  # تضييق العرض نسبياً لاستيعاب الخط الأكبر
                line = test_line
            else:
                draw_shadow_text(draw, (870, set_y), line, font_small, (240, 240, 245, 255))
                set_y += 16
                line = word + " "
        if line:
            draw_shadow_text(draw, (870, set_y), line, font_small, (240, 240, 245, 255))
            set_y += 24


    # ==================== القسم الرابع (أقصى اليمين): قطع الريليكس الستة (طافية وسلسة) ====================
    for idx, r in enumerate(relics[:6]):
        box_y1 = 50 + (idx * 118)  # موازنة المسافات العمودية لتناسب الخطوط الكبيرة
        box_x1 = 1230
        box_x2 = 1560
        
        r_name = r.get("name", f"Relic #{idx+1}")
        r_lvl = r.get("level", 0)
        r_icon = r.get("icon", "")
        
        if r_icon:
            r_img = await get_cached_icon(client, r_icon, (52, 52))
            if r_img:
                card.paste(r_img, (box_x1, box_y1 + 8), r_img)
                
        # اقتصاص أسماء الريليكس لـ 14 حرفاً لتجنب التداخل مع مستوى الريليك
        draw_shadow_text(draw, (box_x1 + 60, box_y1 + 12), f"{r_name[:14]}", font_bold, (255, 250, 240, 255))
        draw_shadow_text(draw, (box_x2 - 38, box_y1 + 12), f"+{r_lvl}", font_bold, highlight_color)
        
        main_stat = r.get("main_affix", {}) or r.get("mainstat", {})
        m_name = main_stat.get("name", "") or main_stat.get("type", "")
        m_display = main_stat.get("display", "")
        if not m_display:
            m_display = format_stat_value(m_name, main_stat.get("value", ""), is_planar=(idx in [4, 5]))
            
        if m_name:
            draw_shadow_text(draw, (box_x1 + 60, box_y1 + 34), f"Main: {m_name} ({m_display})", font_small, subtitle_color)
            
        substats = r.get("sub_affix", []) or r.get("sub_affix_list", []) or r.get("substats", [])
        for i, sub in enumerate(substats[:4]):
            s_name = sub.get("name", "") or sub.get("type", "") or sub.get("field", "")
            s_display = sub.get("display", "")
            if not s_display:
                s_display = format_stat_value(s_name, sub.get("value", ""))
                
            if s_name and s_display:
                short_name = str(s_name).replace("_", " ")[:10]
                stat_text = f"{short_name}: {s_display}"
                
                sub_col_x = box_x1 + 60 if i % 2 == 0 else box_x1 + 175
                sub_row_y = box_y1 + 58 if i < 2 else box_y1 + 82
                draw_shadow_text(draw, (sub_col_x, sub_row_y), stat_text, font_small, (240, 240, 245, 255))

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
