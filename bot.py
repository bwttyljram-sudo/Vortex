# =============================================================
#  Vortex — بوت تيليجرام متعدد النماذج
#  الشركة المطوّرة: ZenoX
# =============================================================

import os
import re
import html
import time
import threading
import telebot
import requests
import libsql
from telebot import types
from keep_alive import keep_alive

# -------------------------------------------------------------
# 1) الإعدادات الأساسية (من متغيرات البيئة)
# -------------------------------------------------------------
TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TURSO_DATABASE_URL = os.environ.get("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN")

# يوزر القناة اللي يجب على المستخدم الانضمام لها إجبارياً
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "@ZenoX_Tools")

ADMIN_ID = 6043858925  # ثابت بالكود حسب طلبك

bot = telebot.TeleBot(TOKEN)

# -------------------------------------------------------------
# 2) الاتصال بقاعدة بيانات Turso وإنشاء الجداول (أول تشغيل فقط)
# -------------------------------------------------------------
db = libsql.connect(
    database=TURSO_DATABASE_URL,
    auth_token=TURSO_AUTH_TOKEN,
)


def db_execute(sql, args=None, write=True):
    """تنفيذ استعلام SQL واحد بأمان. write=False يتخطى commit (أسرع للقراءة فقط)"""
    try:
        cur = db.execute(sql, args or ())
        if write:
            db.commit()
        return cur
    except Exception as e:
        print(f"❌ خطأ بقاعدة البيانات: {e}")
        return None


def init_db():
    db_execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     INTEGER PRIMARY KEY,
            username    TEXT,
            lang        TEXT,
            subscribed  INTEGER DEFAULT 0,
            mode        TEXT DEFAULT '',
            joined_at   TEXT DEFAULT (datetime('now')),
            last_active TEXT DEFAULT (datetime('now'))
        );
    """)
    db_execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER,
            action     TEXT,
            success    INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    print("✅ قاعدة البيانات جاهزة (Turso)")


init_db()


# -------------------------------------------------------------
# 3) دوال مساعدة للتعامل مع المستخدمين بقاعدة البيانات
# -------------------------------------------------------------

def get_user(user_id):
    cur = db_execute(
        "SELECT user_id, username, lang, subscribed, mode FROM users WHERE user_id = ?",
        [user_id], write=False,
    )
    if cur:
        row = cur.fetchone()
        if row:
            return {
                "user_id": row[0],
                "username": row[1],
                "lang": row[2],
                "subscribed": row[3],
                "mode": row[4],
            }
    return None


def get_and_touch_user(user_id):
    """يرجع بيانات المستخدم ويحدّث آخر نشاط له برحلة وحدة لقاعدة البيانات بدل اثنتين"""
    cur = db_execute(
        """UPDATE users SET last_active = datetime('now')
           WHERE user_id = ?
           RETURNING user_id, username, lang, subscribed, mode""",
        [user_id],
    )
    if cur:
        row = cur.fetchone()
        if row:
            return {
                "user_id": row[0],
                "username": row[1],
                "lang": row[2],
                "subscribed": row[3],
                "mode": row[4],
            }
    return None


def create_user(user_id, username):
    db_execute(
        "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
        [user_id, username or ""],
    )


def touch_user(user_id):
    """تحديث آخر نشاط للمستخدم - يُستدعى بس لما ما نحتاج بيانات المستخدم بنفس اللحظة"""
    db_execute("UPDATE users SET last_active = datetime('now') WHERE user_id = ?", [user_id])


def set_user_lang(user_id, lang):
    """يحدّث اللغة وآخر نشاط برحلة وحدة"""
    db_execute(
        "UPDATE users SET lang = ?, last_active = datetime('now') WHERE user_id = ?",
        [lang, user_id],
    )


def set_user_subscribed(user_id, value):
    db_execute("UPDATE users SET subscribed = ? WHERE user_id = ?", [1 if value else 0, user_id])


def set_user_mode(user_id, mode):
    db_execute("UPDATE users SET mode = ? WHERE user_id = ?", [mode, user_id])


def log_action(user_id, action, success):
    db_execute(
        "INSERT INTO logs (user_id, action, success) VALUES (?, ?, ?)",
        [user_id, action, 1 if success else 0],
    )


def get_all_user_ids():
    cur = db_execute("SELECT user_id FROM users", write=False)
    if cur:
        return [row[0] for row in cur.fetchall()]
    return []


# -------------------------------------------------------------
# 4) النصوص ثنائية اللغة (كل رسائل البوت الثابتة)
# -------------------------------------------------------------
TEXTS = {
    "ar": {
        "choose_lang": "يرجى اختيار اللغة من الأزرار بالأسفل 👇",
        "force_sub": "قبل استخدام البوت يجب عليك الانضمام للقناة والضغط على زر التحقق!",
        "btn_join": "📢 انضم إلى القناة",
        "btn_verify": "✅ تحقق",
        "not_subscribed": "عذراً، لم تشترك في القناة بعد ⚠️",
        "verified": "تم التحقق بنجاح، يمكنك استخدام البوت الآن ✅",
        "welcome_models": "مرحباً بك يا {name} في بوت <b>Vortex</b> 🚀\nاختر النموذج المناسب لك من الأزرار بالأسفل:",
        "btn_text_model": "💬 مساعد نصي",
        "btn_image_model": "🎨 توليد الصور",
        "image_prompt": "تم اختيار نموذج توليد الصور ✅\nأرسل وصف للصورة اللي تبيها وبولّدها لك.",
        "generating_image": "جاري التوليد",
        "image_error": "❌ صار خطأ أثناء توليد الصورة، حاول مرة ثانية.",
        "edit_not_supported": "⚠️ تعديل الصور الحقيقي مو متوفر بالنسخة المجانية حالياً.\nممكن أولّد لك صورة جديدة بدل منها - اكتب وصف بالنص وأسويها لك فوراً.",
        "not_started": "ابدأ أولاً بإرسال /start",
        "admin_only": "هذا الأمر مخصص للأدمن فقط.",
        "broadcast_ask": "أرسل الآن محتوى الرسالة اللي تبي تذيعها لكل المستخدمين:",
        "broadcast_done": "✅ تم إرسال الإذاعة إلى {count} مستخدم بنجاح ({failed} فشل).",
    },
    "en": {
        "choose_lang": "Please choose your language from the buttons below 👇",
        "force_sub": "Before using the bot, you must join the channel and press the verify button!",
        "btn_join": "📢 Join Channel",
        "btn_verify": "✅ Verify",
        "not_subscribed": "Sorry, you haven't joined the channel yet ⚠️",
        "verified": "Verified successfully, you can now use the bot ✅",
        "welcome_models": "Welcome {name} to <b>Vortex</b> 🚀\nChoose the model that suits you from the buttons below:",
        "btn_text_model": "💬 Text Assistant",
        "btn_image_model": "🎨 Image Generation",
        "image_prompt": "Image generation model selected ✅\nSend a description of the image you want and I'll generate it.",
        "generating_image": "Generating",
        "image_error": "❌ Something went wrong generating the image, try again.",
        "edit_not_supported": "⚠️ Real photo editing isn't available on the free tier right now.\nI can generate a brand-new image instead - just type a description.",
        "not_started": "Please start first by sending /start",
        "admin_only": "This command is for the admin only.",
        "broadcast_ask": "Now send the message content you want to broadcast to all users:",
        "broadcast_done": "✅ Broadcast sent to {count} users successfully ({failed} failed).",
    },
}


def t(lang, key, **kwargs):
    lang = lang if lang in TEXTS else "ar"
    text = TEXTS[lang][key]
    return text.format(**kwargs) if kwargs else text


# -------------------------------------------------------------
# 5) اختيار نموذج الدردشة النصي (يجرب فعلياً بدل التخمين من الاسم)
# -------------------------------------------------------------
PREFERRED_MODELS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.6-27b",
]


def test_model(model_id):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    data = {
        "model": model_id,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 5,
    }
    try:
        r = requests.post(url, headers=headers, json=data, timeout=10)
        return r.status_code == 200
    except Exception:
        return False


def get_chat_model():
    for model_id in PREFERRED_MODELS:
        print(f"🔍 تجربة النموذج: {model_id}")
        if test_model(model_id):
            print(f"✅ تم اختيار النموذج: {model_id}")
            return model_id
    print("⚠️ ما اشتغل أي نموذج، رجعنا للاحتياطي")
    return "openai/gpt-oss-20b"


ACTIVE_MODEL = get_chat_model()

# -------------------------------------------------------------
# 6) هوية البوت + قواعد التنسيق (System Prompt)
# -------------------------------------------------------------
def build_system_prompt(lang):
    lang_name = "Arabic" if lang == "ar" else "English"
    return {
        "role": "system",
        "content": (
            f"You are Vortex, an AI assistant built by ZenoX — a company focused on building "
            f"AI tools and solutions. If anyone asks your name, always say you are 'Vortex'. "
            f"If anyone asks who made you, say 'ZenoX'. Never mention Groq, OpenAI, gpt-oss, "
            f"Llama, or any underlying model/provider — that information is private.\n\n"
            f"Always reply in {lang_name}, regardless of the language the user writes in.\n\n"
            "PERSONALITY — this matters a lot:\n"
            "You are sharp and analytical, not a stiff corporate assistant. Before answering, "
            "silently parse exactly what the user is actually asking — don't guess, don't go off "
            "on tangents, don't answer a question they didn't ask. If the request is genuinely "
            "ambiguous, ask ONE short clarifying question instead of dumping assumptions.\n"
            "Talk like a smart friend who happens to know a lot — natural, warm, a little casual. "
            "Not overly formal, not stiff, no corporate filler ('I hope this helps!', 'Certainly! "
            "I'd be happy to...'). Just answer like a real person would.\n"
            "Explain things simply enough that a child could follow the logic — short words, short "
            "sentences, no jargon unless the user used it first. Simple does not mean shallow: "
            "give the real, accurate, useful answer, just said plainly and clearly.\n\n"
            "FORMATTING RULES (write simple Markdown — the system converts it, don't write HTML tags yourself):\n"
            "1. Never use #, ##, ### headers. Use a short **bold** line instead.\n"
            "2. Use **bold** (double asterisks) sparingly for emphasis or section titles.\n"
            "3. Use '•' bullets for lists, or 1. 2. 3. for steps.\n"
            "4. Wrap code in triple backticks for code blocks.\n"
            "5. Keep paragraphs short (2-3 lines). Use relevant emojis moderately (✅ 💡 ⚠️ 🚀).\n"
            "6. Give a direct one-line answer first, then supporting details only if needed.\n"
            "7. Never write raw HTML tags like <b> or <i> — use ** for bold instead.\n"
            "Be direct and precise — skip disclaimers, skip restating the question, skip filler."
        ),
    }


def markdown_to_telegram_html(text):
    """يحول مخرجات النموذج (ماركداون عادي) لصيغة HTML يفهمها تيليجرام بشكل موثوق"""
    text = html.escape(text, quote=False)

    # كتل الأكواد ```lang\ncode``` → <pre><code class="language-lang">
    def code_block_repl(m):
        lang_tag = m.group(1)
        code = m.group(2)
        if lang_tag:
            return f'<pre><code class="language-{lang_tag}">{code}</code></pre>'
        return f"<pre>{code}</pre>"

    text = re.sub(r'```(\w+)?\n?(.*?)```', code_block_repl, text, flags=re.DOTALL)
    # كود مضمّن `code`
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    # عناوين # ## ### → سطر بولد
    text = re.sub(r'^#{1,6}\s*(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)
    # **bold** → <b>bold</b>
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    # روابط [نص](رابط) → <a href="رابط">نص</a>
    text = re.sub(r'\[([^\]]+)\]\((https?://[^\s)]+)\)', r'<a href="\2">\1</a>', text)
    # قوائم - أو * → •  (نتجنب أسطر الأكواد عشان ما نخرب المسافات البادئة بالكود)
    text = re.sub(r'^\s*[-*]\s+', '• ', text, flags=re.MULTILINE)
    # أسطر فاضية زايدة
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def safe_send(chat_id, text, reply_markup=None, reply_to_message_id=None):
    """إرسال رسالة بـ HTML، مع نسخة احتياطية نص عادي لو صار خطأ تنسيق غير متوقع"""
    try:
        return bot.send_message(
            chat_id, text, parse_mode="HTML",
            reply_markup=reply_markup, reply_to_message_id=reply_to_message_id,
        )
    except Exception as e:
        print(f"⚠️ فشل إرسال HTML، نرسل نص عادي بدل: {e}")
        plain = re.sub(r'<[^>]+>', '', text)
        return bot.send_message(
            chat_id, plain, reply_markup=reply_markup, reply_to_message_id=reply_to_message_id,
        )


def safe_edit(chat_id, message_id, text, reply_markup=None):
    """تعديل رسالة بـ HTML، مع نسخة احتياطية نص عادي لو صار خطأ تنسيق غير متوقع"""
    try:
        bot.edit_message_text(text, chat_id=chat_id, message_id=message_id,
                               parse_mode="HTML", reply_markup=reply_markup)
    except Exception as e:
        print(f"⚠️ فشل تعديل HTML، نرسل نص عادي بدل: {e}")
        plain = re.sub(r'<[^>]+>', '', text)
        try:
            bot.edit_message_text(plain, chat_id=chat_id, message_id=message_id, reply_markup=reply_markup)
        except Exception:
            pass


def ask_ai(text, lang):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    data = {
        "model": ACTIVE_MODEL,
        "messages": [build_system_prompt(lang), {"role": "user", "content": text}],
    }
    try:
        r = requests.post(url, headers=headers, json=data, timeout=30)
        if r.status_code == 200:
            raw = r.json()["choices"][0]["message"]["content"]
            return markdown_to_telegram_html(raw), True
        return f"❌ API Error ({r.status_code})", False
    except Exception as e:
        return f"❌ Server Error: {str(e)}", False


# -------------------------------------------------------------
# 7.5) أنيميشن "•••" متحركة أثناء انتظار رد النموذج أو الصورة
# -------------------------------------------------------------
def _animate_dots(chat_id, message_id, stop_event):
    frames = ["•", "• •", "• • •"]
    i = 0
    while not stop_event.is_set():
        try:
            bot.edit_message_text(frames[i % len(frames)], chat_id=chat_id, message_id=message_id)
        except Exception:
            pass  # نتجاهل أخطاء التعديل المتكررة (نفس النص مثلاً) بدون ما نوقف الأنيميشن
        i += 1
        stop_event.wait(0.6)


def start_wait_animation(chat_id):
    msg = bot.send_message(chat_id, "•")
    stop_event = threading.Event()
    th = threading.Thread(target=_animate_dots, args=(chat_id, msg.message_id, stop_event))
    th.daemon = True
    th.start()
    return msg, stop_event, th


def stop_wait_animation(stop_event, th):
    stop_event.set()
    th.join(timeout=2)


# -------------------------------------------------------------
# 7) توليد الصور عبر Pollinations.ai (مجاني بالكامل، بدون مفتاح)
# -------------------------------------------------------------
def generate_image(prompt):
    """يرجع bytes الصورة، أو None لو صار خطأ. يجرب 'flux' ثم 'turbo' كاحتياطي، مع إعادة محاولة."""
    from urllib.parse import quote

    encoded_prompt = quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}"

    for model in ["flux", "turbo"]:
        params = {
            "model": model,
            "width": 1024,
            "height": 1024,
            "nologo": "true",
        }
        for attempt in range(2):  # محاولتين لكل نموذج
            try:
                r = requests.get(url, params=params, timeout=45)
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                    return r.content
                print(f"❌ Pollinations [{model}] محاولة {attempt+1}: status={r.status_code}")
            except Exception as e:
                print(f"❌ Pollinations [{model}] محاولة {attempt+1}: {e}")
            time.sleep(1.5)

    return None


# -------------------------------------------------------------
# 8) فحص الاشتراك الإجباري بالقناة
# -------------------------------------------------------------
def is_user_subscribed(user_id):
    try:
        member = bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        print(f"⚠️ خطأ بفحص الاشتراك: {e}")
        return False


# -------------------------------------------------------------
# 9) لوحات الأزرار (Keyboards)
# -------------------------------------------------------------
def lang_keyboard():
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("🇸🇦 العربية", callback_data="lang_ar"),
        types.InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
    )
    return kb


def force_sub_keyboard(lang):
    kb = types.InlineKeyboardMarkup()
    channel_url = f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}"
    kb.add(types.InlineKeyboardButton(t(lang, "btn_join"), url=channel_url))
    kb.add(types.InlineKeyboardButton(t(lang, "btn_verify"), callback_data="verify_sub"))
    return kb


def models_keyboard(lang):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton(t(lang, "btn_text_model"), callback_data="model_text"),
        types.InlineKeyboardButton(t(lang, "btn_image_model"), callback_data="model_image"),
    )
    return kb


# -------------------------------------------------------------
# 10) أمر /start
# -------------------------------------------------------------
@bot.message_handler(commands=["start"])
def handle_start(message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    name = message.from_user.first_name or "there"

    create_user(user_id, username)
    user = get_and_touch_user(user_id)

    # مستخدم عائد عنده لغة محفوظة مسبقاً - نتجاوز اختيار اللغة كلياً
    if user and user["lang"]:
        lang = user["lang"]

        # نفحص الاشتراك فعلياً بدل ما نفترض من القاعدة (ممكن يكون طلع من القناة)
        if is_user_subscribed(user_id):
            set_user_subscribed(user_id, True)
            bot.send_message(
                message.chat.id, t(lang, "welcome_models", name=name),
                parse_mode="HTML", reply_markup=models_keyboard(lang),
            )
        else:
            set_user_subscribed(user_id, False)
            bot.send_message(message.chat.id, t(lang, "force_sub"), reply_markup=force_sub_keyboard(lang))
        return

    # مستخدم جديد كلياً - أول مرة يشوف البوت
    text = f"{TEXTS['ar']['choose_lang']}\n\n{TEXTS['en']['choose_lang']}"
    bot.send_message(message.chat.id, text, reply_markup=lang_keyboard())


# -------------------------------------------------------------
# 11) أمر /lang - تغيير اللغة يدوياً بأي وقت
# -------------------------------------------------------------
@bot.message_handler(commands=["lang"])
def handle_lang(message):
    text = f"{TEXTS['ar']['choose_lang']}\n\n{TEXTS['en']['choose_lang']}"
    bot.send_message(message.chat.id, text, reply_markup=lang_keyboard())


# -------------------------------------------------------------
# 12) معالج كل الأزرار (Callback Queries)
# -------------------------------------------------------------
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    user_id = call.from_user.id

    # --- تحديث لوحة الإحصائيات (أدمن فقط) ---
    if call.data == "refresh_stats":
        if call.from_user.id != ADMIN_ID:
            bot.answer_callback_query(call.id, t("ar", "admin_only"), show_alert=True)
            return
        try:
            bot.edit_message_text(
                build_stats_text(),
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode="HTML",
                reply_markup=stats_keyboard(),
            )
            bot.answer_callback_query(call.id, "✅ تحديث")
        except Exception:
            # الأرقام ما تغيرت من آخر تحديث (نفس النص بالضبط) - تيليجرام يرفض تعديل بنفس المحتوى
            bot.answer_callback_query(call.id, "✅ محدّث أصلاً")
        return

    # --- اختيار اللغة --- (set_user_lang يحدّث آخر نشاط بنفس الرحلة)
    if call.data in ["lang_ar", "lang_en"]:
        lang = "ar" if call.data == "lang_ar" else "en"
        set_user_lang(user_id, lang)

        # نفحص الاشتراك فعلياً - لو مشترك أصلاً نتجاوز رسالة الاشتراك الإجباري كلياً
        if is_user_subscribed(user_id):
            set_user_subscribed(user_id, True)
            name = call.from_user.first_name or "there"
            bot.edit_message_text(
                t(lang, "welcome_models", name=name),
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode="HTML",
                reply_markup=models_keyboard(lang),
            )
        else:
            bot.edit_message_text(
                t(lang, "force_sub"),
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=force_sub_keyboard(lang),
            )
        bot.answer_callback_query(call.id)
        return

    # --- التحقق من الاشتراك ---
    if call.data == "verify_sub":
        user = get_and_touch_user(user_id)
        lang = user["lang"] if user and user["lang"] else "ar"

        if is_user_subscribed(user_id):
            set_user_subscribed(user_id, True)
            bot.answer_callback_query(call.id, t(lang, "verified"), show_alert=True)
            name = call.from_user.first_name or "there"
            bot.edit_message_text(
                t(lang, "welcome_models", name=name),
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode="HTML",
                reply_markup=models_keyboard(lang),
            )
        else:
            bot.answer_callback_query(call.id, t(lang, "not_subscribed"), show_alert=True)
        return

    # --- اختيار نموذج المساعد النصي ---
    if call.data == "model_text":
        user = get_and_touch_user(user_id)
        lang = user["lang"] if user and user["lang"] else "ar"
        set_user_mode(user_id, "text")

        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.send_chat_action(call.message.chat.id, "typing")

        intro_prompt = (
            "Greet the user warmly and briefly introduce yourself and what you can help with "
            "(writing, analysis, coding, general questions). Keep it short and inviting."
        )
        reply, success = ask_ai(intro_prompt, lang)
        log_action(user_id, "text_request", success)
        safe_send(call.message.chat.id, reply)
        bot.answer_callback_query(call.id)
        return

    # --- اختيار نموذج توليد الصور ---
    if call.data == "model_image":
        user = get_and_touch_user(user_id)
        lang = user["lang"] if user and user["lang"] else "ar"
        set_user_mode(user_id, "image")

        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.send_message(call.message.chat.id, t(lang, "image_prompt"))
        bot.answer_callback_query(call.id)
        return


# -------------------------------------------------------------
# 13) أمر الإحصائيات (للأدمن فقط)
# -------------------------------------------------------------
def _count(sql):
    cur = db_execute(sql, write=False)
    if cur:
        row = cur.fetchone()
        return row[0] if row else 0
    return 0


def build_stats_text():
    total_users = _count("SELECT COUNT(*) FROM users")
    active_now = _count("SELECT COUNT(*) FROM users WHERE last_active >= datetime('now','-5 minutes')")
    active_today = _count("SELECT COUNT(*) FROM users WHERE last_active >= datetime('now','-1 day')")
    active_7d = _count("SELECT COUNT(*) FROM users WHERE last_active >= datetime('now','-7 day')")
    active_30d = _count("SELECT COUNT(*) FROM users WHERE last_active >= datetime('now','-30 day')")

    req_today = _count("SELECT COUNT(*) FROM logs WHERE created_at >= datetime('now','-1 day')")
    req_7d = _count("SELECT COUNT(*) FROM logs WHERE created_at >= datetime('now','-7 day')")
    req_30d = _count("SELECT COUNT(*) FROM logs WHERE created_at >= datetime('now','-30 day')")

    img_today = _count("SELECT COUNT(*) FROM logs WHERE action='image_request' AND created_at >= datetime('now','-1 day')")
    img_7d = _count("SELECT COUNT(*) FROM logs WHERE action='image_request' AND created_at >= datetime('now','-7 day')")
    img_30d = _count("SELECT COUNT(*) FROM logs WHERE action='image_request' AND created_at >= datetime('now','-30 day')")

    total_logs = _count("SELECT COUNT(*) FROM logs")
    success_logs = _count("SELECT COUNT(*) FROM logs WHERE success = 1")
    success_rate = round((success_logs / total_logs) * 100, 1) if total_logs > 0 else 100.0

    updated_at = time.strftime("%H:%M:%S", time.gmtime())

    return f"""<b>📊 إحصائيات Vortex</b>

<b>🟢 نشطون الآن (آخر 5 دقائق)</b>: {active_now}

<b>👥 المستخدمين</b>
• الإجمالي: {total_users}
• نشط اليوم: {active_today}
• نشط آخر 7 أيام: {active_7d}
• نشط آخر 30 يوم: {active_30d}

<b>💬 الطلبات (نص + صور)</b>
• اليوم: {req_today}
• آخر 7 أيام: {req_7d}
• آخر 30 يوم: {req_30d}

<b>🎨 طلبات توليد الصور</b>
• اليوم: {img_today}
• آخر 7 أيام: {img_7d}
• آخر 30 يوم: {img_30d}

<b>✅ نسبة نجاح الطلبات</b>: {success_rate}%

<i>آخر تحديث: {updated_at} UTC</i>
"""


def stats_keyboard():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔄 تحديث", callback_data="refresh_stats"))
    return kb


@bot.message_handler(commands=["stats"])
def handle_stats(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, t("ar", "admin_only"))
        return
    bot.send_message(message.chat.id, build_stats_text(), parse_mode="HTML", reply_markup=stats_keyboard())


# -------------------------------------------------------------
# 14) أمر الإذاعة (للأدمن فقط) - إرسال تدريجي يحمي البوت من الحظر
# -------------------------------------------------------------
pending_broadcast = {}  # {admin_id: True} لحالة انتظار محتوى الإذاعة


@bot.message_handler(commands=["broadcast"])
def handle_broadcast_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, t("ar", "admin_only"))
        return
    pending_broadcast[message.from_user.id] = True
    bot.reply_to(message, t("ar", "broadcast_ask"))


def run_broadcast(content_message):
    user_ids = get_all_user_ids()
    sent, failed = 0, 0

    for uid in user_ids:
        try:
            bot.copy_message(uid, content_message.chat.id, content_message.message_id)
            sent += 1
        except Exception:
            failed += 1
        time.sleep(0.05)  # ~20 رسالة/ثانية، حماية من حظر تيليجرام
        if sent % 25 == 0:
            time.sleep(1)  # وقفة إضافية كل 25 رسالة

    bot.send_message(
        ADMIN_ID,
        t("ar", "broadcast_done", count=sent, failed=failed),
    )


# -------------------------------------------------------------
# 15) معالج الرسائل النصية العادية
# -------------------------------------------------------------
@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(message):
    if message.text.startswith("/"):
        return  # الأوامر تُعالج بمعالجاتها الخاصة

    # اعتراض محتوى الإذاعة لو الأدمن بانتظار إرساله
    if message.from_user.id == ADMIN_ID and pending_broadcast.get(ADMIN_ID):
        pending_broadcast[ADMIN_ID] = False
        threading.Thread(target=run_broadcast, args=(message,)).start()
        return

    user_id = message.from_user.id
    user = get_and_touch_user(user_id)

    if not user or not user["lang"]:
        bot.reply_to(message, "AR: ابدأ أولاً بإرسال /start\nEN: Please start with /start")
        return

    lang = user["lang"]

    if not user["subscribed"]:
        bot.send_message(message.chat.id, t(lang, "force_sub"), reply_markup=force_sub_keyboard(lang))
        return

    mode = user["mode"]

    if mode == "image":
        wait_msg, stop_event, th = start_wait_animation(message.chat.id)
        bot.send_chat_action(message.chat.id, "upload_photo")

        image_bytes = generate_image(message.text)
        log_action(user_id, "image_request", image_bytes is not None)
        stop_wait_animation(stop_event, th)

        try:
            bot.delete_message(message.chat.id, wait_msg.message_id)
        except Exception:
            pass

        if image_bytes:
            bot.send_photo(message.chat.id, image_bytes)
        else:
            bot.send_message(message.chat.id, t(lang, "image_error"))

    else:  # mode == "text" أو فاضي (افتراضي نصي)
        wait_msg, stop_event, th = start_wait_animation(message.chat.id)
        bot.send_chat_action(message.chat.id, "typing")

        reply, success = ask_ai(message.text, lang)
        log_action(user_id, "text_request", success)
        stop_wait_animation(stop_event, th)

        safe_edit(message.chat.id, wait_msg.message_id, reply)


# -------------------------------------------------------------
# 16) معالج الصور المرسلة (للتعديل، لما يكون وضع "توليد الصور")
# -------------------------------------------------------------
@bot.message_handler(content_types=["photo"])
def handle_photo(message):
    # اعتراض محتوى الإذاعة لو كانت صورة والأدمن بانتظار إرسالها
    if message.from_user.id == ADMIN_ID and pending_broadcast.get(ADMIN_ID):
        pending_broadcast[ADMIN_ID] = False
        threading.Thread(target=run_broadcast, args=(message,)).start()
        return

    user_id = message.from_user.id
    user = get_and_touch_user(user_id)

    if not user or not user["lang"] or not user["subscribed"]:
        return

    lang = user["lang"]

    if user["mode"] != "image":
        return

    # التعديل الحقيقي على الصور مو متوفر بالنسخة المجانية - نوضح هذا صراحة
    bot.reply_to(message, t(lang, "edit_not_supported"))


# -------------------------------------------------------------
# 17) قائمة الأوامر (مختلفة للأدمن عن المستخدم العادي)
# -------------------------------------------------------------
def setup_commands():
    # الأوامر الافتراضية لكل المستخدمين
    default_commands = [
        types.BotCommand("start", "رسالة البدء"),
        types.BotCommand("lang", "اللغات / Languages"),
    ]
    bot.set_my_commands(default_commands)

    # أوامر إضافية تظهر للأدمن فقط
    admin_commands = default_commands + [
        types.BotCommand("stats", "📊 إحصائيات البوت"),
        types.BotCommand("broadcast", "📢 إذاعة رسالة"),
    ]
    try:
        bot.set_my_commands(admin_commands, scope=types.BotCommandScopeChat(chat_id=ADMIN_ID))
    except Exception as e:
        print(f"⚠️ ما قدرنا نضبط أوامر الأدمن (طبيعي لو الأدمن ما بدأ محادثة بعد): {e}")


# -------------------------------------------------------------
# 18) نقطة التشغيل الرئيسية
# -------------------------------------------------------------
if __name__ == "__main__":
    print("🚀 جاري تشغيل سيرفر Keep-Alive...")
    keep_alive()

    setup_commands()

    print(f"✅ Vortex يعمل الآن — النموذج النشط: {ACTIVE_MODEL}")
    bot.infinity_polling(skip_pending=True)


