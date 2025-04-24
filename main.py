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

# Import Flask
from flask import Flask, request, jsonify

# Configure logging only for errors
# تغییر سطح لاگینگ برای نمایش اطلاعات مهم‌تر در حالت وب‌هوک
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Define required variables
TOKEN = "8145864683:AAEOyjeIvXr_A6F2k5kFyJKR-UEeSKR8AxM" # NOTE: Changed a character in the token for security. Replace with your actual token.
OMDB_API_KEY = "d48fc717"
CHANNEL_USERNAME = "@SausageBots"
ADMIN_ID = 5990266020

# Webhook specific settings
# Replace with your actual public URL where the bot is hosted
# This is crucial for webhooks. Example: "https://your-domain.com"
WEBHOOK_HOST = ""
WEBHOOK_PATH = "/webhook" # Path for the webhook endpoint
WEBHOOK_URL = f"https://{WEBHOOK_HOST}{WEBHOOK_PATH}"
LISTEN_PORT = 8443 # Standard port for webhooks (443, 80, 88, 8443)

# Set cache for OMDb API
import requests_cache
requests_cache.install_cache('omdb_cache', expire_after=86400)

# Conversation states
GENRE, MIN_IMDB_RATING, SELECT, PLAYLIST_NAME, ADD_TO_PLAYLIST, VIEW_PLAYLIST, FEEDBACK, DELETE_MOVIE, PAGE_NAV = range(9)

# Lock for thread-safe file operations
file_lock = Lock()

# Load and save playlists
def load_playlists():
    with file_lock:
        try:
            with open('playlists.json', 'r', encoding='utf-8') as file:
                return json.load(file)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.info("No playlists.json found or file is empty/invalid. Starting with empty playlists.")
            return {}

def save_playlists(playlists):
    with file_lock:
        try:
            with open('playlists.json', 'w', encoding='utf-8') as file:
                json.dump(playlists, file, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error saving playlists: {e}")

playlists = load_playlists()

# Genre list
GENRE_LIST = {
    'اکشن': 'action', 'کمدی': 'comedy', 'درام': 'drama', 'هیجان‌انگیز': 'thriller',
    'ترسناک': 'horror', 'ماجراجویی': 'adventure', 'فانتزی': 'fantasy',
    'علمی تخیلی': 'sci-fi', 'رمانتیک': 'romance', 'انیمیشن': 'animation'
}

# Test command
async def test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await update.message.reply_text("ربات فعاله! 🎉")
    except Exception as e:
        logger.error(f"Error sending /test response for user {update.effective_user.id}: {e}")

# /start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    context.user_data.clear()  # Reset conversation data

    # Check channel membership
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
        logger.error(f"Error checking membership for user {user.id}: {e}")
        await update.message.reply_text(
            "❌ خطا در بررسی عضویت! مطمئن شو که ربات ادمین کاناله یا دوباره تلاش کن."
        )
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Unexpected error checking membership for user {user.id}: {e}")
        await update.message.reply_text("❌ خطایی رخ داد. لطفاً دوباره تلاش کن.")
        return ConversationHandler.END

    # Display main menu
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
        logger.error(f"Bot cannot send messages to user {user.id} (Forbidden): {e}")
    except Exception as e:
        logger.error(f"Error sending main menu to user {user.id}: {e}")
        # No need to send another message if the first one failed due to Forbidden
        if not isinstance(e, telegram.error.Forbidden):
             try:
                 await update.message.reply_text("❌ خطایی رخ داد. لطفاً دوباره تلاش کن.")
             except Exception as e2:
                 logger.error(f"Error sending error message to user {user.id}: {e2}")
    return ConversationHandler.END

# Suggest movie
async def suggest_movie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton(genre, callback_data=genre)] for genre in GENRE_LIST.keys()]
    # Add a back button to the main menu
    keyboard.append([InlineKeyboardButton("برگشت به منوی اصلی", callback_data='back_to_main_menu')])
    await query.edit_message_text(
        text="یه ژانر انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return GENRE

# Get movies by genre
def get_movies_by_genre(genre):
    try:
        url = f"http://www.omdbapi.com/?apikey={OMDB_API_KEY}&s={genre}&type=movie"
        response = requests.get(url, timeout=10) # Increased timeout
        response.raise_for_status()
        data = response.json()
        if data.get('Response') == 'True':
            # OMDb search results don't include rating directly.
            # We'll get top results and then fetch details for filtering.
            return data.get('Search', [])
        return []
    except requests.RequestException as e:
        logger.error(f"Error requesting OMDb API: {e}")
        return []

# Get movie details
def get_movie_details(imdb_id):
    try:
        url = f"http://www.omdbapi.com/?apikey={OMDB_API_KEY}&i={imdb_id}"
        response = requests.get(url, timeout=10) # Increased timeout
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Error getting movie details for {imdb_id}: {e}")
        return {'Response': 'False'}

# Select genre
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
        [InlineKeyboardButton("وارد کردن حداقل امتیاز", callback_data='enter_rating')],
        [InlineKeyboardButton("برگشت", callback_data='suggest_movie')] # Back to genre list
    ]
    await query.edit_message_text(
        text=f"ژانر «{genre_fa}» انتخاب شد.\nآیا می‌خوای یه حداقل امتیاز IMDb (مثلاً 7.0) مشخص کنی؟\n(بین 0.0 تا 10.0)",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return MIN_IMDB_RATING

# Handle minimum IMDb rating
async def min_imdb_rating(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    data = query.data
    await query.answer()

    if data == 'no_rating_filter':
        context.user_data['min_imdb_rating'] = None
        return await show_movies(update, context)
    elif data == 'enter_rating':
        await query.message.reply_text(
            "لطفاً حداقل امتیاز IMDb را وارد کن (مثلاً 7.0، بین 0.0 تا 10.0).\n"
            "یا برای لغو، /cancel رو بزن."
        )
        return MIN_IMDB_RATING # Stay in this state waiting for text input
    elif data == 'suggest_movie': # Handle back button from rating filter
         keyboard = [[InlineKeyboardButton(genre, callback_data=genre)] for genre in GENRE_LIST.keys()]
         keyboard.append([InlineKeyboardButton("برگشت به منوی اصلی", callback_data='back_to_main_menu')])
         await query.edit_message_text(
            text="یه ژانر انتخاب کن:",
            reply_markup=InlineKeyboardMarkup(keyboard)
         )
         return GENRE


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
            return MIN_IMDB_RATING # Stay in this state
    except ValueError:
        await update.message.reply_text(
            "ورودی نامعتبره! یه عدد (مثلاً 7.0) وارد کن یا /cancel رو بزن."
        )
        return MIN_IMDB_RATING # Stay in this state

# Show filtered movies
async def show_movies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query if update.callback_query else None
    genre_en = context.user_data.get('genre_en')
    genre_fa = context.user_data.get('selected_genre')
    min_rating = context.user_data.get('min_imdb_rating')

    if not genre_en: # Should not happen if flow is correct, but defensive
         text = "خطا! ژانر انتخاب نشده. لطفاً دوباره از /start شروع کنید."
         keyboard = [[InlineKeyboardButton("شروع مجدد", callback_data='back_to_main_menu')]]
         if query:
              await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))
         else:
              await update.message.reply_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))
         return ConversationHandler.END

    movies = get_movies_by_genre(genre_en)
    if not movies:
        text = f"هیچ فیلمی در ژانر «{genre_fa}» پیدا نشد! یه ژانر دیگه انتخاب کن یا بعداً امتحان کن."
        keyboard = [[InlineKeyboardButton("برگشت", callback_data='suggest_movie')]]
        if query:
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))
        return GENRE # Stay in GENRE state to allow choosing another genre

    filtered_movies = []
    # Fetch details for filtering by rating
    # Limit the number of details fetches to avoid hitting API limits quickly
    for movie in movies[:15]: # Try fetching details for the first 15 results
        movie_details = get_movie_details(movie.get('imdbID'))
        if movie_details.get('Response') == 'True':
            imdb_rating_str = movie_details.get('imdbRating', 'N/A')
            if imdb_rating_str != 'N/A':
                try:
                    rating = float(imdb_rating_str)
                    if min_rating is None or rating >= min_rating:
                        # Store necessary info including Year and Poster if available
                        filtered_movies.append({
                            'imdbID': movie_details.get('imdbID'),
                            'Title': movie_details.get('Title'),
                            'Year': movie_details.get('Year', 'نامشخص'),
                            'Poster': movie_details.get('Poster', '') # Include poster URL
                        })
                except ValueError:
                    continue # Skip if rating is not a valid number

    if not filtered_movies:
        rating_text = f" با امتیاز {min_rating} یا بالاتر" if min_rating is not None else ""
        text = f"هیچ فیلمی {rating_text} در ژانر «{genre_fa}» پیدا نشد!\nیه ژانر دیگه یا امتیاز پایین‌تر امتحان کن."
        keyboard = [[InlineKeyboardButton("برگشت", callback_data='suggest_movie')]]
        if query:
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
             await update.message.reply_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))
        return GENRE # Stay in GENRE state

    # Store the filtered movies in user_data for later selection
    context.user_data['filtered_movies'] = filtered_movies

    keyboard = [[InlineKeyboardButton(movie['Title'], callback_data=movie['imdbID'])] for movie in filtered_movies[:5]]
    # Add a back button to the rating filter step
    keyboard.append([InlineKeyboardButton("برگشت به فیلتر امتیاز", callback_data='enter_rating')]) # Using 'enter_rating' callback data

    text = f"فیلم‌های ژانر «{genre_fa}»"
    if min_rating is not None:
        text += f" با امتیاز {min_rating} یا بالاتر"
    text += ":"

    if query:
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT

# Show movie details
async def movie_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    imdb_id = query.data
    await query.answer()

    movie_info = get_movie_details(imdb_id)
    if movie_info.get('Response') == 'False':
        await query.edit_message_text(
            text="خطا در گرفتن اطلاعات فیلم! لطفاً بعداً امتحان کن.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("برگشت به لیست فیلم‌ها", callback_data='back_to_movie_list')]
            ])
        )
        return SELECT # Go back to movie selection state

    context.user_data['movie_id'] = movie_info.get('imdbID')
    context.user_data['movie_title'] = movie_info.get('Title')
    # Store full movie info if needed later, or just the necessary parts
    context.user_data['current_movie_details'] = movie_info

    text = (
        f"عنوان: {movie_info.get('Title', 'نامشخص')}\n"
        f"سال: {movie_info.get('Year', 'نامشخص')}\n"
        f"امتیاز: {movie_info.get('imdbRating', 'نامشخص')}\n"
        f"کارگردان: {movie_info.get('Director', 'نامشخص')}\n"
        f"بازیگران: {movie_info.get('Actors', 'نامشخص')}\n"
        f"خلاصه: {movie_info.get('Plot', 'نامشخص')}"
    )
    keyboard = [
        [InlineKeyboardButton("اضافه به پلی‌لیست", callback_data='add_to_playlist')],
        [InlineKeyboardButton("برگشت به لیست فیلم‌ها", callback_data='back_to_movie_list')]
    ]

    # Include poster if available
    poster_url = movie_info.get('Poster')
    if poster_url and poster_url != 'N/A':
         try:
             # Send photo first, then the details as a caption or separate message
             await context.bot.send_photo(
                 chat_id=query.message.chat_id,
                 photo=poster_url,
                 caption=text,
                 reply_markup=InlineKeyboardMarkup(keyboard)
             )
             # Delete the previous message (with just buttons)
             await query.delete_message()
         except telegram.error.BadRequest:
             # If sending photo fails (e.g., invalid URL, too large), send as text
             logger.warning(f"Failed to send poster for {movie_info.get('Title')}. Sending as text.")
             await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
         # If no poster, just edit the message with details and buttons
         await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))

    return ADD_TO_PLAYLIST

# Add to playlist
async def add_to_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    data = query.data
    await query.answer()

    if data == 'back_to_movie_list':
        # Re-show the list of filtered movies
        filtered_movies = context.user_data.get('filtered_movies', [])
        if not filtered_movies:
             # This should not happen if flow is correct, but handle defensively
            await query.edit_message_text("خطا! لیست فیلم‌ها پیدا نشد. لطفاً دوباره از /start شروع کنید.")
            context.user_data.clear()
            return ConversationHandler.END

        keyboard = [[InlineKeyboardButton(movie['Title'], callback_data=movie['imdbID'])] for movie in filtered_movies[:5]] # Displaying first 5 again
        # Add a back button to the rating filter step
        keyboard.append([InlineKeyboardButton("برگشت به فیلتر امتیاز", callback_data='enter_rating')])

        genre_fa = context.user_data.get('selected_genre', 'انتخابی')
        min_rating = context.user_data.get('min_imdb_rating')
        text = f"فیلم‌های ژانر «{genre_fa}»"
        if min_rating is not None:
            text += f" با امتیاز {min_rating} یا بالاتر"
        text += ":"

        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))
        return SELECT # Go back to movie selection state

    # If data is 'add_to_playlist'
    await query.message.reply_text("لطفاً نام پلی‌لیست رو وارد کن (یا یه اسم جدید برای پلی‌لیست جدید):")
    return PLAYLIST_NAME

# Save playlist
async def save_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    playlist_name = update.message.text.strip()
    movie_id = context.user_data.get('movie_id')
    movie_title = context.user_data.get('movie_title')
    user_id = str(update.effective_user.id)

    if not movie_id or not movie_title:
        await update.message.reply_text("خطا! فیلمی انتخاب نشده. دوباره از /start شروع کن.")
        context.user_data.clear() # Clear user data on error
        return ConversationHandler.END

    # Allow spaces and Persian characters, but prevent empty or just special chars
    if not playlist_name or not re.match(r'^[\w\s\u0600-\u06FF]+$', playlist_name):
         await update.message.reply_text("نام پلی‌لیست نمی‌تونه خالی باشه یا شامل کاراکترهای نامعتبر باشه!")
         return PLAYLIST_NAME # Stay in the same state to re-enter name

    playlists.setdefault(user_id, {})
    user_playlists = playlists[user_id]

    # Check if the movie is already in the playlist
    if any(movie['id'] == movie_id for movie in user_playlists.get(playlist_name, [])):
         await update.message.reply_text(f"فیلم «{movie_title}» قبلاً در پلی‌لیست «{playlist_name}» وجود داشت!")
         context.user_data.clear() # Clear user data after completing
         return ConversationHandler.END # End conversation

    user_playlists.setdefault(playlist_name, []).append({'id': movie_id, 'title': movie_title})
    save_playlists(playlists)
    await update.message.reply_text(f"فیلم «{movie_title}» به پلی‌لیست «{playlist_name}» اضافه شد!")
    context.user_data.clear() # Clear user data after completing
    return ConversationHandler.END

# View playlists
async def view_playlists(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # This function can be called by a command (/start menu callback) or a callback from within conversation
    query = update.callback_query
    user_id = str(update.effective_user.id)
    if query: await query.answer()

    user_playlists = playlists.get(user_id, {})
    if not user_playlists:
        text = "هیچ پلی‌لیستی نداری! یه پلی‌لیست جدید بساز."
        keyboard = [[InlineKeyboardButton("برگشت به منوی اصلی", callback_data='back_to_main_menu')]]
        if query:
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
             await update.message.reply_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))

        context.user_data.clear() # Clear user data
        return ConversationHandler.END

    keyboard = [[InlineKeyboardButton(name, callback_data=f"view_playlist_{name}")] for name in user_playlists.keys()]
    # Add a back button
    keyboard.append([InlineKeyboardButton("برگشت به منوی اصلی", callback_data='back_to_main_menu')])

    text="پلی‌لیست‌های تو:"
    if query:
        await query.edit_message_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
         await update.message.reply_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    return VIEW_PLAYLIST

# Show playlist content with pagination and delete buttons
async def view_playlist_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = str(update.effective_user.id)
    data = query.data
    await query.answer()

    user_playlists = playlists.get(user_id, {})
    playlist_name = context.user_data.get('current_playlist_name') # Get playlist name from user_data

    if data.startswith('view_playlist_'):
         playlist_name = data.replace("view_playlist_", "")
         context.user_data['current_playlist_name'] = playlist_name # Store playlist name
         context.user_data['page'] = 0 # Reset page for new playlist view

    elif data.startswith('next_page_'):
         page = context.user_data.get('page', 0)
         context.user_data['page'] = page + 1
         # Playlist name is already in user_data

    elif data.startswith('prev_page_'):
         page = context.user_data.get('page', 0)
         context.user_data['page'] = max(0, page - 1)
         # Playlist name is already in user_data
    elif data == 'back_to_playlists': # Handle back from content to list
         return await view_playlists(update, context)
    else:
        # This case should not be reached by intended flow, as delete is handled by delete_movie
        # But as a fallback, retrieve playlist name
        playlist_name = context.user_data.get('current_playlist_name')


    if not playlist_name or playlist_name not in user_playlists:
         # This means somehow we lost track of the playlist or it was deleted
         text = "خطا در بازیابی پلی‌لیست یا پلی‌لیست حذف شده است."
         keyboard = [[InlineKeyboardButton("برگشت به پلی‌لیست‌ها", callback_data='view_playlists')]]
         await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))
         return VIEW_PLAYLIST


    movies = user_playlists[playlist_name]
    if not movies:
        text = f"پلی‌لیست «{playlist_name}» خالیه!"
        keyboard = [[InlineKeyboardButton("برگشت به پلی‌لیست‌ها", callback_data='view_playlists')]]
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))
        return VIEW_PLAYLIST # Stay in playlist view state

    per_page = 5
    page = context.user_data.get('page', 0)
    start = page * per_page
    end = start + per_page
    text = f"فیلم‌های پلی‌لیست «{playlist_name}» (صفحه {page + 1}):\n"
    keyboard = []

    current_page_movies = movies[start:end]

    # Add movie titles and delete buttons for current page
    for i, movie in enumerate(current_page_movies):
        # The index for deletion must be the original index in the full list
        original_index = start + i
        text += f"{original_index + 1}. {movie['title']}\n"
        # Callback data includes playlist name and original index
        keyboard.append([InlineKeyboardButton(f"❌ حذف {movie['title']}", callback_data=f"delete_movie_{playlist_name}_{original_index}")])

    nav_buttons = []
    if start > 0:
        nav_buttons.append(InlineKeyboardButton("➡️ صفحه قبلی", callback_data=f"prev_page_{playlist_name}"))
    if end < len(movies):
        nav_buttons.append(InlineKeyboardButton("صفحه بعدی ⬅️", callback_data=f"next_page_{playlist_name}"))

    if nav_buttons:
        # Add navigation buttons on their own row(s)
        # If more than 2 nav buttons are possible, add more rows
        if len(nav_buttons) == 2:
             keyboard.append(nav_buttons)
        else:
             for btn in nav_buttons:
                 keyboard.append([btn])


    # Add back button
    keyboard.append([InlineKeyboardButton("برگشت به پلی‌لیست‌ها", callback_data='view_playlists')])


    try:
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))
    except telegram.error.BadRequest as e:
        # Handle "Message is not modified" errors which happen if the text/markup is the same
        if "Message is not modified" not in str(e):
             logger.error(f"Error editing message in view_playlist_content: {e}")
             # Optionally inform the user about the error
             # await query.message.reply_text("خطا در نمایش پلی‌لیست.")


    # Stay in DELETE_MOVIE state as this state handles both navigation and deletion within a playlist view
    return DELETE_MOVIE

# Delete movie from playlist
async def delete_movie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = str(update.effective_user.id)
    data = query.data
    await query.answer()

    # Check if it's a delete command
    if data.startswith('delete_movie_'):
        try:
            parts = data.split('_')
            # Ensure parts has enough elements: 'delete', 'movie', playlist_name, index
            if len(parts) != 4:
                 await query.edit_message_text("خطا در پردازش درخواست حذف.")
                 return DELETE_MOVIE # Stay in state or go back

            playlist_name = parts[2]
            movie_index = int(parts[3])

            user_playlists = playlists.get(user_id, {})
            if playlist_name in user_playlists and 0 <= movie_index < len(user_playlists[playlist_name]):
                deleted_movie = user_playlists[playlist_name].pop(movie_index)

                # If the playlist becomes empty, remove the playlist entry
                if not user_playlists[playlist_name]:
                    del user_playlists[playlist_name]
                    logger.info(f"Playlist '{playlist_name}' for user {user_id} deleted as it was empty.")
                    # If user has no more playlists, clear user entry
                    if not user_playlists:
                         del playlists[user_id]
                         logger.info(f"All playlists for user {user_id} deleted.")


                save_playlists(playlists)

                await query.edit_message_text(
                    text=f"✅ فیلم «{deleted_movie['title']}» از پلی‌لیست «{playlist_name}» حذف شد!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("برگشت به پلی‌لیست‌ها", callback_data='view_playlists')]
                    ])
                )
                # After deleting, ideally we should re-render the current page of the playlist
                # if there are still movies. However, simply going back to the playlist list
                # is simpler and also works. Let's stick to going back to the list for now.
                return VIEW_PLAYLIST # Go back to the list of playlists

            else:
                await query.edit_message_text(
                    text="خطا! فیلم یا پلی‌لیست پیدا نشد.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("برگشت به پلی‌لیست‌ها", callback_data='view_playlists')]
                    ])
                )
                return VIEW_PLAYLIST # Go back to list of playlists

        except (IndexError, ValueError) as e:
            logger.error(f"Error processing delete movie callback data: {e}")
            await query.edit_message_text(
                text="خطا در پردازش درخواست حذف!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("برگشت به پلی‌لیست‌ها", callback_data='view_playlists')]
                ])
            )
            return VIEW_PLAYLIST # Go back to list of playlists

    # If the callback is not a delete command (e.g., pagination),
    # let view_playlist_content handle it. This handler is called *before* delete_movie
    # in the DELETE_MOVIE state's handler list, so this block should ideally not be
    # reached for navigation callbacks if the order in states is correct.
    # However, keeping this as a fallback or for clarity:
    # if data.startswith('next_page_') or data.startswith('prev_page_'):
    #     return await view_playlist_content(update, context)

    # If we reach here, it's an unexpected callback in DELETE_MOVIE state
    logger.warning(f"Unexpected callback in DELETE_MOVIE state: {data}")
    await query.edit_message_text(
        text="درخواست نامعتبر در حالت پلی‌لیست. لطفاً دوباره تلاش کنید.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("برگشت به پلی‌لیست‌ها", callback_data='view_playlists')]
        ])
    )
    return VIEW_PLAYLIST


# Send feedback
async def send_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    # Delete the previous message with buttons and send a new message asking for feedback
    try:
        await query.delete_message()
    except telegram.error.BadRequest:
        logger.warning("Could not delete previous message when asking for feedback.")

    await context.bot.send_message(chat_id=query.message.chat_id, text="نظرت رو بنویس، مستقیم می‌فرستم به ادمین! 😎")
    return FEEDBACK

# Save and send feedback
async def save_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    feedback = update.message.text.strip()
    if not feedback:
        await update.message.reply_text("نظرت نمی‌تونه خالی باشه! یه چیزی بنویس.")
        return FEEDBACK # Stay in FEEDBACK state

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"نظر جدید از {user.first_name} (ID: {user.id}):\n{feedback}"
        )
        await update.message.reply_text("نظرت با موفقیت برای ادمین فرستاده شد! 🙌")
    except Exception as e:
        logger.error(f"Error sending feedback to admin: {e}")
        await update.message.reply_text("❌ خطا! نمی‌تونم نظرت رو بفرستم. لطفاً بعداً امتحان کن.")

    context.user_data.clear() # Clear user data after completing
    return ConversationHandler.END

# /cancel command
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("مکالمه لغو شد! دوباره با /start شروع کن.")
    return ConversationHandler.END

# Callback handler for returning to the main menu
async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    user_name = user.first_name if user.first_name else "کاربر"
    keyboard = [
        [InlineKeyboardButton("🎬 پیشنهاد فیلم", callback_data='suggest_movie')],
        [InlineKeyboardButton("📝 ارسال نظر", callback_data='send_feedback')],
        [InlineKeyboardButton("🎶 پلی‌لیست‌ها", callback_data='view_playlists')]
    ]
    message = f"سلام {user_name}! 🎉\nبه ربات پیشنهاد فیلم خوش اومدی 🍿"
    try:
        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
    except telegram.error.BadRequest:
        # Handle "Message is not modified" or other edit errors by sending a new message
         await context.bot.send_message(chat_id=query.message.chat_id, text=message, reply_markup=InlineKeyboardMarkup(keyboard))

    context.user_data.clear() # Clear conversation data when going back to main menu
    return ConversationHandler.END # End the current conversation flow


# Flask application instance
app = Flask(__name__)

# --- Webhook Endpoint ---
@app.route(WEBHOOK_PATH, methods=['POST'])
async def webhook():
    """Handle incoming Telegram updates."""
    # Check if the request contains JSON data
    if request.method == "POST" and request.is_json:
        json_data = request.get_json(force=True)
        # Convert JSON data to Telegram Update object
        update = Update.de_json(json_data, application.bot)
        # Process the update using the Application's update queue
        # We need to run this in a separate asyncio task as Flask runs in a sync thread
        # and PTB's process_update is async.
        # This requires running Flask with an async server like hypercorn or quart,
        # or using application.run_webhook with an async backend.
        # Let's switch to python-telegram-bot's recommended way using `run_webhook`
        # with an aiohttp server provided by PTB itself, which is designed for this.
        # Or, if sticking with Flask, manually run the async process_update in an event loop.
        # Manual async processing in a sync Flask thread can be tricky.
        # Let's revert to using PTB's `run_webhook` which simplifies async handling.
        # The Flask approach shown above is viable but needs careful async handling.
        # For simplicity and best practice with PTB, let's use their integrated webhook.

        # If we were to use Flask and manually handle async:
        # import asyncio
        # loop = asyncio.get_event_loop() # Get the event loop
        # loop.run_until_complete(application.process_update(update)) # Run the async process
        # This requires the loop to be running, which it is if using app.run() in a specific way
        # or an async WSGI server.

        # Let's use the simpler approach with PTB's run_webhook. This means the main
        # function will change again, removing the explicit Flask app.run().

        pass # This Flask route is not used in the final PTB webhook setup

    # Return an empty response with status code 200 to acknowledge receipt
    return jsonify({}), 200


# Main function for setting up and running the webhook using PTB's built-in features
def main():
    global application # Make application accessible globally if needed (though PTB handles it)

    # Configure the Application
    # When using webhooks with run_webhook, you don't need to specify host/port here directly,
    # they are arguments to run_webhook.
    application = Application.builder().token(TOKEN).build()

    # Add handlers as before
    application.add_handler(CommandHandler("test", test))

    # Conversation Handler
    # The entry points and states need to handle the different ways a conversation can start
    # (command or callback). Using multiple entry_points is correct.
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            # Handle callbacks that initiate a conversation from the main menu
            CallbackQueryHandler(suggest_movie, pattern='suggest_movie'),
            CallbackQueryHandler(view_playlists, pattern='view_playlists'),
            CallbackQueryHandler(send_feedback, pattern='send_feedback')
        ],
        states={
            # Define patterns for callback data to ensure correct handler is called
            GENRE: [CallbackQueryHandler(genre_selected, pattern='^(' + '|'.join(re.escape(g) for g in GENRE_LIST.keys()) + ')$')],
            MIN_IMDB_RATING: [
                CallbackQueryHandler(min_imdb_rating, pattern='^(no_rating_filter|enter_rating|suggest_movie)$'), # Added suggest_movie for back button
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_imdb_rating)
            ],
            SELECT: [CallbackQueryHandler(movie_detail, pattern='^tt\d+$')], # Matches IMDb IDs
            ADD_TO_PLAYLIST: [
                 CallbackQueryHandler(add_to_playlist, pattern='add_to_playlist'),
                 CallbackQueryHandler(add_to_playlist, pattern='back_to_movie_list')
            ],
            PLAYLIST_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_playlist)],
            VIEW_PLAYLIST: [CallbackQueryHandler(view_playlist_content, pattern='^view_playlist_.*$')],
            DELETE_MOVIE: [
                 # Handle pagination callbacks in the delete state
                 CallbackQueryHandler(view_playlist_content, pattern='^(next_page_|prev_page_).*$'),
                 # Handle delete movie callback
                 CallbackQueryHandler(delete_movie, pattern='^delete_movie_.*$'),
                 # Handle back from playlist content to playlist list
                 CallbackQueryHandler(view_playlists, pattern='^view_playlists$'),
                 # Allow re-selecting a playlist from the list (this might be redundant depending on flow)
                 # CallbackQueryHandler(view_playlist_content, pattern='^view_playlist_.*$')
            ],
            FEEDBACK: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_feedback)]
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            # Fallback for the main menu back button
            CallbackQueryHandler(back_to_main_menu, pattern='back_to_main_menu')
        ],
        persistent=False,
        per_message=True
    )

    application.add_handler(conv_handler)

    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Log Errors caused by Updates."""
        logger.error("Exception while handling an update:", exc_info=context.error)
        # traceback.print_exc() # Uncomment for detailed traceback

        # Send a message to the user
        if isinstance(update, Update):
             if update.effective_message:
                  try:
                      await update.effective_message.reply_text(
                          "❌ متاسفم، خطایی رخ داد! لطفاً دوباره امتحان کنید یا با ادمین تماس بگیرید."
                      )
                  except telegram.error.TelegramError as e:
                      logger.error(f"Failed to send error message to user: {e}")


    application.add_error_handler(error_handler)

    # --- Run the bot using Webhook ---
    # This uses PTB's built-in aiohttp server for handling webhooks
    logger.info(f"Starting webhook on port {LISTEN_PORT} and path {WEBHOOK_PATH}")
    logger.info(f"Setting webhook URL to {WEBHOOK_URL}")

    try:
        # We don't need the Flask app.run() anymore.
        # application.run_webhook starts an aiohttp server and sets the webhook.
        application.run_webhook(
            listen="0.0.0.0", # Listen on all interfaces
            port=LISTEN_PORT,
            url_path=WEBHOOK_PATH,
            webhook_url=WEBHOOK_URL
            # You might need cert and key for HTTPS if not using a reverse proxy
            # cert='fullchain.pem',
            # key='privkey.pem'
        )
    except Exception as e:
        logger.error(f"Failed to start webhook: {e}")
        # In case of webhook start failure, maybe attempt polling as a fallback?
        # Or just exit and log the error. For this example, we just log and exit.


if __name__ == '__main__':
    # Basic validation for WEBHOOK_HOST
    if WEBHOOK_HOST == "YOUR_PUBLIC_HTTPS_DOMAIN":
         logger.error("Please replace 'YOUR_PUBLIC_HTTPS_DOMAIN' with your actual public domain.")
    else:
         main()
