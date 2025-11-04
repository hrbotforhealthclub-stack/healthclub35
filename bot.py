#!/usr/bin/env python3
import os
# Загрузка переменных окружения должна быть самым первым действием
from dotenv import load_dotenv

load_dotenv()
from zoneinfo import ZoneInfo
import asyncio
import logging
import html
import re
from datetime import datetime, date
from math import radians, sin, cos, sqrt, atan2

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.bot import DefaultBotProperties
from aiogram.enums import ChatAction, ChatType, ChatMemberStatus, ContentType
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    Message, CallbackQuery,
    KeyboardButton, ReplyKeyboardMarkup,
    InlineKeyboardButton, InlineKeyboardMarkup,
    FSInputFile, ReplyKeyboardRemove,
    ChatMemberUpdated,
    # --- ИЗМЕНЕНИЕ: Добавляем BufferedInputFile ---
    BufferedInputFile
)
from aiogram.utils.chat_action import ChatActionSender
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import IntegrityError

# Импортируем все необходимые модели из вашего файла models.py
from models import (
    Employee, RoleGuide, BotText, OnboardingQuestion, EmployeeCustomData, OnboardingStep,
    Attendance, RegCode, Event, Idea, QuizQuestion, Topic, RoleOnboarding,
    ArchivedEmployee, ArchivedAttendance, ArchivedIdea,
    GroupChat, ConfigSetting, get_session
)

# — Загрузка .env —
# load_dotenv() # <-- Перенесено в начало файла

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///bot.db")
# OFFICE_LAT, OFFICE_LON и OFFICE_RADIUS_METERS удалены.
# Они будут загружаться из БД внутри функции process_time_tracking.
# --- ИЗМЕНЕНИЕ: UPLOAD_FOLDER_ONBOARDING больше не нужен ---
# UPLOAD_FOLDER_ONBOARDING = 'uploads/onboarding'
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
WEATHER_CITY = os.getenv("WEATHER_CITY", "Almaty")
# — Логирование —
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# — Инициализация бота и диспетчера —
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

ME_ID: int | None = None


async def get_me_id() -> int:
    """Получает и кэширует ID самого бота."""
    global ME_ID
    if ME_ID is None:
        ME_ID = (await bot.get_me()).id
    return ME_ID


def initialize_bot_texts():
    """Проверяет и создает тексты для бота по умолчанию, если они отсутствуют."""
    texts_to_ensure = {
        "quiz_success_message": {
            "text": "🎉 Поздравляем, {name}! Вы успешно прошли квиз ({correct}/{total})!",
            "description": "Сообщение после успешного прохождения квиза. Доступные переменные: {name}, {correct}, {total}."
        },
        "group_join_success_message": {
            "text": "Отлично, мы видим, что вы уже в нашем общем чате! Добро пожаловать в команду, теперь вам доступен весь функционал. 🎉",
            "description": "Сообщение, если пользователь прошел квиз и уже состоит в общем чате."
        },
        "group_join_prompt_message": {
            "text": "Остался последний шаг — вступите в наш основной рабочий чат, чтобы быть в курсе всех событий.",
            "description": "Сообщение, если пользователь прошел квиз, но еще не в общем чате."
        },
        "welcome_to_common_chat": {
            "text": "👋 Встречайте нового коллегу! {user_mention} ({user_name}) присоединился к нашему чату. Добро пожаловать в команду! 🎉",
            "description": "Сообщение в общем чате при вступлении нового сотрудника. Доступные переменные: {user_mention}, {user_name}, {role}."
        }
    }
    with get_session() as db:
        for key, data in texts_to_ensure.items():
            if not db.get(BotText, key):
                db.add(BotText(id=key, text=data['text'], description=data['description']))
        db.commit()


# Вызываем функцию инициализации один раз при запуске
initialize_bot_texts()
ALMATY_TZ = ZoneInfo("Asia/Almaty")


def now_almaty() -> datetime:
    return datetime.now(ALMATY_TZ)


# — FSM States —
class Reg(StatesGroup):
    waiting_code = State()
    waiting_for_status = State()


class Onboarding(StatesGroup):
    awaiting_answer = State()


class Training(StatesGroup):
    waiting_ack = State()


class Quiz(StatesGroup):
    waiting_answer = State()


class SubmitIdea(StatesGroup):
    waiting_for_idea = State()


class EditProfile(StatesGroup):
    choosing_field = State()
    waiting_for_new_value = State()


class FindEmployee(StatesGroup):
    waiting_for_name = State()


class TimeTracking(StatesGroup):
    waiting_location = State()


# — Кнопки и клавиатуры —
BACK_BTN = KeyboardButton(text="🔙 Назад")

time_tracking_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Я на месте"), KeyboardButton(text="👋 Я ухожу")],
        [BACK_BTN]
    ],
    resize_keyboard=True
)

employee_main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="⏰ Учет времени")],
        [KeyboardButton(text="🎉 Посмотреть ивенты"), KeyboardButton(text="💡 Поделиться идеей")],
        [KeyboardButton(text="👥 Наши сотрудники"), KeyboardButton(text="🧠 База знаний")],
        [KeyboardButton(text="📊 Мой профиль")]
    ],
    resize_keyboard=True
)

admin_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Добавить сотрудника"), KeyboardButton(text="Список сотрудников")],
        [KeyboardButton(text="Добавить ивент"), KeyboardButton(text="Просмотр идей")],
        [KeyboardButton(text="⏰ Учет времени"), KeyboardButton(text="Посещаемость сотрудника")],
        [KeyboardButton(text="👥 Наши сотрудники"), KeyboardButton(text="🧠 База знаний")],
        [KeyboardButton(text="📊 Мой профиль")]
    ],
    resize_keyboard=True
)

employee_status_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Я новенький")],
        [KeyboardButton(text="Я действующий сотрудник")]
    ],
    resize_keyboard=True, one_time_keyboard=True
)

training_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🏃‍♂️ Пройти тренинг")]], resize_keyboard=True
)
quiz_start_kb = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="Готов!", callback_data="quiz_start")]])
ack_kb = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="✅ Ознакомился", callback_data="training_done")]])
PAGE_SIZE = 5


# --- Вспомогательные функции ---

def render_rich(text: str | None) -> str:
    """Готовит текст с HTML для Telegram: нормализует \\n и чистит опасные теги."""
    if not text:
        return ""
    # Приводим литералы \n к настоящим переносам
    s = text.replace("\\n", "\n").replace("\r\n", "\n").replace("\\t", "\t")
    # Удаляем заведомо опасные блоки (минимальная санация)
    s = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", "", s)
    return s


def get_text(key: str, default: str = "Текст не найден") -> str:
    """Получает текст для бота из БД по ключу."""
    with get_session() as db:
        text_obj = db.get(BotText, key)
        return text_obj.text if text_obj else default


def get_config_value_sync(key: str, default: str = "") -> str:
    """Синхронная версия для получения настроек из БД."""
    with get_session() as db:
        setting = db.get(ConfigSetting, key)
        return setting.value if setting and setting.value is not None else default


def access_check(func):
    """Декоратор: пускаем только активных сотрудников с пройденным тренингом."""

    async def wrapper(message_or_cb: Message | CallbackQuery, state: FSMContext, *args, **kwargs):
        user_id = message_or_cb.from_user.id
        with get_session() as db:
            emp = db.query(Employee).filter_by(telegram_id=user_id).first()

        # 1) Не сотрудник или деактивирован — не пускаем
        if not emp or not emp.is_active:
            msg = message_or_cb.message if isinstance(message_or_cb, CallbackQuery) else message_or_cb
            await msg.answer("Доступ только для сотрудников. Введите регистрационный код, чтобы продолжить.")
            if isinstance(message_or_cb, CallbackQuery):
                await message_or_cb.answer()
            return

        # 2) Требуется тренинг — не пускаем (для действующих он проставляется кнопкой)
        if not emp.training_passed:
            msg = message_or_cb.message if isinstance(message_or_cb, CallbackQuery) else message_or_cb
            await msg.answer(
                get_text("access_denied_training_required", "Пожалуйста, завершите тренинг для доступа к функциям."),
                reply_markup=training_kb
            )
            if isinstance(message_or_cb, CallbackQuery):
                await message_or_cb.answer()
            return

        # 3) Всё ок — выполняем исходный хэндлер
        return await func(message_or_cb, state, *args, **kwargs)

    return wrapper


def upsert_groupchat(db, chat, is_admin: bool):
    """Обновляет или создает запись о группе в БД."""
    ctype = getattr(chat.type, "value", str(chat.type))
    if ctype not in ("group", "supergroup", "channel"):
        return
    title = getattr(chat, "title", None) or getattr(chat, "full_name", None) or str(chat.id)
    username = getattr(chat, "username", None) or ""

    logger.info("[GroupChat] upsert start chat_id=%s title=%r type=%s is_admin=%s",
                chat.id, title, ctype, is_admin)
    try:
        gc = db.query(GroupChat).filter_by(chat_id=chat.id).first()
        if not gc:
            gc = GroupChat(chat_id=chat.id)
            db.add(gc)
        gc.title = title
        gc.username = username
        gc.type = ctype
        gc.is_admin = bool(is_admin)
        gc.updated_at = datetime.utcnow()
        db.commit()
    except Exception as e:
        logger.exception("[GroupChat] upsert FAILED chat_id=%s: %s", chat.id, e)
        db.rollback()


def haversine(lat1, lon1, lat2, lon2):
    """Вычисляет расстояние между двумя точками на Земле."""
    R = 6371000
    lat1_rad, lon1_rad, lat2_rad, lon2_rad = map(radians, [lat1, lon1, lat2, lon2])
    dlon = lon2_rad - lon1_rad
    dlat = lat2_rad - lat1_rad
    a = sin(dlat / 2) ** 2 + cos(lat1_rad) * cos(lat2_rad) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c


from hashlib import blake2s


def role_to_token(role: str) -> str:
    """Создает короткий и стабильный токен из названия роли для callback-данных."""
    return blake2s(role.encode("utf-8"), digest_size=5).hexdigest()


def token_to_role(token: str) -> str | None:
    """Восстанавливает название роли по токену."""
    with get_session() as db:
        rows = db.query(Employee.role).filter(Employee.is_active == True, Employee.role != None).distinct().all()
    for (role,) in rows:
        if role and role_to_token(role) == token:
            return role
    return None


# --- Обработка событий в чатах ---

@dp.message(F.new_chat_members)
async def on_any_new_members(msg: Message):
    """Реагирует, когда бота добавляют в новый чат."""
    me_id = await get_me_id()
    if any(u.id == me_id for u in msg.new_chat_members):
        try:
            m = await bot.get_chat_member(msg.chat.id, me_id)
            st = getattr(getattr(m, "status", None), "value", getattr(m, "status", None))
            is_admin = st in ("administrator", "creator")
        except Exception as e:
            logger.warning("[new_chat_members] get_chat_member failed: %s", e)
            is_admin = False
        with get_session() as db:
            upsert_groupchat(db, msg.chat, is_admin)


@dp.my_chat_member()
async def on_bot_membership_change(event: ChatMemberUpdated):
    """Реагирует на изменение статуса бота в чате (например, сделали админом)."""
    ctype_val = getattr(event.chat.type, "value", str(event.chat.type))
    if ctype_val not in ("group", "supergroup", "channel"):
        return
    new_status = getattr(event.new_chat_member.status, "value", event.new_chat_member.status)
    is_admin = new_status in ("creator", "administrator")
    with get_session() as db:
        upsert_groupchat(db, event.chat, is_admin)


# --- Основная логика: Регистрация и Онбординг ---

@dp.message(Command("start"))
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    with get_session() as db:
        emp = db.query(Employee).filter_by(telegram_id=msg.from_user.id).first()

    if emp and emp.registered:
        if not emp.is_active:
            await msg.answer(get_text("account_deactivated", "Ваш аккаунт деактивирован."))
            return

        kb = admin_kb if emp.role == "Admin" else employee_main_kb
        if emp.training_passed:
            await msg.answer(get_text("welcome_back", "С возвращением, {name}!").format(name=emp.name), reply_markup=kb)
        elif emp.onboarding_completed:
            await msg.answer(
                get_text("training_not_passed_prompt", "Привет! Остался последний шаг - пройдите тренинг."),
                reply_markup=training_kb)
        else:
            await msg.answer(
                get_text("onboarding_not_finished", "Похоже, вы не закончили знакомство. Давайте продолжим!"))
            await run_onboarding(msg.from_user.id, state)
    else:
        await msg.answer(get_text("enter_reg_code", "Пожалуйста, введите ваш 8-значный регистрационный код:"),
                         reply_markup=ReplyKeyboardRemove())
        await state.set_state(Reg.waiting_code)


@dp.message(Command("here", "register_chat", "chatid", "id"))
async def register_chat_here(msg: Message):
    ctype = getattr(msg.chat.type, "value", str(msg.chat.type))
    if ctype not in ("group", "supergroup", "channel"):
        return await msg.answer("Эта команда работает только в группах/каналах.")

    me_id = await get_me_id()
    try:
        m = await bot.get_chat_member(msg.chat.id, me_id)
        st = getattr(getattr(m, "status", None), "value", getattr(m, "status", None))
        is_admin = st in ("administrator", "creator")
    except Exception:
        is_admin = False

    with get_session() as db:
        upsert_groupchat(db, msg.chat, is_admin)

    await msg.answer(
        f"Ок, записал:\n<b>{msg.chat.title or msg.chat.id}</b>\n"
        f"chat_id: <code>{msg.chat.id}</code>\n"
        f"Админ: {'Да' if is_admin else 'Нет'}",
        parse_mode="HTML"
    )


@dp.message(Reg.waiting_code)
async def process_code(msg: Message, state: FSMContext):
    if msg.text == "🔙 Назад":
        await state.clear()
        await msg.answer("Регистрация отменена.")
        return

    code = msg.text.strip()
    with get_session() as db:
        rc = db.query(RegCode).filter_by(code=code, used=False).first()
        if not rc:
            await msg.answer("❌ Код не найден или уже использован. Пожалуйста, попробуйте ещё раз.")
            return

        emp = db.query(Employee).filter_by(email=rc.email).first()
        if not emp or not emp.is_active:
            await msg.answer("❌ Ваш аккаунт был деактивирован. Регистрация невозможна.")
            await state.clear()
            return

        emp.telegram_id = msg.from_user.id
        emp.registered = True
        rc.used = True
        db.commit()

    await msg.answer(
        "Код принят! 🎉\n\nПожалуйста, выберите ваш статус:",
        reply_markup=employee_status_kb
    )
    await state.set_state(Reg.waiting_for_status)


@dp.message(Reg.waiting_for_status, F.text.in_({"Я новенький", "Я действующий сотрудник"}))
async def process_employee_status(msg: Message, state: FSMContext):
    with get_session() as db:
        emp = db.query(Employee).filter_by(telegram_id=msg.from_user.id).first()
        if msg.text == "Я новенький":
            await msg.answer(get_text("lets_get_acquainted", "Отлично, давайте познакомимся!"),
                             reply_markup=ReplyKeyboardRemove())
            await run_onboarding(msg.from_user.id, state)
            return

        emp.onboarding_completed = True
        emp.training_passed = True
        db.commit()

        kb = admin_kb if emp.role == "Admin" else employee_main_kb
        await msg.answer(
            get_text("welcome_existing_employee",
                     "С возвращением! Все функции бота теперь доступны для вас.").format(name=emp.name),
            reply_markup=kb
        )
    await state.clear()


async def run_onboarding(user_id: int, state: FSMContext):
    """Запускает и управляет процессом онбординга (сбор данных)."""
    with get_session() as db:
        emp = db.query(Employee).filter_by(telegram_id=user_id).first()
        answered_keys_rows = db.query(EmployeeCustomData.data_key).filter_by(employee_id=emp.id).all()
        answered_keys = [key for (key,) in answered_keys_rows]

        next_question = db.query(OnboardingQuestion).filter(
            OnboardingQuestion.role == emp.role,
            ~OnboardingQuestion.data_key.in_(answered_keys)
        ).order_by(OnboardingQuestion.order_index).first()

    if next_question:
        await bot.send_message(user_id, next_question.question_text)
        await state.set_state(Onboarding.awaiting_answer)
        await state.update_data(current_question_id=next_question.id)
    else:
        await run_company_introduction(user_id, state)


@dp.message(Onboarding.awaiting_answer)
async def process_onboarding_answer(msg: Message, state: FSMContext):
    """Обрабатывает ответы на вопросы онбординга."""
    data = await state.get_data()
    question_id = data.get("current_question_id")
    answer_text = msg.text

    with get_session() as db:
        emp = db.query(Employee).filter_by(telegram_id=msg.from_user.id).first()
        question = db.get(OnboardingQuestion, question_id)
        if not question:
            await run_onboarding(msg.from_user.id, state)
            return

        parsed_bday = None
        if question.data_key == 'birthday':
            try:
                parsed_bday = datetime.strptime(answer_text, "%d.%m.%Y").date()
            except ValueError:
                await msg.answer("Неверный формат даты. Попробуйте ещё раз в формате ДД.ММ.ГГГГ.")
                return

        values = dict(employee_id=emp.id, data_key=question.data_key, data_value=answer_text)
        stmt = insert(EmployeeCustomData.__table__).values(values).on_conflict_do_update(
            index_elements=['employee_id', 'data_key'],
            set_={'data_value': insert(EmployeeCustomData.__table__).excluded.data_value}
        )
        db.execute(stmt)

        if question.data_key == 'name':
            emp.name = answer_text
        elif question.data_key == 'birthday':
            emp.birthday = parsed_bday
        elif question.data_key == 'contact_info':
            emp.contact_info = answer_text
        db.commit()

    await run_onboarding(msg.from_user.id, state)


async def run_company_introduction(user_id: int, state: FSMContext):
    """Отправляет шаги знакомства с компанией."""
    with get_session() as db:
        emp = db.query(Employee).filter_by(telegram_id=user_id).first()
        if not emp.onboarding_completed:
            emp.onboarding_completed = True
            db.commit()

        steps = db.query(OnboardingStep).filter_by(role=emp.role).order_by(OnboardingStep.order_index).all()

    await bot.send_message(user_id, get_text("company_introduction_start", "Отлично! Теперь немного о компании."))
    for step in steps:
        if step.message_text:
            await bot.send_message(user_id, step.message_text)

        # --- ИЗМЕНЕНИЕ: Отправляем из БД (step.file_data) ---
        if step.file_data:
            try:
                # Используем BufferedInputFile для отправки байтов
                file_to_send = BufferedInputFile(step.file_data, filename=step.file_name or "file")

                if step.file_type == 'video_note':
                    await bot.send_video_note(user_id, file_to_send)
                elif step.file_type == 'video':
                    await bot.send_video(user_id, file_to_send)
                elif step.file_type == 'photo':
                    await bot.send_photo(user_id, file_to_send)
                elif step.file_type == 'document':
                    await bot.send_document(user_id, file_to_send)
            except Exception as e:
                # Логируем без file_path
                logger.error(f"Failed to send file from DB for step {step.id}: {e}")

        await asyncio.sleep(1.5)

    await state.clear()
    await bot.send_message(
        user_id,
        get_text("training_prompt_after_onboarding", "Знакомство завершено! Теперь нужно пройти финальный тренинг."),
        reply_markup=training_kb
    )


# --- Тренинг и квиз ---

@dp.message(F.text == "🏃‍♂️ Пройти тренинг")
async def start_training(msg: Message, state: FSMContext):
    await state.clear()
    with get_session() as db:
        emp = db.query(Employee).filter_by(telegram_id=msg.from_user.id).first()
        if not emp or not emp.is_active: return
        onboarding = db.query(RoleOnboarding).filter_by(role=emp.role).first()

    if onboarding and onboarding.text:
        await msg.answer(onboarding.text, reply_markup=ReplyKeyboardRemove())

        # --- ИЗМЕНЕНИЕ: Отправляем из БД (onboarding.file_data) ---
        if onboarding.file_data:
            try:
                file_to_send = BufferedInputFile(onboarding.file_data, filename=onboarding.file_name or "file")
                if onboarding.file_type == 'video_note':
                    await bot.send_video_note(msg.chat.id, file_to_send)
                # (Добавьте photo, video, document, если нужно)
            except Exception as e:
                logger.error(f"Failed to send training file from DB for role {onboarding.role}: {e}")
    else:
        await msg.answer("📚 Материалы для вашего тренинга пока не добавлены.")

    await msg.answer("Нажмите «✅ Ознакомился», когда будете готовы начать квиз.", reply_markup=ack_kb)
    await state.set_state(Training.waiting_ack)


@dp.callback_query(F.data == "training_done")
async def training_done(cb: CallbackQuery, state: FSMContext):
    await cb.message.delete()
    await cb.message.answer("✨ Отлично! Теперь давайте проверим знания в квизе.", reply_markup=quiz_start_kb)
    await state.clear()
    await cb.answer()


@dp.callback_query(F.data == "quiz_start")
async def on_quiz_start(cb: CallbackQuery, state: FSMContext):
    await cb.message.delete()
    with get_session() as db:
        emp = db.query(Employee).filter_by(telegram_id=cb.from_user.id).first()
        qs = db.query(QuizQuestion).filter_by(role=emp.role).order_by(QuizQuestion.order_index).all()
        if not qs:
            emp.training_passed = True
            db.commit()
            kb = admin_kb if emp.role == "Admin" else employee_main_kb
            await cb.message.answer("🎉 Для вашей роли квиза нет — тренинг пройден.", reply_markup=kb)
            await cb.answer()
            return
    await cb.message.answer("📝 Начинаем квиз:")
    await send_quiz_question(cb.message, qs[0], 0)
    await state.update_data(quiz_questions=qs, quiz_index=0, correct=0)
    await state.set_state(Quiz.waiting_answer)
    await cb.answer()


async def send_quiz_question(chat, question, idx):
    """Отправляет один вопрос квиза."""
    num = idx + 1
    if question.question_type == "choice":
        opts = question.options.split(";")
        buttons = [[InlineKeyboardButton(text=opt, callback_data=f"quiz_ans:{i}")] for i, opt in enumerate(opts)]
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        await chat.answer(f"{num}. {question.question}", reply_markup=kb)
    else:
        await chat.answer(f"{num}. {question.question}")


async def finish_quiz(user_id: int, chat_id: int, state: FSMContext, correct: int, total: int):
    """Завершает квиз и отправляет результат."""
    with get_session() as db:
        emp = db.query(Employee).filter_by(telegram_id=user_id).first()
        is_passed = correct >= total * 0.7
        emp.training_passed = is_passed
        db.commit()

    kb = admin_kb if emp.role == "Admin" else employee_main_kb
    if not is_passed:
        await bot.send_message(chat_id, f"😔 Вы не прошли квиз ({correct}/{total}). Попробуйте снова.",
                               reply_markup=training_kb)
        await state.clear()
        return

    quiz_success_text = get_text("quiz_success_message",
                                 "🎉 Поздравляем, {name}! Вы успешно прошли квиз ({correct}/{total})!").format(
        name=emp.name, correct=correct, total=total
    )
    await bot.send_message(chat_id, quiz_success_text)

    final_message = get_text("group_join_prompt_message",
                             "Остался последний шаг — вступите в наш основной рабочий чат, чтобы быть в курсе всех событий.")
    await bot.send_message(chat_id, final_message, reply_markup=kb)
    await state.clear()


@dp.message(Quiz.waiting_answer, F.text)
async def process_text_answer(msg: Message, state: FSMContext):
    data = await state.get_data()
    qs, idx, correct = data["quiz_questions"], data["quiz_index"], data["correct"]
    q = qs[idx]
    if q.question_type == "choice": return
    if msg.text.strip().lower() == q.answer.strip().lower():
        correct += 1
    idx += 1
    if idx < len(qs):
        await state.update_data(quiz_index=idx, correct=correct)
        await send_quiz_question(msg, qs[idx], idx)
    else:
        await finish_quiz(user_id=msg.from_user.id, chat_id=msg.chat.id, state=state, correct=correct, total=len(qs))


@dp.callback_query(Quiz.waiting_answer, F.data.startswith("quiz_ans:"))
async def process_choice_answer(cb: CallbackQuery, state: FSMContext):
    await cb.message.delete()
    data = await state.get_data()
    qs, idx, correct = data["quiz_questions"], data["quiz_index"], data["correct"]
    q = qs[idx]
    sel = int(cb.data.split(":", 1)[1])
    opts = q.options.split(";")
    user_ans = opts[sel]
    if user_ans.strip().lower() == q.answer.strip().lower():
        correct += 1
    idx += 1
    if idx < len(qs):
        await state.update_data(quiz_index=idx, correct=correct)
        await send_quiz_question(cb.message, qs[idx], idx)
    else:
        await finish_quiz(user_id=cb.from_user.id, chat_id=cb.message.chat.id, state=state, correct=correct,
                          total=len(qs))
    await cb.answer()


# --- Профиль ---

def get_profile_kb() -> InlineKeyboardMarkup:
    """Возвращает клавиатуру для просмотра профиля."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Редактировать", callback_data="profile_edit")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="profile_back")]
    ])


@dp.message(F.text == "📊 Мой профиль")
async def show_profile(msg: Message, state: FSMContext):
    await state.clear()
    with get_session() as db:
        emp = db.query(Employee).filter_by(telegram_id=msg.from_user.id).first()
    if not emp:
        await msg.answer("Не удалось найти ваш профиль.")
        return
    profile_data = [
        f"<b>Имя:</b> {html.escape(emp.name or 'Не указано')}",
        f"<b>Роль:</b> {html.escape(emp.role or 'Не указана')}",
        f"<b>Email:</b> {html.escape(emp.email)}",
        f"<b>Контактная информация:</b> {html.escape(emp.contact_info or 'Не указана')}"
    ]
    caption = "\n".join(profile_data)
    if emp.photo_file_id:
        try:
            await msg.answer_photo(
                photo=emp.photo_file_id,
                caption=caption,
                reply_markup=get_profile_kb()
            )
        except Exception as e:
            logger.error(f"Failed to send photo by file_id for user {emp.id}: {e}")
            await msg.answer(caption, reply_markup=get_profile_kb())
    else:
        await msg.answer(f"📸 У вас пока нет аватара.\n\n{caption}", reply_markup=get_profile_kb())


@dp.callback_query(F.data == "profile_back")
async def process_profile_back(cb: CallbackQuery, state: FSMContext):
    await cb.message.delete()
    await state.clear()
    with get_session() as db:
        emp = db.query(Employee).filter_by(telegram_id=cb.from_user.id).first()
    kb = admin_kb if emp and emp.role == "Admin" else employee_main_kb
    await cb.message.answer("Вы в главном меню.", reply_markup=kb)
    await cb.answer()


def get_edit_profile_kb() -> InlineKeyboardMarkup:
    """Возвращает клавиатуру для редактирования профиля."""
    buttons = [
        [InlineKeyboardButton(text="🖼️ Изменить аватар", callback_data="edit_field:photo")],
        [InlineKeyboardButton(text="👤 Изменить имя", callback_data="edit_field:name")],
        [InlineKeyboardButton(text="✉️ Изменить почту", callback_data="edit_field:email")],
        [InlineKeyboardButton(text="📞 Изменить контакты", callback_data="edit_field:contact_info")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="edit_cancel")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.callback_query(F.data == "profile_edit")
async def start_profile_edit(cb: CallbackQuery, state: FSMContext):
    new_text = "Выберите, что хотите изменить, или отправьте новое фото."
    new_kb = get_edit_profile_kb()

    if cb.message.photo:
        await cb.message.edit_caption(caption=new_text, reply_markup=new_kb)
    else:
        await cb.message.edit_text(new_text, reply_markup=new_kb)

    await state.set_state(EditProfile.choosing_field)
    await cb.answer()


@dp.callback_query(F.data == "edit_cancel")
async def cancel_profile_edit(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.delete()
    await show_profile(cb.message, state)
    await cb.answer("Редактирование отменено.")


@dp.callback_query(F.data.startswith("edit_field:"))
async def choose_field_to_edit(cb: CallbackQuery, state: FSMContext):
    field_to_edit = cb.data.split(":")[1]
    prompts = {
        "photo": "📸 Отправьте мне новое фото для аватара.",
        "name": "👤 Введите ваше новое имя.",
        "email": "✉️ Введите новый email (пример: name@domain.com).",
        "contact_info": "📞 Введите новую контактную информацию (например, номер телефона)."
    }
    await state.update_data(field_to_edit=field_to_edit)
    await state.set_state(EditProfile.waiting_for_new_value)
    await cb.message.answer(prompts.get(field_to_edit, "Введите новое значение:"))
    await cb.answer()


@dp.message(EditProfile.waiting_for_new_value, F.photo)
async def handle_new_photo(msg: Message, state: FSMContext):
    data = await state.get_data()
    if data.get("field_to_edit") != "photo":
        await msg.answer("Сейчас я ожидаю текстовое значение. Чтобы сменить фото, нажмите кнопку 'Изменить аватар'.")
        return

    photo_file_id = msg.photo[-1].file_id
    with get_session() as db:
        emp = db.query(Employee).filter_by(telegram_id=msg.from_user.id).first()
        emp.photo_file_id = photo_file_id
        db.commit()

    await msg.answer("✅ Аватар успешно обновлен!")
    await state.clear()
    await show_profile(msg, state)


@dp.message(EditProfile.waiting_for_new_value, F.text)
async def handle_new_text_value(msg: Message, state: FSMContext):
    data = await state.get_data()
    field_to_edit = data.get("field_to_edit")

    if not field_to_edit or field_to_edit == "photo":
        await msg.answer("Неверное действие. Выберите поле для редактирования с помощью кнопок.")
        return

    new_value = msg.text.strip()
    if field_to_edit == "email":
        if not re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", new_value):
            await msg.answer(
                "❌ Неверный формат email. Пример: name@domain.com\nПопробуйте ещё раз или нажмите «❌ Отмена».")
            return
        new_value = new_value.lower()
        with get_session() as db:
            emp = db.query(Employee).filter_by(telegram_id=msg.from_user.id).first()
            conflict = db.query(Employee).filter(
                Employee.email == new_value,
                Employee.id != emp.id
            ).first()
            if conflict:
                await msg.answer("❌ Такой email уже используется другим пользователем. Введите другой адрес.")
                return
            emp.email = new_value
            db.commit()
        await msg.answer("✅ Email успешно обновлён!")
        await state.clear()
        await show_profile(msg, state)
        return

    with get_session() as db:
        emp = db.query(Employee).filter_by(telegram_id=msg.from_user.id).first()
        setattr(emp, field_to_edit, new_value)
        db.commit()

    await msg.answer(f"✅ Поле '{field_to_edit}' успешно обновлено!")
    await state.clear()
    await show_profile(msg, state)


# --- Учет времени ---

@dp.message(F.text == "⏰ Учет времени")
@access_check
async def show_time_tracking_menu(message: Message, state: FSMContext, **kwargs):
    await state.clear()
    await message.answer("Выберите действие:", reply_markup=time_tracking_kb)


@dp.message(F.text == "✅ Я на месте")
@access_check
async def ask_arrival(msg: Message, state: FSMContext, **kwargs):
    await state.update_data(tracking="arrival")
    await state.set_state(TimeTracking.waiting_location)
    await msg.answer(
        "Отлично! Теперь отправьте мне вашу геолокацию или нажмите «🔙 Назад»:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📍 Отправить локацию", request_location=True)],
                [BACK_BTN]
            ],
            resize_keyboard=True
        )
    )


@dp.message(F.text == "👋 Я ухожу")
@access_check
async def ask_departure(msg: Message, state: FSMContext, **kwargs):
    await state.update_data(tracking="departure")
    await state.set_state(TimeTracking.waiting_location)
    await msg.answer(
        "Хорошо! Отправьте, пожалуйста, геолокацию или нажмите «🔙 Назад»:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📍 Отправить локацию", request_location=True)],
                [BACK_BTN]
            ],
            resize_keyboard=True
        )
    )


@dp.message(TimeTracking.waiting_location, F.location)
@access_check
async def process_time_tracking(msg: Message, state: FSMContext, **kwargs):
    data = await state.get_data()
    kind = data.get("tracking")
    await state.clear()

    # --- ИЗМЕНЕНИЕ: Загружаем настройки из БД ---
    with get_session() as db:
        try:
            # Используем get_config_value_sync, который уже есть в вашем коде
            office_lat = float(get_config_value_sync("OFFICE_LAT", "0.0"))
            office_lon = float(get_config_value_sync("OFFICE_LON", "0.0"))
            office_radius = int(get_config_value_sync("OFFICE_RADIUS_METERS", "300"))

            if office_lat == 0.0 or office_lon == 0.0:
                logger.error("OFFICE_LAT или OFFICE_LON не найдены в config_settings БД.")
                await msg.answer("❌ Ошибка конфигурации: не заданы координаты офиса. Обратитесь к администратору.")
                return

        except ValueError:
            await msg.answer("❌ Ошибка конфигурации: неверные значения координат офиса в базе данных.")
            return

        distance = haversine(
            msg.location.latitude, msg.location.longitude,
            office_lat, office_lon  # Используем переменные из БД
        )
        if distance > office_radius:
            return await msg.answer(f"❌ Слишком далеко от офиса ({int(distance)} м).")
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---

        # Этот блок остается без изменений, но отступ `with` убран,
        # так как `db` уже открыта выше.
        emp = db.query(Employee).filter_by(telegram_id=msg.from_user.id).first()
        almaty_now = now_almaty()
        today = almaty_now.date()
        now = almaty_now.time()
        rec = db.query(Attendance).filter_by(employee_id=emp.id, date=today).first()
        if not rec:
            rec = Attendance(employee_id=emp.id, date=today)
            db.add(rec)
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                rec = db.query(Attendance).filter_by(employee_id=emp.id, date=today).first()

        if kind == "arrival":
            if rec.arrival_time:
                resp = "🤔 Уже отмечали приход сегодня."
            else:
                rec.arrival_time = now
                db.commit()
                resp = f"✅ Приход зафиксирован в {now.strftime('%H:%M:%S')}."
        else:
            if not rec.arrival_time:
                resp = "🤔 Сначала отметьте приход."
            elif rec.departure_time:
                resp = "🤔 Уже отмечали уход сегодня."
            else:
                rec.departure_time = now
                db.commit()
                resp = f"👋 Уход зафиксирован в {now.strftime('%H:%M:%S')}."

        kb = admin_kb if emp.role == "Admin" else employee_main_kb
        await msg.answer(resp, reply_markup=kb)

# --- Прочие функции сотрудника ---

@dp.message(F.text == "🎉 Посмотреть ивенты")
@access_check
async def view_events(msg: Message, state: FSMContext, **kwargs):
    almaty_now = now_almaty().replace(tzinfo=None)
    with get_session() as db:
        upcoming_events = (
            db.query(Event)
            .filter(Event.event_date >= almaty_now)
            .order_by(Event.event_date)
            .all()
        )

    if not upcoming_events:
        await msg.answer("😢 Пока нет предстоящих ивентов.")
        return
    response = "<b>🎉 Предстоящие ивенты:</b>\n\n"
    for event in upcoming_events:
        event_date_str = event.event_date.strftime("%d.%m.%Y в %H:%M")
        response += (f"<b>{html.escape(event.title)}</b>\n"
                     f"<i>{html.escape(event.description)}</i>\n"
                     f"<b>Когда:</b> {event_date_str}\n\n")
    await msg.answer(response)


@dp.message(F.text == "💡 Поделиться идеей")
@access_check
async def share_idea_start(msg: Message, state: FSMContext, **kwargs):
    await msg.answer("Напишите вашу идею или предложение. Администрация обязательно рассмотрит его.",
                     reply_markup=ReplyKeyboardMarkup(keyboard=[[BACK_BTN]], resize_keyboard=True))
    await state.set_state(SubmitIdea.waiting_for_idea)


@dp.message(SubmitIdea.waiting_for_idea)
async def process_idea(msg: Message, state: FSMContext, **kwargs):
    if msg.text == "🔙 Назад":
        await state.clear()
        with get_session() as db:
            emp = db.query(Employee).filter_by(telegram_id=msg.from_user.id).first()
        kb = admin_kb if emp and emp.role == "Admin" else employee_main_kb
        await msg.answer("Главное меню.", reply_markup=kb)
        return

    with get_session() as db:
        emp = db.query(Employee).filter_by(telegram_id=msg.from_user.id, is_active=True).first()
        if not emp: return

        new_idea = Idea(employee_id=emp.id, text=msg.text)
        db.add(new_idea)
        db.commit()

    kb = admin_kb if emp and emp.role == "Admin" else employee_main_kb
    await msg.answer("Спасибо! Ваша идея принята.", reply_markup=kb)
    await state.clear()


# --- Наши сотрудники ---

def get_employees_menu_kb() -> InlineKeyboardMarkup:
    """Возвращает клавиатуру для раздела 'Наши сотрудники'."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗂 По отделам", callback_data="browse_by_role")],
        [InlineKeyboardButton(text="🔎 Поиск по имени", callback_data="search_by_name")],
    ])


@dp.message(F.text == "👥 Наши сотрудники")
@access_check
async def show_employees_main_menu(msg: Message, state: FSMContext, **kwargs):
    await state.clear()
    await msg.answer(
        "Как вы хотите найти сотрудника?",
        reply_markup=get_employees_menu_kb()
    )


@dp.callback_query(F.data == "search_by_name")
async def start_employee_search(cb: CallbackQuery, state: FSMContext):
    await state.set_state(FindEmployee.waiting_for_name)
    await cb.message.edit_text(
        "Введите имя или фамилию сотрудника для поиска.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_employees_menu")]
        ])
    )
    await cb.answer()


@dp.message(FindEmployee.waiting_for_name, F.text)
async def process_employee_search(msg: Message, state: FSMContext):
    await state.clear()
    query = msg.text.strip()
    with get_session() as db:
        found_employees = db.query(Employee).filter(
            Employee.name.ilike(f'%{query}%'),
            Employee.is_active == True
        ).all()

    if not found_employees:
        await msg.answer(
            f"😔 Сотрудники с именем '{html.escape(query)}' не найдены.",
            reply_markup=get_employees_menu_kb()
        )
        return

    buttons = [
        [InlineKeyboardButton(text=emp.name, callback_data=f"view_employee:{emp.id}")]
        for emp in found_employees
    ]
    buttons.append([InlineKeyboardButton(text="🔙 Назад к выбору", callback_data="back_to_employees_menu")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    await msg.answer(f"<b>🔎 Результаты поиска по запросу '{html.escape(query)}':</b>", reply_markup=kb)


@dp.callback_query(F.data == "browse_by_role")
async def browse_by_role(cb: CallbackQuery):
    await send_roles_page(chat_id=cb.message.chat.id, message_id=cb.message.message_id)
    await cb.answer()


async def send_employee_buttons_by_role(chat_id: int, message_id: int, role: str, page: int = 0):
    with get_session() as db:
        offset = page * PAGE_SIZE
        employees_on_page = db.query(Employee).filter(
            Employee.role == role, Employee.is_active == True
        ).offset(offset).limit(PAGE_SIZE).all()
        total_employees = db.query(func.count(Employee.id)).filter(
            Employee.role == role, Employee.is_active == True
        ).scalar()

    text = f"<b>Сотрудники отдела '{html.escape(role)}' (Стр. {page + 1}):</b>"
    buttons = [
        [InlineKeyboardButton(text=emp.name, callback_data=f"view_employee:{emp.id}")]
        for emp in employees_on_page
    ]
    pagination_row = []
    tok = role_to_token(role)
    if page > 0:
        pagination_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"role_page:{tok}:{page - 1}"))
    if (page + 1) * PAGE_SIZE < total_employees:
        pagination_row.append(InlineKeyboardButton(text="➡️", callback_data=f"role_page:{tok}:{page + 1}"))
    if pagination_row:
        buttons.append(pagination_row)
    buttons.append([InlineKeyboardButton(text="🔙 Назад к отделам", callback_data="back_to_roles")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    await bot.edit_message_text(text=text, chat_id=chat_id, message_id=message_id, reply_markup=kb)


@dp.callback_query(F.data.startswith("role_select:"))
async def handle_role_select(cb: CallbackQuery):
    _, tok, page_str = cb.data.split(":")
    role = token_to_role(tok)
    if not role:
        await cb.answer("Раздел не найден.", show_alert=True)
        return
    await send_employee_buttons_by_role(cb.message.chat.id, cb.message.message_id, role, int(page_str))
    await cb.answer()


@dp.callback_query(F.data.startswith("role_page:"))
async def handle_employee_page_switch(cb: CallbackQuery):
    _, tok, page_str = cb.data.split(":")
    role = token_to_role(tok)
    if not role:
        await cb.answer("Страница не найдена.", show_alert=True)
        return
    await send_employee_buttons_by_role(cb.message.chat.id, cb.message.message_id, role, int(page_str))
    await cb.answer()


@dp.callback_query(F.data.startswith("view_employee:"))
async def show_employee_profile(cb: CallbackQuery, state: FSMContext):
    """Показывает подробную карточку сотрудника с фото и контактами."""
    await state.clear()
    employee_id = int(cb.data.split(":")[1])

    with get_session() as db:
        emp = db.get(Employee, employee_id)
    if not emp:
        await cb.answer("Не удалось найти сотрудника.", show_alert=True)
        return

    profile_text = [
        f"<b>Имя:</b> {html.escape(emp.name or 'Не указано')}",
        f"<b>Роль:</b> {html.escape(emp.role or 'Не указана')}",
        f"<b>Email:</b> {html.escape(emp.email)}",
        f"<b>Контакт:</b> {html.escape(emp.contact_info or 'Не указан')}"
    ]
    caption = "\n".join(profile_text)
    buttons = []
    if emp.telegram_id:
        buttons.append([InlineKeyboardButton(text="💬 Связаться в Telegram", url=f"tg://user?id={emp.telegram_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_employees_menu")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await cb.message.delete()
    if emp.photo_file_id:
        await cb.message.answer_photo(photo=emp.photo_file_id, caption=caption, reply_markup=kb)
    else:
        await cb.message.answer(f"📸 Аватар не установлен.\n\n{caption}", reply_markup=kb)
    await cb.answer()


@dp.callback_query(F.data == "back_to_employees_menu")
async def back_to_employees_menu(cb: CallbackQuery, state: FSMContext):
    """Возвращает пользователя в главное меню раздела 'Наши сотрудники'."""
    await state.clear()
    await cb.message.delete()
    await cb.message.answer(
        "Как вы хотите найти сотрудника?",
        reply_markup=get_employees_menu_kb()
    )
    await cb.answer()


@dp.callback_query(F.data == "back_to_roles")
async def handle_back_to_roles(cb: CallbackQuery):
    """Возвращает к списку отделов."""
    await send_roles_page(chat_id=cb.message.chat.id, message_id=cb.message.message_id)
    await cb.answer()


async def send_roles_page(chat_id: int, message_id: int | None = None):
    with get_session() as db:
        roles = (
            db.query(Employee.role)
            .filter(Employee.is_active == True, Employee.role != None)
            .distinct()
            .all()
        )

    text = "Выберите отдел для просмотра сотрудников:"
    buttons = []
    for (role,) in roles:
        tok = role_to_token(role)
        buttons.append([InlineKeyboardButton(text=role, callback_data=f"role_select:{tok}:0")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_employees_menu")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    if message_id:
        await bot.edit_message_text(text=text, chat_id=chat_id, message_id=message_id, reply_markup=kb)
    else:
        await bot.send_message(chat_id, text, reply_markup=kb)


# --- База знаний ---

def get_kb_menu_kb() -> InlineKeyboardMarkup:
    """Возвращает клавиатуру для выбора разделов Базы Знаний."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧠 База знаний", callback_data="kb_show_topics")],
        [InlineKeyboardButton(text="📚 Регламенты и гайды", callback_data="kb_show_guides")]
    ])


@dp.message(F.text == "🧠 База знаний")
@access_check
async def show_kb_main_menu(message: Message, state: FSMContext, **kwargs):
    await state.clear()
    await message.answer("Выберите раздел Базы Знаний:", reply_markup=get_kb_menu_kb())


@dp.callback_query(F.data == "kb_show_topics")
async def show_kb_topics_handler(cb: CallbackQuery):
    await send_kb_page(chat_id=cb.message.chat.id, message_id=cb.message.message_id)
    await cb.answer()


@dp.callback_query(F.data == "kb_show_guides")
async def show_role_guides(cb: CallbackQuery):
    await cb.answer()
    with get_session() as db:
        emp = db.query(Employee).filter_by(telegram_id=cb.from_user.id).first()
        if not emp:
            await cb.message.edit_text("Не удалось найти ваш профиль.")
            return
        guides = db.query(RoleGuide).filter_by(role=emp.role).order_by(RoleGuide.order_index).all()

    if not guides:
        await cb.message.edit_text(f"Для вашей должности '{emp.role}' пока нет специальных регламентов.",
                                   reply_markup=get_kb_menu_kb())
        return

    await cb.message.edit_text(f"<b>Регламенты для должности «{emp.role}»:</b>")
    for guide in guides:
        text = f"<b>{html.escape(guide.title)}</b>"
        if guide.content:
            text += f"\n\n{render_rich(guide.content)}"
        await cb.message.answer(text)

        # --- ИЗМЕНЕНИЕ: Отправляем из БД (guide.file_data) ---
        if guide.file_data:
            try:
                # Используем BufferedInputFile
                await cb.message.answer_document(
                    BufferedInputFile(guide.file_data, filename=guide.file_name or "document")
                )
            except Exception as e:
                logger.error(f"Не удалось отправить файл регламента из БД {guide.id}: {e}")
        await asyncio.sleep(0.5)


async def send_kb_page(chat_id: int, message_id: int | None = None, page: int = 0):
    with get_session() as db:
        offset = page * PAGE_SIZE
        topics_on_page = db.query(Topic).order_by(Topic.title).offset(offset).limit(PAGE_SIZE).all()
        total_topics = db.query(func.count(Topic.id)).scalar()

    if not total_topics:
        text, kb = "😔 В Базе Знаний пока нет ни одной статьи.", None
    else:
        text = f"<b>🧠 База знаний</b>\n\nВыберите интересующую вас тему (Страница {page + 1}):"
        buttons = [
            [InlineKeyboardButton(text=topic.title, callback_data=f"view_topic:{topic.id}:{page}")]
            for topic in topics_on_page
        ]
        pagination_row = []
        if page > 0:
            pagination_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"kb_page:{page - 1}"))
        if (page + 1) * PAGE_SIZE < total_topics:
            pagination_row.append(InlineKeyboardButton(text="➡️", callback_data=f"kb_page:{page + 1}"))
        if pagination_row:
            buttons.append(pagination_row)
        buttons.append([InlineKeyboardButton(text="🔙 Назад к выбору раздела", callback_data="back_to_kb_main_menu")])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    if message_id:
        await bot.edit_message_text(text=text, chat_id=chat_id, message_id=message_id, reply_markup=kb)
    else:
        await bot.send_message(chat_id, text, reply_markup=kb)


@dp.callback_query(F.data.startswith("kb_page:"))
async def switch_kb_page(cb: CallbackQuery):
    page = int(cb.data.split(":")[1])
    await send_kb_page(chat_id=cb.message.chat.id, message_id=cb.message.message_id, page=page)
    await cb.answer()


@dp.callback_query(F.data.startswith("view_topic:"))
async def view_kb_topic(cb: CallbackQuery):
    _, topic_id, page_to_return = cb.data.split(":")
    with get_session() as db:
        topic = db.get(Topic, int(topic_id))

    if not topic:
        await cb.answer("Статья не найдена!", show_alert=True)
        return

    text_content = f"<b>{html.escape(topic.title)}</b>\n\n{render_rich(topic.content)}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад к списку", callback_data=f"back_to_kb_list:{page_to_return}")]])
    await cb.message.delete()

    # --- ИЗМЕНЕНИЕ: Отправляем из БД (topic.image_data) ---
    if topic.image_data:
        try:
            await bot.send_photo(
                chat_id=cb.message.chat.id,
                # Используем BufferedInputFile
                photo=BufferedInputFile(topic.image_data, filename=topic.image_name or "image.jpg"),
                caption=text_content,
                reply_markup=kb
            )
        except Exception as e:
            logger.error(f"Failed to send topic photo from DB {topic.id}: {e}")
            await bot.send_message(cb.message.chat.id, text_content, reply_markup=kb, disable_web_page_preview=True)
    else:
        await bot.send_message(cb.message.chat.id, text_content, reply_markup=kb, disable_web_page_preview=True)
    await cb.answer()


@dp.callback_query(F.data.startswith("back_to_kb_list:"))
async def back_to_kb_list(cb: CallbackQuery):
    page = int(cb.data.split(":")[1])
    await cb.message.delete()
    await send_kb_page(chat_id=cb.message.chat.id, page=page)
    await cb.answer()


@dp.callback_query(F.data == "back_to_kb_main_menu")
async def back_to_kb_main_menu_handler(cb: CallbackQuery):
    await cb.message.edit_text("Выберите раздел Базы Знаний:", reply_markup=get_kb_menu_kb())
    await cb.answer()


# --- Фоновые задачи и обработка вступления в чат ---

scheduler = AsyncIOScheduler(timezone="Asia/Almaty")


@scheduler.scheduled_job("cron", hour=7, minute=30)
async def send_daily_weather():
    if not WEATHER_API_KEY:
        return logger.warning("WEATHER_API_KEY не задан. Рассылка погоды пропущена.")
    url = f"http://api.openweathermap.org/data/2.5/weather?q={WEATHER_CITY}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return logger.error(f"Ошибка получения погоды: {resp.status}")
                data = await resp.json()
                weather_text = (
                    f"☀️ <b>Доброе утро! Погода в г. {WEATHER_CITY} на сегодня:</b>\n\n"
                    f"🌡️ Температура: <b>{round(data['main']['temp'])}°C</b> (ощущается как {round(data['main']['feels_like'])}°C)\n"
                    f"📝 На небе: {data['weather'][0]['description'].capitalize()}\n"
                    f"💨 Ветер: {round(data['wind']['speed'])} м/с\n\n"
                    f"Хорошего дня!"
                )
    except Exception as e:
        return logger.error(f"Исключение при запросе погоды: {e}")

    with get_session() as db:
        user_ids = [row[0] for row in db.query(Employee.telegram_id).filter(Employee.is_active == True,
                                                                            Employee.telegram_id != None).all()]
    for user_id in user_ids:
        try:
            await bot.send_message(user_id, weather_text)
        except Exception as e:
            logger.warning(f"Не удалось отправить погоду пользователю {user_id}: {e}")
        await asyncio.sleep(0.1)


@scheduler.scheduled_job("cron", hour=00, minute=00)
async def birthday_jobs():
    active_chats_str = get_config_value_sync("ACTIVE_CHAT_IDS", "")
    if not active_chats_str:
        return logger.warning("ACTIVE_CHAT_IDS не задан в настройках. Рассылка поздравлений пропущена.")
    chat_ids = [int(cid.strip()) for cid in active_chats_str.split(',') if
                cid.strip() and cid.strip().lstrip('-').isdigit()]
    if not chat_ids: return

    almaty_today = datetime.now(ALMATY_TZ).date()
    today_md = almaty_today.strftime("%m-%d")
    with get_session() as db:
        engine_url = make_url(os.getenv("DATABASE_URL", "sqlite:///bot.db"))
        if engine_url.get_backend_name().startswith("postgres"):
            emps = db.query(Employee).filter(text("to_char(birthday, 'MM-DD') = :md and is_active = true")).params(
                md=today_md).all()
        else:
            emps = db.query(Employee).filter(func.strftime("%m-%d", Employee.birthday) == today_md,
                                             Employee.is_active == True).all()

    if not emps: return
    greeting_template = get_text("birthday_greeting", "🎂 Сегодня у {name} ({role}) день рождения! Поздрявляем! 🎉")
    for emp in emps:
        greeting_text = greeting_template.format(name=html.escape(emp.name or ""), role=html.escape(emp.role or ""))
        for chat_id in chat_ids:
            try:
                await bot.send_message(chat_id=chat_id, text=greeting_text)
            except Exception as e:
                logger.error(f"Failed to send birthday greeting for {emp.name} to chat {chat_id}: {e}")
            await asyncio.sleep(0.2)


@dp.chat_member()
async def on_user_join_tracked_chat(event: ChatMemberUpdated):
    """Реагирует на вступление пользователя в любой отслеживаемый чат из БД."""
    with get_session() as db:
        if not db.query(GroupChat).filter_by(chat_id=event.chat.id).first():
            return

    was = event.old_chat_member.status
    now = event.new_chat_member.status
    joined_from = {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED, ChatMemberStatus.RESTRICTED}
    joined_to = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}
    if not (was in joined_from and now in joined_to and not event.new_chat_member.user.is_bot):
        return

    user = event.new_chat_member.user
    with get_session() as db:
        emp = db.query(Employee).filter_by(telegram_id=user.id).first()
        # Убрана проверка emp.joined_main_chat
        if not emp or not emp.training_passed:
            return

        # Строка emp.joined_main_chat = True также удалена

    user_mention = f"@{user.username}" if user.username else user.mention_html()
    welcome_text = get_text("welcome_to_common_chat").format(
        user_mention=user_mention,
        user_name=html.escape(user.full_name),
        role=html.escape(emp.role or "—")
    )
    try:
        await bot.send_message(chat_id=event.chat.id, text=welcome_text)
        logger.info(f"Sent welcome message for {user.full_name} to chat {event.chat.id}")
    except Exception as e:
        logger.error(f"Failed to send welcome message to {event.chat.id}: {e}")


# --- Общие обработчики "Назад" ---

@dp.message(F.text == "🔙 Назад", StateFilter(None))
async def back_to_main_menu_from_reply(message: Message, state: FSMContext):
    """Возвращает пользователя в главное меню из подменю."""
    with get_session() as db:
        emp = db.query(Employee).filter_by(telegram_id=message.from_user.id).first()
    kb = admin_kb if emp and emp.role == "Admin" else employee_main_kb
    await message.answer("Вы в главном меню.", reply_markup=kb)


@dp.message(F.text == "🔙 Назад", StateFilter("*"))
async def cancel_state_and_return(msg: Message, state: FSMContext):
    """Отменяет любое состояние и возвращает в главное меню."""
    await state.clear()
    with get_session() as db:
        emp = db.query(Employee).filter_by(telegram_id=msg.from_user.id).first()
    kb = admin_kb if emp and emp.role == "Admin" else employee_main_kb
    await msg.answer("Действие отменено. Вы в главном меню.", reply_markup=kb)


async def main():
    # --- ИЗМЕНЕНИЕ: os.makedirs больше не нужен ---
    # os.makedirs(UPLOAD_FOLDER_ONBOARDING, exist_ok=True)
    await get_me_id()
    logger.info("DATABASE_URL=%s", DATABASE_URL)
    scheduler.start()
    allowed_updates = dp.resolve_used_update_types()
    logger.info("Bot starting polling... allowed_updates=%s", allowed_updates)
    await dp.start_polling(bot, allowed_updates=allowed_updates)


if __name__ == "__main__":
    asyncio.run(main())