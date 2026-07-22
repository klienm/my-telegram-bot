import os
import re
import math
import sqlite3
import logging
import aiohttp
import tempfile
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters
)

# إعداد السجلات
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# إعداد قاعدة البيانات لتخزين الرسائل
def init_db():
    conn = sqlite3.connect('messages.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_messages (
            user_id INTEGER PRIMARY KEY,
            full_name TEXT,
            username TEXT,
            message_count INTEGER
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# دالة تسجيل وحساب الرسائل
def track_message(user):
    if user.is_bot:
        return

    conn = sqlite3.connect('messages.db', check_same_thread=False, timeout=10)
    cursor = conn.cursor()

    user_id = user.id
    full_name = f"{user.first_name} {user.last_name or ''}".strip()
    username = f"@{user.username}" if user.username else "لا يوجد"

    cursor.execute('SELECT message_count FROM user_messages WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()

    if row:
        new_count = row[0] + 1
        cursor.execute('''
            UPDATE user_messages
            SET message_count = ?, full_name = ?, username = ?
            WHERE user_id = ?
        ''', (new_count, full_name, username, user_id))
    else:
        cursor.execute('''
            INSERT INTO user_messages (user_id, full_name, username, message_count)
            VALUES (?, ?, ?, 1)
        ''', (user_id, full_name, username))

    conn.commit()
    conn.close()

# دالة جلب عدد رسائل المستخدم
def get_user_message_count(user_id):
    conn = sqlite3.connect('messages.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('SELECT message_count FROM user_messages WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0

# دالة لتهريب نص HTML
def escape_html(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

# أمر الايدي
async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return

    # تسجيل الرسالة أيضاً عند استخدام الأمر
    track_message(user)

    msg_count = get_user_message_count(user.id)
    full_name = escape_html(f"{user.first_name} {user.last_name or ''}".strip())
    username = f"@{escape_html(user.username)}" if user.username else "لا يوجد"

    gender_text = "ولد"

    # استخدام HTML بدلاً من Markdown لتجنب كسر التنسيق بسبب الرموز الخاصة
    caption = (
        f"👤 <b>الاسم:</b> {full_name}\n"
        f"🔗 <b>اليوزر:</b> {username}\n"
        f"💬 <b>عدد رسائلك:</b> {msg_count}\n"
        f"⚧ <b>الجنس:</b> {gender_text}"
    )

    try:
        photos = await context.bot.get_user_profile_photos(user.id, limit=1)
        if photos and photos.total_count > 0:
            photo_file_id = photos.photos[0][0].file_id
            await update.message.reply_photo(
                photo=photo_file_id,
                caption=caption,
                parse_mode="HTML",
                reply_to_message_id=update.message.message_id
            )
        else:
            await update.message.reply_text(
                caption,
                parse_mode="HTML",
                reply_to_message_id=update.message.message_id
            )
    except Exception as e:
        logging.warning(f"id_command error: {e}")
        try:
            await update.message.reply_text(
                caption,
                parse_mode="HTML",
                reply_to_message_id=update.message.message_id
            )
        except Exception as e2:
            logging.error(f"id_command fallback error: {e2}")

# أدوات مساعدة للتصميم الفاخر
async def fetch_image(session, url):
    """جلب صورة مع fallback على SSL=False لحل مشاكل الشهادات"""
    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with session.get(url, timeout=timeout, allow_redirects=True) as response:
            if response.status == 200:
                content = await response.read()
                return Image.open(BytesIO(content)).convert("RGBA")
            logging.warning(f"fetch_image HTTP {response.status} for {url}")
    except Exception as e:
        logging.warning(f"fetch_image error for {url}: {e}")
    # محاولة ثانية بدون التحقق من SSL
    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as fallback_session:
            async with fallback_session.get(url, timeout=timeout, allow_redirects=True) as response:
                if response.status == 200:
                    content = await response.read()
                    return Image.open(BytesIO(content)).convert("RGBA")
    except Exception as e2:
        logging.warning(f"fetch_image fallback error for {url}: {e2}")
    return None

def resize_cover(img, target_w, target_h):
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w, new_h = max(1, round(src_w * scale)), max(1, round(src_h * scale))
    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))

FONT_BOLD_URL = "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat-Bold.ttf"
FONT_REG_URL = "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat-Medium.ttf"

temp_dir = tempfile.gettempdir()
LOCAL_BOLD_PATH = os.path.join(temp_dir, "Montserrat-Bold.ttf")
LOCAL_REG_PATH = os.path.join(temp_dir, "Montserrat-Medium.ttf")

async def download_fonts_on_startup():
    if not os.path.exists(LOCAL_BOLD_PATH) or not os.path.exists(LOCAL_REG_PATH):
        logging.info("⏳ Startup: Downloading Montserrat fonts...")
        # الإصلاح: استخدام aiohttp.ClientTimeout الصحيح
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession() as session:
            if not os.path.exists(LOCAL_BOLD_PATH):
                try:
                    async with session.get(FONT_BOLD_URL, timeout=timeout) as r:
                        if r.status == 200:
                            content = await r.read()
                            with open(LOCAL_BOLD_PATH, "wb") as f:
                                f.write(content)
                            logging.info("✅ Montserrat-Bold downloaded.")
                        else:
                            logging.error(f"❌ Font download failed, status: {r.status}")
                except Exception as e:
                    logging.error(f"❌ Failed to download bold font: {e}")

            if not os.path.exists(LOCAL_REG_PATH):
                try:
                    async with session.get(FONT_REG_URL, timeout=timeout) as r:
                        if r.status == 200:
                            content = await r.read()
                            with open(LOCAL_REG_PATH, "wb") as f:
                                f.write(content)
                            logging.info("✅ Montserrat-Medium downloaded.")
                        else:
                            logging.error(f"❌ Font download failed, status: {r.status}")
                except Exception as e:
                    logging.error(f"❌ Failed to download regular font: {e}")

def get_sharp_font(size, bold=True):
    local_path = LOCAL_BOLD_PATH if bold else LOCAL_REG_PATH

    if os.path.exists(local_path):
        try:
            return ImageFont.truetype(local_path, size)
        except Exception:
            pass

    sys_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans.ttf"
    ]
    for path in sys_paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass

    return ImageFont.load_default()

icon_cache = {}

async def get_cached_icon(session, icon_path, size=None):
    if not icon_path:
        return None
    cache_key = (icon_path, size)
    if cache_key in icon_cache:
        return icon_cache[cache_key]

    img_url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/{icon_path}"
    img = await fetch_image(session, img_url)
    if img:
        if size:
            img = img.resize(size, Image.Resampling.LANCZOS)
        icon_cache[cache_key] = img
        return img
    return None

def draw_shadow_text(draw, position, text, font, fill, shadow_fill=(0, 0, 0, 255), offset=(3, 3)):
    x, y = position
    draw.text((x + offset[0], y + offset[1]), text, font=font, fill=shadow_fill)
    draw.text((x, y), text, font=font, fill=fill)

def get_dominant_color(img):
    """استخراج اللون السائد من الصورة"""
    small = img.convert("RGB").resize((50, 50), Image.Resampling.LANCZOS)
    pixels = list(small.getdata())
    # استبعاد الألوان الداكنة جداً أو الفاتحة جداً
    filtered = [(r, g, b) for r, g, b in pixels if 30 < r + g + b < 700]
    if not filtered:
        filtered = pixels
    avg_r = int(sum(p[0] for p in filtered) / len(filtered))
    avg_g = int(sum(p[1] for p in filtered) / len(filtered))
    avg_b = int(sum(p[2] for p in filtered) / len(filtered))
    return avg_r, avg_g, avg_b

def vivid_color(r, g, b, boost=1.7):
    """تشبيع اللون وتفتيحه ليبدو حيوياً"""
    mx = max(r, g, b) or 1
    scale = min(255 / mx, boost)
    return (min(255, int(r * scale)), min(255, int(g * scale)), min(255, int(b * scale)))

def lerp_color(c1, c2, t):
    """مزج بين لونين"""
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))

def draw_glass_rect(canvas, x, y, w, h, fill_color, alpha=40, radius=12):
    """رسم مستطيل بتأثير الزجاج المصنفر"""
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    r, g, b = fill_color[:3]
    d.rounded_rectangle([x, y, x + w, y + h], radius=radius,
                         fill=(r, g, b, alpha), outline=(r, g, b, 80), width=2)
    return Image.alpha_composite(canvas, overlay)

def draw_glow_line(canvas, x1, y1, x2, y2, color, width=3, glow_radius=8):
    """رسم خط بتأثير توهج"""
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    r, g, b = color[:3]
    # طبقات التوهج
    for w in range(glow_radius, 0, -2):
        a = int(60 * (1 - w / glow_radius))
        d.line([(x1, y1), (x2, y2)], fill=(r, g, b, a), width=w * 2)
    d.line([(x1, y1), (x2, y2)], fill=(r, g, b, 220), width=width)
    return Image.alpha_composite(canvas, overlay)

def draw_ambient_glow(canvas, cx, cy, color, max_radius, max_alpha=60):
    """رسم توهج دائري ناعم (ambient glow)"""
    glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(glow)
    r, g, b = color[:3]
    steps = 20
    for i in range(steps, 0, -1):
        radius = int(max_radius * i / steps)
        alpha = int(max_alpha * (1 - i / steps) ** 0.5)
        d.ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
                  fill=(r, g, b, alpha))
    return Image.alpha_composite(canvas, glow)

# --- دالة رسم بطاقة الشخصية بتصميم متجدد ---
async def create_character_card(session, char_data, player_data):
    S = 2  # مضاعف الدقة

    W, H = 1600 * S, 800 * S

    char_name = char_data.get("name", "Character")
    char_level = char_data.get("level", 1)
    char_id = str(char_data.get("id", ""))

    # ═══ حساب الإحصائيات ═══
    final_stats = {}
    for attr in char_data.get("attributes", []):
        field = attr["field"]
        final_stats[field] = {
            "name": attr["name"], "icon": attr["icon"],
            "value": attr["value"],
            "is_percent": attr.get("percent", False) or field in
                          ['crit_rate', 'crit_dmg', 'effect_hit', 'effect_res', 'break_effect']
        }
    for add in char_data.get("additions", []):
        field = add["field"]
        if field in final_stats:
            final_stats[field]["value"] += add["value"]
        else:
            final_stats[field] = {
                "name": add["name"], "icon": add["icon"],
                "value": add["value"],
                "is_percent": add.get("percent", False) or field in
                              ['crit_rate', 'crit_dmg', 'effect_hit', 'effect_res', 'break_effect']
            }

    stat_order = ["hp", "atk", "def", "spd", "crit_rate", "crit_dmg",
                  "break_effect", "effect_hit", "effect_res", "heal_rate", "sp_rate"]
    rendered_stats = []
    for field in stat_order:
        if field in final_stats:
            stat = final_stats[field]
            val = stat["value"]
            display_val = f"{val * 100:.1f}%" if stat["is_percent"] else str(int(math.floor(val)))
            rendered_stats.append({"name": stat["name"], "value": display_val, "icon": stat["icon"]})

    # ═══ جلب صورة الشخصية ═══
    splash_img = None
    if char_id:
        portrait_url = (f"https://raw.githubusercontent.com/Mar-7th/StarRailRes"
                        f"/master/image/character_portrait/{char_id}.png")
        splash_img = await fetch_image(session, portrait_url)

    # ═══ استخراج وتشبيع لون الشخصية ═══
    if splash_img:
        dr, dg, db = get_dominant_color(splash_img)
        vr, vg, vb = vivid_color(dr, dg, db, boost=1.8)
    else:
        vr, vg, vb = 130, 100, 220  # بنفسجي افتراضي

    accent = (vr, vg, vb)
    accent_dim = (vr // 3, vg // 3, vb // 3)
    gold = (255, 210, 80)
    white = (255, 255, 255)

    # ═══ الخلفية الأساسية ═══
    # لون قاعدي داكن مع لمسة من لون الشخصية
    base_r, base_g, base_b = max(6, vr // 12), max(6, vg // 12), max(8, vb // 10)
    card = Image.new("RGBA", (W, H), (base_r, base_g, base_b, 255))

    # تدرج أفقي في الخلفية
    bg_grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bg_d = ImageDraw.Draw(bg_grad)
    for x in range(W):
        t = x / W
        # يذهب من داكن في اليسار إلى أكثر عمقاً في اليمين
        cr = int(lerp_color((base_r * 2, base_g * 2, base_b * 2), (base_r, base_g, base_b + 15), t)[0])
        cg = int(lerp_color((base_r * 2, base_g * 2, base_b * 2), (base_r, base_g, base_b + 15), t)[1])
        cb = int(lerp_color((base_r * 2, base_g * 2, base_b * 2), (base_r, base_g, base_b + 15), t)[2])
        bg_d.line([(x, 0), (x, H)], fill=(cr, cg, cb, 255))
    card = Image.alpha_composite(card, bg_grad)

    if splash_img:
        # ═══ الخلفية المضببة من صورة الشخصية ═══
        bg_blur = resize_cover(splash_img, W, H).filter(ImageFilter.GaussianBlur(60))
        dark_veil = Image.new("RGBA", (W, H), (base_r, base_g, base_b, 185))
        bg_layer = Image.alpha_composite(bg_blur.convert("RGBA"), dark_veil)
        card = Image.alpha_composite(card, bg_layer)

        # ═══ توهج بيئي (ambient glow) خلف الشخصية ═══
        card = draw_ambient_glow(card, cx=280 * S, cy=H // 2,
                                 color=accent, max_radius=360 * S, max_alpha=55)

        # ═══ رسم صورة الشخصية مع fade ناعم ═══
        splash_render = resize_cover(splash_img, 620 * S, H)
        mask = Image.new("L", splash_render.size, 255)
        mask_draw = ImageDraw.Draw(mask)

        # fade أفقي ناعم للجانب الأيمن
        fade_w = 210 * S
        for x in range(splash_render.width - fade_w, splash_render.width):
            t = (x - (splash_render.width - fade_w)) / fade_w
            alpha = int(255 * (1 - t ** 1.3))
            mask_draw.line([(x, 0), (x, splash_render.height)], fill=max(0, alpha))

        # fade ناعم للجزء السفلي
        fade_bot = 120 * S
        for y in range(H - fade_bot, H):
            t = (y - (H - fade_bot)) / fade_bot
            cur = mask.getpixel((0, y))
            new_val = int(cur * (1 - t ** 1.5))
            mask_draw.line([(0, y), (splash_render.width, y)], fill=max(0, new_val))

        card.paste(splash_render, (-30 * S, 0), mask)

    else:
        # توهج افتراضي بدون صورة
        card = draw_ambient_glow(card, cx=200 * S, cy=H // 2,
                                 color=accent, max_radius=280 * S, max_alpha=40)

    # ═══ خط فاصل متوهج بين القسم الأيسر والأوسط ═══
    card = draw_glow_line(card, 575 * S, 20 * S, 575 * S, H - 20 * S,
                          color=accent, width=2, glow_radius=10)

    # ═══ خط فاصل بين القسم الأوسط والأيمن ═══
    card = draw_glow_line(card, 1000 * S, 20 * S, 1000 * S, H - 20 * S,
                          color=(vr // 2, vg // 2, vb // 2), width=1, glow_radius=6)

    # ═══ لوحات زجاجية للأقسام ═══
    # قسم الوسط (light cone + traces + stats)
    card = draw_glass_rect(card, 578 * S, 10 * S, 415 * S, H - 20 * S,
                           fill_color=accent, alpha=18, radius=0)
    # قسم الريليكس
    card = draw_glass_rect(card, 1003 * S, 10 * S, 590 * S, H - 20 * S,
                           fill_color=(vr // 2, vg // 2, vb // 2), alpha=15, radius=0)

    draw = ImageDraw.Draw(card)

    # ═══ الخطوط ═══
    font_large = get_sharp_font(52 * S, bold=True)
    font_title = get_sharp_font(30 * S, bold=True)
    font_bold  = get_sharp_font(21 * S, bold=True)
    font_sub   = get_sharp_font(18 * S, bold=False)
    font_small = get_sharp_font(15 * S, bold=False)

    def glow_text(draw_obj, pos, text, font, color, glow_color=None, glow_passes=3):
        """رسم نص مع توهج خفيف"""
        if glow_color is None:
            glow_color = accent
        x, y = pos
        gr, gg, gb = glow_color[:3]
        for off in range(glow_passes, 0, -1):
            a = int(80 / off)
            for dx, dy in [(-off, 0), (off, 0), (0, -off), (0, off)]:
                draw_obj.text((x + dx, y + dy), text, font=font, fill=(gr, gg, gb, a))
        draw_obj.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, 200))
        draw_obj.text(pos, text, font=font, fill=color)

    # ═══ الإيدولونز ═══
    rank       = char_data.get("rank", 0)
    rank_icons = char_data.get("rank_icons", [])
    eid_x      = 520 * S
    eid_start  = 65 * S
    e_size     = 46 * S

    for i in range(6):
        e_y = eid_start + i * 78 * S
        e_bg = Image.new("RGBA", (e_size, e_size), (0, 0, 0, 0))
        e_d  = ImageDraw.Draw(e_bg)

        is_unlocked = i < rank
        if is_unlocked:
            fill_c   = (vr // 2 + 120, vg // 2 + 80, 30, 230)
            border_c = gold + (255,)
            # توهج للمفتوح
            glow_bg = Image.new("RGBA", (e_size + 16 * S, e_size + 16 * S), (0, 0, 0, 0))
            glow_d = ImageDraw.Draw(glow_bg)
            for r_off in range(8 * S, 0, -S):
                ga = int(40 * (1 - r_off / (8 * S)))
                glow_d.ellipse([8*S - r_off, 8*S - r_off, 8*S + e_size + r_off, 8*S + e_size + r_off],
                               fill=gold + (ga,))
            card.paste(glow_bg, (eid_x - 8 * S, e_y - 8 * S), glow_bg)
        else:
            fill_c   = (25, 28, 45, 200)
            border_c = (80, 85, 110, 160)

        e_d.ellipse([0, 0, e_size, e_size], fill=fill_c, outline=border_c, width=2 * S)

        if i < len(rank_icons):
            e_icon = await get_cached_icon(session, rank_icons[i], (32 * S, 32 * S))
            if e_icon:
                ix = (e_size - 32 * S) // 2
                if not is_unlocked:
                    e_icon = e_icon.convert("LA").convert("RGBA")
                    e_icon.putalpha(e_icon.split()[3].point(lambda p: int(p * 0.25)))
                e_bg.paste(e_icon, (ix, ix), e_icon)

        card.paste(e_bg, (eid_x, e_y), e_bg)

    # ═══ اسم الشخصية والمعلومات (أسفل اليسار) ═══
    name_y = 528 * S
    glow_text(draw, (40 * S, name_y), char_name.upper(), font_large,
              white, glow_color=accent, glow_passes=4)

    # خط تحت الاسم بلون التمييز
    line_y = name_y + 88 * S
    card = draw_glow_line(card, 40 * S, line_y, 40 * S + len(char_name) * 26 * S, line_y,
                          color=accent, width=2, glow_radius=5)
    draw = ImageDraw.Draw(card)

    glow_text(draw, (40 * S, name_y + 98 * S), f"LEVEL {char_level} / 80",
              font_title, gold, glow_color=gold, glow_passes=2)

    p_name = player_data.get("nickname", "Unknown")
    p_uid  = player_data.get("uid", "-")
    draw.text((40 * S, name_y + 168 * S), f"{p_name}  •  UID {p_uid}",
              font=font_sub, fill=(200, 205, 220, 210))

    # ═══ Light Cone (السلاح) ═══
    equip = char_data.get("light_cone", {})
    if equip:
        lc_name = equip.get("name", "Unknown LC")
        lc_level = equip.get("level", "-")
        lc_rank  = equip.get("rank", 1)
        lc_id    = str(equip.get("id", ""))

        lc_x, lc_y = 585 * S, 30 * S

        lc_img = None
        if lc_id:
            lc_url = (f"https://raw.githubusercontent.com/Mar-7th/StarRailRes"
                      f"/master/image/light_cone_portrait/{lc_id}.png")
            lc_img = await fetch_image(session, lc_url)
            if lc_img:
                lc_img = resize_cover(lc_img, 95 * S, 120 * S)

        if not lc_img:
            lc_img = await get_cached_icon(session, equip.get("icon", ""), (95 * S, 120 * S))

        if lc_img:
            # إطار متوهج حول صورة السلاح
            frame = Image.new("RGBA", (95 * S + 8, 120 * S + 8), (0, 0, 0, 0))
            fd = ImageDraw.Draw(frame)
            fd.rounded_rectangle([0, 0, 95 * S + 7, 120 * S + 7], radius=6,
                                  outline=accent + (150,), width=2)
            card.paste(frame, (lc_x - 4, lc_y - 4), frame)
            card.paste(lc_img, (lc_x, lc_y), lc_img)
            draw = ImageDraw.Draw(card)

        glow_text(draw, (lc_x + 110 * S, lc_y + 2 * S), lc_name[:22], font_bold,
                  gold, glow_color=gold, glow_passes=2)
        draw.text((lc_x + 110 * S, lc_y + 44 * S),
                  f"Lv. {lc_level}  |  S{lc_rank}",
                  font=font_sub, fill=(200, 205, 220, 210))

        lc_props = equip.get("properties", [])
        prop_text = ""
        for p in lc_props[:2]:
            p_nm = str(p.get("type", "")).replace("AddedRatio", "").replace("Delta", "")
            p_val = p.get("value", 0)
            prop_text += f"{p_nm}: {p_val*100:.1f}%  " if p_val < 3.0 else f"{p_nm}: {int(p_val)}  "
        if not prop_text:
            prop_text = "Standard Passive Active"
        draw.text((lc_x + 110 * S, lc_y + 86 * S), prop_text[:42],
                  font=font_small, fill=(170, 175, 195, 200))

    # ═══ المهارات (Traces) ═══
    skills = char_data.get("skills", [])
    tr_x, tr_y = 585 * S, 195 * S
    if skills:
        # رأس القسم مع خط تحته
        glow_text(draw, (tr_x, tr_y), "TRACES", font_bold, accent, glow_color=accent, glow_passes=2)
        card = draw_glow_line(card, tr_x, tr_y + 34 * S, tr_x + 380 * S, tr_y + 34 * S,
                              color=accent, width=1, glow_radius=4)
        draw = ImageDraw.Draw(card)

        t_y = tr_y + 50 * S
        for si, skill in enumerate(skills[:4]):
            sk_name  = skill.get("name", "Skill")
            sk_level = skill.get("level", 1)
            sk_max   = skill.get("max_level", 10)
            sk_icon  = skill.get("icon", "")

            # تلوين خلفية السطر المتبادل
            row_col = accent + (12,) if si % 2 == 0 else (255, 255, 255, 6)
            row_layer = Image.new("RGBA", card.size, (0, 0, 0, 0))
            rd = ImageDraw.Draw(row_layer)
            rd.rectangle([tr_x - 5, t_y - 3, tr_x + 390 * S, t_y + 45 * S],
                          fill=row_col)
            card = Image.alpha_composite(card, row_layer)
            draw = ImageDraw.Draw(card)

            if sk_icon:
                sk_img = await get_cached_icon(session, sk_icon, (44 * S, 44 * S))
                if sk_img:
                    card.paste(sk_img, (tr_x, t_y - 2 * S), sk_img)
                    draw = ImageDraw.Draw(card)

            draw.text((tr_x + 55 * S, t_y + 6 * S), sk_name[:18],
                      font=font_small, fill=(220, 225, 240, 230))
            glow_text(draw, (tr_x + 330 * S, t_y + 4 * S), f"Lv.{sk_level}/{sk_max}",
                      font_bold, gold, glow_color=gold, glow_passes=1)
            t_y += 48 * S

    # ═══ الإحصائيات (Combat Stats) ═══
    stat_x, stat_y = 585 * S, 415 * S
    glow_text(draw, (stat_x, stat_y), "COMBAT STATS", font_bold, accent, glow_color=accent, glow_passes=2)
    card = draw_glow_line(card, stat_x, stat_y + 34 * S, stat_x + 380 * S, stat_y + 34 * S,
                          color=accent, width=1, glow_radius=4)
    draw = ImageDraw.Draw(card)

    s_y = stat_y + 46 * S
    for si, stat in enumerate(rendered_stats[:7]):
        # خلفية متبادلة للصفوف
        row_col = accent + (12,) if si % 2 == 0 else (255, 255, 255, 6)
        row_layer = Image.new("RGBA", card.size, (0, 0, 0, 0))
        rd = ImageDraw.Draw(row_layer)
        rd.rectangle([stat_x - 5, s_y - 2, stat_x + 390 * S, s_y + 36 * S], fill=row_col)
        card = Image.alpha_composite(card, row_layer)
        draw = ImageDraw.Draw(card)

        if stat["icon"]:
            s_img = await get_cached_icon(session, stat["icon"], (35 * S, 35 * S))
            if s_img:
                card.paste(s_img, (stat_x, s_y), s_img)
                draw = ImageDraw.Draw(card)

        draw.text((stat_x + 48 * S, s_y + 2 * S), stat["name"],
                  font=font_sub, fill=(200, 210, 230, 220))

        try:
            val_w = draw.textlength(stat["value"], font=font_bold)
        except AttributeError:
            val_w = len(stat["value"]) * 14 * S

        glow_text(draw, (980 * S - int(val_w), s_y + 2 * S), stat["value"],
                  font_bold, gold, glow_color=gold, glow_passes=1)
        s_y += 42 * S

    # ═══ الريليكس (Relics) ═══
    relics = char_data.get("relics", []) or char_data.get("relicList", []) or []
    for idx, r in enumerate(relics[:6]):
        box_y = (45 + idx * 124) * S
        box_x = 1010 * S

        r_lvl  = r.get("level", 0)
        r_icon = r.get("icon", "")

        # لوحة زجاجية لكل ريليك
        card = draw_glass_rect(card, box_x - 5, box_y - 3, 585 * S, 118 * S,
                               fill_color=accent, alpha=10, radius=8)
        # خط علوي ملون لكل بطاقة
        card = draw_glow_line(card, box_x - 5, box_y - 3, box_x + 580 * S, box_y - 3,
                              color=accent, width=1, glow_radius=3)
        draw = ImageDraw.Draw(card)

        if r_icon:
            r_img = await get_cached_icon(session, r_icon, (95 * S, 95 * S))
            if r_img:
                card.paste(r_img, (box_x, box_y + 2 * S), r_img)
                draw = ImageDraw.Draw(card)

        glow_text(draw, (box_x + 105 * S, box_y + 2 * S), f"+{r_lvl}",
                  font_bold, gold, glow_color=gold, glow_passes=1)

        main_stat = r.get("main_affix", {})
        m_name    = main_stat.get("name", "")
        m_display = main_stat.get("display", "")
        if not m_display:
            m_val     = main_stat.get("value", 0)
            m_display = f"{m_val*100:.1f}%" if main_stat.get("percent") else str(int(m_val))

        draw.text((box_x + 175 * S, box_y + 2 * S), f"{m_name}: {m_display}",
                  font=font_bold, fill=(230, 235, 250, 240))

        substats = r.get("sub_affix", [])
        for i, sub in enumerate(substats[:4]):
            s_name    = sub.get("name", "")
            s_display = sub.get("display", "")
            if not s_display:
                s_val     = sub.get("value", 0)
                s_display = f"{s_val*100:.1f}%" if sub.get("percent") else str(int(s_val))

            # تلوين الإحصائيات المهمة
            stat_text = f"{s_name}: {s_display}"
            is_crit   = "crit" in s_name.lower() or "crit" in s_display.lower()
            sub_color = (255, 200, 80, 230) if is_crit else (185, 195, 215, 210)

            sub_x = box_x + 105 * S if i % 2 == 0 else box_x + 355 * S
            sub_y = box_y + 44 * S if i < 2 else box_y + 78 * S
            draw.text((sub_x, sub_y), stat_text, font=font_small, fill=sub_color)

    # ═══ التشطيب النهائي ═══
    # vignette (تعتيم الأطراف) لإضافة عمق
    vignette = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    v_draw   = ImageDraw.Draw(vignette)
    steps = 80
    for i in range(steps, 0, -1):
        t      = i / steps
        alpha  = int(140 * t ** 2.5)
        margin = int((steps - i) * 8)
        v_draw.rectangle([margin, margin, W - margin, H - margin],
                          outline=(0, 0, 0, alpha), width=8)
    card = Image.alpha_composite(card, vignette)

    # تصغير وتحسين الحدة
    card = card.resize((1600, 800), Image.Resampling.LANCZOS)
    card = card.filter(ImageFilter.UnsharpMask(radius=1, percent=120, threshold=2))

    buf = BytesIO()
    card.save(buf, format="PNG", optimize=False)
    buf.seek(0)
    return buf

# نظام شخصيات Honkai: Star Rail
async def hsr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "الرجاء إدخال الـ UID الخاص بك بعد الأمر، مثال:\n<code>/hsr 701021140</code>",
            parse_mode="HTML",
            reply_to_message_id=update.message.message_id
        )
        return

    uid = context.args[0]
    url = f"https://api.mihomo.me/sr_info_parsed/{uid}?lang=en"

    # الإصلاح: استخدام aiohttp.ClientTimeout الصحيح
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=timeout) as response:
                if response.status != 200:
                    await update.message.reply_text(
                        "❌ تعذر جلب بيانات الحساب. تأكد من صحة الـ UID وأن معرض الحساب عام في اللعبة.",
                        reply_to_message_id=update.message.message_id
                    )
                    return
                data = await response.json()
        except Exception as e:
            await update.message.reply_text(
                f"❌ حدث خطأ في الاتصال بالخادم: {escape_html(str(e))}",
                parse_mode="HTML",
                reply_to_message_id=update.message.message_id
            )
            return

    try:
        avatars = data.get("characters", []) or data.get("avatar_list", [])
        if not avatars:
            await update.message.reply_text(
                "❌ لا توجد شخصيات معروضة في هذا الحساب حالياً.",
                reply_to_message_id=update.message.message_id
            )
            return

        keyboard = []
        row = []
        for idx, char in enumerate(avatars):
            name = char.get("name", f"شخصية #{idx + 1}")
            # الإصلاح: استخدام فاصل مختلف بدلاً من _ لتجنب مشاكل التقسيم
            row.append(InlineKeyboardButton(name, callback_data=f"hsr|{uid}|{idx}"))
            if len(row) == 4:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        reply_markup = InlineKeyboardMarkup(keyboard)
        player = data.get("player", {})
        nickname = escape_html(player.get("nickname", "Player"))

        await update.message.reply_text(
            f"👤 <b>اللاعب:</b> {nickname}\n✨ <b>اختر الشخصية لتصميم بطاقتها الاحترافية:</b>",
            reply_markup=reply_markup,
            parse_mode="HTML",
            reply_to_message_id=update.message.message_id
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ حدث خطأ أثناء معالجة البيانات: {escape_html(str(e))}",
            parse_mode="HTML"
        )

async def hsr_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("جاري تصميم البطاقة الفنية...", show_alert=False)

    # الإصلاح: استخدام | كفاصل بدلاً من _ لأن الـ UID قد يحتوي على رموز
    data_parts = query.data.split("|")
    if len(data_parts) < 3:
        await query.message.reply_text("❌ بيانات غير صالحة.")
        return

    uid = data_parts[1]
    char_idx = int(data_parts[2])

    url = f"https://api.mihomo.me/sr_info_parsed/{uid}?lang=en"

    timeout = aiohttp.ClientTimeout(total=30)
    # استخدام ssl=False لضمان جلب الصور من GitHub بدون مشاكل الشهادات
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            async with session.get(url, timeout=timeout, allow_redirects=True) as response:
                if response.status != 200:
                    await query.message.reply_text("❌ انتهت صلاحية البيانات أو حدث خطأ أثناء الاتصال بالسيرفر.")
                    return
                data = await response.json()
        except Exception as e:
            await query.message.reply_text(f"❌ حدث خطأ أثناء جلب البيانات: {escape_html(str(e))}", parse_mode="HTML")
            return

        avatars = data.get("characters", []) or data.get("avatar_list", [])
        player_data = data.get("player", {})

        if char_idx >= len(avatars):
            await query.message.reply_text("❌ لم يتم العثور على الشخصية المطلوبة.")
            return

        char_data = avatars[char_idx]
        name = escape_html(char_data.get("name", "Unknown"))

        try:
            card_buf = await create_character_card(session, char_data, player_data)

            reply_id = None
            if query.message.reply_to_message:
                reply_id = query.message.reply_to_message.message_id
            else:
                reply_id = query.message.message_id

            await query.message.reply_photo(
                photo=card_buf,
                caption=f"✨ <b>إحصائيات وبيلد {name} الاحترافي</b>",
                parse_mode="HTML",
                reply_to_message_id=reply_id
            )
        except Exception as e:
            logging.error(f"Error drawing card: {e}")
            await query.message.reply_text(
                f"❌ حدث خطأ أثناء معالجة الصورة الفنية: {escape_html(str(e))}",
                parse_mode="HTML"
            )

# مراقب الرسائل والرد الذكي
async def message_listener(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message or not update.message.text:
        return

    user = update.effective_user
    track_message(user)

    text = update.message.text.strip()

    # التقاط كلمة ايدي بأكثر من شكل
    if text in ["ايدي", "أيدي", "إيدي", "ايدى"]:
        await id_command(update, context)
        return

    # الرد على المنشن أو الرد على البوت
    bot_username = context.application.bot_data.get("username")
    is_mentioned = bot_username and (f"@{bot_username}" in text)
    is_reply_to_bot = (
        update.message.reply_to_message is not None
        and update.message.reply_to_message.from_user is not None
        and update.message.reply_to_message.from_user.id == context.bot.id
    )

    if is_mentioned or is_reply_to_bot:
        full_name = escape_html(f"{user.first_name} {user.last_name or ''}".strip())
        ai_reply_text = (
            f"مرحباً يا {full_name}! 🤖✨\n\n"
            "أنا مساعدك الذكي في هذا الجروب. لقد رصدت إشارتك لي بنجاح! 🚀\n\n"
            "إليك ما يمكنني مساعدتك به حالياً:\n"
            "• اكتب كلمة <b>ايدي</b> لعرض معلومات حسابك وعدد رسائلك في المجموعة كـ Reply.\n"
            "• استخدم الأمر <code>/hsr [UID]</code> لعرض تفاصيل بيلدات شخصياتك في لعبة Honkai: Star Rail ببطاقة فنية احترافية!"
        )
        await update.message.reply_text(
            ai_reply_text,
            parse_mode="HTML",
            reply_to_message_id=update.message.message_id
        )

# السيرفر الوهمي (keep alive)
app_web = Flask('')

@app_web.route('/')
def home():
    return "Bot is alive and running!"

def run_web():
    app_web.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_web)
    t.daemon = True
    t.start()

async def post_init(application):
    await download_fonts_on_startup()
    bot_info = await application.bot.get_me()
    application.bot_data["username"] = bot_info.username
    logging.info(f"🤖 Bot @{bot_info.username} is fully initialized and ready!")

def main():
    TOKEN = os.environ.get("BOT_TOKEN")
    if not TOKEN:
        logging.error("Error: BOT_TOKEN environment variable not found!")
        return

    application = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    application.add_handler(CommandHandler("id", id_command))
    application.add_handler(CommandHandler("hsr", hsr_command))
    application.add_handler(CommandHandler("start", hsr_command))
    # الإصلاح: تحديث pattern ليتوافق مع الفاصل الجديد |
    application.add_handler(CallbackQueryHandler(hsr_callback, pattern=r"^hsr\|"))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message_listener))

    keep_alive()
    logging.info("Bot is up and running...")
    application.run_polling()

if __name__ == '__main__':
    main()
