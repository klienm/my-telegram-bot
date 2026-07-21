import os
import re
import math
import httpx
import urllib.request
import tempfile
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
        res = await client.get(url, timeout=10)
        if res.status_code == 200:
            return Image.open(BytesIO(res.content)).convert("RGBA")
    except Exception as e:
        print(f"⚠️ Error fetching image {url}: {e}")
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

# استخدام مجلد مؤقت آمن لضمان صلاحيات الكتابة وحفظ الخطوط الحادة على خوادم الاستضافة
temp_dir = tempfile.gettempdir()
LOCAL_BOLD_PATH = os.path.join(temp_dir, "Montserrat-Bold.ttf")
LOCAL_REG_PATH = os.path.join(temp_dir, "Montserrat-Medium.ttf")

def download_fonts_on_startup():
    """
    تحميل خطوط Montserrat المتجهية وحفظها في مجلد النظام المؤقت فور إقلاع البوت.
    """
    if not os.path.exists(LOCAL_BOLD_PATH):
        print("⏳ Startup: Downloading Montserrat-Bold font for high-resolution text...")
        try:
            r = httpx.get(FONT_BOLD_URL, follow_redirects=True, timeout=15)
            with open(LOCAL_BOLD_PATH, "wb") as f:
                f.write(r.content)
            print("✅ Montserrat-Bold downloaded successfully.")
        except Exception as e:
            print(f"❌ Failed to download bold font: {e}")

    if not os.path.exists(LOCAL_REG_PATH):
        print("⏳ Startup: Downloading Montserrat-Medium font for high-resolution text...")
        try:
            r = httpx.get(FONT_REG_URL, follow_redirects=True, timeout=15)
            with open(LOCAL_REG_PATH, "wb") as f:
                f.write(r.content)
            print("✅ Montserrat-Medium downloaded successfully.")
        except Exception as e:
            print(f"❌ Failed to download regular font: {e}")

def get_sharp_font(size, bold=True):
    local_path = LOCAL_BOLD_PATH if bold else LOCAL_REG_PATH
    
    if os.path.exists(local_path):
        try:
            return ImageFont.truetype(local_path, size)
        except Exception:
            pass
            
    # التحقق من وجود الخطوط بمسارات نظام لينكس البديلة
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
async def get_cached_icon(client, icon_path, size=None):
    if not icon_path: return None
    cache_key = (icon_path, size)
    if cache_key in icon_cache: return icon_cache[cache_key]
    
    img_url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/{icon_path}"
    img = await fetch_image(client, img_url)
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
    resample_filter = getattr(Image, "Resampling", None)
    box_filter = resample_filter.BOX if resample_filter else getattr(Image, "BOX", Image.NEAREST)
    tiny_img = img.resize((1, 1), box_filter)
    avg_pixel = tiny_img.getpixel((0, 0))
    return int(avg_pixel[0]), int(avg_pixel[1]), int(avg_pixel[2])

# --- دالة رسم البطاقة (خطوط عملاقة، أيقونات بارزة، بدون بوكسات ريليكس وبدون أخطاء) ---
async def create_character_card(client, char_data, player_data):
    SCALE = 2  # دقة مضاعفة للتنعيم الخارق ورسم التفاصيل بجودة فائقة
    
    char_name = char_data.get("name", "Character")
    char_level = char_data.get("level", 1)
    char_id = str(char_data.get("id", ""))

    # 1. إحصائيات الشخصية
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
            if stat["is_percent"]:
                display_val = f"{val * 100:.1f}%"
            else:
                display_val = str(int(math.floor(val)))
            
            rendered_stats.append({
                "name": stat["name"],
                "value": display_val,
                "icon": stat["icon"]
            })

    # 2. إنشاء اللوحة بخلفية داكنة راقية
    card = Image.new("RGBA", (1600 * SCALE, 800 * SCALE), (12, 12, 18, 255))
    
    splash_img = None
    if char_id:
        portrait_url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/image/character_portrait/{char_id}.png"
        splash_img = await fetch_image(client, portrait_url)

    if splash_img:
        dom_r, dom_g, dom_b = get_dominant_color(splash_img)
        # زيادة التفتيح للتدرج اللوني المشع لزيادة الوضوح والقوة
        text_highlight = (min(255, dom_r + 140), min(255, dom_g + 140), min(255, dom_b + 140), 255)
        
        bg_blur = resize_cover(splash_img, 1600 * SCALE, 800 * SCALE).filter(ImageFilter.GaussianBlur(130))
        # جعل التظليل أفتح بنسبة بسيطة (140 بدلاً من 170) لإبراز تفاصيل وألوان الخلفية
        tint = Image.new("RGBA", (1600 * SCALE, 800 * SCALE), (dom_r // 7, dom_g // 7, dom_b // 7, 140))
        bg_blur = Image.alpha_composite(bg_blur.convert("RGBA"), tint)
        card.paste(bg_blur, (0, 0))

        splash_render = resize_cover(splash_img, 620 * SCALE, 800 * SCALE)
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
    
    # 3. أحجام خطوط موزونة واحترافية وبدقة متناهية تمنع التداخل نهائياً
    font_large = get_sharp_font(52 * SCALE, bold=True)   # اسم الشخصية
    font_title = get_sharp_font(30 * SCALE, bold=True)   # مستويات الشخصية والسلاح
    font_bold = get_sharp_font(21 * SCALE, bold=True)    # القيم والإحصائيات الرئيسية
    font_sub = get_sharp_font(18 * SCALE, bold=False)    # مسميات الإحصائيات
    font_small = get_sharp_font(15 * SCALE, bold=False)  # الإحصائيات الفرعية

    # 4. رسم الإيدولونز بألوان متباينة جداً (ذهبي وفحمي)
    rank = char_data.get("rank", 0)
    rank_icons = char_data.get("rank_icons", [])
    eidolon_start_y = 65 * SCALE
    eidolon_x = 520 * SCALE
    e_size = 46 * SCALE
    
    for i in range(6):
        e_y = eidolon_start_y + (i * 78 * SCALE)
        e_bg = Image.new("RGBA", (e_size, e_size), (0, 0, 0, 0))
        e_draw = ImageDraw.Draw(e_bg)
        
        is_unlocked = i < rank
        if is_unlocked:
            fill_color = (245, 165, 35, 240)    # ذهبي مشرق واضح
            border_color = (255, 220, 100, 255)
        else:
            fill_color = (40, 40, 55, 230)      # فحمي غامق متباين تماماً
            border_color = (110, 110, 130, 200)
        
        e_draw.ellipse([0, 0, e_size, e_size], fill=fill_color, outline=border_color, width=2 * SCALE)
        
        if i < len(rank_icons):
            e_icon = await get_cached_icon(client, rank_icons[i], (32 * SCALE, 32 * SCALE))
            if e_icon:
                icon_pos = ((e_size - 32 * SCALE) // 2, (e_size - 32 * SCALE) // 2)
                if not is_unlocked:
                    e_icon = e_icon.convert("LA").convert("RGBA")
                    e_icon.putalpha(e_icon.split()[3].point(lambda p: p * 0.3))
                e_bg.paste(e_icon, icon_pos, e_icon)
                
        card.paste(e_bg, (eidolon_x, e_y), e_bg)

    # معلومات الشخصية واللاعب بمقاسات مريحة وتناسق عمودي ممتاز
    name_y = 530 * SCALE
    draw_shadow_text(draw, (40 * SCALE, name_y), char_name.upper(), font_large, (255, 255, 255, 255))
    draw_shadow_text(draw, (40 * SCALE, name_y + 105 * SCALE), f"LEVEL {char_level} / 80", font_title, text_highlight)
    
    p_name = player_data.get("nickname", "Unknown")
    p_uid = player_data.get("uid", "-")
    draw_shadow_text(draw, (40 * SCALE, name_y + 175 * SCALE), f"{p_name}  •  UID {p_uid}", font_sub, (255, 255, 255, 255))

    # 5. السلاح (Light Cone) بمقاسات منسقة
    equip = char_data.get("light_cone", {})
    if equip:
        lc_name = equip.get("name", "Unknown LC")
        lc_level = equip.get("level", "-")
        lc_rank = equip.get("rank", 1)
        lc_id = str(equip.get("id", ""))
        
        lc_x, lc_y = 585 * SCALE, 40 * SCALE
        lc_img = None
        if lc_id:
            lc_portrait_url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/image/light_cone_portrait/{lc_id}.png"
            lc_img = await fetch_image(client, lc_portrait_url)
            if lc_img:
                lc_img = resize_cover(lc_img, 95 * SCALE, 120 * SCALE)
        
        if not lc_img:
            lc_icon = equip.get("icon", "")
            lc_img = await get_cached_icon(client, lc_icon, (95 * SCALE, 120 * SCALE))
            
        if lc_img:
            card.paste(lc_img, (lc_x, lc_y), lc_img)
                
        draw_shadow_text(draw, (lc_x + 110 * SCALE, lc_y + 2 * SCALE), f"{lc_name[:22]}", font_bold, text_highlight)
        draw_shadow_text(draw, (lc_x + 110 * SCALE, lc_y + 44 * SCALE), f"Lv. {lc_level}  |  Superimposition {lc_rank}", font_sub, (255, 255, 255, 255))
        
        lc_props = equip.get("properties", [])
        prop_text = ""
        for p in lc_props[:2]:
            p_name = str(p.get("type", "")).replace("AddedRatio", "").replace("Delta", "")
            p_val = p.get("value", 0)
            if p_val < 3.0:
                prop_text += f"{p_name}: {p_val*100:.1f}%  "
            else:
                prop_text += f"{p_name}: {int(p_val)}  "
        if not prop_text:
            prop_text = "Standard Light Cone Passive Active."
            
        draw_shadow_text(draw, (lc_x + 110 * SCALE, lc_y + 86 * SCALE), prop_text[:42], font_small, (255, 255, 255, 255))

    # 6. المهارات (Traces) - تم زيادة مسافات التباعد الرأسي لمنع تداخل الأحرف
    skills = char_data.get("skills", [])
    if skills:
        tr_x, tr_y = 585 * SCALE, 195 * SCALE
        draw_shadow_text(draw, (tr_x, tr_y), "TRACES & ABILITIES", font_bold, text_highlight)
        
        t_y = tr_y + 50 * SCALE
        for skill in skills[:4]:
            sk_name = skill.get("name", "Skill")
            sk_level = skill.get("level", 1)
            sk_max = skill.get("max_level", 10)
            sk_icon = skill.get("icon", "")
            
            if sk_icon:
                sk_img = await get_cached_icon(client, sk_icon, (44 * SCALE, 44 * SCALE))
                if sk_img:
                    card.paste(sk_img, (tr_x, t_y - 2 * SCALE), sk_img)
                    
            draw_shadow_text(draw, (tr_x + 55 * SCALE, t_y + 4 * SCALE), sk_name[:16], font_small, (255, 255, 255, 255))
            draw_shadow_text(draw, (tr_x + 330 * SCALE, t_y + 4 * SCALE), f"Lv.{sk_level}/{sk_max}", font_bold, text_highlight)
            t_y += 48 * SCALE

    # 7. لوحة الإحصائيات (Stats) بمقاسات وهوامش مريحة جداً
    stat_start_x, stat_start_y = 585 * SCALE, 415 * SCALE
    draw_shadow_text(draw, (stat_start_x, stat_start_y), "COMBAT STATS", font_bold, text_highlight)
    
    s_y = stat_start_y + 46 * SCALE
    for stat in rendered_stats[:7]:
        s_icon = stat["icon"]
        if s_icon:
            s_img = await get_cached_icon(client, s_icon, (35 * SCALE, 35 * SCALE))
            if s_img:
                card.paste(s_img, (stat_start_x, s_y), s_img)
                
        draw_shadow_text(draw, (stat_start_x + 48 * SCALE, s_y + 2 * SCALE), stat["name"], font_sub, (255, 255, 255, 255))
        
        try:
            val_width = draw.textlength(stat["value"], font=font_bold)
        except AttributeError:
            val_width = len(stat["value"]) * 14 * SCALE
            
        draw_shadow_text(draw, (980 * SCALE - val_width, s_y + 2 * SCALE), stat["value"], font_bold, text_highlight)
        s_y += 42 * SCALE

    # 8. قطع الريليكس (Relics) بمقاسات وهوامش ذهبية منسقة تمنع التداخل تماماً
    relics = char_data.get("relics", []) or char_data.get("relicList", []) or []
    for idx, r in enumerate(relics[:6]):
        box_y = (45 + (idx * 124)) * SCALE
        box_x = 1010 * SCALE
        
        r_lvl = r.get("level", 0)
        r_icon = r.get("icon", "")
        if r_icon:
            r_img = await get_cached_icon(client, r_icon, (95 * SCALE, 95 * SCALE))
            if r_img:
                card.paste(r_img, (box_x, box_y + 2 * SCALE), r_img)
                
        draw_shadow_text(draw, (box_x + 105 * SCALE, box_y + 2 * SCALE), f"+{r_lvl}", font_bold, text_highlight)
        
        main_stat = r.get("main_affix", {})
        m_name = main_stat.get("name", "")
        m_val = main_stat.get("value", 0)
        m_display = main_stat.get("display", "")
        if not m_display:
             m_display = f"{m_val*100:.1f}%" if main_stat.get("percent") else str(int(m_val))
             
        draw_shadow_text(draw, (box_x + 175 * SCALE, box_y + 2 * SCALE), f"{m_name}: {m_display}", font_bold, (255, 255, 255, 255))
        
        substats = r.get("sub_affix", [])
        for i, sub in enumerate(substats[:4]):
            s_name = sub.get("name", "")
            s_display = sub.get("display", "")
            if not s_display:
                s_val = sub.get("value", 0)
                s_display = f"{s_val*100:.1f}%" if sub.get("percent") else str(int(s_val))
                
            stat_text = f"{s_name}: {s_display}"
            sub_x = box_x + 105 * SCALE if i % 2 == 0 else box_x + 355 * SCALE
            sub_y = box_y + 44 * SCALE if i < 2 else box_y + 78 * SCALE
            draw_shadow_text(draw, (sub_x, sub_y), stat_text, font_small, (255, 255, 255, 255))

    # التنعيم الأخير لزيادة جودة وحجم الصورة لتبدو خلابة
    card = card.resize((1600, 800), Image.Resampling.LANCZOS)
    card = card.filter(ImageFilter.SHARPEN)

    buf = BytesIO()
    card.save(buf, format="PNG")
    buf.seek(0)
    return buf

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 **أهلاً بك يا بشار!**\n\n"
        "أدخل الـ UID لعرض شخصياتك بالبطاقة المطورة والخطوط الضخمة والواضحة تماماً:\n"
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
                await update.message.reply_text(f"❌ لم يتم العثور على الحساب أو أن الخادم مشغول (الكود: {response.status_code}).", reply_to_message_id=update.message.message_id)
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

        except Exception as e:
            print(f"⚠️ Error in hsr_check: {e}")
            await update.message.reply_text("❌ حدث خطأ في الاتصال بالخادم. يرجى المحاولة لاحقاً.", reply_to_message_id=update.message.message_id)

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
                res = await client.get(url, timeout=20)
                if res.status_code == 200:
                    data = res.json()
                    player_data = data.get("player", {})
                    avatars = data.get("characters", [])
                    
                    if char_idx < len(avatars):
                        char_data = avatars[char_idx]
                        card_buf = await create_character_card(client, char_data, player_data)

                        await query.message.reply_photo(
                            photo=card_buf,
                            reply_to_message_id=target_message_id
                        )
                    else:
                        await query.message.reply_text("❌ لم يتم العثور على الشخصية.", reply_to_message_id=target_message_id)
                else:
                    await query.message.reply_text(f"❌ تعذر جلب بيانات الشخصية (الكود: {res.status_code}).", reply_to_message_id=target_message_id)
            except Exception as e:
                print(f"⚠️ Error in callback: {e}")
                await query.message.reply_text("❌ حدث خطأ أثناء تجهيز الصورة الفنية.", reply_to_message_id=target_message_id)

def main():
    # تحميل الخطوط وحفظها مؤقتاً عند بداية تشغيل البوت مباشرة
    download_fonts_on_startup()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("hsr", hsr_check))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("🚀 البوت الفني يعمل بنجاح!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
