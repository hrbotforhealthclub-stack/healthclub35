import os
import random
from datetime import datetime
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from werkzeug.utils import secure_filename
from dotenv import load_dotenv, set_key
import subprocess  # ← добавили
import asyncio  # <-- ДОБАВИТЬ
from aiogram import Bot  # <-- ДОБАВИТЬ
import threading
import html
import shutil

from models import (
    Base, engine, get_session, Employee, Event, Idea, QuizQuestion,
    RoleOnboarding, Topic, RegCode, ArchivedEmployee, Attendance,
    ArchivedAttendance, ArchivedIdea, Role,
    BotText, OnboardingQuestion, OnboardingStep, EmployeeCustomData, RoleGuide, GroupChat
)

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, session
from functools import wraps

# стало
import os
import io

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.formatting.rule import FormulaRule

# --- НАСТРОЙКА ПРИЛОЖЕНИЯ ---
load_dotenv()
app = Flask(__name__)
from datetime import datetime, date, time


@app.template_filter('fmt_dt')
def fmt_dt(value, fmt='%Y-%m-%d %H:%M'):
    if value is None:
        return ''
    if isinstance(value, (datetime, date)):
        try:
            return value.strftime(fmt)
        except Exception:
            pass
    s = str(value)
    patterns = [
        '%Y-%m-%d %H:%M:%S.%f',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%dT%H:%M',
        '%Y-%m-%d %H:%M',
        '%Y-%m-%d',
        '%d.%m.%Y %H:%M',
        '%d.%m.%Y',
    ]
    for p in patterns:
        try:
            dt = datetime.strptime(s, p)
            return dt.strftime(fmt)
        except Exception:
            continue
    return s


def _run_async_bg(coro):
    """Запустить корутину безопасно: если event loop уже крутится — уводим в поток."""
    try:
        asyncio.run(coro)
    except RuntimeError:
        threading.Thread(target=lambda: asyncio.run(coro), daemon=True).start()


@app.template_filter('fmt_date')
def fmt_date(value, fmt="%d.%m.%Y"):
    if not value:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return value  # если формат неизвестный, вернуть как есть
    return value.strftime(fmt)


@app.template_filter('fmt_time')
def fmt_time(value, fmt='%H:%M:%S'):
    if value is None:
        return ''
    return value.strftime(fmt)


app.secret_key = os.getenv("FLASK_SECRET_KEY", "a_very_secret_key_for_flask")

UPLOAD_FOLDER_ONBOARDING = 'uploads/onboarding'
UPLOAD_FOLDER_TOPICS = 'uploads/topics'
app.config['UPLOAD_FOLDER_ONBOARDING'] = UPLOAD_FOLDER_ONBOARDING
app.config['UPLOAD_FOLDER_TOPICS'] = UPLOAD_FOLDER_TOPICS

os.makedirs(UPLOAD_FOLDER_ONBOARDING, exist_ok=True)
os.makedirs(UPLOAD_FOLDER_TOPICS, exist_ok=True)
Base.metadata.create_all(engine)

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
if not ADMIN_USERNAME or not ADMIN_PASSWORD:
    raise RuntimeError("ADMIN_USERNAME/ADMIN_PASSWORD не заданы в .env — задайте перед запуском.")

ONBOARDING_DATA_KEYS = {
    'name': 'Имя (обновляет основной профиль)',
    'birthday': 'Дата рождения (обновляет профиль, формат ДД.ММ.ГГГГ)',
    'contact_info': 'Контактная информация (обновляет профиль)',
    'hobby': 'Хобби (дополнительное поле)',
    'favorite_quote': 'Любимая цитата (дополнительное поле)',
    'tshirt_size': 'Размер футболки (дополнительное поле)'
}


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)

    return wrapped


@app.before_request
def require_login_for_all():
    # Разрешаем доступ без логина для страниц логина, статики и webhooks/healthchecks при желании
    open_paths = {"/login", "/logout"}  # можно пополнять
    if request.path.startswith("/static"):
        return
    if request.path in open_paths:
        return
    if not session.get("is_admin"):
        return redirect(url_for("login", next=request.path))


def get_text(key: str, default: str = "Текст не найден") -> str:
    """Получает текст для бота из БД по ключу."""
    with get_session() as db:
        text_obj = db.get(BotText, key)
        return text_obj.text if text_obj else default


from aiogram.client.bot import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from flask import current_app


def _chat_candidates(raw: str | int) -> list:
    """
    Возвращает список возможных chat_id:
    - @username как есть
    - уже нормальный -100... как есть
    - укороченный отрицательный -XXXXXXXXX -> пробуем как есть и с префиксом -100
    """
    raw = str(raw if raw is not None else "").strip()
    if not raw:
        return []
    if raw.startswith("@"):
        return [raw]
    if raw.startswith("-100"):
        return [raw]
    if raw.startswith("-") and raw[1:].isdigit():
        return [raw, f"-100{raw[1:]}"]
    return [raw]


async def _send_tg_message(text: str, chat_id: str | None = None):
    """Единая отправка сообщений: поддерживает и числовой id (-100...), и @username."""
    token = os.getenv("BOT_TOKEN")
    cid_raw = chat_id if chat_id is not None else os.getenv("COMMON_CHAT_ID")
    cid = str(cid_raw or "").strip()

    if not token:
        print("[tg] BOT_TOKEN missing");
        return False, "BOT_TOKEN missing"
    if not cid:
        print("[tg] COMMON_CHAT_ID missing");
        return False, "COMMON_CHAT_ID missing"

    # Определяем целевой идентификатор
    s = cid.lstrip('-')
    target = int(cid) if s.isdigit() else cid

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode="HTML"))
    try:
        msg = await bot.send_message(chat_id=target, text=text)
        return True, f"sent:{msg.message_id}"
    except Exception as e:
        print(f"[tg] send error: {e}")
        return False, str(e)
    finally:
        await bot.session.close()


def notify_common_chat(text: str):
    """Удобная обёртка для отправки в общий чат в фоне."""
    _run_async_bg(_send_tg_message(text))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["is_admin"] = True
            flash("Вы вошли в админ-панель.", "success")
            next_url = request.args.get("next") or url_for("index")
            return redirect(next_url)
        else:
            error = "Неверный логин или пароль."
    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST", "GET"])
def logout():
    session.pop("is_admin", None)
    flash("Вы вышли.", "info")
    return redirect(url_for("login"))


async def _list_verified_admin_groups_async(rows):
    """Проверяем GroupChat через Телеграм и возвращаем только валидные (бот=админ)."""
    token = os.getenv("BOT_TOKEN")
    if not token:
        return []

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode="HTML"))
    out = []
    try:
        me = await bot.get_me()
        for row in rows:
            ok = False
            for cid in _chat_candidates(row.chat_id):
                try:
                    chat = await bot.get_chat(cid)
                    mem = await bot.get_chat_member(chat.id, me.id)
                    status = str(getattr(mem, "status", ""))
                    if "administrator" in status or "creator" in status:
                        norm_id = str(chat.id)
                        name = getattr(chat, "title", None) or getattr(chat, "username", None) or row.name
                        out.append({"chat_id": norm_id, "name": name, "_db_id": getattr(row, "id", None)})
                        ok = True
                        break
                except Exception:
                    continue
        return out
    finally:
        await bot.session.close()


def list_verified_admin_groups():
    """Слой над async: вытаскивает строки из БД, верифицирует, обновляет БД, отдаёт список для шаблона."""
    with get_session() as db:
        rows = db.query(GroupChat).order_by(GroupChat.name).all()

    try:
        verified = asyncio.run(_list_verified_admin_groups_async(rows))
    except RuntimeError:
        return [{"chat_id": r.chat_id, "name": r.name} for r in rows]

    with get_session() as db:
        for v in verified:
            if v["_db_id"] is None:
                continue
            obj = db.get(GroupChat, v["_db_id"])
            if obj:
                changed = False
                if obj.chat_id != v["chat_id"]:
                    obj.chat_id = v["chat_id"];
                    changed = True
                if obj.name != v["name"]:
                    obj.name = v["name"];
                    changed = True
                if changed:
                    db.add(obj)
        db.commit()

    return [{"chat_id": v["chat_id"], "name": v["name"]} for v in verified]


# --- ОСНОВНОЙ МАРШРУТ (ГЛАВНАЯ СТРАНИЦА) ---
@app.route('/')
def index():
    with get_session() as db:
        employees = db.query(Employee).filter_by(is_active=True).order_by(Employee.name).all()
        archived_employees = db.query(ArchivedEmployee).order_by(ArchivedEmployee.dismissal_date.desc()).all()
        events = db.query(Event).order_by(Event.event_date.desc()).all()
        ideas = db.query(Idea, Employee.name).outerjoin(Employee, Idea.employee_id == Employee.id).order_by(
            Idea.submission_date.desc()).all()
        topics = db.query(Topic).order_by(Topic.title).all()
        roles = db.query(Role).order_by(Role.name).all()
        bot_texts = db.query(BotText).order_by(BotText.id).all()

        onboarding_constructor_data = {}
        for role in roles:
            questions = db.query(OnboardingQuestion).filter_by(role=role.name).order_by(
                OnboardingQuestion.order_index).all()
            steps = db.query(OnboardingStep).filter_by(role=role.name).order_by(OnboardingStep.order_index).all()
            onboarding_constructor_data[role.name] = {
                "questions": questions,
                "steps": steps
            }

        role_guides_data = {}
        for role in roles:
            guides = db.query(RoleGuide).filter_by(role=role.name).order_by(RoleGuide.order_index).all()
            role_guides_data[role.name] = guides

        attendance_records = db.query(
            Attendance, Employee.name
        ).join(
            Employee, Attendance.employee_id == Employee.id
        ).order_by(
            Attendance.date.desc(), Attendance.arrival_time.desc()
        ).all()

        onboarding_data = {}
        for role in roles:
            onboarding_info = db.query(RoleOnboarding).filter_by(role=role.name).first()
            quiz_questions = db.query(QuizQuestion).filter_by(role=role.name).order_by(QuizQuestion.order_index).all()
            onboarding_data[role.name] = {
                "info": onboarding_info,
                "quizzes": quiz_questions
            }

        admin_groups = list_verified_admin_groups()

        config = {
            "BOT_TOKEN": os.getenv("BOT_TOKEN"),
            "DATABASE_URL": os.getenv("DATABASE_URL"),
            "COMMON_CHAT_ID": os.getenv("COMMON_CHAT_ID"),
            "OFFICE_LAT": os.getenv("OFFICE_LAT"),
            "OFFICE_LON": os.getenv("OFFICE_LON"),
            "OFFICE_RADIUS_METERS": os.getenv("OFFICE_RADIUS_METERS")
        }

    return render_template('index.html', employees=employees, archived_employees=archived_employees,
                           events=events, ideas=ideas, topics=topics,
                           onboarding_data=onboarding_data, roles=roles, config=config, bot_texts=bot_texts,
                           onboarding_constructor_data=onboarding_constructor_data,
                           onboarding_data_keys=ONBOARDING_DATA_KEYS,
                           attendance_records=attendance_records, role_guides_data=role_guides_data,
                           admin_groups=admin_groups, )


# --- AJAX-READY ROUTES ---

@app.route('/texts/update/<string:text_id>', methods=['POST'])
def update_text(text_id):
    with get_session() as db:
        text_obj = db.get(BotText, text_id)
        if text_obj:
            text_obj.text = request.form.get('text', '')
            db.commit()
            return jsonify({"success": True, "message": f"Текст '{text_id}' обновлен.", "category": "success"})
        return jsonify({"success": False, "message": "Текст не найден.", "category": "danger"}), 404


@app.route('/onboarding/question/add/<path:role>', methods=['POST'])
def add_onboarding_question(role):
    with get_session() as db:
        # НАХОДИМ СЛЕДУЮЩИЙ СВОБОДНЫЙ ИНДЕКС
        max_idx = db.query(func.max(OnboardingQuestion.order_index)).filter_by(role=role).scalar()
        next_idx = (max_idx or -1) + 1

        new_q = OnboardingQuestion(
            role=role,
            question_text=request.form['question_text'],
            data_key=request.form['data_key'],
            is_required=('is_required' in request.form),
            order_index=next_idx  # <-- ПРИСВАИВАЕМ ПРАВИЛЬНЫЙ ИНДЕКС
        )
        db.add(new_q)
        db.commit()

        return jsonify({
            "success": True, "message": "Новый вопрос для онбординга добавлен.", "category": "success",
            "item": {
                "id": new_q.id, "question_text": new_q.question_text, "data_key": new_q.data_key,
                "is_required": new_q.is_required, "delete_url": url_for('delete_onboarding_question', q_id=new_q.id)
            }
        })


@app.route('/onboarding/question/delete/<int:q_id>', methods=['POST'])
def delete_onboarding_question(q_id):
    with get_session() as db:
        q = db.get(OnboardingQuestion, q_id)
        if q:
            db.delete(q)
            db.commit()
            return jsonify({"success": True, "message": "Вопрос онбординга удален.", "category": "warning"})
    return jsonify({"success": False, "message": "Вопрос не найден.", "category": "danger"}), 404


@app.route('/onboarding/question/reorder', methods=['POST'])
def reorder_onboarding_question():
    ordered_ids = request.get_json(silent=True).get('ordered_ids', [])
    if not ordered_ids:
        return jsonify(success=False, message="Нет данных для сортировки"), 400

    with get_session() as session:
        # 1. Сначала загружаем все нужные объекты в словарь для быстрого доступа
        questions_map = {
            str(q.id): q for q in session.query(OnboardingQuestion).filter(
                OnboardingQuestion.id.in_([int(i) for i in ordered_ids])
            ).all()
        }

        # 2. Теперь изменяем их в памяти, не вызывая преждевременных запросов к БД
        for index, qid in enumerate(ordered_ids):
            if qid in questions_map:
                questions_map[qid].order_index = index

        # 3. Сохраняем все изменения одним коммитом
        session.commit()

    return jsonify(success=True)

@app.route('/onboarding/step/add/<path:role>', methods=['POST'])
def add_onboarding_step(role):
    with get_session() as db:
        new_step = OnboardingStep(
            role=role,
            message_text=request.form.get('message_text'),
            file_type=request.form.get('file_type', 'document')
        )
        if 'file' in request.files and request.files['file'].filename != '':
            file = request.files['file']
            filename = secure_filename(f"{role.lower()}_step_{file.filename}")
            upload_folder = os.path.join(app.config['UPLOAD_FOLDER_ONBOARDING'], 'steps')
            os.makedirs(upload_folder, exist_ok=True)
            file_path = os.path.join(upload_folder, filename)
            file.save(file_path)
            new_step.file_path = file_path
        db.add(new_step)
        db.commit()

        return jsonify({
            "success": True, "message": "Новый шаг знакомства добавлен.", "category": "success",
            "item": {
                "id": new_step.id, "message_text": new_step.message_text, "file_type": new_step.file_type,
                "file_path": new_step.file_path, "delete_url": url_for('delete_onboarding_step', step_id=new_step.id)
            }
        })


@app.route('/onboarding/step/delete/<int:step_id>', methods=['POST'])
def delete_onboarding_step(step_id):
    with get_session() as db:
        step = db.get(OnboardingStep, step_id)
        if step:
            if step.file_path and os.path.exists(step.file_path):
                os.remove(step.file_path)
            db.delete(step)
            db.commit()
            return jsonify({"success": True, "message": "Шаг знакомства удален.", "category": "warning"})
    return jsonify({"success": False, "message": "Шаг не найден.", "category": "danger"}), 404


@app.route('/onboarding/step/reorder', methods=['POST'])
def reorder_onboarding_step():
    ordered_ids = request.get_json(silent=True).get('ordered_ids', [])
    with get_session() as session:
        for index, sid in enumerate(ordered_ids):
            step = session.get(OnboardingStep, int(sid))
            if step:
                step.order_index = index
        session.commit()
    return jsonify(success=True)


@app.route('/role/add', methods=['POST'])
def add_role():
    with get_session() as db:
        role_name = request.form.get('role_name')
        if not role_name:
            return jsonify({"success": False, "message": "Имя роли не указано.", "category": "danger"}), 400
        if db.query(Role).filter_by(name=role_name).first():
            return jsonify(
                {"success": False, "message": f"Роль '{role_name}' уже существует.", "category": "danger"}), 409
        new_role = Role(name=role_name)
        db.add(new_role)
        db.commit()
        return jsonify({
            "success": True, "message": f"Роль '{role_name}' добавлена.", "category": "success",
            "role": {"id": new_role.id, "name": new_role.name}
        })


@app.route('/role/delete/<int:role_id>', methods=['POST'])
def delete_role(role_id):
    with get_session() as db:
        role = db.get(Role, role_id)
        if role:
            db.delete(role)
            db.commit()
            return jsonify({"success": True, "message": f"Роль '{role.name}' удалена.", "category": "warning"})
    return jsonify({"success": False, "message": "Роль не найдена.", "category": "danger"}), 404


@app.route('/employee/add', methods=['POST'])
def add_employee():
    with get_session() as db:
        email, role = request.form.get('email'), request.form.get('role')
        if not email or not role:
            return jsonify({"success": False, "message": "Email и роль обязательны.", "category": "danger"}), 400
        if db.query(Employee).filter_by(email=email).first():
            return jsonify(
                {"success": False, "message": f"Сотрудник с email {email} уже существует.", "category": "danger"}), 409

        new_emp = Employee(email=email, name=email, role=role, is_active=True)
        db.add(new_emp)
        db.commit()

        return jsonify({
            "success": True, "message": f"Сотрудник с email {email} добавлен. Сгенерируйте код для регистрации.",
            "category": "success",
            "employee": {"id": new_emp.id, "name": new_emp.name, "email": new_emp.email, "role": new_emp.role}
        })


@app.route('/employee/reset_progress/<int:emp_id>', methods=['POST'])
def reset_progress(emp_id):
    with get_session() as db:
        emp = db.get(Employee, emp_id)
        if not emp:
            return jsonify({"success": False, "message": "Сотрудник не найден.", "category": "danger"}), 404
        emp.onboarding_completed = False
        emp.training_passed = False
        db.query(EmployeeCustomData).filter_by(employee_id=emp_id).delete(synchronize_session=False)
        db.commit()
        if emp.telegram_id:
            _run_async_bg(
                bot.send_message(emp.telegram_id, get_text('progress_reset_notification', 'Ваш прогресс сброшен.')))
        return jsonify({"success": True, "message": f"Прогресс для {emp.name} сброшен.", "category": "warning"})


@app.route('/broadcast/send', methods=['POST'])
def send_broadcast():
    message_text, target_role = request.form.get('message_text'), request.form.get('target_role')
    if not message_text:
        return jsonify(
            {"success": False, "message": "Текст сообщения не может быть пустым.", "category": "danger"}), 400
    with get_session() as db:
        query = db.query(Employee.telegram_id).filter(Employee.is_active == True, Employee.telegram_id != None)
        if target_role != 'all': query = query.filter(Employee.role == target_role)
        target_users_ids = [row[0] for row in query.all()]
    if not target_users_ids:
        return jsonify({"success": False, "message": "Не найдено сотрудников для рассылки.", "category": "warning"})

    async def _send_to_all():
        tg_bot = Bot(token=os.getenv("BOT_TOKEN"))
        try:
            for user_id in target_users_ids:
                try:
                    await tg_bot.send_message(chat_id=user_id, text=message_text)
                except Exception:
                    pass
                await asyncio.sleep(0.1)
        finally:
            await tg_bot.session.close()

    _run_async_bg(_send_to_all())
    return jsonify({"success": True, "message": f"Рассылка для {len(target_users_ids)} пользователей запущена.",
                    "category": "info"})


@app.route('/employee/edit/<int:emp_id>', methods=['POST'])
def edit_employee(emp_id):
    with get_session() as db:
        emp = db.get(Employee, emp_id)
        if not emp:
            return jsonify({"success": False, "message": "Сотрудник не найден.", "category": "danger"}), 404
        emp.name = request.form.get('name', emp.name)
        emp.email = request.form.get('email', emp.email)
        emp.role = request.form.get('role', emp.role)
        birthday_str = request.form.get('birthday')
        emp.birthday = datetime.strptime(birthday_str, '%Y-%m-%d').date() if birthday_str else None
        db.commit()
        return jsonify({
            "success": True, "message": f"Данные сотрудника {emp.name} обновлены.", "category": "success",
            "employee": {"id": emp.id, "name": emp.name, "email": emp.email, "role": emp.role}
        })


@app.route('/employee/dismiss/<int:emp_id>', methods=['POST'])
def dismiss_employee(emp_id):
    with get_session() as db:
        emp = db.get(Employee, emp_id)
        if not emp:
            return jsonify({"success": False, "message": "Сотрудник не найден.", "category": "danger"}), 404
        name_cache, role_cache = emp.name or emp.email, emp.role or ''
        emp.is_active = False
        emp.registered = False
        emp.telegram_id = None
        db.commit()
        dismiss_text = get_text('employee_dismissed_announcement', '👋 {name} ({role}) больше не с нами.').format(
            name=html.escape(name_cache), role=html.escape(role_cache))
        notify_common_chat(dismiss_text)
        return jsonify({"success": True, "message": f"Сотрудник {name_cache} деактивирован.", "category": "warning",
                        "action": "reload"})


@app.route('/employee/reset_telegram/<int:emp_id>', methods=['POST'])
def reset_telegram(emp_id):
    with get_session() as db:
        emp = db.get(Employee, emp_id)
        if not emp: return jsonify({"success": False, "message": "Сотрудник не найден.", "category": "danger"}), 404
        emp.telegram_id = None
        emp.registered = False
        db.commit()
        return jsonify(
            {"success": True, "message": f"Привязка Telegram для {emp.name} сброшена.", "category": "warning"})


@app.route('/employee/generate_code/<int:emp_id>', methods=['POST'])
def generate_new_code(emp_id):
    with get_session() as db:
        emp = db.get(Employee, emp_id)
        if not emp: return jsonify({"success": False, "message": "Сотрудник не найден.", "category": "danger"}), 404
        while True:
            code = "".join(str(random.randint(0, 9)) for _ in range(8))
            if not db.query(RegCode).filter_by(code=code).first(): break
        db.add(RegCode(code=code, email=emp.email, used=False))
        db.commit()
        return jsonify({"success": True, "message": f"Новый код для {emp.name}: {code}", "category": "success"})


@app.route('/onboarding/update/<path:role>', methods=['POST'])
def update_onboarding(role):
    with get_session() as db:
        onboarding = db.query(RoleOnboarding).filter_by(role=role).first()
        if not onboarding:
            onboarding = RoleOnboarding(role=role)
            db.add(onboarding)
        onboarding.text = request.form['text']
        onboarding.file_type = request.form['file_type']
        if 'file' in request.files and request.files['file'].filename != '':
            file = request.files['file']
            filename = secure_filename(f"{role.lower()}_{file.filename}")
            file_path = os.path.join(app.config['UPLOAD_FOLDER_ONBOARDING'], filename)
            file.save(file_path)
            onboarding.file_path = file_path
        db.commit()
    return jsonify({"success": True, "message": f"Онбординг для '{role}' обновлен.", "category": "success"})


@app.route('/quiz/add/<path:role>', methods=['POST'])
def add_quiz(role):
    with get_session() as db:
        qtype, question = request.form['question_type'], request.form['question']
        options = request.form.get('options') if qtype == 'choice' else None
        answer = request.form.get('answer') if qtype == 'choice' else request.form.get('text_answer')
        max_idx = db.query(func.max(QuizQuestion.order_index)).filter_by(role=role).scalar() or -1
        new_q = QuizQuestion(role=role, question=question, answer=answer, question_type=qtype, options=options,
                             order_index=max_idx + 1)
        db.add(new_q)
        db.commit()
        return jsonify({
            "success": True, "message": "Новый вопрос добавлен.", "category": "success",
            "item": {"id": new_q.id, "question": new_q.question, "answer": new_q.answer,
                     "delete_url": url_for('delete_quiz', quiz_id=new_q.id)}
        })


@app.route('/quiz/edit/<int:quiz_id>', methods=['POST'])
def edit_quiz(quiz_id):
    with get_session() as db:
        quiz = db.get(QuizQuestion, quiz_id)
        if not quiz: return jsonify({"success": False, "message": "Вопрос не найден.", "category": "danger"}), 404
        qtype = request.form['question_type']
        quiz.question = request.form['question']
        quiz.options = request.form.get('options') if qtype == 'choice' else None
        quiz.answer = request.form.get('answer') if qtype == 'choice' else request.form.get('text_answer')
        quiz.question_type = qtype
        db.commit()
        return jsonify({"success": True, "message": "Вопрос обновлен.", "category": "success"})


@app.route('/quiz/delete/<int:quiz_id>', methods=['POST'])
def delete_quiz(quiz_id):
    with get_session() as db:
        quiz = db.get(QuizQuestion, quiz_id)
        if quiz:
            db.delete(quiz)
            db.commit()
            return jsonify({"success": True, "message": "Вопрос квиза удален.", "category": "warning"})
    return jsonify({"success": False, "message": "Вопрос не найден.", "category": "danger"}), 404


@app.route('/quiz/reorder', methods=['POST'])
def reorder_quiz():
    ordered_ids = request.get_json(silent=True).get('ordered_ids', [])
    with get_session() as session:
        for index, qid in enumerate(ordered_ids):
            quiz = session.get(QuizQuestion, int(qid))
            if quiz: quiz.order_index = index
        session.commit()
    return jsonify(success=True)


@app.route('/event/add', methods=['POST'])
def add_event():
    with get_session() as db:
        title, description = request.form['title'], request.form['description']
        event_dt = datetime.strptime(request.form['event_date'], '%Y-%m-%dT%H:%M')
        new_event = Event(title=title, description=description, event_date=event_dt)
        db.add(new_event)
        db.commit()
        notify_common_chat(
            get_text('event_created_announcement', '📅 {title}\n{when}\n{desc}').format(title=html.escape(title),
                                                                                       when=event_dt.strftime(
                                                                                           '%d.%m.%Y %H:%M'),
                                                                                       desc=html.escape(description)))
        return jsonify({"success": True, "message": "Новый ивент добавлен.", "category": "success", "action": "reload"})


@app.route('/event/edit/<int:event_id>', methods=['POST'])
def edit_event(event_id):
    with get_session() as db:
        event = db.get(Event, event_id)
        if not event: return jsonify({"success": False, "message": "Ивент не найден.", "category": "danger"}), 404
        event.title, event.description = request.form['title'], request.form['description']
        event.event_date = datetime.strptime(request.form['event_date'], '%Y-%m-%dT%H:%M')
        db.commit()
        return jsonify({"success": True, "message": "Ивент обновлен.", "category": "success", "action": "reload"})


@app.route('/event/delete/<int:event_id>', methods=['POST'])
def delete_event(event_id):
    with get_session() as db:
        event = db.get(Event, event_id)
        if event:
            db.delete(event)
            db.commit()
            return jsonify({"success": True, "message": "Ивент удален.", "category": "warning", "action": "reload"})
    return jsonify({"success": False, "message": "Ивент не найден.", "category": "danger"}), 404


@app.route('/idea/delete/<int:idea_id>', methods=['POST'])
def delete_idea(idea_id):
    with get_session() as db:
        idea = db.get(Idea, idea_id)
        if idea:
            db.delete(idea)
            db.commit()
            return jsonify({"success": True, "message": "Идея удалена.", "category": "warning", "action": "reload"})
    return jsonify({"success": False, "message": "Идея не найдена.", "category": "danger"}), 404


@app.route('/topic/add', methods=['POST'])
def add_topic():
    with get_session() as db:
        new_topic = Topic(title=request.form['title'], content=request.form['content'])
        if 'image' in request.files and request.files['image'].filename != '':
            file = request.files['image']
            filename = secure_filename(file.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER_TOPICS'], filename)
            file.save(file_path)
            new_topic.image_path = file_path
        db.add(new_topic)
        db.commit()
    return jsonify({"success": True, "message": "Новая тема создана.", "category": "success", "action": "reload"})


@app.route('/topic/edit/<int:topic_id>', methods=['POST'])
def edit_topic(topic_id):
    with get_session() as db:
        topic = db.get(Topic, topic_id)
        if not topic: return jsonify({"success": False, "message": "Тема не найдена.", "category": "danger"}), 404
        topic.title, topic.content = request.form['title'], request.form['content']
        if 'image' in request.files and request.files['image'].filename != '':
            file = request.files['image']
            if topic.image_path and os.path.exists(topic.image_path): os.remove(topic.image_path)
            filename = secure_filename(file.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER_TOPICS'], filename)
            file.save(file_path)
            topic.image_path = file_path
        db.commit()
    return jsonify({"success": True, "message": "Тема обновлена.", "category": "success", "action": "reload"})


@app.route('/topic/delete/<int:topic_id>', methods=['POST'])
def delete_topic(topic_id):
    with get_session() as db:
        topic = db.get(Topic, topic_id)
        if topic:
            if topic.image_path and os.path.exists(topic.image_path): os.remove(topic.image_path)
            db.delete(topic)
            db.commit()
            return jsonify({"success": True, "message": "Тема удалена.", "category": "warning", "action": "reload"})
    return jsonify({"success": False, "message": "Тема не найдена.", "category": "danger"}), 404


@app.route('/guide/add/<path:role>', methods=['POST'])
def add_guide(role):
    with get_session() as db:
        max_idx = db.query(func.max(RoleGuide.order_index)).filter_by(role=role).scalar() or -1
        new_guide = RoleGuide(role=role, title=request.form['title'], content=request.form.get('content', ''),
                              order_index=max_idx + 1)
        if 'file' in request.files and request.files['file'].filename != '':
            file = request.files['file']
            upload_folder = os.path.join(app.config['UPLOAD_FOLDER_ONBOARDING'], 'guides')
            os.makedirs(upload_folder, exist_ok=True)
            filename = secure_filename(f"{role.lower()}_guide_{file.filename}")
            file_path = os.path.join(upload_folder, filename)
            file.save(file_path)
            new_guide.file_path = file_path
        db.add(new_guide)
        db.commit()
    return jsonify({"success": True, "message": "Новый регламент добавлен.", "category": "success", "action": "reload"})


@app.route('/guide/delete/<int:guide_id>', methods=['POST'])
def delete_guide(guide_id):
    with get_session() as db:
        guide = db.get(RoleGuide, guide_id)
        if guide:
            if guide.file_path and os.path.exists(guide.file_path): os.remove(guide.file_path)
            db.delete(guide)
            db.commit()
            return jsonify({"success": True, "message": "Регламент удален.", "category": "warning", "action": "reload"})
    return jsonify({"success": False, "message": "Регламент не найден.", "category": "danger"}), 404


@app.route('/config/update', methods=['POST'])
def update_config():
    env_file = '.env'
    raw_id = (request.form.get("COMMON_CHAT_ID_SELECT", "") or request.form.get("COMMON_CHAT_ID", "")).strip()

    async def _resolve_chat_id_async(raw: str) -> str | None:
        token = os.getenv("BOT_TOKEN")
        if not token or not raw: return None
        bot = Bot(token=token)
        try:
            for cid in _chat_candidates(raw):
                try:
                    return str((await bot.get_chat(cid)).id)
                except Exception:
                    continue
            return None
        finally:
            await bot.session.close()

    try:
        norm = asyncio.run(_resolve_chat_id_async(raw_id))
    except RuntimeError:
        norm = None

    common_chat_id = norm or raw_id
    set_key(env_file, "COMMON_CHAT_ID", common_chat_id)
    os.environ["COMMON_CHAT_ID"] = common_chat_id

    set_key(env_file, "OFFICE_LAT", request.form.get("OFFICE_LAT", ""));
    os.environ["OFFICE_LAT"] = request.form.get("OFFICE_LAT", "")
    set_key(env_file, "OFFICE_LON", request.form.get("OFFICE_LON", ""));
    os.environ["OFFICE_LON"] = request.form.get("OFFICE_LON", "")
    set_key(env_file, "OFFICE_RADIUS_METERS", request.form.get("OFFICE_RADIUS_METERS", ""));
    os.environ["OFFICE_RADIUS_METERS"] = request.form.get("OFFICE_RADIUS_METERS", "")

    if request.form.get("BOT_TOKEN"):
        set_key(env_file, "BOT_TOKEN", request.form.get("BOT_TOKEN"));
        os.environ["BOT_TOKEN"] = request.form.get("BOT_TOKEN")

    return jsonify({"success": True, "message": "Настройки сохранены. Перезапустите бота, чтобы они применились.",
                    "category": "info"})


@app.route('/export/employees.xlsx', methods=['GET'])
def export_employees_xlsx():
    rows = []
    with get_session() as db:
        employees = db.query(Employee).order_by(Employee.name).all()
        emails = [e.email for e in employees]
        existing_free = {rc.email: rc.code for rc in
                         db.query(RegCode).filter(RegCode.used == False, RegCode.email.in_(emails)).all()}
        for emp in employees:
            code_for_row = ""
            if not emp.telegram_id:
                code_for_row = existing_free.get(emp.email, "")
                if not code_for_row:
                    while True:
                        code = "".join(str(random.randint(0, 9)) for _ in range(8))
                        if not db.query(RegCode).filter_by(code=code).first(): break
                    db.add(RegCode(code=code, email=emp.email, used=False))
                    code_for_row = code
            rows.append([
                emp.id, emp.name or "", emp.email, emp.role or "",
                emp.birthday.strftime("%d.%m.%Y") if emp.birthday else "",
                "Да" if emp.is_active else "Нет", "Да" if emp.registered else "Нет",
                "Да" if emp.training_passed else "Нет",
                        emp.telegram_id or "", code_for_row
            ])
        db.commit()

    wb = Workbook()
    ws = wb.active;
    ws.title = "Сотрудники"
    headers = ["ID", "ФИО", "Email", "Роль", "Дата рождения", "Активен", "Зарегистрирован", "Прошёл тренинг",
               "Telegram ID", "Код для привязки"]
    ws.append(headers)
    # Styles...
    header_fill = PatternFill("solid", fgColor="4F46E5");
    header_font = Font(color="FFFFFF", bold=True)
    thin_border = Border(left=Side(style="thin", color="D1D5DB"), right=Side(style="thin", color="D1D5DB"),
                         top=Side(style="thin", color="D1D5DB"), bottom=Side(style="thin", color="D1D5DB"))
    for cell in ws[1]: cell.fill = header_fill; cell.font = header_font; cell.alignment = Alignment(horizontal="center",
                                                                                                    vertical="center"); cell.border = thin_border
    for r in rows: ws.append(r)
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        for cell in row: cell.border = thin_border
    widths = [6, 28, 28, 18, 14, 10, 16, 16, 14, 18]
    for i, w in enumerate(widths, 1): ws.column_dimensions[get_column_letter(i)].width = w
    ws.auto_filter.ref = ws.dimensions
    ws.freeze_panes = "A2"
    ws.conditional_formatting.add(f"A2:{get_column_letter(len(headers))}{ws.max_row}",
                                  FormulaRule(formula=[f'LEN($I2)=0'], stopIfTrue=False,
                                              fill=PatternFill("solid", fgColor="FEE2E2")))
    ws.conditional_formatting.add(f"J2:J{ws.max_row}", FormulaRule(formula=[f'LEN($J2)>0'], stopIfTrue=False,
                                                                   fill=PatternFill("solid", fgColor="ECFDF5")))

    mem = io.BytesIO()
    wb.save(mem);
    mem.seek(0)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    return send_file(mem, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True, download_name=f"employees_{ts}.xlsx")


@app.route('/landing')
def landing():
    return render_template("landing.html")


# --- API & DEBUG ROUTES ---
@app.post("/api/bot/chats/recheck")
def api_bot_chats_recheck():
    if not session.get("is_admin"):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    token = os.getenv("BOT_TOKEN")
    if not token:
        return jsonify({"ok": False, "error": "BOT_TOKEN is empty"}), 400

    async def _recheck():
        bot = Bot(token=token, default=DefaultBotProperties(parse_mode="HTML"))
        try:
            me = await bot.get_me()
            updated, errors = 0, []

            with get_session() as s:
                rows = s.execute(select(GroupChat)).scalars().all()
                for r in rows:
                    try:
                        sid = int(str(r.chat_id).strip())
                        try:
                            m = await bot.get_chat_member(sid, me.id)
                        except TelegramBadRequest as e1:
                            if "chat not found" in str(e1).lower() and r.username:
                                try:
                                    ch2 = await bot.get_chat("@" + r.username.lstrip("@"))
                                    sid = ch2.id
                                    r.chat_id = sid
                                    m = await bot.get_chat_member(sid, me.id)
                                except Exception as e2:
                                    errors.append({"chat_id": r.chat_id, "error": f"fallback failed: {e2}"})
                                    continue
                            else:
                                errors.append({"chat_id": r.chat_id, "error": str(e1)})
                                continue

                        status_value = getattr(getattr(m, "status", None), "value", getattr(m, "status", None))
                        is_admin = status_value in ("creator", "administrator")

                        ch = await bot.get_chat(sid)
                        r.is_admin = bool(is_admin)
                        r.title = getattr(ch, "title", None) or getattr(ch, "full_name", None) or str(sid)
                        r.username = getattr(ch, "username", "")
                        r.type = getattr(getattr(ch, "type", None), "value", getattr(ch, "type", None)) or ""
                        r.updated_at = datetime.utcnow()
                        s.add(r)
                        updated += 1

                    except (TelegramBadRequest, TelegramForbiddenError) as e:
                        errors.append({"chat_id": r.chat_id, "error": str(e)})

                s.commit()
            return {"updated": updated, "errors": errors, "bot": {"id": me.id, "username": me.username}}
        finally:
            await bot.session.close()

    result = asyncio.run(_recheck())
    return jsonify({"ok": True, **result})


@app.route("/api/bot/chats", methods=["GET"])
def api_bot_chats_list():
    if not session.get("is_admin"):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    with get_session() as db:
        rows = db.query(GroupChat).order_by(GroupChat.updated_at.desc().nullslast()).all()
        data = [{
            "chat_id": r.chat_id, "title": r.title or "", "username": r.username or "",
            "type": r.type or "", "is_admin": bool(r.is_admin),
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        } for r in rows]
        return jsonify({"ok": True, "data": data})


@app.route('/debug/me')
@login_required
def debug_me():
    async def _me():
        bot = Bot(token=os.getenv("BOT_TOKEN"))
        try:
            me = await bot.get_me()
            return {"id": me.id, "username": me.username, "name": me.first_name}
        finally:
            await bot.session.close()

    try:
        info = asyncio.run(_me())
    except RuntimeError:
        info = {"note": "event loop busy; re-run or check logs"}
    return jsonify(info)


@app.route('/debug/diag_chat')
@login_required
def debug_diag_chat():
    raw = str(request.args.get('chat_id') or os.getenv('COMMON_CHAT_ID') or '').strip()
    if not raw:
        return jsonify(ok=False, error="no chat_id provided and COMMON_CHAT_ID empty")

    async def _diag(raw_id: str):
        bot = Bot(token=os.getenv("BOT_TOKEN"))
        try:
            me = await bot.get_me()
            out = {"me": {"id": me.id, "username": me.username}, "candidates": _chat_candidates(raw_id), "results": []}
            for cid in out["candidates"]:
                item = {"cid": cid}
                try:
                    chat = await bot.get_chat(cid)
                    item.update({"resolved_id": chat.id, "title": getattr(chat, "title", None),
                                 "type": getattr(chat, "type", None)})
                    try:
                        mem = await bot.get_chat_member(chat.id, me.id)
                        item["bot_status"] = str(mem.status)
                    except Exception as e:
                        item["bot_status_error"] = str(e)
                except Exception as e:
                    item["error"] = str(e)
                out["results"].append(item)
            return out
        finally:
            await bot.session.close()

    try:
        res = asyncio.run(_diag(raw))
    except RuntimeError:
        return jsonify(ok=False, error="event loop busy, retry once"), 503
    return jsonify(ok=True, **res)


@app.route('/settings/copy', methods=['POST'])
def copy_settings():
    source_role = request.form.get('source_role')
    target_roles = request.form.getlist('target_roles')
    sections_to_copy = request.form.getlist('sections_to_copy')

    if not all([source_role, target_roles, sections_to_copy]):
        return jsonify({"success": False, "message": "Недостаточно данных для копирования.", "category": "danger"}), 400

    with get_session() as db:
        for target_role in target_roles:
            if 'scenarios' in sections_to_copy:
                # --- Копирование Сценариев Онбординга ---
                # 1. Удаляем старые вопросы и шаги у целевой роли
                db.query(OnboardingQuestion).filter_by(role=target_role).delete()
                db.query(OnboardingStep).filter_by(role=target_role).delete()

                # 2. Копируем вопросы
                source_questions = db.query(OnboardingQuestion).filter_by(role=source_role).order_by(
                    OnboardingQuestion.order_index).all()
                for q in source_questions:
                    new_q = OnboardingQuestion(
                        role=target_role,
                        question_text=q.question_text,
                        data_key=q.data_key,
                        is_required=q.is_required,
                        order_index=q.order_index
                    )
                    db.add(new_q)

                # 3. Копируем шаги (включая файлы)
                source_steps = db.query(OnboardingStep).filter_by(role=source_role).order_by(
                    OnboardingStep.order_index).all()
                for step in source_steps:
                    new_step = OnboardingStep(
                        role=target_role,
                        message_text=step.message_text,
                        file_type=step.file_type,
                        order_index=step.order_index
                    )
                    # Копируем файл, если он есть
                    if step.file_path and os.path.exists(step.file_path):
                        filename = os.path.basename(step.file_path)
                        new_filename = f"{target_role.lower().replace('/', '_')}_step_{filename.split('_step_')[-1]}"
                        upload_folder = os.path.join(app.config['UPLOAD_FOLDER_ONBOARDING'], 'steps')
                        new_filepath = os.path.join(upload_folder, new_filename)
                        shutil.copy(step.file_path, new_filepath)
                        new_step.file_path = new_filepath
                    db.add(new_step)

            if 'training' in sections_to_copy:
                # --- Копирование Тренинга и Квизов ---
                # 1. Удаляем старые квизы и информацию о тренинге
                db.query(QuizQuestion).filter_by(role=target_role).delete()
                db.query(RoleOnboarding).filter_by(role=target_role).delete()

                # 2. Копируем квизы
                source_quizzes = db.query(QuizQuestion).filter_by(role=source_role).order_by(
                    QuizQuestion.order_index).all()
                for quiz in source_quizzes:
                    new_quiz = QuizQuestion(
                        role=target_role,
                        question=quiz.question,
                        answer=quiz.answer,
                        question_type=quiz.question_type,
                        options=quiz.options,
                        order_index=quiz.order_index
                    )
                    db.add(new_quiz)

                # 3. Копируем информацию о тренинге (включая файл)
                source_training = db.query(RoleOnboarding).filter_by(role=source_role).first()
                if source_training:
                    new_training = RoleOnboarding(
                        role=target_role,
                        text=source_training.text,
                        file_type=source_training.file_type
                    )
                    if source_training.file_path and os.path.exists(source_training.file_path):
                        filename = os.path.basename(source_training.file_path)
                        new_filename = f"{target_role.lower().replace('/', '_')}_{filename.split('_', 1)[-1]}"
                        new_filepath = os.path.join(app.config['UPLOAD_FOLDER_ONBOARDING'], new_filename)
                        shutil.copy(source_training.file_path, new_filepath)
                        new_training.file_path = new_filepath
                    db.add(new_training)

        db.commit()

    return jsonify({
        "success": True,
        "message": f"Настройки из '{source_role}' скопированы в {len(target_roles)} ролей. Страница будет перезагружена.",
        "category": "success",
        "action": "reload"  # Говорим фронтенду перезагрузить страницу
    })

# --- ЗАПУСК ПРИЛОЖЕНИЯ ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

