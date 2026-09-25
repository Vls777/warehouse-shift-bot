import csv
import io
import logging
import os
import random
from datetime import datetime, date as date_cls, timedelta

import requests
from vk_api import VkApi
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType

import config
import storage
import scheduler
import templates
import keyboards
import image_gen
import tasks

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

vk_session = VkApi(token=config.VK_TOKEN)
vk = vk_session.get_api()
longpoll = VkBotLongPoll(vk_session, config.VK_GROUP_ID)

user_states: dict = {}


def send(peer_id, text, keyboard=None):
    try:
        kwargs = {
            "peer_id": peer_id,
            "message": text,
            "random_id": random.randint(1, 2**31 - 1),
        }
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


def send_photo(peer_id, png_bytes, caption=""):
    upload_url = vk.photos.getMessagesUploadServer(peer_id=peer_id)["upload_url"]
    files = {"photo": ("schedule.png", png_bytes, "image/png")}
    resp = requests.post(upload_url, files=files).json()
    saved = vk.photos.saveMessagesPhoto(
        photo=resp["photo"], server=resp["server"], hash=resp["hash"]
    )
    p = saved[0]
    vk.messages.send(
        peer_id=peer_id, message=caption,
        attachment=f"photo{p['owner_id']}_{p['id']}",
        random_id=random.randint(1, 2**31 - 1),
    )


def send_doc(peer_id, data: bytes, filename: str):
    upload_url = vk.docs.getMessagesUploadServer(peer_id=peer_id, type="doc")["upload_url"]
    files = {"file": (filename, data, "application/octet-stream")}
    up = requests.post(upload_url, files=files).json()
    saved = vk.docs.save(file=up["file"], title=filename)
    doc = saved["doc"]
    vk.messages.send(
        peer_id=peer_id, message=f"📎 {filename}",
        attachment=f"doc{doc['owner_id']}_{doc['id']}",
        random_id=random.randint(1, 2**31 - 1),
    )


def get_role(user_id: int):
    if user_id in config.VK_ADMIN_IDS:
        return "admin"
    if user_id in config.VK_MANAGER_IDS:
        return "manager"
    w = storage.get_worker_by_vk(user_id)
    if w:
        return "worker"
    return "guest"


def parse_date(s: str):
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def reset_state(peer_id):
    user_states.pop(peer_id, None)


def ctx():
    return {
        "send": send,
        "send_long": send_long,
        "send_photo": send_photo,
        "send_doc": send_doc,
    }


def action_today(peer_id):
    d = date_cls.today()
    if not scheduler.is_working_day(d):
        return send(peer_id, f"📅 {d.strftime('%d.%m.%Y')} — выходной", keyboards.main_menu())
    rows = storage.get_schedule(d.isoformat())
    if not rows:
        _, err = scheduler.generate_for_date(d.isoformat())
        if err:
            return send(peer_id, f"⚠️ {err}", keyboards.main_menu())
        rows = storage.get_schedule(d.isoformat())
    ws = scheduler.week_start(d).isoformat()
    ww = storage.get_week_assignment_worker(ws)
    send_long(peer_id, templates.fmt_schedule(rows, d, templates.day_type_for(d), ww),
              keyboards.main_menu())
    try:
        png = image_gen.render_day(d, rows)
        send_photo(peer_id, png, "")
    except Exception as e:
        logging.warning(f"image: {e}")


def action_week(peer_id):
    today = date_cls.today()
    start = scheduler.week_start(today)
    parts, days_data = [], []
    for i in range(6):
        d = start + timedelta(days=i)
        dt = templates.day_type_for(d)
        if not scheduler.is_working_day(d):
            parts.append(templates.fmt_schedule([], d, dt))
            days_data.append((d, [], dt))
            continue
        _, err = scheduler.generate_for_date(d.isoformat())
        if err:
            parts.append(f"📅 {d.strftime('%d.%m.%Y')}: ⚠️ {err}")
            continue
        rows = storage.get_schedule(d.isoformat())
        ws = scheduler.week_start(d).isoformat()
        ww = storage.get_week_assignment_worker(ws)
        parts.append(templates.fmt_schedule(rows, d, dt, ww))
        days_data.append((d, rows, dt))
    send_long(peer_id, "\n\n".join(parts), keyboards.main_menu())
    try:
        png = image_gen.render_week(start, days_data, scheduler.week_label(today))
        send_photo(peer_id, png, "")
    except Exception as e:
        logging.warning(f"image week: {e}")


def action_second(peer_id):
    today = date_cls.today()
    ws = scheduler.week_start(today)
    w = storage.get_week_assignment_worker(ws.isoformat())
    if not w:
        sid, _ = scheduler.get_second_shift_worker(today.isoformat())
        if sid is None:
            return send(peer_id, "⚠️ Нет работников", keyboards.main_menu())
        w = storage.get_week_assignment_worker(ws.isoformat())

    workers = storage.list_workers()
    others = [x for x in workers if x["id"] != w["id"]]
    others.sort(key=lambda x: (x["last_second_shift"] or "1970-01-01", x["name"]))
    next_name = others[0]["name"] if others else "—"

    wd_list = scheduler.working_days_of_week(today)
    wd_str = ", ".join(x.strftime("%d.%m") for x in wd_list)
    send(peer_id,
         f"🌙 Вторая смена — неделя {scheduler.week_label(today)}\n"
         f"👤 {w['name']}\n"
         f"📆 Рабочие дни: {wd_str}\n\n"
         f"Следующий по ротации: {next_name}",
         keyboards.main_menu())


def action_workers_list(peer_id):
    ws = storage.list_workers()
    if not ws:
        return send(peer_id, "Список пуст", keyboards.workers_menu())
    lines = ["👥 Работники:"]
    for w in ws:
        last = w["last_second_shift"] or "—"
        vk = f"VK:{w['vk_id']}" if w["vk_id"] else "VK:—"
        lines.append(f"  • {w['name']}  ({vk}, 2-я смена: {last})")
    send(peer_id, "\n".join(lines), keyboards.workers_menu())


def action_saturdays_list(peer_id):
    rows = storage.list_working_saturdays()
    lines = ["🟢 Рабочие субботы:"] if rows else ["Рабочих суббот пока нет"]
    for r in rows:
        d = date_cls.fromisoformat(r["date"])
        lines.append(f"  • {d.strftime('%d.%m.%Y (%a)')}")
    send(peer_id, "\n".join(lines), keyboards.saturdays_menu())


def action_journal_today(peer_id):
    d = date_cls.today()
    shifts, absences = storage.journal_for_date(d.isoformat())
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
            lines.append(f"  • {s['name']} — {s['section']}")
    else:
        lines.append("  — никого")
    lines.append("")
    if absences:
        lines.append("🚫 Отсутствовали:")
        for a in absences:
            lines.append(f"  • {a['name']} — {a['reason'] or '—'}")
    else:
        lines.append("🚫 Отсутствовали: никого")
    send_long(peer_id, "\n".join(lines), keyboards.journal_menu())


def action_replan(peer_id):
    today = date_cls.today()
    if not scheduler.is_working_day(today):
        return send(peer_id, "📅 Сегодня выходной", keyboards.more_menu())
    old_rows = storage.get_schedule(today.isoformat())
    _, err = scheduler.generate_for_date(today.isoformat())
    if err:
        return send(peer_id, f"⚠️ {err}", keyboards.more_menu())
    new_rows = storage.get_schedule(today.isoformat())
    diff = templates.compare_schedules(old_rows, new_rows)
    send(peer_id, f"🔄 Пересобрано на {today.strftime('%d.%m.%Y')}:\n\n{diff}", keyboards.more_menu())
    send_long(peer_id, templates.fmt_schedule(new_rows, today, templates.day_type_for(today)),
              keyboards.more_menu())


def handle_state(peer_id, user_id, text):
    st = user_states.get(peer_id)
    if not st:
        return False

    if text.strip() == "❌ Отмена":
        reset_state(peer_id)
        send(peer_id, "❌ Отменено", keyboards.main_menu(get_role(user_id)))
        return True

    state = st["state"]
    data = st.get("data", {})

    if state == "wait_add_name":
        name = text.strip()
        if storage.add_worker(name):
            send(peer_id, f"✅ Добавлен: {name}", keyboards.workers_menu())
        else:
            send(peer_id, f"⚠️ {name} уже в списке", keyboards.workers_menu())
        reset_state(peer_id); return True

    if state == "wait_remove_name":
        name = text.strip()
        reset_state(peer_id)
        send(peer_id, f"Удалить {name}?", keyboards.confirm_inline("remove_worker", name))
        return True

    if state == "wait_history_name":
        name = text.strip()
        shifts, absences = storage.history_worker(name, 30)
        if shifts is None:
            send(peer_id, f"⚠️ {name} не найден", keyboards.workers_menu())
            reset_state(peer_id); return True
        lines = [f"📖 История: {name} (30 дней)", ""]
        if shifts:
            lines.append("Работал:")
            for s in shifts:
                tag = "🌙" if s["shift"] == "second" else "☀️"
                lines.append(f"  • {s['date']} {tag} {s['section']}")
        else:
            lines.append("Работал: —")
        lines.append("")
        if absences:
            lines.append("Отсутствовал:")
            for a in absences:
                lines.append(f"  • {a['date']} — {a['reason'] or 'без причины'}")
        else:
            lines.append("Отсутствовал: —")
        send_long(peer_id, "\n".join(lines), keyboards.workers_menu())
        reset_state(peer_id); return True

    if state == "wait_bind_name":
        data["name"] = text.strip()
        st["data"] = data
        st["state"] = "wait_bind_vk"
        return send(peer_id, f"Введите VK ID для {data['name']}:", keyboards.cancel_menu())

    if state == "wait_bind_vk":
        try:
            vk_id = int(text.strip())
        except ValueError:
            return send(peer_id, "❌ VK ID — это число", keyboards.cancel_menu())
        name = data.get("name", "")
        if storage.bind_vk(name, vk_id):
            send(peer_id, f"✅ {name} → VK:{vk_id}", keyboards.workers_menu())
        else:
            send(peer_id, f"⚠️ {name} не найден", keyboards.workers_menu())
        reset_state(peer_id); return True

    if state == "wait_unbind_name":
        name = text.strip()
        if storage.unbind_vk(name):
            send(peer_id, f"🔓 {name} отвязан", keyboards.workers_menu())
        else:
            send(peer_id, f"⚠️ {name} не найден", keyboards.workers_menu())
        reset_state(peer_id); return True

    if state == "wait_sick_name":
        name = text.strip()
        today = date_cls.today()
        if not scheduler.is_working_day(today):
            send(peer_id, "Сегодня выходной", keyboards.main_menu(get_role(user_id)))
            reset_state(peer_id); return True
        old_rows = storage.get_schedule(today.isoformat())
        if not storage.add_absence(name, today.isoformat(), "заболел"):
            send(peer_id, f"⚠️ {name} не найден", keyboards.main_menu(get_role(user_id)))
            reset_state(peer_id); return True
        _, err = scheduler.generate_for_date(today.isoformat())
        if err:
            send(peer_id, f"⚠️ {err}", keyboards.main_menu(get_role(user_id)))
            reset_state(peer_id); return True
        new_rows = storage.get_schedule(today.isoformat())
        diff = templates.compare_schedules(old_rows, new_rows)
        send(peer_id, f"🚫 {name} — заболел\n🔄 Изменения:\n{diff}",
             keyboards.main_menu(get_role(user_id)))
        reset_state(peer_id); return True

    if state == "wait_back_name":
        name = text.strip()
        today = date_cls.today()
        if not storage.remove_absence(name, today.isoformat()):
            send(peer_id, f"⚠️ {name} не отмечен отсутствующим",
                 keyboards.main_menu(get_role(user_id)))
            reset_state(peer_id); return True
        old_rows = storage.get_schedule(today.isoformat())
        _, err = scheduler.generate_for_date(today.isoformat())
        if err:
            send(peer_id, f"⚠️ {err}", keyboards.main_menu(get_role(user_id)))
            reset_state(peer_id); return True
        new_rows = storage.get_schedule(today.isoformat())
        diff = templates.compare_schedules(old_rows, new_rows)
        send(peer_id, f"✅ {name} вернулся\n🔄 Изменения:\n{diff}",
             keyboards.main_menu(get_role(user_id)))
        reset_state(peer_id); return True

    if state in ("wait_workday_date", "wait_dayoff_date"):
        d = parse_date(text)
        if not d:
            return send(peer_id, "❌ Формат: ДД.ММ.ГГГГ", keyboards.cancel_menu())
        if d.weekday() != 5:
            send(peer_id, "⚠️ Только суббота", keyboards.saturdays_menu())
            reset_state(peer_id); return True
        working = state == "wait_workday_date"
        storage.set_saturday_working(d.isoformat(), working)
        send(peer_id, f"{'🟢' if working else '🚫'} Суббота {d.strftime('%d.%m.%Y')}",
             keyboards.saturdays_menu())
        reset_state(peer_id); return True

    if state == "wait_holiday_add":
        parts = text.split(";", 1)
        d = parse_date(parts[0])
        if not d:
            return send(peer_id, "❌ Формат: ДД.ММ.ГГГГ;Название", keyboards.cancel_menu())
        title = parts[1].strip() if len(parts) > 1 else ""
        storage.add_holiday(d.isoformat(), title)
        send(peer_id, f"🎉 Праздник {d.strftime('%d.%m.%Y')} {title}", keyboards.saturdays_menu())
        reset_state(peer_id); return True

    if state == "wait_holiday_remove":
        d = parse_date(text)
        if not d:
            return send(peer_id, "❌ Формат: ДД.ММ.ГГГГ", keyboards.cancel_menu())
        if storage.remove_holiday(d.isoformat()):
            send(peer_id, f"✅ Праздник {d.strftime('%d.%m.%Y')} удалён", keyboards.saturdays_menu())
        else:
            send(peer_id, "Не найдено", keyboards.saturdays_menu())
        reset_state(peer_id); return True

    if state == "wait_show_date":
        d = parse_date(text)
        if not d:
            return send(peer_id, "❌ Формат: ДД.ММ.ГГГГ", keyboards.cancel_menu())
        rows = storage.get_schedule(d.isoformat())
        if not rows and scheduler.is_working_day(d):
            _, err = scheduler.generate_for_date(d.isoformat())
            if err:
                send(peer_id, f"⚠️ {err}", keyboards.more_menu())
                reset_state(peer_id); return True
            rows = storage.get_schedule(d.isoformat())
        ws = scheduler.week_start(d).isoformat()
        ww = storage.get_week_assignment_worker(ws)
        send_long(peer_id, templates.fmt_schedule(rows, d, templates.day_type_for(d), ww),
                  keyboards.more_menu())
        reset_state(peer_id); return True

    if state == "wait_clear_date":
        d = parse_date(text)
        if not d:
            return send(peer_id, "❌ Формат: ДД.ММ.ГГГГ", keyboards.cancel_menu())
        reset_state(peer_id)
        send(peer_id, f"Удалить расписание на {d.strftime('%d.%m.%Y')}?",
             keyboards.confirm_inline("clear_schedule", d.isoformat()))
        return True

    if state == "wait_absence_name":
        data["name"] = text.strip(); st["data"] = data; st["state"] = "wait_absence_date"
        return send(peer_id, f"Дата для {data['name']} (ДД.ММ.ГГГГ):", keyboards.cancel_menu())

    if state == "wait_absence_date":
        d = parse_date(text)
        if not d:
            return send(peer_id, "❌ Формат: ДД.ММ.ГГГГ", keyboards.cancel_menu())
        data["date"] = d.isoformat(); st["data"] = data; st["state"] = "wait_absence_reason"
        return send(peer_id, "Причина (или «-»):", keyboards.cancel_menu())

    if state == "wait_absence_reason":
        reason = "" if text.strip() == "-" else text.strip()
        if storage.add_absence(data.get("name", ""), data.get("date", ""), reason):
            d = date_cls.fromisoformat(data["date"])
            send(peer_id, f"🚫 {data['name']} → {d.strftime('%d.%m.%Y')}", keyboards.more_menu())
        else:
            send(peer_id, "⚠️ Работник не найден", keyboards.more_menu())
        reset_state(peer_id); return True

    if state == "wait_second_set_name":
        data["name"] = text.strip(); st["data"] = data; st["state"] = "wait_second_set_date"
        return send(peer_id, "Дата из нужной недели:", keyboards.cancel_menu())

    if state == "wait_second_set_date":
        d = parse_date(text)
        if not d:
            return send(peer_id, "❌ Формат: ДД.ММ.ГГГГ", keyboards.cancel_menu())
        w = storage.get_worker_by_name(data.get("name", ""))
        if not w:
            send(peer_id, "⚠️ Работник не найден", keyboards.more_menu())
            reset_state(peer_id); return True
        ws = scheduler.week_start(d).isoformat()
        storage.set_week_assignment(ws, w["id"])
        storage.update_last_second_shift(w["id"], ws)
        send(peer_id, f"✅ {w['name']} — 2-я смена на {scheduler.week_label(d)}",
             keyboards.more_menu())
        reset_state(peer_id); return True

    if state == "wait_second_reset_date":
        d = parse_date(text)
        if not d:
            return send(peer_id, "❌ Формат: ДД.ММ.ГГГГ", keyboards.cancel_menu())
        ws = scheduler.week_start(d).isoformat()
        if storage.clear_week_assignment(ws):
            send(peer_id, f"♻️ Назначение на {scheduler.week_label(d)} сброшено", keyboards.more_menu())
        else:
            send(peer_id, "Нечего сбрасывать", keyboards.more_menu())
        reset_state(peer_id); return True

    if state == "wait_journal_date":
        d = parse_date(text)
        if not d:
            return send(peer_id, "❌ Формат: ДД.ММ.ГГГГ", keyboards.cancel_menu())
        shifts, absences = storage.journal_for_date(d.isoformat())
        lines = [f"📖 Журнал за {d.strftime('%d.%m.%Y')}", ""]
        day = [s for s in shifts if s["shift"] == "day"]
        night = [s for s in shifts if s["shift"] == "second"]
        lines.append("☀️ Дневная:")
        lines += [f"  • {s['name']} — {s['section']}" for s in day] or ["  — никого"]
        lines.append("")
        lines.append("🌙 Вторая смена:")
        lines += [f"  • {s['name']} — {s['section']}" for s in night] or ["  — никого"]
        lines.append("")
        if absences:
            lines.append("🚫 Отсутствовали:")
            lines += [f"  • {a['name']} — {a['reason'] or '—'}" for a in absences]
        else:
            lines.append("🚫 Отсутствовали: никого")
        send_long(peer_id, "\n".join(lines), keyboards.journal_menu())
        reset_state(peer_id); return True

    if state == "wait_period_start":
        d = parse_date(text)
        if not d:
            return send(peer_id, "❌ Формат: ДД.ММ.ГГГГ", keyboards.cancel_menu())
        data["start"] = d.isoformat(); st["data"] = data; st["state"] = "wait_period_end"
        return send(peer_id, "Дата окончания:", keyboards.cancel_menu())

    if state == "wait_period_end":
        d = parse_date(text)
        if not d:
            return send(peer_id, "❌ Формат: ДД.ММ.ГГГГ", keyboards.cancel_menu())
        start = data["start"]; end = d.isoformat()
        spw, absences, sbs, night, missed = storage.period_report(start, end)
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
        if night:
            lines.append("")
            lines.append("🌙 Чаще всех на 2-й смене:")
            for r in night[:5]:
                lines.append(f"  • {r['name']} — {r['cnt']}")
        if missed:
            lines.append("")
            lines.append("🚫 Больше всех пропусков:")
            for r in missed[:5]:
                lines.append(f"  • {r['name']} — {r['cnt']}")
        if not any([spw, sbs, night, missed]):
            lines.append("Нет данных за период")
        send_long(peer_id, "\n".join(lines), keyboards.journal_menu())
        reset_state(peer_id); return True

    if state == "wait_sicklist_date":
        d = parse_date(text)
        if not d:
            return send(peer_id, "❌ Формат: ДД.ММ.ГГГГ", keyboards.cancel_menu())
        rows = storage.sicklist_since(d.isoformat())
        if not rows:
            send(peer_id, f"✅ Нет отсутствий с {d.strftime('%d.%m.%Y')}", keyboards.journal_menu())
            reset_state(peer_id); return True
        lines = [f"🤒 Отсутствия с {d.strftime('%d.%m.%Y')}:"]
        for r in rows:
            lines.append(f"  • {r['date']} — {r['name']} ({r['reason'] or '—'})")
        send_long(peer_id, "\n".join(lines), keyboards.journal_menu())
        reset_state(peer_id); return True

    if state == "wait_export_start":
        d = parse_date(text)
        if not d:
            return send(peer_id, "❌ Формат: ДД.ММ.ГГГГ", keyboards.cancel_menu())
        data["start"] = d.isoformat(); st["data"] = data; st["state"] = "wait_export_end"
        return send(peer_id, "Дата окончания:", keyboards.cancel_menu())

    if state == "wait_export_end":
        d = parse_date(text)
        if not d:
            return send(peer_id, "❌ Формат: ДД.ММ.ГГГГ", keyboards.cancel_menu())
        start = data["start"]; end = d.isoformat()
        rows = storage.export_rows(start, end)
        if not rows:
            send(peer_id, "Нечего экспортировать", keyboards.journal_menu())
            reset_state(peer_id); return True
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["Дата", "Работник", "Участок", "Смена", "Отсутствие"])
        for r in rows:
            w.writerow([
                r["date"], r["name"], r["section"],
                "день" if r["shift"] == "day" else "2-я смена",
                r["absent_reason"] or "",
            ])
        try:
            send_doc(peer_id, buf.getvalue().encode("utf-8-sig"),
                     f"journal_{start}_{end}.csv")
            send(peer_id, "Готово", keyboards.journal_menu())
        except Exception as e:
            logging.exception("export")
            send(peer_id, f"⚠️ {e}", keyboards.journal_menu())
        reset_state(peer_id); return True

    if state == "wait_broadcast_add":
        try:
            pid = int(text.strip())
        except ValueError:
            return send(peer_id, "❌ peer_id — число", keyboards.cancel_menu())
        storage.add_broadcast_target(pid)
        send(peer_id, f"✅ Чат {pid} добавлен в рассылку", keyboards.broadcast_menu())
        reset_state(peer_id); return True

    if state == "wait_broadcast_remove":
        try:
            pid = int(text.strip())
        except ValueError:
            return send(peer_id, "❌ peer_id — число", keyboards.cancel_menu())
        if storage.remove_broadcast_target(pid):
            send(peer_id, f"🗑 Чат {pid} удалён", keyboards.broadcast_menu())
        else:
            send(peer_id, "Не найден", keyboards.broadcast_menu())
        reset_state(peer_id); return True

    if state == "worker_sick_confirm":
        if text.strip().lower() in ("да", "yes", "y", "✅"):
            w = storage.get_worker_by_vk(user_id)
            today = date_cls.today()
            if storage.add_absence(w["name"], today.isoformat(), "заболел"):
                scheduler.generate_for_date(today.isoformat())
                send(peer_id, f"🚫 Отметил: {w['name']} отсутствует сегодня",
                     keyboards.worker_menu())
            else:
                send(peer_id, "⚠️ Ошибка", keyboards.worker_menu())
        else:
            send(peer_id, "Отменено", keyboards.worker_menu())
        reset_state(peer_id); return True

    return False


def handle_button(peer_id, user_id, text):
    role = get_role(user_id)
    t = text.strip()
    main_kb = keyboards.main_menu(role)

    if t == "📅 Сегодня":
        action_today(peer_id); return True
    if t == "📅 Неделя":
        action_week(peer_id); return True
    if t == "🌙 2-я смена":
        action_second(peer_id); return True
    if t == "👥 Работники":
        if role not in ("admin", "manager"): return False
        send(peer_id, "👥 Работники", keyboards.workers_menu()); return True
    if t == "🤒 Заболел":
        if role not in ("admin", "manager"): return False
        user_states[peer_id] = {"state": "wait_sick_name", "data": {}}
        send(peer_id, "Кто заболел? Введите имя:", keyboards.cancel_menu()); return True
    if t == "✅ Вернулся":
        if role not in ("admin", "manager"): return False
        user_states[peer_id] = {"state": "wait_back_name", "data": {}}
        send(peer_id, "Кто вернулся? Введите имя:", keyboards.cancel_menu()); return True
    if t == "📆 Субботы":
        send(peer_id, "📆 Субботы", keyboards.saturdays_menu()); return True
    if t == "📖 Журнал":
        send(peer_id, "📖 Журнал", keyboards.journal_menu()); return True
    if t == "⚙️ Ещё":
        if role != "admin": return False
        send(peer_id, "⚙️ Дополнительно", keyboards.more_menu()); return True
    if t == "❓ Помощь":
        send(peer_id, templates.HELP_TEXT, main_kb); return True

    if t == "➕ Добавить":
        user_states[peer_id] = {"state": "wait_add_name", "data": {}}
        send(peer_id, "Имя нового работника:", keyboards.cancel_menu()); return True
    if t == "🗑 Удалить":
        user_states[peer_id] = {"state": "wait_remove_name", "data": {}}
        send(peer_id, "Имя для удаления:", keyboards.cancel_menu()); return True
    if t == "📋 Список":
        action_workers_list(peer_id); return True
    if t == "📊 История":
        user_states[peer_id] = {"state": "wait_history_name", "data": {}}
        send(peer_id, "Имя работника:", keyboards.cancel_menu()); return True
    if t == "🔗 Привязать VK":
        user_states[peer_id] = {"state": "wait_bind_name", "data": {}}
        send(peer_id, "Имя работника:", keyboards.cancel_menu()); return True
    if t == "🔓 Отвязать VK":
        user_states[peer_id] = {"state": "wait_unbind_name", "data": {}}
        send(peer_id, "Имя работника:", keyboards.cancel_menu()); return True

    if t == "➕ Суббота рабочая":
        user_states[peer_id] = {"state": "wait_workday_date", "data": {}}
        send(peer_id, "Дата субботы (ДД.ММ.ГГГГ):", keyboards.cancel_menu()); return True
    if t == "➖ Суббота выходная":
        user_states[peer_id] = {"state": "wait_dayoff_date", "data": {}}
        send(peer_id, "Дата субботы:", keyboards.cancel_menu()); return True
    if t == "📋 Список суббот":
        action_saturdays_list(peer_id); return True
    if t == "🎉 Праздник добавить":
        user_states[peer_id] = {"state": "wait_holiday_add", "data": {}}
        send(peer_id, "Формат: ДД.ММ.ГГГГ;Название", keyboards.cancel_menu()); return True
    if t == "🎉 Праздник удалить":
        user_states[peer_id] = {"state": "wait_holiday_remove", "data": {}}
        send(peer_id, "Дата праздника:", keyboards.cancel_menu()); return True

    if t == "📖 Журнал сегодня":
        action_journal_today(peer_id); return True
    if t == "📅 Журнал за дату":
        user_states[peer_id] = {"state": "wait_journal_date", "data": {}}
        send(peer_id, "Дата (ДД.ММ.ГГГГ):", keyboards.cancel_menu()); return True
    if t == "📊 Аналитика":
        user_states[peer_id] = {"state": "wait_period_start", "data": {}}
        send(peer_id, "Дата начала периода:", keyboards.cancel_menu()); return True
    if t == "🤒 Отсутствия":
        user_states[peer_id] = {"state": "wait_sicklist_date", "data": {}}
        send(peer_id, "Показать с даты:", keyboards.cancel_menu()); return True
    if t == "📎 Экспорт CSV":
        user_states[peer_id] = {"state": "wait_export_start", "data": {}}
        send(peer_id, "Начало периода:", keyboards.cancel_menu()); return True

    if t == "📅 На дату":
        user_states[peer_id] = {"state": "wait_show_date", "data": {}}
        send(peer_id, "Дата (ДД.ММ.ГГГГ):", keyboards.cancel_menu()); return True
    if t == "📆 Отметить отсутствие":
        user_states[peer_id] = {"state": "wait_absence_name", "data": {}}
        send(peer_id, "Имя работника:", keyboards.cancel_menu()); return True
    if t == "🌙 Назначить 2-ю":
        user_states[peer_id] = {"state": "wait_second_set_name", "data": {}}
        send(peer_id, "Имя работника:", keyboards.cancel_menu()); return True
    if t == "♻️ Сбросить 2-ю":
        user_states[peer_id] = {"state": "wait_second_reset_date", "data": {}}
        send(peer_id, "Дата недели:", keyboards.cancel_menu()); return True
    if t == "🔧 Пересобрать":
        action_replan(peer_id); return True
    if t == "🗑 Удалить расписание":
        user_states[peer_id] = {"state": "wait_clear_date", "data": {}}
        send(peer_id, "Дата:", keyboards.cancel_menu()); return True
    if t == "🔔 Рассылки":
        send(peer_id, "🔔 Рассылки", keyboards.broadcast_menu()); return True
    if t == "💾 Бэкап сейчас":
        try:
            with open(config.DB_PATH, "rb") as f:
                send_doc(peer_id, f.read(),
                         f"backup_{date_cls.today().isoformat()}.db")
            send(peer_id, "✅ Бэкап отправлен", keyboards.more_menu())
        except Exception as e:
            send(peer_id, f"⚠️ {e}", keyboards.more_menu())
        return True

    if t == "➕ Добавить чат":
        user_states[peer_id] = {"state": "wait_broadcast_add", "data": {}}
        send(peer_id, "peer_id чата (для беседы обычно 2000000001):", keyboards.cancel_menu())
        return True
    if t == "🗑 Удалить чат":
        user_states[peer_id] = {"state": "wait_broadcast_remove", "data": {}}
        send(peer_id, "peer_id для удаления:", keyboards.cancel_menu()); return True
    if t == "📋 Список чатов":
        pids = storage.list_broadcast_targets()
        txt = "📋 Чаты рассылки:\n" + ("\n".join(f"  • {p}" for p in pids) if pids else "  — пусто")
        send(peer_id, txt, keyboards.broadcast_menu()); return True

    if t == "📅 Моё расписание":
        w = storage.get_worker_by_vk(user_id)
        if not w: return False
        today = date_cls.today()
        ws = scheduler.week_start(today)
        lines = [f"👤 {w['name']} — неделя {scheduler.week_label(today)}", ""]
        for i in range(6):
            d = ws + timedelta(days=i)
            if not scheduler.is_working_day(d):
                continue
            rows = storage.get_schedule(d.isoformat())
            my = [r for r in rows if r["name"] == w["name"]]
            if my:
                sec, sh = my[0]["section"], my[0]["shift"]
                tag = "🌙" if sh == "second" else "☀️"
                lines.append(f"  {d.strftime('%a %d.%m')}: {tag} {sec}")
            else:
                lines.append(f"  {d.strftime('%a %d.%m')}: выходной")
        send(peer_id, "\n".join(lines), keyboards.worker_menu())
        return True
    if t == "🤒 Я заболел":
        w = storage.get_worker_by_vk(user_id)
        if not w: return False
        user_states[peer_id] = {"state": "worker_sick_confirm", "data": {}}
        send(peer_id, f"Отметить {w['name']} отсутствующим сегодня? Напишите «да»",
             keyboards.cancel_menu())
        return True
    if t == "✅ Я вернулся":
        w = storage.get_worker_by_vk(user_id)
        if not w: return False
        today = date_cls.today()
        if storage.remove_absence(w["name"], today.isoformat()):
            scheduler.generate_for_date(today.isoformat())
            send(peer_id, f"✅ {w['name']} снова в строю", keyboards.worker_menu())
        else:
            send(peer_id, "Ты не был отмечен отсутствующим", keyboards.worker_menu())
        return True
    if t == "ℹ️ Профиль":
        w = storage.get_worker_by_vk(user_id)
        if not w: return False
        send(peer_id, f"👤 {w['name']}\nVK ID: {w['vk_id']}\nПоследняя 2-я смена: {w['last_second_shift'] or '—'}",
             keyboards.worker_menu())
        return True

    if t == "⬅️ Назад":
        send(peer_id, "Главное меню", main_kb); return True

    return False


def handle_inline(event):
    payload = event.object.payload
    user_id = event.object.user_id
    peer_id = event.object.peer_id

    try:
        vk.messages.sendMessageEventAnswer(
            event_id=event.object.event_id,
            user_id=user_id,
            peer_id=peer_id,
        )
    except Exception:
        pass

    cmd = payload.get("cmd")
    if cmd == "cancel_inline":
        send(peer_id, "Отменено", keyboards.main_menu(get_role(user_id)))
        return

    if cmd == "pick":
        prefix = payload.get("prefix")
        name = payload.get("name")
        if prefix == "second_set":
            user_states[peer_id] = {"state": "wait_second_set_date", "data": {"name": name}}
            send(peer_id, f"Дата из недели для {name}:", keyboards.cancel_menu())
        return

    if cmd == "confirm":
        action = payload.get("action")
        extra = payload.get("extra", "")
        if action == "remove_worker":
            if storage.remove_worker(extra):
                send(peer_id, f"🗑 Удалён: {extra}", keyboards.workers_menu())
            else:
                send(peer_id, "⚠️ Не найден", keyboards.workers_menu())
        elif action == "clear_schedule":
            if storage.clear_schedule(extra):
                send(peer_id, f"🗑 Удалено расписание на {extra}", keyboards.more_menu())
            else:
                send(peer_id, "Нечего удалять", keyboards.more_menu())


def main():
    storage.init_db()
    logging.info("VK bot starting…")
    tasks.start_scheduler(ctx())

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
                    send(peer_id,
                         "⛔ Нет доступа. Попроси админа привязать твой VK ID к профилю работника.")
                elif role == "worker":
                    send(peer_id, templates.WORKER_HELP, keyboards.worker_menu())
                else:
                    send(peer_id, templates.HELP_TEXT, keyboards.main_menu(role))
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
                    send(peer_id, "🤔 Выбери пункт меню", keyboards.worker_menu())
                else:
                    send(peer_id, "🤔 Выбери пункт меню", keyboards.main_menu(role))
            except Exception as e:
                logging.exception("handle error")
                send(peer_id, f"⚠️ Ошибка: {e}", keyboards.main_menu(role))

        elif event.type == VkBotEventType.MESSAGE_EVENT:
            try:
                handle_inline(event)
            except Exception:
                logging.exception("inline error")


if __name__ == "__main__":
    main()
