import os
from datetime import datetime, date
from sqlalchemy import (
    create_engine, Column, Integer, String, Date, Boolean,
    ForeignKey, Time, Text, DateTime, BigInteger, UniqueConstraint, event, LargeBinary
)
from sqlalchemy.orm import declarative_base, synonym
from contextlib import contextmanager
from sqlalchemy.orm import sessionmaker, Session
from aiogram.fsm.state import StatesGroup, State

# --- БАЗОВАЯ НАСТРОЙКА ---

DATABASE_URL = os.getenv("DATABASE_URL")
Base = declarative_base()

# создаём engine
engine = create_engine(DATABASE_URL, pool_pre_ping=True)


# 👇 ключевой кусок: заставляем каждое подключение говорить Postgres'у "я в UTF-8"
@event.listens_for(engine, "connect")
def set_client_encoding(dbapi_connection, connection_record):
    cur = dbapi_connection.cursor()
    # это понимает psycopg2 / psycopg2-binary
    cur.execute("SET client_encoding TO 'UTF8';")
    cur.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def get_session():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- МОДЕЛИ ---

class Role(Base):
    __tablename__ = "roles"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)


class Employee(Base):
    __tablename__ = "employees"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, index=True, nullable=True)
    email = Column(String, unique=True, nullable=False)
    role = Column(String, nullable=False)
    name = Column(String, nullable=True)
    birthday = Column(Date, nullable=True)
    registered = Column(Boolean, default=False)
    greeted = Column(Boolean, default=False)
    training_passed = Column(Boolean, default=False)
    onboarding_completed = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    contact_info = Column(String(256), nullable=True)
    photo_file_id = Column(String, nullable=True)  # file_id телеграма, не требует LargeBinary


class Attendance(Base):
    __tablename__ = "attendance"
    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    arrival_time = Column(Time, nullable=True)
    departure_time = Column(Time, nullable=True)
    __table_args__ = (UniqueConstraint("employee_id", "date", name="uix_attendance_emp_date"),)


class RegCode(Base):
    __tablename__ = "reg_codes"
    code = Column(String(8), primary_key=True)
    email = Column(String, ForeignKey("employees.email"), nullable=False)
    used = Column(Boolean, default=False)


class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    event_date = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Idea(Base):
    __tablename__ = "ideas"
    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    text = Column(Text, nullable=False)
    submission_date = Column(DateTime, default=datetime.utcnow)


class Topic(Base):
    __tablename__ = "topics"
    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)

    # --- Новые поля для хранения в БД ---
    image_data = Column(LargeBinary, nullable=True)  # сам файл (BYTEA)
    image_mime = Column(String(100), nullable=True)  # например, "image/png"
    image_name = Column(String(255), nullable=True)  # оригинальное имя файла

    # --- Старое поле (удалено) ---
    # image_path = Column(String, nullable=True)


class RoleGuide(Base):
    __tablename__ = "role_guides"
    id = Column(Integer, primary_key=True)
    role = Column(String, index=True, nullable=False)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=True)
    order_index = Column(Integer, default=0)

    # --- Новые поля ---
    file_data = Column(LargeBinary, nullable=True)
    file_mime = Column(String(100), nullable=True)
    file_name = Column(String(255), nullable=True)

    # --- Старое поле (удалено) ---
    # file_path = Column(String, nullable=True)


class RoleOnboarding(Base):
    __tablename__ = "role_onboarding"
    id = Column(Integer, primary_key=True)
    role = Column(String, unique=True, nullable=False)
    text = Column(String, nullable=False)
    file_type = Column(String(50), default='document', nullable=False)

    # --- Новые поля ---
    file_data = Column(LargeBinary, nullable=True)
    file_mime = Column(String(100), nullable=True)
    file_name = Column(String(255), nullable=True)

    # --- Старое поле (удалено) ---
    # file_path = Column(String, nullable=True)


class QuizQuestion(Base):
    __tablename__ = "quiz_questions"
    id = Column(Integer, primary_key=True)
    role = Column(String, index=True, nullable=False)
    question = Column(String, nullable=False)
    answer = Column(String, nullable=False)
    question_type = Column(String(20), nullable=False, default="text")
    options = Column(String, nullable=True)
    order_index = Column(Integer, nullable=False, default=0)


class GroupChat(Base):
    __tablename__ = "group_chats"
    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, nullable=False, unique=True, index=True)
    title = Column(String, nullable=True)
    name = synonym('title')
    username = Column(String, nullable=True)
    type = Column(String, nullable=True)
    is_admin = Column(Boolean, default=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ArchivedEmployee(Base):
    __tablename__ = "archived_employees"
    id = Column(Integer, primary_key=True, autoincrement=True)
    original_employee_id = Column(Integer, nullable=False, index=True)
    telegram_id = Column(BigInteger)
    email = Column(String, nullable=False)
    role = Column(String, nullable=False)
    name = Column(String)
    birthday = Column(Date)
    registered = Column(Boolean, default=False)
    training_passed = Column(Boolean, default=False)
    dismissal_date = Column(DateTime, default=datetime.utcnow)


class ArchivedAttendance(Base):
    __tablename__ = "archived_attendance"
    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    arrival_time = Column(Time, nullable=True)
    departure_time = Column(Time, nullable=True)


class BotText(Base):
    __tablename__ = "bot_texts"
    id = Column(String(50), primary_key=True)
    text = Column(Text, nullable=False, default="Текст не задан")
    description = Column(String, nullable=True)


class OnboardingQuestion(Base):
    __tablename__ = "onboarding_questions"
    id = Column(Integer, primary_key=True)
    role = Column(String, index=True, nullable=False)
    order_index = Column(Integer, default=0, nullable=False)
    question_text = Column(String, nullable=False)
    data_key = Column(String(50), nullable=False)
    is_required = Column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("role", "order_index", name="onboarding_questions_role_order_key"),)


class EmployeeCustomData(Base):
    __tablename__ = "employee_custom_data"
    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), index=True, nullable=False)
    data_key = Column(String(50), nullable=False)
    data_value = Column(Text, nullable=False)
    __table_args__ = (UniqueConstraint("employee_id", "data_key", name="u_employee_data_key"),)


class OnboardingStep(Base):
    __tablename__ = "onboarding_steps"
    id = Column(Integer, primary_key=True)
    role = Column(String, index=True, nullable=False)
    order_index = Column(Integer, default=0)
    message_text = Column(Text, nullable=True)
    file_type = Column(String(20), nullable=True)

    # --- Новые поля ---
    file_data = Column(LargeBinary, nullable=True)
    file_mime = Column(String(100), nullable=True)
    file_name = Column(String(255), nullable=True)

    # --- Старое поле (удалено) ---
    # file_path = Column(String, nullable=True)


class ArchivedIdea(Base):
    __tablename__ = "archived_ideas"
    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, nullable=False)
    idea_text = Column(Text, nullable=False)
    submission_date = Column(DateTime, default=datetime.utcnow)


class ConfigSetting(Base):
    __tablename__ = "config_settings"
    key = Column(String(50), primary_key=True)
    value = Column(Text, nullable=True)


class CircleVideo(Base):
    __tablename__ = "circle_videos"

    id = Column(Integer, primary_key=True)

    # --- Новые поля ---
    file_data = Column(LargeBinary, nullable=False)
    file_mime = Column(String(100), nullable=True)
    original_filename = Column(String(255), nullable=True)  # Используем это вместо file_name

    # --- Старые поля (удалены) ---
    # stored_filename = Column(String(255), nullable=False)

    uploaded_by = Column(String(120), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# FSM States
class Onboarding(StatesGroup):
    awaiting_answer = State()