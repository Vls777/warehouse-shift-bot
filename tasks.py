import logging
from datetime import date as date_cls, timedelta
from apscheduler.schedulers.background import BackgroundScheduler

import config
import storage
import scheduler
import templates
import image_gen


def job_morning(ctx):
    try:
        today = date_cls.today()
        if not scheduler.is_working_day(today):
            return
        _, err = scheduler.generate_for_date(today.isoformat())
        if err:
            logging.warning(f"morning: {err}")
            return
        rows = storage.get_schedule(today.isoformat())
        dt = templates.day_type_for(today)

        ws = scheduler.week_start(today).isoformat()
        week_worker = storage.get_week_assignment_worker(ws)

        text = templates.fmt_schedule(rows, today, dt, week_worker)
        png = image_gen.render_day(today, rows)

        for peer_id in storage.list_broadcast_targets():
            ctx["send"](peer_id, text)
            try:
                ctx["send_photo"](peer_id, png, f"Расписание на {today.strftime('%d.%m.%Y')}")
            except Exception as e:
                logging.warning(f"photo send error: {e}")
    except Exception:
        logging.exception("morning job error")


def job_sunday_planning(ctx):
    try:
        today = date_cls.today()
        next_monday = scheduler.week_start(today) + timedelta(days=7)
        for i in range(7):
            d = next_monday + timedelta(days=i)
            if scheduler.is_working_day(d):
                scheduler.generate_for_date(d.isoformat())

        text = f"📅 Расписание на неделю {scheduler.week_label(next_monday)}\n\n"
        parts = []
        for i in range(6):
            d = next_monday + timedelta(days=i)
            if not scheduler.is_working_day(d):
                continue
            rows = storage.get_schedule(d.isoformat())
            ws = scheduler.week_start(d).isoformat()
            ww = storage.get_week_assignment_worker(ws)
            parts.append(templates.fmt_schedule(rows, d, templates.day_type_for(d), ww))

        full = text + "\n\n".join(parts)
        for peer_id in storage.list_broadcast_targets():
            ctx["send_long"](peer_id, full)
    except Exception:
        logging.exception("sunday planning error")


def job_backup(ctx):
    try:
        with open(config.DB_PATH, "rb") as f:
            data = f.read()
        filename = f"warehouse_backup_{date_cls.today().isoformat()}.db"
        for admin_id in config.VK_ADMIN_IDS:
            try:
                ctx["send_doc"](admin_id, data, filename)
            except Exception as e:
                logging.warning(f"backup send error to {admin_id}: {e}")
    except Exception:
        logging.exception("backup job error")


def start_scheduler(ctx):
    sched = BackgroundScheduler(timezone=config.TZ_NAME)
    sched.add_job(
        lambda: job_morning(ctx), "cron",
        hour=config.AUTO_MORNING_HOUR, minute=config.AUTO_MORNING_MIN,
    )
    sched.add_job(
        lambda: job_sunday_planning(ctx), "cron",
        day_of_week="sun", hour=config.AUTO_PLANNING_HOUR, minute=0,
    )
    sched.add_job(
        lambda: job_backup(ctx), "cron",
        day_of_week="sun", hour=config.AUTO_BACKUP_HOUR, minute=0,
    )
    sched.start()
    logging.info(f"Scheduler started (TZ={config.TZ_NAME})")
    return sched
