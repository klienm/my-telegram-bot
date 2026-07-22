import os
import math
import asyncio
import sqlite3
import logging
import aiohttp
import tempfile
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageFilter
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


# ---------------------------------------------------------------------------
# قاعدة البيانات لتخزين الرسائل
# ---------------------------------------------------------------------------

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


def get_user_message_count(user_id):
    conn = sqlite3.connect('messages.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('SELECT message_count FROM user_messages WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0


def escape_html(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ---------------------------------------------------------------------------
# أمر الايدي
# ---------------------------------------------------------------------------

async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return

    track_message(user)

    msg_count = get_user_message_count(user.id)
    full_name = escape_html(f"{user.first_name} {user.last_name or ''}".strip())
    username = f"@{escape_html(user.username)}" if user.username else "لا يوجد"
    gender_text = "ولد"

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


# ---------------------------------------------------------------------------
# أدوات مساعدة للتصميم
# ---------------------------------------------------------------------------

async def fetch_image(session, url):
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with session.get(url, timeout=timeout) as response:
            if response.status == 200:
                content = await response.read()
                return Image.open(BytesIO(content)).convert("RGBA")
            else:
                logging.warning(f"fetch_image: status {response.status} for {url}")
    except Exception as e:
        logging.warning(f"fetch_image error for {url}: {e}")
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


def get_dominant_color(img):
    resample_filter = getattr(Image, "Resampling", None)
    box_filter = resample_filter.BOX if resample_filter else getattr(Image, "BOX", Image.NEAREST)
    tiny_img = img.resize((1, 1), box_filter)
    avg_pixel = tiny_img.getpixel((0, 0))
    return int(avg_pixel[0]), int(avg_pixel[1]), int(avg_pixel[2])


def draw_soft_text(base_img, draw, position, text, font, fill,
                    shadow_color=(0, 0, 0, 175), blur_radius=5, offset=(0, 4)):
    """
    يرسم نص فوق ظل ناعم (مموّه) بدل ظل حاد بأوفست، حتى يصير شكل الكارد أهدأ للعين.
    يشتغل بس على مساحة النص (مو الكارد كامل) حتى يضل سريع.
    """
    x, y = position
    bbox = draw.textbbox((x, y), text, font=font)
    pad = blur_radius * 3 + 12
    layer_w = max(1, (bbox[2] - bbox[0]) + pad * 2)
    layer_h = max(1, (bbox[3] - bbox[1]) + pad * 2)

    shadow_layer = Image.new("RGBA", (layer_w, layer_h), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)
    shadow_draw.text((pad + offset[0], pad + offset[1]), text, font=font, fill=shadow_color)
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(blur_radius))

    base_img.alpha_composite(shadow_layer, (int(bbox[0] - pad), int(bbox[1] - pad)))
    draw.text((x, y), text, font=font, fill=fill)


def draw_panel(base_img, box, radius=22, fill=(8, 10, 20, 145), outline=(255, 255, 255, 30), outline_width=2):
    """لوحة خلفية شبه شفافة بزوايا دائرية، تحسن التباين وتخلي كل قسم يبين مرتب وواضح."""
    x0, y0, x1, y1 = [int(v) for v in box]
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return
    panel = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panel)
    panel_draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=fill, outline=outline, width=outline_width)
    base_img.alpha_composite(panel, (x0, y0))


# ---------------------------------------------------------------------------
# دالة رسم بطاقة الشخصية
# ---------------------------------------------------------------------------

async def create_character_card(session, char_data, player_data):
    SCALE = 2
    W, H = 1600 * SCALE, 800 * SCALE

    char_name = char_data.get("name", "Character")
    char_level = char_data.get("level", 1)
    char_id = str(char_data.get("id", ""))

    # دمج attributes + additions لحساب القيمة النهائية لكل ستات
    final_stats = {}
    for attr in char_data.get("attributes", []):
        field = attr["field"]
        final_stats[field] = {
            "name": attr["name"],
            "icon": attr["icon"],
            "value": attr["value"],
            "is_percent": attr.get("percent", False) or field in ['crit_rate', 'crit_dmg', 'effect_hit', 'effect_res', 'break_effect']
        }

    for add in char_data.get("additions", []):
        field = add["field"]
        if field in final_stats:
            final_stats[field]["value"] += add["value"]
        else:
            final_stats[field] = {
                "name": add["name"],
                "icon": add["icon"],
                "value": add["value"],
                "is_percent": add.get("percent", False) or field in ['crit_rate', 'crit_dmg', 'effect_hit', 'effect_res', 'break_effect']
            }

    stat_order = ["hp", "atk", "def", "spd", "crit_rate", "crit_dmg", "break_effect", "effect_hit", "effect_res", "heal_rate", "sp_rate"]
    rendered_stats = []
    for field in stat_order:
        if field in final_stats:
            stat = final_stats[field]
            val = stat["value"]
            display_val = f"{val * 100:.1f}%" if stat["is_percent"] else str(int(math.floor(val)))
            rendered_stats.append({"name": stat["name"], "value": display_val, "icon": stat["icon"]})

    # ---------------- الخلفية ----------------
    card = Image.new("RGBA", (W, H), (12, 12, 18, 255))

    splash_img = None
    if char_id:
        portrait_url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/image/character_portrait/{char_id}.png"
        splash_img = await fetch_image(session, portrait_url)

    if splash_img:
        dom_r, dom_g, dom_b = get_dominant_color(splash_img)
        text_highlight = (min(255, dom_r + 140), min(255, dom_g + 140), min(255, dom_b + 140), 255)

        # نبني خلفية معتمة بالكامل (بدون أي ثقوب شفافية) عشان الألوان تطلع نظيفة
        bg_base = Image.new("RGBA", (W, H), (dom_r // 7, dom_g // 7, dom_b // 7, 255))
        bg_blur = resize_cover(splash_img, W, H).filter(ImageFilter.GaussianBlur(130))
        bg_base.alpha_composite(bg_blur, (0, 0))
        card.alpha_composite(bg_base, (0, 0))

        splash_render = resize_cover(splash_img, 620 * SCALE, 800 * SCALE)

        # ظل ناعم خلف الشخصية لإحساس بالعمق
        silhouette = Image.new("RGBA", splash_render.size, (0, 0, 0, 0))
        silhouette.putalpha(splash_render.split()[3].point(lambda a: int(a * 0.5)))
        silhouette = silhouette.filter(ImageFilter.GaussianBlur(16 * SCALE))
        card.alpha_composite(silhouette, (-50 * SCALE + 8 * SCALE, 8 * SCALE))

        # قناع تلاشي أفقي حتى تندمج الشخصية بالخلفية بسلاسة
        mask = Image.new("L", splash_render.size, 255)
        mask_draw = ImageDraw.Draw(mask)
        fade_width = 240 * SCALE
        for x in range(splash_render.width - fade_width, splash_render.width):
            alpha = int(255 * (1 - (x - (splash_render.width - fade_width)) / fade_width))
            mask_draw.line([(x, 0), (x, splash_render.height)], fill=alpha)

        card.paste(splash_render, (-50 * SCALE, 0), mask)
    else:
        text_highlight = (255, 215, 100, 255)

    draw = ImageDraw.Draw(card)

    font_large = get_sharp_font(52 * SCALE, bold=True)
    font_title = get_sharp_font(30 * SCALE, bold=True)
    font_bold = get_sharp_font(21 * SCALE, bold=True)
    font_sub = get_sharp_font(18 * SCALE, bold=False)
    font_small = get_sharp_font(15 * SCALE, bold=False)

    # ---------------- الإيدولونز ----------------
    rank = char_data.get("rank", 0)
    rank_icons = char_data.get("rank_icons", [])
    eidolon_start_y = 65 * SCALE
    eidolon_x = 520 * SCALE
    e_size = 46 * SCALE

    draw_panel(card, (eidolon_x - 14 * SCALE, eidolon_start_y - 14 * SCALE,
                       eidolon_x + e_size + 14 * SCALE, eidolon_start_y + 6 * (78 * SCALE) - 32 * SCALE))

    for i in range(6):
        e_y = eidolon_start_y + (i * 78 * SCALE)
        e_bg = Image.new("RGBA", (e_size, e_size), (0, 0, 0, 0))
        e_draw = ImageDraw.Draw(e_bg)

        is_unlocked = i < rank
        if is_unlocked:
            fill_color = (245, 165, 35, 240)
            border_color = (255, 220, 100, 255)
        else:
            fill_color = (40, 40, 55, 230)
            border_color = (110, 110, 130, 200)

        e_draw.ellipse([0, 0, e_size, e_size], fill=fill_color, outline=border_color, width=2 * SCALE)

        if i < len(rank_icons):
            e_icon = await get_cached_icon(session, rank_icons[i], (32 * SCALE, 32 * SCALE))
            if e_icon:
                icon_pos = ((e_size - 32 * SCALE) // 2, (e_size - 32 * SCALE) // 2)
                if not is_unlocked:
                    e_icon = e_icon.convert("LA").convert("RGBA")
                    e_icon.putalpha(e_icon.split()[3].point(lambda p: p * 0.3))
                e_bg.paste(e_icon, icon_pos, e_icon)

        card.paste(e_bg, (eidolon_x, e_y), e_bg)

    # ---------------- معلومات الشخصية (اسم + مستوى + لاعب) ----------------
    name_y = 530 * SCALE
    draw_panel(card, (20 * SCALE, 500 * SCALE, 950 * SCALE, 770 * SCALE))

    draw_soft_text(card, draw, (40 * SCALE, name_y), char_name.upper(), font_large, (255, 255, 255, 255))
    draw_soft_text(card, draw, (40 * SCALE, name_y + 105 * SCALE), f"LEVEL {char_level} / 80", font_title, text_highlight)

    p_name = player_data.get("nickname", "Unknown")
    p_uid = player_data.get("uid", "-")
    draw_soft_text(card, draw, (40 * SCALE, name_y + 175 * SCALE), f"{p_name}  •  UID {p_uid}", font_sub, (255, 255, 255, 255))

    # مجموعات الريليكس المفعّلة (2pc / 4pc)
    relic_sets = char_data.get("relic_sets", [])
    if relic_sets:
        set_text = "   ".join(f"{s.get('num', 0)}pc {s.get('name', '')}" for s in relic_sets[:3])
        draw_soft_text(card, draw, (40 * SCALE, name_y + 215 * SCALE), set_text[:60], font_small, text_highlight)

    # ---------------- السلاح (Light Cone) ----------------
    equip = char_data.get("light_cone", {})
    if equip:
        lc_x, lc_y = 585 * SCALE, 40 * SCALE
        draw_panel(card, (lc_x - 15 * SCALE, lc_y - 15 * SCALE, 990 * SCALE, lc_y + 150 * SCALE))

        lc_name = equip.get("name", "Unknown LC")
        lc_level = equip.get("level", "-")
        lc_rank = equip.get("rank", 1)
        lc_id = str(equip.get("id", ""))

        lc_img = None
        if lc_id:
            lc_portrait_url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/image/light_cone_portrait/{lc_id}.png"
            lc_img = await fetch_image(session, lc_portrait_url)
            if lc_img:
                lc_img = resize_cover(lc_img, 95 * SCALE, 120 * SCALE)

        if not lc_img:
            lc_icon = equip.get("icon", "")
            lc_img = await get_cached_icon(session, lc_icon, (95 * SCALE, 120 * SCALE))

        if lc_img:
            card.paste(lc_img, (lc_x, lc_y), lc_img)

        draw_soft_text(card, draw, (lc_x + 110 * SCALE, lc_y + 2 * SCALE), f"{lc_name[:22]}", font_bold, text_highlight)
        draw_soft_text(card, draw, (lc_x + 110 * SCALE, lc_y + 44 * SCALE), f"Lv. {lc_level}  |  Superimposition {lc_rank}", font_sub, (255, 255, 255, 255))

        lc_props = equip.get("properties", [])
        prop_parts = []
        for p in lc_props[:3]:
            p_name = p.get("name") or p.get("type", "")
            p_display = p.get("display") or (
                f"{p.get('value', 0) * 100:.1f}%" if p.get("percent") else str(int(p.get("value", 0)))
            )
            prop_parts.append(f"{p_name}: {p_display}")
        prop_text = "   ".join(prop_parts) if prop_parts else "Passive effect active"

        draw_soft_text(card, draw, (lc_x + 110 * SCALE, lc_y + 86 * SCALE), prop_text[:42], font_small, (255, 255, 255, 255))

    # ---------------- المهارات (Skills) ----------------
    skills = char_data.get("skills", [])
    if skills:
        tr_x, tr_y = 585 * SCALE, 195 * SCALE
        draw_panel(card, (tr_x - 15 * SCALE, tr_y - 15 * SCALE, 990 * SCALE, tr_y + 15 * SCALE + 4 * 48 * SCALE))

        draw_soft_text(card, draw, (tr_x, tr_y), "SKILLS & ABILITIES", font_bold, text_highlight)

        t_y = tr_y + 50 * SCALE
        for skill in skills[:4]:
            sk_name = skill.get("name", "Skill")
            sk_level = skill.get("level", 1)
            sk_max = skill.get("max_level", 10)
            sk_icon = skill.get("icon", "")

            if sk_icon:
                sk_img = await get_cached_icon(session, sk_icon, (44 * SCALE, 44 * SCALE))
                if sk_img:
                    card.paste(sk_img, (tr_x, t_y - 2 * SCALE), sk_img)

            draw_soft_text(card, draw, (tr_x + 55 * SCALE, t_y + 4 * SCALE), sk_name[:16], font_small, (255, 255, 255, 255))
            draw_soft_text(card, draw, (tr_x + 330 * SCALE, t_y + 4 * SCALE), f"Lv.{sk_level}/{sk_max}", font_bold, text_highlight)
            t_y += 48 * SCALE

    # ---------------- الإحصائيات (Stats) ----------------
    stat_start_x, stat_start_y = 585 * SCALE, 415 * SCALE
    draw_panel(card, (stat_start_x - 15 * SCALE, stat_start_y - 15 * SCALE, 990 * SCALE, stat_start_y + 46 * SCALE + 7 * 42 * SCALE))

    draw_soft_text(card, draw, (stat_start_x, stat_start_y), "COMBAT STATS", font_bold, text_highlight)

    s_y = stat_start_y + 46 * SCALE
    for stat in rendered_stats[:7]:
        s_icon = stat["icon"]
        if s_icon:
            s_img = await get_cached_icon(session, s_icon, (35 * SCALE, 35 * SCALE))
            if s_img:
                card.paste(s_img, (stat_start_x, s_y), s_img)

        draw_soft_text(card, draw, (stat_start_x + 48 * SCALE, s_y + 2 * SCALE), stat["name"], font_sub, (255, 255, 255, 255))

        try:
            val_width = draw.textlength(stat["value"], font=font_bold)
        except AttributeError:
            val_width = len(stat["value"]) * 14 * SCALE

        draw_soft_text(card, draw, (980 * SCALE - val_width, s_y + 2 * SCALE), stat["value"], font_bold, text_highlight)
        s_y += 42 * SCALE

    # ---------------- الريليكس (Relics) ----------------
    relics = char_data.get("relics", [])
    if relics:
        draw_panel(card, (995 * SCALE, 30 * SCALE, 1580 * SCALE, 30 * SCALE + min(len(relics), 6) * 124 * SCALE + 20 * SCALE))

    for idx, r in enumerate(relics[:6]):
        box_y = (45 + (idx * 124)) * SCALE
        box_x = 1010 * SCALE

        r_lvl = r.get("level", 0)
        r_icon = r.get("icon", "")
        if r_icon:
            r_img = await get_cached_icon(session, r_icon, (95 * SCALE, 95 * SCALE))
            if r_img:
                card.paste(r_img, (box_x, box_y + 2 * SCALE), r_img)

        draw_soft_text(card, draw, (box_x + 105 * SCALE, box_y + 2 * SCALE), f"+{r_lvl}", font_bold, text_highlight)

        main_stat = r.get("main_affix", {})
        m_name = main_stat.get("name", "")
        m_display = main_stat.get("display", "")
        if not m_display:
            m_val = main_stat.get("value", 0)
            m_display = f"{m_val * 100:.1f}%" if main_stat.get("percent") else str(int(m_val))

        draw_soft_text(card, draw, (box_x + 175 * SCALE, box_y + 2 * SCALE), f"{m_name}: {m_display}", font_bold, (255, 255, 255, 255))

        substats = r.get("sub_affix", [])
        for i, sub in enumerate(substats[:4]):
            s_name = sub.get("name", "")
            s_display = sub.get("display", "")
            if not s_display:
                s_val = sub.get("value", 0)
                s_display = f"{s_val * 100:.1f}%" if sub.get("percent") else str(int(s_val))

            stat_text = f"{s_name}: {s_display}"
            sub_x = box_x + 105 * SCALE if i % 2 == 0 else box_x + 355 * SCALE
            sub_y = box_y + 44 * SCALE if i < 2 else box_y + 78 * SCALE
            draw_soft_text(card, draw, (sub_x, sub_y), stat_text, font_small, (255, 255, 255, 255))

    card = card.resize((1600, 800), Image.Resampling.LANCZOS)
    card = card.filter(ImageFilter.SHARPEN)

    buf = BytesIO()
    card.save(buf, format="PNG")
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# نظام شخصيات Honkai: Star Rail
# ---------------------------------------------------------------------------

async def hsr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "الرجاء إدخال الـ UID الخاص بك بعد الأمر، مثال:\n<code>/hsr 701021140</code>",
            parse_mode="HTML",
            reply_to_message_id=update.message.message_id
        )
        return

    uid = context.args[0].strip()
    url = f"https://api.mihomo.me/sr_info_parsed/{uid}?lang=en"

    timeout = aiohttp.ClientTimeout(total=20)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    async with aiohttp.ClientSession(headers=headers) as session:
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
            avatars = data.get("characters", [])
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

    data_parts = query.data.split("|")
    if len(data_parts) < 3:
        await query.message.reply_text("❌ بيانات غير صالحة.")
        return

    uid = data_parts[1].strip()
    char_idx = int(data_parts[2])

    url = f"https://api.mihomo.me/sr_info_parsed/{uid}?lang=en"

    timeout = aiohttp.ClientTimeout(total=30)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    # مهم جداً: كل شيء يحتاج الجلسة (جلب البيانات + رسم الكارد وتحميل الصور)
    # لازم يضل داخل نفس الـ async with، وإلا الجلسة تنغلق وتفشل كل الصور بصمت.
    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            async with session.get(url, timeout=timeout) as response:
                if response.status != 200:
                    await query.message.reply_text("❌ انتهت صلاحية البيانات أو حدث خطأ أثناء الاتصال بالسيرفر.")
                    return
                data = await response.json()
        except Exception as e:
            await query.message.reply_text(f"❌ حدث خطأ أثناء جلب البيانات: {escape_html(str(e))}", parse_mode="HTML")
            return

        avatars = data.get("characters", [])
        player_data = data.get("player", {})

        if char_idx >= len(avatars):
            await query.message.reply_text("❌ لم يتم العثور على الشخصية المطلوبة.")
            return

        char_data = avatars[char_idx]
        name = escape_html(char_data.get("name", "Unknown"))

        try:
            card_buf = await create_character_card(session, char_data, player_data)

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


# ---------------------------------------------------------------------------
# مراقب الرسائل والرد الذكي
# ---------------------------------------------------------------------------

async def message_listener(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message or not update.message.text:
        return

    user = update.effective_user
    track_message(user)

    text = update.message.text.strip()

    if text in ["ايدي", "أيدي", "إيدي", "ايدى"]:
        await id_command(update, context)
        return

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


# ---------------------------------------------------------------------------
# سيرفر وهمي (keep alive) - يعتمد على PORT من البيئة لو موجود (مهم لـ Render)
# ---------------------------------------------------------------------------

app_web = Flask('')


@app_web.route('/')
def home():
    return "Bot is alive and running!"


def run_web():
    port = int(os.environ.get("PORT", 8080))
    app_web.run(host='0.0.0.0', port=port)


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

    # إصلاح: بايثون 3.12+ ما عاد ينشئ event loop تلقائياً بالـ MainThread،
    # ومكتبة python-telegram-bot لسا بتعتمد على asyncio.get_event_loop() داخلياً.
    # ننشئ الـ loop يدوياً هون قبل أي شي حتى نتجنب:
    # "RuntimeError: There is no current event loop in thread 'MainThread'"
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    application = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    application.add_handler(CommandHandler("id", id_command))
    application.add_handler(CommandHandler("hsr", hsr_command))
    application.add_handler(CommandHandler("start", hsr_command))
    application.add_handler(CallbackQueryHandler(hsr_callback, pattern=r"^hsr\|"))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message_listener))

    keep_alive()
    logging.info("Bot is up and running...")
    application.run_polling()


if __name__ == '__main__':
    main()
