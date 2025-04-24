import requests
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ConversationHandler, ContextTypes, MessageHandler, filters
import logging
from threading import Lock
import re
import telegram.error
import time
import os

# تنظیم لاگینگ فقط برای خطاها
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.ERROR)
logger = logging.getLogger(__name__)

# تعریف متغیرهای مورد نیاز
TOKEN = "8145864683:AAEOyjeIvXr_A6F2k5kFyJKr-UEeSKR8AxM"
OMDB_API_KEY = "d48fc717"
CHANNEL_USERNAME = "@SausageBots"
ADMIN_ID = 5990266020


# تنظیم کش برای API OMDb
import requests_cache
requests_cache.install_cache('omdb_cache', expire_after=86400)

# حالت‌های مکالمه
GENRE, MIN_IMDB_RATING, SELECT, PLAYLIST_NAME, ADD_TO_PLAYLIST, VIEW_PLAYLIST, FEEDBACK, DELETE_MOVIE, PAGE_NAV = range(9)

# قفل برای عملیات فایل thread-safe
file_lock = Lock()

# بارگذاری و ذخیره پلی‌لیست‌ها
def load_playlists():
    with file_lock:
        try:
            with open('playlists.json', 'r', encoding='utf-8') as file:
                return json.load(file)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

def save_playlists(playlists):
    with file_lock:
        try:
            with open('playlists.json', 'w', encoding='utf-8') as file:
                json.dump(playlists, file, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"خطا در ذخیره پلی‌لیست‌ها: {e}")

playlists = load_playlists()

# لیست ژانرها
GENRE_LIST = {
    'اکشن': 'action', 'کمدی': 'comedy', 'درام': 'drama', 'هیجان‌انگیز': 'thriller',
    'ترسناک': 'horror', 'ماجراجویی': 'adventure', 'فانتزی': 'fantasy',
    'علمی تخیلی': 'sci-fi', 'رمانتیک': 'romance', 'انیمیشن': 'animation'
}

# دستور تست برای بررسی اتصال
async def test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await update.message.reply_text("ربات فعاله! 🎉")
    except Exception as e:
        logger.error(f"خطا در ارسال پاسخ /test برای کاربر {update.effective_user.id}: {e}")

# دستور /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    context.user_data.clear()  # ریست داده‌های مکالمه

    # بررسی عضویت در کانال
    try:
        member = await context.bot.get_chat_member(CHANNEL_USERNAME, user.id)
        if member.status not in ['member', 'administrator', 'creator']:
            keyboard = [[InlineKeyboardButton("عضویت در کانال", url=f"https://t.me/{CHANNEL_USERNAME[1:]}")]]
            await update.message.reply_text(
                "برای استفاده از ربات اول عضو کانال شو 👇",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return ConversationHandler.END
    except telegram.error.TelegramError as e:
        logger.error(f"خطا در بررسی عضویت برای کاربر {user.id}: {e}")
        await update.message.reply_text(
            "❌ خطا در بررسی عضویت! مطمئن شو که ربات ادمین کاناله یا دوباره تلاش کن."
        )
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"خطای غیرمنتظره در بررسی عضویت برای کاربر {user.id}: {e}")
        await update.message.reply_text("❌ خطایی رخ داد. لطفاً دوباره تلاش کن.")
        return ConversationHandler.END

    # نمایش منوی اصلی
    try:
        user_name = user.first_name if user.first_name else "کاربر"
        keyboard = [
            [InlineKeyboardButton("🎬 پیشنهاد فیلم", callback_data='suggest_movie')],
            [InlineKeyboardButton("📝 ارسال نظر", callback_data='send_feedback')],
            [InlineKeyboardButton("🎶 پلی‌لیست‌ها", callback_data='view_playlists')]
        ]
        message = f"سلام {user_name}! 🎉\nبه ربات پیشنهاد فیلم خوش اومدی 🍿"
        await update.message.reply_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
    except telegram.error.Forbidden as e:
        logger.error(f"ربات نمی‌تواند به کاربر {user.id} پیام بفرستد (ممنوع): {e}")
    except Exception as e:
        logger.error(f"خطا در ارسال منوی اصلی به کاربر {user.id}: {e}")
        try:
            await update.message.reply_text("❌ خطایی رخ داد. لطفاً دوباره تلاش کن.")
        except Exception as e2:
            logger.error(f"خطا در ارسال پیام خطا به کاربر {user.id}: {e2}")
    return ConversationHandler.END

# پیشنهاد فیلم
async def suggest_movie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton(genre, callback_data=genre)] for genre in GENRE_LIST.keys()]
    await query.edit_message_text(
        text="یه ژانر انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return GENRE

# دریافت فیلم‌ها بر اساس ژانر
def get_movies_by_genre(genre):
    try:
        url = f"http://www.omdbapi.com/?apikey={OMDB_API_KEY}&s={genre}&type=movie"
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        if data.get('Response') == 'True':
            return data['Search'][:5]
        return []
    except requests.RequestException as e:
        logger.error(f"خطا در درخواست به OMDb API: {e}")
        return []

# انتخاب ژانر
async def genre_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    genre_fa = query.data
    genre_en = GENRE_LIST.get(genre_fa)
    if not genre_en:
        await query.answer("ژانر نامعتبر!")
        return GENRE

    context.user_data['selected_genre'] = genre_fa
    context.user_data['genre_en'] = genre_en
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("بدون فیلتر امتیاز", callback_data='no_rating_filter')],
        [InlineKeyboardButton("وارد کردن حداقل امتیاز", callback_data='enter_rating')]
    ]
    await query.edit_message_text(
        text="آیا می‌خوای یه حداقل امتیاز IMDb (مثلاً 7.0) مشخص کنی؟\n(بین 0.0 تا 10.0)",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return MIN_IMDB_RATING

# مدیریت حداقل امتیاز IMDb
async def min_imdb_rating(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query.data == 'no_rating_filter':
        context.user_data['min_imdb_rating'] = None
        await query.answer()
        return await show_movies(update, context)

    await query.answer()
    await query.message.reply_text(
        "لطفاً حداقل امتیاز IMDb را وارد کن (مثلاً 7.0، بین 0.0 تا 10.0).\n"
        "یا برای لغو، /cancel رو بزن."
    )
    return MIN_IMDB_RATING

async def process_imdb_rating(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    rating_text = update.message.text.strip()
    try:
        rating = float(rating_text)
        if 0.0 <= rating <= 10.0:
            context.user_data['min_imdb_rating'] = rating
            return await show_movies(update, context)
        else:
            await update.message.reply_text(
                "امتیاز باید بین 0.0 تا 10.0 باشه! دوباره وارد کن یا /cancel رو بزن."
            )
            return MIN_IMDB_RATING
    except ValueError:
        await update.message.reply_text(
            "ورودی نامعتبره! یه عدد (مثلاً 7.0) وارد کن یا /cancel رو بزن."
        )
        return MIN_IMDB_RATING

# نمایش فیلم‌های فیلترشده
async def show_movies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query if update.callback_query else None
    genre_en = context.user_data.get('genre_en')
    genre_fa = context.user_data.get('selected_genre')
    min_rating = context.user_data.get('min_imdb_rating')

    movies = get_movies_by_genre(genre_en)
    if not movies:
        text = "هیچ فیلمی پیدا نشد! یه ژانر دیگه انتخاب کن یا بعداً امتحان کن."
        keyboard = [[InlineKeyboardButton("برگشت", callback_data='suggest_movie')]]
        if query:
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))
        return GENRE

    filtered_movies = []
    for movie in movies:
        movie_details = get_movie_details(movie['imdbID'])
        if movie_details.get('Response') == 'True':
            imdb_rating = movie_details.get('imdbRating', 'N/A')
            if imdb_rating != 'N/A':
                try:
                    rating = float(imdb_rating)
                    if min_rating is None or rating >= min_rating:
                        filtered_movies.append(movie)
                except ValueError:
                    continue

    if not filtered_movies:
        text = f"هیچ فیلمی با امتیاز {min_rating} یا بالاتر در ژانر {genre_fa} پیدا نشد!\nیه ژانر دیگه یا امتیاز پایین‌تر امتحان کن."
        keyboard = [[InlineKeyboardButton("برگشت", callback_data='suggest_movie')]]
        if query:
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))
        return GENRE

    keyboard = [[InlineKeyboardButton(movie['Title'], callback_data=movie['imdbID'])] for movie in filtered_movies[:5]]
    text = f"فیلم‌های ژانر {genre_fa}"
    if min_rating:
        text += f" با امتیاز {min_rating} یا بالاتر"
    text += ":"

    if query:
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT

# دریافت جزئیات فیلم
def get_movie_details(imdb_id):
    try:
        url = f"http://www.omdbapi.com/?apikey={OMDB_API_KEY}&i={imdb_id}"
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"خطا در گرفتن جزئیات فیلم: {e}")
        return {'Response': 'False'}

# نمایش جزئیات فیلم
async def movie_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    imdb_id = query.data
    movie_info = get_movie_details(imdb_id)
    if movie_info.get('Response') == 'False':
        await query.answer()
        await query.edit_message_text(
            text="خطا در گرفتن اطلاعات فیلم! لطفاً بعداً امتحان کن.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("برگشت", callback_data='suggest_movie')]
            ])
        )
        return SELECT

    context.user_data['movie_id'] = imdb_id
    context.user_data['movie_title'] = movie_info['Title']
    text = (
        f"عنوان: {movie_info['Title']}\n"
        f"سال: {movie_info.get('Year', 'نامشخص')}\n"
        f"امتیاز: {movie_info.get('imdbRating', 'نامشخص')}\n"
        f"خلاصه: {movie_info.get('Plot', 'نامشخص')}"
    )
    keyboard = [
        [InlineKeyboardButton("اضافه به پلی‌لیست", callback_data='add_to_playlist')],
        [InlineKeyboardButton("برگشت", callback_data='back_to_genre')]
    ]
    await query.answer()
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))
    return ADD_TO_PLAYLIST

# اضافه کردن به پلی‌لیست
async def add_to_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query.data == 'back_to_genre':
        keyboard = [[InlineKeyboardButton(genre, callback_data=genre)] for genre in GENRE_LIST.keys()]
        await query.answer()
        await query.edit_message_text(
            text="یه ژانر انتخاب کن:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return GENRE

    await query.answer()
    await query.message.reply_text("لطفاً نام پلی‌لیست رو وارد کن (یا یه اسم جدید برای پلی‌لیست جدید):")
    return PLAYLIST_NAME

# ذخیره پلی‌لیست
async def save_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    playlist_name = update.message.text.strip()
    movie_id = context.user_data.get('movie_id')
    movie_title = context.user_data.get('movie_title')
    user_id = str(update.effective_user.id)

    if not movie_id or not movie_title:
        await update.message.reply_text("خطا! فیلمی انتخاب نشده. دوباره از /start شروع کن.")
        return ConversationHandler.END

    if not playlist_name or not re.match(r'^[\w\s]+$', playlist_name):
        await update.message.reply_text("نام پلی‌لیست نمی‌تونه خالی باشه یا شامل کاراکترهای خاص باشه!")
        return PLAYLIST_NAME

    playlists.setdefault(user_id, {})
    playlists[user_id].setdefault(playlist_name, []).append({'id': movie_id, 'title': movie_title})
    save_playlists(playlists)
    await update.message.reply_text(f"فیلم «{movie_title}» به پلی‌لیست «{playlist_name}» اضافه شد!")
    return ConversationHandler.END

# مشاهده پلی‌لیست‌ها
async def view_playlists(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = str(update.effective_user.id)
    await query.answer()

    user_playlists = playlists.get(user_id, {})
    if not user_playlists:
        await query.edit_message_text(
            text="هیچ پلی‌لیستی نداری! یه پلی‌لیست جدید بساز.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("برگشت", callback_data='suggest_movie')]
            ])
        )
        return ConversationHandler.END

    keyboard = [[InlineKeyboardButton(name, callback_data=f"view_playlist_{name}")] for name in user_playlists.keys()]
    await query.edit_message_text(
        text="پلی‌لیست‌های تو:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return VIEW_PLAYLIST

# نمایش محتوای پلی‌لیست با صفحه‌بندی
async def view_playlist_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = str(update.effective_user.id)
    data = query.data

    if data.startswith('next_page_') or data.startswith('prev_page_'):
        playlist_name = data.split('_', 2)[2]
        page = context.user_data.get('page', 0)
        if data.startswith('next_page_'):
            page += 1
        else:
            page = max(0, page - 1)
        context.user_data['page'] = page
    else:
        playlist_name = data.replace("view_playlist_", "")
        context.user_data['page'] = 0
        context.user_data['playlist_name'] = playlist_name

    user_playlists = playlists.get(user_id, {})
    if playlist_name not in user_playlists:
        await query.answer()
        await query.edit_message_text(
            text="این پلی‌لیست وجود نداره!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("برگشت", callback_data='view_playlists')]
            ])
        )
        return ConversationHandler.END

    movies = user_playlists[playlist_name]
    if not movies:
        text = f"پلی‌لیست «{playlist_name}» خالیه!"
        keyboard = [[InlineKeyboardButton("برگشت", callback_data='view_playlists')]]
    else:
        per_page = 5
        page = context.user_data.get('page', 0)
        start = page * per_page
        end = start + per_page
        text = f"فیلم‌های پلی‌لیست «{playlist_name}» (صفحه {page + 1}):\n"
        keyboard = []
        for i, movie in enumerate(movies[start:end], start + 1):
            text += f"{i}. {movie['title']}\n"
            keyboard.append([InlineKeyboardButton(f"حذف {movie['title']}", callback_data=f"delete_movie_{playlist_name}_{start + i - 1}")])
        
        nav_buttons = []
        if start > 0:
            nav_buttons.append(InlineKeyboardButton("قبلی", callback_data=f"prev_page_{playlist_name}"))
        if end < len(movies):
            nav_buttons.append(InlineKeyboardButton("بعدی", callback_data=f"next_page_{playlist_name}"))
        if nav_buttons:
            keyboard.append(nav_buttons)
        keyboard.append([InlineKeyboardButton("برگشت", callback_data='view_playlists')])

    await query.answer()
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))
    return DELETE_MOVIE

# حذف فیلم از پلی‌لیست
async def delete_movie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = str(update.effective_user.id)
    if query.data == 'view_playlists':
        return await view_playlists(update, context)

    try:
        data = query.data.split('_')
        playlist_name = data[2]
        movie_index = int(data[3])

        user_playlists = playlists.get(user_id, {})
        if playlist_name in user_playlists and 0 <= movie_index < len(user_playlists[playlist_name]):
            deleted_movie = user_playlists[playlist_name].pop(movie_index)
            save_playlists(playlists)
            await query.answer()
            await query.edit_message_text(
                text=f"فیلم «{deleted_movie['title']}» از پلی‌لیست «{playlist_name}» حذف شد!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("برگشت", callback_data='view_playlists')]
                ])
            )
        else:
            await query.answer()
            await query.edit_message_text(
                text="خطا! فیلم یا پلی‌لیست پیدا نشد.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("برگشت", callback_data='view_playlists')]
                ])
            )
    except (IndexError, ValueError) as e:
        logger.error(f"خطا در حذف فیلم: {e}")
        await query.answer()
        await query.edit_message_text(
            text="خطا در پردازش درخواست!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("برگشت", callback_data='view_playlists')]
            ])
        )
    return VIEW_PLAYLIST

# ارسال نظر
async def send_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("نظرت رو بنویس، مستقیم می‌فرستم به ادمین! 😎")
    return FEEDBACK

# ذخیره و ارسال نظر
async def save_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    feedback = update.message.text.strip()
    if not feedback:
        await update.message.reply_text("نظرت نمی‌تونه خالی باشه! یه چیزی بنویس.")
        return FEEDBACK

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"نظر جدید از {user.first_name} (ID: {user.id}):\n{feedback}"
        )
        await update.message.reply_text("نظرت با موفقیت برای ادمین فرستاده شد! 🙌")
    except Exception as e:
        logger.error(f"خطا در ارسال نظر به ادمین: {e}")
        await update.message.reply_text("❌ خطا! نمی‌تونم نظرت رو بفرستم. لطفاً بعداً امتحان کن.")
    return ConversationHandler.END

# دستور /cancel
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("مکالمه لغو شد! دوباره با /start شروع کن.")
    return ConversationHandler.END

# تابع اصلی
def main():
    max_retries = 3
    retry_delay = 5
    retries = 0

    while retries < max_retries:
        try:
            application = Application.builder().token(TOKEN).build()
            application.add_handler(CommandHandler("test", test))

            conv_handler = ConversationHandler(
                entry_points=[
                    CommandHandler("start", start),
                    CallbackQueryHandler(suggest_movie, pattern='suggest_movie'),
                    CallbackQueryHandler(view_playlists, pattern='view_playlists'),
                    CallbackQueryHandler(send_feedback, pattern='send_feedback')
                ],
                states={
                    GENRE: [CallbackQueryHandler(genre_selected)],
                    MIN_IMDB_RATING: [
                        CallbackQueryHandler(min_imdb_rating, pattern='(no_rating_filter|enter_rating)'),
                        MessageHandler(filters.TEXT & ~filters.COMMAND, process_imdb_rating)
                    ],
                    SELECT: [CallbackQueryHandler(movie_detail)],
                    ADD_TO_PLAYLIST: [CallbackQueryHandler(add_to_playlist)],
                    PLAYLIST_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_playlist)],
                    VIEW_PLAYLIST: [CallbackQueryHandler(view_playlist_content, pattern='view_playlist_.*')],
                    DELETE_MOVIE: [
                        CallbackQueryHandler(view_playlist_content, pattern='(next_page_|prev_page_).*'),
                        CallbackQueryHandler(delete_movie)
                    ],
                    FEEDBACK: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_feedback)]
                },
                fallbacks=[CommandHandler("cancel", cancel)],
                persistent=False,
                per_message=True
            )

            application.add_handler(conv_handler)

            async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
                logger.error(f"به‌روزرسانی {update} باعث خطا شد: {context.error}")
                if update and update.message:
                    try:
                        await update.message.reply_text("❌ خطایی رخ داد. لطفاً دوباره تلاش کن یا با ادمین تماس بگیر.")
                    except Exception as e:
                        logger.error(f"خطا در ارسال پیام خطا: {e}")

            application.add_error_handler(error_handler)
            application.run_polling()
            break

        except telegram.error.Conflict as e:
            logger.error(f"خطای تداخل: {e}. تلاش مجدد پس از {retry_delay} ثانیه...")
            retries += 1
            time.sleep(retry_delay)
            if retries == max_retries:
                logger.error("حداکثر تعداد تلاش‌ها به پایان رسید. خروج.")
                raise
        except Exception as e:
            logger.error(f"خطای غیرمنتظره در راه‌اندازی ربات: {e}")
            retries += 1
            time.sleep(retry_delay)
            if retries == max_retries:
                logger.error("حداکثر تعداد تلاش‌ها به پایان رسید. خروج.")
                raise

if __name__ == '__main__':
    main()
