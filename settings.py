"""Читання/запис двох JSON-файлів налаштувань: system/settings.json
(SettingsStore) і персональний для кожного користувача ПК
display_settings_<user>.json (DisplaySettingsStore) — разом з їхніми
значеннями за замовчуванням і списками допустимих варіантів
(REQUEST_PROCESSING_MODES, EXCEL_SYNC_MODES).

Обидва класи-сховища отримують шлях до файлу параметром конструктора, а не
імпортують константу шляху напряму — тому цей модуль ніяк не залежить
від main.py.
"""

import json
import os
from datetime import datetime
from pathlib import Path

# Значення за замовчуванням для system/settings.json.
DEFAULT_SETTINGS = {
    "telegram_token_file": "",
    "last_file_dialog_dir": "C:\\",
    "request_processing_mode": "no_ai",
    "excel_sync_mode": "after_each_operation",
    "excel_source_mode": "local",
    "excel_local_path": "",
    "excel_online_account": "",
    "excel_online_drive_id": "",
    "excel_online_item_id": "",
    "excel_online_file_name": "",
    "low_stock_threshold": 20,
    # Задача користувача (2026-08-12): "додай... оновлення, кнопку" - шлях
    # до спільного JSON-файлу-маніфесту версій (гілка з версіями gui.py й
    # client_app.py) - порожньо за замовчуванням, перевірка оновлень тоді
    # просто мовчки нічого не знаходить, нічого не ламаючи для тих, хто
    # ще не налаштував спільне розташування (мережева папка/OneDrive).
    "update_manifest_path": "",
    # Задача користувача (2026-08-16): "щоб клієнт вранці отримав оновлення
    # незалежно від того, чи я онлайн" - публікація client_app.py тепер іде
    # через публічний GitHub-реліз (github_releases.py), не спільну теку.
    # Fine-grained PAT, обмежений ОДНИМ репозиторієм (лише Contents:
    # Read/write) - потрібен виключно для ПУБЛІКАЦІЇ (gui.py), клієнт качає
    # готові релізи повністю публічно, без токена. Порожньо за замовчуванням
    # - той самий принцип, що й update_manifest_path вище.
    "github_publish_token": "",
    # Задача користувача (2026-08-13, client_app.py): дві "галочки в
    # квадратах" поруч із "Перезапустить" - увімкнено = програма сама
    # запускає/стежить (бот - автостарт при запуску програми; форма -
    # автостарт через 5с після бота + автовідновлення при збоях), вимкнено
    # = лише вручну кнопками. True за замовчуванням - зберігає поведінку,
    # яка вже є й працює, нічого не змінюючи для тих, хто ще не торкався.
    "client_bot_auto_manage": True,
    "client_webapp_auto_manage": True,
    # Задача користувача (2026-08-13): "всі внесені зміни... зміна теми, чи
    # то перемикання якоїсь кнопки - це все має запам'ятовуватись" - раніше
    # тема (світла/темна) в client_app.py була лише в пам'яті процесу,
    # завжди скидалась на світлу при кожному запуску.
    "client_dark_mode": False,
    # Задача користувача (2026-08-14): "може паролем краще захистити?" -
    # щоденні знімки app_data.sqlite3 (warehouse_data.py, create_db_snapshot)
    # шифруються цим паролем, якщо він заданий. Порожньо за замовчуванням =
    # поведінка не змінюється для тих, хто його не задав (звичайні .sqlite3
    # знімки, як і раніше). Той самий рівень довіри, що й telegram_token_file
    # вище - звичайний локальний файл, без додаткового шифрування самого
    # settings.json.
    "backup_encryption_password": "",
    # Задача користувача (2026-08-15): "тепер змінюй це на автоматичне
    # з'єднання між программами" - раніше тут жили remote_control_status_
    # path/remote_control_token (спільна тека + вручну вставлений ключ).
    # Тепер і адреса (paths.CLOUDFLARED_TUNNEL_HOSTNAME), і ключ (paths.
    # REMOTE_CONTROL_TOKEN) - фіксовані константи, "зашиті" в обидві
    # програми одразу - жодних налаштувань тут більше не потрібно.
}

# Допустимі значення для "excel_sync_mode": коли записувати SQLite в Excel.
# "after_each_operation" — після кожного приходу/продажу (як зараз за замовчуванням).
# "manual" — тільки вручну, кнопкою "Обновити Excel".
EXCEL_SYNC_MODES = {"after_each_operation", "manual"}

# Джерело таблиці Excel (Задача користувача: "можна як локальний так і
# онлайн. потрібно вибрати або або") — взаємовиключне. "local" —
# excel_local_path (порожньо = типовий paths.FILE_PATH). "online" —
# OneDrive/SharePoint через excel_online_drive_id/excel_online_item_id
# (заповнюються після підключення посилання, excel_source.py/
# onedrive_sync.py).
EXCEL_SOURCE_MODES = {"local", "online"}

# Варіанти режиму обробки повідомлень бота: (код, назва для показу, опис).
# Використовується і для валідації значення в settings.json, і для побудови
# списку вибору в налаштуваннях (Tkinter).
REQUEST_PROCESSING_MODES = [
    (
        "no_ai",
        "Без ШИ",
        "Работает без токенов, интернета и нагрузки от ШИ. "
        "Программа понимает только заранее настроенные команды и отвечает коротко по инструкции.",
    ),
    (
        "online_ai",
        "Онлайн ШИ",
        "Сообщения отправляются внешнему ШИ-сервису. Нужен интернет и API-ключ: "
        "расходуются токены, лимиты или деньги того аккаунта, чей ключ подключен.",
    ),
    (
        "local_ai",
        "Локальный ШИ",
        "ШИ запускается на этом ПК. Онлайн-токены не нужны, но расходуются ресурсы ноутбука: "
        "минимум 16 ГБ ОЗУ и процессор от 4 ядер; комфортнее 32 ГБ ОЗУ или видеокарта. "
        "На слабом ПК может заметно тормозить.",
    ),
]

# Значення за замовчуванням для персональних налаштувань показу (формат дати
# в журналі дій тощо) — окремий файл на кожного користувача Windows.
# button_bg_color/button_text_color: єдиний формат відображення кнопок
# ("ред"/"x"/"+" і подібні по всій програмі) — Задача користувача: "зроби
# один формат відображення кнопок, і дай змогу вибрати йому колір і текст
# в налаштуваннях". Фон PNG-картинкою — свідомо НЕ в цьому кроці ("пізніше
# додасиш" — окреме прохання на майбутнє).
# language: мова інтерфейсу GUI (не бота — telegram_dialog.py вже завжди
# російською). Задача користувача: "перейменуй тепер всю програму
# російською мовою із подальшою можливістю додати потім і англійську мову і
# Українську" — переклади зберігаються в i18n.py (TRANSLATIONS[language]),
# тут лише активний вибір.
DEFAULT_DISPLAY_SETTINGS = {
    "date_format": "yyyy.mm.dd_dow_hhmm",
    "button_bg_color": "#f6f8fa",
    "button_text_color": "#1f2328",
    "language": "ru",
    # Задача користувача (2026-08-15): "додай темну тему... на всю прогу...
    # і щоб вибір зберігався" - персональне (як і button_bg_color/language),
    # не спільне для всіх Windows-користувачів цього ПК.
    "dark_mode": False,
    # Оформлення екрана "Проверьте данные" у Mini App (webapp/) — Задача
    # користувача: "хочу мати змогу вибрати колір фону, колір тексту, розмір
    # тексту... який жирний, який ні". Кольори — порожній рядок = "не
    # перевизначати", сторінка й далі бере колір з теми самого Telegram
    # (той самий контракт, що вже діє для button_bg_color/button_text_color
    # НЕ використовується тут — це навмисно РІЗНІ речі: колір кнопок
    # десктоп-програми vs оформлення сторінки в Telegram). Розміри/жирність
    # — типові значення точно повторюють сьогоднішні хардкоджені значення в
    # webapp/style.css, тож "не торкався налаштувань" = "вигляд не змінився".
    "webapp_confirm_heading_text": "Проверьте данные",
    "webapp_title_color": "",
    "webapp_title_size": 20,
    "webapp_title_bold": None,
    "webapp_category_color": "",
    "webapp_category_size": 15,
    "webapp_category_bold": None,
    "webapp_body_color": "",
    "webapp_body_size": 15,
    "webapp_body_bold": None,
    "webapp_common_color": "",
    "webapp_common_size": 14,
    "webapp_common_bold": None,
    "webapp_card_bg_color": "",
    # Задача користувача (2026-08-09): "щоб я окремо міг кожному заголовку
    # [полю форми] міг вибрати колір, товщину, розмір" - на відміну від 4
    # агрегованих груп вище (екран "Проверьте данные"), тут КОЖЕН реальний
    # field.key (порода/товщина/ширина/довжина/кількість/ціна/клієнт/
    # адреса/спосіб оплати/причина/об'єм/категорія) керується окремо. Один
    # словник (не 30+ окремих флет-ключів) - ключ=field.key, значення=
    # {"color","size","bold"}; порожній словник = ні один ще не
    # перевизначений (нуль зміни вигляду форми).
    "webapp_field_label_styles": {},
}


# Зберігає system/settings.json: токен Telegram, режим ШИ, режим синхронізації Excel.
class SettingsStore:
    def __init__(self, settings_path):
        self.settings_path = Path(settings_path)
        self.data = dict(DEFAULT_SETTINGS)
        self.load_error = None
        self.load()

    # Важлива знахідка нового аудиту (28.07.2026, #9): раніше пошкоджений
    # файл (JSONDecodeError/OSError) мовчки замінювався ПОРОЖНІМ dict -
    # оскільки {} теж проходить isinstance-перевірку нижче, КОЖЕН ключ
    # (токен бота, підключення Excel/OneDrive) скидався на дефолт без
    # жодного попередження чи навіть логу. Тепер: зіпсований файл
    # відкладається убік (той самий timestamp-формат, що вже використовує
    # create_db_snapshot) і причина зберігається в self.load_error - GUI
    # (єдина точка створення цих сховищ) показує це один раз користувачу.
    def load(self):
        self.load_error = None
        if not self.settings_path.exists():
            self.save()
            return

        try:
            loaded = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.load_error = str(exc)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            try:
                self.settings_path.replace(
                    self.settings_path.with_name(self.settings_path.name + f".corrupted_{timestamp}")
                )
            except OSError:
                pass
            loaded = {}

        # Реальний баг (2026-08-14): раніше тут було
        # {key: loaded.get(key, value) for key, value in DEFAULT_SETTINGS.items()} -
        # копіювались ЛИШЕ ключі, що вже є в DEFAULT_SETTINGS. Будь-який
        # НОВИЙ ключ, збережений через .set(...) (table_format_*,
        # table_column_widths/filters, excel_source_identity - усі додані
        # ЦІЄЇ сесії) правильно писався у файл, але при НАСТУПНОМУ
        # створенні SettingsStore (перезапуск програми, або просто інший
        # SettingsStore(SETTINGS_PATH), напр. apply_standard_table_format)
        # мовчки губився - .load() відновлював тільки "відомі" ключі.
        # Тепер - звичайне злиття: усе завантажене поверх дефолтів,
        # незалежно від того, чи ключ був заздалегідь "заявлений".
        if isinstance(loaded, dict):
            self.data.update(loaded)

    # Атомарний запис (тимчасовий файл у тій самій директорії + os.replace,
    # атомарно навіть на Windows) - раніше write_text писав напряму в
    # кінцевий файл, тож крах/kill процесу мідсейву лишав би пошкоджений
    # JSON на диску (той самий файл, який load() вище тепер відкладає убік).
    def save(self):
        self.settings_path.parent.mkdir(exist_ok=True)
        tmp_path = self.settings_path.with_suffix(self.settings_path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp_path, self.settings_path)

    def get(self, key):
        return self.data.get(key, DEFAULT_SETTINGS.get(key, ""))

    # Реальний баг (2026-08-17, живий продакшн): "поріг завжди скидується,
    # не зберігається". client_app.py тримає ОДИН довгоживучий SettingsStore
    # (self.settings) на весь час роботи програми, а webapp_server.py
    # (update_low_stock_threshold) створює свій, ОКРЕМИЙ, короткочасний
    # SettingsStore для одного запису - обидва пишуть у ТОЙ САМИЙ файл, але
    # кожен зі своєю копією self.data в пам'яті. Досі set() зберігав УВЕСЬ
    # self.data (не лише змінений ключ) - щойно довгоживучий GUI-екземпляр
    # хоч раз викликав set() для БУДЬ-ЯКОГО іншого ключа (напр. тема,
    # автовключення), він перезаписував файл своєю ЗАСТАРІЛОЮ копією,
    # стираючи поріг, щойно змінений окремим екземпляром із webapp. load()
    # ПЕРЕД зміною підхоплює все, що встигли записати інші SettingsStore-
    # екземпляри з моменту останнього load() цього самого екземпляра -
    # звужує вікно гонки з "назавжди застаріла копія" до "рідкісний
    # мілісекундний збіг", не вимагаючи повного перепроєктування на спільний
    # процес/блокування.
    def set(self, key, value):
        self.load()
        self.data[key] = value
        self.save()


# Зберігає display_settings_<user>.json: суто вигляд інтерфейсу (формат дати),
# нічого критичного для роботи бота — тому окремий клас від SettingsStore.
class DisplaySettingsStore:
    def __init__(self, settings_path):
        self.settings_path = Path(settings_path)
        self.data = dict(DEFAULT_DISPLAY_SETTINGS)
        self.load_error = None
        self.load()

    # Той самий фікс, що й у SettingsStore.load() вище (аудит 28.07.2026, #9).
    def load(self):
        self.load_error = None
        if not self.settings_path.exists():
            return

        try:
            loaded = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.load_error = str(exc)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            try:
                self.settings_path.replace(
                    self.settings_path.with_name(self.settings_path.name + f".corrupted_{timestamp}")
                )
            except OSError:
                pass
            loaded = {}

        # Той самий фікс, що й у SettingsStore.load() вище - злиття всього
        # завантаженого, а не лише вже "відомих" ключів.
        if isinstance(loaded, dict):
            self.data.update(loaded)

    # Той самий атомарний запис, що й у SettingsStore.save() вище.
    def save(self):
        self.settings_path.parent.mkdir(exist_ok=True)
        tmp_path = self.settings_path.with_suffix(self.settings_path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp_path, self.settings_path)

    def get(self, key):
        return self.data.get(key, DEFAULT_DISPLAY_SETTINGS.get(key, ""))

    # Той самий фікс, що й у SettingsStore.set() вище - той самий ризик
    # (кілька екземплярів на той самий файл, кожен зі своєю копією в
    # пам'яті) теоретично можливий і тут.
    def set(self, key, value):
        self.load()
        self.data[key] = value
        self.save()
