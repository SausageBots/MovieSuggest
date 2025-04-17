import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ConversationHandler, ContextTypes
import logging

# اطلاعات ربات
TOKEN = "8001327650:AAGXeimUMzxpspEUq7DAnSROPuAQ0ju0xwk"
CHANNEL_USERNAME = "@SausageBots"
OMDB_API_KEY = 'd48fc717'

# لاگ‌برداری
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)

# مراحل گفتگو
GENRE, YEAR, RATING, SELECT = range(4)

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    try:
        member = await context.bot.get_chat_member(CHANNEL_USERNAME, user.id)
        if member.status in ['member', 'administrator', 'creator']:
            keyboard = [
                [InlineKeyboardButton("🎬 پیشنهاد فیلم", callback_data='suggest_movie')],
                [InlineKeyboardButton("📝 ارسال نظر", callback_data='send_feedback')]
            ]
            await update.message.reply_text(
                f"سلام {user.first_name}! 🎉\nبه ربات پیشنهاد فیلم خوش اومدی 🍿",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            keyboard = [[InlineKeyboardButton("عضویت در کانال", url=f"https://t.me/{CHANNEL_USERNAME[1:]}")]]
            await update.message.reply_text("برای استفاده از ربات اول عضو کانال شو 👇", reply_markup=InlineKeyboardMarkup(keyboard))
    except:
        await update.message.reply_text("❌ خطا در بررسی عضویت. لطفاً دوباره تلاش کن.")

# دکمه پیشنهاد فیلم
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "suggest_movie":
        keyboard = [
            [InlineKeyboardButton("اکشن", callback_data="action"),
             InlineKeyboardButton("درام", callback_data="drama"),
             InlineKeyboardButton("کمدی", callback_data="comedy")],
            [InlineKeyboardButton("ترسناک", callback_data="horror"),
             InlineKeyboardButton("فانتزی", callback_data="fantasy"),
             InlineKeyboardButton("ماجراجویی", callback_data="adventure")],
            [InlineKeyboardButton("علمی‌تخیلی", callback_data="sci-fi"),
             InlineKeyboardButton("ورزشی", callback_data="sport"),
             InlineKeyboardButton("تاریخی", callback_data="history")],
            [InlineKeyboardButton("عاشقانه", callback_data="romance"),
             InlineKeyboardButton("جنایی", callback_data="crime"),
             InlineKeyboardButton("انیمیشن", callback_data="animation")]
        ]
        await query.edit_message_text("🎭 ژانر مورد نظر رو انتخاب کن:", reply_markup=InlineKeyboardMarkup(keyboard))
        return GENRE

    elif query.data == "send_feedback":
        await query.edit_message_text("✏️ لطفاً نظر خود را ارسال کنید.")
        return ConversationHandler.END

# انتخاب ژانر
async def genre_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    context.user_data["genre"] = query.data
    await query.answer()

    years = list(range(2000, 2027))
    keyboard = [[InlineKeyboardButton(str(y), callback_data=str(y)) for y in years[i:i+3]] for i in range(0, len(years), 3)]
    await query.edit_message_text("📅 سال ساخت فیلم رو انتخاب کن:", reply_markup=InlineKeyboardMarkup(keyboard))
    return YEAR

# انتخاب سال
async def year_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    context.user_data["year"] = query.data
    await query.answer()

    keyboard = [[InlineKeyboardButton(str(i), callback_data=str(i)) for i in range(6, 11)]]
    await query.edit_message_text("⭐ حداقل امتیاز IMDb رو انتخاب کن:", reply_markup=InlineKeyboardMarkup(keyboard))
    return RATING

# انتخاب امتیاز و نمایش لیست فیلم‌ها
async def rating_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    rating = context.user_data["rating"] = query.data
    genre = context.user_data["genre"]
    year = context.user_data["year"]
    await query.answer()

    url = f"http://www.omdbapi.com/?apikey={OMDB_API_KEY}&s={genre}&y={year}&type=movie"
    response = requests.get(url).json()
    movie_list = []

    if response.get("Response") == "True":
        for movie in response.get("Search", []):
            detail = requests.get(f"http://www.omdbapi.com/?apikey={OMDB_API_KEY}&i={movie['imdbID']}&plot=short&r=json").json()
            imdb_rating = detail.get("imdbRating", "N/A")
            
            # فقط اگر imdbRating عددی است، آن را به float تبدیل کنیم
            if imdb_rating != "N/A" and float(imdb_rating) >= float(rating):
                movie_list.append({
                    "title": detail["Title"],
                    "year": detail["Year"],
                    "rating": imdb_rating,
                    "plot": detail.get("Plot", "خلاصه‌ای در دسترس نیست."),
                    "poster": detail.get("Poster", ""),
                    "id": detail["imdbID"]
                })

        if movie_list:
            context.user_data["movies"] = movie_list[:6]
            buttons = [[InlineKeyboardButton(f"{m['title']} ({m['year']})", callback_data=m["id"])] for m in context.user_data["movies"]]
            await query.edit_message_text("🎥 یکی از فیلم‌های زیر رو انتخاب کن:", reply_markup=InlineKeyboardMarkup(buttons))
            return SELECT
        else:
            await query.edit_message_text("❌ فیلمی با این مشخصات پیدا نشد.")
    else:
        await query.edit_message_text("⛔ مشکلی در دریافت اطلاعات به‌وجود اومد.")

    return ConversationHandler.END


# صفحه انتخاب امتیاز
async def year_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    context.user_data["year"] = query.data
    await query.answer()

    keyboard = [[InlineKeyboardButton(str(i), callback_data=str(i)) for i in range(1, 10)]]
    await query.edit_message_text("⭐ حداقل امتیاز IMDb رو انتخاب کن:", reply_markup=InlineKeyboardMarkup(keyboard))
    return RATING


# نمایش اطلاعات کامل فیلم انتخاب‌شده
async def movie_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    imdb_id = query.data
    await query.answer()

    movie = next((m for m in context.user_data.get("movies", []) if m["id"] == imdb_id), None)

    if movie:
        caption = f"🎬 {movie['title']}\n📅 سال: {movie['year']}\n⭐ امتیاز IMDb: {movie['rating']}\n📖 خلاصه: {movie['plot']}"
        if movie['poster'] != "N/A":
            await query.message.reply_photo(photo=movie['poster'], caption=caption)
        else:
            await query.message.reply_text(caption)
    else:
        await query.message.reply_text("❌ خطا در نمایش اطلاعات فیلم.")

    return ConversationHandler.END

# اجرای بات
def main() -> None:
    application = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button)],
        states={
            GENRE: [CallbackQueryHandler(genre_selected)],
            YEAR: [CallbackQueryHandler(year_selected)],
            RATING: [CallbackQueryHandler(rating_selected)],
            SELECT: [CallbackQueryHandler(movie_detail)]
        },
        fallbacks=[],
        per_message=False
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)

    application.run_polling()

if __name__ == "__main__":
    main()
