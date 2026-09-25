from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from datetime import date as date_cls


def _font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold
        else "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def render_day(date_obj: date_cls, rows, extra_notes: str = "") -> bytes:
    W = 900
    H = 1100
    bg = (255, 255, 255)
    fg = (30, 30, 30)
    accent = (60, 110, 200)
    night_bg = (40, 40, 70)
    night_fg = (240, 240, 255)

    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)

    d.rectangle([0, 0, W, 130], fill=accent)
    title = "Расписание смен"
    subtitle = date_obj.strftime("%d.%m.%Y (%a)")
    d.text((40, 25), title, font=_font(40, True), fill=(255, 255, 255))
    d.text((40, 80), subtitle, font=_font(28), fill=(230, 230, 255))

    y = 170
    d.text((40, y), "ДНЕВНАЯ СМЕНА  09:00–18:00", font=_font(26, True), fill=fg)
    y += 50

    day_rows = [r for r in rows if r["shift"] == "day"]
    by_sec = {}
    for r in day_rows:
        by_sec.setdefault(r["section"], []).append(r["name"])

    for sec in ["сборка", "приёмка", "отгрузка"]:
        names = by_sec.get(sec, [])
        d.text((40, y), sec.upper(), font=_font(22, True), fill=accent)
        y += 36
        if names:
            text = ", ".join(names)
            while len(text) > 55:
                d.text((60, y), text[:55], font=_font(22), fill=fg)
                text = text[55:]
                y += 32
            if text:
                d.text((60, y), text, font=_font(22), fill=fg)
                y += 32
        else:
            d.text((60, y), "—", font=_font(22), fill=(150, 150, 150))
            y += 32
        y += 10

    y += 20
    night_rows = [r for r in rows if r["shift"] == "second"]
    d.rectangle([30, y, W - 30, y + 160], fill=night_bg)
    d.text((50, y + 20), "ВТОРАЯ СМЕНА  14:00–23:00", font=_font(26, True), fill=night_fg)
    if night_rows:
        r = night_rows[0]
        d.text((50, y + 70), f"{r['name']}", font=_font(32, True), fill=night_fg)
        d.text((50, y + 115), f"участок: {r['section']}", font=_font(22), fill=(200, 200, 230))
    else:
        d.text((50, y + 70), "Вторая смена не работает", font=_font(24), fill=(255, 180, 180))

    if extra_notes:
        d.text((40, H - 60), extra_notes, font=_font(18), fill=(120, 120, 120))

    out = BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def render_week(start_date: date_cls, days_data: list, title_suffix: str = "") -> bytes:
    W = 1000
    H = 200 + 260 * len(days_data)
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)

    d.rectangle([0, 0, W, 120], fill=(60, 110, 200))
    d.text((40, 30), f"Расписание недели {title_suffix}", font=_font(38, True), fill=(255, 255, 255))

    y = 150
    for date_obj, rows, day_type in days_data:
        d.rectangle([30, y, W - 30, y + 230], outline=(200, 200, 200), width=2)
        d.text((50, y + 12), date_obj.strftime("%d.%m (%a)"), font=_font(28, True), fill=(30, 30, 30))

        if day_type == "sunday":
            d.text((50, y + 70), "Выходной", font=_font(24), fill=(150, 150, 150))
            y += 250
            continue
        if day_type == "saturday_off":
            d.text((50, y + 70), "Суббота — выходной", font=_font(24), fill=(150, 150, 150))
            y += 250
            continue

        day_rows = [r for r in rows if r["shift"] == "day"]
        night_rows = [r for r in rows if r["shift"] == "second"]

        by_sec = {}
        for r in day_rows:
            by_sec.setdefault(r["section"], []).append(r["name"])

        x = 50
        for i, sec in enumerate(["сборка", "приёмка", "отгрузка"]):
            names = ", ".join(by_sec.get(sec, [])) or "—"
            d.text((x, y + 65), sec.upper(), font=_font(18, True), fill=(60, 110, 200))
            yy = y + 92
            text = names
            while text:
                chunk = text[:24]
                text = text[24:]
                d.text((x, yy), chunk, font=_font(18), fill=(30, 30, 30))
                yy += 24
            x += 310

        d.rectangle([50, y + 175, W - 50, y + 210], fill=(40, 40, 70))
        if night_rows:
            d.text((60, y + 180), f"🌙 {night_rows[0]['name']} — {night_rows[0]['section']}",
                   font=_font(18, True), fill=(255, 255, 255))
        else:
            d.text((60, y + 180), "🌙 вторая смена не работает",
                   font=_font(18), fill=(255, 180, 180))
        y += 250

    out = BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()
