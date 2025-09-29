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
    'format': 'bestvideo+bestaudio/best',
    'outtmpl': 'downloads/%(title).100s-%(id)s.%(ext)s',
    'noplaylist': True,
    'merge_output_format': 'mp4',
    'postprocessors': [{
        'key': 'FFmpegVideoConvertor',
        'preferedformat': 'mp4',
    }],
    'restrictfilenames': True,
    'nooverwrites': True,
    'noprogress': True,
    'quiet': True,
    # إعدادات لدعم المزيد من المواقع
    'extract_flat': False,
    'ignoreerrors': False,
    'no_warnings': False,
    'verbose': False,
    # إعدادات الشبكة
    'socket_timeout': 30,
    'retries': 3,
    'fragment_retries': 3,
    'skip_unavailable_fragments': True,
    # دعم المواقع المختلفة
    'compat_opts': ['no-youtube-unavailable'],
    'extractor_args': {
        'youtube': {
            'player_client': ['android', 'web'],
        },
    },
}

YDL_OPTS_AUDIO = {
    'format': 'bestaudio/best',
    'outtmpl': 'downloads/%(title).100s-%(id)s.%(ext)s',
    'noplaylist': True,
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'm4a',
        'preferredquality': '192',
    }],
    'restrictfilenames': True,
    'nooverwrites': True,
    'noprogress': True,
    'quiet': True,
    # نفس الإعدادات الإضافية
    'extract_flat': False,
    'ignoreerrors': False,
    'no_warnings': False,
    'verbose': False,
    'socket_timeout': 30,
    'retries': 3,
    'fragment_retries': 3,
    'skip_unavailable_fragments': True,
}

def format_duration(seconds: float) -> str:
    """يحول المدة من ثوانٍ إلى تنسيق مقروء (س:د:ث)."""
    if not seconds:
        return "غير معروف"
    
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes}:{secs:02d}"
def _is_better_format(new_format: dict, current_format: dict) -> bool:
    """يقارن بين صيغتين ويحدد أيهما أفضل."""
    # الأفضلية للصيغ المدمجة (فيديو+صوت)
    if (new_format.get('acodec') != 'none' and 
        current_format.get('acodec') == 'none'):
        return True
    
    # ثم الأفضلية لأعلى معدل بت
    new_tbr = new_format.get('tbr', 0) or 0
    current_tbr = current_format.get('tbr', 0) or 0
    
    return new_tbr > current_tbr
def format_bytes(size):
    """يحول البايت إلى صيغة مقروءة (KB, MB, GB) بدقة."""
    if size is None or size <= 0:
        return "غير معروف"
    
    power = 1024
    power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB'}
    
    n = 0
    while size >= power and n < len(power_labels) - 1:
        size /= power
        n += 1
    
    if n == 0:  # بايت
        return f"{size:.0f} {power_labels[n]}"
    elif size < 10:  # أرقام صغيرة
        return f"{size:.2f} {power_labels[n]}"
    elif size < 100:  # أرقام متوسطة
        return f"{size:.1f} {power_labels[n]}"
    else:  # أرقام كبيرة
        return f"{size:.0f} {power_labels[n]}"

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
    يدعم جميع المواقع المتاحة في yt-dlp.
    """
    
    if not os.path.exists('downloads'):
        os.makedirs('downloads')
    
    # استخدام الإعدادات المناسبة بناءً على نوع الوسائط
    if media_type == 'video':
        opts = YDL_OPTS_VIDEO.copy()
    else:
        opts = YDL_OPTS_AUDIO.copy()
    
    # إضافة format_id إذا كان موجوداً
    if format_id and format_id != 'audio':
        opts['format'] = format_id
    
    # إعدادات إضافية لدعم المواقع المختلفة
    opts.update({
        'extract_flat': False,  # تأكد من تحميل المعلومات الكاملة
        'ignoreerrors': False,  # عرض الأخطاء للمساعدة في التشخيص
    })

    try:
        await status_message.edit_text("⏳ جارٍ التحميل... يرجى الانتظار")
        
        # تشغيل yt-dlp في منفذ منفصل
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = await asyncio.get_event_loop().run_in_executor(
                None, 
                lambda: ydl.extract_info(url, download=True)
            )
            
        await status_message.edit_text("✅ اكتمل التحميل، جارٍ الرفع...")
        
    except Exception as e:
        logging.error(f"فشل تحميل {media_type} من {url}: {e}", exc_info=True)
        
        # رسائل خطأ أكثر وضوحاً حسب نوع الموقع
        error_msg = f"❌ فشل التحميل من الرابط"
        if "Unsupported URL" in str(e):
            error_msg += "\n⚠️ الرابط غير مدعوم أو الموقع غير متاح"
        elif "Private video" in str(e):
            error_msg += "\n🔒 الفيديو خاص أو محمي بكلمة مرور"
        elif "Geo restricted" in str(e):
            error_msg += "\n🌍 المحتوى غير متاح في منطقتك الجغرافية"
        elif "Sign in" in str(e):
            error_msg += "\n🔐 يتطلب تسجيل الدخول أو الاشتراك"
        else:
            error_msg += f"\n📋 الخطأ: {str(e)}"
            
        await status_message.edit_text(error_msg)
        return None, None

    # البحث عن الملف الذي تم تحميله
    try:
        # الحصول على اسم الملف المتوقع
        expected_filename = ydl.prepare_filename(info)
        expected_path = os.path.join('downloads', os.path.basename(expected_filename))
        
        # التحقق من وجود الملف بالامتداد المتوقع أولاً
        if os.path.exists(expected_path):
            return expected_path, media_type
        
        # إذا لم يوجد، ابحث عن أي ملف في مجلد التنزيلات
        video_id = info.get('id', '') or 'unknown'
        for filename in os.listdir('downloads'):
            if video_id in filename or 'unknown' in filename:
                filepath = os.path.join('downloads', filename)
                if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                    logging.info(f"تم العثور على الملف: {filepath}")
                    return filepath, media_type
        
        # محاولة أخيرة: احصل على أحدث ملف في المجلد
        download_files = [f for f in os.listdir('downloads') 
                         if os.path.isfile(os.path.join('downloads', f))]
        if download_files:
            latest_file = max([os.path.join('downloads', f) for f in download_files], 
                            key=os.path.getmtime)
            if os.path.exists(latest_file) and os.path.getsize(latest_file) > 0:
                logging.info(f"تم العثور على أحدث ملف: {latest_file}")
                return latest_file, media_type
        
        logging.error("لم يتم العثور على أي ملفات محملة")
        return None, None
        
    except Exception as e:
        logging.error(f"خطأ في العثور على الملف المحمل: {e}")
        return None, None
    
def get_estimated_size(fmt: dict, duration: float | None) -> float | None:
    """
    يقدر حجم الصيغة بالبايت بدقة أكبر.
    """
    if not fmt:
        return None
    
    # 1. الأولوية: filesize المباشر
    size = fmt.get('filesize')
    if size and size > 0:
        return size
    
    # 2. filesize_approx
    size = fmt.get('filesize_approx')
    if size and size > 0:
        return size
    
    # 3. الحساب من tbr و duration (الأكثر دقة)
    if duration and fmt.get('tbr'):
        # tbr هو بالكيلوبت في الثانية، نحتاج للبايت في الثانية
        # tbr (kbps) → bytes = (tbr * 1000 / 8) * duration
        tbr = fmt.get('tbr', 0)
        if tbr > 0:
            size = (tbr * 1000 / 8) * duration
            return size
    
    # 4. إذا كانت الصيغة مدمجة (فيديو+صوت)، حاول حساب حجم الصوت أيضاً
    if fmt.get('vcodec') != 'none' and fmt.get('acodec') != 'none':
        # تقدير حجم الصوت إذا كان هناك فيديو
        audio_bitrate = fmt.get('abr', 0) or 128  # معدل صوت افتراضي 128 kbps
        if duration and audio_bitrate > 0:
            audio_size = (audio_bitrate * 1000 / 8) * duration
            video_size = get_estimated_size(fmt, duration) or 0
            return video_size + audio_size
    
    return None

class UploadProgress:
    def __init__(self, file_path: str, status_message: Message):
        self._file_path = file_path
        self._status_message = status_message
        self._total_size = os.path.getsize(file_path)
        self._last_update_time = 0
        self._last_percentage = -1

    async def update_progress(self, current: int, total: int):
        """يتم استدعاؤها من قبل مكتبة تليجرام أثناء الرفع."""
        percentage = (current / total) * 100
        current_time = asyncio.get_event_loop().time()

        # تحديث كل 3 ثوان أو إذا زادت النسبة 10%
        if current_time - self._last_update_time > 3 or percentage - self._last_percentage >= 10:
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
            # إعدادات أكثر مرونة لجلب المعلومات
            info_opts = {
                'noplaylist': True,
                'ignoreerrors': False,
                'no_warnings': False,
                'extract_flat': False,  # جلب المعلومات الكاملة
            }
            
            # جلب المعلومات فقط بدون تحميل
            with yt_dlp.YoutubeDL(info_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
            # إذا كان الرابط لقائمة تشغيل، خذ أول فيديو
            if '_type' in info and info['_type'] == 'playlist':
                if info['entries']:
                    info = info['entries'][0]
                else:
                    await status_message.edit_text("❌ قائمة التشغيل فارغة")
                    return

            duration = info.get('duration')

            # --- منطق جديد دقيق لحساب الأحجام ---
            keyboard = []
            available_formats = {} # لتخزين أفضل صيغة لكل دقة
            
            # البحث عن أفضل صيغة صوت M4A
            best_audio = None
            audio_formats = [f for f in info.get('formats', []) 
                            if f.get('vcodec') == 'none' and f.get('acodec') != 'none']
            
            if audio_formats:
                # اختيار أفضل صيغة صوت (أعلى جودة)
                best_audio = max(audio_formats, 
                            key=lambda x: x.get('abr', 0) or x.get('tbr', 0) or 0)
                
                # حساب الحجم بدقة
                audio_size = get_estimated_size(best_audio, duration)
                if not audio_size or audio_size <= BOT_API_UPLOAD_LIMIT:
                    size_str = format_bytes(audio_size)
                    keyboard.append([InlineKeyboardButton(f"🎵 صوت M4A ({size_str})", callback_data=f"download:audio:audio:{update.message.message_id}")])
                    available_formats['audio'] = best_audio

            # --- منطق دقيق للفيديو ---
            video_formats_by_height = {}
            
            for f in info.get('formats', []):
                # تجاهل الصيغ التي لا تحتوي على فيديو
                if f.get('vcodec') == 'none' or not f.get('height'):
                    continue

                height = f['height']
                current_format = video_formats_by_height.get(height)
                
                # اختيار أفضل صيغة لكل دقة
                if not current_format or _is_better_format(f, current_format):
                    video_formats_by_height[height] = f

            # فرز الدقات المتاحة من الأعلى إلى الأقل
            sorted_heights = sorted(video_formats_by_height.keys(), reverse=True)

            for height in sorted_heights:
                best_format = video_formats_by_height[height]
                
                # حساب الحجم الإجمالي (فيديو + صوت إذا لزم الأمر)
                total_size = 0
                
                if best_format.get('acodec') == 'none' and best_audio:
                    # صيغة فيديو فقط، نضيف حجم الصوت
                    video_size = get_estimated_size(best_format, duration) or 0
                    audio_size = get_estimated_size(best_audio, duration) or 0
                    total_size = video_size + audio_size
                    best_format['combined_format'] = f"{best_format['format_id']}+{best_audio['format_id']}"
                else:
                    # صيغة مدمجة (فيديو+صوت)
                    total_size = get_estimated_size(best_format, duration) or 0
                
                # التحقق من أن حجم الملف لا يتجاوز حد الرفع
                if total_size > 0 and total_size <= BOT_API_UPLOAD_LIMIT:
                    size_str = format_bytes(total_size)
                    # تخزين الحجم المحسوب للاستخدام لاحقاً
                    best_format['calculated_size'] = total_size
                    keyboard.append([InlineKeyboardButton(f"🎬 فيديو {height}p ({size_str})", callback_data=f"download:video:{height}:{update.message.message_id}")])
                    available_formats[height] = best_format

            if not keyboard:
                await status_message.edit_text("❌ عذراً، لم يتم العثور على صيغ تحميل مدعومة لهذا الرابط.")
                return

            # تخزين معلومات الصيغ المتاحة في chat_data
            original_message_id = update.message.message_id
            context.chat_data[original_message_id] = {
                'url': url, 
                'formats': available_formats,
                'duration': duration,
                'best_audio': best_audio
            }

            # إضافة زر الإلغاء
            keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:{original_message_id}")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            title = info.get('title', 'فيديو')
            duration_str = format_duration(duration) if duration else "غير معروف"
            
            await status_message.edit_text(
                f"<b>{title}</b>\n⏱️ المدة: {duration_str}\n\nاختر الصيغة التي تريد تحميلها:", 
                reply_markup=reply_markup, 
                parse_mode=ParseMode.HTML
            )

    except Exception as e:
        logging.error(f"فشل في جلب معلومات الفيديو من {url}: {e}")
        
        # رسائل خطأ محددة
        if "Unsupported URL" in str(e):
            error_msg = "❌ هذا الرابط غير مدعوم حالياً"
        elif "No video formats found" in str(e):
            error_msg = "❌ لم يتم العثور على صيغ فيديو متاحة"
        elif "Private video" in str(e):
            error_msg = "❌ الفيديو خاص أو محمي"
        elif "Sign in" in str(e):
            error_msg = "❌ يتطلب تسجيل الدخول أو الاشتراك"
        else:
            error_msg = f"❌ حدث خطأ أثناء جلب المعلومات: {str(e)}"
            
        await status_message.edit_text(error_msg)
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    # استعادة البيانات: action, media_type, format_id, original_message_id
    parts = data.split(":")
    
    if len(parts) < 2:
        await query.edit_message_text(text="❌ بيانات غير صالحة.")
        return
        
    action = parts[0]
    
    if action == "cancel":
        original_message_id = int(parts[1])
        await query.message.delete()
        context.chat_data.pop(original_message_id, None)
        return

    if action == "download" and len(parts) == 4:
        media_type = parts[1]
        format_key = parts[2]
        original_message_id = int(parts[3])

        # استرجاع بيانات الرابط والصيغ من chat_data
        media_info = context.chat_data.get(original_message_id)
        if not media_info:
            await query.edit_message_text(text="❌ حدث خطأ. ربما تكون هذه الرسالة قديمة جداً. الرجاء إرسال الرابط مرة أخرى.")
            return

        download_url = media_info.get('url')
        user_id = query.from_user.id

        # استرجاع الصيغة المطلوبة من chat_data
        try:
            # format_key هو 'audio' أو رقم الدقة مثل '720'
            selected_format = media_info['formats'].get(format_key)
            if not selected_format:
                # محاولة البحث بالمفتاح الرقمي
                try:
                    selected_format = media_info['formats'].get(int(format_key))
                except ValueError:
                    pass
            
            if not selected_format:
                await query.edit_message_text(text="❌ لم يتم العثور على الصيغة المطلوبة.")
                return
                
            # استخدام format_id المحسوب مسبقاً إن وجد
            if 'combined_format' in selected_format:
                format_id = selected_format['combined_format']
            else:
                format_id = selected_format.get('format_id', '')
            
            # استخدام الحجم المحسوب مسبقاً
            file_size = selected_format.get('calculated_size')
            
        except (KeyError, TypeError, ValueError) as e:
            logging.error(f"خطأ في استرجاع الصيغة: {e}")
            await query.edit_message_text(text="❌ حدث خطأ أثناء استرجاع معلومات الصيغة.")
            return

        await query.edit_message_text(text=f"⏳ جارٍ تحميل الـ {media_type}، يرجى الانتظار...")
        
        # تحميل الوسائط
        filepath, downloaded_type = await download_media(
            download_url, 
            media_type, 
            format_id, 
            query.message, 
            context
        )
        
        if not filepath:
            await query.edit_message_text(text=f"❌ فشل تحميل الـ {media_type}. حاول مجدداً أو جرب رابطاً آخر.")
            return

        try:
            await query.edit_message_text(text=f"⬆️ جارٍ رفع الـ {downloaded_type}...")
            
            # إرسال الملف بدون معامل progress
            with open(filepath, 'rb') as file:
                if downloaded_type == 'video':
                    await context.bot.send_video(
                        chat_id=query.message.chat_id, 
                        video=file, 
                        caption=f"تم التحميل بواسطة @{context.bot.username}", 
                        supports_streaming=True,
                        read_timeout=60,
                        write_timeout=60
                    )
                elif downloaded_type == 'audio':
                    await context.bot.send_audio(
                        chat_id=query.message.chat_id, 
                        audio=file, 
                        caption=f"تم التحميل بواسطة @{context.bot.username}",
                        read_timeout=60,
                        write_timeout=60
                    )
            
            await query.message.delete()
            
        except TelegramError as e:
            logger.error(f"فشل رفع الملف: {e}")
            await query.edit_message_text(text=f"❌ فشل رفع الملف إلى تليجرام.\n\nالخطأ: {str(e)}")
        except Exception as e:
            logger.error(f"خطأ غير متوقع: {e}")
            await query.edit_message_text(text=f"❌ حدث خطأ غير متوقع: {str(e)}")
        finally:
            # تنظيف الملفات المؤقتة
            if filepath and os.path.exists(filepath):
                try:
                    os.remove(filepath)
                    logger.info(f"تم حذف الملف المؤقت: {filepath}")
                except Exception as e:
                    logger.error(f"خطأ في حذف الملف المؤقت: {e}")
            
            # تنظيف chat_data
            context.chat_data.pop(original_message_id, None)

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
