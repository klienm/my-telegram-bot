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

# 1. إعداد قاعدة البيانات لتخزين وتتبع الرسائل
def init_db():
    conn = sqlite3.connect('messages.db')
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

# دالة لتسجيل وحساب الرسائل في القاعدة
def track_message(user):
    if user.is_bot:
        return
    
    conn = sqlite3.connect('messages.db')
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
    conn = sqlite3.connect('messages.db')
    cursor = conn.cursor()
    cursor.execute('SELECT message_count FROM user_messages WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0

# 2. أمر ايدي (Id Command)
async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    track_message(user)
    
    msg_count = get_user_message_count(user.id)
    full_name = f"{user.first_name} {user.last_name or ''}".strip()
    username = f"@{user.username}" if user.username else "لا يوجد"
    
    gender_text = "ولد" # التحديد الافتراضي أو التحليل الذكي
    
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

# 3. نظام شخصيات Honkai: Star Rail (HSR)
async def hsr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    track_message(user)
    
    if not context.args:
        await update.message.reply_text("الرجاء إدخال الـ UID الخاص بك بعد الأمر، مثال:\n`/hsr 700000000`", parse_mode="Markdown")
        return
    
    uid = context.args[0]
    url = f"https://api.mihomo.me/sr_record/{uid}?lang=en"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                await update.message.reply_text("❌ تعذر جلب بيانات الحساب. تأكد من صحة الـ UID وأن الملف الشخصي عام في اللعبة.")
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
    await query.answer()
    
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
    
    # تصميم بطاقة الشخصية بالفوتوشوب البرمجي (Pillow)
    card = Image.new("RGB", (800, 450), color=(25, 25, 35))
    draw = ImageDraw.Draw(card)
    
    try:
        font = ImageFont.truetype("arial.ttf", 28)
        font_small = ImageFont.truetype("arial.ttf", 20)
    except:
        font = ImageFont.load_default()
        font_small = ImageFont.load_default()
        
    draw.rectangle([20, 20, 780, 430], outline=(255, 215, 0), width=3)
    draw.text((40, 40), f"Character: {name}", fill=(255, 255, 255), font=font)
    draw.text((40, 90), f"Level: {level} | Rarity: {rarity}★", fill=(200, 200, 200), font=font_small)
    draw.text((40, 130), f"Element: {element}", fill=(200, 200, 200), font=font_small)
    draw.text((40, 180), f"UID: {uid}", fill=(150, 150, 150), font=font_small)
    
    bio = BytesIO()
    card.save(bio, "JPEG")
    bio.seek(0)
    
    await query.message.reply_photo(photo=bio, caption=f"✨ بطاقة الشخصية: **{name}**", parse_mode="Markdown")

# 4. مراقب الرسائل العام (Message Listener)
async def message_listener(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user and update.message:
        track_message(update.effective_user)
        
        text = update.message.text or ""
        if text.strip() == "ايدي":
            await id_command(update, context)

# 5. السيرفر الوهمي للبقاء مستيقظاً 24/7 (Dummy Server)
app_web = Flask('')

@app_web.route('/')
def home():
    return "Bot is alive, connected to DB and running 24/7!"

def run_web():
    app_web.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_web)
    t.start()

# الدالة الرئيسية لتشغيل البوت
def main():
    TOKEN = os.environ.get("BOT_TOKEN")
    if not TOKEN:
        print("Error: BOT_TOKEN environment variable not found!")
        return

    application = ApplicationBuilder().token(TOKEN).build()

    # تسجيل الهاندلرز والأوامر
    application.add_handler(CommandHandler("id", id_command))
    application.add_handler(CommandHandler("hsr", hsr_command))
    application.add_handler(CommandHandler("start", hsr_command))
    application.add_handler(CallbackQueryHandler(hsr_callback, pattern="^hsr_"))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message_listener))

    # تشغيل السيرفر الوهمي في الخلفية
    keep_alive()
    
    print("Bot is up and running smoothly...")
    application.run_polling()

if __name__ == '__main__':
    main()
