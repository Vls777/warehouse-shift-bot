# ============================================================
# БОТ РАСПИСАНИЯ СМЕН — ЕДИНЫЙ ФАЙЛ
# ============================================================
import csv
import io
import json
import logging
import os
import random
import sqlite3
from contextlib import contextmanager
from datetime import datetime, date as date_cls, timedelta
from io import BytesIO

import requests
from vk_api import VkApi
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor

# ============================================================
# CONFIG
# ============================================================
VK_TOKEN = os.environ.get("VK_TOKEN", "")
VK_GROUP_ID = int(os.environ.get("VK_GROUP_ID", "0"))
VK_ADMIN_IDS = [int(x) for x in os.environ.get("VK_ADMIN_IDS", "").split(",") if x.strip()]
VK_MANAGER_IDS = [int(x) for x in os.environ.get("VK_MANAGER_IDS", "").split(",") if x.strip()]
TZ_NAME = os.environ.get("TZ_NAME", "Europe/Moscow")
AUTO_MORNING_HOUR = int(os.environ.get("AUTO_MORNING_HOUR", "7"))
AUTO_MORNING_MIN = int(os.environ.get("AUTO_MORNING_MIN", "30"))
AUTO_PLANNING_HOUR = int(os.environ.get("AUTO_PLANNING_HOUR", "20"))
AUTO_BACKUP_HOUR = int(os.environ.get("AUTO_BACKUP_HOUR", "23"))
DB_PATH = os.environ.get("DB_PATH", "warehouse.db")

if not VK_TOKEN or not VK_GROUP_ID:
    raise SystemExit("Задайте VK_TOKEN и VK_GROUP_ID")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

vk_session = VkApi(token=VK_TOKEN)
vk = vk_session.get_api()
longpoll = VkBotLongPoll(vk_session, VK_GROUP_ID)

user_states: dict = {}


# ============================================================
# STORAGE
# ============================================================
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS workers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            active INTEGER DEFAULT 1,
            last_second_shift TEXT,
            vk_id INTEGER
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS absences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            worker_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            reason TEXT DEFAULT '',
            UNIQUE(worker_id, date)
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            worker_id INTEGER NOT NULL,
            section TEXT NOT NULL,
            shift TEXT NOT NULL,
            UNIQUE(date, worker_id)
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS week_assignments (
            week_start TEXT PRIMARY KEY,
            worker_id INTEGER NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS working_saturdays (
            date TEXT PRIMARY KEY
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS holidays (
            date TEXT PRIMARY KEY,
            title TEXT DEFAULT ''
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS broadcast_targets (
            peer_id INTEGER PRIMARY KEY
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS worker_pins (
            worker_id INTEGER PRIMARY KEY,
            section TEXT NOT NULL
        )""")
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(workers)")]
        if "vk_id" not in cols:
            conn.execute("ALTER TABLE workers ADD COLUMN vk_id INTEGER")


def add_worker(name):
    with get_db() as conn:
        try:
            conn.execute("INSERT INTO workers (name) VALUES (?)", (name,))
            return True
        except sqlite3.IntegrityError:
            return False


def remove_worker(name):
    with get_db() as conn:
        cur = conn.execute("DELETE FROM workers WHERE name = ?", (name,))
        return cur.rowcount > 0


def list_workers():
    with get_db() as conn:
        return conn.execute("SELECT * FROM workers WHERE active = 1 ORDER BY name").fetchall()


def get_worker_by_name(name):
    with get_db() as conn:
        return conn.execute("SELECT * FROM workers WHERE name=?", (name,)).fetchone()


def get_worker_by_vk(vk_id):
    with get_db() as conn:
        return conn.execute("SELECT * FROM workers WHERE vk_id=?", (vk_id,)).fetchone()


def bind_vk(name, vk_id):
    with get_db() as conn:
        cur = conn.execute("UPDATE workers SET vk_id=? WHERE name=?", (vk_id, name))
        return cur.rowcount > 0


def unbind_vk(name):
    with get_db() as conn:
        cur = conn.execute("UPDATE workers SET vk_id=NULL WHERE name=?", (name,))
        return cur.rowcount > 0


def add_absence(name, date_str, reason=""):
    w = get_worker_by_name(name)
    if not w:
        return False
    with get_db() as conn:
        try:
            conn.execute("INSERT INTO absences (worker_id, date, reason) VALUES (?,?,?)",
                         (w["id"], date_str, reason))
        except sqlite3.IntegrityError:
            conn.execute("UPDATE absences SET reason=? WHERE worker_id=? AND date=?",
                         (reason, w["id"], date_str))
        return True


def remove_absence(name, date_str):
    w = get_worker_by_name(name)
    if not w:
        return False
    with get_db() as conn:
        cur = conn.execute("DELETE FROM absences WHERE worker_id=? AND date=?",
                           (w["id"], date_str))
        return cur.rowcount > 0


def get_absent_ids(date_str):
    with get_db() as conn:
        rows = conn.execute("SELECT worker_id FROM absences WHERE date=?", (date_str,)).fetchall()
        return {r["worker_id"] for r in rows}


def get_schedule(date_str):
    with get_db() as conn:
        return conn.execute(
            """SELECT s.section, s.shift, w.name FROM schedule s
               JOIN workers w ON w.id = s.worker_id
               WHERE s.date=? ORDER BY s.shift, s.section, w.name""", (date_str,)).fetchall()


def get_schedule_full(date_str):
    with get_db() as conn:
        return conn.execute(
            """SELECT s.worker_id as id, w.name, s.section, s.shift
               FROM schedule s JOIN workers w ON w.id = s.worker_id
               WHERE s.date=? ORDER BY s.shift, s.section, w.name""", (date_str,)).fetchall()


def save_schedule(date_str, assignments):
    with get_db() as conn:
        conn.execute("DELETE FROM schedule WHERE date=?", (date_str,))
        for a in assignments:
            conn.execute("INSERT INTO schedule (date, worker_id, section, shift) VALUES (?,?,?,?)",
                         (date_str, a["worker_id"], a["section"], a["shift"]))


def clear_schedule(date_str):
    with get_db() as conn:
        cur = conn.execute("DELETE FROM schedule WHERE date=?", (date_str,))
        return cur.rowcount > 0


def update_last_second_shift(worker_id, week_start):
    with get_db() as conn:
        conn.execute("UPDATE workers SET last_second_shift=? WHERE id=?", (week_start, worker_id))


def get_week_assignment(week_start):
    with get_db() as conn:
        row = conn.execute("SELECT worker_id FROM week_assignments WHERE week_start=?", (week_start,)).fetchone()
        return row["worker_id"] if row else None


def set_week_assignment(week_start, worker_id):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO week_assignments (week_start, worker_id) VALUES (?,?)",
                     (week_start, worker_id))


def get_week_assignment_worker(week_start):
    with get_db() as conn:
        return conn.execute(
            """SELECT w.* FROM week_assignments a
               JOIN workers w ON w.id = a.worker_id WHERE a.week_start=?""", (week_start,)).fetchone()


def clear_week_assignment(week_start):
    with get_db() as conn:
        cur = conn.execute("DELETE FROM week_assignments WHERE week_start=?", (week_start,))
        return cur.rowcount > 0


def set_saturday_working(date_str, working):
    with get_db() as conn:
        if working:
            conn.execute("INSERT OR IGNORE INTO working_saturdays (date) VALUES (?)", (date_str,))
        else:
            conn.execute("DELETE FROM working_saturdays WHERE date=?", (date_str,))


def is_saturday_working(date_str):
    with get_db() as conn:
        row = conn.execute("SELECT 1 FROM working_saturdays WHERE date=?", (date_str,)).fetchone()
        return row is not None


def list_working_saturdays():
    with get_db() as conn:
        return conn.execute("SELECT date FROM working_saturdays ORDER BY date").fetchall()


def add_holiday(date_str, title=""):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO holidays (date, title) VALUES (?,?)", (date_str, title))


def remove_holiday(date_str):
    with get_db() as conn:
        cur = conn.execute("DELETE FROM holidays WHERE date=?", (date_str,))
        return cur.rowcount > 0


def is_holiday(date_str):
    with get_db() as conn:
        row = conn.execute("SELECT 1 FROM holidays WHERE date=?", (date_str,)).fetchone()
        return row is not None


def add_broadcast_target(peer_id):
    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO broadcast_targets (peer_id) VALUES (?)", (peer_id,))


def remove_broadcast_target(peer_id):
    with get_db() as conn:
        cur = conn.execute("DELETE FROM broadcast_targets WHERE peer_id=?", (peer_id,))
        return cur.rowcount > 0


def list_broadcast_targets():
    with get_db() as conn:
        return [r["peer_id"] for r in conn.execute("SELECT peer_id FROM broadcast_targets ORDER BY peer_id").fetchall()]


def get_setting(key, default=""):
    with get_db() as conn:
        row = conn.execute("SELECT value FROM bot_settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?,?)", (key, value))


def history_worker(name, days=30):
    since = (date_cls.today() - timedelta(days=days)).isoformat()
    w = get_worker_by_name(name)
    if not w:
        return None, None
    with get_db() as conn:
        shifts = conn.execute(
            """SELECT date, section, shift FROM schedule
               WHERE worker_id=? AND date>=? ORDER BY date DESC""", (w["id"], since)).fetchall()
        absences = conn.execute(
            """SELECT date, reason FROM absences
               WHERE worker_id=? AND date>=? ORDER BY date DESC""", (w["id"], since)).fetchall()
    return shifts, absences


def journal_for_date(date_str):
    with get_db() as conn:
        shifts = conn.execute(
            """SELECT w.name, s.section, s.shift FROM schedule s
               JOIN workers w ON w.id = s.worker_id
               WHERE s.date=? ORDER BY s.shift, s.section""", (date_str,)).fetchall()
        absences = conn.execute(
            """SELECT w.name, a.reason FROM absences a
               JOIN workers w ON w.id = a.worker_id
               WHERE a.date=? ORDER BY w.name""", (date_str,)).fetchall()
    return shifts, absences


def period_report(start, end):
    with get_db() as conn:
        spw = conn.execute(
            """SELECT w.name, s.shift, COUNT(*) as cnt FROM schedule s
               JOIN workers w ON w.id = s.worker_id
               WHERE s.date BETWEEN ? AND ?
               GROUP BY w.name, s.shift ORDER BY w.name""", (start, end)).fetchall()
        absences = conn.execute(
            """SELECT w.name, a.date, a.reason FROM absences a
               JOIN workers w ON w.id = a.worker_id
               WHERE a.date BETWEEN ? AND ?
               ORDER BY a.date, w.name""", (start, end)).fetchall()
        sbs = conn.execute(
            """SELECT section, COUNT(*) as cnt FROM schedule
               WHERE date BETWEEN ? AND ? GROUP BY section""", (start, end)).fetchall()
        night = conn.execute(
            """SELECT w.name, COUNT(*) as cnt FROM schedule s
               JOIN workers w ON w.id = s.worker_id
               WHERE s.date BETWEEN ? AND ? AND s.shift='second'
               GROUP BY w.name ORDER BY cnt DESC""", (start, end)).fetchall()
        missed = conn.execute(
            """SELECT w.name, COUNT(*) as cnt FROM absences a
               JOIN workers w ON w.id = a.worker_id
               WHERE a.date BETWEEN ? AND ?
               GROUP BY w.name ORDER BY cnt DESC""", (start, end)).fetchall()
    return spw, absences, sbs, night, missed


def sicklist_since(start):
    with get_db() as conn:
        return conn.execute(
            """SELECT w.name, a.date, a.reason FROM absences a
               JOIN workers w ON w.id = a.worker_id
               WHERE a.date >= ? ORDER BY a.date DESC""", (start,)).fetchall()


def export_rows(start, end):
    with get_db() as conn:
        return conn.execute(
            """SELECT s.date, w.name, s.section, s.shift,
                      COALESCE(a.reason, '') as absent_reason
               FROM schedule s
               JOIN workers w ON w.id = s.worker_id
               LEFT JOIN absences a ON a.worker_id = w.id AND a.date = s.date
               WHERE s.date BETWEEN ? AND ?
               ORDER BY s.date, s.shift, w.name""", (start, end)).fetchall()


def move_worker_in_schedule(date_str, name, new_section):
    w = get_worker_by_name(name)
    if not w:
        return False
    with get_db() as conn:
        cur = conn.execute("UPDATE schedule SET section=? WHERE date=? AND worker_id=?",
                           (new_section, date_str, w["id"]))
        return cur.rowcount > 0


def remove_worker_from_schedule(date_str, name):
    w = get_worker_by_name(name)
    if not w:
        return False
    with get_db() as conn:
        cur = conn.execute("DELETE FROM schedule WHERE date=? AND worker_id=?", (date_str, w["id"]))
        return cur.rowcount > 0


def add_worker_to_schedule(date_str, name, section, shift):
    w = get_worker_by_name(name)
    if not w:
        return False
    with get_db() as conn:
        try:
            conn.execute("INSERT INTO schedule (date, worker_id, section, shift) VALUES (?,?,?,?)",
                         (date_str, w["id"], section, shift))
        except sqlite3.IntegrityError:
            conn.execute("UPDATE schedule SET section=?, shift=? WHERE date=? AND worker_id=?",
                         (section, shift, date_str, w["id"]))
        return True


def swap_workers_in_schedule(date_str, name_a, name_b):
    a = get_worker_by_name(name_a)
    b = get_worker_by_name(name_b)
    if not a or not b:
        return False, "Один из работников не найден"
    with get_db() as conn:
        ra = conn.execute("SELECT section, shift FROM schedule WHERE date=? AND worker_id=?",
                          (date_str, a["id"])).fetchone()
        rb = conn.execute("SELECT section, shift FROM schedule WHERE date=? AND worker_id=?",
                          (date_str, b["id"])).fetchone()
        if not ra or not rb:
            return False, "Кто-то не в расписании на этот день"
        conn.execute("UPDATE schedule SET section=?, shift=? WHERE date=? AND worker_id=?",
                     (rb["section"], rb["shift"], date_str, a["id"]))
        conn.execute("UPDATE schedule SET section=?, shift=? WHERE date=? AND worker_id=?",
                     (ra["section"], ra["shift"], date_str, b["id"]))
    return True, "OK"


def set_day_second_shift(date_str, name):
    w = get_worker_by_name(name)
    if not w:
        return False
    with get_db() as conn:
        conn.execute("DELETE FROM schedule WHERE date=? AND shift='second'", (date_str,))
        try:
            conn.execute("INSERT INTO schedule (date, worker_id, section, shift) VALUES (?,?,?,?)",
                         (date_str, w["id"], "—", "second"))
        except sqlite3.IntegrityError:
            conn.execute("UPDATE schedule SET section='—', shift='second' WHERE date=? AND worker_id=?",
                         (date_str, w["id"]))
    return True


def get_assembly_size(date_str, default=2):
    key = f"asm_{date_str}"
    val = get_setting(key, str(default))
    try:
        n = int(val)
        return n if n in (1, 2, 3, 4) else default
    except ValueError:
        return default


def set_assembly_size(date_str, n):
    set_setting(f"asm_{date_str}", str(n))


def set_pin(worker_id, section):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO worker_pins (worker_id, section) VALUES (?,?)",
                     (worker_id, section))


def remove_pin(worker_id):
    with get_db() as conn:
        cur = conn.execute("DELETE FROM worker_pins WHERE worker_id=?", (worker_id,))
        return cur.rowcount > 0


def list_pins():
    with get_db() as conn:
        return conn.execute(
            """SELECT w.id, w.name, p.section FROM worker_pins p
               JOIN workers w ON w.id = p.worker_id
               ORDER BY p.section, w.name""").fetchall()


def get_all_pins():
    with get_db() as conn:
        rows = conn.execute("SELECT worker_id, section FROM worker_pins").fetchall()
        return {r["worker_id"]: r["section"] for r in rows}


# ============================================================
# SCHEDULER
# ============================================================
SECTIONS = ["приёмка", "сборка", "склад №4", "погрузка"]
ASSEMBLY_SIZE = 2
RECEIVING_SIZE = 1
SUNDAY = 6
SATURDAY = 5


def week_start(d):
    return d - timedelta(days=d.weekday())


def week_label(d):
    ws = week_start(d)
    we = ws + timedelta(days=6)
    return f"{ws.strftime('%d.%m')}–{we.strftime('%d.%m.%Y')}"


def is_working_day(d):
    if d.weekday() == SUNDAY:
        return False
    if is_holiday(d.isoformat()):
        return False
    if d.weekday() == SATURDAY:
        return is_saturday_working(d.isoformat())
    return True


def working_days_of_week(d):
    ws = week_start(d)
    return [ws + timedelta(days=i) for i in range(6) if is_working_day(ws + timedelta(days=i))]


def get_second_shift_worker(date_str):
    d = date_cls.fromisoformat(date_str)
    ws = week_start(d).isoformat()
    workers = list_workers()
    if not workers:
        return None, False
    worker_ids = {w["id"] for w in workers}
    assigned = get_week_assignment(ws)
    if assigned is None or assigned not in worker_ids:
        workers_sorted = sorted(workers, key=lambda w: (w["last_second_shift"] or "1970-01-01", w["name"]))
        chosen = workers_sorted[0]
        set_week_assignment(ws, chosen["id"])
        update_last_second_shift(chosen["id"], ws)
        assigned = chosen["id"]
    absent = get_absent_ids(date_str)
    if assigned in absent:
        return None, False
    return assigned, False


def generate_for_date(date_str):
    d = date_cls.fromisoformat(date_str)
    if not is_working_day(d):
        return None, f"{d.strftime('%d.%m.%Y')} — выходной"
    workers = list_workers()
    if not workers:
        return None, "Нет работников"
    absent = get_absent_ids(date_str)
    available = [dict(w) for w in workers if w["id"] not in absent]
    if not available:
        return None, "Все отсутствуют в этот день"
    second_id, _ = get_second_shift_worker(date_str)
    day_workers = [w for w in available if w["id"] != second_id]

    pins = get_all_pins()
    pinned = {}
    free = []
    for w in day_workers:
        sec = pins.get(w["id"])
        if sec in ("приёмка", "сборка", "склад №4", "погрузка"):
            pinned.setdefault(sec, []).append(w)
        else:
            free.append(w)

    free.sort(key=lambda w: w["name"])
    if free:
        shift = d.toordinal() % len(free)
        free = free[shift:] + free[:shift]

    receiving = list(pinned.get("приёмка", []))
    if len(receiving) < RECEIVING_SIZE:
        need = RECEIVING_SIZE - len(receiving)
        receiving += free[:need]
        free = free[need:]

    asm_want = get_assembly_size(date_str, ASSEMBLY_SIZE)
    assembly = list(pinned.get("сборка", []))
    if len(assembly) < asm_want:
        need = asm_want - len(assembly)
        assembly += free[:need]
        free = free[need:]

    warehouse4 = list(pinned.get("склад №4", []))
    loading = list(pinned.get("погрузка", [])) + free

    assignments = []
    if second_id is not None:
        assignments.append({"worker_id": second_id, "section": "—", "shift": "second"})
    for w in receiving:
        assignments.append({"worker_id": w["id"], "section": "приёмка", "shift": "day"})
    for w in assembly:
        assignments.append({"worker_id": w["id"], "section": "сборка", "shift": "day"})
    for w in warehouse4:
        assignments.append({"worker_id": w["id"], "section": "склад №4", "shift": "day"})
    for w in loading:
        assignments.append({"worker_id": w["id"], "section": "погрузка", "shift": "day"})

    save_schedule(date_str, assignments)
    return assignments, None


# ============================================================
# TEMPLATES
# ============================================================
def fmt_schedule(rows, date_obj, day_type="", week_worker=None):
    header = f"📅 Расписание на {date_obj.strftime('%d.%m.%Y')}"
    if day_type == "sunday":
        return f"{header}\n\n🚫 Воскресенье — выходной"
    if day_type == "holiday":
        return f"{header}\n\n🚫 Праздник — выходной"
    if day_type == "saturday_off":
        return f"{header}\n\n🚫 Суббота — выходной"
    if day_type == "saturday_on":
        header += "  🟢 (рабочая суббота)"

    if not rows:
        return f"{header}\n\nРасписание не сформировано"

    day = [r for r in rows if r["shift"] == "day"]
    night = [r for r in rows if r["shift"] == "second"]

    lines = [header, ""]
    lines.append("☀️ Дневная смена (08:30–19:00)")

    by_sec = {}
    for r in day:
        by_sec.setdefault(r["section"], []).append(r["name"])

    pins = get_all_pins()
    for sec in ["приёмка", "сборка", "склад №4", "погрузка"]:
        names = by_sec.get(sec, [])
        if names:
            marked = []
            for n in names:
                w = get_worker_by_name(n)
                if w and pins.get(w["id"]) == sec:
                    marked.append(f"{n}📌")
                else:
                    marked.append(n)
            lines.append(f"  • {sec.capitalize()}: {', '.join(marked)}")
    if not day:
        lines.append("  — никого")

    lines.append("")
    lines.append("🌙 Вторая смена (14:00–23:00)")
    if night:
        for r in night:
            lines.append(f"  • {r['name']}")
    else:
        if week_worker:
            lines.append(f"  🚫 {week_worker['name']} отсутствует — вторая смена не работает")
        else:
            lines.append("  — никого")

    return "\n".join(lines)


def day_type_for(d):
    if d.weekday() == 6:
        return "sunday"
    if is_holiday(d.isoformat()):
        return "holiday"
    if d.weekday() == 5:
        return "saturday_on" if is_saturday_working(d.isoformat()) else "saturday_off"
    return ""


def compare_schedules(old_rows, new_rows):
    old_map = {r["name"]: (r["shift"], r["section"]) for r in old_rows}
    new_map = {r["name"]: (r["shift"], r["section"]) for r in new_rows}
    SHIFT_RU = {"day": "☀️ день", "second": "🌙 2-я смена"}
    lines = []
    for name in sorted(set(old_map) | set(new_map)):
        o = old_map.get(name)
        n = new_map.get(name)
        if o == n:
            continue
        if o is None:
            lines.append(f"  ➕ {name}: {SHIFT_RU.get(n[0], n[0])} / {n[1]}")
        elif n is None:
            lines.append(f"  ➖ {name}: убран(а)")
        else:
            lines.append(f"  🔄 {name}: {SHIFT_RU.get(o[0], o[0])}/{o[1]} → {SHIFT_RU.get(n[0], n[0])}/{n[1]}")
    return "\n".join(lines) if lines else "  (без изменений)"


HELP_TEXT = (
    "🏭 Бот расписания смен склада\n\n"
    "Рабочие дни: Пн–Сб (Сб — выходной по умолчанию).\n"
    "Участки: приёмка (1), сборка (2–3), склад №4 (только закреплённые), погрузка (остальные).\n"
    "Дневная смена: 08:30–19:00\n"
    "Вторая смена: 14:00–23:00, один человек на всю неделю.\n"
    "Если 2-й сменщик заболел — в этот день смена не работает.\n\n"
    "Кнопка 🏗️ Сборка — добавить/убрать человека на сборку на сегодня.\n"
    "Кнопка 📌 Закрепления — прикрепить человека к участку.\n"
)

WORKER_HELP = (
    "🏭 Привет! Это бот расписания смен.\n\n"
    "Ты можешь:\n"
    "• Посмотреть своё расписание на неделю\n"
    "• Отметить, что заболел\n"
    "• Отменить своё отсутствие\n"
)


# ============================================================
# KEYBOARDS
# ============================================================
def main_menu(role="admin"):
    kb = VkKeyboard(one_time=False)
    kb.add_button("📅 Сегодня", color=VkKeyboardColor.PRIMARY)
    kb.add_button("📅 Неделя", color=VkKeyboardColor.PRIMARY)
    kb.add_line()
    kb.add_button("🏗️ Сборка", color=VkKeyboardColor.PRIMARY)
    kb.add_button("🌙 2-я смена", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("👥 Работники", color=VkKeyboardColor.SECONDARY)
    if role in ("admin", "manager"):
        kb.add_button("🤒 Заболел", color=VkKeyboardColor.NEGATIVE)
        kb.add_line()
        kb.add_button("✅ Вернулся", color=VkKeyboardColor.POSITIVE)
    kb.add_button("📆 Субботы", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("📖 Журнал", color=VkKeyboardColor.SECONDARY)
    if role == "admin":
        kb.add_button("⚙️ Ещё", color=VkKeyboardColor.SECONDARY)
        kb.add_line()
        kb.add_button("❓ Помощь", color=VkKeyboardColor.PRIMARY)
    else:
        kb.add_button("❓ Помощь", color=VkKeyboardColor.PRIMARY)
    return kb.get_keyboard()


def workers_menu():
    kb = VkKeyboard(one_time=False)
    kb.add_button("➕ Добавить", color=VkKeyboardColor.POSITIVE)
    kb.add_button("🗑 Удалить", color=VkKeyboardColor.NEGATIVE)
    kb.add_line()
    kb.add_button("📋 Список", color=VkKeyboardColor.PRIMARY)
    kb.add_button("📊 История", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("🔗 Привязать VK", color=VkKeyboardColor.SECONDARY)
    kb.add_button("🔓 Отвязать VK", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("⬅️ Назад", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def saturdays_menu():
    kb = VkKeyboard(one_time=False)
    kb.add_button("➕ Суббота рабочая", color=VkKeyboardColor.POSITIVE)
    kb.add_button("➖ Суббота выходная", color=VkKeyboardColor.NEGATIVE)
    kb.add_line()
    kb.add_button("📋 Список суббот", color=VkKeyboardColor.PRIMARY)
    kb.add_line()
    kb.add_button("🎉 Праздник добавить", color=VkKeyboardColor.SECONDARY)
    kb.add_button("🎉 Праздник удалить", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("⬅️ Назад", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def journal_menu():
    kb = VkKeyboard(one_time=False)
    kb.add_button("📖 Журнал сегодня", color=VkKeyboardColor.PRIMARY)
    kb.add_button("📅 Журнал за дату", color=VkKeyboardColor.PRIMARY)
    kb.add_line()
    kb.add_button("📊 Аналитика", color=VkKeyboardColor.SECONDARY)
    kb.add_button("🤒 Отсутствия", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("📎 Экспорт CSV", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("⬅️ Назад", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def more_menu():
    kb = VkKeyboard(one_time=False)
    kb.add_button("📅 На дату", color=VkKeyboardColor.PRIMARY)
    kb.add_button("📆 Отметить отсутствие", color=VkKeyboardColor.NEGATIVE)
    kb.add_line()
    kb.add_button("✏️ Править расписание", color=VkKeyboardColor.PRIMARY)
    kb.add_button("🔧 Пересобрать", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("📌 Закрепления", color=VkKeyboardColor.PRIMARY)
    kb.add_button("🔔 Рассылки", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("🌙 Назначить 2-ю", color=VkKeyboardColor.SECONDARY)
    kb.add_button("♻️ Сбросить 2-ю", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("🗑 Удалить расписание", color=VkKeyboardColor.NEGATIVE)
    kb.add_button("💾 Бэкап сейчас", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("⬅️ Назад", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def assembly_menu():
    kb = VkKeyboard(one_time=False)
    kb.add_button("➕ Добавить на сборку", color=VkKeyboardColor.POSITIVE)
    kb.add_button("➖ Убрать со сборки", color=VkKeyboardColor.NEGATIVE)
    kb.add_line()
    kb.add_button("👁 Показать расписание", color=VkKeyboardColor.PRIMARY)
    kb.add_button("🔄 Пересобрать день", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("⬅️ Назад", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def pins_menu():
    kb = VkKeyboard(one_time=False)
    kb.add_button("📌 Закрепить", color=VkKeyboardColor.POSITIVE)
    kb.add_button("🔓 Открепить", color=VkKeyboardColor.NEGATIVE)
    kb.add_line()
    kb.add_button("📋 Список закреплений", color=VkKeyboardColor.PRIMARY)
    kb.add_line()
    kb.add_button("⬅️ Назад", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def pin_sections_menu():
    kb = VkKeyboard(one_time=False)
    kb.add_button("приёмка", color=VkKeyboardColor.PRIMARY)
    kb.add_button("сборка", color=VkKeyboardColor.PRIMARY)
    kb.add_line()
    kb.add_button("склад №4", color=VkKeyboardColor.PRIMARY)
    kb.add_button("погрузка", color=VkKeyboardColor.PRIMARY)
    kb.add_line()
    kb.add_button("❌ Отмена", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def edit_menu():
    kb = VkKeyboard(one_time=False)
    kb.add_button("🔀 Переставить", color=VkKeyboardColor.PRIMARY)
    kb.add_button("↔️ Поменять местами", color=VkKeyboardColor.PRIMARY)
    kb.add_line()
    kb.add_button("➖ Убрать из смены", color=VkKeyboardColor.NEGATIVE)
    kb.add_button("➕ Добавить в смену", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("🌙 Сменить 2-го сменщика", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("👁 Показать расписание", color=VkKeyboardColor.SECONDARY)
    kb.add_button("⬅️ Назад", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def sections_menu():
    kb = VkKeyboard(one_time=False)
    kb.add_button("приёмка", color=VkKeyboardColor.PRIMARY)
    kb.add_button("сборка", color=VkKeyboardColor.PRIMARY)
    kb.add_line()
    kb.add_button("склад №4", color=VkKeyboardColor.PRIMARY)
    kb.add_button("погрузка", color=VkKeyboardColor.PRIMARY)
    kb.add_line()
    kb.add_button("❌ Отмена", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def broadcast_menu():
    kb = VkKeyboard(one_time=False)
    kb.add_button("➕ Добавить чат", color=VkKeyboardColor.POSITIVE)
    kb.add_button("🗑 Удалить чат", color=VkKeyboardColor.NEGATIVE)
    kb.add_line()
    kb.add_button("📋 Список чатов", color=VkKeyboardColor.PRIMARY)
    kb.add_line()
    kb.add_button("⬅️ Назад", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def worker_menu():
    kb = VkKeyboard(one_time=False)
    kb.add_button("📅 Моё расписание", color=VkKeyboardColor.PRIMARY)
    kb.add_line()
    kb.add_button("🤒 Я заболел", color=VkKeyboardColor.NEGATIVE)
    kb.add_button("✅ Я вернулся", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("ℹ️ Профиль", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def cancel_menu():
    kb = VkKeyboard(one_time=False)
    kb.add_button("❌ Отмена", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def confirm_inline(action, extra=""):
    kb = VkKeyboard(inline=True)
    payload_yes = json.dumps({"cmd": "confirm", "action": action, "extra": extra})
    kb.add_callback_button(label="✅ Да", color=VkKeyboardColor.POSITIVE, payload=payload_yes)
    kb.add_callback_button(label="❌ Отмена", color=VkKeyboardColor.NEGATIVE, payload='{"cmd":"cancel_inline"}')
    return kb.get_keyboard()


def workers_inline(prefix, workers, cols=2):
    kb = VkKeyboard(inline=True)
    n = len(workers)
    for i, w in enumerate(workers):
        payload = json.dumps({"cmd": "pick_w", "p": prefix, "n": w["name"]})
        kb.add_callback_button(label=w["name"], color=VkKeyboardColor.SECONDARY, payload=payload)
        if (i + 1) % cols == 0 and i != n - 1:
            kb.add_line()
    return kb.get_keyboard()


# ============================================================
# VK HELPERS
# ============================================================
def send(peer_id, text, keyboard=None):
    try:
        kwargs = {"peer_id": peer_id, "message": text, "random_id": random.randint(1, 2**31 - 1)}
        if keyboard:
            kwargs["keyboard"] = keyboard
        vk.messages.send(**kwargs)
    except Exception as e:
        logging.exception(f"send error: {e}")


def send_long(peer_id, text, keyboard=None):
    limit = 3500
    chunks = [text[i:i + limit] for i in range(0, len(text), limit)] or [""]
    for i, chunk in enumerate(chunks):
        is_last = (i == len(chunks) - 1)
        send(peer_id, chunk, keyboard if is_last else None)


def send_photo(peer_id, png_bytes, caption="", keyboard=None):
    try:
        logging.info(f"send_photo: {len(png_bytes)} bytes")
        upload_url = vk.photos.getMessagesUploadServer(peer_id=peer_id)["upload_url"]
        files = {"photo": ("schedule.png", png_bytes, "image/png")}
        resp = requests.post(upload_url, files=files).json()
        if not resp.get("photo"):
            logging.warning(f"VK upload returned: {resp}")
            return
        saved = vk.photos.saveMessagesPhoto(photo=resp["photo"], server=resp["server"], hash=resp["hash"])
        p = saved[0]
        kwargs = {
            "peer_id": peer_id,
            "message": caption,
            "attachment": f"photo{p['owner_id']}_{p['id']}",
            "random_id": random.randint(1, 2**31 - 1),
        }
        if keyboard:
            kwargs["keyboard"] = keyboard
        vk.messages.send(**kwargs)
        logging.info("send_photo: OK")
    except Exception as e:
        logging.warning(f"send_photo error: {e}")


def send_doc(peer_id, data, filename):
    upload_url = vk.docs.getMessagesUploadServer(peer_id=peer_id, type="doc")["upload_url"]
    files = {"file": (filename, data, "application/octet-stream")}
    up = requests.post(upload_url, files=files).json()
    saved = vk.docs.save(file=up["file"], title=filename)
    doc = saved["doc"]
    vk.messages.send(peer_id=peer_id, message=f"📎 {filename}",
                     attachment=f"doc{doc['owner_id']}_{doc['id']}",
                     random_id=random.randint(1, 2**31 - 1))


def get_role(user_id):
    if user_id in VK_ADMIN_IDS:
        return "admin"
    if user_id in VK_MANAGER_IDS:
        return "manager"
    if get_worker_by_vk(user_id):
        return "worker"
    return "guest"


def parse_date(s):
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def reset_state(peer_id):
    user_states.pop(peer_id, None)


# ============================================================
# IMAGE GEN
# ============================================================
_FONT_CACHE = {}
_FONT_PATHS = {}


def _ensure_font_files():
    global _FONT_PATHS
    if _FONT_PATHS:
        return _FONT_PATHS

    sys_paths = {
        "regular": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ],
        "bold": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ],
    }

    found = {}
    for key, paths in sys_paths.items():
        for p in paths:
            if os.path.exists(p):
                found[key] = p
                logging.info(f"font found in system: {p}")
                break

    base_dir = os.path.dirname(os.path.abspath(DB_PATH)) or "."

    urls = {
        "regular": [
            "https://raw.githubusercontent.com/matplotlib/matplotlib/main/lib/matplotlib/mpl-data/fonts/ttf/DejaVuSans.ttf",
            "https://cdn.jsdelivr.net/gh/matplotlib/matplotlib@main/lib/matplotlib/mpl-data/fonts/ttf/DejaVuSans.ttf",
        ],
        "bold": [
            "https://raw.githubusercontent.com/matplotlib/matplotlib/main/lib/matplotlib/mpl-data/fonts/ttf/DejaVuSans-Bold.ttf",
            "https://cdn.jsdelivr.net/gh/matplotlib/matplotlib@main/lib/matplotlib/mpl-data/fonts/ttf/DejaVuSans-Bold.ttf",
        ],
    }

    for key, url_list in urls.items():
        if key in found:
            continue
        local = os.path.join(base_dir, f"font_{key}.ttf")
        if os.path.exists(local) and os.path.getsize(local) > 50000:
            found[key] = local
            logging.info(f"font cached: {local}")
            continue
        for url in url_list:
            try:
                r = requests.get(url, timeout=30)
                r.raise_for_status()
                if len(r.content) < 50000:
                    logging.warning(f"font too small from {url}: {len(r.content)} bytes")
                    continue
                with open(local, "wb") as f:
                    f.write(r.content)
                logging.info(f"font downloaded ({key}): {len(r.content)} bytes")
                found[key] = local
                break
            except Exception as e:
                logging.warning(f"font download failed ({key}): {url} -> {e}")
                continue

    _FONT_PATHS = found
    return found


def _has_font():
    paths = _ensure_font_files()
    return bool(paths.get("regular"))


def _font(size, bold=False):
    from PIL import ImageFont
    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    paths = _ensure_font_files()
    path = paths.get("bold" if bold else "regular") or paths.get("regular")

    if path and os.path.exists(path):
        try:
            f = ImageFont.truetype(path, size)
            _FONT_CACHE[key] = f
            return f
        except Exception as e:
            logging.warning(f"font load error: {e}")

    logging.warning("нет кириллического шрифта")
    return None


def render_day(date_obj, rows):
    from PIL import Image, ImageDraw
    W, H = 900, 1000
    accent = (60, 110, 200)
    night_bg = (40, 40, 70)
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 130], fill=accent)
    d.text((40, 25), "Расписание смен", font=_font(40, True), fill=(255, 255, 255))
    d.text((40, 80), date_obj.strftime("%d.%m.%Y (%a)"), font=_font(28), fill=(230, 230, 255))

    y = 170
    d.text((40, y), "ДНЕВНАЯ СМЕНА  08:30–19:00", font=_font(26, True), fill=(30, 30, 30))
    y += 50
    day_rows = [r for r in rows if r["shift"] == "day"]
    by_sec = {}
    for r in day_rows:
        by_sec.setdefault(r["section"], []).append(r["name"])
    for sec in ["приёмка", "сборка", "склад №4", "погрузка"]:
        names = by_sec.get(sec, [])
        d.text((40, y), sec.upper(), font=_font(22, True), fill=accent)
        y += 36
        if names:
            text = ", ".join(names)
            while len(text) > 55:
                d.text((60, y), text[:55], font=_font(22), fill=(30, 30, 30))
                text = text[55:]
                y += 32
            if text:
                d.text((60, y), text, font=_font(22), fill=(30, 30, 30))
                y += 32
        else:
            d.text((60, y), "—", font=_font(22), fill=(150, 150, 150))
            y += 32
        y += 10

    y += 20
    night_rows = [r for r in rows if r["shift"] == "second"]
    d.rectangle([30, y, W - 30, y + 160], fill=night_bg)
    d.text((50, y + 20), "ВТОРАЯ СМЕНА  14:00–23:00", font=_font(26, True), fill=(240, 240, 255))
    if night_rows:
        d.text((50, y + 70), night_rows[0]["name"], font=_font(32, True), fill=(240, 240, 255))
        d.text((50, y + 115), "смена 14:00–23:00", font=_font(22), fill=(200, 200, 230))
    else:
        d.text((50, y + 70), "Вторая смена не работает", font=_font(24), fill=(255, 180, 180))

    out = BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


# ============================================================
# TASKS
# ============================================================
def job_morning():
    try:
        today = date_cls.today()
        if not is_working_day(today):
            return
        _, err = generate_for_date(today.isoformat())
        if err:
            return
        rows = get_schedule(today.isoformat())
        dt = day_type_for(today)
        ws = week_start(today).isoformat()
        ww = get_week_assignment_worker(ws)
        text = fmt_schedule(rows, today, dt, ww)
        png = None
        if _has_font():
            try:
                png = render_day(today, rows)
            except Exception as e:
                logging.warning(f"morning image: {e}")
        for peer_id in list_broadcast_targets():
            send(peer_id, text)
            if png:
                send_photo(peer_id, png, f"Расписание на {today.strftime('%d.%m.%Y')}")
    except Exception:
        logging.exception("morning job")


def job_sunday_planning():
    try:
        today = date_cls.today()
        next_monday = week_start(today) + timedelta(days=7)
        for i in range(7):
            d = next_monday + timedelta(days=i)
            if is_working_day(d):
                generate_for_date(d.isoformat())
        parts = []
        for i in range(6):
            d = next_monday + timedelta(days=i)
            if not is_working_day(d):
                continue
            rows = get_schedule(d.isoformat())
            ws = week_start(d).isoformat()
            ww = get_week_assignment_worker(ws)
            parts.append(fmt_schedule(rows, d, day_type_for(d), ww))
        full = f"📅 Расписание на неделю {week_label(next_monday)}\n\n" + "\n\n".join(parts)
        for peer_id in list_broadcast_targets():
            send_long(peer_id, full)
    except Exception:
        logging.exception("sunday planning")


def job_backup():
    try:
        with open(DB_PATH, "rb") as f:
            data = f.read()
        filename = f"warehouse_backup_{date_cls.today().isoformat()}.db"
        for admin_id in VK_ADMIN_IDS:
            try:
                send_doc(admin_id, data, filename)
            except Exception:
                pass
    except Exception:
        logging.exception("backup job")


def start_scheduler():
    from apscheduler.schedulers.background import BackgroundScheduler
    sched = BackgroundScheduler(timezone=TZ_NAME)
    sched.add_job(job_morning, "cron", hour=AUTO_MORNING_HOUR, minute=AUTO_MORNING_MIN)
    sched.add_job(job_sunday_planning, "cron", day_of_week="sun", hour=AUTO_PLANNING_HOUR, minute=0)
    sched.add_job(job_backup, "cron", day_of_week="sun", hour=AUTO_BACKUP_HOUR, minute=0)
    sched.start()
    logging.info(f"Scheduler started TZ={TZ_NAME}")


# ============================================================
# ACTIONS
# ============================================================
def action_today(peer_id):
    d = date_cls.today()
    if not is_working_day(d):
        return send(peer_id, f"📅 {d.strftime('%d.%m.%Y')} — выходной", main_menu())
    rows = get_schedule(d.isoformat())
    if not rows:
        _, err = generate_for_date(d.isoformat())
        if err:
            return send(peer_id, f"⚠️ {err}", main_menu())
        rows = get_schedule(d.isoformat())
    ws = week_start(d).isoformat()
    ww = get_week_assignment_worker(ws)
    kb = main_menu()
    send_long(peer_id, fmt_schedule(rows, d, day_type_for(d), ww), kb)
    try:
        if not _has_font():
            logging.warning("Нет шрифта — картинка пропущена")
            return
        png = render_day(d, rows)
        send_photo(peer_id, png, "", keyboard=kb)
    except Exception as e:
        logging.warning(f"image: {e}")


def action_week(peer_id):
    today = date_cls.today()
    start = week_start(today)
    parts = []
    for i in range(6):
        d = start + timedelta(days=i)
        dt = day_type_for(d)
        if not is_working_day(d):
            parts.append(fmt_schedule([], d, dt))
            continue
        _, err = generate_for_date(d.isoformat())
        if err:
            parts.append(f"📅 {d.strftime('%d.%m.%Y')}: ⚠️ {err}")
            continue
        rows = get_schedule(d.isoformat())
        ws = week_start(d).isoformat()
        ww = get_week_assignment_worker(ws)
        parts.append(fmt_schedule(rows, d, dt, ww))
    send_long(peer_id, "\n\n".join(parts), main_menu())


def action_second(peer_id):
    today = date_cls.today()
    ws = week_start(today)
    w = get_week_assignment_worker(ws.isoformat())
    if not w:
        sid, _ = get_second_shift_worker(today.isoformat())
        if sid is None:
            return send(peer_id, "⚠️ Нет работников", main_menu())
        w = get_week_assignment_worker(ws.isoformat())
    workers = list_workers()
    others = [x for x in workers if x["id"] != w["id"]]
    others.sort(key=lambda x: (x["last_second_shift"] or "1970-01-01", x["name"]))
    next_name = others[0]["name"] if others else "—"
    wd_list = working_days_of_week(today)
    wd_str = ", ".join(x.strftime("%d.%m") for x in wd_list)
    send(peer_id,
         f"🌙 Вторая смена — неделя {week_label(today)}\n"
         f"👤 {w['name']}\n"
         f"📆 Рабочие дни: {wd_str}\n\n"
         f"Следующий по ротации: {next_name}",
         main_menu())


def action_workers_list(peer_id):
    ws = list_workers()
    if not ws:
        return send(peer_id, "Список пуст", workers_menu())
    lines = ["👥 Работники:"]
    for w in ws:
        last = w["last_second_shift"] or "—"
        vk = f"VK:{w['vk_id']}" if w["vk_id"] else "VK:—"
        lines.append(f"  • {w['name']}  ({vk}, 2-я смена: {last})")
    send(peer_id, "\n".join(lines), workers_menu())


def action_saturdays_list(peer_id):
    rows = list_working_saturdays()
    lines = ["🟢 Рабочие субботы:"] if rows else ["Рабочих суббот пока нет"]
    for r in rows:
        d = date_cls.fromisoformat(r["date"])
        lines.append(f"  • {d.strftime('%d.%m.%Y (%a)')}")
    send(peer_id, "\n".join(lines), saturdays_menu())


def action_journal_today(peer_id):
    d = date_cls.today()
    shifts, absences = journal_for_date(d.isoformat())
    lines = [f"📖 Журнал за {d.strftime('%d.%m.%Y')}", ""]
    lines.append("☀️ Дневная:")
    day = [s for s in shifts if s["shift"] == "day"]
    if day:
        by_sec = {}
        for s in day:
            by_sec.setdefault(s["section"], []).append(s["name"])
        for sec, names in by_sec.items():
            lines.append(f"  • {sec}: {', '.join(names)}")
    else:
        lines.append("  — никого")
    lines.append("")
    lines.append("🌙 Вторая смена:")
    night = [s for s in shifts if s["shift"] == "second"]
    if night:
        for s in night:
            lines.append(f"  • {s['name']}")
    else:
        lines.append("  — никого")
    lines.append("")
    if absences:
        lines.append("🚫 Отсутствовали:")
        for a in absences:
            lines.append(f"  • {a['name']} — {a['reason'] or '—'}")
    else:
        lines.append("🚫 Отсутствовали: никого")
    send_long(peer_id, "\n".join(lines), journal_menu())


def action_replan(peer_id):
    today = date_cls.today()
    if not is_working_day(today):
        return send(peer_id, "📅 Сегодня выходной", more_menu())
    old_rows = get_schedule(today.isoformat())
    _, err = generate_for_date(today.isoformat())
    if err:
        return send(peer_id, f"⚠️ {err}", more_menu())
    new_rows = get_schedule(today.isoformat())
    diff = compare_schedules(old_rows, new_rows)
    send(peer_id, f"🔄 Пересобрано:\n\n{diff}", more_menu())
    send_long(peer_id, fmt_schedule(new_rows, today, day_type_for(today)), more_menu())


def show_edit_menu(peer_id, date_iso):
    d = date_cls.fromisoformat(date_iso)
    rows = get_schedule_full(date_iso)
    lines = [f"✏️ Правка на {d.strftime('%d.%m.%Y')}", ""]
    day = [r for r in rows if r["shift"] == "day"]
    night = [r for r in rows if r["shift"] == "second"]
    lines.append("☀️ Дневная:")
    if day:
        for r in day:
            lines.append(f"  • {r['name']} — {r['section']}")
    else:
        lines.append("  — никого")
    lines.append("")
    lines.append("🌙 Вторая:")
    if night:
        for r in night:
            lines.append(f"  • {r['name']}")
    else:
        lines.append("  — никого")
    lines.append("")
    lines.append("Что сделать?")
    send(peer_id, "\n".join(lines), edit_menu())


# ============================================================
# HANDLE STATE
# ============================================================
def handle_state(peer_id, user_id, text):
    st = user_states.get(peer_id)
    if not st:
        return False

    if text.strip() == "❌ Отмена":
        reset_state(peer_id)
        send(peer_id, "❌ Отменено", main_menu(get_role(user_id)))
        return True

    state = st["state"]
    data = st.get("data", {})

    if state == "wait_add_name":
        name = text.strip()
        if add_worker(name):
            send(peer_id, f"✅ Добавлен: {name}", workers_menu())
        else:
            send(peer_id, f"⚠️ {name} уже в списке", workers_menu())
        reset_state(peer_id); return True

    if state == "wait_bind_vk":
        try:
            vk_id = int(text.strip())
        except ValueError:
            return send(peer_id, "❌ VK ID — число", cancel_menu())
        name = data.get("name", "")
        if bind_vk(name, vk_id):
            send(peer_id, f"✅ {name} → VK:{vk_id}", workers_menu())
        else:
            send(peer_id, f"⚠️ {name} не найден", workers_menu())
        reset_state(peer_id); return True

    if state == "wait_sick_name":
        name = text.strip()
        today = date_cls.today()
        if not is_working_day(today):
            send(peer_id, "Сегодня выходной", main_menu(get_role(user_id)))
            reset_state(peer_id); return True
        old_rows = get_schedule(today.isoformat())
        if not add_absence(name, today.isoformat(), "заболел"):
            send(peer_id, f"⚠️ {name} не найден", main_menu(get_role(user_id)))
            reset_state(peer_id); return True
        generate_for_date(today.isoformat())
        new_rows = get_schedule(today.isoformat())
        diff = compare_schedules(old_rows, new_rows)
        send(peer_id, f"🚫 {name} — заболел\n🔄 Изменения:\n{diff}", main_menu(get_role(user_id)))
        reset_state(peer_id); return True

    if state == "wait_back_name":
        name = text.strip()
        today = date_cls.today()
        if not remove_absence(name, today.isoformat()):
            send(peer_id, f"⚠️ {name} не отмечен", main_menu(get_role(user_id)))
            reset_state(peer_id); return True
        generate_for_date(today.isoformat())
        send(peer_id, f"✅ {name} вернулся", main_menu(get_role(user_id)))
        reset_state(peer_id); return True

    if state in ("wait_workday_date", "wait_dayoff_date"):
        d = parse_date(text)
        if not d:
            return send(peer_id, "❌ ДД.ММ.ГГГГ", cancel_menu())
        if d.weekday() != 5:
            send(peer_id, "⚠️ Только суббота", saturdays_menu())
            reset_state(peer_id); return True
        working = state == "wait_workday_date"
        set_saturday_working(d.isoformat(), working)
        send(peer_id, f"{'🟢' if working else '🚫'} Суббота {d.strftime('%d.%m.%Y')}", saturdays_menu())
        reset_state(peer_id); return True

    if state == "wait_holiday_add":
        parts = text.split(";", 1)
        d = parse_date(parts[0])
        if not d:
            return send(peer_id, "❌ ДД.ММ.ГГГГ;Название", cancel_menu())
        title = parts[1].strip() if len(parts) > 1 else ""
        add_holiday(d.isoformat(), title)
        send(peer_id, f"🎉 Праздник {d.strftime('%d.%m.%Y')}", saturdays_menu())
        reset_state(peer_id); return True

    if state == "wait_holiday_remove":
        d = parse_date(text)
        if not d:
            return send(peer_id, "❌ ДД.ММ.ГГГГ", cancel_menu())
        if remove_holiday(d.isoformat()):
            send(peer_id, f"✅ Праздник удалён", saturdays_menu())
        else:
            send(peer_id, "Не найдено", saturdays_menu())
        reset_state(peer_id); return True

    if state == "wait_show_date":
        d = parse_date(text)
        if not d:
            return send(peer_id, "❌ ДД.ММ.ГГГГ", cancel_menu())
        rows = get_schedule(d.isoformat())
        if not rows and is_working_day(d):
            _, err = generate_for_date(d.isoformat())
            if err:
                send(peer_id, f"⚠️ {err}", more_menu())
                reset_state(peer_id); return True
            rows = get_schedule(d.isoformat())
        ws = week_start(d).isoformat()
        ww = get_week_assignment_worker(ws)
        send_long(peer_id, fmt_schedule(rows, d, day_type_for(d), ww), more_menu())
        reset_state(peer_id); return True

    if state == "wait_clear_date":
        d = parse_date(text)
        if not d:
            return send(peer_id, "❌ ДД.ММ.ГГГГ", cancel_menu())
        reset_state(peer_id)
        send(peer_id, f"Удалить расписание на {d.strftime('%d.%m.%Y')}?",
             confirm_inline("clear_schedule", d.isoformat()))
        return True

    if state == "wait_absence_name":
        data["name"] = text.strip(); st["data"] = data; st["state"] = "wait_absence_date"
        return send(peer_id, f"Дата для {data['name']} (ДД.ММ.ГГГГ):", cancel_menu())

    if state == "wait_absence_date":
        d = parse_date(text)
        if not d:
            return send(peer_id, "❌ ДД.ММ.ГГГГ", cancel_menu())
        data["date"] = d.isoformat(); st["data"] = data; st["state"] = "wait_absence_reason"
        return send(peer_id, "Причина (или «-»):", cancel_menu())

    if state == "wait_absence_reason":
        reason = "" if text.strip() == "-" else text.strip()
        if add_absence(data.get("name", ""), data.get("date", ""), reason):
            d = date_cls.fromisoformat(data["date"])
            send(peer_id, f"🚫 {data['name']} → {d.strftime('%d.%m.%Y')}", more_menu())
        else:
            send(peer_id, "⚠️ Не найден", more_menu())
        reset_state(peer_id); return True

    if state == "wait_second_set_date":
        d = parse_date(text)
        if not d:
            return send(peer_id, "❌ ДД.ММ.ГГГГ", cancel_menu())
        w = get_worker_by_name(data.get("name", ""))
        if not w:
            send(peer_id, "⚠️ Не найден", more_menu())
            reset_state(peer_id); return True
        ws = week_start(d).isoformat()
        set_week_assignment(ws, w["id"])
        update_last_second_shift(w["id"], ws)
        send(peer_id, f"✅ {w['name']} — 2-я смена на {week_label(d)}", more_menu())
        reset_state(peer_id); return True

    if state == "wait_second_reset_date":
        d = parse_date(text)
        if not d:
            return send(peer_id, "❌ ДД.ММ.ГГГГ", cancel_menu())
        ws = week_start(d).isoformat()
        if clear_week_assignment(ws):
            send(peer_id, f"♻️ Сброшено", more_menu())
        else:
            send(peer_id, "Нечего сбрасывать", more_menu())
        reset_state(peer_id); return True

    if state == "wait_journal_date":
        d = parse_date(text)
        if not d:
            return send(peer_id, "❌ ДД.ММ.ГГГГ", cancel_menu())
        shifts, absences = journal_for_date(d.isoformat())
        lines = [f"📖 Журнал за {d.strftime('%d.%m.%Y')}", ""]
        day = [s for s in shifts if s["shift"] == "day"]
        night = [s for s in shifts if s["shift"] == "second"]
        lines.append("☀️ Дневная:")
        if day:
            for s in day:
                lines.append(f"  • {s['name']} — {s['section']}")
        else:
            lines.append("  — никого")
        lines.append("")
        lines.append("🌙 Вторая:")
        if night:
            for s in night:
                lines.append(f"  • {s['name']}")
        else:
            lines.append("  — никого")
        lines.append("")
        if absences:
            lines.append("🚫 Отсутствовали:")
            for a in absences:
                lines.append(f"  • {a['name']} — {a['reason'] or '—'}")
        else:
            lines.append("🚫 Отсутствовали: никого")
        send_long(peer_id, "\n".join(lines), journal_menu())
        reset_state(peer_id); return True

    if state == "wait_period_start":
        d = parse_date(text)
        if not d:
            return send(peer_id, "❌ ДД.ММ.ГГГГ", cancel_menu())
        data["start"] = d.isoformat(); st["data"] = data; st["state"] = "wait_period_end"
        return send(peer_id, "Дата окончания:", cancel_menu())

    if state == "wait_period_end":
        d = parse_date(text)
        if not d:
            return send(peer_id, "❌ ДД.ММ.ГГГГ", cancel_menu())
        start = data["start"]; end = d.isoformat()
        spw, absences, sbs, night, missed = period_report(start, end)
        d1, d2 = date_cls.fromisoformat(start), date_cls.fromisoformat(end)
        lines = [f"📊 Аналитика {d1.strftime('%d.%m')}–{d2.strftime('%d.%m.%Y')}", ""]
        lines.append("👥 Смены:")
        by_name = {}
        for r in spw:
            by_name.setdefault(r["name"], {})[r["shift"]] = r["cnt"]
        for name, cnts in by_name.items():
            parts = []
            if cnts.get("day"): parts.append(f"день {cnts['day']}")
            if cnts.get("second"): parts.append(f"🌙 {cnts['second']}")
            lines.append(f"  • {name} — {', '.join(parts)}")
        if sbs:
            lines.append("")
            lines.append("📦 По участкам:")
            for r in sbs:
                lines.append(f"  • {r['section']}: {r['cnt']}")
        if not any([spw, sbs]):
            lines.append("Нет данных")
        send_long(peer_id, "\n".join(lines), journal_menu())
        reset_state(peer_id); return True

    if state == "wait_sicklist_date":
        d = parse_date(text)
        if not d:
            return send(peer_id, "❌ ДД.ММ.ГГГГ", cancel_menu())
        rows = sicklist_since(d.isoformat())
        if not rows:
            send(peer_id, f"✅ Нет отсутствий", journal_menu())
            reset_state(peer_id); return True
        lines = [f"🤒 Отсутствия с {d.strftime('%d.%m.%Y')}:"]
        for r in rows:
            lines.append(f"  • {r['date']} — {r['name']} ({r['reason'] or '—'})")
        send_long(peer_id, "\n".join(lines), journal_menu())
        reset_state(peer_id); return True

    if state == "wait_export_start":
        d = parse_date(text)
        if not d:
            return send(peer_id, "❌ ДД.ММ.ГГГГ", cancel_menu())
        data["start"] = d.isoformat(); st["data"] = data; st["state"] = "wait_export_end"
        return send(peer_id, "Дата окончания:", cancel_menu())

    if state == "wait_export_end":
        d = parse_date(text)
        if not d:
            return send(peer_id, "❌ ДД.ММ.ГГГГ", cancel_menu())
        start = data["start"]; end = d.isoformat()
        rows = export_rows(start, end)
        if not rows:
            send(peer_id, "Нечего экспортировать", journal_menu())
            reset_state(peer_id); return True
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["Дата", "Работник", "Участок", "Смена", "Отсутствие"])
        for r in rows:
            w.writerow([r["date"], r["name"], r["section"],
                        "день" if r["shift"] == "day" else "2-я смена",
                        r["absent_reason"] or ""])
        try:
            send_doc(peer_id, buf.getvalue().encode("utf-8-sig"), f"journal_{start}_{end}.csv")
            send(peer_id, "Готово", journal_menu())
        except Exception as e:
            send(peer_id, f"⚠️ {e}", journal_menu())
        reset_state(peer_id); return True

    if state == "wait_broadcast_add":
        try:
            pid = int(text.strip())
        except ValueError:
            return send(peer_id, "❌ число", cancel_menu())
        add_broadcast_target(pid)
        send(peer_id, f"✅ Чат {pid} добавлен", broadcast_menu())
        reset_state(peer_id); return True

    if state == "wait_broadcast_remove":
        try:
            pid = int(text.strip())
        except ValueError:
            return send(peer_id, "❌ число", cancel_menu())
        if remove_broadcast_target(pid):
            send(peer_id, f"🗑 Удалён", broadcast_menu())
        else:
            send(peer_id, "Не найден", broadcast_menu())
        reset_state(peer_id); return True

    if state == "worker_sick_confirm":
        if text.strip().lower() in ("да", "yes", "y", "✅"):
            w = get_worker_by_vk(user_id)
            today = date_cls.today()
            if w and add_absence(w["name"], today.isoformat(), "заболел"):
                generate_for_date(today.isoformat())
                send(peer_id, f"🚫 Отмечено", worker_menu())
            else:
                send(peer_id, "⚠️ Ошибка", worker_menu())
        else:
            send(peer_id, "Отменено", worker_menu())
        reset_state(peer_id); return True

    if state == "pin_section":
        sec = text.strip().lower()
        if sec not in ("приёмка", "сборка", "погрузка", "склад №4"):
            return send(peer_id, "❌ приёмка / сборка / склад №4 / погрузка", pin_sections_menu())
        name = data.get("pin_name", "")
        w = get_worker_by_name(name)
        if not w:
            send(peer_id, f"⚠️ Не найден", pins_menu())
            reset_state(peer_id); return True
        set_pin(w["id"], sec)
        today = date_cls.today()
        if is_working_day(today):
            generate_for_date(today.isoformat())
        send(peer_id, f"📌 {name} закреплён за «{sec}»\nРасписание пересобрано.", pins_menu())
        reset_state(peer_id); return True

    if state == "edit_pick_date":
        if text.strip().lower() in ("сегодня", "today"):
            d = date_cls.today()
        else:
            d = parse_date(text)
        if not d:
            return send(peer_id, "❌ ДД.ММ.ГГГГ или «сегодня»", cancel_menu())
        if not is_working_day(d):
            return send(peer_id, "⚠️ Выходной", cancel_menu())
        data["edit_date"] = d.isoformat(); st["data"] = data; st["state"] = "edit_menu"
        show_edit_menu(peer_id, d.isoformat())
        return True

    if state == "edit_menu":
        date_iso = data.get("edit_date", "")
        t = text.strip()
        workers_all = list_workers()

        if t == "🔀 Переставить":
            if not workers_all:
                return send(peer_id, "Нет работников", edit_menu())
            return send(peer_id, "Кого переставить?", workers_inline("edit_move", workers_all))
        if t == "↔️ Поменять местами":
            if not workers_all:
                return send(peer_id, "Нет работников", edit_menu())
            return send(peer_id, "Первый работник:", workers_inline("edit_swap_a", workers_all))
        if t == "➖ Убрать из смены":
            if not workers_all:
                return send(peer_id, "Нет работников", edit_menu())
            return send(peer_id, "Кого убрать?", workers_inline("edit_remove", workers_all))
        if t == "➕ Добавить в смену":
            if not workers_all:
                return send(peer_id, "Нет работников", edit_menu())
            return send(peer_id, "Кого добавить?", workers_inline("edit_add", workers_all))
        if t == "🌙 Сменить 2-го сменщика":
            if not workers_all:
                return send(peer_id, "Нет работников", edit_menu())
            return send(peer_id, "Кто будет 2-м сменщиком?", workers_inline("edit_second", workers_all))
        if t == "👁 Показать расписание":
            show_edit_menu(peer_id, date_iso); return True
        if t == "⬅️ Назад":
            reset_state(peer_id)
            return send(peer_id, "Главное меню", main_menu(get_role(user_id)))
        return send(peer_id, "Выберите кнопкой", edit_menu())

    if state == "edit_move_section":
        sec = text.strip().lower()
        if sec not in ("приёмка", "сборка", "погрузка", "склад №4"):
            return send(peer_id, "❌ приёмка/сборка/склад №4/погрузка", sections_menu())
        date_iso = data.get("edit_date", "")
        name = data.get("move_name", "")
        if move_worker_in_schedule(date_iso, name, sec):
            send(peer_id, f"✅ {name} → {sec}")
        else:
            send(peer_id, f"⚠️ Не найден")
        user_states[peer_id] = {"state": "edit_menu", "data": {"edit_date": date_iso}}
        show_edit_menu(peer_id, date_iso); return True

    if state == "edit_add_section":
        sec = text.strip().lower()
        if sec not in ("приёмка", "сборка", "погрузка", "склад №4"):
            return send(peer_id, "❌ приёмка/сборка/склад №4/погрузка", sections_menu())
        date_iso = data.get("edit_date", "")
        name = data.get("add_name", "")
        if add_worker_to_schedule(date_iso, name, sec, "day"):
            send(peer_id, f"✅ {name} → {sec}")
        else:
            send(peer_id, f"⚠️ Не найден")
        user_states[peer_id] = {"state": "edit_menu", "data": {"edit_date": date_iso}}
        show_edit_menu(peer_id, date_iso); return True

    return False


# ============================================================
# HANDLE BUTTON
# ============================================================
def handle_button(peer_id, user_id, text):
    role = get_role(user_id)
    t = text.strip()
    main_kb = main_menu(role)

    if t == "📅 Сегодня":
        action_today(peer_id); return True
    if t == "📅 Неделя":
        action_week(peer_id); return True
    if t == "🌙 2-я смена":
        action_second(peer_id); return True
    if t == "👥 Работники":
        if role not in ("admin", "manager"): return False
        send(peer_id, "👥 Работники", workers_menu()); return True
    if t == "🤒 Заболел":
        if role not in ("admin", "manager"): return False
        workers = list_workers()
        if not workers:
            send(peer_id, "Нет работников", main_kb); return True
        send(peer_id, "Кто заболел?", workers_inline("sick", workers))
        return True
    if t == "✅ Вернулся":
        if role not in ("admin", "manager"): return False
        workers = list_workers()
        if not workers:
            send(peer_id, "Нет работников", main_kb); return True
        send(peer_id, "Кто вернулся?", workers_inline("back", workers))
        return True
    if t == "📆 Субботы":
        send(peer_id, "📆 Субботы", saturdays_menu()); return True
    if t == "📖 Журнал":
        send(peer_id, "📖 Журнал", journal_menu()); return True
    if t == "⚙️ Ещё":
        if role != "admin": return False
        send(peer_id, "⚙️ Дополнительно", more_menu()); return True
    if t == "❓ Помощь":
        send(peer_id, HELP_TEXT, main_kb); return True

    if t == "🏗️ Сборка":
        if role not in ("admin", "manager"): return False
        today = date_cls.today()
        if not is_working_day(today):
            send(peer_id, "📅 Сегодня выходной", main_kb); return True
        cur = get_assembly_size(today.isoformat(), 2)
        rows = get_schedule(today.isoformat())
        if not rows:
            _, err = generate_for_date(today.isoformat())
            if err:
                send(peer_id, f"⚠️ {err}", main_kb); return True
            rows = get_schedule(today.isoformat())
        day = [r for r in rows if r["shift"] == "day"]
        asm = [r["name"] for r in day if r["section"] == "сборка"]
        rec = [r["name"] for r in day if r["section"] == "приёмка"]
        lod = [r["name"] for r in day if r["section"] == "погрузка"]
        wh4 = [r["name"] for r in day if r["section"] == "склад №4"]
        txt = (f"🏗️ Сборка на {today.strftime('%d.%m.%Y')}\n\n"
               f"Сборка ({cur}): {', '.join(asm) or '—'}\n"
               f"Приёмка: {', '.join(rec) or '—'}\n"
               f"Склад №4: {', '.join(wh4) or '—'}\n"
               f"Погрузка: {', '.join(lod) or '—'}")
        send(peer_id, txt, assembly_menu()); return True

    if t == "➕ Добавить на сборку":
        today = date_cls.today()
        cur = get_assembly_size(today.isoformat(), 2)
        if cur >= 4:
            send(peer_id, "⚠️ Максимум 4", assembly_menu()); return True
        set_assembly_size(today.isoformat(), cur + 1)
        generate_for_date(today.isoformat())
        rows = get_schedule(today.isoformat())
        day = [r for r in rows if r["shift"] == "day"]
        asm = [r["name"] for r in day if r["section"] == "сборка"]
        send(peer_id, f"✅ Сборка ({cur+1}): {', '.join(asm)}", assembly_menu()); return True

    if t == "➖ Убрать со сборки":
        today = date_cls.today()
        cur = get_assembly_size(today.isoformat(), 2)
        if cur <= 1:
            send(peer_id, "⚠️ Минимум 1", assembly_menu()); return True
        set_assembly_size(today.isoformat(), cur - 1)
        generate_for_date(today.isoformat())
        rows = get_schedule(today.isoformat())
        day = [r for r in rows if r["shift"] == "day"]
        asm = [r["name"] for r in day if r["section"] == "сборка"]
        send(peer_id, f"✅ Сборка ({cur-1}): {', '.join(asm) or '—'}", assembly_menu()); return True

    if t == "👁 Показать расписание":
        action_today(peer_id); return True

    if t == "🔄 Пересобрать день":
        today = date_cls.today()
        generate_for_date(today.isoformat())
        rows = get_schedule(today.isoformat())
        day = [r for r in rows if r["shift"] == "day"]
        asm = [r["name"] for r in day if r["section"] == "сборка"]
        send(peer_id, f"🔄 Пересобрано. Сборка: {', '.join(asm) or '—'}", assembly_menu()); return True

    if t == "➕ Добавить":
        user_states[peer_id] = {"state": "wait_add_name", "data": {}}
        send(peer_id, "Введите имя нового работника:", cancel_menu()); return True
    if t == "🗑 Удалить":
        workers = list_workers()
        if not workers:
            send(peer_id, "Нет работников", workers_menu()); return True
        send(peer_id, "Кого удалить?", workers_inline("remove", workers)); return True
    if t == "📋 Список":
        action_workers_list(peer_id); return True
    if t == "📊 История":
        workers = list_workers()
        if not workers:
            send(peer_id, "Нет работников", workers_menu()); return True
        send(peer_id, "Чья история?", workers_inline("history", workers)); return True
    if t == "🔗 Привязать VK":
        workers = list_workers()
        if not workers:
            send(peer_id, "Нет работников", workers_menu()); return True
        send(peer_id, "Кого привязать?", workers_inline("bind", workers)); return True
    if t == "🔓 Отвязать VK":
        workers = list_workers()
        if not workers:
            send(peer_id, "Нет работников", workers_menu()); return True
        send(peer_id, "Кого отвязать?", workers_inline("unbind", workers)); return True

    if t == "➕ Суббота рабочая":
        user_states[peer_id] = {"state": "wait_workday_date", "data": {}}
        send(peer_id, "Дата субботы (ДД.ММ.ГГГГ):", cancel_menu()); return True
    if t == "➖ Суббота выходная":
        user_states[peer_id] = {"state": "wait_dayoff_date", "data": {}}
        send(peer_id, "Дата субботы:", cancel_menu()); return True
    if t == "📋 Список суббот":
        action_saturdays_list(peer_id); return True
    if t == "🎉 Праздник добавить":
        user_states[peer_id] = {"state": "wait_holiday_add", "data": {}}
        send(peer_id, "Формат: ДД.ММ.ГГГГ;Название", cancel_menu()); return True
    if t == "🎉 Праздник удалить":
        user_states[peer_id] = {"state": "wait_holiday_remove", "data": {}}
        send(peer_id, "Дата праздника:", cancel_menu()); return True

    if t == "📖 Журнал сегодня":
        action_journal_today(peer_id); return True
    if t == "📅 Журнал за дату":
        user_states[peer_id] = {"state": "wait_journal_date", "data": {}}
        send(peer_id, "Дата:", cancel_menu()); return True
    if t == "📊 Аналитика":
        user_states[peer_id] = {"state": "wait_period_start", "data": {}}
        send(peer_id, "Начало периода:", cancel_menu()); return True
    if t == "🤒 Отсутствия":
        user_states[peer_id] = {"state": "wait_sicklist_date", "data": {}}
        send(peer_id, "С даты:", cancel_menu()); return True
    if t == "📎 Экспорт CSV":
        user_states[peer_id] = {"state": "wait_export_start", "data": {}}
        send(peer_id, "Начало:", cancel_menu()); return True

    if t == "📅 На дату":
        user_states[peer_id] = {"state": "wait_show_date", "data": {}}
        send(peer_id, "Дата:", cancel_menu()); return True
    if t == "📆 Отметить отсутствие":
        user_states[peer_id] = {"state": "wait_absence_name", "data": {}}
        send(peer_id, "Имя работника:", cancel_menu()); return True
    if t == "✏️ Править расписание":
        if role != "admin": return False
        user_states[peer_id] = {"state": "edit_pick_date", "data": {}}
        send(peer_id, "Дата (или «сегодня»):", cancel_menu()); return True
    if t == "🔧 Пересобрать":
        action_replan(peer_id); return True
    if t == "🌙 Назначить 2-ю":
        workers = list_workers()
        if not workers:
            send(peer_id, "Нет работников", more_menu()); return True
        send(peer_id, "Кто будет 2-м сменщиком?", workers_inline("second_set", workers)); return True
    if t == "♻️ Сбросить 2-ю":
        user_states[peer_id] = {"state": "wait_second_reset_date", "data": {}}
        send(peer_id, "Дата недели:", cancel_menu()); return True
    if t == "🗑 Удалить расписание":
        user_states[peer_id] = {"state": "wait_clear_date", "data": {}}
        send(peer_id, "Дата:", cancel_menu()); return True
    if t == "🔔 Рассылки":
        send(peer_id, "🔔 Рассылки", broadcast_menu()); return True
    if t == "💾 Бэкап сейчас":
        try:
            with open(DB_PATH, "rb") as f:
                send_doc(peer_id, f.read(), f"backup_{date_cls.today().isoformat()}.db")
            send(peer_id, "✅ Бэкап отправлен", more_menu())
        except Exception as e:
            send(peer_id, f"⚠️ {e}", more_menu())
        return True

    if t == "📌 Закрепления":
        if role != "admin": return False
        send(peer_id, "📌 Закрепления", pins_menu()); return True
    if t == "📌 Закрепить":
        workers = list_workers()
        if not workers:
            send(peer_id, "Нет работников", pins_menu()); return True
        send(peer_id, "Кого закрепить?", workers_inline("pin", workers)); return True
    if t == "🔓 Открепить":
        workers = list_workers()
        if not workers:
            send(peer_id, "Нет работников", pins_menu()); return True
        send(peer_id, "Кого открепить?", workers_inline("unpin", workers)); return True
    if t == "📋 Список закреплений":
        rows = list_pins()
        if not rows:
            send(peer_id, "Закреплений нет", pins_menu()); return True
        lines = ["📌 Закрепления:"]
        by_sec = {}
        for r in rows:
            by_sec.setdefault(r["section"], []).append(r["name"])
        for sec in ["приёмка", "сборка", "склад №4", "погрузка"]:
            names = by_sec.get(sec, [])
            if names:
                lines.append(f"  • {sec.capitalize()}: {', '.join(names)}")
        send(peer_id, "\n".join(lines), pins_menu()); return True

    if t == "➕ Добавить чат":
        user_states[peer_id] = {"state": "wait_broadcast_add", "data": {}}
        send(peer_id, "peer_id:", cancel_menu()); return True
    if t == "🗑 Удалить чат":
        user_states[peer_id] = {"state": "wait_broadcast_remove", "data": {}}
        send(peer_id, "peer_id:", cancel_menu()); return True
    if t == "📋 Список чатов":
        pids = list_broadcast_targets()
        txt = "📋 Чаты:\n" + ("\n".join(f"  • {p}" for p in pids) if pids else "  — пусто")
        send(peer_id, txt, broadcast_menu()); return True

    if t == "📅 Моё расписание":
        w = get_worker_by_vk(user_id)
        if not w: return False
        today = date_cls.today()
        ws = week_start(today)
        lines = [f"👤 {w['name']} — неделя {week_label(today)}", ""]
        for i in range(6):
            d = ws + timedelta(days=i)
            if not is_working_day(d):
                continue
            rows = get_schedule(d.isoformat())
            my = [r for r in rows if r["name"] == w["name"]]
            if my:
                sec, sh = my[0]["section"], my[0]["shift"]
                tag = "🌙" if sh == "second" else "☀️"
                sec_str = "2-я смена" if sh == "second" else sec
                lines.append(f"  {d.strftime('%a %d.%m')}: {tag} {sec_str}")
            else:
                lines.append(f"  {d.strftime('%a %d.%m')}: выходной")
        send(peer_id, "\n".join(lines), worker_menu()); return True
    if t == "🤒 Я заболел":
        w = get_worker_by_vk(user_id)
        if not w: return False
        user_states[peer_id] = {"state": "worker_sick_confirm", "data": {}}
        send(peer_id, f"Отметить {w['name']}? Напишите «да»", cancel_menu()); return True
    if t == "✅ Я вернулся":
        w = get_worker_by_vk(user_id)
        if not w: return False
        today = date_cls.today()
        if remove_absence(w["name"], today.isoformat()):
            generate_for_date(today.isoformat())
            send(peer_id, f"✅ Снова в строю", worker_menu())
        else:
            send(peer_id, "Не был отмечен", worker_menu())
        return True
    if t == "ℹ️ Профиль":
        w = get_worker_by_vk(user_id)
        if not w: return False
        send(peer_id, f"👤 {w['name']}\nVK ID: {w['vk_id']}\n2-я смена: {w['last_second_shift'] or '—'}", worker_menu())
        return True

    if t == "⬅️ Назад":
        send(peer_id, "Главное меню", main_kb); return True

    return False


# ============================================================
# INLINE — исправлено (убирает кружок загрузки)
# ============================================================
def _payload_dict(raw):
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


def handle_inline(event):
    obj = event.object

    def _get(name, default=None):
        try:
            return getattr(obj, name)
        except AttributeError:
            try:
                return obj[name]
            except (KeyError, TypeError):
                return default

    event_id = _get("event_id")
    user_id = _get("user_id")
    peer_id = _get("peer_id")
    payload = _payload_dict(_get("payload"))

    logging.info(f"inline: user={user_id} peer={peer_id} payload={payload}")

    # --- ЗАКРЫТИЕ CALLBACK (снимает кружок загрузки) ---
    if event_id and user_id and peer_id:
        try:
            vk.messages.sendMessageEventAnswer(
                event_id=str(event_id),
                user_id=str(user_id),
                peer_id=str(peer_id),
                event_data=json.dumps({"type": "show_snackbar", "text": "OK"}),
            )
        except Exception as e:
            logging.warning(f"sendMessageEventAnswer error: {e}")
            try:
                vk.messages.sendMessageEventAnswer(
                    event_id=str(event_id),
                    user_id=str(user_id),
                    peer_id=str(peer_id),
                )
            except Exception as e2:
                logging.warning(f"sendMessageEventAnswer retry error: {e2}")

    try:
        _process_inline(peer_id, user_id, payload)
    except Exception as e:
        logging.exception("inline process error")
        send(peer_id, f"⚠️ Ошибка: {e}", main_menu(get_role(user_id)))


def _process_inline(peer_id, user_id, payload):
    cmd = payload.get("cmd")

    if cmd == "cancel_inline":
        send(peer_id, "Отменено", main_menu(get_role(user_id)))
        return

    if cmd == "confirm":
        action = payload.get("action")
        extra = payload.get("extra", "")
        if action == "remove_worker":
            if remove_worker(extra):
                send(peer_id, f"🗑 Удалён: {extra}", workers_menu())
            else:
                send(peer_id, "⚠️ Не найден", workers_menu())
        elif action == "clear_schedule":
            if clear_schedule(extra):
                send(peer_id, f"🗑 Удалено", more_menu())
            else:
                send(peer_id, "Нечего удалять", more_menu())
        return

    if cmd != "pick_w":
        logging.warning(f"unknown inline cmd: {cmd}")
        return

    p = payload.get("p")
    n = payload.get("n")
    today = date_cls.today()
    role = get_role(user_id)

    if p == "pin":
        user_states[peer_id] = {"state": "pin_section", "data": {"pin_name": n}}
        send(peer_id, f"На какой участок закрепить «{n}»?", pin_sections_menu())
        return

    if p == "unpin":
        w = get_worker_by_name(n)
        if w and remove_pin(w["id"]):
            if is_working_day(today):
                generate_for_date(today.isoformat())
            send(peer_id, f"🔓 {n} откреплён\nРасписание пересобрано.", pins_menu())
        else:
            send(peer_id, f"⚠️ {n} не был закреплён", pins_menu())
        return

    if p == "sick":
        if not is_working_day(today):
            send(peer_id, "Сегодня выходной", main_menu(role))
            return
        old_rows = get_schedule(today.isoformat())
        if not add_absence(n, today.isoformat(), "заболел"):
            send(peer_id, f"⚠️ {n} не найден", main_menu(role))
            return
        generate_for_date(today.isoformat())
        new_rows = get_schedule(today.isoformat())
        diff = compare_schedules(old_rows, new_rows)
        send(peer_id, f"🚫 {n} — заболел\n🔄 Изменения:\n{diff}", main_menu(role))
        return

    if p == "back":
        if not remove_absence(n, today.isoformat()):
            send(peer_id, f"⚠️ {n} не отмечен", main_menu(role))
            return
        generate_for_date(today.isoformat())
        send(peer_id, f"✅ {n} вернулся", main_menu(role))
        return

    if p == "history":
        shifts, absences = history_worker(n, 30)
        if shifts is None:
            send(peer_id, f"⚠️ {n} не найден", workers_menu())
            return
        lines = [f"📖 История: {n} (30 дней)", ""]
        if shifts:
            lines.append("Работал:")
            for s in shifts:
                tag = "🌙" if s["shift"] == "second" else "☀️"
                sec = "" if s["shift"] == "second" else f" {s['section']}"
                lines.append(f"  • {s['date']} {tag}{sec}")
        else:
            lines.append("Работал: —")
        lines.append("")
        if absences:
            lines.append("Отсутствовал:")
            for a in absences:
                lines.append(f"  • {a['date']} — {a['reason'] or '—'}")
        else:
            lines.append("Отсутствовал: —")
        send_long(peer_id, "\n".join(lines), workers_menu())
        return

    if p == "remove":
        send(peer_id, f"Удалить {n}?", confirm_inline("remove_worker", n))
        return

    if p == "bind":
        user_states[peer_id] = {"state": "wait_bind_vk", "data": {"name": n}}
        send(peer_id, f"Введите VK ID для «{n}»:", cancel_menu())
        return

    if p == "unbind":
        if unbind_vk(n):
            send(peer_id, f"🔓 {n} отвязан", workers_menu())
        else:
            send(peer_id, f"⚠️ {n} не найден", workers_menu())
        return

    if p == "second_set":
        user_states[peer_id] = {"state": "wait_second_set_date", "data": {"name": n}}
        send(peer_id, f"Дата из недели для «{n}»:", cancel_menu())
        return

    st = user_states.get(peer_id, {})
    date_iso = st.get("data", {}).get("edit_date", "")

    if p == "edit_move":
        user_states[peer_id] = {"state": "edit_move_section",
                                "data": {"edit_date": date_iso, "move_name": n}}
        send(peer_id, f"На какой участок «{n}»?", sections_menu())
        return

    if p == "edit_swap_a":
        user_states[peer_id] = {"state": "edit_swap_second",
                                "data": {"edit_date": date_iso, "swap_a": n}}
        send(peer_id, f"С кем поменять «{n}»?", workers_inline("edit_swap_b", list_workers()))
        return

    if p == "edit_swap_b":
        a = st.get("data", {}).get("swap_a", "")
        ok, msg = swap_workers_in_schedule(date_iso, a, n)
        send(peer_id, f"✅ Поменяли {a} ↔ {n}" if ok else f"⚠️ {msg}")
        user_states[peer_id] = {"state": "edit_menu", "data": {"edit_date": date_iso}}
        show_edit_menu(peer_id, date_iso)
        return

    if p == "edit_remove":
        if remove_worker_from_schedule(date_iso, n):
            send(peer_id, f"➖ {n} убран")
        else:
            send(peer_id, f"⚠️ Не найден")
        user_states[peer_id] = {"state": "edit_menu", "data": {"edit_date": date_iso}}
        show_edit_menu(peer_id, date_iso)
        return

    if p == "edit_add":
        user_states[peer_id] = {"state": "edit_add_section",
                                "data": {"edit_date": date_iso, "add_name": n}}
        send(peer_id, f"На какой участок «{n}»?", sections_menu())
        return

    if p == "edit_second":
        if set_day_second_shift(date_iso, n):
            send(peer_id, f"✅ {n} — 2-я смена на этот день")
        else:
            send(peer_id, f"⚠️ Не найден")
        user_states[peer_id] = {"state": "edit_menu", "data": {"edit_date": date_iso}}
        show_edit_menu(peer_id, date_iso)
        return

    logging.warning(f"unknown pick prefix: {p}")


# ============================================================
# MAIN
# ============================================================
def main():
    init_db()
    logging.info("VK bot starting…")
    start_scheduler()

    for event in longpoll.listen():
        if event.type == VkBotEventType.MESSAGE_NEW:
            msg = event.object.message
            text = (msg.get("text") or "").strip()
            peer_id = msg.get("peer_id")
            user_id = msg.get("from_id")
            if not text:
                continue
            role = get_role(user_id)

            if text.lower() == "/whoami":
                send(peer_id, f"Твой VK ID: {user_id}")
                continue

            if text.lower() in ("/start", "/help", "начать", "меню", "start"):
                if role == "guest":
                    send(peer_id, "⛔ Нет доступа. Попроси админа привязать твой VK ID.")
                elif role == "worker":
                    send(peer_id, WORKER_HELP, worker_menu())
                else:
                    send(peer_id, HELP_TEXT, main_menu(role))
                continue

            if role == "guest":
                send(peer_id, "⛔ Нет доступа")
                continue

            try:
                if handle_state(peer_id, user_id, text):
                    continue
                if handle_button(peer_id, user_id, text):
                    continue
                if role == "worker":
                    send(peer_id, "🤔 Выбери пункт меню", worker_menu())
                else:
                    send(peer_id, "🤔 Выбери пункт меню", main_menu(role))
            except Exception as e:
                logging.exception("handle error")
                send(peer_id, f"⚠️ Ошибка: {e}", main_menu(role))

        elif event.type == VkBotEventType.MESSAGE_EVENT:
            try:
                handle_inline(event)
            except Exception:
                logging.exception("inline error")


if __name__ == "__main__":
    main()
