import os
import re
import httpx
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops, ImageEnhance
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN")

# --- Dummy HTTP Server ---
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

# --- تلاشي الحواف سینمائي للسبلاش آرت ---
def fade_edges(img):
    """تقوم بدمج صورة الشخصية بسلاسة مع الخلفية بتلاشي الحافة اليمنى والسفلية"""
    w, h = img.size
    alpha_mask = Image.new("L", (w, h), 255)
    draw = ImageDraw.Draw(alpha_mask)
    
    fade_start_x = int(w * 0.45)
    for x in range(fade_start_x, w):
        alpha = int(255 * (1 - (x - fade_start_x) / (w - fade_start_x)))
        draw.line([(x, 0), (x, h)], fill=max(0, alpha))
        
    fade_start_y = int(h * 0.75)
    for y in range(fade_start_y, h):
        alpha = int(255 * (1 - (y - fade_start_y) / (h - fade_start_y)))
        # دمج التلاشي الأفقي مع العمودي
        for x in range(w):
            current = alpha_mask.getpixel((x, y))
            alpha_mask.putpixel((x, y), min(current, alpha))
            
    result = img.copy()
    result.putalpha(alpha_mask)
    return result

def draw_shadow_text(draw, position, text, font, fill, shadow_fill=(0, 0, 0, 180), offset=(2, 2)):
    x, y = position
    draw.text((x + offset[0], y + offset[1]), text, font=font, fill=shadow_fill)
    draw.text((x, y), text, font=font, fill=fill)

def wrap_text(text, font, max_width, draw):
    lines = []
    words = text.split()
    current_line = ""
    for word in words:
        test_line = current_line + word + " "
        w = draw.textlength(test_line, font=font) if hasattr(draw, 'textlength') else len(test_line)*7
        if w > max_width and current_line:
            lines.append(current_line)
            current_line = word + " "
        else:
            current_line = test_line
    if current_line:
        lines.append(current_line)
    return lines

# --- خطوط فائقة الدقة (Inter) ---
FONT_URLS = {
    "bold": "https://github.com/rsms/inter/raw/master/docs/font-files/Inter-Bold.ttf",
    "medium": "https://github.com/rsms/inter/raw/master/docs/font-files/Inter-Medium.ttf"
}

def get_beautiful_font(size, is_bold=True):
    filename = "Inter-Bold.ttf" if is_bold else "Inter-Medium.ttf"
    if not os.path.exists(filename):
        try:
            print(f"⏳ Downloading Font {filename} for crisp text...")
            urllib.request.urlretrieve(FONT_URLS["bold" if is_bold else "medium"], filename)
        except Exception:
            return ImageFont.load_default()
    try:
        return ImageFont.truetype(filename, size)
    except Exception:
        return ImageFont.load_default()

icon_cache = {}

async def get_cached_icon(client, icon_path, size=None):
    if not icon_path: return None
    cache_key = (icon_path, size)
    if cache_key in icon_cache: return icon_cache[cache_key]
    
    img_url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/{icon_path}"
    img = await fetch_image(client, img_url)
    if img:
        if size: img = img.resize(size, Image.Resampling.LANCZOS)
        icon_cache[cache_key] = img
        return img
    return None

async def create_character_card(client, char_data, player_data):
    char_name = char_data.get("name", "Character")
    char_level = char_data.get("level", 1)
    char_id = str(char_data.get("id", ""))
    icon_path = char_data.get("icon", "")
    rank = char_data.get("rank", 0) # Eidolons

    equip = char_data.get("light_cone", {}) or {}
    relics = char_data.get("relics", []) or char_data.get("relicList", []) or []

    card = Image.new("RGBA", (1600, 800), (10, 10, 15, 255))
    draw = ImageDraw.Draw(card)

    font_huge = get_beautiful_font(42, is_bold=True)
    font_large = get_beautiful_font(28, is_bold=True)
    font_bold = get_beautiful_font(20, is_bold=True)
    font_med = get_beautiful_font(16, is_bold=True)
    font_sub = get_beautiful_font(14, is_bold=False)

    splash_img = None
    if char_id:
        splash_img = await fetch_image(client, f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/image/character_portrait/{char_id}.png")

    highlight_color = (255, 220, 120, 255)
    
    # === 1. الخلفية الحية (Vivid Background) ===
    if splash_img:
        # استخراج متوسط اللون للإضاءات
        tiny = splash_img.resize((1, 1))
        r, g, b = tiny.getpixel((0, 0))[:3]
        highlight_color = (min(255, r + 100), min(255, g + 100), min(255, b + 100), 255)
        
        # تجهيز الخلفية الضبابية المشبعة بالألوان
        bg_blur = resize_cover(splash_img, 1600, 800, focus_y=0.2)
        enhancer = ImageEnhance.Color(bg_blur)
        bg_blur = enhancer.enhance(1.8) # تشبع ألوان عالي
        enhancer = ImageEnhance.Brightness(bg_blur)
        bg_blur = enhancer.enhance(0.7) # تعتيم خفيف لا يميل للرمادي
        bg_blur = bg_blur.filter(ImageFilter.GaussianBlur(radius=45)) # ضبابية سينمائية
        
        # فلتر زجاجي لوني لمنع الرمادي
        tint = Image.new("RGBA", (1600, 800), (r//4, g//4, b//4, 160))
        bg_blur.paste(tint, (0, 0), tint)
        card.paste(bg_blur, (0, 0))
    else:
        card.paste(Image.new("RGBA", (1600, 800), (20, 25, 35, 255)), (0, 0))

    # === 2. السبلاش آرت متلاشي الحواف (Seamless Splash) ===
    if splash_img:
        splash_crop = resize_cover(splash_img, 850, 800, focus_y=0.15)
        seamless_splash = fade_edges(splash_crop)
        card.paste(seamless_splash, (-80, 0), seamless_splash)

    # اسم الشخصية ومعلومات اللاعب (تحت يسار)
    draw_shadow_text(draw, (60, 640), char_name.upper(), font_huge, (255, 255, 255, 255))
    draw_shadow_text(draw, (65, 700), f"Lv. {char_level} / 80", font_large, highlight_color)
    
    p_name = player_data.get("nickname", "Player")
    draw_shadow_text(draw, (65, 745), f"UID: {player_data.get('uid','-')}  |  {p_name}", font_med, (220, 225, 235, 255))

    # === 3. الأيديلونز (Eidolons Nodes) بجوار السبلاش ===
    eido_start_y = 150
    eido_x = 720
    for i in range(1, 7):
        is_active = i <= rank
        node_color = highlight_color if is_active else (50, 50, 60, 180)
        border_color = (255, 255, 255, 200) if is_active else (100, 100, 110, 100)
        
        draw.ellipse([eido_x, eido_start_y, eido_x + 28, eido_start_y + 28], fill=node_color, outline=border_color, width=2)
        if is_active: # إضاءة خفيفة
            draw.ellipse([eido_x-4, eido_start_y-4, eido_x + 32, eido_start_y + 32], outline=highlight_color, width=1)
            
        eido_start_y += 65

    # === 4. إصلاح أرقام الإحصائيات (Exact Stats Logic) ===
    # السيرفر يعطي الـ attributes (الأساسي) والـ additions (الإضافي). نجمعهم.
    calc_stats = {}
    PERCENT_FIELDS = ["crit_rate", "crit_dmg", "break_effect", "effect_hit", "effect_res", "sp_rate", "heal_rate"]
    
    for s in char_data.get("attributes", []):
        calc_stats[s["field"]] = s.copy()
        
    for s in char_data.get("additions", []):
        f = s["field"]
        if f in calc_stats:
            calc_stats[f]["value"] += s["value"]
        else:
            calc_stats[f] = s.copy()

    display_order = ["hp", "atk", "def", "spd", "crit_rate", "crit_dmg", "break_effect", "effect_hit", "effect_res", "heal_rate", "sp_rate"]
    
    stat_x = 800
    stat_y = 60
    for field in display_order:
        if field in calc_stats:
            st = calc_stats[field]
            val = st["value"]
            is_percent = field in PERCENT_FIELDS or st.get("percent", False)
            
            if is_percent:
                final_val_str = f"{val * 100:.1f}%"
            else:
                final_val_str = str(int(round(val)))
                
            if final_val_str in ["0", "0.0%"]: continue

            # خلفية زجاجية شفافة للإحصائيات
            draw.rounded_rectangle([stat_x - 10, stat_y - 5, stat_x + 320, stat_y + 35], radius=8, fill=(0, 0, 0, 70))
            
            if st.get("icon"):
                img_ico = await get_cached_icon(client, st["icon"], (26, 26))
                if img_ico: card.paste(img_ico, (stat_x, stat_y - 2), img_ico)
                
            draw_shadow_text(draw, (stat_x + 35, stat_y), st["name"], font_bold, (240, 245, 255, 255))
            
            val_w = draw.textlength(final_val_str, font=font_bold) if hasattr(draw, 'textlength') else len(final_val_str)*12
            draw_shadow_text(draw, (stat_x + 310 - val_w, stat_y), final_val_str, font_bold, highlight_color)
            
            stat_y += 50

    # === 5. السلاح مع شرح القدرات (Light Cone & Skill) ===
    lc_y = 450
    if equip:
        lc_icon = equip.get("icon", "")
        if lc_icon:
            lc_img = await get_cached_icon(client, lc_icon, (110, 110))
            if lc_img: card.paste(lc_img, (790, lc_y), lc_img)
            
        draw_shadow_text(draw, (915, lc_y + 5), equip.get("name", "Unknown")[:25], font_large, (255, 255, 255, 255))
        draw_shadow_text(draw, (915, lc_y + 40), f"Lv. {equip.get('level', 1)}  |  Superimposition {equip.get('rank', 1)}", font_bold, highlight_color)
        
        # استخراج وصف السلاح
        lc_skill_desc = "No Ability Data."
        if "skill" in equip:
            lc_skill_desc = str(equip["skill"].get("desc", ""))
        elif "desc" in equip:
            lc_skill_desc = str(equip.get("desc", ""))
            
        # تنظيف الـ HTML من النص
        clean_desc = re.sub(r'<[^>]+>', '', lc_skill_desc).replace("\\n", " ")
        if len(clean_desc) > 5:
            lines = wrap_text(clean_desc, font_sub, 340, draw)
            desc_y = lc_y + 75
            for line in lines[:5]: # عرض أول 5 أسطر فقط كحد أقصى
                draw_shadow_text(draw, (915, desc_y), line, font_sub, (200, 210, 220, 255))
                desc_y += 20

    # === 6. الريليكس (Relics Panel) بخطوط أصغر وأرتب ===
    box_x = 1170
    relic_y = 60
    for idx, r in enumerate(relics[:6]):
        draw.rounded_rectangle([box_x, relic_y, box_x + 390, relic_y + 110], radius=10, fill=(0, 0, 0, 85))
        
        r_icon = r.get("icon", "")
        if r_icon:
            r_img = await get_cached_icon(client, r_icon, (55, 55))
            if r_img: card.paste(r_img, (box_x + 10, relic_y + 10), r_img)
            
        draw_shadow_text(draw, (box_x + 75, relic_y + 12), f"+{r.get('level', 0)}", font_bold, highlight_color)
        
        main = r.get("main_affix", {}) or r.get("mainstat", {})
        m_name = main.get("name", "") or main.get("type", "")
        m_val = main.get("display", "")
        draw_shadow_text(draw, (box_x + 140, relic_y + 12), f"{m_name} : {m_val}", font_bold, (255, 255, 255, 255))
        
        # Substats
        subs = r.get("sub_affix", []) or r.get("sub_affix_list", []) or r.get("substats", [])
        sub_y_offset = relic_y + 45
        for i, sub in enumerate(subs[:4]):
            s_name = sub.get("name", "") or sub.get("type", "")
            s_val = sub.get("display", "")
            if not s_val:
                v = sub.get("value", 0)
                is_p = sub.get("percent", False) or s_name.lower() in ["crit rate", "crit dmg", "break effect", "effect hit rate", "effect res"]
                s_val = f"{v*100:.1f}%" if is_p else str(int(v))
            
            s_name = s_name.replace("CRIT Rate", "CR").replace("CRIT DMG", "CD").replace("Break Effect", "BE").replace("Effect Hit Rate", "EHR").replace("Effect RES", "RES")
            
            sx = box_x + 15 if i % 2 == 0 else box_x + 200
            sy = sub_y_offset if i < 2 else sub_y_offset + 25
            
            draw_shadow_text(draw, (sx, sy), f"{s_name[:12]}:", font_med, (180, 190, 205, 255))
            val_w = draw.textlength(s_val, font=font_med) if hasattr(draw, 'textlength') else len(s_val)*10
            draw_shadow_text(draw, (sx + 150 - val_w, sy), s_val, font_med, (240, 240, 245, 255))
            
        relic_y += 120

    buf = BytesIO()
    card.save(buf, format="PNG")
    buf.seek(0)
    return buf

# === دوال البوت الأساسية ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 **أهلاً بك!**\nأرسل الـ UID لعرض شخصياتك بجودة سينمائية:\n🔹 `/hsr <UID>`", parse_mode='Markdown')

async def hsr_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ يرجى كتابة الـ UID بعد الأمر!\nمثال: `/hsr 701021140`")
        return
    uid = context.args[0]
    m = await update.message.reply_text("⏳ جاري جلب البيانات السحرية...")
    
    url = f"https://api.mihomo.me/sr_info_parsed/{uid}?lang=en"
    async with httpx.AsyncClient() as client:
        try:
            res = await client.get(url, timeout=15)
            if res.status_code != 200:
                await m.edit_text("❌ الحساب غير موجود أو غير مفعل العرض للعامة.")
                return
            data = res.json()
            avatars = data.get("characters", [])
            if not avatars:
                await m.edit_text("⚠️ لا توجد شخصيات معروضة (تأكد من إعدادات حسابك باللعبة).")
                return

            keyboard = [[InlineKeyboardButton(c.get("name", f"Char {i+1}"), callback_data=f"hsr_{uid}_{i}")] for i, c in enumerate(avatars)]
            # ترتيب الأزرار كـ 4 في الصف
            formatted_kb = [keyboard[i:i + 4] for i in range(0, len(keyboard), 4)]
            
            await m.edit_text(f"👤 **{data.get('player',{}).get('nickname', 'Player')}**\n✨ اختر الشخصية لاستخراج البطاقة السينمائية:", 
                              reply_markup=InlineKeyboardMarkup(formatted_kb), parse_mode='Markdown')
        except Exception as e:
            await m.edit_text("❌ حدث خطأ أثناء الاتصال.")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid, char_idx = query.data.split("_")[1], int(query.data.split("_")[2])
    
    msg = await query.message.reply_text("✨ يتم الآن بناء الصورة سينمائياً، ثواني فقط...")
    url = f"https://api.mihomo.me/sr_info_parsed/{uid}?lang=en"
    
    async with httpx.AsyncClient() as client:
        try:
            res = await client.get(url, timeout=15)
            data = res.json()
            card_buf = await create_character_card(client, data["characters"][char_idx], data["player"])
            await query.message.reply_photo(photo=card_buf, reply_to_message_id=query.message.message_id)
            await msg.delete()
        except Exception as e:
            print(f"Error: {e}")
            await msg.edit_text("❌ فشل بناء الصورة. حاول مرة أخرى.")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("hsr", hsr_check))
    app.add_handler(CallbackQueryHandler(button_callback))
    print("🚀 البوت السحري يعمل!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
