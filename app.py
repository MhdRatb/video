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
# ١. الإعدادات
# ==============================================================================
dotenv.load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(admin_id) for admin_id in ADMIN_IDS_STR.split(',') if admin_id]

DATABASE_PATH = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
DATABASE_NAME = os.path.join(DATABASE_PATH, "bot_data.db") if DATABASE_PATH else "bot_data.db"

if not BOT_TOKEN:
    raise ValueError("لم يتم العثور على متغير البيئة BOT_TOKEN. يرجى إضافته.")

BOT_API_UPLOAD_LIMIT = 50 * 1024 * 1024

# ==============================================================================
# ٢. دوال قاعدة البيانات
# ==============================================================================

def init_db():
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users ( 
                user_id INTEGER PRIMARY KEY
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        conn.commit()

def add_user(user_id: int):
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()

def get_all_users() -> list[int]:
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users")
        return [row[0] for row in cursor.fetchall()]

def get_user_count() -> int:
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        return cursor.fetchone()[0]

def set_setting(key: str, value: str):
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()

def get_setting(key: str) -> str | None:
    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        result = cursor.fetchone()
        return result[0] if result else None

# ==============================================================================
# ٣. الدوال المساعدة - بدون كوكيز نهائياً
# ==============================================================================

def get_ydl_opts(media_type='video'):
    """
    إرجاع إعدادات yt-dlp بدون أي كوكيز.
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
        # إعدادات متقدمة لدعم جميع المنصات بدون كوكيز
        'extract_flat': False,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Accept-Encoding': 'gzip,deflate',
            'Accept-Charset': 'ISO-8859-1,utf-8;q=0.7,*;q=0.7',
            'Referer': 'https://www.google.com/',
        },
        # إعدادات إضافية لدعم المنصات بدون كوكيز
        'compat_opts': ['no-youtube-unavailable'],
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web'],
            },
            'instagram': {
                'extract_flat': True,
            },
            'tiktok': {
                'api': ['m', 'web'],
            },
            'twitter': {
                'cards': True,
            },
        },
    }
    
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
    new_has_audio = new_format.get('acodec') != 'none'
    current_has_audio = current_format.get('acodec') != 'none'
    
    if new_has_audio and not current_has_audio:
        return True
    elif not new_has_audio and current_has_audio:
        return False
    
    new_tbr = new_format.get('tbr', 0) or 0
    current_tbr = current_format.get('tbr', 0) or 0
    
    return new_tbr > current_tbr

def format_bytes(size):
    if size is None or size <= 0:
        return "غير معروف"
    
    power = 1024
    power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB'}
    
    n = 0
    while size >= power and n < len(power_labels) - 1:
        size /= power
        n += 1
    
    if n == 0:
        return f"{size:.0f} {power_labels[n]}"
    elif size < 10:
        return f"{size:.2f} {power_labels[n]}"
    elif size < 100:
        return f"{size:.1f} {power_labels[n]}"
    else:
        return f"{size:.0f} {power_labels[n]}"

def generate_progress_bar(percentage: float) -> str:
    filled_length = int(10 * percentage / 100)
    bar = '█' * filled_length + '░' * (10 - filled_length)
    return f"[{bar}] {percentage:.1f}%"

def get_supported_domains():
    return [
        'youtube.com', 'youtu.be',
        'instagram.com', 'instagr.am',
        'tiktok.com', 'vm.tiktok.com',
        'twitter.com', 'x.com',
        'facebook.com', 'fb.watch',
        'reddit.com',
        'twitch.tv',
        'vimeo.com',
        'dailymotion.com',
        'soundcloud.com',
        'pinterest.com',
        'likee.video',
        'ok.ru',
        'bilibili.com',
        'rutube.ru',
        'linkedin.com',
        'snapchat.com',
        'pinterest.com',
        'rumble.com',
        '9gag.com',
    ]

def is_supported_url(url: str) -> bool:
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
    
    if not os.path.exists('downloads'):
        os.makedirs('downloads')
    
    opts = get_ydl_opts(media_type)
    
    if format_id and format_id != 'audio':
        opts['format'] = format_id

    try:
        await status_message.edit_text("⏳ جارٍ التحميل... يرجى الانتظار")
        
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = await asyncio.get_event_loop().run_in_executor(
                None, 
                lambda: ydl.extract_info(url, download=True)
            )
            
        await status_message.edit_text("✅ اكتمل التحميل، جارٍ الرفع...")
        
    except Exception as e:
        logging.error(f"فشل تحميل {media_type} من {url}: {e}", exc_info=True)
        
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
        else:
            error_msg += f"\n📋 الخطأ: {str(e)}"
            
        await status_message.edit_text(error_msg)
        return None, None

    try:
        expected_filename = ydl.prepare_filename(info)
        expected_path = os.path.join('downloads', os.path.basename(expected_filename))
        
        if os.path.exists(expected_path):
            return expected_path, media_type
        
        video_id = info.get('id', '') or 'unknown'
        for filename in os.listdir('downloads'):
            if video_id in filename:
                filepath = os.path.join('downloads', filename)
                if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                    logging.info(f"تم العثور على الملف: {filepath}")
                    return filepath, media_type
        
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
    if not fmt:
        return None
    
    size = fmt.get('filesize')
    if size and size > 0:
        return size
    
    size = fmt.get('filesize_approx')
    if size and size > 0:
        return size
    
    if duration and fmt.get('tbr'):
        tbr = fmt.get('tbr', 0)
        if tbr > 0:
            return (tbr * 1000 / 8) * duration
    
    if duration and fmt.get('vcodec') == 'none' and fmt.get('abr'):
        abr = fmt.get('abr', 0)
        if abr > 0:
            return (abr * 1000 / 8) * duration
    
    return None

class UploadProgress:
    def __init__(self, file_path: str, status_message: Message):
        self._file_path = file_path
        self._status_message = status_message
        self._total_size = os.path.getsize(file_path)
        self._last_update_time = 0
        self._last_percentage = -1

    async def update_progress(self, current: int, total: int):
        percentage = (current / total) * 100
        current_time = asyncio.get_event_loop().time()

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

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

async def is_user_subscribed(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    channel_id = get_setting('force_channel')
    if not channel_id:
        return True

    try:
        member = await context.bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except TelegramError as e:
        logger.error(f"خطأ في التحقق من اشتراك المستخدم {user_id} في القناة {channel_id}: {e}")
        return False

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    if not await is_user_subscribed(user_id, context):
        channel_id = get_setting('force_channel')
        channel_link = f"https://t.me/{channel_id.lstrip('@')}" if channel_id else ""
        await update.message.reply_text(
            f"عذراً، يجب عليك الاشتراك في القناة أولاً للاستمرار: {channel_link}\n\n"
            "بعد الاشتراك، اضغط على /start مجدداً."
        )
        return

    add_user(user.id)
    
    supported_platforms = "\n".join([f"• {domain}" for domain in get_supported_domains()[:12]])
    
    await update.message.reply_html(
        f"أهلاً بك يا {user.mention_html()}! 🚀\n\n"
        "أنا بوت تحميل الفيديوهات من مختلف المنصات الاجتماعية.\n\n"
        "<b>المنصات المدعومة:</b>\n"
        f"{supported_platforms}\n\n"
        "<b>🔄 الطريقة:</b>\n"
        "فقط أرسل رابط الفيديو وسأحمله لك فوراً!\n\n"
        "<b>⚡ بدون حاجة لتسجيل الدخول أو كوكيز</b>\n\n"
        "استخدم /supported لعرض جميع المنصات"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    supported_platforms = "\n".join([f"• {domain}" for domain in get_supported_domains()[:10]])
    
    help_text = (
        "<b>🚀 بوت تحميل الفيديوهات</b>\n\n"
        "<b>المنصات المدعومة:</b>\n"
        f"{supported_platforms}\n\n"
        "<b>كيفية الاستخدام:</b>\n"
        "• أرسل رابط الفيديو\n"
        "• اختر الجودة المطلوبة\n"
        "• انتظر حتى يكتمل التحميل\n\n"
        "<b>⚡ المميزات:</b>\n"
        "• يدعم +20 منصة\n"
        "• بدون حاجة لتسجيل الدخول\n"
        "• خيارات متعددة للجودة\n"
        "• دعم الفيديوهات الطويلة\n\n"
        "<b>الأوامر:</b>\n"
        "/start - بدء الاستخدام\n"
        "/help - المساعدة\n"
        "/supported - المنصات المدعومة"
    )
    await update.message.reply_html(help_text)

async def supported_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    supported_platforms = "\n".join([f"• {domain}" for domain in get_supported_domains()])
    
    await update.message.reply_text(
        f"<b>🌐 جميع المنصات المدعومة:</b>\n\n{supported_platforms}\n\n"
        "يمكنك إرسال رابط من أي من هذه المنصات وسأقوم بتحميل المحتوى لك فوراً! 🚀",
        parse_mode=ParseMode.HTML
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return

    user_id = update.effective_user.id
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
    
    if not is_supported_url(url):
        await update.message.reply_text(
            "❌ هذا الرابط غير مدعوم حالياً.\n\n"
            "استخدم /supported لعرض قائمة المنصات المدعومة."
        )
        return

    status_message = await update.message.reply_text("⏳ جارٍ جلب معلومات الفيديو...")

    try:
        info_opts = get_ydl_opts('video')
        info_opts.update({'extract_flat': False, 'ignoreerrors': True})

        with yt_dlp.YoutubeDL(info_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
        if '_type' in info and info['_type'] == 'playlist':
            if info['entries']:
                info = info['entries'][0]
            else:
                await status_message.edit_text("❌ قائمة التشغيل فارغة")
                return

        duration = info.get('duration')

        keyboard = []
        available_formats = {}
        
        best_audio = None
        audio_formats = [f for f in info.get('formats', []) 
                        if f.get('vcodec') == 'none' and f.get('acodec') != 'none']
        
        if audio_formats:
            best_audio = max(audio_formats, 
                        key=lambda x: x.get('abr', 0) or x.get('tbr', 0) or 0)
            
            audio_size = get_estimated_size(best_audio, duration)
            size_str = format_bytes(audio_size)
            
            if audio_size and audio_size > BOT_API_UPLOAD_LIMIT:
                keyboard.append([InlineKeyboardButton(f"🎵 صوت M4A ({size_str}) - حجم كبير", callback_data="noop")])
            else:
                keyboard.append([InlineKeyboardButton(f"🎵 صوت M4A ({size_str})", callback_data=f"download:audio:audio:{update.message.message_id}")])
                available_formats['audio'] = best_audio

        video_formats_by_height = {}
        
        for f in info.get('formats', []):
            if f.get('vcodec') == 'none' or not f.get('height'):
                continue

            height = f['height']
            current_format = video_formats_by_height.get(height)
            
            if not current_format or _is_better_format(f, current_format):
                video_formats_by_height[height] = f

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
                keyboard.append([InlineKeyboardButton(f"🎬 فيديو {height}p ({size_str}) - حجم كبير", callback_data="noop")])
            elif total_size > 0:
                best_format['calculated_size'] = total_size
                keyboard.append([InlineKeyboardButton(f"🎬 فيديو {height}p ({size_str})", callback_data=f"download:video:{height}:{update.message.message_id}")])
                available_formats[height] = best_format

        if not keyboard:
            await status_message.edit_text("❌ عذراً، لم يتم العثور على صيغ تحميل مدعومة لهذا الرابط.")
            return

        original_message_id = update.message.message_id
        context.chat_data[original_message_id] = {
            'url': url, 
            'formats': available_formats,
            'duration': duration,
            'best_audio': best_audio
        }

        keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel:{original_message_id}")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        title = info.get('title', 'فيديو')
        duration_str = format_duration(duration) if duration else "غير معروف"
        
        await status_message.edit_text(
            f"<b>{title}</b>\n⏱️ المدة: {duration_str}\n\nاختر الصيغة:", 
            reply_markup=reply_markup, 
            parse_mode=ParseMode.HTML
        )

    except Exception as e:
        logging.error(f"فشل في جلب معلومات الفيديو من {url}: {e}")
        
        error_msg = "❌ حدث خطأ أثناء جلب المعلومات"
        if "Unsupported URL" in str(e):
            error_msg = "❌ هذا الرابط غير مدعوم"
        elif "No video formats found" in str(e):
            error_msg = "❌ لم يتم العثور على صيغ فيديو"
        elif "Private video" in str(e):
            error_msg = "❌ الفيديو خاص أو محمي"
        elif "Sign in" in str(e):
            error_msg = "❌ يتطلب تسجيل الدخول"
        else:
            error_msg = f"❌ حدث خطأ: {str(e)}"
            
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
            await query.edit_message_text(text="❌ حدث خطأ. الرجاء إرسال الرابط مرة أخرى.")
            return

        download_url = media_info.get('url')

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
            await query.edit_message_text(text="❌ حدث خطأ في معالجة الصيغة.")
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
                    progress_args=(os.path.getsize(file_path),)
                )
            else:
                await context.bot.send_audio(
                    chat_id=query.message.chat_id,
                    audio=open(file_path, 'rb'),
                    read_timeout=60,
                    write_timeout=60,
                    connect_timeout=60,
                    progress=progress.update_progress,
                    progress_args=(os.path.getsize(file_path),)
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
            except:
                pass

        context.chat_data.pop(original_message_id, None)

# ==============================================================================
# ٥. لوحة تحكم الأدمن (مبسطة)
# ==============================================================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ ليس لديك صلاحية الوصول إلى هذه الأداة.")
        return

    keyboard = [
        [InlineKeyboardButton("📊 إحصائيات البوت", callback_data="admin_stats")],
        [InlineKeyboardButton("📢 إرسال رسالة للمستخدمين", callback_data="admin_broadcast")],
        [InlineKeyboardButton("⚙️ إعدادات القناة", callback_data="admin_channel")],
        [InlineKeyboardButton("❌ إغلاق", callback_data="admin_close")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🔧 <b>لوحة تحكم الأدمن</b>\n\nاختر الإجراء:", 
        reply_markup=reply_markup, 
        parse_mode=ParseMode.HTML
    )

async def admin_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.edit_message_text(text="❌ ليس لديك صلاحية.")
        return

    data = query.data

    if data == "admin_stats":
        total_users = get_user_count()
        await query.edit_message_text(
            text=f"📊 <b>إحصائيات البوت</b>\n\n👥 إجمالي المستخدمين: <code>{total_users}</code>",
            parse_mode=ParseMode.HTML
        )
    elif data == "admin_close":
        await query.message.delete()

# ==============================================================================
# ٦. الدالة الرئيسية
# ==============================================================================

def main():
    init_db()
    
    if not os.path.exists('downloads'):
        os.makedirs('downloads')

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("supported", supported_command))
    application.add_handler(CommandHandler("admin", admin_command))
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(admin_button_callback, pattern="^admin_"))
    application.add_handler(CallbackQueryHandler(button_callback, pattern="^(download|cancel|noop)"))

    print("🚀 بوت تحميل الفيديوهات يعمل الآن بدون كوكيز!")
    print("✅ يدعم أكثر من 20 منصة اجتماعية")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
