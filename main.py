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
    """
    يكبّر/يصغّر الصورة عشان تغطي المساحة المطلوبة بالكامل (متل CSS background-size: cover)
    وبعدين يقصّها لنفس الأبعاد. focus_y بيتحكم بمكان القص العمودي (0 = من فوق، 0.5 = نص، 1 = من تحت)
    عشان نضمن وجه الشخصية يضل ظاهر بدل ما ينقص من فوق.
    """
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

# --- دالة رسم بطاقة الشخصية ---
async def create_character_card(client, char_data, player_data):
    char_name = char_data.get("name", "Character")
    char_level = char_data.get("level", 1)
    char_id = str(char_data.get("id", ""))
    icon_path = char_data.get("icon", "")

    equip = char_data.get("light_cone", {})
    lc_name = equip.get("name", "None") if isinstance(equip, dict) else "None"
    lc_level = equip.get("level", "-") if isinstance(equip, dict) else "-"
    lc_icon = equip.get("icon", "") if isinstance(equip, dict) else ""

    relics = char_data.get("relics", []) or char_data.get("relicList", [])

    card = Image.new("RGBA", (1100, 750), (18, 20, 28, 255))
    draw = ImageDraw.Draw(card)

    draw.rectangle([10, 10, 1090, 740], outline=(65, 80, 110, 255), width=2)

    # --- القسم الأيسر: سبلاش آرت كامل ---
    LEFT_X1, LEFT_Y1, LEFT_X2, LEFT_Y2 = 20, 20, 420, 730
    LEFT_W, LEFT_H = LEFT_X2 - LEFT_X1, LEFT_Y2 - LEFT_Y1

    draw.rectangle([LEFT_X1, LEFT_Y1, LEFT_X2, LEFT_Y2], fill=(24, 28, 38, 255), outline=(45, 60, 85, 255))

    # نجيب صورة السبلاش آرت (بورتريه كامل) بدل الأيقونة الصغيرة
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

    if splash_img:
        splash_img = resize_cover(splash_img, LEFT_W, LEFT_H, focus_y=0.12)
        card.paste(splash_img, (LEFT_X1, LEFT_Y1), splash_img)

    # تدرّج غامق بأسفل السبلاش آرت عشان النصوص تبين واضحة فوقه
    grad_h = 340
    gradient = Image.new("RGBA", (LEFT_W, grad_h), (0, 0, 0, 0))
    grad_draw = ImageDraw.Draw(gradient)
    for gy in range(grad_h):
        t = gy / grad_h
        alpha = int(235 * (t ** 1.6))
        grad_draw.line([(0, gy), (LEFT_W, gy)], fill=(8, 10, 16, alpha))
    card.paste(gradient, (LEFT_X1, LEFT_Y2 - grad_h), gradient)

    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        font_sub = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        font_bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
    except Exception:
        font_title = font_sub = font_bold = font_small = ImageFont.load_default()

    # اسم الشخصية + المستوى فوق التدرّج
    name_y = 560
    draw.text((40, name_y), char_name.upper(), font=font_title, fill=(255, 215, 100, 255))
    draw.text((40, name_y + 28), f"LEVEL {char_level} / 80", font=font_sub, fill=(220, 225, 235, 255))

    # خط فاصل رفيع شبه شفاف
    draw.line([(40, name_y + 55), (400, name_y + 55)], fill=(255, 255, 255, 60), width=1)

    # صف اللايت كون (أيقونة + اسم + مستوى) بدون صندوق صلب
    lc_row_y = name_y + 68
    if lc_icon:
        lc_img_url = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/{lc_icon}"
        lc_img = await fetch_image(client, lc_img_url)
        if lc_img:
            lc_img = lc_img.resize((48, 48), Image.Resampling.LANCZOS)
            card.paste(lc_img, (40, lc_row_y), lc_img)

    draw.text((98, lc_row_y), f"{lc_name[:26]}", font=font_bold, fill=(255, 255, 255, 255))
    draw.text((98, lc_row_y + 22), f"Lv. {lc_level} / 80", font=font_small, fill=(150, 220, 150, 255))

    # معلومات اللاعب بشكل مختصر بالأسفل
    p_name = player_data.get("nickname", "Unknown")
    p_uid = player_data.get("uid", "-")
    p_level = player_data.get("level", "-")
    p_eq = player_data.get("world_level", "-")

    info_y = lc_row_y + 62
    draw.text((40, info_y), f"{p_name}  •  UID {p_uid}", font=font_bold, fill=(255, 255, 255, 255))
    draw.text((40, info_y + 20), f"Trailblaze Lv. {p_level}   |   Equilibrium Lv. {p_eq}", font=font_small, fill=(200, 210, 230, 255))

    # --- القسم الأيمن ---
    draw.rectangle([440, 20, 1070, 730], fill=(20, 24, 34, 255), outline=(45, 60, 85, 255))
    draw.text((460, 35), "EQUIPPED RELICS & STATS", font=font_title, fill=(255, 165, 80, 255))

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
                draw.text((box_x1 + 75, box_y1 + 55), "No Substats recorded", font=font_small, fill=(170, 185, 205, 255))
    else:
        draw.text((470, 120), "No Relics Equipped", font=font_sub, fill=(170, 170, 170, 255))

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
            # الرد مباشرة على الشخص الذي أرسل أمر الـ hsr
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

        # العثور على الرسالة الأصلية التي تحتوي على الأزرار (والتي تمثل رد الشخص على البيلد أو طلبه)
        target_message_id = query.message.message_id

        # إذا كانت رسالة الأزرار نفسها رداً على شخص آخر، سيتم استهداف الرسالة الأصلية أو رسالة الشخص الذي طلب الأزرار
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

                        # إرسال الصورة كـ Reply (رد مع منشن) على الشخص الذي طلب الأزرار/البيلد
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
