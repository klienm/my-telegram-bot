import os
import re
import httpx
import urllib.request
import sqlite3
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN")

# --- إعداد قاعدة البيانات لتسزين الرسائل ---
db_connection = sqlite3.connect("messages.db", check_same_thread=False)
db_cursor = db_connection.cursor()

db_cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_messages (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        message_count INTEGER DEFAULT 0
    )
""")
db_connection.commit()

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

def draw_shadow_text(draw, position, text, font, fill, shadow_fill=(0, 0, 0, 255), offset=(2, 2)):
    x, y = position
    draw.text((x + offset[0], y + offset[1]), text, font=font, fill=shadow_fill)
    draw.text((x, y), text, font=font, fill=fill)

DEJAVU_BOLD_URL = "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/resources/fonts/dejavu-fonts-ttf-2.37/ttf/DejaVuSans-Bold.ttf"
DEJAVU_REG_URL = "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/resources/fonts/dejavu-fonts-ttf-2.37/ttf/DejaVuSans.ttf"

LOCAL_BOLD_PATH = "DejaVuSans-Bold.ttf"
LOCAL_REG_PATH = "DejaVuSans.ttf"

def get_sharp_font(size, bold=True):
    sys_bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    sys_reg = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    
    selected_path = sys_bold if bold else sys_reg
    local_path = LOCAL_BOLD_PATH if bold else LOCAL_REG_PATH
    fallback_url = DEJAVU_BOLD_URL if bold else DEJAVU_REG_URL
    
    if os.path.exists(selected_path):
        try:
            return ImageFont.truetype(selected_path, size)
        except Exception:
            pass
            
    if os.path.exists(local_path):
        try:
            return ImageFont.truetype(local_path, size)
        except Exception:
            pass
            
    try:
        urllib.request.urlretrieve(fallback_url, local_path)
        return ImageFont.truetype(local_path, size)
    except Exception:
        return ImageFont.load_default()

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

    card = Image.new("RGBA", (1600, 800), (10, 12, 18, 255))
    draw = ImageDraw.Draw(card)

    font_large = get_sharp_font(34, bold=True)
    font_bold = get_sharp_font(18, bold=True)
    font_sub = get_sharp_font(15, bold=False)
    font_small = get_sharp_font(13, bold=False)

    splash_img = None
    if char_id:
        portrait_url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/image/character_portrait/{char_id}.png"
        splash_img = await fetch_image(client, portrait_url)

    if not splash_img and icon_path:
        splash_icon = icon_path.replace("icon/character", "image/character_portrait").replace("steps/", "")
        img_url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/{splash_icon}"
        splash_img = await fetch_image(client, img_url)

    highlight_color = (255, 215, 100, 255)
    subtitle_color = (150, 200, 255, 255)
    bg_color = (10, 12, 18, 255)

    if splash_img:
        box_filter = Image.Resampling.BOX if hasattr(Image, "Resampling") else Image.BOX
        tiny_img = splash_img.resize((1, 1), box_filter)
        avg_pixel = tiny_img.getpixel((0, 0))
        r, g, b = int(avg_pixel[0]), int(avg_pixel[1]), int(avg_pixel[2])
        
        bg_color = (int(r * 0.18), int(g * 0.18), int(b * 0.18), 255)
        bg_base = Image.new("RGBA", (1600, 800), bg_color)
        
        blurred_splash = resize_cover(splash_img, 1600, 800, focus_y=0.2)
        blurred_splash = blurred_splash.filter(ImageFilter.GaussianBlur(radius=30))
        
        bg_final = Image.blend(bg_base, blurred_splash, 0.55)
        card.paste(bg_final, (0, 0))

        max_c = max(r, g, b, 1)
        highlight_color = (
            int((r / max_c) * 0.75 * 255 + 255 * 0.25),
            int((g / max_c) * 0.75 * 255 + 255 * 0.25),
            int((b / max_c) * 0.75 * 255 + 255 * 0.25),
            255
        )
        subtitle_color = (
            int((r / max_c) * 0.5 * 255 + 255 * 0.2),
            int((g / max_c) * 0.5 * 255 + 255 * 0.2),
            int((b / max_c) * 0.5 * 255 + 255 * 0.2),
            255
        )
    else:
        card.paste(Image.new("RGBA", (1600, 800), bg_color), (0, 0))

    tint = Image.new("RGBA", (1600, 800), (8, 10, 16, 95))
    card = Image.alpha_composite(card, tint)
    draw = ImageDraw.Draw(card)

    all_stats = {}
    for stat in char_data.get("attributes", []) + char_data.get("properties", []):
        field = stat.get("field", "")
        name = stat.get("name", "")
        icon = stat.get("icon", "")
        val_str = stat.get("display", "")
        if not val_str:
            val_str = format_stat_value(name, stat.get("value", 0))
        if name:
            all_stats[field] = {"name": name, "value": val_str, "icon": icon}

    stat_order = ["hp", "atk", "def", "spd", "crit_rate", "crit_dmg", "break_effect", "sp_rate", "effect_hit", "effect_res", "heal_rate"]
    rendered_stats = []
    seen_fields = set()
    for field in stat_order:
        if field in all_stats:
            rendered_stats.append(all_stats[field])
            seen_fields.add(field)

    for field, stat in all_stats.items():
        if field not in seen_fields:
            if stat["value"] not in ["0", "0%", "0.0%"]:
                rendered_stats.append(stat)
                seen_fields.add(field)

    if splash_img:
        splash_crop = resize_cover(splash_img, 440, 720, focus_y=0.12)
        splash_styled = mask_rounded_fade(splash_crop, radius=24, fade_width=80)
        card.paste(splash_styled, (40, 40), splash_styled)
        
    grad_h = 300
    gradient = Image.new("RGBA", (440, grad_h), (0, 0, 0, 0))
    grad_draw = ImageDraw.Draw(gradient)
    for gy in range(grad_h):
        t = gy / grad_h
        grad_draw.line([(0, gy), (440, gy)], fill=(8, 10, 16, int(245 * (t ** 1.6))))
    card.paste(mask_rounded_fade(gradient, radius=24, fade_width=80), (40, 760 - grad_h))
    
    draw_shadow_text(draw, (75, 570), char_name.upper(), font_large, highlight_color)
    draw_shadow_text(draw, (75, 608), f"LEVEL {char_level} / 80", font_bold, (255, 240, 210, 255))
    draw_shadow_text(draw, (75, 650), f"{player_data.get('nickname', 'Unknown')}  •  UID {player_data.get('uid', '-')}", font_bold, (255, 255, 255, 255))
    draw_shadow_text(draw, (75, 674), f"Trailblaze Lv. {player_data.get('level', '-')}   |   Equilibrium Lv. {player_data.get('world_level', '-')}", font_small, subtitle_color)

    skills = char_data.get("skills", []) or []
    skill_y = 50
    for skill in skills[:5]:
        if skill.get("icon"):
            sk_img = await get_cached_icon(client, skill.get("icon"), (34, 34))
            if sk_img: card.paste(sk_img, (510, skill_y), sk_img)
        draw_shadow_text(draw, (555, skill_y - 2), skill.get("name", "Skill")[:20], font_bold, (255, 255, 255, 255))
        draw_shadow_text(draw, (555, skill_y + 18), f"{skill.get('type_text', 'Trace')}  •  Lv. {skill.get('level', 1)}/{skill.get('max_level', 10)}", font_small, subtitle_color)
        skill_y += 70

    lc_y = 420
    if lc_icon:
        lc_img = await get_cached_icon(client, lc_icon, (75, 75))
        if lc_img: card.paste(lc_img, (510, lc_y), lc_img)
    draw_shadow_text(draw, (595, lc_y), f"{lc_name[:22]}", font_bold, (255, 255, 255, 255))
    draw_shadow_text(draw, (595, lc_y + 26), f"Lv. {lc_level} / 80", font_sub, highlight_color)

    stat_y = 50
    for stat in rendered_stats[:8]:
        if stat["icon"]:
            s_img = await get_cached_icon(client, stat["icon"], (24, 24))
            if s_img: card.paste(s_img, (870, stat_y), s_img)
        draw_shadow_text(draw, (905, stat_y + 3), stat["name"], font_bold, (255, 240, 220, 255))
        val_w = draw.textlength(stat["value"], font=font_bold) if hasattr(draw, "textlength") else len(stat["value"]) * 8.5
        draw_shadow_text(draw, (1180 - val_w, stat_y + 3), stat["value"], font_bold, highlight_color)
        stat_y += 44

    relic_sets = char_data.get("relic_sets", [])
    set_y = 425
    for r_set in relic_sets[:2]:
        draw_shadow_text(draw, (870, set_y), f"[{r_set.get('num', 2)}-Pc] {r_set.get('name', 'Set')}", font_bold, highlight_color)
        set_y += 24
        clean_desc = re.sub(r'<[^>]+>', '', str(r_set.get("desc", ""))).replace("\n", " ")
        line = ""
        for word in clean_desc.split(" "):
            test_line = line + word + " "
            w = draw.textlength(test_line, font=font_sub) if hasattr(draw, "textlength") else len(test_line) * 7.5
            if w < 290: line = test_line
            else:
                draw_shadow_text(draw, (870, set_y), line, font_sub, (240, 240, 245, 255))
                set_y += 20
                line = word + " "
        if line:
            draw_shadow_text(draw, (870, set_y), line, font_sub, (240, 240, 245, 255))
            set_y += 28

    for idx, r in enumerate(relics[:6]):
        by = 50 + (idx * 118)
        bx = 1230
        if r.get("icon"):
            r_img = await get_cached_icon(client, r.get("icon"), (52, 52))
            if r_img: card.paste(r_img, (bx, by + 8), r_img)
        draw_shadow_text(draw, (bx + 60, by + 12), f"{r.get('name', 'Relic')[:14]}", font_bold, (255, 250, 240, 255))
        draw_shadow_text(draw, (1522, by + 12), f"+{r.get('level', 0)}", font_bold, highlight_color)
        
        main_stat = r.get("main_affix", {}) or r.get("mainstat", {})
        m_name = main_stat.get("name", "") or main_stat.get("type", "")
        m_disp = main_stat.get("display", "") or format_stat_value(m_name, main_stat.get("value", ""), is_planar=(idx in [4, 5]))
        if m_name:
            draw_shadow_text(draw, (bx + 60, by + 34), f"Main: {m_name} ({m_disp})", font_small, subtitle_color)

    buf = BytesIO()
    card.save(buf, format="PNG")
    buf.seek(0)
    return buf

# --- نظام حساب الرسائل وتلبية كلمة "ايدي" ---
async def track_and_respond_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user:
        return
        
    user = update.message.from_user
    if user.is_bot:
        return

    user_id = user.id
    username = user.username or user.first_name
    text_content = update.message.text or ""

    # 1. تحديث أو تسجيل عدد الرسائل في القاعدة
    db_cursor.execute("SELECT message_count FROM user_messages WHERE user_id = ?", (user_id,))
    result = db_cursor.fetchone()

    if result:
        new_count = result[0] + 1
        db_cursor.execute("UPDATE user_messages SET message_count = ?, username = ? WHERE user_id = ?", (new_count, username, user_id))
    else:
        new_count = 1
        db_cursor.execute("INSERT INTO user_messages (user_id, username, message_count) VALUES (?, ?, 1)", (user_id, username))
    
    db_connection.commit()

    # 2. التحقق مما إذا كتب المستخدم كلمة "ايدي" (أو أي صيغة تشبهها) للرد برسالته وصورة بروفايله
    if text_content.strip() == "ايدي" or text_content.strip().lower() == "/id":
        # جلب عدد الرسائل الحالي للمستخدم
        db_cursor.execute("SELECT message_count FROM user_messages WHERE user_id = ?", (user_id,))
        count_res = db_cursor.fetchone()
        total_messages = count_res[0] if count_res else new_count

        # تجهيز معلومات المستخدم (بدون رقم الـ ID)
        name_str = f"{user.first_name} {user.last_name}" if user.last_name else user.first_name
        username_str = f"@{user.username}" if user.username else "لا يوجد"
        
        info_message = (
            f"👤 **معلومات حسابك الشخصي:**\n"
            f"• الاسم: {name_str}\n"
            f"• اسم المستخدم: {username_str}\n"
            f"📊 إجمالي عدد رسائلك في القروب: **{total_messages}** رسالة."
        )

        try:
            # محاولة جلب صورة البروفايل الخاصة بالمستخدم عبر تليجرام
            photos = await context.bot.get_user_profile_photos(user_id, limit=1)
            if photos.total_count > 0:
                # إذا كانت لديه صورة بروفايل، نرسلها مع المعلومات كشرح للصورة
                photo_file_id = photos.photos[0][-1].file_id
                await update.message.reply_photo(
                    photo=photo_file_id,
                    caption=info_message,
                    parse_mode='Markdown',
                    reply_to_message_id=update.message.message_id
                )
            else:
                # إذا لم يكن لديه صورة بروفايل، نرسل الرسالة النصية فقط
                await update.message.reply_text(
                    info_message,
                    parse_mode='Markdown',
                    reply_to_message_id=update.message.message_id
                )
        except Exception:
            # في حال حدث خطأ برمجياً في جلب الصورة، نرسل المعلومات النصية مباشرة لضمان عدم توقف الرد
            await update.message.reply_text(
                info_message,
                parse_mode='Markdown',
                reply_to_message_id=update.message.message_id
            )

# --- أوامر البوت الأساسية ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 أهلاً بك! اكتب `/hsr <UID>` لفحص الشخصيات، أو اكتب **ايدي** في القروب لمعرفة عدد رسائلك ومعلوماتك.", parse_mode='Markdown', reply_to_message_id=update.message.message_id)

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
            avatars = data.get("characters", []) or data.get("avatar_list", [])

            if not avatars:
                await update.message.reply_text("⚠️ لا توجد شخصيات معروضة في هذا الحساب.", reply_to_message_id=update.message.message_id)
                return

            keyboard = []
            row = []
            for idx, char in enumerate(avatars):
                row.append(InlineKeyboardButton(char.get("name", f"#{idx+1}"), callback_data=f"hsr_{uid}_{idx}"))
                if len(row) == 4:
                    keyboard.append(row)
                    row = []
            if row: keyboard.append(row)

            await update.message.reply_text(
                f"👤 **اللاعب:** {player.get('nickname', 'Player')}\n👇 **اختر الشخصية:**",
                reply_markup=InlineKeyboardMarkup(keyboard),
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
        uid, char_idx = data_parts[1], int(data_parts[2])
        target_msg_id = query.message.reply_to_message.message_id if query.message.reply_to_message else query.message.message_id

        async with httpx.AsyncClient() as client:
            try:
                res = await client.get(f"https://api.mihomo.me/sr_info_parsed/{uid}?lang=en", timeout=15)
                if res.status_code == 200:
                    data = res.json()
                    avatars = data.get("characters", []) or data.get("avatar_list", [])
                    if char_idx < len(avatars):
                        card_buf = await create_character_card(client, avatars[char_idx], data.get("player", {}))
                        await query.message.reply_photo(photo=card_buf, reply_to_message_id=target_msg_id)
                        return
                await query.message.reply_text("❌ تعذر إنشاء البطاقة.", reply_to_message_id=target_msg_id)
            except Exception:
                await query.message.reply_text("❌ حدث خطأ أثناء تجهيز الصورة.", reply_to_message_id=target_msg_id)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("hsr", hsr_check))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    # معالج الرسائل العام الذي يقوم بحساب الرسائل والرد عند كتابة "ايدي"
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track_and_respond_handler))

    print("🚀 البوت يعمل بنجاح!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
