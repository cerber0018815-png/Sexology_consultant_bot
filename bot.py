import asyncio
import time
import openai
import sys
import os
import json
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, LabeledPrice, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, PreCheckoutQueryHandler, CallbackQueryHandler
)

load_dotenv()

# ===== НАСТРОЙКИ =====
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
PAYMENT_PROVIDER_TOKEN = os.getenv('PAYMENT_PROVIDER_TOKEN')
CURRENCY = os.getenv('CURRENCY', 'RUB')
PRICE = int(os.getenv('PRICE', 15000))          # цена в копейках
AUTHOR_CHAT_ID = os.getenv('AUTHOR_CHAT_ID')    # Telegram ID администратора для отзывов

# Флаги AI
USE_AI_WELCOME = os.getenv('USE_AI_WELCOME', 'True').lower() in ('true', '1', 'yes')
USE_AI_END = os.getenv('USE_AI_END', 'True').lower() in ('true', '1', 'yes')

# Включены ли платежи
PAYMENT_ENABLED = os.getenv('PAYMENT_ENABLED', 'False').lower() in ('true', '1', 'yes')

if not TELEGRAM_TOKEN or not DEEPSEEK_API_KEY:
    print("❌ Ошибка: TELEGRAM_TOKEN или DEEPSEEK_API_KEY не найдены!")
    sys.exit(1)

if PAYMENT_ENABLED and not PAYMENT_PROVIDER_TOKEN:
    print("⚠️ PAYMENT_ENABLED = True, но PAYMENT_PROVIDER_TOKEN не задан. Платежи будут недоступны.")
    PAYMENT_ENABLED = False

openai.api_base = "https://api.deepseek.com/v1"
openai.api_key = DEEPSEEK_API_KEY
# =====================

# ===== КОНСТАНТЫ =====
MAX_HISTORY = 30
SESSION_DURATION = 45 * 60          # 40 минут
COOLDOWN_SECONDS = 24 * 60 * 60     # 24 часа (можно изменить)
TIMER_UPDATE_INTERVAL = 60

END_MESSAGE = (
    "Время нашей консультации подошло к концу.\n"
    "Спасибо за доверие и за то, что поделились своей ситуацией. Если возникнут новые вопросы или потребуется уточнить что-то из наших рекомендаций – обращайтесь в любое время."
)

DEFAULT_WELCOME = (
    "Здравствуйте.\n\n"
    "Я — виртуальный консультант, созданный на основе классических подходов сексологии. Здесь Вы сможете спокойно и анонимно обсудить любые вопросы, связанные с интимной сферой, отношениями или сексуальным здоровьем. Всё, что вы напишете, остаётся между нами.\n\n"
    "Моя задача — не ставить диагнозов, а помочь разобраться в ситуации, предложить информацию и, если потребуется, дать рекомендации, основанные на проверенных методах.\n\n"
    "Вы можете рассказать обо всём, что вас беспокоит, — начиная с самых общих ощущений и заканчивая конкретными трудностями. Здесь нет тем, которые нельзя обсуждать, и нет оценок — только бережное отношение и профессиональная поддержка.\n\n"
    "Если вы готовы, расскажите, с чем вы столкнулись. С чего бы вы хотели начать?"
)

START_KEYBOARD = ReplyKeyboardMarkup([["Начать сессию"]], resize_keyboard=True)
END_KEYBOARD = ReplyKeyboardMarkup([["Завершить сессию"]], resize_keyboard=True)

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
def ensure_user_data(context: ContextTypes.DEFAULT_TYPE):
    """Гарантирует, что в context.user_data есть нужные ключи."""
    if 'last_session_end' not in context.user_data:
        context.user_data['last_session_end'] = 0
    if 'history' not in context.user_data:
        context.user_data['history'] = []
    if 'awaiting_feedback' not in context.user_data:
        context.user_data['awaiting_feedback'] = False

def split_long_message(text: str, max_length: int = 4096) -> list[str]:
    if len(text) <= max_length:
        return [text]
    parts = []
    while text:
        if len(text) <= max_length:
            parts.append(text)
            break
        split_index = text.rfind(' ', 0, max_length)
        if split_index == -1:
            split_index = max_length
        parts.append(text[:split_index].strip())
        text = text[split_index:].strip()
    return parts

# ===== AI-ФУНКЦИИ =====
SYSTEM_PROMPT = """
1. Роль и идентичность бота
Ты – виртуальный сексолог-консультант. Ты помогаешь пользователям разбираться в их сексуальных трудностях, опираясь на проверенные методы, изложенные в классических работах по сексологии (но в ответах не упоминаешь авторов и названия книг).

Твой стиль – спокойный, тактичный, безоценочный. Ты создаёшь атмосферу безопасности и анонимности. Ты не ставишь медицинских диагнозов, но даёшь образовательные и психологические рекомендации. Если ситуация выходит за рамки твоей компетенции (органические нарушения, тяжёлые психические расстройства), ты направляешь к соответствующим специалистам.

2. Принципы ведения диалога
Сначала задавай уточняющие вопросы. Не переходи к рекомендациям, пока не соберёшь достаточно информации. Исключение – если пользователь сразу дал очень подробное описание, из которого ясны ключевые параметры. В этом случае можно дать развёрнутый ответ.

Используй вопросы для сбора информации. Ориентируйся на схему сексуального анамнеза:

Как давно возникла проблема?

При каких обстоятельствах проявляется / не проявляется?

Есть ли постоянный партнёр? Осведомлён ли он? Какова атмосфера в отношениях?

Есть ли утренняя эрекция (у мужчин), смазка (у женщин), способность к самостимуляции?

Были ли стрессы, изменения в жизни, приём лекарств, употребление алкоголя/наркотиков?

Есть ли страхи, тревога, депрессивное настроение?

Какие попытки решения уже предпринимались?

Давай рекомендации, только когда картина стала достаточно ясной. Используй техники из раздела 4, описывай их шаг за шагом понятным языком.

Если пользователь находится в паре – всегда подчёркивай важность участия партнёра. Если партнёр не вовлечён, уточни, возможно ли его участие, или предложи варианты самостоятельной работы.

При признаках органической причины (отсутствие утренней эрекции, боли, выделения) – направь к врачу (уролог, андролог, гинеколог, эндокринолог).

При признаках тяжёлого психического расстройства (суицидальные мысли, панические атаки, психотические симптомы) – мягко направь к психотерапевту или психиатру.

3. Диагностические ориентиры (используй для уточнения)
Утренняя эрекция – если есть, физиологический механизм сохранён, проблема психогенная. Если нет – требуется врачебное обследование.

Смазка у женщин – отсутствие даже при психологическом возбуждении может указывать на гормональные причины.

Продолжительность коитуса – норма 7–30 минут (включая прелюдию), собственно фрикции 2–7 минут.

Сексуальные мифы, которые нужно развенчивать: обязательность одновременного оргазма, необходимость эрекции «по первому требованию», «правильность» вагинального оргазма, опасность мастурбации, нормальность эротических фантазий и др.

4. Арсенал рекомендаций (используй после сбора информации)
Техники сгруппированы по типу проблем. Выбирай те, которые соответствуют ситуации. Описывай их доступно, без ссылок на источники.

4.1. Работа с тревогой и страхом неудачи (чувственное фокусирование)
Показания: тревога по поводу эрекции/оргазма, напряжение в паре, избегание тактильности.

Чувственное фокусирование I (негенитальное):
Пара договаривается на 1–2 недели не заниматься половым актом и не доводить до оргазма. По очереди ласкают всё тело, исключая половые органы и грудь. Ласкающий сосредоточен на удовольствии партнёра, принимающий – на своих ощущениях и даёт обратную связь.

Чувственное фокусирование II (генитальное):
После освоения первого этапа разрешается стимуляция гениталий и груди, но по‑прежнему без оргазма и коитуса. Цель – исследовать эрогенные зоны без цели «долженствования».

4.2. Преждевременная эякуляция
Стоп‑старт (самостоятельно или с партнёршей):
Мужчина сосредотачивается на ощущениях. При приближении к оргазму (оценка 9 по 10-балльной шкале) подаёт сигнал «стоп», стимуляция прекращается на 10–30 секунд. Повторяется 3–4 раза, на четвёртый раз разрешается эякуляция. После освоения – переход к коитусу в позе «женщина сверху» с аналогичными остановками.

Сжатие:
Партнёрша сжимает головку полового члена большим и указательным пальцами до исчезновения позыва к эякуляции (10–30 секунд), затем стимуляция продолжается. Применяется как при мануальной стимуляции, так и при коитусе (в позе женщины сверху).

4.3. Эректильная дисфункция (импотенция)
Поэтапная десенсибилизация:

Эротическое наслаждение без эрекции (чувственное фокусирование I).

Эрекция без оргазма (стимуляция до эрекции, затем прекращение).

Экстравагинальная эрекция (стимуляция до оргазма без коитуса).

Интромиссия без оргазма (введение члена на несколько минут без фрикций).

Коитус с возможностью экстравагинального завершения при тревоге.

Использование утренней эрекции:
Если утренняя эрекция регулярна, перенести близость на утро, когда тревога ниже.

Стимуляция через одежду:
Для снижения тревоги при обнажении – начинать стимуляцию через бельё или брюки.

Сжатие как демонстрация управляемости эрекции:
Партнёрша сжимает член ниже головки, эрекция ослабевает, затем восстанавливается – показывает, что временное ослабление не опасно.

4.4. Замедленная эякуляция (задержанная эякуляция)
Прогрессивная десенсибилизация:
От мастурбации в одиночку → в присутствии партнёрши → мануальная стимуляция партнёршей → «мужской мост» (стимуляция до предоргазма + введение члена).

«Мужской мост»:
Партнёрша стимулирует пенис рукой или ртом до момента, когда оргазм вот-вот наступит. Мужчина вводит член во влагалище и совершает несколько фрикций, завершая эякуляцию. Если не получается – повторить цикл.

Усиление стимуляции:
Поза «женщина с плотно сомкнутыми бёдрами» (лёжа на боку или на спине) – усиливает трение.

Запрет на мастурбацию в одиночку – чтобы перенаправить эякуляторный рефлекс на партнёршу.

4.5. Аноргазмия у женщин
Полная аноргазмия (никогда не было оргазма):

Самостоятельная мастурбация с использованием рук, вибратора, эротических фантазий, литературы или фильмов. Разрешается уделять этому столько времени, сколько нужно.

Оргазм в присутствии партнёра (сначала женщина сама, затем партнёр стимулирует мануально).

Приём «мост» (см. ниже).

Ситуативная аноргазмия (оргазм при клиторной стимуляции, но не при коитусе):
«Мост»: Во время коитуса женщина (или партнёр) продолжает стимуляцию клитора. Как только ощущение приближается к оргазму, стимуляция клитора прекращается, и женщина делает резкое надвигающее движение тазом. Если оргазм не наступает – цикл повторяется. Со временем порог может снизиться.

Упражнения Кегеля:
Попеременное напряжение и расслабление мышц тазового дна (как при остановке мочеиспускания). Напряжение на 2–3 секунды, затем расслабление. Повторять по 10 раз несколько раз в день. Помогает усилить оргазмические ощущения и улучшить тонус.

4.6. Вагинизм
Прогрессивная десенсибилизация in vivo:

Женщина самостоятельно, с помощью зеркала, вводит кончик указательного пальца во влагалище, затем весь палец, затем два пальца.

Палец партнёра под её контролем.

Тампон на несколько часов.

Первый коитус: смазанный пенис, женщина сверху, сама направляет введение, мужчина остаётся неподвижен несколько минут, затем извлекает.
Каждый этап занимает столько времени, сколько нужно, без форсирования. При сильной тревоге предварительно осваивают релаксацию (например, по Джекобсу).

4.7. Снижение либидо (гипоактивное сексуальное влечение)
Предписание эротических фантазий:
Ежедневно 10–15 минут вызывать в воображении эротические сцены без физической стимуляции, чтобы «разбудить» влечение.

Модификация поведения избегания:
Постепенное приближение к ситуациям, которые пациент избегает (например, сначала просто находиться рядом с партнёром в обнажённом виде, затем – тактильный контакт без обязательств).

«Свидания» без сексуального исхода:
Паре предписывается романтический вечер с поцелуями и объятиями, но с чётким запретом на коитус – чтобы снять давление.

Использование эротических материалов:
Просмотр фильмов, чтение литературы, которые раньше вызывали интерес, для активации фантазий.

Выдача разрешения:
Прямая поддержка в том, что желания и фантазии нормальны, если они не нарушают границ партнёра.

4.8. Сексуальное избегание и коитофобия
Систематическая десенсибилизация in vivo:
Построить иерархию пугающих ситуаций (например: думать о половом акте → смотреть на обнажённого партнёра → касаться гениталий → введение пальца → коитус) и проходить ступени с использованием релаксации (дыхание, напряжение‑расслабление).

Парадоксальная интенция:
Даётся задание пытаться испытать страх или избежать эрекции – часто это снижает тревогу.

4.9. Асимметрия желаний (дисгамия)
«График близости»:
Пара договаривается о фиксированных днях для интимной близости (например, два раза в неделю), что снижает тревогу «когда же это случится».

Правило «отказа без объяснений»:
Партнёр, не настроенный на близость, может отказаться, не объясняя причин, но при этом следующий раз инициатива переходит к нему/ней – чтобы не создавалось ощущение отвержения.

Чувственное фокусирование как способ восстановления тактильного контакта без давления.

4.10. Методы для одиноких пациентов (без партнёра)
Самостоятельный стоп‑старт для мужчин с преждевременной эякуляцией.

Использование вибратора для мужчин с замедленной эякуляцией или тревогой.

Мастурбация с эротическими фантазиями и вибратором для женщин с аноргазмией.

Дилататоры (мягкие расширители) для женщин с вагинизмом – введение по нарастающей самостоятельно.

Работа с эротическими материалами для активации влечения.

Если в дальнейшем появится партнёр – переход к парным техникам.

4.11. Работа с чувством вины, стыда и негативными установками
Выдача разрешения – прямая поддержка нормальности сексуальных желаний, фантазий, мастурбации, разнообразия поз.

Анализ и коррекция негативных родительских посланий – отслеживание автоматических мыслей («это грязно», «так нельзя») и замена их на нейтральные или позитивные.

Развенчание мифов – объяснение, что одновременный оргазм не обязателен, размер члена не имеет значения, вагинальный оргазм не «лучше» клиторального и т.д.

4.12. Дополнительные техники для снижения тревоги и расслабления
Релаксация по Джекобсу – последовательное напряжение и расслабление всех групп мышц (особенно при вагинизме и тревожном ожидании).

Диафрагмальное дыхание – медленный вдох животом, пауза, медленный выдох – для снижения симпатической активации во время близости.

Массаж без сексуального контекста – для пар, у которых утрачен тактильный контакт.

5. Ограничения и направления к специалистам
Направь к врачу (урологу, андрологу, гинекологу, эндокринологу), если:

У мужчины нет утренней эрекции при сохранном либидо.

Есть боли, выделения, кровь в сперме, изменение формы члена.

У женщины постоянная сухость, жжение, зуд, боли в глубине таза.

Принимаются лекарства, влияющие на сексуальную функцию (антидепрессанты, бета-блокаторы, антиандрогены, гормональные препараты).

Направь к психотерапевту/психиатру, если:

Пользователь сообщает о депрессии, апатии, суицидальных мыслях.

Есть панические атаки, фобии, навязчивости.

Сексуальная проблема возникла после психологической травмы (насилие, измена, потеря) и сопровождается выраженными эмоциональными реакциями.

Пользователь сообщает о галлюцинациях, бредовых идеях, маниакальном состоянии.

6. Примеры диалогового поведения
Начало (приветственное сообщение):

Здравствуйте. Я – виртуальный консультант. Всё, что вы расскажете, останется анонимным. Здесь можно говорить открыто и без осуждения. Расскажите, что вас беспокоит. Если хотите, можете сразу описать ситуацию, а я помогу разобраться.

Если описание развёрнутое (можно переходить к рекомендациям):

Спасибо за подробности. Теперь я вижу картину: у вас есть партнёрша, проблема длится несколько месяцев, утренняя эрекция сохранена. Это говорит о том, что физиологически всё в порядке, а сложности связаны с тревожным ожиданием. Есть эффективный подход, который помогает в таких случаях. Он называется «чувственное фокусирование». Суть в том, чтобы на время убрать цель «половой акт» и сосредоточиться на взаимных ласках без обязательств… [далее описание техники].

Если описание короткое, задаёшь уточняющие вопросы:

Спасибо, что поделились. Чтобы мне лучше понять ситуацию и предложить подходящие шаги, уточните, пожалуйста:

Есть ли у вас постоянный партнёр? Если да, знает ли он/она о ваших переживаниях?

Бывает ли у вас эрекция по утрам или при самостимуляции?

Как давно возникла эта тревога, было ли какое-то событие, с которого всё началось?
Ответы помогут определить, с чем именно мы имеем дело.

Если пользователь задаёт общий вопрос без описания проблемы:

Чтобы я мог дать вам полезный ответ, расскажите немного о том, что вас беспокоит. Например: это касается влечения, возбуждения, оргазма, отношений с партнёром? Чем подробнее вы опишете ситуацию, тем точнее я смогу сориентироваться.
"""

async def generate_session_summary(history: list) -> str:
    if not history:
        return None
    history_copy = history.copy()
    history_copy.append({
        "role": "user",
        "content": (
            "Наша сессия подходит к концу. Пожалуйста, напиши небольшое завершающее поддерживающее напутствие, "
            "учитывая всё, что мы обсуждали. Если уместно, мягко пригласи к следующей сессии. "
            "Сохрани свой обычный тон."
        )
    })
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history_copy
    try:
        print("🔄 Генерация итога...")
        response = await asyncio.to_thread(
            openai.ChatCompletion.create,
            model="deepseek-chat",
            messages=messages,
            max_tokens=1500,
            temperature=1
        )
        summary = response.choices[0].message.content
        print("✅ Итог получен")
        return summary
    except Exception as e:
        print(f"❌ Ошибка при генерации итога: {e}")
        return None

async def generate_welcome_message() -> str:
    try:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Пользователь готов начать разговор. Напиши приветствие, которое пригласит его поделиться тем, что его беспокоит. Объясни что чем более детально пользователь опишит свою проблему, тем более подробным будет ответ. Сохрани свой обычный тон. Не используй Markdown, просто текст."}
        ]
        response = await asyncio.to_thread(
            openai.ChatCompletion.create,
            model="deepseek-chat",
            messages=messages,
            max_tokens=800,
            temperature=1
        )
        welcome = response.choices[0].message.content.strip()
        return welcome
    except Exception as e:
        print(f"❌ Ошибка при генерации приветствия: {e}")
        return None

# ===== ТАЙМЕРЫ И ОЧИСТКА =====
async def send_typing_periodically(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    try:
        while True:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass

async def stop_typing(typing_task: asyncio.Task):
    if typing_task and not typing_task.done():
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass

async def update_timer_periodically(chat_id: int, message_id: int, context: ContextTypes.DEFAULT_TYPE):
    try:
        await asyncio.sleep(TIMER_UPDATE_INTERVAL)
        while True:
            current_timer_id = context.user_data.get('timer_message_id')
            if current_timer_id != message_id:
                break
            if 'session_start_time' not in context.user_data:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
                except:
                    pass
                break
            elapsed = time.time() - context.user_data['session_start_time']
            remaining = SESSION_DURATION - elapsed
            if remaining <= 0:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
                except:
                    pass
                break
            minutes = int(remaining // 60)
            seconds = int(remaining % 60)
            timer_text = f"⏳ Осталось: {minutes} мин {seconds} сек"
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=timer_text
                )
            except Exception:
                break
            await asyncio.sleep(TIMER_UPDATE_INTERVAL)
    except asyncio.CancelledError:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except:
            pass
        raise

async def refresh_timer(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    old_task = context.user_data.get('timer_task')
    if old_task and not old_task.done():
        old_task.cancel()
        try:
            await old_task
        except asyncio.CancelledError:
            pass
    old_msg_id = context.user_data.get('timer_message_id')
    if old_msg_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=old_msg_id)
        except:
            pass
    if 'session_start_time' in context.user_data:
        remaining = SESSION_DURATION - (time.time() - context.user_data['session_start_time'])
        if remaining > 0:
            minutes = int(remaining // 60)
            seconds = int(remaining % 60)
            timer_text = f"⏳ Осталось: {minutes} мин {seconds} сек"
            try:
                timer_msg = await context.bot.send_message(chat_id=chat_id, text=timer_text)
            except Exception:
                return
            context.user_data['timer_message_id'] = timer_msg.message_id
            task = asyncio.create_task(
                update_timer_periodically(chat_id, timer_msg.message_id, context)
            )
            context.user_data['timer_task'] = task

async def cleanup_session(context: ContextTypes.DEFAULT_TYPE, clear_history: bool = True, chat_id: int = None):
    timer_task = context.user_data.get('timer_task')
    if timer_task and not timer_task.done():
        timer_task.cancel()
        try:
            await timer_task
        except asyncio.CancelledError:
            pass
    exp_task = context.user_data.get('expiration_task')
    if exp_task and not exp_task.done():
        exp_task.cancel()
        try:
            await exp_task
        except asyncio.CancelledError:
            pass
    typing_task = context.user_data.get('typing_task')
    if typing_task and not typing_task.done():
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass
    if chat_id:
        timer_msg_id = context.user_data.get('timer_message_id')
        if timer_msg_id:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=timer_msg_id)
            except:
                pass
    if 'history' in context.user_data and context.user_data['history']:
        context.user_data['last_session_end'] = time.time()
    if clear_history:
        context.user_data['history'] = []
    context.user_data.pop('timer_task', None)
    context.user_data.pop('timer_message_id', None)
    context.user_data.pop('expiration_task', None)
    context.user_data.pop('typing_task', None)
    context.user_data.pop('session_start_time', None)

async def end_session_by_timeout(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    if 'session_start_time' not in context.user_data:
        return
    history = context.user_data.get('history', []).copy()
    user_id = context.user_data.get('user_id')
    await cleanup_session(context, clear_history=False, chat_id=chat_id)
    typing_task = asyncio.create_task(send_typing_periodically(chat_id, context))
    try:
        if USE_AI_END and history:
            summary = await generate_session_summary(history)
            final_message = summary if summary else END_MESSAGE
        else:
            final_message = END_MESSAGE
    finally:
        await stop_typing(typing_task)
    parts = split_long_message(final_message)
    for i, part in enumerate(parts):
        if i == 0:
            await context.bot.send_message(chat_id, part, reply_markup=START_KEYBOARD)
        else:
            await context.bot.send_message(chat_id, part)
    context.user_data['last_session_end'] = time.time()
    await ask_feedback(chat_id, context)

# ===== ОТЗЫВЫ (без сохранения) =====
async def ask_feedback(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📝 Оставить отзыв", callback_data="feedback_yes")],
        [InlineKeyboardButton("❌ Пропустить", callback_data="feedback_no")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=chat_id,
        text="Вы можете оставить отзыв о прошедшей сессии, если захотите.",
        reply_markup=reply_markup
    )

async def feedback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "feedback_yes":
        context.user_data['awaiting_feedback'] = True
        await query.edit_message_text("Пожалуйста, напишите Ваш отзыв одним сообщением. ⤵️")
    else:
        await query.edit_message_text("Если захотите оставить отзыв позже, просто напишите /feedback.")

async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ask_feedback(update.effective_chat.id, context)

async def view_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Просмотр отзывов отключён (хранение не ведётся).")

# ===== ПЛАТЕЖИ =====
async def send_invoice(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not PAYMENT_ENABLED or not PAYMENT_PROVIDER_TOKEN:
        return
    prices = [LabeledPrice(label="Сессия (40 мин)", amount=PRICE)]
    provider_data = json.dumps({
        "receipt": {
            "items": [{
                "description": "Консультация (40 минут)",
                "quantity": "1.00",
                "amount": {"value": f"{PRICE/100:.2f}", "currency": CURRENCY},
                "vat_code": 1
            }]
        }
    })
    invoice_message = await context.bot.send_invoice(
        chat_id=chat_id,
        title="Оплата сессии",
        description="Одна консультация (40 минут).",
        payload="session_payment",
        provider_token=PAYMENT_PROVIDER_TOKEN,
        currency=CURRENCY,
        prices=prices,
        provider_data=provider_data,
        need_email=True,
        send_email_to_provider=True
    )
    return invoice_message

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not PAYMENT_ENABLED:
        await update.message.reply_text("Платёжные функции отключены администратором.")
        return
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    ensure_user_data(context)
    if 'session_start_time' in context.user_data:
        await context.bot.send_message(chat_id, "У вас уже есть активная сессия.", reply_markup=END_KEYBOARD)
        return
    last_end = context.user_data.get('last_session_end', 0)
    if last_end and (time.time() - last_end) < COOLDOWN_SECONDS:
        remaining = COOLDOWN_SECONDS - (time.time() - last_end)
        hours_left = int(remaining // 3600)
        minutes_left = int((remaining % 3600) // 60)
        await context.bot.send_message(
            chat_id,
            f"Здравствуйте, мы рады вас видеть! "
            f"Для более эффективной работы необходимо делать перерывы между консультациями. Возвращайтесь через {hours_left} ч {minutes_left} мин.",
            reply_markup=START_KEYBOARD
        )
        return
    invoice_message = await send_invoice(chat_id, context)
    if invoice_message:
        context.user_data['invoice_message_id'] = invoice_message.message_id

async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if PAYMENT_ENABLED:
        await update.pre_checkout_query.answer(ok=True)
    else:
        await update.pre_checkout_query.answer(ok=False, error_message="Платежи временно недоступны.")

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not PAYMENT_ENABLED:
        await update.message.reply_text("Платежи отключены, сессия не может быть начата.")
        return
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    # Удаляем служебные сообщения
    service_msg_id = context.user_data.get('service_message_id')
    if service_msg_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=service_msg_id)
        except:
            pass
        context.user_data.pop('service_message_id', None)
    invoice_msg_id = context.user_data.get('invoice_message_id')
    if invoice_msg_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=invoice_msg_id)
        except:
            pass
        context.user_data.pop('invoice_message_id', None)
    await update.message.reply_text("✅ Оплата прошла успешно! Сейчас начнём сессию.", reply_markup=END_KEYBOARD)
    await start_session_core(chat_id, user_id, context)

# ===== ОСНОВНЫЕ ФУНКЦИИ СЕССИИ =====
async def start_session_core(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    ensure_user_data(context)
    context.user_data['user_id'] = user_id
    context.user_data['history'] = []
    context.user_data['session_start_time'] = time.time()

    async def timeout_wrapper():
        await asyncio.sleep(SESSION_DURATION)
        await end_session_by_timeout(chat_id, context)

    context.user_data['expiration_task'] = asyncio.create_task(timeout_wrapper())

    typing_task = asyncio.create_task(send_typing_periodically(chat_id, context))
    try:
        if USE_AI_WELCOME:
            welcome_text = await generate_welcome_message()
            if not welcome_text:
                welcome_text = DEFAULT_WELCOME
        else:
            welcome_text = DEFAULT_WELCOME
    finally:
        await stop_typing(typing_task)

    await context.bot.send_message(chat_id, welcome_text, reply_markup=END_KEYBOARD)
    await refresh_timer(chat_id, context)
    print(f"✅ Сессия начата для {user_id}.")

async def start_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("🟢 Запуск start_session")
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    ensure_user_data(context)

    # Если уже есть активная сессия
    if 'session_start_time' in context.user_data:
        await update.message.reply_text(
            "У вас уже есть активная сессия. Завершите её командой /end или кнопкой.",
            reply_markup=END_KEYBOARD
        )
        return

    # Проверка кулдауна
    last_end = context.user_data.get('last_session_end', 0)
    if last_end and (time.time() - last_end) < COOLDOWN_SECONDS:
        remaining = COOLDOWN_SECONDS - (time.time() - last_end)
        hours_left = int(remaining // 3600)
        minutes_left = int((remaining % 3600) // 60)
        await update.message.reply_text(
            f"Здравствуйте, мы рады вас видеть! "
            f"Для более эффективной работы необходимо делать перерывы между консультациями. Возвращайтесь через {hours_left} ч {minutes_left} мин.",
            reply_markup=START_KEYBOARD
        )
        return

    # Если платежи отключены — стартуем сессию сразу
    if not PAYMENT_ENABLED:
        await start_session_core(chat_id, user_id, context)
        return

    # Иначе — отправляем инвойс
    service_text = (
        "🧑‍⚕️ **Консультация сексолога (45 минут)**\n\n"
        "Вы получите бережное, конфиденциальное пространство, где можно:\n"
        "• Открыто и без осуждения рассказать о том, что вас беспокоит\n"
        "• Разобраться в возможных причинах трудностей (влечение, возбуждение, оргазм, отношения)\n"
        "• Получить конкретные упражнения и техники, адаптированные под вашу ситуацию\n"
        "• Узнать, когда стоит обратиться к врачу (уролог, гинеколог, психотерапевт)\n"
        "• Задать уточняющие вопросы и почувствовать опору\n\n"
        "В основе работы – проверенные подходы, описанные в классических трудах по сексологии.\n"
        "Консультация не заменяет приём живого специалиста, но даёт ясность и направление.\n\n"
        f"💰 Стоимость: {PRICE/100} {CURRENCY}\n\n"
        "Консультация начнётся сразу после оплаты.\n\n"
    )
    service_msg = await update.message.reply_text(service_text, parse_mode='Markdown')
    context.user_data['service_message_id'] = service_msg.message_id
    invoice_message = await send_invoice(chat_id, context)
    if invoice_message:
        context.user_data['invoice_message_id'] = invoice_message.message_id
    else:
        await update.message.reply_text("Платёжный сервис временно недоступен. Попробуйте позже.")

async def end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("🔚 Получена команда /end")
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    ensure_user_data(context)
    if 'session_start_time' not in context.user_data:
        await update.message.reply_text("Сейчас нет активной сессии.", reply_markup=START_KEYBOARD)
        return
    history = context.user_data.get('history', []).copy()
    await cleanup_session(context, clear_history=False, chat_id=chat_id)
    typing_task = asyncio.create_task(send_typing_periodically(chat_id, context))
    try:
        if USE_AI_END and history:
            summary = await generate_session_summary(history)
            final_message = summary if summary else END_MESSAGE
        else:
            final_message = END_MESSAGE
    finally:
        await stop_typing(typing_task)
    parts = split_long_message(final_message)
    for i, part in enumerate(parts):
        if i == 0:
            await context.bot.send_message(chat_id, part, reply_markup=START_KEYBOARD)
        else:
            await context.bot.send_message(chat_id, part)
    context.user_data['last_session_end'] = time.time()
    await ask_feedback(chat_id, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text

    ensure_user_data(context)

    if context.user_data.get('awaiting_feedback'):
        feedback_text = user_message
        user_id = update.effective_user.id
        username = update.effective_user.username or "без имени"
        if AUTHOR_CHAT_ID:
            try:
                await context.bot.send_message(chat_id=int(AUTHOR_CHAT_ID), text=f"📬 Новый отзыв:\n\n{feedback_text}")
            except Exception as e:
                print(f"Не удалось отправить отзыв автору: {e}")
        await update.message.reply_text("Спасибо за ваш отзыв! Он очень важен для меня.", reply_markup=START_KEYBOARD)
        context.user_data['awaiting_feedback'] = False
        return

    if user_message == "Начать сессию":
        await start_session(update, context)
        return

    if user_message == "Завершить сессию":
        await end(update, context)
        return

    if 'session_start_time' not in context.user_data:
        await update.message.reply_text("Сейчас нет активной сессии. Нажмите «Начать сессию».", reply_markup=START_KEYBOARD)
        return

    typing_task = asyncio.create_task(send_typing_periodically(update.effective_chat.id, context))
    context.user_data['typing_task'] = typing_task

    context.user_data['history'].append({"role": "user", "content": user_message})
    if len(context.user_data['history']) > MAX_HISTORY * 2:
        context.user_data['history'] = context.user_data['history'][-MAX_HISTORY*2:]

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + context.user_data['history']

    try:
        response = await asyncio.to_thread(
            openai.ChatCompletion.create,
            model="deepseek-chat",
            messages=messages,
            max_tokens=2500,
            temperature=1
        )
        clean_reply = response.choices[0].message.content
        context.user_data['history'].append({"role": "assistant", "content": clean_reply})
        if len(context.user_data['history']) > MAX_HISTORY * 2:
            context.user_data['history'] = context.user_data['history'][-MAX_HISTORY*2:]

        await stop_typing(typing_task)
        context.user_data.pop('typing_task', None)

        parts = split_long_message(clean_reply)
        for i, part in enumerate(parts):
            if i == 0:
                await update.message.reply_text(part, reply_markup=END_KEYBOARD)
            else:
                await update.message.reply_text(part)

        await refresh_timer(update.effective_chat.id, context)

    except Exception as e:
        print(f"❌ Ошибка при запросе к DeepSeek: {e}")
        await stop_typing(typing_task)
        context.user_data.pop('typing_task', None)
        error_message = "Извините, произошла техническая ошибка. Пожалуйста, попробуйте позже."
        await update.message.reply_text(error_message, reply_markup=END_KEYBOARD)
        await refresh_timer(update.effective_chat.id, context)

def main():
    print("🚀 Запуск бота...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start_session))
    app.add_handler(CommandHandler("end", end))
    app.add_handler(CommandHandler("feedback", feedback_command))
    app.add_handler(CommandHandler("view_feedback", view_feedback))

    if PAYMENT_ENABLED:
        app.add_handler(CommandHandler("buy", buy))
        app.add_handler(PreCheckoutQueryHandler(pre_checkout))
        app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    app.add_handler(CallbackQueryHandler(feedback_callback, pattern="^feedback_"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Обработчики добавлены")
    app.run_polling(timeout=50, drop_pending_updates=True)

if __name__ == "__main__":
    main()
