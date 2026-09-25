from datetime import date as date_cls
import scheduler


def fmt_schedule(rows, date_obj: date_cls, day_type: str = "", week_worker=None) -> str:
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
    lines.append("☀️ Дневная смена (09:00–18:00)")
    if day:
        by_sec = {}
        for r in day:
            by_sec.setdefault(r["section"], []).append(r["name"])
        for sec in scheduler.SECTIONS:
            names = by_sec.get(sec, [])
            if names:
                lines.append(f"  • {sec.capitalize()}: {', '.join(names)}")
    else:
        lines.append("  — никого")

    lines.append("")
    lines.append("🌙 Вторая смена (14:00–23:00)")
    if night:
        for r in night:
            lines.append(f"  • {r['name']} — {r['section']}")
    else:
        if week_worker:
            lines.append(f"  🚫 {week_worker['name']} отсутствует — вторая смена не работает")
        else:
            lines.append("  — никого")

    return "\n".join(lines)


def day_type_for(d: date_cls) -> str:
    import storage
    if d.weekday() == 6:
        return "sunday"
    if storage.is_holiday(d.isoformat()):
        return "holiday"
    if d.weekday() == 5:
        return "saturday_on" if storage.is_saturday_working(d.isoformat()) else "saturday_off"
    return ""


def compare_schedules(old_rows, new_rows) -> str:
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
            lines.append(
                f"  🔄 {name}: {SHIFT_RU.get(o[0], o[0])}/{o[1]} → "
                f"{SHIFT_RU.get(n[0], n[0])}/{n[1]}"
            )
    return "\n".join(lines) if lines else "  (без изменений)"


HELP_TEXT = (
    "🏭 Бот расписания смен склада\n\n"
    "Управление через кнопки. Рабочие дни: Пн–Сб (Сб — выходной по умолчанию).\n"
    "Вторая смена: один человек на всю неделю.\n"
    "Если 2-й сменщик заболел — в этот день смена не работает.\n\n"
    "Автоматика:\n"
    "• 07:30 ежедневно — рассылка расписания на сегодня\n"
    "• 20:00 в воскресенье — генерация следующей недели\n"
    "• 23:00 в воскресенье — автобэкап базы\n"
)


WORKER_HELP = (
    "🏭 Привет! Это бот расписания смен.\n\n"
    "Ты можешь:\n"
    "• Посмотреть своё расписание на неделю\n"
    "• Отметить, что заболел\n"
    "• Отменить своё отсутствие\n"
)
