import os
import sqlite3
import logging
import aiohttp
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
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

# أمر ايدي المطور
async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    
    # لا داعي لزيادة العداد هنا لأن مراقب الرسائل سيزيده
    msg_count = get_user_message_count(user.id)
    full_name = f"{user.first_name} {user.last_name or ''}".strip()
    username = f"@{user.username}" if user.username else "لا يوجد"
    
    gender_text = "ولد" 
    
    caption = (
        f"👤 **الاسم:** {full_name}\n"
        f"🔗 **اليوزر:** {username}\n"
        f"💬 **عدد رسائلك:** {msg_count}\n"
        f"⚧ **الجنس:** {gender_text}"
    )

    try:
        photos = await context.bot.get_user_profile_photos(user.id, limit=1)
        if photos and photos.total_count > 0:
            photo_file_id = photos.photos[0][0].file_id
            await update.message.reply_photo(photo=photo_file_id, caption=caption, parse_mode="Markdown")
        else:
            await update.message.reply_text(caption, parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(caption, parse_mode="Markdown")

# أدوات مساعدة للتصميم
def create_gradient(width, height, color1, color2):
    base = Image.new('RGB', (width, height), color1)
    top = Image.new('RGB', (width, height), color2)
    mask = Image.new('L', (width, height))
    mask_data = []
    for y in range(height):
        mask_data.extend([int(255 * (y / height))] * width)
    mask.putdata(mask_data)
    base.paste(top, (0, 0), mask)
    return base

def draw_shadow_text(draw, position, text, font, fill_color, shadow_color=(10, 10, 15), offset=(3, 3)):
    x, y = position
    draw.text((x + offset[0], y + offset[1]), text, font=font, fill=shadow_color)
    draw.text((x, y), text, font=font, fill=fill_color)

def get_best_font(size):
    fonts_to_try = ["DejaVuSans.ttf", "arial.ttf", "FreeSans.ttf"]
    for f in fonts_to_try:
        try:
            return ImageFont.truetype(f, size)
        except IOError:
            continue
    return ImageFont.load_default()

# نظام شخصيات Honkai: Star Rail (HSR)
async def hsr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("الرجاء إدخال الـ UID الخاص بك بعد الأمر، مثال:\n`/hsr 700000000`", parse_mode="Markdown")
        return
    
    uid = context.args[0]
    url = f"https://api.mihomo.me/sr_record/{uid}?lang=en"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                await update.message.reply_text("❌ تعذر جلب بيانات الحساب. تأكد من صحة الـ UID وأن الملف الشخصي عام.")
                return
            data = await response.json()
            
    try:
        characters = data.get("characters", [])
        if not characters:
            await update.message.reply_text("❌ لا توجد شخصيات ظاهرة في هذا الحساب.")
            return
            
        keyboard = []
        for char in characters:
            name = char.get("name", "Unknown")
            element = char.get("element", {}).get("name", "")
            char_id = char.get("id")
            keyboard.append([InlineKeyboardButton(f"{name} ({element})", callback_data=f"hsr_{uid}_{char_id}")])
            
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("✨ **اختر الشخصية لعرض بطاقتها الاحترافية:**", reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ أثناء معالجة البيانات: {e}")

async def hsr_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("جاري تصميم البطاقة...", show_alert=False)
    
    data_parts = query.data.split("_")
    uid = data_parts[1]
    char_id = data_parts[2]
    
    url = f"https://api.mihomo.me/sr_record/{uid}?lang=en"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                await query.edit_message_text("❌ انتهت صلاحية البيانات أو حدث خطأ.")
                return
            data = await response.json()
            
    characters = data.get("characters", [])
    selected_char = next((c for c in characters if str(c.get("id")) == char_id), None)
    
    if not selected_char:
        await query.edit_message_text("❌ لم يتم العثور على الشخصية المطلوبة.")
        return
        
    name = selected_char.get("name", "Unknown")
    level = selected_char.get("level", 1)
    rarity = selected_char.get("rarity", 5)
    element = selected_char.get("element", {}).get("name", "Unknown")
    
    base_stats = {a["name"]: a["value"] for a in selected_char.get("attributes", [])}
    add_stats = {a["name"]: a["value"] for a in selected_char.get("additions", [])}
    
    def format_stat(stat_name, val):
        if "CRIT" in stat_name or "Boost" in stat_name or "Effect" in stat_name or "Regen" in stat_name:
            return f"{val * 100:.1f}%"
        return f"{int(val):,}"

    target_stats = ["HP", "ATK", "DEF", "SPD", "CRIT Rate", "CRIT DMG"]
    display_stats = []
    for st in target_stats:
        total_val = base_stats.get(st, 0) + add_stats.get(st, 0)
        if total_val > 0:
            display_stats.append((st, format_stat(st, total_val)))

    color_top = (18, 20, 35)
    color_bottom = (40, 25, 55)
    card = create_gradient(1200, 675, color_top, color_bottom)
    draw = ImageDraw.Draw(card)
    
    font_title = get_best_font(75)
    font_sub = get_best_font(45)
    font_stat_label = get_best_font(40)
    font_stat_val = get_best_font(40)
    
    draw_shadow_text(draw, (70, 70), f"{name}", font_title, (255, 220, 100))
    draw_shadow_text(draw, (70, 170), f"Level: {level}   |   Rarity: {rarity}★   |   Element: {element}", font_sub, (230, 230, 230))
    
    start_x, start_y = 70, 280
    y_offset = 0
    for stat_name, stat_val in display_stats:
        draw_shadow_text(draw, (start_x, start_y + y_offset), f"{stat_name}", font_stat_label, (180, 190, 200))
        draw_shadow_text(draw, (start_x + 350, start_y + y_offset), f"{stat_val}", font_stat_val, (255, 255, 255))
        y_offset += 65
    
    draw_shadow_text(draw, (70, 590), f"UID: {uid}", font_sub, (120, 120, 140))
    
    bio = BytesIO()
    card.save(bio, "PNG", quality=100)
    bio.seek(0)
    
    await query.message.reply_photo(photo=bio, caption=f"✨ **إحصائيات {name} الدقيقة**", parse_mode="Markdown")

# مراقب الرسائل والرد الذكي
async def message_listener(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message or not update.message.text:
        return
        
    user = update.effective_user
    track_message(user)
    
    text = update.message.text.strip()
    
    # 1. التقاط كلمة ايدي بأكثر من شكل
    if text in ["ايدي", "أيدي", "إيدي", "ايدى"]:
        await id_command(update, context)
        return

    # 2. الرد على المنشن أو الرد على البوت
    bot_username = context.bot.username
    is_mentioned = bot_username and (f"@{bot_username}" in text)
    is_reply_to_bot = update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id

    if is_mentioned or is_reply_to_bot:
        ai_reply_text = (
            "مرحباً! أنا مساعدك الذكي 🤖\n\n"
            "إليك ما يمكنني فعله:\n"
            "• اكتب كلمة **ايدي** لعرض معلومات حسابك وعدد رسائلك.\n"
            "• استخدم الأمر `/hsr [UID]` لعرض بطاقات شخصياتك بدقة عالية."
        )
        await update.message.reply_text(ai_reply_text, parse_mode="Markdown")

# السيرفر الوهمي
app_web = Flask('')

@app_web.route('/')
def home():
    return "Bot is alive and running!"

def run_web():
    app_web.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_web)
    t.start()

def main():
    TOKEN = os.environ.get("BOT_TOKEN")
    if not TOKEN:
        print("Error: BOT_TOKEN environment variable not found!")
        return

    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("id", id_command))
    application.add_handler(CommandHandler("hsr", hsr_command))
    application.add_handler(CommandHandler("start", hsr_command))
    application.add_handler(CallbackQueryHandler(hsr_callback, pattern="^hsr_"))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message_listener))

    keep_alive()
    print("Bot is up and running...")
    application.run_polling()

if __name__ == '__main__':
    main()
