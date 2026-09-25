from vk_api.keyboard import VkKeyboard, VkKeyboardColor


def main_menu(role: str = "admin") -> str:
    kb = VkKeyboard(one_time=False)
    kb.add_button("📅 Сегодня", color=VkKeyboardColor.PRIMARY)
    kb.add_button("📅 Неделя", color=VkKeyboardColor.PRIMARY)
    kb.add_line()
    kb.add_button("🌙 2-я смена", color=VkKeyboardColor.SECONDARY)
    kb.add_button("👥 Работники", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    if role in ("admin", "manager"):
        kb.add_button("🤒 Заболел", color=VkKeyboardColor.NEGATIVE)
        kb.add_button("✅ Вернулся", color=VkKeyboardColor.POSITIVE)
        kb.add_line()
    kb.add_button("📆 Субботы", color=VkKeyboardColor.SECONDARY)
    kb.add_button("📖 Журнал", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    if role == "admin":
        kb.add_button("⚙️ Ещё", color=VkKeyboardColor.SECONDARY)
    kb.add_button("❓ Помощь", color=VkKeyboardColor.PRIMARY)
    return kb.get_keyboard()


def workers_menu() -> str:
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


def saturdays_menu() -> str:
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


def journal_menu() -> str:
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


def more_menu() -> str:
    kb = VkKeyboard(one_time=False)
    kb.add_button("📅 На дату", color=VkKeyboardColor.PRIMARY)
    kb.add_button("📆 Отметить отсутствие", color=VkKeyboardColor.NEGATIVE)
    kb.add_line()
    kb.add_button("🌙 Назначить 2-ю", color=VkKeyboardColor.SECONDARY)
    kb.add_button("♻️ Сбросить 2-ю", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("🔧 Пересобрать", color=VkKeyboardColor.SECONDARY)
    kb.add_button("🗑 Удалить расписание", color=VkKeyboardColor.NEGATIVE)
    kb.add_line()
    kb.add_button("🔔 Рассылки", color=VkKeyboardColor.SECONDARY)
    kb.add_button("💾 Бэкап сейчас", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("⬅️ Назад", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def broadcast_menu() -> str:
    kb = VkKeyboard(one_time=False)
    kb.add_button("➕ Добавить чат", color=VkKeyboardColor.POSITIVE)
    kb.add_button("🗑 Удалить чат", color=VkKeyboardColor.NEGATIVE)
    kb.add_line()
    kb.add_button("📋 Список чатов", color=VkKeyboardColor.PRIMARY)
    kb.add_line()
    kb.add_button("⬅️ Назад", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def worker_menu() -> str:
    kb = VkKeyboard(one_time=False)
    kb.add_button("📅 Моё расписание", color=VkKeyboardColor.PRIMARY)
    kb.add_line()
    kb.add_button("🤒 Я заболел", color=VkKeyboardColor.NEGATIVE)
    kb.add_button("✅ Я вернулся", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("ℹ️ Профиль", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def cancel_menu() -> str:
    kb = VkKeyboard(one_time=True)
    kb.add_button("❌ Отмена", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def confirm_inline(action: str, extra: str = "") -> str:
    kb = VkKeyboard(inline=True)
    payload_yes = f'{{"cmd":"confirm","action":"{action}","extra":"{extra}"}}'
    payload_no = '{"cmd":"cancel_inline"}'
    kb.add_callback_button(label="✅ Да", color=VkKeyboardColor.POSITIVE, payload=payload_yes)
    kb.add_callback_button(label="❌ Отмена", color=VkKeyboardColor.NEGATIVE, payload=payload_no)
    return kb.get_keyboard()


def worker_pick_inline(prefix: str, workers: list) -> str:
    kb = VkKeyboard(inline=True)
    for i, w in enumerate(workers):
        payload = f'{{"cmd":"pick","prefix":"{prefix}","name":"{w["name"]}"}}'
        kb.add_callback_button(label=w["name"], color=VkKeyboardColor.SECONDARY, payload=payload)
        if i % 2 == 1 and i != len(workers) - 1:
            kb.add_line()
    return kb.get_keyboard()
