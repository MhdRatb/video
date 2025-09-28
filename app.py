# /path/to/your/project/video_bot.py

import logging
import os
import sqlite3
import yt_dlp
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler, ConversationHandler
from telegram.constants import ParseMode
from telegram.error import TelegramError

# ==============================================================================
# ١. الإعدادات (بديل لـ config.py)
# ==============================================================================

# ضع هنا توكن البوت الذي حصلت عليه من BotFather
BOT_TOKEN = os.getenv("BOT_TOKEN")

# ضع هنا معرف حسابك العددي على تليجرام (يمكنك الحصول عليه من @userinfobot)
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(admin_id) for admin_id in ADMIN_IDS_STR.split(',') if admin_id]

# اسم ملف قاعدة البيانات. سيتم تخزينه في مسار ثابت على Railway
# إذا لم يتم تحديد مسار، سيستخدم المسار المحلي للتجربة
DATABASE_PATH = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
DATABASE_NAME = os.path.join(DATABASE_PATH, "bot_data.db") if DATABASE_PATH else "bot_data.db"

if not BOT_TOKEN:
    raise ValueError("لم يتم العثور على متغير البيئة BOT_TOKEN. يرجى إضافته.")

# حد التحميل للمستخدمين العاديين (100 ميجابايت)
FREE_TIER_LIMIT_BYTES = 100 * 1024 * 1024

# ==============================================================================
# ٢. دوال قاعدة البيانات (بديل لـ database.py)
# ==============================================================================

def init_db():
    """
    يقوم بإنشاء الجداول في قاعدة البيانات إذا لم تكن موجودة.
    """
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        # جدول لتخزين المستخدمين
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users ( 
                user_id INTEGER PRIMARY KEY,
                is_subscriber INTEGER DEFAULT 0
            )
        ''')
        # جدول لتخزين الإعدادات (مثل قناة الاشتراك الإجباري)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        conn.commit()
        # التأكد من وجود عمود is_subscriber في الجداول القديمة
        try:
            cursor.execute("SELECT is_subscriber FROM users LIMIT 1")
        except sqlite3.OperationalError:
            cursor.execute("ALTER TABLE users ADD COLUMN is_subscriber INTEGER DEFAULT 0")
            conn.commit()
            logger.info("تم تحديث جدول المستخدمين بنجاح.")

def is_premium_user(user_id: int) -> bool:
    """يتحقق مما إذا كان المستخدم مشتركًا."""
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_subscriber FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        return result and result[0] == 1

def add_user(user_id: int):
    """
    يضيف مستخدمًا جديدًا إلى قاعدة البيانات إذا لم يكن موجودًا.
    """
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()

def get_all_users() -> list[int]:
    """
    يعيد قائمة بجميع معرفات المستخدمين المسجلين في البوت.
    """
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users")
        return [row[0] for row in cursor.fetchall()]

def get_user_count() -> int:
    """
    يعيد عدد المستخدمين الكلي.
    """
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        return cursor.fetchone()[0]

def set_setting(key: str, value: str):
    """
    يضبط قيمة مفتاح معين في جدول الإعدادات (مثل قناة الاشتراك).
    """
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()

def subscribe_user(user_id: int):
    """يجعل المستخدم مشتركًا."""
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        # التأكد من وجود المستخدم أولاً
        cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        cursor.execute("UPDATE users SET is_subscriber = 1 WHERE user_id = ?", (user_id,))
        conn.commit()

def unsubscribe_user(user_id: int):
    """يلغي اشتراك المستخدم."""
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_subscriber = 0 WHERE user_id = ?", (user_id,))
        conn.commit()

def get_setting(key: str) -> str | None:
    """
    يجلب قيمة مفتاح معين من جدول الإعدادات.
    """
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        result = cursor.fetchone()
        return result[0] if result else None

# ==============================================================================
# ٣. الدوال المساعدة (بديل لـ helpers.py)
# ==============================================================================

# إعدادات yt-dlp لتحميل أفضل صيغة فيديو وصوت ودمجهما
YDL_OPTS_VIDEO = {
    # إعادة الإعدادات إلى الوضع الأمثل الذي يعتمد على ffmpeg لدمج أفضل جودة
    'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
    'outtmpl': 'downloads/%(id)s.%(ext)s', # مسار حفظ الفيديو المؤقت
    'noplaylist': True,
    'max_filesize': 2000 * 1024 * 1024, # 2GB حد أقصى لحجم الفيديو (حد تليجرام)
}

YDL_OPTS_AUDIO = {
    'format': 'bestaudio/best',
    'outtmpl': 'downloads/%(id)s.%(ext)s',
    'noplaylist': True,
    'max_filesize': 2000 * 1024 * 1024,
    # إعادة تفعيل المعالجة اللاحقة لتحويل الصوت إلى صيغة m4a القياسية
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'm4a',
    }],
}

def format_bytes(size):
    """يحول البايت إلى صيغة مقروءة (KB, MB, GB)."""
    if size is None:
        return "غير معروف"
    power = 1024
    n = 0
    power_labels = {0: '', 1: 'KB', 2: 'MB', 3: 'GB'}
    while size > power and n < len(power_labels) -1 :
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}"

async def download_video(url: str) -> str | None:
    """
    يقوم بتحميل الفيديو من الرابط المحدد باستخدام yt-dlp.
    يعيد مسار الملف المحمل أو None في حالة الفشل.
    """
    if not os.path.exists('downloads'):
        os.makedirs('downloads')

    try:
        with yt_dlp.YoutubeDL(YDL_OPTS_VIDEO) as ydl:
            logging.info(f"بدء تحميل الفيديو من: {url}")
            info = ydl.extract_info(url, download=True)
            filepath = ydl.prepare_filename(info)
            logging.info(f"اكتمل التحميل: {filepath}")
            return filepath
    except Exception as e:
        logging.error(f"فشل تحميل الفيديو: {e}")
        return None

async def download_media(url: str, media_type: str, format_id: str = None) -> tuple[str | None, str | None]:
    """
    يقوم بتحميل الفيديو أو الصوت من الرابط المحدد.
    يعيد مسار الملف المحمل ونوعه (video/audio) أو (None, None) في حالة الفشل.
    """
    if not os.path.exists('downloads'):
        os.makedirs('downloads')
    
    opts = YDL_OPTS_VIDEO.copy() if media_type == 'video' else YDL_OPTS_AUDIO.copy()
    if format_id:
        opts['format'] = format_id

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            logging.info(f"بدء تحميل {media_type} من: {url}")
            info = ydl.extract_info(url, download=True)
            filepath = ydl.prepare_filename(info)
            logging.info(f"اكتمل التحميل: {filepath}")
            return filepath, media_type
    except Exception as e:
        logging.error(f"فشل تحميل {media_type}: {e}")
        return None, None
# ==============================================================================
# ٤. منطق البوت الرئيسي (ملف bot.py سابقاً)
# ==============================================================================

# إعداد تسجيل الأحداث لعرض معلومات مفيدة أثناء التشغيل
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- دوال مساعدة ---

async def is_user_subscribed(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    للتحقق مما إذا كان المستخدم مشتركًا في القناة الإجبارية.
    """
    channel_id = get_setting('force_channel')
    if not channel_id:
        return True  # لا توجد قناة إجبارية، لذا نعتبره مشتركًا

    try:
        member = await context.bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except TelegramError as e:
        logger.error(f"خطأ في التحقق من اشتراك المستخدم {user_id} في القناة {channel_id}: {e}")
        return False

# --- أوامر المستخدمين ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    # إضافة التحقق من الاشتراك هنا أيضاً
    if not await is_user_subscribed(user_id, context):
        channel_id = get_setting('force_channel')
        channel_link = f"https://t.me/{channel_id.lstrip('@')}" if channel_id else ""
        await update.message.reply_text(
            f"عذراً، يجب عليك الاشتراك في القناة أولاً للاستمرار: {channel_link}\n\n"
            "بعد الاشتراك، اضغط على /start مجدداً."
        )
        return

    add_user(user.id)
    await update.message.reply_html(
        f"أهلاً بك يا {user.mention_html()}!\n\n"
        "أنا بوت تحميل الفيديوهات. أرسل لي أي رابط فيديو وسأقوم بتحميله وإرساله لك.\n\n"
        "للحصول على المساعدة، استخدم الأمر /help."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "<b>مرحباً بك في بوت تحميل الفيديوهات!</b>\n\n"
        "<b>كيفية الاستخدام:</b>\n"
        "فقط أرسل رابط الفيديو الذي تريد تحميله.\n\n"
        "<b>الأوامر المتاحة:</b>\n"
        "/start - بدء استخدام البوت\n"
        "/help - عرض هذه الرسالة\n\n"
        "<b>أوامر الأدمن (خاصة بالمسؤولين):</b>\n"
        "/stats - عرض إحصائيات البوت\n"
        "/broadcast - لعمل إذاعة للمستخدمين (بالرد على رسالة)\n"
        "/setchannel <code>@username</code> - لضبط قناة الاشتراك الإجباري\n"
        "/delchannel - لحذف قناة الاشتراك الإجباري"
    )
    await update.message.reply_html(help_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # التحقق من وجود مستخدم مرتبط بالرسالة
    if not update.effective_user:
        logger.info("تم استلام رسالة بدون مستخدم (قد تكون من قناة أو مشرف مجهول)، سيتم تجاهلها.")
        return

    user_id = update.effective_user.id
    
    if not await is_user_subscribed(user_id, context):
        channel_id = get_setting('force_channel')
        channel_link = f"https://t.me/{channel_id.lstrip('@')}"
        await update.message.reply_text(
            f"عذراً، يجب عليك الاشتراك في القناة أولاً للاستمرار: {channel_link}\n\n"
            "بعد الاشتراك، حاول مجدداً."
        )
        return

    url = update.message.text
    if not (url.startswith('http://') or url.startswith('https://')):
        await update.message.reply_text("الرجاء إرسال رابط صالح.")
        return

    status_message = await update.message.reply_text("⏳ جارٍ جلب معلومات الفيديو...")

    try:
        # جلب المعلومات فقط بدون تحميل
        with yt_dlp.YoutubeDL({'noplaylist': True}) as ydl:
            info = ydl.extract_info(url, download=False)

        # --- منطق جديد لتجميع خيارات التحميل ---
        keyboard = []
        available_formats = {} # لتخزين أفضل صيغة لكل دقة

        # البحث عن أفضل صيغة صوت M4A
        best_audio = next((f for f in sorted(info.get('formats', []), key=lambda x: x.get('filesize') or 0, reverse=True) 
                           if f.get('vcodec') == 'none' and f.get('ext') == 'm4a'), None)
        if best_audio:
            size_str = format_bytes(best_audio.get('filesize') or best_audio.get('filesize_approx'))
            keyboard.append([InlineKeyboardButton(f"🎵 صوت M4A ({size_str})", callback_data=f"download:audio:{best_audio['format_id']}:{update.message.message_id}")])
            available_formats['audio'] = best_audio

        # البحث عن صيغ الفيديو المختلفة
        resolutions = ['1080', '720', '480', '360', '240']
        for res in resolutions:
            # البحث عن أفضل صيغة MP4 مدمجة (فيديو+صوت) لهذه الدقة
            best_format = next((f for f in sorted(info.get('formats', []), key=lambda x: x.get('filesize') or 0, reverse=True)
                                if f.get('height') == int(res) and f.get('ext') == 'mp4' and f.get('vcodec') != 'none' and f.get('acodec') != 'none'), None)
            
            # إذا لم نجد صيغة مدمجة، نبحث عن أفضل فيديو منفصل وندمجه مع أفضل صوت
            if not best_format:
                video_only = next((f for f in sorted(info.get('formats', []), key=lambda x: x.get('tbr') or 0, reverse=True)
                                   if f.get('height') == int(res) and f.get('ext') == 'mp4' and f.get('vcodec') != 'none' and f.get('acodec') == 'none'), None)
                if video_only:
                    # أفضل صوت متاح للدمج
                    audio_for_merge = next((f for f in sorted(info.get('formats', []), key=lambda x: x.get('tbr') or 0, reverse=True)
                                            if f.get('acodec') != 'none' and f.get('vcodec') == 'none'), None)
                    if audio_for_merge:
                        video_size = video_only.get('filesize') or video_only.get('filesize_approx')
                        audio_size = audio_for_merge.get('filesize') or audio_for_merge.get('filesize_approx')
                        total_size = (video_size or 0) + (audio_size or 0)
                        best_format = video_only
                        best_format['filesize_approx'] = total_size
                        # سنقوم بدمج أفضل فيديو مع أفضل صوت
                        best_format['format_id'] = f"{video_only['format_id']}+{audio_for_merge['format_id']}"

            if best_format:
                size_str = format_bytes(best_format.get('filesize') or best_format.get('filesize_approx'))
                keyboard.append([InlineKeyboardButton(f"🎬 فيديو {res}p ({size_str})", callback_data=f"download:video:{best_format['format_id']}:{update.message.message_id}")])
                available_formats[res] = best_format

        if not keyboard:
            await status_message.edit_text("❌ عذراً، لم يتم العثور على صيغ تحميل مدعومة لهذا الرابط.")
            return

        # تخزين معلومات الصيغ المتاحة في chat_data
        original_message_id = update.message.message_id
        context.chat_data[original_message_id] = {'url': url, 'formats': available_formats}

        # إضافة زر الإلغاء
        keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:{original_message_id}")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        title = info.get('title', 'فيديو')
        await status_message.edit_text(f"<b>{title}</b>\n\nاختر الصيغة التي تريد تحميلها:", reply_markup=reply_markup, parse_mode=ParseMode.HTML)

    except Exception as e:
        # التحقق من نوع الخطأ لتوفير رسالة أوضح للمستخدم
        if isinstance(e, TelegramError) and "Button_data_invalid" in str(e):
            logger.error(f"فشل في جلب معلومات الفيديو بسبب طول الرابط: {e}")
            await status_message.edit_text("❌ حدث خطأ: الرابط الذي أرسلته طويل جدًا ولا يمكن معالجته. حاول استخدام رابط أقصر إذا أمكن.")
        else:
            logger.error(f"فشل في جلب معلومات الفيديو: {e}")
        await status_message.edit_text("❌ حدث خطأ أثناء جلب معلومات الفيديو. قد يكون الرابط غير صالح أو غير مدعوم.")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    # استعادة البيانات: action, media_type, format_id, original_message_id
    parts = data.split(":", 3)
    action, media_type, format_id, original_message_id_str = parts if len(parts) == 4 else (parts[0], None, None, parts[1])
    original_message_id = int(original_message_id_str)

    if action == "cancel":
        await query.message.delete()
        # تنظيف chat_data
        context.chat_data.pop(original_message_id, None)
        return

    if action == "download":
        # استرجاع بيانات الرابط والصيغ من chat_data
        media_info = context.chat_data.get(original_message_id)
        if not media_info:
            await query.edit_message_text(text="❌ حدث خطأ. ربما تكون هذه الرسالة قديمة جداً. الرجاء إرسال الرابط مرة أخرى.")
            return

        download_url = media_info.get('url')
        user_id = query.from_user.id
        is_premium = is_premium_user(user_id)

        # التحقق من حجم الملف للمستخدمين العاديين
        file_size = None
        selected_format_key = 'audio' if media_type == 'audio' else format_id.split('+')[0] # للبحث في القاموس
        for key, fmt in media_info.get('formats', {}).items():
            if fmt['format_id'] == format_id or fmt['format_id'] == selected_format_key:
                file_size = fmt.get('filesize') or fmt.get('filesize_approx')
                break

        if not is_premium and file_size and file_size > FREE_TIER_LIMIT_BYTES:
            limit_mb = FREE_TIER_LIMIT_BYTES / (1024*1024)
            await query.edit_message_text(
                text=f"🚫 عذراً، حجم الملف يتجاوز الحد المسموح به للمستخدمين العاديين ({limit_mb:.0f} MB).\n\n"
                     "لتحميل ملفات بأحجام غير محدودة، يرجى الترقية إلى الاشتراك المدفوع.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إغلاق", callback_data=f"cancel:{original_message_id}")]]),
            )
            return

        await query.edit_message_text(text=f"⏳ جارٍ تحميل الـ {media_type}، يرجى الانتظار...")
        
        filepath, downloaded_type = await download_media(download_url, media_type, format_id)

        if not filepath:
            await query.edit_message_text(text=f"❌ فشل تحميل الـ {media_type}. حاول مجدداً أو جرب رابطاً آخر.")
            return

        try:
            await query.edit_message_text(text=f"⬆️ جارٍ رفع الـ {downloaded_type}...")
            with open(filepath, 'rb') as file:
                if downloaded_type == 'video':
                    await context.bot.send_video(chat_id=query.message.chat_id, video=file, caption=f"تم التحميل بواسطة @{context.bot.username}", supports_streaming=True)
                elif downloaded_type == 'audio':
                    await context.bot.send_audio(chat_id=query.message.chat_id, audio=file, caption=f"تم التحميل بواسطة @{context.bot.username}")
            
            await query.message.delete() # حذف رسالة الأزرار بعد الإرسال
        except TelegramError as e:
            # تنظيف chat_data في حالة الخطأ أيضاً
            context.chat_data.pop(original_message_id, None)
            logger.error(f"فشل رفع الملف: {e}")
            await query.edit_message_text(text=f"❌ فشل رفع الملف إلى تليجرام. قد يكون حجمه أكبر من 2 جيجابايت.\n\nالخطأ: {e}")
        finally:
            if os.path.exists(filepath):
                os.remove(filepath)
                logger.info(f"تم حذف الملف المؤقت: {filepath}")
            # تنظيف chat_data بعد الانتهاء بنجاح
            context.chat_data.pop(original_message_id, None)

# --- نظام محادثة لوحة تحكم الأدمن ---

# تعريف الحالات
ADMIN_PANEL, AWAITING_BROADCAST, AWAITING_SUBSCRIBE_ID, AWAITING_UNSUBSCRIBE_ID, AWAITING_CHANNEL_ID = range(5)

async def admin_panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """نقطة الدخول لمحادثة الأدمن."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats")],
        [InlineKeyboardButton("📢 إذاعة", callback_data="admin_broadcast")],
        [InlineKeyboardButton("➕ تفعيل اشتراك", callback_data="admin_subscribe")],
        [InlineKeyboardButton("➖ إلغاء اشتراك", callback_data="admin_unsubscribe")],
        [InlineKeyboardButton("📺 ضبط القناة", callback_data="admin_setchannel")],
        [InlineKeyboardButton("🗑️ حذف القناة", callback_data="admin_delchannel")],
        [InlineKeyboardButton("❌ إغلاق", callback_data="admin_close")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # إذا كانت الرسالة جديدة، أرسل لوحة التحكم. إذا كانت تعديلاً، قم بتعديلها.
    # نتحقق إذا كان هناك رسالة موجودة يمكن تعديلها (من ضغط زر)
    if update.callback_query and update.callback_query.message:
        await update.callback_query.edit_message_text(
            "⚙️ <b>لوحة تحكم الأدمن</b>\n\nاختر الإجراء المطلوب:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    else:
        # إذا لم يكن، نرسل رسالة جديدة (عند بدء الأمر /admin أو العودة من عملية)
        await update.message.reply_text(
            "⚙️ <b>لوحة تحكم الأدمن</b>\n\nاختر الإجراء المطلوب:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    return ADMIN_PANEL

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يعرض إحصائيات البوت."""
    query = update.callback_query
    await query.answer()
    user_count = get_user_count()
    await query.edit_message_text(
        f"📊 <b>إحصائيات البوت</b>\n\n👥 عدد المستخدمين: {user_count}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_back_to_panel")]])
    )
    return ADMIN_PANEL

async def admin_request_input(update: Update, context: ContextTypes.DEFAULT_TYPE, message: str, next_state: int) -> int:
    """دالة مساعدة لطلب إدخال من الأدمن."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        text=message,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_back_to_panel")]])
    )
    return next_state

async def handle_subscribe_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يعالج إدخال ID المستخدم للاشتراك."""
    try:
        user_id_to_subscribe = int(update.message.text)
        subscribe_user(user_id_to_subscribe)
        await update.message.reply_text(
            f"✅ تم تفعيل اشتراك المستخدم: `{user_id_to_subscribe}` بنجاح\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except (ValueError, IndexError):
        await update.message.reply_text("❌ خطأ: الرجاء إدخال معرف مستخدم رقمي صالح.")
    
    # العودة إلى لوحة التحكم الرئيسية
    await admin_panel_command(update, context)
    return ADMIN_PANEL

async def handle_unsubscribe_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يعالج إدخال ID المستخدم لإلغاء الاشتراك."""
    try:
        user_id_to_unsubscribe = int(update.message.text)
        unsubscribe_user(user_id_to_unsubscribe)
        await update.message.reply_text(
            f"✅ تم إلغاء اشتراك المستخدم: `{user_id_to_unsubscribe}` بنجاح\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except (ValueError, IndexError):
        await update.message.reply_text("❌ خطأ: الرجاء إدخال معرف مستخدم رقمي صالح.")
    
    await admin_panel_command(update, context)
    return ADMIN_PANEL

async def handle_set_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يعالج إدخال معرف القناة."""
    channel_id = update.message.text
    if not channel_id.startswith('@'):
        await update.message.reply_text("❌ خطأ: يجب أن يبدأ معرف القناة بـ @.")
        await admin_panel_command(update, context)
        return ADMIN_PANEL

    try:
        bot_member = await context.bot.get_chat_member(chat_id=channel_id, user_id=context.bot.id)
        if not bot_member.status in ['administrator', 'creator']:
             await update.message.reply_text("❌ خطأ: يجب أن يكون البوت مشرفًا في القناة أولاً.")
             await admin_panel_command(update, context)
             return ADMIN_PANEL
    except TelegramError:
        await update.message.reply_text("❌ خطأ: لا يمكن الوصول إلى القناة. تأكد من صحة المعرف وأن البوت عضو فيها.")
        await admin_panel_command(update, context)
        return ADMIN_PANEL

    set_setting('force_channel', channel_id)
    await update.message.reply_text(f"✅ تم تعيين قناة الاشتراك الإجباري إلى: {channel_id}")
    await admin_panel_command(update, context)
    return ADMIN_PANEL

async def admin_del_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يحذف قناة الاشتراك الإجباري."""
    query = update.callback_query
    await query.answer()
    set_setting('force_channel', '')
    await query.edit_message_text(
        "✅ تم حذف قناة الاشتراك الإجباري بنجاح.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_back_to_panel")]])
    )
    return ADMIN_PANEL

async def handle_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """ينفذ الإذاعة."""
    users = get_all_users()
    sent_count = 0
    failed_count = 0
    status_msg = await update.message.reply_text(f"⏳ جارٍ بدء الإذاعة إلى `{len(users)}` مستخدم\.\.\.", parse_mode=ParseMode.MARKDOWN_V2)

    for user_id in users:
        try:
            await context.bot.copy_message(chat_id=user_id, from_chat_id=update.message.chat_id, message_id=update.message.message_id)
            sent_count += 1
        except TelegramError as e:
            logger.warning(f"فشل إرسال الإذاعة إلى {user_id}: {e}")
            failed_count += 1
    
    await status_msg.edit_text(
        f"✅ اكتملت الإذاعة!\n\n"
        f"✔️ تم الإرسال بنجاح إلى: {sent_count} مستخدم\n"
        f"❌ فشل الإرسال إلى: {failed_count} مستخدم"
    )
    await admin_panel_command(update, context)
    return ADMIN_PANEL

async def admin_close_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يغلق لوحة تحكم الأدمن."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("تم إغلاق لوحة التحكم.")
    return ConversationHandler.END

async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يلغي العملية الحالية ويعود للوحة التحكم."""
    await update.message.reply_text("تم إلغاء العملية.")
    await admin_panel_command(update, context)
    return ADMIN_PANEL

# ==============================================================================
# ٥. نقطة انطلاق البوت
# ==============================================================================

def main():
    """
    الدالة الرئيسية لتشغيل البوت.
    """
    # أولاً، قم بتهيئة قاعدة البيانات
    init_db()

    # إنشاء تطبيق البوت
    application = Application.builder().token(BOT_TOKEN).build()

    # إضافة معالجات الأوامر والرسائل
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    
    # --- محادثة الأدمن ---
    admin_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("admin", admin_panel_command)],
        states={
            ADMIN_PANEL: [
                CallbackQueryHandler(admin_stats, pattern="^admin_stats$"),
                CallbackQueryHandler(lambda u, c: admin_request_input(u, c, "أرسل الآن الرسالة التي تريد إذاعتها...", AWAITING_BROADCAST), pattern="^admin_broadcast$"),
                CallbackQueryHandler(lambda u, c: admin_request_input(u, c, "أرسل الآن معرف المستخدم لتفعيل اشتراكه...", AWAITING_SUBSCRIBE_ID), pattern="^admin_subscribe$"),
                CallbackQueryHandler(lambda u, c: admin_request_input(u, c, "أرسل الآن معرف المستخدم لإلغاء اشتراكه...", AWAITING_UNSUBSCRIBE_ID), pattern="^admin_unsubscribe$"),
                CallbackQueryHandler(lambda u, c: admin_request_input(u, c, "أرسل الآن معرف القناة (مثال: @username)...", AWAITING_CHANNEL_ID), pattern="^admin_setchannel$"),
                CallbackQueryHandler(admin_del_channel, pattern="^admin_delchannel$"),
                CallbackQueryHandler(admin_close_panel, pattern="^admin_close$"),
                CallbackQueryHandler(admin_panel_command, pattern="^admin_back_to_panel$"),
            ],
            AWAITING_BROADCAST: [MessageHandler(filters.ALL & ~filters.COMMAND, handle_broadcast)],
            AWAITING_SUBSCRIBE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_subscribe_id)],
            AWAITING_UNSUBSCRIBE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_unsubscribe_id)],
            AWAITING_CHANNEL_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_set_channel)],
        },
        fallbacks=[CommandHandler("cancel", admin_cancel)],
    )
    application.add_handler(admin_conv_handler)

    # معالج الرسائل النصية التي لا تبدأ بأمر
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # معالج ضغطات الأزرار
    # استخدام نمط مختلف لكل نوع من الأزرار لتنظيم الكود
    application.add_handler(CallbackQueryHandler(button_callback, pattern=r"^(download|cancel):"))

    # بدء تشغيل البوت
    logger.info("البوت قيد التشغيل...")
    application.run_polling()

if __name__ == "__main__":
    main()
