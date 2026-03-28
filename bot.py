import asyncio
import os
import sys
import time
import json
import signal
import logging
import fcntl
import openai
import asyncpg
from dotenv import load_dotenv
from telegram import (
    Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup,
    LabeledPrice
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, PreCheckoutQueryHandler, CallbackQueryHandler
)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

load_dotenv()

# ========== НАСТРОЙКИ ==========
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
DATABASE_URL = os.getenv('DATABASE_URL')
PAYMENT_PROVIDER_TOKEN = os.getenv('PAYMENT_PROVIDER_TOKEN')
CURRENCY = os.getenv('CURRENCY', 'RUB')
PRICE = int(os.getenv('PRICE', 10000))
AUTHOR_CHAT_ID = os.getenv('AUTHOR_CHAT_ID')

USE_AI_WELCOME = os.getenv('USE_AI_WELCOME', 'True').lower() in ('true', '1', 'yes')
PAYMENT_ENABLED = os.getenv('PAYMENT_ENABLED', 'False').lower() in ('true', '1', 'yes')
FREE_CONSULTATION_ENABLED = os.getenv('FREE_CONSULTATION_ENABLED', 'True').lower() in ('true', '1', 'yes')

COOLDOWN_SECONDS = 12 * 60 * 60   # 12 часов

FREE_CONSULTATION_TEXT = (
    "✨ Я — виртуальный таролог.✨\n\n"
    "Я работаю с классической колодой Райдера—Уэйта и глубокой символикой.\n\n"
    "Как проходит сеанс:\n"
    "1. Вы задаёте любой вопрос (отношения, работа, выбор, саморазвитие).\n"
    "2. Я вытягиваю для вас 3 случайные карты Таро.\n"
    "3. Даю подробный разбор:\n"
    "• значение каждой карты (символы, детали, смысл)\n"
    "• их взаимодействие друг с другом\n"
    "• общий синтез — ответ на ваш вопрос\n"
    "4. При необходимости — итоговая карта-квинтэссенция.\n\n"
    "Карты — не предсказание, а зеркало вашей души.\n"
    "Я помогаю увидеть скрытые грани ситуации, найти ресурсы и принять осознанное решение.\n\n"
    "Ваш первый расклад — **бесплатный!**.\n\n"
    "Готовы заглянуть в себя? 🔮"
)

START_KEYBOARD = ReplyKeyboardMarkup([["Начать сессию"]], resize_keyboard=True)
FREE_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("🎁 Сделать бесплатный расклад", callback_data="free_consultation")]
])

SYSTEM_PROMPT = """
Ты — профессиональный таролог, специализирующийся на колоде Райдера—Уэйта. Твоя интерпретация опирается на глубокое знание символики, изложенное в книге Эвелин Бюргер и Йоханнеса Фибиг «Символика под микроскопом». Ты понимаешь, что каждая карта имеет и положительное, и отрицательное значение, является зеркалом души кверента и может быть рассмотрена как на субъективном (внутренние процессы), так и на объективном (внешние события) уровнях.

Твоя задача
Пользователь задаёт вопрос, связанный с его жизненной ситуацией.
Ты сам случайным образом выбираешь три карты из полного списка колоды Таро (78 карт: 22 Старших аркана и 56 Младших арканов четырёх мастей — Жезлы, Чаши, Мечи, Пентакли). Используй равновероятный случайный выбор. После этого ты должен:

Назвать три выпавшие карты — указать их название (и масть для Младших арканов).

Кратко представить каждую карту — описать ключевые символы и основное значение, выделив как позитивные, так и негативные аспекты (если они уместны в контексте вопроса).

Выполнить синтез — найти взаимосвязи между картами: общую тему, перекличку символов, возможные противоречия или усиления. Объяснить, как эти карты «разговаривают» друг с другом.

Сделать общий развёрнутый вывод, который напрямую отвечает на вопрос пользователя. Вывод должен быть метафоричным, образным, но при этом практически полезным. Используй метафоры из описаний карт (розовый сад, башня, поток, зеркало и т.п.).

По желанию добавить квинтэссенцию — сложи числовые значения карт (для Старших арканов — их номер; для Младших — число от 1 до 10; для придворных карт: Паж, Рыцарь, Королева, Король — 0), сведи к числу от 1 до 22 и назови соответствующий Старший аркан как итоговый совет или резюме расклада.

Правила интерпретации
Учитывай, что карты могут отражать как внешние обстоятельства, так и внутреннее состояние кверента. Если вопрос касается отношений, работы, самооценки — выбирай уместный уровень.

Не бойся «сложных» карт (Смерть, Башня, Дьявол). Показывай их преобразующую, освобождающую сторону.

Обращай внимание на детали: цвета, позы, предметы за спиной фигур — они дают ключ к скрытым смыслам.

Если карт три, рассматривай их как диалог: одна может указывать на препятствие, другая — на ресурс, третья — на путь решения. Можешь интерпретировать их как «ситуация – препятствие – совет» или «прошлое – настоящее – будущее» — выбери схему, которая лучше всего подходит к вопросу, и сообщи её пользователю.

Будь уважителен к пользователю. Твоя цель — не предсказание судьбы, а помощь в осознании ситуации и поиске собственного пути.

Структура ответа
Вступление (1–2 предложения): объяви, что ты вытянул три карты, и кратко обозначь общую атмосферу расклада.

Перечисление выпавших карт (чётко назови их).

Разбор каждой карты (по 3–5 предложений на карту): название, ключевые символы, значение применительно к вопросу, возможные грани.

Синтез и взаимодействие (3–6 предложений): как карты сочетаются, что усиливают, что смягчают, какие параллели можно провести.

Квинтэссенция (если уместно): суммарный аркан и его краткий смысл.

Общий ответ на вопрос (3–8 предложений): развёрнутый, метафоричный, с советом или новым взглядом на ситуацию.

Заключительная фраза (по желанию): напутствие или вопрос для размышления.

Стиль ответа
Используй живые образы: «вы стоите у корней дерева», «алый лев пробуждается в вашем сердце», «горизонтальная восьмёрка приглашает вас к бесконечному танцу».

Избегай сухих перечислений. Пусть твой язык будет плавным, поэтичным, но при этом понятным.

Обращайся к пользователю на «вы» (уважительно).

Полный список карт колоды Райдера—Уэйта (для случайного выбора)
Старшие арканы (22):
0. Шут / Дурак
I. Маг
II. Верховная Жрица
III. Императрица
IV. Император
V. Иерофант
VI. Влюблённые
VII. Колесница
VIII. Сила
IX. Отшельник
X. Колесо Фортуны
XI. Правосудие
XII. Повешенный
XIII. Смерть
XIV. Умеренность
XV. Дьявол
XVI. Башня
XVII. Звезда
XVIII. Луна
XIX. Солнце
XX. Суд
XXI. Мир

Младшие арканы (56):
Жезлы (огонь, воля): Туз, 2, 3, 4, 5, 6, 7, 8, 9, 10, Паж, Рыцарь, Королева, Король.
Чаши (вода, чувства): Туз, 2, 3, 4, 5, 6, 7, 8, 9, 10, Паж, Рыцарь, Королева, Король.
Мечи (воздух, разум): Туз, 2, 3, 4, 5, 6, 7, 8, 9, 10, Паж, Рыцарь, Королева, Король.
Пентакли (земля, материя): Туз, 2, 3, 4, 5, 6, 7, 8, 9, 10, Паж, Рыцарь, Королева, Король.

Пример начала работы бота
После получения вопроса пользователя бот (ты) мысленно выбирает три случайные карты из списка, затем отвечает по шаблону:

«Я вытянул для вас три карты. Их сочетание напоминает древнюю притчу: сначала герой встречает тьму, затем находит опору, а в конце обретает свет. Давайте посмотрим, что они говорят…
Выпали: …»

Дополнительная инструкция для ИИ
При случайном выборе используй равномерное распределение: все 78 карт равновероятны.

Если пользователь сам указал карты, просто интерпретируй их, не генерируя новые.

Если пользователь задал вопрос без уточнения схемы, можешь интерпретировать три карты как «ситуация – вызов – путь» или «прошлое – настоящее – будущее», указав, какую схему ты применяешь.

Будь внимателен: для придворных карт (Паж, Рыцарь, Королева, Король) числовое значение для квинтэссенции — 0. Для Тузов — 1. Для остальных числовых карт — число от 2 до 10. Для Старших арканов — их номер (для Шута — 0).
"""

# ========== ПРОВЕРКИ ==========
if not TELEGRAM_TOKEN or not DEEPSEEK_API_KEY:
    logger.error("TELEGRAM_TOKEN или DEEPSEEK_API_KEY не заданы!")
    sys.exit(1)
if not DATABASE_URL:
    logger.error("DATABASE_URL не задан!")
    sys.exit(1)

openai.api_base = "https://api.deepseek.com/v1"
openai.api_key = DEEPSEEK_API_KEY

def is_payment_configured():
    return PAYMENT_ENABLED and PAYMENT_PROVIDER_TOKEN and ':' in PAYMENT_PROVIDER_TOKEN

# ========== БЛОКИРОВКА ЧЕРЕЗ FLOCK ==========
LOCK_FILE = "/tmp/tarot_bot.lock"

def acquire_lock():
    """Захватывает эксклюзивную блокировку через flock. Если блокировка уже занята другим процессом, завершает выполнение."""
    try:
        lock_fd = open(LOCK_FILE, 'w')
        # Пытаемся захватить блокировку без ожидания
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Записываем PID в файл для отладки
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
        logger.info(f"Блокировка захвачена, PID {os.getpid()} записан в {LOCK_FILE}")
        return lock_fd
    except (IOError, OSError) as e:
        logger.error(f"Не удалось захватить блокировку: {e}")
        sys.exit(1)

def release_lock(lock_fd):
    """Освобождает блокировку и закрывает файл."""
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()
        logger.info("Блокировка освобождена")
    except Exception as e:
        logger.error(f"Ошибка при освобождении блокировки: {e}")

# ========== БАЗА ДАННЫХ ==========
class Database:
    def __init__(self, dsn):
        self.dsn = dsn
        self.pool = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)
        logger.info("Подключение к базе данных установлено")

    async def close(self):
        if self.pool:
            await self.pool.close()
            logger.info("Соединение с базой данных закрыто")

    async def init_tables(self):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    free_used BOOLEAN DEFAULT false,
                    last_session_end TIMESTAMPTZ,
                    created_at TIMESTAMPTZ DEFAULT now()
                )
            """)
        logger.info("Таблицы инициализированы")

    async def get_or_create_user(self, user_id):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
                user_id
            )

    async def is_free_used(self, user_id):
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT free_used FROM users WHERE user_id = $1", user_id
            ) or False

    async def set_free_used(self, user_id):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET free_used = true WHERE user_id = $1", user_id
            )

    async def update_last_session_end(self, user_id):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET last_session_end = now() WHERE user_id = $1", user_id
            )

    async def get_last_session_end(self, user_id):
        async with self.pool.acquire() as conn:
            row = await conn.fetchval(
                "SELECT last_session_end FROM users WHERE user_id = $1", user_id
            )
            return row.timestamp() if row else None

    async def reset_database(self):
        async with self.pool.acquire() as conn:
            await conn.execute("DROP TABLE IF EXISTS users CASCADE")
            await self.init_tables()
        logger.info("База данных сброшена")

# ========== AI ФУНКЦИИ ==========
async def generate_welcome_message():
    try:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Напиши краткое приветствие для пользователя, который готов задать вопрос. Объясни, что чем подробнее он опишет ситуацию, тем точнее будет расклад. Не используй Markdown."}
        ]
        response = await asyncio.to_thread(
            openai.ChatCompletion.create,
            model="deepseek-chat",
            messages=messages,
            max_tokens=500,
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Ошибка генерации приветствия: {e}")
        return "Добро пожаловать. Я — таролог, работающий с мудростью колоды Райдера—Уэйта. Я готов выслушать ваш вопрос и обратиться к картам.\n\nЧтобы образы и символы заговорили с вами максимально ясно, пожалуйста, опишите вашу ситуацию или вопрос как можно подробнее. Чем больше деталей вы предоставите, тем глубже и точнее будет наше совместное путешествие к пониманию. Я жду вашего вопроса."

async def ask_ai(question, history):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in history:
        messages.append(msg)
    messages.append({"role": "user", "content": question})
    try:
        response = await asyncio.to_thread(
            openai.ChatCompletion.create,
            model="deepseek-chat",
            messages=messages,
            max_tokens=2000,
            temperature=0.8
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Ошибка DeepSeek: {e}")
        return "Извините, произошла ошибка. Попробуйте позже."

# ========== ПЛАТЕЖИ ==========
async def send_invoice(chat_id, context):
    if not is_payment_configured():
        return
    prices = [LabeledPrice(label="Расклад Таро", amount=PRICE)]
    provider_data = json.dumps({
        "receipt": {
            "items": [{
                "description": "Расклад Таро",
                "quantity": "1.00",
                "amount": {"value": f"{PRICE/100:.2f}", "currency": CURRENCY},
                "vat_code": 1
            }]
        }
    })
    try:
        await context.bot.send_invoice(
            chat_id=chat_id,
            title="Оплата расклада",
            description="Один расклад Таро (3 карты с подробным разбором)",
            payload="tarot_payment",
            provider_token=PAYMENT_PROVIDER_TOKEN,
            currency=CURRENCY,
            prices=prices,
            provider_data=provider_data,
            need_email=True,
            send_email_to_provider=True
        )
    except Exception as e:
        logger.error(f"Ошибка отправки инвойса: {e}")
        await context.bot.send_message(
            chat_id, "Платёжная система временно недоступна. Попробуйте позже."
        )

# ========== ОТЗЫВЫ ==========
async def ask_feedback(chat_id, context):
    keyboard = [
        [InlineKeyboardButton("📝 Оставить отзыв", callback_data="feedback_yes")],
        [InlineKeyboardButton("❌ Пропустить", callback_data="feedback_no")]
    ]
    await context.bot.send_message(
        chat_id,
        "Вы можете оставить отзыв если захотите.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ========== ОБРАБОТЧИКИ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Добро пожаловать! Нажмите «Начать сессию», чтобы получить расклад.",
        reply_markup=START_KEYBOARD
    )

async def start_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    db: Database = context.bot_data['db']

    await db.get_or_create_user(user_id)

    if context.user_data.get('waiting_for_question'):
        await update.message.reply_text(
            "Вы уже задаёте вопрос. Напишите его, я готовлю расклад."
        )
        return

    if FREE_CONSULTATION_ENABLED:
        free_used = await db.is_free_used(user_id)
        if not free_used:
            await update.message.reply_text(
                FREE_CONSULTATION_TEXT,
                parse_mode='Markdown',
                reply_markup=FREE_KEYBOARD
            )
            return

    last_end = await db.get_last_session_end(user_id)
    if last_end and (time.time() - last_end) < COOLDOWN_SECONDS:
        remaining = COOLDOWN_SECONDS - (time.time() - last_end)
        hours = int(remaining // 3600)
        minutes = int((remaining % 3600) // 60)
        await update.message.reply_text(
            f"🌙 Картам нужно время, чтобы их образы улеглись в душе.\n"
            f"Следующий расклад будет доступен через {hours} ч {minutes} мин.\n"
            f"Приходите позже — мудрость не терпит спешки.",
            reply_markup=START_KEYBOARD
        )
        return

    if is_payment_configured():
        service_text = (
            "✨ Я — виртуальный таролог.✨\n\n"
            "Я работаю с классической колодой Райдера—Уэйта и глубокой символикой.\n\n"
            "Как проходит сеанс:\n"
            "1. Вы задаёте любой вопрос (отношения, работа, выбор, саморазвитие).\n"
            "2. Я вытягиваю для вас 3 случайные карты Таро.\n"
            "3. Даю подробный разбор:\n"
            "• значение каждой карты (символы, детали, смысл)\n"
            "• их взаимодействие друг с другом\n"
            "• общий синтез — ответ на ваш вопрос\n"
            "4. При необходимости — итоговая карта-квинтэссенция.\n\n"
            "Карты — не предсказание, а зеркало вашей души.\n"
            "Я помогаю увидеть скрытые грани ситуации, найти ресурсы и принять осознанное решение.\n\n"
            f"💰 Стоимость одного расклада — {PRICE/100} {CURRENCY}\n\n"
            "Сразу после оплаты вы сможете задать свой вопрос.\n\n"
            "Готовы заглянуть в себя? 🔮"
        )
        await update.message.reply_text(service_text, parse_mode='Markdown')
        await send_invoice(chat_id, context)
        return

    await start_session_core(chat_id, user_id, context)

async def free_consultation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    db: Database = context.bot_data['db']

    free_used = await db.is_free_used(user_id)
    if free_used:
        await query.edit_message_text("Вы уже использовали бесплатную сессию.")
        return

    if context.user_data.get('waiting_for_question'):
        await query.edit_message_text("У вас уже есть активная сессия. Завершите её.")
        return

    await db.set_free_used(user_id)
    await query.edit_message_text("Начинаем бесплатную сессию...")
    await start_session_core(chat_id, user_id, context)

async def start_session_core(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    if USE_AI_WELCOME:
        welcome = await generate_welcome_message()
    else:
        welcome = "Добро пожаловать. Я — таролог, работающий с мудростью колоды Райдера—Уэйта. Я готов выслушать ваш вопрос и обратиться к картам.\n\nЧтобы образы и символы заговорили с вами максимально ясно, пожалуйста, опишите вашу ситуацию или вопрос как можно подробнее. Чем больше деталей вы предоставите, тем глубже и точнее будет наше совместное путешествие к пониманию. Я жду вашего вопроса."

    await context.bot.send_message(
        chat_id, welcome,
        reply_markup=ReplyKeyboardMarkup([["Завершить сессию"]], resize_keyboard=True)
    )
    context.user_data['waiting_for_question'] = True
    context.user_data['user_id'] = user_id
    context.user_data['chat_id'] = chat_id
    context.user_data['history'] = []

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    db: Database = context.bot_data['db']

    if user_message == "Завершить сессию":
        if context.user_data.get('waiting_for_question'):
            context.user_data.clear()
            await update.message.reply_text(
                "✨",
                reply_markup=START_KEYBOARD
            )
        else:
            await update.message.reply_text(
                "Активной сессии нет. Нажмите «Начать сессию».",
                reply_markup=START_KEYBOARD
            )
        return

    if context.user_data.get('waiting_for_question'):
        if context.user_data.get('user_id') != user_id:
            await update.message.reply_text(
                "Сейчас идёт сессия другого пользователя. Подождите.",
                reply_markup=START_KEYBOARD
            )
            return

        await context.bot.send_chat_action(chat_id, action="typing")

        history = context.user_data.get('history', [])
        answer = await ask_ai(user_message, history)

        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": answer})
        if len(history) > 10:
            history = history[-10:]
        context.user_data['history'] = history

        if len(answer) > 4096:
            for i in range(0, len(answer), 4096):
                await update.message.reply_text(answer[i:i+4096])
        else:
            await update.message.reply_text(answer)

        await db.update_last_session_end(user_id)
        context.user_data.pop('waiting_for_question', None)
        context.user_data.pop('user_id', None)
        context.user_data.pop('chat_id', None)
        context.user_data.pop('history', None)

        # Сначала галочка с клавиатурой
        await update.message.reply_text(
            "✨",
            reply_markup=START_KEYBOARD
        )
        # Потом предложение отзыва
        await ask_feedback(chat_id, context)
        return

    await update.message.reply_text(
        "Сейчас нет активной сессии. Нажмите «Начать сессию».",
        reply_markup=START_KEYBOARD
    )

async def feedback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "feedback_yes":
        context.user_data['awaiting_feedback'] = True
        await query.edit_message_text("Пожалуйста, напишите ваш отзыв одним сообщением.⤵️")
    else:
        await query.edit_message_text("Спасибо! Если захотите оставить отзыв позже, используйте /feedback.")

async def feedback_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('awaiting_feedback'):
        feedback = update.message.text
        if AUTHOR_CHAT_ID:
            try:
                await context.bot.send_message(
                    int(AUTHOR_CHAT_ID),
                    f"📬 Новый отзыв\n\n{feedback}"
                )
            except Exception as e:
                logger.error(f"Не удалось отправить отзыв: {e}")
        await update.message.reply_text("Спасибо за ваш отзыв!")
        context.user_data.pop('awaiting_feedback', None)

# ========== ПЛАТЁЖНЫЕ ОБРАБОТЧИКИ ==========
async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_payment_configured():
        await update.pre_checkout_query.answer(ok=True)
    else:
        await update.pre_checkout_query.answer(ok=False, error_message="Платежи временно недоступны.")

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        "✅ Оплата прошла успешно! Начинаем сеанс.",
        reply_markup=ReplyKeyboardMarkup([["Завершить сессию"]], resize_keyboard=True)
    )
    await start_session_core(chat_id, user_id, context)

# ========== КОМАНДА СБРОСА БД ==========
async def resetdb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if AUTHOR_CHAT_ID and update.effective_user.id != int(AUTHOR_CHAT_ID):
        await update.message.reply_text("⛔ Недостаточно прав для выполнения этой команды.")
        return

    db: Database = context.bot_data['db']
    await update.message.reply_text("⚠️ Сброс базы данных... Это удалит всех пользователей и историю.")
    try:
        await db.reset_database()
        await update.message.reply_text("✅ База данных успешно сброшена.")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при сбросе базы: {e}")

# ========== ЗАПУСК ==========
async def main():
    lock_fd = acquire_lock()
    try:
        logger.info("🚀 Запуск бота...")
        db = Database(DATABASE_URL)
        await db.connect()
        await db.init_tables()

        app = Application.builder().token(TELEGRAM_TOKEN).build()
        app.bot_data['db'] = db

        # Удаляем вебхук, чтобы использовать polling
        await app.bot.delete_webhook()
        await asyncio.sleep(1)
        webhook_info = await app.bot.get_webhook_info()
        if webhook_info.url:
            logger.warning(f"Вебхук всё ещё установлен: {webhook_info.url}. Повторная попытка удаления...")
            await app.bot.delete_webhook()
            await asyncio.sleep(1)

        # Регистрация обработчиков
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("resetdb", resetdb))
        app.add_handler(MessageHandler(filters.Regex("^Начать сессию$"), start_session))
        app.add_handler(CallbackQueryHandler(free_consultation_callback, pattern="^free_consultation$"))
        app.add_handler(PreCheckoutQueryHandler(pre_checkout))
        app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        app.add_handler(CallbackQueryHandler(feedback_callback, pattern="^feedback_"))
        app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^Завершить сессию$"), handle_message))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, feedback_text))

        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        logger.info("✅ Бот запущен, polling активен")

        # Ожидание сигнала остановки
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)

        await stop_event.wait()

        logger.info("🛑 Остановка...")
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await db.close()
    except Exception as e:
        logger.exception(f"Критическая ошибка: {e}")
    finally:
        release_lock(lock_fd)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
