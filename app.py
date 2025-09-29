import asyncio
import logging
import os
import sqlite3
import yt_dlp
import dotenv
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Message
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler, ConversationHandler
from telegram.constants import ParseMode
from telegram.error import TelegramError

# ==============================================================================
# ١. الإعدادات (بديل لـ config.py)
# ==============================================================================
dotenv.load_dotenv()
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

def setup_cookies():
    """
    يقوم بإعداد ملف الكوكيز من متغيرات البيئة أو ينشئ ملفاً فارغاً.
    """
    try:
        # قراءة بيانات الكوكيز من متغيرات البيئة
        instagram_cookies = os.getenv("INSTAGRAM_COOKIES")
        youtube_cookies = os.getenv("YOUTUBE_COOKIES")
        
        cookies_content = []
        
        if youtube_cookies:
            cookies_content.append("# YouTube Cookies")
            cookies_content.append(youtube_cookies)
            cookies_content.append("")
        
        if instagram_cookies:
            cookies_content.append("# Instagram Cookies")
            cookies_content.append(instagram_cookies)
            cookies_content.append("")
        
        if cookies_content:
            with open("cookies.txt", "w", encoding="utf-8") as f:
                f.write("\n".join(cookies_content))
            logger.info("✅ تم إنشاء ملف الكوكيز بنجاح")
            return True
        else:
            # إنشاء ملف كوكيز فارغ إذا لم توجد بيانات
            open("cookies.txt", "a").close()
            logger.info("⚠️ لم يتم العثور على بيانات الكوكيز، سيتم استخدام ملف فارغ")
            return False
            
    except Exception as e:
        logger.error(f"❌ فشل في إعداد ملف الكوكيز: {e}")
        # إنشاء ملف فارغ كبديل
        try:
            open("cookies.txt", "a").close()
            return False
        except:
            return False

def get_ydl_opts(media_type='video'):
    """
    إرجاع إعدادات yt-dlp مع التعامل الصحيح مع ملف الكوكيز.
    """
    base_opts = {
        'outtmpl': 'downloads/%(title).100s-%(id)s.%(ext)s',
        'noplaylist': True,
        'restrictfilenames': True,
        'nooverwrites': True,
        'noprogress': True,
        'quiet': True,
        'ignoreerrors': True,
        'no_warnings': False,
        'socket_timeout': 60,
        'retries': 10,
        'fragment_retries': 10,
        'skip_unavailable_fragments': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Accept-Encoding': 'gzip,deflate',
            'Accept-Charset': 'ISO-8859-1,utf-8;q=0.7,*;q=0.7',
        },
    }
    
    # التحقق من وجود ملف الكوكيز وإضافته إذا كان موجوداً
    if os.path.exists('cookies.txt') and os.path.getsize('cookies.txt') > 0:
        base_opts['cookiefile'] = 'cookies.txt'
        logger.info("✅ استخدام ملف الكوكيز المتاح")
    else:
        logger.info("⚠️ لا يوجد ملف كوكيز، سيتم التحميل بدون كوكيز")
    
    if media_type == 'video':
        base_opts.update({
            'format': 'bestvideo+bestaudio/best',
            'merge_output_format': 'mp4',
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
        })
    else:  # audio
        base_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'm4a',
                'preferredquality': '192',
            }],
        })
    
    return base_opts

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
    new_has_audio = new_format.get('acodec') != 'none'
    current_has_audio = current_format.get('acodec') != 'none'
    
    if new_has_audio and not current_has_audio:
        return True
    elif not new_has_audio and current_has_audio:
        return False
    
    # إذا كانتا مدمجتين أو غير مدمجتين، قارن بمعدل البت
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

def get_supported_domains():
    """قائمة بالمنصات المدعومة."""
    return [
        'youtube.com', 'youtu.be',  # يوتيوب
        'instagram.com', 'instagr.am',  # انستغرام
        'tiktok.com', 'vm.tiktok.com',  # تيك توك
        'twitter.com', 'x.com',  # تويتر
        'facebook.com', 'fb.watch',  # فيسبوك
        'reddit.com',  # ريديت
        'twitch.tv',  # تويش
        'vimeo.com',  # فيمو
        'dailymotion.com',  # ديلي موشن
        'soundcloud.com',  # ساوند كلاود
        'pinterest.com',  # بينتيريست
        'likee.video',  # لايكي
        'ok.ru',  # أودنوكلاسنيكي
        'bilibili.com',  # بيلبيل
        'rutube.ru',  # روتيوب
        'linkedin.com',  # لينكدإن
        'snapchat.com',  # سناب شات
    ]

def is_supported_url(url: str) -> bool:
    """يتحقق مما إذا كان الرابط مدعومًا."""
    import re
    domains = '|'.join(re.escape(domain) for domain in get_supported_domains())
    pattern = f'https?://(?:[^/]+\\.)?(?:{domains})/\\S+'
    return bool(re.match(pattern, url))

async def download_media(
    url: str, 
    media_type: str, 
    format_id: str, 
    status_message: Message, 
    context: ContextTypes.DEFAULT_TYPE
) -> tuple[str | None, str | None]:
    """
    يقوم بتحميل الفيديو أو الصوت من الرابط المحدد.
    """
    
    if not os.path.exists('downloads'):
        os.makedirs('downloads')
    
    # استخدام الإعدادات المناسبة
    opts = get_ydl_opts(media_type)
    
    # إضافة format_id إذا كان موجوداً
    if format_id and format_id != 'audio':
        opts['format'] = format_id

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
        
        # رسائل خطأ أكثر وضوحاً
        error_msg = f"❌ فشل التحميل من الرابط"
        if "Unsupported URL" in str(e):
            error_msg += "\n⚠️ الرابط غير مدعوم أو الموقع غير متاح"
        elif "Private video" in str(e):
            error_msg += "\n🔒 الفيديو خاص أو محمي بكلمة مرور"
        elif "Geo restricted" in str(e):
            error_msg += "\n🌍 المحتوى غير متاح في منطقتك الجغرافية"
        elif "Sign in" in str(e):
            error_msg += "\n🔐 يتطلب تسجيل الدخول أو الاشتراك"
        elif "Video unavailable" in str(e):
            error_msg += "\n🚫 الفيديو غير متاح أو تم حذفه"
        elif "This video is not available" in str(e):
            error_msg += "\n🚫 هذا الفيديو غير متاح في منطقتك"
        elif "failed to load cookies" in str(e):
            error_msg += "\n🍪 مشكلة في ملف الكوكيز، جارٍ التحميل بدون كوكيز"
            # إعادة المحاولة بدون كوكيز
            return await download_without_cookies(url, media_type, format_id, status_message, context)
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
            if video_id in filename:
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

async def download_without_cookies(
    url: str, 
    media_type: str, 
    format_id: str, 
    status_message: Message, 
    context: ContextTypes.DEFAULT_TYPE
) -> tuple[str | None, str | None]:
    """
    يحاول التحميل بدون استخدام ملف الكوكيز.
    """
    try:
        await status_message.edit_text("🔄 جارٍ المحاولة بدون كوكيز...")
        
        opts = get_ydl_opts(media_type)
        # إزالة ملف الكوكيز من الإعدادات
        if 'cookiefile' in opts:
            del opts['cookiefile']
        
        # إضافة format_id إذا كان موجوداً
        if format_id and format_id != 'audio':
            opts['format'] = format_id

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = await asyncio.get_event_loop().run_in_executor(
                None, 
                lambda: ydl.extract_info(url, download=True)
            )
            
        await status_message.edit_text("✅ اكتمل التحميل بدون كوكيز، جارٍ الرفع...")
        
        # البحث عن الملف المحمل
        expected_filename = ydl.prepare_filename(info)
        expected_path = os.path.join('downloads', os.path.basename(expected_filename))
        
        if os.path.exists(expected_path):
            return expected_path, media_type
        
        return None, None
        
    except Exception as e:
        logging.error(f"فشل التحميل بدون كوكيز: {e}")
        await status_message.edit_text("❌ فشل التحميل حتى بدون كوكيز. يرجى المحاولة لاحقاً.")
        return None, None
    
def get_estimated_size(fmt: dict, duration: float | None) -> float | None:
    """
    إصدار مبسط لحساب الحجم بدون تعقيدات.
    """
    if not fmt:
        return None
    
    # المحاولة الأولى: filesize المباشر
    size = fmt.get('filesize')
    if size and size > 0:
        return size
    
    # المحاولة الثانية: filesize_approx
    size = fmt.get('filesize_approx')
    if size and size > 0:
        return size
    
    # المحاولة الثالثة: الحساب من tbr
    if duration and fmt.get('tbr'):
        tbr = fmt.get('tbr', 0)
        if tbr > 0:
            # tbr (kbps) → bytes = (tbr * 1000 / 8) * duration
            return (tbr * 1000 / 8) * duration
    
    # المحاولة الرابعة: إذا كان هناك معدل بت للصوت فقط
    if duration and fmt.get('vcodec') == 'none' and fmt.get('abr'):
        abr = fmt.get('abr', 0)
        if abr > 0:
            return (abr * 1000 / 8) * duration
    
    # إذا فشلت جميع المحاولات
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
# ٤. منطق البوت الرئيسي
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
    
    # رسالة ترحيب محسنة مع قائمة المنصات المدعومة
    supported_platforms = "\n".join([f"• {domain}" for domain in get_supported_domains()[:10]])  # عرض أول 10 فقط
    
    await update.message.reply_html(
        f"أهلاً بك يا {user.mention_html()}!\n\n"
        "أنا بوت تحميل الفيديوهات من مختلف المنصات الاجتماعية.\n\n"
        "<b>أهم المنصات المدعومة:</b>\n"
        f"{supported_platforms}\n\n"
        "<b>طريقة الاستخدام:</b>\n"
        "فقط أرسل رابط الفيديو الذي تريد تحميله وسأقوم بتحميله وإرساله لك.\n\n"
        "استخدم /supported لعرض جميع المنصات المدعومة."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    supported_platforms = "\n".join([f"• {domain}" for domain in get_supported_domains()[:10]])
    
    help_text = (
        "<b>مرحباً بك في بوت تحميل الفيديوهات!</b>\n\n"
        "<b>أهم المنصات المدعومة:</b>\n"
        f"{supported_platforms}\n\n"
        "<b>كيفية الاستخدام:</b>\n"
        "فقط أرسل رابط الفيديو الذي تريد تحميله.\n\n"
        "<b>الأوامر المتاحة:</b>\n"
        "/start - بدء استخدام البوت\n"
        "/help - عرض هذه الرسالة\n"
        "/supported - عرض جميع المنصات المدعومة\n\n"
        "<b>ملاحظة:</b>\n"
        "بعض الفيديوهات قد تتطلب وقتاً أطول للتحميل حسب الموقع وحجم الفيديو."
    )
    await update.message.reply_html(help_text)

async def supported_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يعرض قائمة بالمنصات المدعومة."""
    supported_platforms = "\n".join([f"• {domain}" for domain in get_supported_domains()])
    
    await update.message.reply_text(
        f"<b>جميع المنصات المدعومة:</b>\n\n{supported_platforms}\n\n"
        "يمكنك إرسال رابط من أي من هذه المنصات وسأقوم بتحميل المحتوى لك.",
        parse_mode=ParseMode.HTML
    )

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
    
    # التحقق من أن النص هو رابط
    if not (url.startswith('http://') or url.startswith('https://')):
        await update.message.reply_text("الرجاء إرسال رابط صالح.")
        return
    
    # التحقق من أن الرابط مدعوم
    if not is_supported_url(url):
        await update.message.reply_text(
            "❌ هذا الرابط غير مدعوم حالياً.\n\n"
            "استخدم /supported لعرض قائمة المنصات المدعومة."
        )
        return

    status_message = await update.message.reply_text("⏳ جارٍ جلب معلومات الفيديو...")

    try:
        # استخدام إعدادات موحدة لجلب المعلومات
        info_opts = get_ydl_opts('video')
        info_opts.update({
            'extract_flat': False,
            'ignoreerrors': True,
        })

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

        # --- منطق دقيق لحساب الأحجام ---
        keyboard = []
        available_formats = {}
        
        # البحث عن أفضل صيغة صوت
        best_audio = None
        audio_formats = [f for f in info.get('formats', []) 
                        if f.get('vcodec') == 'none' and f.get('acodec') != 'none']
        
        if audio_formats:
            best_audio = max(audio_formats, 
                        key=lambda x: x.get('abr', 0) or x.get('tbr', 0) or 0)
            
            audio_size = get_estimated_size(best_audio, duration)
            size_str = format_bytes(audio_size)
            
            if audio_size and audio_size > BOT_API_UPLOAD_LIMIT:
                keyboard.append([InlineKeyboardButton(f"🎵 صوت M4A ({size_str}) - حجم كبير جداً", callback_data="noop")])
            else:
                keyboard.append([InlineKeyboardButton(f"🎵 صوت M4A ({size_str})", callback_data=f"download:audio:audio:{update.message.message_id}")])
                available_formats['audio'] = best_audio

        # --- منطق دقيق للفيديو ---
        video_formats_by_height = {}
        
        for f in info.get('formats', []):
            if f.get('vcodec') == 'none' or not f.get('height'):
                continue

            height = f['height']
            current_format = video_formats_by_height.get(height)
            
            if not current_format or _is_better_format(f, current_format):
                video_formats_by_height[height] = f

        # فرز الدقات المتاحة من الأعلى إلى الأقل
        sorted_heights = sorted(video_formats_by_height.keys(), reverse=True)

        for height in sorted_heights:
            best_format = video_formats_by_height[height]
            
            total_size = 0
            
            if best_format.get('acodec') == 'none' and best_audio:
                video_size = get_estimated_size(best_format, duration) or 0
                audio_size = get_estimated_size(best_audio, duration) or 0
                total_size = video_size + audio_size
                best_format['combined_format'] = f"{best_format['format_id']}+{best_audio['format_id']}"
            else:
                total_size = get_estimated_size(best_format, duration) or 0
            
            size_str = format_bytes(total_size)
            
            if total_size > 0 and total_size > BOT_API_UPLOAD_LIMIT:
                keyboard.append([InlineKeyboardButton(f"🎬 فيديو {height}p ({size_str}) - حجم كبير جداً", callback_data="noop")])
            elif total_size > 0:
                best_format['calculated_size'] = total_size
                keyboard.append([InlineKeyboardButton(f"🎬 فيديو {height}p ({size_str})", callback_data=f"download:video:{height}:{update.message.message_id}")])
                available_formats[height] = best_format

        if not keyboard:
            await status_message.edit_text("❌ عذراً، لم يتم العثور على صيغ تحميل مدعومة لهذا الرابط.")
            return

        # تخزين المعلومات في chat_data
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
        
        error_msg = "❌ حدث خطأ أثناء جلب المعلومات"
        if "Unsupported URL" in str(e):
            error_msg = "❌ هذا الرابط غير مدعوم حالياً"
        elif "No video formats found" in str(e):
            error_msg = "❌ لم يتم العثور على صيغ فيديو متاحة"
        elif "Private video" in str(e):
            error_msg = "❌ الفيديو خاص أو محمي"
        elif "Sign in" in str(e):
            error_msg = "❌ يتطلب تسجيل الدخول أو الاشتراك"
        elif "failed to load cookies" in str(e):
            error_msg = "❌ مشكلة في الإعدادات، جارٍ المحاولة مرة أخرى..."
            # يمكن إضافة محاولة إضافية هنا إذا لزم الأمر
            
        await status_message.edit_text(error_msg)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.data == "noop":
        await query.answer("⚠️ هذا الخيار غير متاح لأن حجم الملف يتجاوز 50 ميجابايت.", show_alert=True)
        return
    await query.answer()

    data = query.data
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

        media_info = context.chat_data.get(original_message_id)
        if not media_info:
            await query.edit_message_text(text="❌ حدث خطأ. ربما تكون هذه الرسالة قديمة جداً. الرجاء إرسال الرابط مرة أخرى.")
            return

        download_url = media_info.get('url')
        user_id = query.from_user.id

        try:
            selected_format = media_info['formats'].get(format_key)
            if not selected_format:
                try:
                    selected_format = media_info['formats'].get(int(format_key))
                except ValueError:
                    pass
            
            if not selected_format:
                await query.edit_message_text(text="❌ لم يتم العثور على الصيغة المطلوبة.")
                return
                
            if 'combined_format' in selected_format:
                format_id = selected_format['combined_format']
            else:
                format_id = selected_format.get('format_id', '')
            
        except (KeyError, TypeError, ValueError) as e:
            logging.error(f"خطأ في معالجة الصيغة: {e}")
            await query.edit_message_text(text="❌ حدث خطأ في معالجة الصيغة المطلوبة.")
            return

        await query.edit_message_text(text="⏳ جارٍ التحميل... يرجى الانتظار")

        file_path, actual_media_type = await download_media(
            download_url, 
            media_type, 
            format_id, 
            query.message, 
            context
        )

        if not file_path:
            await query.edit_message_text(text="❌ فشل التحميل. الرجاء المحاولة مرة أخرى.")
            return

        if not os.path.exists(file_path):
            await query.edit_message_text(text="❌ لم يتم العثور على الملف المحمل.")
            return

        file_size = os.path.getsize(file_path)
        if file_size == 0:
            await query.edit_message_text(text="❌ الملف المحمل فارغ.")
            os.remove(file_path)
            return

        progress = UploadProgress(file_path, query.message)

        try:
            if actual_media_type == 'video':
                await context.bot.send_video(
                    chat_id=query.message.chat_id,
                    video=open(file_path, 'rb'),
                    supports_streaming=True,
                    read_timeout=60,
                    write_timeout=60,
                    connect_timeout=60,
                    progress=progress.update_progress,
                    progress_args=(file_size,)
                )
            else:
                await context.bot.send_audio(
                    chat_id=query.message.chat_id,
                    audio=open(file_path, 'rb'),
                    read_timeout=60,
                    write_timeout=60,
                    connect_timeout=60,
                    progress=progress.update_progress,
                    progress_args=(file_size,)
                )

            await query.edit_message_text(text="✅ تم الرفع بنجاح!")
            
        except TelegramError as e:
            error_message = f"❌ فشل الرفع: {str(e)}"
            if "File too large" in str(e):
                error_message += "\n\nالملف كبير جداً للرفع عبر تليجرام (الحد الأقصى 50 ميجابايت)."
            await query.edit_message_text(text=error_message)
        except Exception as e:
            await query.edit_message_text(text=f"❌ حدث خطأ غير متوقع: {str(e)}")
        finally:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception as e:
                logging.warning(f"لم أتمكن من حذف الملف {file_path}: {e}")

        context.chat_data.pop(original_message_id, None)

# ==============================================================================
# ٥. لوحة تحكم الأدمن
# ==============================================================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    يعرض لوحة تحكم الأدمن إذا كان المستخدم مسؤولاً.
    """
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ ليس لديك صلاحية الوصول إلى هذه الأداة.")
        return

    keyboard = [
        [InlineKeyboardButton("📊 إحصائيات البوت", callback_data="admin_stats")],
        [InlineKeyboardButton("📢 إرسال رسالة للمستخدمين", callback_data="admin_broadcast")],
        [InlineKeyboardButton("⚙️ إعدادات القناة الإجبارية", callback_data="admin_channel")],
        [InlineKeyboardButton("🔄 تحديث الكوكيز", callback_data="admin_update_cookies")],
        [InlineKeyboardButton("❌ إغلاق", callback_data="admin_close")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🔧 <b>لوحة تحكم الأدمن</b>\n\nاختر الإجراء الذي تريد تنفيذه:", 
        reply_markup=reply_markup, 
        parse_mode=ParseMode.HTML
    )

async def admin_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    يتعامل مع ضغطات الأزرار في لوحة تحكم الأدمن.
    """
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.edit_message_text(text="❌ ليس لديك صلاحية الوصول إلى هذه الأداة.")
        return

    data = query.data

    if data == "admin_stats":
        total_users = get_user_count()
        await query.edit_message_text(
            text=f"📊 <b>إحصائيات البوت</b>\n\n👥 إجمالي المستخدمين: <code>{total_users}</code>",
            parse_mode=ParseMode.HTML
        )

    elif data == "admin_broadcast":
        context.user_data['awaiting_broadcast'] = True
        await query.edit_message_text(
            text="📢 <b>إرسال رسالة للمستخدمين</b>\n\nأرسل الآن الرسالة التي تريد بثها لجميع المستخدمين.\n\nلإلغاء الأمر، استخدم /cancel.",
            parse_mode=ParseMode.HTML
        )

    elif data == "admin_channel":
        current_channel = get_setting('force_channel')
        if current_channel:
            text = f"⚙️ <b>إعدادات القناة الإجبارية</b>\n\nالقناة الحالية: <code>{current_channel}</code>\n\nلتعيين قناة جديدة، أرسل معرف القناة (مثل @channel_username).\nلحذف القناة الحالية، أرسل 'delete'.\n\nلإلغاء الأمر، استخدم /cancel."
        else:
            text = "⚙️ <b>إعدادات القناة الإجبارية</b>\n\nلا توجد قناة إجبارية حالياً.\n\nلتعيين قناة جديدة، أرسل معرف القناة (مثل @channel_username).\n\nلإلغاء الأمر، استخدم /cancel."
        
        context.user_data['awaiting_channel'] = True
        await query.edit_message_text(text=text, parse_mode=ParseMode.HTML)

    elif data == "admin_update_cookies":
        success = setup_cookies()
        if success:
            await query.edit_message_text(text="✅ تم تحديث ملف الكوكيز بنجاح!")
        else:
            await query.edit_message_text(text="⚠️ تم إنشاء ملف كوكيز فارغ. يرجى إضافة بيانات الكوكيز في متغيرات البيئة.")

    elif data == "admin_close":
        await query.message.delete()

async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    يتعامل مع المدخلات النصية من الأدمن في وضع انتظار إدخال.
    """
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return

    text = update.message.text

    if context.user_data.get('awaiting_broadcast'):
        context.user_data.pop('awaiting_broadcast', None)
        await update.message.reply_text("⏳ جارٍ إرسال الرسالة لجميع المستخدمين...")
        
        users = get_all_users()
        success_count = 0
        fail_count = 0
        
        for user in users:
            try:
                await context.bot.send_message(chat_id=user, text=text)
                success_count += 1
                await asyncio.sleep(0.1)
            except TelegramError as e:
                logger.error(f"فشل إرسال الرسالة إلى {user}: {e}")
                fail_count += 1
        
        await update.message.reply_text(
            f"✅ تم الانتهاء من البث!\n\n"
            f"✅ عدد المرسل لهم: {success_count}\n"
            f"❌ عدد الفاشل: {fail_count}"
        )

    elif context.user_data.get('awaiting_channel'):
        context.user_data.pop('awaiting_channel', None)
        
        if text.lower() == 'delete':
            set_setting('force_channel', '')
            await update.message.reply_text("✅ تم حذف القناة الإجبارية.")
        else:
            if not text.startswith('@'):
                await update.message.reply_text("❌ يجب أن يبدأ معرف القناة بـ @ (مثل @channel_username).")
                return
            
            try:
                chat = await context.bot.get_chat(chat_id=text)
                if chat.type not in ['channel', 'supergroup']:
                    await update.message.reply_text("❌ المعرف المعطى ليس قناة أو مجموعة.")
                    return
                
                bot_member = await context.bot.get_chat_member(chat_id=text, user_id=context.bot.id)
                if bot_member.status not in ['member', 'administrator', 'creator']:
                    await update.message.reply_text("❌ البوت ليس عضوًا في القناة. يرجى إضافته أولاً.")
                    return
                
                set_setting('force_channel', text)
                await update.message.reply_text(f"✅ تم تعيين القناة الإجبارية إلى: {text}")
                
            except TelegramError as e:
                await update.message.reply_text(f"❌ حدث خطأ: {str(e)}")

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    يلغي أي عملية قيد الانتظار.
    """
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return

    context.user_data.pop('awaiting_broadcast', None)
    context.user_data.pop('awaiting_channel', None)
    
    await update.message.reply_text("✅ تم الإلغاء.")

# ==============================================================================
# ٦. الدالة الرئيسية لتشغيل البوت
# ==============================================================================

def main():
    # تهيئة قاعدة البيانات
    init_db()
    
    # إنشاء مجلد التنزيلات إذا لم يكن موجوداً
    if not os.path.exists('downloads'):
        os.makedirs('downloads')
    
    # إعداد ملف الكوكيز
    setup_cookies()

    # إنشاء تطبيق البوت
    application = Application.builder().token(BOT_TOKEN).build()

    # إضافة المعالجات
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("supported", supported_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    
    # معالج للرسائل النصية (الروابط)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # معالج لأزرار الأدمن
    application.add_handler(CallbackQueryHandler(admin_button_callback, pattern="^admin_"))
    
    # معالج لأزرار التحميل والإلغاء
    application.add_handler(CallbackQueryHandler(button_callback, pattern="^(download|cancel|noop)"))
    
    # معالج لمدخلات الأدمن النصية
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_input))

    # بدء البوت
    print("🤖 بوت تحميل الفيديوهات يعمل الآن...")
    print("✅ تم إعداد ملف الكوكيز بنجاح")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
