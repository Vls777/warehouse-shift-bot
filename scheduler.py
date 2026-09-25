from datetime import date as date_cls, timedelta
import storage

SECTIONS = ["приёмка", "сборка", "погрузка"]
ASSEMBLY_SIZE = 2
RECEIVING_SIZE = 1
SUNDAY = 6
SATURDAY = 5


def week_start(d: date_cls) -> date_cls:
    return d - timedelta(days=d.weekday())


def week_label(d: date_cls) -> str:
    ws = week_start(d)
    we = ws + timedelta(days=6)
    return f"{ws.strftime('%d.%m')}–{we.strftime('%d.%m.%Y')}"


def is_working_day(d: date_cls) -> bool:
    if d.weekday() == SUNDAY:
        return False
    if storage.is_holiday(d.isoformat()):
        return False
    if d.weekday() == SATURDAY:
        return storage.is_saturday_working(d.isoformat())
    return True


def working_days_of_week(d: date_cls):
    ws = week_start(d)
    return [
        ws + timedelta(days=i)
        for i in range(6)
        if is_working_day(ws + timedelta(days=i))
    ]


def get_second_shift_worker(date_str: str):
    d = date_cls.fromisoformat(date_str)
    ws = week_start(d).isoformat()

    workers = storage.list_workers()
    if not workers:
        return None, False

    worker_ids = {w["id"] for w in workers}
    assigned = storage.get_week_assignment(ws)
    if assigned is None or assigned not in worker_ids:
        workers_sorted = sorted(
            workers,
            key=lambda w: (w["last_second_shift"] or "1970-01-01", w["name"]),
        )
        chosen = workers_sorted[0]
        storage.set_week_assignment(ws, chosen["id"])
        storage.update_last_second_shift(chosen["id"], ws)
        assigned = chosen["id"]

    absent = storage.get_absent_ids(date_str)
    if assigned in absent:
        return None, False
    return assigned, False


def generate_for_date(date_str: str):
    d = date_cls.fromisoformat(date_str)
    if not is_working_day(d):
        return None, f"{d.strftime('%d.%m.%Y')} — выходной"

    workers = storage.list_workers()
    if not workers:
        return None, "Нет работников"

    absent = storage.get_absent_ids(date_str)
    available = [dict(w) for w in workers if w["id"] not in absent]
    if not available:
        return None, "Все отсутствуют в этот день"

    second_id, _ = get_second_shift_worker(date_str)
    day_workers = [w for w in available if w["id"] != second_id]

    day_workers.sort(key=lambda w: w["name"])
    if day_workers:
        shift = d.toordinal() % len(day_workers)
        day_workers = day_workers[shift:] + day_workers[:shift]

    assignments = []

    if second_id is not None:
        assignments.append(
            {"worker_id": second_id, "section": "—", "shift": "second"}
        )

    n = len(day_workers)
    n_receiving = min(RECEIVING_SIZE, n) if n >= 1 else 0
    n_assembly = min(ASSEMBLY_SIZE, max(0, n - n_receiving))

    idx = 0
    for _ in range(n_receiving):
        assignments.append(
            {"worker_id": day_workers[idx]["id"], "section": "приёмка", "shift": "day"}
        )
        idx += 1
    for _ in range(n_assembly):
        assignments.append(
            {"worker_id": day_workers[idx]["id"], "section": "сборка", "shift": "day"}
        )
        idx += 1
    while idx < n:
        assignments.append(
            {"worker_id": day_workers[idx]["id"], "section": "погрузка", "shift": "day"}
        )
        idx += 1

    storage.save_schedule(date_str, assignments)
    return assignments, None
