"""Централізований шар перевірки ролей. Єдине місце, де визначено, яка роль
що може робити — весь інший код лише запитує has_permission(role, "...") і
ніколи сам не порівнює рядки ролей. Це навмисно: коли з'явиться 15+ нових
кнопкових флоу (Фаза B), кожен матиме одну й ту саму перевірку з одного
джерела правди, а не власну ad-hoc логіку, яку легко забути десь одну.

Без залежностей від інших файлів проєкту, окрім utils.py (_normalize_phrase).
"""

from utils import _normalize_phrase

ADMIN = "admin"
WAREHOUSE = "warehouse"
SALES = "sales"
ACCOUNTING = "accounting"
# Автоматично призначається новому Telegram-користувачу при першому
# зверненні до бота (warehouse_data.py: ensure_bot_user_seen) — 0 прав,
# поки адміністратор сам не призначить реальну роль у GUI "Персонал".
GUEST = "guest"

ROLES = (ADMIN, WAREHOUSE, SALES, ACCOUNTING, GUEST)

# Назви ролей для показу користувачу (кнопки вибору ролі в GUI, повідомлення).
ROLE_LABELS = {
    ADMIN: "Адміністратор",
    WAREHOUSE: "Склад",
    SALES: "Продажі",
    ACCOUNTING: "Бухгалтерія",
    GUEST: "Гість",
}

# Рос. мовою - показується користувачу БОТА (сповіщення про зміну ролі),
# на відміну від ROLE_LABELS вище (укр., лише GUI "Персонал").
ROLE_LABELS_RU = {
    ADMIN: "Администратор",
    WAREHOUSE: "Склад",
    SALES: "Продажи",
    ACCOUNTING: "Бухгалтерия",
    GUEST: "Гость",
}

# Задача користувача (2026-08-16): клікабельний бейдж ролі в "Персонал" -
# і в gui.py, і в client_app.py - той самий колір ролі має означати те
# саме в обох программах. (bg, fg) фіксовані, незалежні від теми (роль
# лишається впізнаваною однаково світлим і темним) - раніше жив лише
# всередині gui.py, тепер тут як єдине джерело правди для обох.
ROLE_CHIP_COLORS = {
    ADMIN: ("#0F6E56", "#9FE1CB"),
    WAREHOUSE: ("#185FA5", "#B5D4F4"),
    SALES: ("#854F0B", "#FAC775"),
    ACCOUNTING: ("#534AB7", "#CECBF6"),
    GUEST: ("#444441", "#B4B2A9"),
}

# Дії (capabilities), на які можна перевіряти доступ. Розбито "приход" на дію
# створення (sale_create для продажу відповідно) і перегляду (sale_view),
# бо в специфікації ACCOUNTING бачить продажі, але не створює їх, а SALES —
# навпаки, створює й бачить свої, але без бухгалтерських розрізів.
INCOME = "income"
SHIPMENT = "shipment"
WRITEOFF = "writeoff"
SALE_CREATE = "sale_create"
SALE_VIEW = "sale_view"
WAREHOUSE_VIEW = "warehouse_view"
# Аудит коду: REPORTS/EDIT_OPERATIONS/DELETE_OPERATIONS/HISTORY/CLIENTS/
# PAYMENTS/DOCUMENTS нижче (і SHIPMENT вище — знахідка свіжого пере-аудиту
# 2026-08-02, Notable #7, той самий греп повторено й для неї) призначені
# ролям у ROLE_PERMISSIONS, але НЕ перевіряються ЖОДНИМ реальним місцем
# коду сьогодні (перевірено грепом по _require_permission/has_permission у
# всьому telegram_dialog.py) — під кожну з них немає ще жодної функції
# бота. Це НЕ означає діру: без коду, що перевіряв би capability, немає й
# шляху її обійти. SHIPMENT ("отгрузка со склада", надано ролі WAREHOUSE)
# конкретно НЕ реалізовано зараз навмисно — реальна перевірка означала б
# бізнес-рішення "хто саме може оформлювати відвантаження" (чи прив'язати
# stock_sale/Реализация до SHIPMENT замість/на додачу до SALE_CREATE), що
# виходить за межі "закрити знахідку аудиту" й потребує окремого запиту
# користувача про бажаний розподіл ролей. Але якщо колись з'явиться
# реальна функція під одну з цих capability (напр. "Клиенты"/"Платежи"/
# "Документы" як окрема команда бота, чи редагування/видалення вже
# записаної операції через бот, чи саме розмежування SHIPMENT/SALE_CREATE) —
# перший рядок цієї функції МАЄ викликати self._require_permission(store,
# context, perm.<ЦЯ_CAPABILITY>) (той самий патерн, що вже є в
# _stock_balance_reply/_start_income_category_menu/
# _start_sale_payment_method_menu тощо) ще ДО будь-якого читання/запису —
# не покладатись на це "згодом допишемо", саме так двічі раніше в цьому
# проєкті реальні перевірки прав губились (звіт по продажах, /status/
# /sheets/​/first).
REPORTS = "reports"
EDIT_OPERATIONS = "edit_operations"
DELETE_OPERATIONS = "delete_operations"
HISTORY = "history"
CLIENTS = "clients"
PAYMENTS = "payments"
DOCUMENTS = "documents"
# Навмисно НЕ додано в жоден ROLE_PERMISSIONS набір нижче — доступ до
# службових команд /status, /sheets, /first (сирий дамп будь-якого листа,
# включно з ПРОДАЖА МАТЕРИАЛА і СКЛАД) дає лише ADMIN через автоматичний
# бупас у has_permission, без окремого перелічування.
DEBUG_TOOLS = "debug_tools"

CAPABILITY_LABELS = {
    # Показуються напряму в _require_permission/permission_denied_reply -
    # тексті, який бачить реальний користувач Telegram-бота, тож обов'язково
    # чистою російською (єдиний ужиток цього словника, gui.py його не
    # використовує - на відміну від ROLE_LABELS вище, який показується лише
    # в GUI "Персонал" і тому лишається українською).
    INCOME: "приход товара",
    SHIPMENT: "отгрузка со склада",
    WRITEOFF: "списание товара",
    SALE_CREATE: "оформление продажи",
    SALE_VIEW: "просмотр продаж",
    WAREHOUSE_VIEW: "просмотр остатков склада",
    REPORTS: "отчеты",
    EDIT_OPERATIONS: "редактирование операций",
    DELETE_OPERATIONS: "удаление операций",
    HISTORY: "просмотр истории действий",
    CLIENTS: "работа с клиентами",
    PAYMENTS: "оплаты",
    DOCUMENTS: "документы",
    DEBUG_TOOLS: "служебные команды",
}

# ADMIN навмисно не перелічений тут — has_permission() дає йому доступ
# до всього автоматично, щоб "повний доступ" не могло розійтися зі списком
# нижче, якщо пізніше додасться нова дія.
ROLE_PERMISSIONS = {
    WAREHOUSE: {INCOME, SHIPMENT, WRITEOFF, WAREHOUSE_VIEW},
    SALES: {SALE_CREATE, SALE_VIEW, WAREHOUSE_VIEW, CLIENTS},
    ACCOUNTING: {SALE_VIEW, PAYMENTS, DOCUMENTS, REPORTS},
    # Явно порожній набір (не покладаємось на .get(..., frozenset()) нижче) —
    # новачок не має жодних прав, поки адмін не призначить реальну роль.
    GUEST: frozenset(),
}

# Старі/довільні написання ролі (з часів, коли роль була вільним текстом) ->
# канонічна роль. Невідоме значення -> None (доступу нема, поки адмін не
# призначить одну з 4 ролей явно в GUI "Персонал").
_LEGACY_ROLE_ALIASES = {
    "admin": ADMIN,
    "администратор": ADMIN,
    "адміністратор": ADMIN,
    "warehouse": WAREHOUSE,
    "склад": WAREHOUSE,
    "складской": WAREHOUSE,
    "складський": WAREHOUSE,
    "sales": SALES,
    "продажи": SALES,
    "продажі": SALES,
    "accounting": ACCOUNTING,
    "бухгалтерия": ACCOUNTING,
    "бухгалтерія": ACCOUNTING,
    "бухгалтер": ACCOUNTING,
    "guest": GUEST,
    "гість": GUEST,
    "гость": GUEST,
}


def normalize_role(role):
    if not role:
        return None
    return _LEGACY_ROLE_ALIASES.get(_normalize_phrase(role))


def has_permission(role, capability):
    canonical = normalize_role(role)
    if canonical == ADMIN:
        return True
    return capability in ROLE_PERMISSIONS.get(canonical, frozenset())


def permission_denied_reply(capability):
    label = CAPABILITY_LABELS.get(capability, capability)
    return (
        f"У вас нет доступа к этому действию: {label}.\n"
        "Обратитесь к администратору, чтобы изменить роль."
    )
