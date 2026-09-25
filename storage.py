import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
import config

DB_PATH = config.DB_PATH


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
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(workers)")]
        if "vk_id" not in cols:
            conn.execute("ALTER TABLE workers ADD COLUMN vk_id INTEGER")


def add_worker(name: str) -> bool:
    with get_db() as conn:
        try:
            conn.execute("INSERT INTO workers (name) VALUES (?)", (name,))
            return True
        except sqlite3.IntegrityError:
            return False


def remove_worker(name: str) -> bool:
    with get_db() as conn:
        cur = conn.execute("DELETE FROM workers WHERE name = ?", (name,))
        return cur.rowcount > 0


def list_workers():
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM workers WHERE active = 1 ORDER BY name"
        ).fetchall()


def get_worker_by_name(name: str):
    with get_db() as conn:
        return conn.execute("SELECT * FROM workers WHERE name=?", (name,)).fetchone()


def get_worker_by_vk(vk_id: int):
    with get_db() as conn:
        return conn.execute("SELECT * FROM workers WHERE vk_id=?", (vk_id,)).fetchone()


def bind_vk(name: str, vk_id: int) -> bool:
    with get_db() as conn:
        cur = conn.execute("UPDATE workers SET vk_id=? WHERE name=?", (vk_id, name))
        return cur.rowcount > 0


def unbind_vk(name: str) -> bool:
    with get_db() as conn:
        cur = conn.execute("UPDATE workers SET vk_id=NULL WHERE name=?", (name,))
        return cur.rowcount > 0


def add_absence(name: str, date_str: str, reason: str = "") -> bool:
    w = get_worker_by_name(name)
    if not w:
        return False
    with get_db() as conn:
        try:
            conn.execute(
                "INSERT INTO absences (worker_id, date, reason) VALUES (?,?,?)",
                (w["id"], date_str, reason),
            )
        except sqlite3.IntegrityError:
            conn.execute(
                "UPDATE absences SET reason=? WHERE worker_id=? AND date=?",
                (reason, w["id"], date_str),
            )
        return True


def remove_absence(name: str, date_str: str) -> bool:
    w = get_worker_by_name(name)
    if not w:
        return False
    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM absences WHERE worker_id=? AND date=?",
            (w["id"], date_str),
        )
        return cur.rowcount > 0


def get_absent_ids(date_str: str) -> set:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT worker_id FROM absences WHERE date=?", (date_str,)
        ).fetchall()
        return {r["worker_id"] for r in rows}


def list_absences(date_str: str):
    with get_db() as conn:
        return conn.execute(
            """SELECT w.name, a.reason FROM absences a
               JOIN workers w ON w.id = a.worker_id
               WHERE a.date = ? ORDER BY w.name""",
            (date_str,),
        ).fetchall()


def get_schedule(date_str: str):
    with get_db() as conn:
        return conn.execute(
            """SELECT s.section, s.shift, w.name
               FROM schedule s JOIN workers w ON w.id = s.worker_id
               WHERE s.date=? ORDER BY s.shift, s.section, w.name""",
            (date_str,),
        ).fetchall()


def get_schedule_full(date_str: str):
    with get_db() as conn:
        return conn.execute(
            """SELECT s.worker_id as id, w.name, s.section, s.shift
               FROM schedule s JOIN workers w ON w.id = s.worker_id
               WHERE s.date=? ORDER BY s.shift, s.section, w.name""",
            (date_str,),
        ).fetchall()


def save_schedule(date_str: str, assignments: list):
    with get_db() as conn:
        conn.execute("DELETE FROM schedule WHERE date=?", (date_str,))
        for a in assignments:
            conn.execute(
                "INSERT INTO schedule (date, worker_id, section, shift) VALUES (?,?,?,?)",
                (date_str, a["worker_id"], a["section"], a["shift"]),
            )


def clear_schedule(date_str: str) -> bool:
    with get_db() as conn:
        cur = conn.execute("DELETE FROM schedule WHERE date=?", (date_str,))
        return cur.rowcount > 0


def update_last_second_shift(worker_id: int, week_start: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE workers SET last_second_shift=? WHERE id=?",
            (week_start, worker_id),
        )


def get_week_assignment(week_start: str):
    with get_db() as conn:
        row = conn.execute(
            "SELECT worker_id FROM week_assignments WHERE week_start=?",
            (week_start,),
        ).fetchone()
        return row["worker_id"] if row else None


def set_week_assignment(week_start: str, worker_id: int):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO week_assignments (week_start, worker_id) VALUES (?,?)",
            (week_start, worker_id),
        )


def get_week_assignment_worker(week_start: str):
    with get_db() as conn:
        return conn.execute(
            """SELECT w.* FROM week_assignments a
               JOIN workers w ON w.id = a.worker_id
               WHERE a.week_start=?""",
            (week_start,),
        ).fetchone()


def clear_week_assignment(week_start: str) -> bool:
    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM week_assignments WHERE week_start=?", (week_start,)
        )
        return cur.rowcount > 0


def set_saturday_working(date_str: str, working: bool):
    with get_db() as conn:
        if working:
            conn.execute(
                "INSERT OR IGNORE INTO working_saturdays (date) VALUES (?)", (date_str,)
            )
        else:
            conn.execute("DELETE FROM working_saturdays WHERE date=?", (date_str,))


def is_saturday_working(date_str: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM working_saturdays WHERE date=?", (date_str,)
        ).fetchone()
        return row is not None


def list_working_saturdays():
    with get_db() as conn:
        return conn.execute(
            "SELECT date FROM working_saturdays ORDER BY date"
        ).fetchall()


def add_holiday(date_str: str, title: str = ""):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO holidays (date, title) VALUES (?,?)",
            (date_str, title),
        )


def remove_holiday(date_str: str) -> bool:
    with get_db() as conn:
        cur = conn.execute("DELETE FROM holidays WHERE date=?", (date_str,))
        return cur.rowcount > 0


def is_holiday(date_str: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM holidays WHERE date=?", (date_str,)
        ).fetchone()
        return row is not None


def add_broadcast_target(peer_id: int):
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO broadcast_targets (peer_id) VALUES (?)", (peer_id,)
        )


def remove_broadcast_target(peer_id: int) -> bool:
    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM broadcast_targets WHERE peer_id=?", (peer_id,)
        )
        return cur.rowcount > 0


def list_broadcast_targets():
    with get_db() as conn:
        return [r["peer_id"] for r in conn.execute(
            "SELECT peer_id FROM broadcast_targets ORDER BY peer_id"
        ).fetchall()]


def get_setting(key: str, default: str = "") -> str:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM bot_settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?,?)",
            (key, value),
        )


def history_worker(name: str, days: int = 30):
    since = (date.today() - timedelta(days=days)).isoformat()
    w = get_worker_by_name(name)
    if not w:
        return None, None
    with get_db() as conn:
        shifts = conn.execute(
            """SELECT date, section, shift FROM schedule
               WHERE worker_id=? AND date>=? ORDER BY date DESC""",
            (w["id"], since),
        ).fetchall()
        absences = conn.execute(
            """SELECT date, reason FROM absences
               WHERE worker_id=? AND date>=? ORDER BY date DESC""",
            (w["id"], since),
        ).fetchall()
    return shifts, absences


def journal_for_date(date_str: str):
    with get_db() as conn:
        shifts = conn.execute(
            """SELECT w.name, s.section, s.shift FROM schedule s
               JOIN workers w ON w.id = s.worker_id
               WHERE s.date=? ORDER BY s.shift, s.section""",
            (date_str,),
        ).fetchall()
        absences = conn.execute(
            """SELECT w.name, a.reason FROM absences a
               JOIN workers w ON w.id = a.worker_id
               WHERE a.date=? ORDER BY w.name""",
            (date_str,),
        ).fetchall()
    return shifts, absences


def period_report(start: str, end: str):
    with get_db() as conn:
        spw = conn.execute(
            """SELECT w.name, s.shift, COUNT(*) as cnt
               FROM schedule s JOIN workers w ON w.id = s.worker_id
               WHERE s.date BETWEEN ? AND ?
               GROUP BY w.name, s.shift ORDER BY w.name""",
            (start, end),
        ).fetchall()
        absences = conn.execute(
            """SELECT w.name, a.date, a.reason
               FROM absences a JOIN workers w ON w.id = a.worker_id
               WHERE a.date BETWEEN ? AND ?
               ORDER BY a.date, w.name""",
            (start, end),
        ).fetchall()
        sbs = conn.execute(
            """SELECT section, COUNT(*) as cnt FROM schedule
               WHERE date BETWEEN ? AND ? GROUP BY section""",
            (start, end),
        ).fetchall()
        night = conn.execute(
            """SELECT w.name, COUNT(*) as cnt FROM schedule s
               JOIN workers w ON w.id = s.worker_id
               WHERE s.date BETWEEN ? AND ? AND s.shift='second'
               GROUP BY w.name ORDER BY cnt DESC""",
            (start, end),
        ).fetchall()
        missed = conn.execute(
            """SELECT w.name, COUNT(*) as cnt FROM absences a
               JOIN workers w ON w.id = a.worker_id
               WHERE a.date BETWEEN ? AND ?
               GROUP BY w.name ORDER BY cnt DESC""",
            (start, end),
        ).fetchall()
    return spw, absences, sbs, night, missed


def sicklist_since(start: str):
    with get_db() as conn:
        return conn.execute(
            """SELECT w.name, a.date, a.reason
               FROM absences a JOIN workers w ON w.id = a.worker_id
               WHERE a.date >= ? ORDER BY a.date DESC""",
            (start,),
        ).fetchall()


def export_rows(start: str, end: str):
    with get_db() as conn:
        return conn.execute(
            """SELECT s.date, w.name, s.section, s.shift,
                      COALESCE(a.reason, '') as absent_reason
               FROM schedule s
               JOIN workers w ON w.id = s.worker_id
               LEFT JOIN absences a ON a.worker_id = w.id AND a.date = s.date
               WHERE s.date BETWEEN ? AND ?
               ORDER BY s.date, s.shift, w.name""",
            (start, end),
        ).fetchall()


# ---------- РУЧНАЯ ПРАВКА РАСПИСАНИЯ ----------

def move_worker_in_schedule(date_str: str, name: str, new_section: str) -> bool:
    """Переставить человека на другой участок в конкретный день."""
    w = get_worker_by_name(name)
    if not w:
        return False
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE schedule SET section=? WHERE date=? AND worker_id=?",
            (new_section, date_str, w["id"]),
        )
        return cur.rowcount > 0


def remove_worker_from_schedule(date_str: str, name: str) -> bool:
    """Убрать человека из расписания на день."""
    w = get_worker_by_name(name)
    if not w:
        return False
    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM schedule WHERE date=? AND worker_id=?",
            (date_str, w["id"]),
        )
        return cur.rowcount > 0


def add_worker_to_schedule(date_str: str, name: str, section: str, shift: str) -> bool:
    """Добавить человека в расписание на день."""
    w = get_worker_by_name(name)
    if not w:
        return False
    with get_db() as conn:
        try:
            conn.execute(
                "INSERT INTO schedule (date, worker_id, section, shift) VALUES (?,?,?,?)",
                (date_str, w["id"], section, shift),
            )
        except sqlite3.IntegrityError:
            conn.execute(
                "UPDATE schedule SET section=?, shift=? WHERE date=? AND worker_id=?",
                (section, shift, date_str, w["id"]),
            )
        return True


def swap_workers_in_schedule(date_str: str, name_a: str, name_b: str):
    """Поменять двух работников местами (участок+смена)."""
    a = get_worker_by_name(name_a)
    b = get_worker_by_name(name_b)
    if not a or not b:
        return False, "Один из работников не найден"
    with get_db() as conn:
        ra = conn.execute(
            "SELECT section, shift FROM schedule WHERE date=? AND worker_id=?",
            (date_str, a["id"]),
        ).fetchone()
        rb = conn.execute(
            "SELECT section, shift FROM schedule WHERE date=? AND worker_id=?",
            (date_str, b["id"]),
        ).fetchone()
        if not ra or not rb:
            return False, "Кто-то не в расписании на этот день"
        conn.execute(
            "UPDATE schedule SET section=?, shift=? WHERE date=? AND worker_id=?",
            (rb["section"], rb["shift"], date_str, a["id"]),
        )
        conn.execute(
            "UPDATE schedule SET section=?, shift=? WHERE date=? AND worker_id=?",
            (ra["section"], ra["shift"], date_str, b["id"]),
        )
    return True, "OK"


def set_day_second_shift(date_str: str, name: str) -> bool:
    """Назначить 2-го сменщика на конкретный день (только на день)."""
    w = get_worker_by_name(name)
    if not w:
        return False
    with get_db() as conn:
        conn.execute(
            "DELETE FROM schedule WHERE date=? AND shift='second'",
            (date_str,),
        )
        try:
            conn.execute(
                "INSERT INTO schedule (date, worker_id, section, shift) VALUES (?,?,?,?)",
                (date_str, w["id"], "—", "second"),
            )
        except sqlite3.IntegrityError:
            conn.execute(
                "UPDATE schedule SET section='—', shift='second' WHERE date=? AND worker_id=?",
                (date_str, w["id"]),
            )
    return True
