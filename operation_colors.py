"""Кольори операцій журналу (Задача користувача, 2026-09-06).

Людина обирає ОДИН колір на операцію (кнопка «Цвета операций» у налаштуваннях
клієнта); усе інше програма виводить сама: для світлої теми - пастельне тло й
темніший текст, для темної - темне тло й світліший текст, і в обох випадках
перевіряє контраст тексту до тла числом (WCAG: не менше 4.5), доводячи текст
до читабельності. Вимога користувача: «видимість тексту та кольору не має
хрватись чи бути занадто яскравою, що аж нечитабельна» - і в Telegram, і в
клієнті.

Ті самі функції для клієнта (вікно журналу, вікно кольорів), бота (контекст
форми: обидва набори, форма обирає за темою Telegram) і домашки (через тунель)."""

import colorsys

OPERATION_TYPES = ("income", "sale", "writeoff", "exchange", "antiseptic", "correction")
OPERATION_LABELS = {
    "income": "Приход", "sale": "Продажа", "writeoff": "Списание", "exchange": "Обмен",
    "antiseptic": "Антисептирование", "correction": "Коррекция",
}
DEFAULT_OPERATION_COLORS = {
    "income": "#0F6E56", "sale": "#8A3A05", "writeoff": "#B42318",
    "exchange": "#534AB7", "antiseptic": "#0E7490", "correction": "#1D4ED8",
}
# Палітра вікна кольорів: спокійні, читабельні відтінки; «Другой…» - будь-який.
PALETTE = ["#0F6E56", "#3EA96E", "#185FA5", "#1D4ED8", "#534AB7", "#9D174D", "#B42318", "#C2410C", "#8A3A05", "#0E7490", "#6B7280", "#374151"]
SETTING_KEY = "operation_colors"
MIN_CONTRAST = 4.5
LIGHT_PAGE = "#FFFFFF"
DARK_PAGE = "#1E2126"


def parse_hex(value):
    text = str(value or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return None
    try:
        return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def to_hex(rgb):
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def mix(rgb_a, rgb_b, weight):
    """weight = частка rgb_b (0 → a, 1 → b)."""
    return tuple(a + (b - a) * weight for a, b in zip(rgb_a, rgb_b))


def _channel(value):
    value = value / 255.0
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def luminance(rgb):
    r, g, b = (_channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(rgb_a, rgb_b):
    la, lb = luminance(rgb_a), luminance(rgb_b)
    light, dark = max(la, lb), min(la, lb)
    return (light + 0.05) / (dark + 0.05)


def _with_lightness(rgb, lightness, saturation=None):
    h, l, s = colorsys.rgb_to_hls(*(c / 255.0 for c in rgb))
    if saturation is not None:
        s = saturation
    r, g, b = colorsys.hls_to_rgb(h, max(0.0, min(1.0, lightness)), s)
    return (r * 255, g * 255, b * 255)


def derive_pair(base, dark=False):
    """(тло, текст) позначки з одного базового кольору для світлої або темної
    теми; текст завжди читабельний на тлі (контраст ≥ MIN_CONTRAST)."""
    rgb = parse_hex(base) or parse_hex(DEFAULT_OPERATION_COLORS["correction"])
    h, l, s = colorsys.rgb_to_hls(*(c / 255.0 for c in rgb))
    s = max(s, 0.35)
    if dark:
        page = parse_hex(DARK_PAGE)
        background = mix(page, _with_lightness(rgb, 0.30, s), 0.55)
        text_lightness = 0.72
        step = 0.04
        direction = 1
    else:
        page = parse_hex(LIGHT_PAGE)
        background = mix(page, _with_lightness(rgb, 0.55, s), 0.22)
        text_lightness = min(l, 0.36)
        step = -0.04
        direction = -1
    text = _with_lightness(rgb, text_lightness, s)
    for _ in range(25):
        if contrast(text, background) >= MIN_CONTRAST:
            break
        text_lightness += step
        if text_lightness <= 0.02 or text_lightness >= 0.98:
            text = (245, 246, 248) if dark else (26, 29, 33)
            break
        text = _with_lightness(rgb, text_lightness, s)
    # Тло не має бути яскравішим за сторінку в темній темі й темнішим за
    # текст у світлій - інакше позначка «вибиває» з таблиці.
    if not dark and contrast(background, page) > 1.6:
        background = mix(page, background, 0.6)
    return to_hex(background), to_hex(text)


def normalize_colors(raw):
    """Налаштування → {type: "#RRGGBB"} лише з правильних значень."""
    result = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key in OPERATION_TYPES and parse_hex(value):
                result[key] = to_hex(parse_hex(value))
    return result


def base_colors(raw=None):
    """Повний набір базових кольорів: типові, поверх них - вибрані людиною."""
    colors = dict(DEFAULT_OPERATION_COLORS)
    colors.update(normalize_colors(raw))
    return colors


def palette_for(raw=None, dark=False):
    """{type: (тло, текст)} для вікон програм, включно з обома боками обміну."""
    result = {}
    for type_key, base in base_colors(raw).items():
        pair = derive_pair(base, dark)
        result[type_key] = pair
        if type_key == "exchange":
            result["exchange_out"] = pair
            result["exchange_in"] = pair
    return result


def palettes_for_form(raw=None):
    """Обидві теми для форми бота: {"light": {type: [bg, fg]}, "dark": {...}};
    форма обирає за темою Telegram."""
    return {
        "light": {key: list(pair) for key, pair in palette_for(raw, False).items()},
        "dark": {key: list(pair) for key, pair in palette_for(raw, True).items()},
    }


def readable(raw=None):
    """Чи всі пари читабельні в обох темах (для проби й для вікна кольорів)."""
    worst = 21.0
    for dark in (False, True):
        for _type, (bg, fg) in palette_for(raw, dark).items():
            worst = min(worst, contrast(parse_hex(bg), parse_hex(fg)))
    return worst >= MIN_CONTRAST, round(worst, 2)
