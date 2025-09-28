# /path/to/your/project/video_bot.py

import asyncio
import logging
import os
import sqlite3
import yt_dlp
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Message
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

# الحد الأقصى للرفع عبر واجهة برمجة التطبيقات القياسية لتليجرام (50 ميجابايت)
BOT_API_UPLOAD_LIMIT = 50 * 1024 * 1024

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
                user_id INTEGER PRIMARY KEY
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
    'format': 'bestvideo+bestaudio/best',
    # السماح لـ yt-dlp باختيار الامتداد المناسب أثناء التحميل (مثل mkv)
    # سيقوم المعالج اللاحق بتحويله إلى mp4
    'outtmpl': 'downloads/%(id)s.%(ext)s',
    'noplaylist': True,
    'max_filesize': 2000 * 1024 * 1024,
    # سلسلة معالجات لاحقة مرنة
    'postprocessors': [{
        'key': 'FFmpegVideoConvertor',
        'toformat': 'mp4',
    }],
}

YDL_OPTS_AUDIO = {
    'format': 'bestaudio/best',
    'outtmpl': 'downloads/%(id)s.%(ext)s',
    'noplaylist': True,
    'max_filesize': 2000 * 1024 * 1024, # الحد الأقصى لحجم الصوت
    # إعادة تفعيل المعالجة اللاحقة لتحويل الصوت إلى صيغة m4a القياسية
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'm4a',
    }]
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

def generate_progress_bar(percentage: float) -> str:
    """ينشئ شريط تقدم نصي."""
    filled_length = int(10 * percentage / 100)
    bar = '█' * filled_length + '░' * (10 - filled_length)
    return f"[{bar}] {percentage:.1f}%"


async def download_media(
    url: str, 
    media_type: str, 
    format_id: str, 
    status_message: Message, 
    context: ContextTypes.DEFAULT_TYPE
) -> tuple[str | None, str | None]:
    """
    يقوم بتحميل الفيديو أو الصوت من الرابط المحدد.
    يعيد مسار الملف المحمل ونوعه (video/audio) أو (None, None) في حالة الفشل.
    """
    last_update_time = 0

    def progress_hook(d):
        nonlocal last_update_time
        if d['status'] == 'downloading':
            current_time = asyncio.get_event_loop().time()
            # تحديث كل ثانيتين لتجنب أخطاء API Flood
            if current_time - last_update_time > 2:
                total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate')
                if total_bytes:
                    downloaded_bytes = d.get('downloaded_bytes', 0)
                    percentage = (downloaded_bytes / total_bytes) * 100
                    progress_bar = generate_progress_bar(percentage)
                    
                    # جدولة الكوروتين للتنفيذ في loop الأحداث
                    # هذا هو الأسلوب الصحيح لاستدعاء دالة async من دالة sync
                    asyncio.create_task(
                        status_message.edit_text(
                            f"⏳ جارٍ التحميل...\n{progress_bar}"
                        )
                    )
                    last_update_time = current_time

    if not os.path.exists('downloads'):
        os.makedirs('downloads')
    
    opts = YDL_OPTS_VIDEO.copy() if media_type == 'video' else YDL_OPTS_AUDIO.copy()
    if format_id:
        opts['format'] = format_id
    
    opts['progress_hooks'] = [progress_hook]

    # تشغيل yt-dlp في منفذ منفصل لتجنب حظر loop الأحداث
    loop = asyncio.get_running_loop()
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            # استخدام run_in_executor لتشغيل الكود المتزامن (blocking)
            info = await loop.run_in_executor(
                None, lambda: ydl.extract_info(url, download=True)
            )
        except Exception as e:
            logging.error(f"فشل تحميل {media_type}: {e}", exc_info=True)
            return None, None

    # بعد المعالجة (الدمج والتحويل)، قد يتغير امتداد الملف
    # المسار الأصلي الذي تم تنزيله
    original_filepath = ydl.prepare_filename(info)
    
    # المسار المتوقع بعد المعالجة (التحويل إلى mp4 أو m4a)
    base_filepath, _ = os.path.splitext(original_filepath)
    expected_ext = ".mp4" if media_type == 'video' else ".m4a"
    expected_filepath = base_filepath + expected_ext

    # استخدم المسار المتوقع إذا كان موجودًا (حدثت معالجة)، وإلا استخدم المسار الأصلي
    filepath = expected_filepath if os.path.exists(expected_filepath) else original_filepath
    logging.info(f"اكتمل التحميل: {filepath}")
    return filepath, media_type

def get_estimated_size(fmt: dict, duration: float | None) -> float | None:
    """
    يقدر حجم الصيغة بالبايت.
    يعتمد على filesize، ثم filesize_approx، ثم يحسبه من tbr و duration.
    """
    if not fmt:
        return None
    
    size = fmt.get('filesize') or fmt.get('filesize_approx')
    if not size and duration and fmt.get('tbr'):
        size = (fmt.get('tbr') * 1024 / 8) * duration
    
    return size if size and size > 0 else None

class UploadProgress:
    def __init__(self, file_path: str, status_message: Message):
        self._file_path = file_path
        self._status_message = status_message
        self._total_size = os.path.getsize(file_path)
        self._uploaded_size = 0
        self._last_update_time = 0
        self._last_percentage = -1

    async def __call__(self, current: int, total: int):
        """يتم استدعاؤها من قبل مكتبة تليجرام أثناء الرفع."""
        percentage = (current / total) * 100
        current_time = asyncio.get_event_loop().time()

        # تحديث كل ثانيتين أو إذا زادت النسبة 5%
        if current_time - self._last_update_time > 2 or percentage - self._last_percentage >= 5:
            try:
                progress_bar = generate_progress_bar(percentage)
                await self._status_message.edit_text(
                    f"⬆️ جارٍ الرفع...\n{progress_bar}"
                )
                self._last_update_time = current_time
                self._last_percentage = percentage
            except TelegramError as e:
                if "Message is not modified" not in str(e):
                    logger.warning(f"خطأ أثناء تحديث شريط تقدم الرفع: {e}")








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
        "أنا بوت تحميل الفيديوهات. أرسل لي أي رابط فيديو وسأقوم بتحميله وإرساله لك."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "<b>مرحباً بك في بوت تحميل الفيديوهات!</b>\n\n"
        "<b>كيفية الاستخدام:</b>\n"
        "فقط أرسل رابط الفيديو الذي تريد تحميله.\n\n"
        "<b>الأوامر المتاحة:</b>\n"
        "/start - بدء استخدام البوت\n"
        "/help - عرض هذه الرسالة\n\n"
        "<b>لوحة تحكم الأدمن (خاصة بالمسؤولين):</b>\n"
        "/admin - لفتح لوحة التحكم التفاعلية"
    )
    await update.message.reply_html(help_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # التحقق من وجود مستخدم مرتبط بالرسالة
    if not update.effective_user:
        logger.info("تم استلام رسالة بدون مستخدم (قد تكون من قناة أو مشرف مجهول)، سيتم تجاهلها.")
        return

    user_id = update.effective_user.id
    
    # إضافة المستخدم إلى قاعدة البيانات عند أول تفاعل
    add_user(user_id)
    
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
        duration = info.get('duration')

        # --- منطق جديد لتجميع خيارات التحميل ---
        keyboard = []
        available_formats = {} # لتخزين أفضل صيغة لكل دقة
        # البحث عن أفضل صيغة صوت M4A
        best_audio = next((f for f in sorted(info.get('formats', []), key=lambda x: x.get('filesize') or 0, reverse=True) 
                           if f.get('vcodec') == 'none' and f.get('ext') == 'm4a'), None)
        if best_audio:
            best_audio['filesize_approx'] = get_estimated_size(best_audio, duration)
            # التحقق من أن حجم الملف لا يتجاوز حد الرفع
            if not best_audio['filesize_approx'] or best_audio['filesize_approx'] <= BOT_API_UPLOAD_LIMIT:
                size_str = format_bytes(best_audio['filesize_approx'])
                keyboard.append([InlineKeyboardButton(f"🎵 صوت M4A ({size_str})", callback_data=f"download:audio:audio:{update.message.message_id}")])
                available_formats['audio'] = best_audio

        # --- منطق جديد ومرن للبحث عن صيغ الفيديو ---
        video_formats_by_height = {}
        for f in info.get('formats', []):
            # تجاهل الصيغ التي لا تحتوي على فيديو أو لا تحتوي على ارتفاع
            if f.get('vcodec') == 'none' or not f.get('height'):
                continue

            height = f['height']
            # إذا لم تكن هذه الدقة موجودة، أو إذا كانت الصيغة الحالية أفضل، قم بتحديثها
            # الأفضلية للصيغ المدمجة، ثم الأعلى bitrate
            is_better = (
                height not in video_formats_by_height or
                (f.get('acodec') != 'none' and video_formats_by_height[height].get('acodec') == 'none') or
                ((f.get('tbr') or 0) > (video_formats_by_height[height].get('tbr') or 0))
            )
            if is_better:
                video_formats_by_height[height] = f

        # فرز الدقات المتاحة من الأعلى إلى الأقل
        sorted_heights = sorted(video_formats_by_height.keys(), reverse=True)

        for height in sorted_heights:
            best_format = video_formats_by_height[height]
            
            # إذا كانت الصيغة فيديو فقط، قم بدمجها مع أفضل صوت
            if best_format.get('acodec') == 'none' and best_audio:
                video_size = get_estimated_size(best_format, duration) or 0
                audio_size = get_estimated_size(best_audio, duration) or 0
                total_size = video_size + audio_size
                best_format['filesize_approx'] = total_size if total_size > 0 else None
                best_format['format_id'] = f"{best_format['format_id']}+{best_audio['format_id']}" # يتم تحديثه بعد حساب الحجم
            else: # إذا كانت الصيغة مدمجة بالفعل
                best_format['filesize_approx'] = get_estimated_size(best_format, duration)

            # التحقق من أن حجم الملف لا يتجاوز حد الرفع
            if not best_format['filesize_approx'] or best_format['filesize_approx'] <= BOT_API_UPLOAD_LIMIT:
                size_str = format_bytes(best_format['filesize_approx'])
                keyboard.append([InlineKeyboardButton(f"🎬 فيديو {height}p ({size_str})", callback_data=f"download:video:{height}:{update.message.message_id}")])
                available_formats[height] = best_format

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
    parts = data.split(":", 3) # download:video:720:12345
    action, media_type, format_key, original_message_id_str = parts if len(parts) == 4 else (parts[0], None, None, parts[1])
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

        # استرجاع الصيغة المطلوبة من chat_data باستخدام format_key
        try:
            # format_key هو 'audio' أو رقم الدقة مثل '720'
            selected_format = media_info['formats'][int(format_key) if format_key.isdigit() else format_key]
            format_id = selected_format['format_id']
            file_size = selected_format.get('filesize') or selected_format.get('filesize_approx')
        except (KeyError, TypeError):
            await query.edit_message_text(text="❌ حدث خطأ أثناء استرجاع معلومات الصيغة. قد تكون الرسالة قديمة جداً.")
            return

        await query.edit_message_text(text=f"⏳ جارٍ تحميل الـ {media_type}، يرجى الانتظار...")
        
        # الخطأ كان هنا: يجب تمرير query.message و context
        filepath, downloaded_type = await download_media(download_url, media_type, format_id, query.message, context)
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
ADMIN_PANEL, AWAITING_BROADCAST, AWAITING_CHANNEL_ID = range(3)

async def admin_panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """نقطة الدخول لمحادثة الأدمن."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats")],
        [InlineKeyboardButton("📢 إذاعة", callback_data="admin_broadcast")],
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
                CallbackQueryHandler(lambda u, c: admin_request_input(u, c, "أرسل الآن معرف القناة (مثال: @username)...", AWAITING_CHANNEL_ID), pattern="^admin_setchannel$"),
                CallbackQueryHandler(admin_del_channel, pattern="^admin_delchannel$"),
                CallbackQueryHandler(admin_close_panel, pattern="^admin_close$"),
                CallbackQueryHandler(admin_panel_command, pattern="^admin_back_to_panel$"),
            ],
            AWAITING_BROADCAST: [
                MessageHandler(filters.ALL & ~filters.COMMAND, handle_broadcast),
                CallbackQueryHandler(admin_panel_command, pattern="^admin_back_to_panel$"),
            ],
            AWAITING_CHANNEL_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_set_channel),
                CallbackQueryHandler(admin_panel_command, pattern="^admin_back_to_panel$"),
            ],
        },
        fallbacks=[
            CommandHandler("admin", admin_panel_command), # للسماح بإعادة تشغيل اللوحة
            CommandHandler("cancel", admin_cancel),
            CommandHandler("start", start_command) # للسماح بالخروج من وضع الأدمن
        ],
        per_message=False, # مهم لجعل أزرار الرجوع تعمل بشكل صحيح
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
    # --- إعداد ملف الكوكيز ---
    # يقرأ بيانات الكوكيز من متغيرات البيئة وينشئ ملفًا مؤقتًا ليستخدمه yt-dlp
    instagram_cookie_data = os.getenv("INSTAGRAM_COOKIES")
    if instagram_cookie_data:
        with open("instagram_cookies.txt", "w") as f:
            f.write(instagram_cookie_data)
        logger.info("تم إنشاء ملف كوكيز انستغرام بنجاح.")
    main()
