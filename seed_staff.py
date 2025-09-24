#!/usr/bin/env python3
# seed_staff.py

import csv
import os
import sys
from datetime import datetime

# подключаемся к main.py
sys.path.append(os.path.dirname(__file__))
from bot import get_session, Employee  # убедитесь, что main.py экспортирует эти имена

def parse_date(s: str):
    s = s.strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%d.%m.%Y").date()
    except ValueError:
        return None

def seed():
    path = os.path.join(os.path.dirname(__file__), "staff.tsv")
    with open(path, encoding="utf-8") as f, get_session() as db:
        reader = csv.reader(f, delimiter="\t")
        for row in reader:
            # убираем лишние пробелы вокруг
            row = [cell.strip() for cell in row]
            # пропускаем пустые строки
            if not row or not row[0]:
                continue
            # добавляем пустые ячейки до длины 8
            if len(row) < 8:
                row += [''] * (8 - len(row))
            # распаковка
            full_name, position, contract, hire_date, contact, bday, branch, coop = row[:8]

            # формируем уникальный email-заглушку
            email = f"{full_name.replace(' ', '_').lower()}@example.com"
            role  = "Staff"

            # не дублируем
            if db.query(Employee).filter_by(email=email).first():
                continue

            emp = Employee(
                telegram_id        = None,
                email              = email,
                role               = role,
                name               = full_name,
                birthday           = parse_date(bday),
                registered         = False,
                greeted            = False,

                # HR‑поля
                full_name          = full_name,
                position           = position or None,
                contract_number    = contract or None,
                employment_date    = parse_date(hire_date),
                contact_info       = contact or None,
                branch             = branch or None,
                cooperation_format = coop or None,
            )
            db.add(emp)

        db.commit()
    print("✅ Таблица employees заполнена из staff.tsv")

if __name__ == "__main__":
    seed()
