import os

VK_TOKEN = os.environ.get("VK_TOKEN", "")
VK_GROUP_ID = int(os.environ.get("VK_GROUP_ID", "0"))


def _parse_ids(s):
    return [int(x.strip()) for x in s.split(",") if x.strip()]


VK_ADMIN_IDS = _parse_ids(os.environ.get("VK_ADMIN_IDS", ""))
VK_MANAGER_IDS = _parse_ids(os.environ.get("VK_MANAGER_IDS", ""))

TZ_NAME = os.environ.get("TZ_NAME", "Europe/Moscow")

AUTO_MORNING_HOUR = int(os.environ.get("AUTO_MORNING_HOUR", "7"))
AUTO_MORNING_MIN = int(os.environ.get("AUTO_MORNING_MIN", "30"))
AUTO_PLANNING_HOUR = int(os.environ.get("AUTO_PLANNING_HOUR", "20"))
AUTO_BACKUP_HOUR = int(os.environ.get("AUTO_BACKUP_HOUR", "23"))

DB_PATH = os.environ.get("DB_PATH", "warehouse.db")

if not VK_TOKEN or not VK_GROUP_ID:
    raise SystemExit("Задайте VK_TOKEN и VK_GROUP_ID")
