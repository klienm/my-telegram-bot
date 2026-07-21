import os
import re
import math
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

# خطوط Montserrat الاحترافية لدقة نصوص فائقة النعومة
FONT_BOLD_URL = "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat-Bold.ttf"
FONT_REG_URL = "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat-Medium.ttf"

LOCAL_BOLD_PATH = "Montserrat-Bold.ttf"
LOCAL_REG_PATH = "Montserrat-Medium.ttf"

def get_sharp_font(size, bold=True):
    local_path = LOCAL_BOLD_PATH if bold else LOCAL_REG_PATH
    fallback_url = FONT_BOLD_URL if bold else FONT_REG_URL
    
    if os.path.exists(local_path):
        try:
            return ImageFont.truetype(local_path, size)
        except Exception:
            pass
            
    try:
        urllib.request.urlretrieve(fallback_url, local_path)
        return ImageFont.truetype(local_path, size)
    except Exception as e:
        print(f"⚠️ Font download failed: {e}")
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

def draw_shadow_text(draw, position, text, font, fill, shadow_fill=(0, 0, 0, 160), offset=(2, 2)):
    x, y = position
    draw.text((x + offset[0], y + offset[1]), text, font=font, fill=shadow_fill)
    draw.text((x, y), text, font=font, fill=fill)

def get_dominant_color(img):
    resample_filter = getattr(Image, "Resampling", None)
    box_filter = resample_filter.BOX if resample_filter else getattr(Image, "BOX", Image.NEAREST)
    tiny_img = img.resize((1, 1), box_filter)
    avg_pixel = tiny_img.getpixel((0, 0))
    return int(avg_pixel[0]), int(avg_pixel[1]), int(avg_pixel[2])

def create_glass_panel(w, h, color, opacity=140, radius=16):
    panel = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(panel)
    r, g, b = color
    draw.rounded_rectangle([0, 0, w, h], radius=radius, fill=(r, g, b, opacity))
    return panel

# --- دالة رسم البطاقة الفنية الاحترافية الحية ---
async def create_character_card(client, char_data, player_data):
    char_name = char_data.get("name", "Character")
    char_level = char_data.get("level", 1)
    char_id = str(char_data.get("id", ""))
    icon_path = char_data.get("icon", "")

    # 1. إعداد الإحصائيات (Stats) بالخوارزمية الدقيقة
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

    # 2. تجهيز الخلفية (Vibrant Blurred Splash)
    card = Image.new("RGBA", (1600, 800), (0, 0, 0, 255))
    
    splash_img = None
    if char_id:
        portrait_url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/image/character_portrait/{char_id}.png"
        splash_img = await fetch_image(client, portrait_url)

    if splash_img:
        dom_r, dom_g, dom_b = get_dominant_color(splash_img)
        panel_color = (int(dom_r * 0.3), int(dom_g * 0.3), int(dom_b * 0.3))
        text_highlight = (min(255, dom_r + 90), min(255, dom_g + 90), min(255, dom_b + 90), 255)
        
        bg_blur = resize_cover(splash_img, 1600, 800).filter(ImageFilter.GaussianBlur(55))
        tint = Image.new("RGBA", (1600, 800), (dom_r // 5, dom_g // 5, dom_b // 5, 150))
        bg_blur = Image.alpha_composite(bg_blur.convert("RGBA"), tint)
        card.paste(bg_blur, (0, 0))

        # السبلاش آرت الأصلي مع تلاشي (Fade) متقن للدمج
        splash_render = resize_cover(splash_img, 600, 800)
        mask = Image.new("L", splash_render.size, 255)
        mask_draw = ImageDraw.Draw(mask)
        fade_width = 220
        for x in range(splash_render.width - fade_width, splash_render.width):
            alpha = int(255 * (1 - (x - (splash_render.width - fade_width)) / fade_width))
            mask_draw.line([(x, 0), (x, splash_render.height)], fill=alpha)
        
        card.paste(splash_render, (-50, 0), mask)
    else:
        panel_color = (25, 35, 55)
        text_highlight = (255, 215, 100, 255)

    draw = ImageDraw.Draw(card)
    
    # 3. الخطوط الحادة الواضحة
    font_large = get_sharp_font(38, bold=True)
    font_title = get_sharp_font(24, bold=True)
    font_bold = get_sharp_font(18, bold=True)
    font_sub = get_sharp_font(15, bold=False)
    font_small = get_sharp_font(13, bold=False)

    # 4. رسم الـ Eidolons (6 دوائر على شكل عامود بجانب السبلاش)
    rank = char_data.get("rank", 0)
    rank_icons = char_data.get("rank_icons", [])
    eidolon_start_y = 80
    eidolon_x = 515
    
    for i in range(6):
        e_y = eidolon_start_y + (i * 75)
        e_bg = Image.new("RGBA", (54, 54), (0, 0, 0, 0))
        e_draw = ImageDraw.Draw(e_bg)
        
        is_unlocked = i < rank
        circle_color = text_highlight if is_unlocked else (panel_color[0], panel_color[1], panel_color[2], 120)
        
        e_draw.ellipse([0, 0, 54, 54], fill=circle_color)
        
        if i < len(rank_icons):
            e_icon = await get_cached_icon(client, rank_icons[i], (42, 42))
            if e_icon:
                icon_pos = ((54 - 42) // 2, (54 - 42) // 2)
                if not is_unlocked:
                    e_icon = e_icon.convert("LA").convert("RGBA")
                    e_icon.putalpha(e_icon.split()[3].point(lambda p: p * 0.35))
                e_bg.paste(e_icon, icon_pos, e_icon)
                
        card.paste(e_bg, (eidolon_x, e_y), e_bg)

    # معلومات اللاعب والاسم أسفل السبلاش
    name_y = 610
    draw_shadow_text(draw, (40, name_y), char_name.upper(), font_large, (255, 255, 255, 255))
    draw_shadow_text(draw, (40, name_y + 44), f"LEVEL {char_level} / 80", font_title, text_highlight)
    
    p_name = player_data.get("nickname", "Unknown")
    p_uid = player_data.get("uid", "-")
    draw_shadow_text(draw, (40, name_y + 80), f"{p_name}  •  UID {p_uid}", font_sub, (220, 230, 255, 255))

    # 5. السلاح (Light Cone) وقدراته
    equip = char_data.get("light_cone", {})
    if equip:
        lc_name = equip.get("name", "Unknown LC")
        lc_level = equip.get("level", "-")
        lc_rank = equip.get("rank", 1)
        lc_icon = equip.get("icon", "")
        
        lc_panel = create_glass_panel(390, 130, panel_color, opacity=150, radius=16)
        card.paste(lc_panel, (590, 80), lc_panel)
        
        if lc_icon:
            lc_img = await get_cached_icon(client, lc_icon, (75, 75))
            if lc_img:
                card.paste(lc_img, (605, 95), lc_img)
                
        draw_shadow_text(draw, (690, 92), f"{lc_name[:24]}", font_bold, text_highlight)
        draw_shadow_text(draw, (690, 116), f"Lv. {lc_level}  |  Superimposition {lc_rank}", font_sub, (255, 255, 255, 255))
        
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
            
        draw_shadow_text(draw, (690, 148), prop_text[:40], font_small, (200, 220, 255, 255))

    # 6. المهارات (Traces / Skills) - عادت مكانها بانتظام تام
    skills = char_data.get("skills", [])
    if skills:
        tr_panel = create_glass_panel(390, 160, panel_color, opacity=140, radius=16)
        card.paste(tr_panel, (590, 225), tr_panel)
        
        draw_shadow_text(draw, (610, 235), "TRACES & ABILITIES", font_bold, text_highlight)
        
        trace_y = 262
        for skill in skills[:4]:
            sk_name = skill.get("name", "Skill")
            sk_level = skill.get("level", 1)
            sk_max = skill.get("max_level", 10)
            sk_icon = skill.get("icon", "")
            
            if sk_icon:
                sk_img = await get_cached_icon(client, sk_icon, (26, 26))
                if sk_img:
                    card.paste(sk_img, (610, trace_y), sk_img)
                    
            draw_shadow_text(draw, (645, trace_y + 3), sk_name[:18], font_small, (255, 255, 255, 255))
            draw_shadow_text(draw, (910, trace_y + 3), f"Lv.{sk_level}/{sk_max}", font_small, text_highlight)
            trace_y += 30

    # 7. لوحة الإحصائيات (Stats Panel)
    stats_panel = create_glass_panel(390, 360, panel_color, opacity=140, radius=16)
    card.paste(stats_panel, (590, 400), stats_panel)
    
    draw_shadow_text(draw, (610, 410), "COMBAT STATS", font_bold, text_highlight)
    stat_y = 442
    for stat in rendered_stats[:8]:
        s_icon = stat["icon"]
        if s_icon:
            s_img = await get_cached_icon(client, s_icon, (24, 24))
            if s_img:
                card.paste(s_img, (610, stat_y - 2), s_img)
                
        draw_shadow_text(draw, (645, stat_y), stat["name"], font_sub, (240, 245, 255, 255))
        
        try:
            val_width = draw.textlength(stat["value"], font=font_sub)
        except AttributeError:
            val_width = len(stat["value"]) * 9
            
        draw_shadow_text(draw, (960 - val_width, stat_y), stat["value"], font_bold, text_highlight)
        stat_y += 36

    # 8. لوحة الريليكس (Relics Panel) الشفافة على اليمين
    relics = char_data.get("relics", []) or char_data.get("relicList", []) or []
    for idx, r in enumerate(relics[:6]):
        box_y1 = 80 + (idx * 110)
        box_x1 = 1005
        
        r_panel = create_glass_panel(575, 100, panel_color, opacity=140, radius=16)
        card.paste(r_panel, (box_x1, box_y1), r_panel)
        
        r_lvl = r.get("level", 0)
        r_icon = r.get("icon", "")
        if r_icon:
            r_img = await get_cached_icon(client, r_icon, (64, 64))
            if r_img:
                card.paste(r_img, (box_x1 + 12, box_y1 + 18), r_img)
                
        draw_shadow_text(draw, (box_x1 + 86, box_y1 + 12), f"+{r_lvl}", font_bold, text_highlight)
        
        main_stat = r.get("main_affix", {})
        m_name = main_stat.get("name", "")
        m_val = main_stat.get("value", 0)
        m_display = main_stat.get("display", "")
        if not m_display:
             m_display = f"{m_val*100:.1f}%" if main_stat.get("percent") else str(int(m_val))
             
        draw_shadow_text(draw, (box_x1 + 140, box_y1 + 14), f"{m_name}: {m_display}", font_bold, (255, 255, 255, 255))
        
        substats = r.get("sub_affix", [])
        for i, sub in enumerate(substats[:4]):
            s_name = sub.get("name", "")
            s_display = sub.get("display", "")
            if not s_display:
                s_val = sub.get("value", 0)
                s_display = f"{s_val*100:.1f}%" if sub.get("percent") else str(int(s_val))
                
            stat_text = f"{s_name}: {s_display}"
            sub_x = box_x1 + 86 if i % 2 == 0 else box_x1 + 335
            sub_y = box_y1 + 42 if i < 2 else box_y1 + 68
            draw_shadow_text(draw, (sub_x, sub_y), stat_text, font_small, (220, 230, 255, 255))

    buf = BytesIO()
    card.save(buf, format="PNG")
    buf.seek(0)
    return buf

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 **أهلاً بك يا بشار!**\n\n"
        "أدخل الـ UID لعرض قائمة شخصياتك بالبطاقة الفنية الحية الجديدة:\n"
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
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("hsr", hsr_check))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("🚀 البوت الفني يعمل بنجاح!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
