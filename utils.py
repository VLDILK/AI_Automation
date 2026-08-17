"""Чисті допоміжні функції без стану: без читання диска/мережі, без імпортів
з settings.py чи main.py. (Де)серіалізація значень для рядків SQLite,
нормалізація тексту для розпізнавання команд користувача, форматування чисел.

Якщо функції потрібно читати файл, ходити в мережу або знати про
Telegram/Tkinter — їй не місце в цьому файлі.
"""

import json
import math
import re
from datetime import date, datetime, time


# SQLite зберігає values_json як звичайний текст, тож datetime/date/time
# треба самим перетворити на щось, що json.dumps вміє записати.
def _serialize_value(value):
    if isinstance(value, datetime):
        return {"__type__": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"__type__": "date", "value": value.isoformat()}
    if isinstance(value, time):
        return {"__type__": "time", "value": value.isoformat()}
    return value


# Зворотне до _serialize_value — відновлює datetime/date/time після зчитування з БД.
def _deserialize_value(value):
    if not isinstance(value, dict) or "__type__" not in value:
        return value

    value_type = value["__type__"]
    raw_value = value["value"]
    if value_type == "datetime":
        return datetime.fromisoformat(raw_value)
    if value_type == "date":
        return date.fromisoformat(raw_value)
    if value_type == "time":
        return time.fromisoformat(raw_value)
    return raw_value


# Пакує весь рядок таблиці (список значень) в один JSON-рядок для стовпця values_json.
def _serialize_row(values):
    return json.dumps([_serialize_value(value) for value in values], ensure_ascii=False)


# Розпаковує рядок таблиці назад зі стовпця values_json.
def _deserialize_row(values_json):
    return [_deserialize_value(value) for value in json.loads(values_json)]


def _display_value(value):
    return "" if value is None else str(value)


# Приводить будь-яку відповідь користувача до єдиного вигляду для порівняння:
# нижній регістр, "ё"->"е", лише літери/цифри без пунктуації.
# Використовується всюди, де бот розпізнає команди/відповіді за змістом слів.
def _normalize_phrase(text):
    text = str(text or "").casefold().replace("ё", "е")
    return " ".join(re.findall(r"[0-9a-zа-яіїєґ]+", text, flags=re.IGNORECASE))


# Якщо людина набрала кириличну команду, забувши перемкнути розкладку клавіатури
# (літери вийшли латиницею), ця функція "перекладає" набір клавіш назад у кирилицю,
# щоб бот все одно розпізнав команду.
def _normalize_keyboard_code(text):
    replacements = str.maketrans(
        {
            "а": "a",
            "в": "b",
            "е": "e",
            "к": "k",
            "м": "m",
            "н": "h",
            "о": "o",
            "р": "p",
            "с": "c",
            "т": "t",
            "у": "y",
            "х": "x",
            "д": "d",
            "і": "i",
        }
    )
    return _normalize_phrase(text).translate(replacements)


# Форматує число для показу в чаті: ціле число без ".0", дробове — з комою
# замість крапки (звичний вигляд для користувача, а не для Python).
def _display_bot_number(value):
    if value is None or value == "":
        return "0"
    # Свіжий пере-аудит (2026-08-02, Notable #9): та сама inf/nan-перевірка,
    # що й у _number_value (тут - для значень, які потрапили НАПРЯМУ, минаючи
    # _number_value) - інакше буквальне англійське "inf"/"nan" могло б
    # потрапити в російськомовне повідомлення бота.
    if isinstance(value, float):
        if not math.isfinite(value):
            return "0"
        if value.is_integer():
            return str(int(value))
    if isinstance(value, int):
        return str(value)
    text = str(value).strip()
    try:
        number = float(text.replace(",", "."))
        if not math.isfinite(number):
            return "0"
        if number.is_integer():
            return str(int(number))
        # Важлива знахідка нового аудиту (28.07.2026, #6): на відміну від
        # reports.py._display_number/pdf_stock_report.py.display_number
        # (те саме число, лише в звіті) - тут не було round(..., 4), тож
        # бот-повідомлення показували довгий float-хвіст (напр.
        # "12,333333333333334" замість "12,3333").
        return str(round(number, 4)).replace(".", ",")
    except ValueError:
        pass
    return text.replace(".", ",")


# Парсить число з довільного тексту користувача (кома або крапка як десятковий
# роздільник); якщо це взагалі не число — повертає 0, а не кидає виняток.
def _number_value(value):
    if value in (None, ""):
        return 0
    # Свіжий пере-аудит (2026-08-02, Notable #9): float("inf")/float("nan")
    # НЕ кидають ValueError - текст "inf"/"nan" (чи вже готовий inf/nan-
    # об'єкт, напр. від переповнення десь-інде) тихо проходив би як "реальне"
    # число. Захист - на ОБОХ гілках (готовий int/float і розпарсений з
    # тексту), не лише на текстовій.
    if isinstance(value, (int, float)):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else 0
    text = str(value).strip().replace(",", ".")
    try:
        parsed = float(text)
    except ValueError:
        return 0
    return parsed if math.isfinite(parsed) else 0


# Реальний баг (аудит коду, 2026-08-14): "ціна за одиницю × вимір,
# округлено до 2 знаків" рахувалась незалежно в 3 місцях (sale_sheet_values
# і antiseptic_sheet_values у warehouse_data.py, _sale_total_amount у
# telegram_dialog_income_sale_parsing.py) - уже один раз розійшлась
# насправді (одне місце округлювало, інше ні - у "Сумма" потрапляли числа
# на кшталт 100.31233000000001). Один спільний хелпер тут (utils.py - той
# самий модуль, що вже імпортують обидва файли), щоб майбутня зміна
# округлення не могла знову забутись в одному з трьох місць.
def _priced_amount(price_per_unit, measure):
    return round(_number_value(price_per_unit) * _number_value(measure), 2)


# "Дата продажи" (ТЗ, розділ 4) — необов'язкове поле, користувач може
# override-нути дату вручну ("Дата: 09.07.2026"), інакше запис іде з
# датою "сьогодні". Кілька поширених форматів, невідомий/зіпсований текст —
# None (виклик сам вирішує, чи підставляти сьогодні замість помилки).
def _parse_date_text(text):
    text = str(text or "").strip()
    if not text:
        return None
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


# Excel-ін'єкція формулою (критична знахідка аудиту 28.07.2026): клієнт чи
# коментар, що починається з "=", "+", "-" чи "@", openpyxl запише як
# ФОРМУЛУ (не просто текст, що виглядає як формула) — Excel виконає її при
# відкритті файлу. Провідний одинарний апостроф змушує openpyxl (і сам
# Excel) трактувати значення як звичайний текст. Тут (не в reports.py) —
# щоб і reports.py (експорт звітів на вимогу), і warehouse_data.py (головне
# Excel-дзеркало, синхронізується після кожної реальної операції) могли
# перевикористати ОДНУ функцію без циклічного імпорту одне від одного.
_FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@")


def _sanitize_excel_value(value):
    if isinstance(value, str) and value and value[0] in _FORMULA_TRIGGER_CHARS:
        return "'" + value
    return value


# Задача користувача (2026-08-08): реконсиляція розбіжності "кількість, шт"
# vs "фізичний вимір" (м3/м2/мп) для рядка складу МАЄ бути доступна в GUI
# (gui.py не залежить від telegram_dialog.py) - без єдиного джерела правди
# ці правила класифікації товару (площинний/безрозмірний/погонний) довелось
# би тримати у двох місцях одразу. Перенесено сюди з
# telegram_dialog_income_sale_parsing.py (`_is_area_based_product`/
# `_is_quantity_only_product`/`_is_linear_meter_size`/`_piece_*`) - той файл
# тепер лише тонкі методи-обгортки над цими самими функціями, поведінка бота
# НЕ змінюється.
_AREA_BASED_PRODUCT_NAMES = {"вагонка"}
_QUANTITY_ONLY_PRODUCT_NAMES = {"осб"}
_LINEAR_METER_SIZES = {(25.0, 50.0), (30.0, 50.0), (50.0, 50.0)}


def is_area_based_product(value):
    return _normalize_phrase(value) in _AREA_BASED_PRODUCT_NAMES


def is_quantity_only_product(value):
    return _normalize_phrase(value) in _QUANTITY_ONLY_PRODUCT_NAMES


def is_linear_meter_size(thickness, width):
    thickness_value = _number_value(thickness)
    width_value = _number_value(width)
    if thickness_value <= 0 or width_value <= 0:
        return False
    return tuple(sorted((thickness_value, width_value))) in _LINEAR_METER_SIZES


# Те саме розгалуження, що вже має telegram_dialog_income_sale_parsing.py
# _row_measure_kind - товарна властивість (площинний товар) перевіряється
# ПЕРШОЮ, потім розмір-специфічна (мп), інакше об'єм за замовчуванням.
def row_measure_kind(product, thickness, width):
    if is_quantity_only_product(product):
        return None
    if is_area_based_product(product):
        return "area"
    if is_linear_meter_size(thickness, width):
        return "linear"
    return "volume"


# Аудит коду (minor, 2026-08-14): формула тут (для measure_kind="volume")
# продубльована окремо в webapp/app.js::antisepticVolumeFor - клієнт
# рахує об'єм антисептирування наживо (без мережевого round-trip при
# кожному натисканні клавіші), тож справжнього спільного коду між
# Python/JS тут не буде. Якщо ЦЯ формула колись зміниться - обов'язково
# перевір і оновити ту саму формулу в app.js.
def piece_measure(thickness, width, length, measure_kind):
    if measure_kind == "area":
        return _number_value(width) / 1000 * _number_value(length) / 1000
    if measure_kind == "linear":
        return _number_value(length) / 1000
    return _number_value(thickness) / 1000 * _number_value(width) / 1000 * _number_value(length) / 1000


# Реальний ризик (аудит коду, 2026-08-14): webapp/app.js тримав РУЧНУ,
# окрему JS-копію _AREA_BASED_PRODUCT_NAMES/_QUANTITY_ONLY_PRODUCT_NAMES/
# _LINEAR_METER_SIZES (для показу м3/м2/мп на екрані підтвердження) - у
# двох мовах/файлах, підтримується вручну. Наступний товар, доданий лише
# по один бік, тихо розсинхронізував би одиниці виміру на екрані
# підтвердження з тим, що бот реально записав. JSON-серіалізовний вигляд
# тут - webapp_server.py (register_context) кладе це в ctx КОЖНОЇ форми,
# JS читає звідти замість власної копії.
def measure_classification_data():
    return {
        "area_based_products": sorted(_AREA_BASED_PRODUCT_NAMES),
        "quantity_only_products": sorted(_QUANTITY_ONLY_PRODUCT_NAMES),
        "linear_meter_sizes": [list(pair) for pair in sorted(_LINEAR_METER_SIZES)],
    }
