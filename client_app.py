"""Отдельная, упрощённая программа для клиента (не для разработчика) -
"витрина" поверх тех самых backend-модулей, что уже использует gui.py
(warehouse_data.py, main.py TelegramBotWorker, settings.py, excel_source.py).
Никакая бизнес-логика тут НЕ дублируется и не переписывается - лишь
меньший, другой Tkinter-интерфейс (через CustomTkinter) с ограниченным
набором действий, предназначенный для установки на ноутбук клиента.
Полная админская программа (gui.py) остаётся исключительно у разработчика.

Задача пользователя (2026-08-12): "єдине що хочу обсудити які меню
віддавати, а які залишати в себе" - тут намеренно НЕТ редактора кнопок,
цветов формы, настроек Mini App, прямых инструментов базы и т.п. - лишь то,
что показано на эскизе (Форма вкл/выкл/перезапуск, Настройки, Журналы,
Персонал, Обновить эксели, просмотр копии таблицы, Выход).

Задача користувача (2026-08-12): "застосунок тільки російською" - усі
рядки, що бачить користувач ВСЕРЕДИНІ цієї програми (не коментарі коду) -
чистою російською, без українських/англійських слів (той самий принцип, що
вже діє для самого Telegram-бота).

Це перша, спрощена версія (v1): реальні дії - старт/стоп/перезапуск бота,
підключення форми (Cloudflare Quick Tunnel, той самий watchdog-механізм, що
й gui.py), оновлення з Excel, перегляд копії таблиці, вибір токена й файлу
Excel. "Журналы"/"Персонал" (перенесені з gui.py, той самий лінивий
tk.Toplevel-патерн) також реалізовані - _open_journals_window/
_open_personnel_window нижче, не заглушки.
"""

import atexit
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import ttk
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox

import customtkinter as ctk
from PIL import Image

import paths
import autostart
import watchdog_task
import code_backup
import config_backup
import excel_source
import github_releases
import permissions as perm
import standard_menu_cloud
import update_check
from settings import SettingsStore
from warehouse_data import (
    ExcelSqliteStore,
    CUSTOM_BUTTON_ACTIONS,
    TABLE_FORMAT_COLOR_KEY,
    TABLE_FORMAT_COLUMN_WIDTH_KEY,
    TABLE_FORMAT_COLUMN_WIDTH_MODE_KEY,
    TABLE_FORMAT_DEFAULT_COLOR,
    TABLE_FORMAT_DEFAULT_COLUMN_WIDTH,
    TABLE_FORMAT_DEFAULT_COLUMN_WIDTH_MODE,
    TABLE_FORMAT_DEFAULT_FONT_SIZE,
    TABLE_FORMAT_FONT_SIZE_KEY,
    TABLE_FORMAT_HEADER_FONT_SIZE_KEY,
    TABLE_FORMAT_HEADER_ROW_HEIGHT_KEY,
    apply_standard_table_format,
    ensure_workbook_has_required_sheets,
    create_db_snapshot,
    create_excel_backup,
    list_db_snapshots,
    restore_db_snapshot,
    regenerate_excel_after_restore,
)
import webapp_server
from webapp_server import WebappServer

# Задача користувача: "потрібно ще все інше доробити" (Журналы/Персонал -
# після stub-заглушок) - короткі російські назви днів тижня для форматування
# дати (той самий набір, що й gui.py RU_WEEKDAYS), скопійовані навмисно
# замість імпорту з gui.py (важкий адмінський модуль).
RU_WEEKDAYS = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]

__version__ = "0.2.85"
UPDATE_CHECK_INTERVAL_MS = 5 * 60 * 1000

# Той самий перелік, що й READ_ONLY_SHEETS у gui.py (дубльований навмисно -
# ця програма не повинна імпортувати gui.py, важкий Tkinter-адмінський
# модуль, лише заради однієї константи).
READ_ONLY_SHEETS = {
    "АНАЛИТИКА ПРОДАЖ",
    "АНАЛИТИКА КЛИЕНТОВ",
    "АНАЛИТИКА МЕНЕДЖЕРОВ",
}

# Задача користувача: "роби відразу світлу тему, не надто яскраву... і
# додай змогу на темну перейти" - кожен колір тут кортеж (світла, темна),
# ідіома CustomTkinter: усі fg_color/text_color нижче автоматично
# перемальовуються самі при ctk.set_appearance_mode(...), без ручного
# оновлення кожного віджета в _on_theme_toggle.
COLOR_BG = ("#EDEFF2", "#1A1D21")
COLOR_CARD = ("#F7F8FA", "#25282D")
COLOR_ROW = ("#FFFFFF", "#2C3036")
COLOR_TEXT = ("#20242A", "#E5E7EA")
COLOR_TEXT_MUTED = ("#5B6470", "#9AA1AB")
COLOR_HOVER = ("#E9ECF0", "#33383F")
COLOR_DIVIDER = ("#E3E6EA", "#33373D")
COLOR_BORDER = ("#D8DDE3", "#3A3F46")
COLOR_OFF = ("#B0B4BA", "#5B6470")
COLOR_ON = "#3EA96E"

# Задача користувача (2026-08-16): "зроби нерухомими ролі" - той самий
# фікс, що й у gui.py, той самий Персонал-бейдж - фіксована ширина під
# найдовший підпис (тут - рос. ROLE_LABELS_RU, "Администратор ▾"), щоб
# кнопка "Удалить" поруч не "стрибала" залежно від довжини ролі.
ROLE_CHIP_WIDTH = max(len(f"{label} ▾") for label in perm.ROLE_LABELS_RU.values())
# Задача користувача (2026-08-17): "точно такий же вигляд зроби і в
# клієнті. ролі - стаціонарні кнопци. відвідуваність теж." - той самий
# фіксований відступ під колонку "Час", що вже й gui.py._LAST_SEEN_WIDTH.
LAST_SEEN_WIDTH = 16
COLOR_WARN = "#D9A441"
COLOR_STOP = ("#E7EAEE", "#3A3F46")
COLOR_STOP_TEXT = ("#B23B3B", "#E08080")
COLOR_UPDATE_BLUE = "#2F7BD9"
# Задача користувача: "Выход"... "зроби їй червоний значок відключення" -
# окрема, приглушено-червона картка (не просто рядок меню).
COLOR_DANGER_BG = ("#FBEAEA", "#3A2426")
COLOR_DANGER_HOVER = ("#F5D9D9", "#472B2D")

# Ті самі 5 кольорів, що показані в чаті (mcp__visualize__show_widget,
# 2026-08-14) перед вибором "Формат 5" + синій - готові пресети в екрані
# "Формат таблицы" (_open_table_format_window), плюс сам обраний колір
# лишається довільним hex у settings.json (TABLE_FORMAT_COLOR_KEY), не
# обмеженим лише цими п'ятьма.
TABLE_FORMAT_COLOR_PRESETS = [
    ("Графіт", "1a1a18"),
    ("Синій", "0C447C"),
    ("Зелений", "085041"),
    ("Теракотовий", "712B13"),
    ("Фіолетовий", "3C3489"),
]

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

# Задача користувача: "значки точно такі, 1 в 1" - справжні PNG-іконки
# (Segoe MDL2 Assets, системний шрифт іконок Windows - той самий стиль, що
# й у власних Настройках/Провіднику Windows) замість emoji-тексту: emoji
# рендериться кольоровим/мультяшним у нативному Tkinter, зовсім не так,
# як у прев'ю-макеті. Кожна іконка - пара PNG (світла/темна тема), уже
# згенерована заздалегідь (D:\IT\Python\AI_Automation\icons\generate -
# скрипт-джерело лишився в scratchpad, тут лише готові файли).
ICONS_DIR = paths.BASE_DIR / "icons"
_ICON_CACHE = {}


def _load_icon(name, size=20):
    key = (name, size)
    cached = _ICON_CACHE.get(key)
    if cached is None:
        cached = ctk.CTkImage(
            light_image=Image.open(ICONS_DIR / f"{name}_light.png"),
            dark_image=Image.open(ICONS_DIR / f"{name}_dark.png"),
            size=(size, size),
        )
        _ICON_CACHE[key] = cached
    return cached


# Задача користувача (2026-08-16): "кругова іконка-спінер" (варіант 3 з
# 5 обраних макетів) - обертовий індикатор на кнопці "Загрузка обновления"
# під час завантаження. Tkinter/CTk не має вбудованої CSS-анімації, тож
# спінер - просто N наперед повернутих (PIL .rotate()) кадрів тієї самої
# refresh-іконки, кешованих по кроку, циклічно підставлених у button.
# configure(image=...) на таймері (_advance_update_spinner нижче).
_SPINNER_FRAME_COUNT = 8
_SPINNER_FRAME_MS = 100
_SPINNER_CACHE = {}

# Задача користувача (2026-08-16): "змінимо збереження резервних копій...
# раз в годину" - інтервал автотіку знімків БД (_schedule_db_backup_tick).
_DB_BACKUP_TICK_MS = 3600 * 1000


def _spinner_frame(step, size=14):
    key = (step, size)
    cached = _SPINNER_CACHE.get(key)
    if cached is None:
        angle = -step * (360 / _SPINNER_FRAME_COUNT)
        light = Image.open(ICONS_DIR / "refresh_light.png").rotate(angle, resample=Image.BICUBIC)
        dark = Image.open(ICONS_DIR / "refresh_dark.png").rotate(angle, resample=Image.BICUBIC)
        cached = ctk.CTkImage(light_image=light, dark_image=dark, size=(size, size))
        _SPINNER_CACHE[key] = cached
    return cached



class ClientApp(ctk.CTk):
    _STATUS_POLL_MS = 5000
    _STALE_THRESHOLD_SECONDS = 60
    # Реальний ризик (аудит коду, 2026-08-14): worker.stop() (main.py)
    # чекає щонайбільше ~7с (TELEGRAM_POLL_TIMEOUT+2) - getUpdates
    # всередині поллінг-циклу може легально тривати аж до 15с
    # (TELEGRAM_POLL_TIMEOUT+10), тож потік технічно міг ще НЕ померти,
    # коли worker.stop() уже повернувся. gui.py має той самий висновок
    # (watchdog-тік чекає is_alive()==False на НАСТУПНИХ тіках, не стартує
    # новий одразу) - тут простіше: _stop_bot_async і так уже виконується у
    # ВЛАСНОМУ фоновому потоці, тож можна просто почекати довше тут-таки,
    # перш ніж дозволити виклику "on_stopped" (перезапуск) стартувати
    # ДРУГИЙ TelegramBotWorker, поки перший ще технічно живий.
    _BOT_STOP_EXTRA_WAIT_SECONDS = 15

    # Задача користувача (2026-08-12): "підключай" форму (Cloudflare Quick
    # Tunnel) - той самий, уже перевірений і кілька разів виправлений цієї
    # ж сесії механізм, що й у gui.py (стук 10с на старті, 30с reconnect,
    # 11 невдалих спроб -> 30хв простій -> 20с тест -> 15хв, і так по колу).
    # Навмисно СКОПІЙОВАНО сюди (не імпортовано з gui.py) - gui.py дуже
    # важкий Tkinter-адмінський модуль, а сама ця логіка тісно прив'язана
    # до self.root/self.is_closing тощо, не є окремим reusable модулем.
    _WEBAPP_TUNNEL_START_TIMEOUT_SECONDS = 30
    _WEBAPP_HEALTH_CHECK_INTERVAL_MS = 30000
    _WEBAPP_RECONNECT_TIMEOUT_SECONDS = 30
    _WEBAPP_EXTENDED_FAILURE_THRESHOLD = 11
    _WEBAPP_EXTENDED_COOLDOWN_SECONDS = 30 * 60
    _WEBAPP_RECOVERY_PROBE_SECONDS = 20
    _WEBAPP_SHORT_COOLDOWN_SECONDS = 15 * 60
    _WEBAPP_CALM_CHECK_INTERVAL_MS = 30 * 60 * 1000

    # Задача користувача (2026-08-12): "форма постійно перепід'єднується,
    # це якийсь прикол... це ж не радіозв'язок що втратив контакт" - той
    # самий фікс, що й у gui.py: одна невдала HTTP-проба (миттєва затримка
    # на боці Cloudflare edge - звичайна річ для quick tunnel) більше не
    # вважається "тунель мертвий" сама по собі - лише кілька спроб поспіль.
    _WEBAPP_PROBE_RETRY_ATTEMPTS = 3
    _WEBAPP_PROBE_RETRY_DELAY_SECONDS = 3

    # Задача користувача (2026-08-13): "не підключає з першого разу" -
    # cloudflared сам попереджає, що щойно видана адреса "may take some time
    # to be reachable" - без grace period health-check міг перевіряти (і
    # хибно "хоронити") тунель ще до того, як DNS/edge встигли розповсюдитись.
    _WEBAPP_URL_GRACE_PERIOD_SECONDS = 20

    # Задача користувача (2026-08-13): "галочки 'Автовключение' - це по
    # суті вмикання таймерів, та робота програми по таймерам. якщо
    # вимкнено - таймери вимикаються" - для форми такий таймер вже є
    # (health-watchdog вище). Для бота - новий, окремий: раз на 60с,
    # якщо галочка увімкнена, а бот зараз не працює (з БУДЬ-ЯКОЇ причини -
    # збій, втрата зв'язку, чи навіть ручне "Выкл" - різниці нема, галочка
    # вирішує все) - запускає його знову.
    _BOT_AUTO_CHECK_INTERVAL_MS = 60000

    def __init__(self):
        super().__init__()
        self.title("AI Automation")
        # Задача користувача: "головний екран зроби ширшим трішки" - щоб
        # влазив підпис "Автовключение" праворуч від чекбокса, не тіснячи
        # решту рядка (той самий клас проблеми, що вже й обрізав "Телеграм-
        # Бот" минулого разу).
        # Задача користувача (2026-08-15): "збільши екран просто" - екран
        # Настройки виміряно потребує 656px вмісту (headless-тест,
        # winfo_reqheight), а вікно було 640px - переповнення й ховало
        # "Выход" унизу без прокрутки. CTkScrollableFrame (нижче,
        # _open_settings_screen) лишається запобіжником на майбутнє
        # зростання, але тут - пряме прохання: вікно просто вище, щоб
        # скрол здебільшого й не був потрібен.
        self.geometry("440x760")
        self.minsize(440, 760)
        self.configure(fg_color=COLOR_BG)

        self.settings = SettingsStore(paths.SETTINGS_PATH)
        self.store = ExcelSqliteStore(paths.DB_PATH)
        _reconcile_standard_menu_with_cloud(self.store)
        self.telegram_worker = None
        self._bot_stop_in_progress = False
        self._bot_next_auto_check_at = None
        self._bot_connect_handled = False
        self.is_closing = False

        # Задача користувача: "дві галочки в квадратах... якщо вони
        # увімкнені - програма їх автоматично вмикає/контролює, а якщо
        # прибрана - то тільки вручну" - персистентні (settings.json),
        # спільні для обох рядків (бот/форма) прапорці автоматики.
        self._bot_auto_manage = bool(self.settings.get("client_bot_auto_manage"))
        self._webapp_auto_manage = bool(self.settings.get("client_webapp_auto_manage"))
        self._bot_auto_var = tk.BooleanVar(value=self._bot_auto_manage)
        self._webapp_auto_var = tk.BooleanVar(value=self._webapp_auto_manage)

        self._bot_subtitle_var = tk.StringVar(value="выключен")

        # Форма (Telegram Mini App): локальный сервер + Cloudflare Quick
        # Tunnel, тот же принцип, что и в gui.py - НЕ привязан к
        # конкретному TelegramBotWorker (не "мигает" адресом на каждый
        # reconnect бота, только на явное "Выкл.").
        self.webapp_server = WebappServer(
            db_path=paths.DB_PATH,
            get_token=lambda: self._read_telegram_token()[0],
            get_fresh_context=lambda store, is_admin: (
                self.telegram_worker._webapp_data_browser_context(store, is_admin)
                if self.telegram_worker else None
            ),
            get_remote_control_token=lambda: paths.REMOTE_CONTROL_TOKEN,
            get_remote_status=self._get_remote_status,
            handle_remote_command=self._handle_remote_command,
            handle_home_heartbeat=self._handle_home_heartbeat,
            handle_set_role=self._handle_set_role,
            get_form_content_enabled=lambda: self._webapp_content_enabled,
        )
        self._webapp_content_enabled = True
        self.cloudflared_process = None
        self.webapp_public_url = ""
        self._webapp_tunnel_starting = False
        # Реальна знахідка (аудит коду, 2026-08-16): плановий вихід/
        # оновлення коректно зупиняють cloudflared.exe (_stop_webapp_tunnel),
        # але непередбачений збій Python (необроблений виняток, що вибиває
        # з mainloop) - ні, залишаючи процес "сиротою", що далі тримає
        # адресу тунелю. atexit НЕ рятує від жорсткого вбивства процесу чи
        # вимкнення живлення (для них узагалі немає способу відреагувати з
        # коду), але покриває реальний і значно частіший випадок
        # "програма впала сама" - навмисно мінімальний, без звернень до
        # Tkinter-віджетів чи self.webapp_server (їх стан на момент atexit
        # непередбачуваний).
        atexit.register(self._atexit_kill_tunnel_process)
        # Задача користувача (2026-08-15): "щоб у клієнта був датчик який
        # слухає завжди домашня программа" - тепер зберігається в пам'яті
        # процесу (webapp_server's HTTP-потік записує, головний потік
        # читає для _update_home_status_label), а не читається зі
        # спільного файлу - gui.py сам стукає СЮДИ (POST /control/
        # heartbeat), а не навпаки (стара програма не має власної
        # публічної адреси, тож ініціатива завжди йде від неї).
        self._last_home_heartbeat_at = None
        # Реальний баг (2026-08-13): "спершу вмикається форма, а потім бот" -
        # True тут ЗАВЖДИ, з моменту запуску програми, ще до будь-якого
        # кліку - watchdog нижче (_webapp_health_watchdog_tick, кожні 30с,
        # перший тік вже за 5с ПІСЛЯ ЗАПУСКУ ПРОГРАМИ, а не після старту
        # бота) бачив це й сам намагався підняти тунель через ВЛАСНИЙ,
        # незалежний probe-цикл (_webapp_health_check_worker -> _toggle_
        # webapp_form("restart")) - той шлях НЕ перевіряв _webapp_not_before
        # (той гейт стояв лише в _check_webapp_tunnel_health). Тепер False
        # за замовчуванням - _on_start_clicked виставляє True лише коли бот
        # реально стартував, тож жоден watchdog не підхопить форму раніше.
        self._webapp_should_run = False
        self._webapp_health_check_active = False
        self._webapp_check_interval_ms = self._WEBAPP_HEALTH_CHECK_INTERVAL_MS
        self._webapp_last_probe_error = None
        self._webapp_not_before = None
        self._webapp_url_assigned_at = None
        self._webapp_next_watchdog_tick_at = None
        self._webapp_subtitle_var = tk.StringVar(value="выключена")
        self.webapp_indicator_dot = None

        self.main_frame = None
        self.settings_frame = None
        self.indicator_dot = None
        self.bot_stop_button = None
        self.bot_start_button = None
        self.bot_auto_checkbox = None
        self.webapp_stop_button = None
        self.webapp_start_button = None
        self.webapp_auto_checkbox = None
        self.update_button = None
        self.theme_toggle_button = None
        self._pending_update_entry = None
        # Задача користувача (2026-08-13): вибір теми має запам'ятовуватись
        # так само, як усе інше в застосунку - раніше завжди стартувало зі
        # світлої, незалежно від того, що людина обрала минулого разу.
        self._dark_mode = bool(self.settings.get("client_dark_mode"))
        ctk.set_appearance_mode("dark" if self._dark_mode else "light")

        self.journals_window = None
        self.journals_list_frame = None
        self._action_log_detail_windows = {}
        self.personnel_window = None
        self.personnel_list_frame = None
        # Задача користувача (2026-08-17): "точно такий же вигляд зроби і в
        # клієнті" - той самий кеш+сортування+фільтр стан, що вже й gui.py
        # (_personnel_users_cache/_personnel_sort_field/_personnel_sort_
        # reverse/_personnel_role_filter).
        self._personnel_users_cache = None
        self._personnel_sort_field = None
        self._personnel_sort_reverse = False
        self._personnel_role_filter = None
        self.table_format_window = None
        # Задача користувача (2026-08-19, третя редакція): "вона має
        # спливаюче вікно відкривати з налаштуваннями" - той самий
        # singleton-Toplevel принцип, що й backup_window/table_format_
        # window тут же (замінює перший варіант - розгортання на місці
        # у Настройках, який не відповідав очікуваному вигляду).
        self.auto_update_window = None
        self.update_channel_window = None
        self.main_title_style_window = None
        # Задача користувача (2026-08-15): "домашня программа" (gui.py) і
        # ця (client_app.py) мають ОКРЕМІ бази - редактор кнопок у gui.py
        # ніяк не впливає на реального бота, бо gui.py більше не хостить
        # бота взагалі. Той самий редактор (без вкладки "Дії" - лише
        # дерево/порядок/розмір кнопок), перенесений сюди, де бот реально
        # живе - редагування тут одразу відбивається на живому меню бота.
        self.custom_buttons_window = None
        self.custom_buttons_list_frame = None
        self.custom_button_preview_frame = None
        # Задача користувача (2026-08-16): "давай додай кнопку резервные
        # копии" - той самий singleton-Toplevel принцип, що й custom_
        # buttons_window вище.
        self.backup_window = None
        self.backup_tab_switch = None
        self.backup_list_frame = None
        self.backup_footer_label = None
        self.backup_now_button = None
        self._backup_tab = "local"
        self._backup_restore_in_progress = False
        self.custom_buttons_selected_id = None

        self.refresh_excel_button = None
        self._excel_refresh_in_progress = False
        self._update_download_in_progress = False
        self._update_check_in_progress = False
        # Задача користувача (2026-08-15): "давай вже працювати через
        # оновлення та автоперезапуск після підтвердження" - друга фаза
        # тієї самої кнопки: коли завантаження завершилось, вона стає
        # "Установить и перезапустить" (сам клік - і є підтвердження, той
        # самий принцип "клік завжди свідомий", що вже й у create_code_
        # snapshot(force=True)).
        self._update_ready_to_install = False
        self._update_install_in_progress = False
        self._downloaded_update_target = None
        self._journals_fetch_limit = 50

        self._build_main_screen()
        self._refresh_webapp_status_text()
        self.protocol("WM_DELETE_WINDOW", self._on_window_close_clicked)
        self.after(500, self._poll_bot_status)
        self.after(2000, self._poll_for_update)
        self.after(1800000, self._schedule_code_backup_tick)
        # Задача користувача (2026-08-16, того самого дня двічі): спершу
        # "нашо ти робиш знімки? не роби ніякі знімки" - автотік прибрано
        # (падав через self.db_path, якого в ClientApp немає). Тоді ж,
        # повторно: "змінимо збереження резервних копій... раз в годину" -
        # автотік повертається, вже виправлений (paths.DB_PATH), на
        # ГОДИННОМУ інтервалі (_DB_BACKUP_TICK_MS нижче). Ліміти (20
        # локально/10 в хмарі) - вже готові константи (warehouse_data.
        # DB_BACKUP_LIMIT/self._ONEDRIVE_BACKUP_LIMIT), нових не додано.
        self.after(_DB_BACKUP_TICK_MS, self._schedule_db_backup_tick)
        self._webapp_next_watchdog_tick_at = time.monotonic() + 5
        self.after(5000, self._webapp_health_watchdog_tick)
        # Задача користувача: "при запуску програми - телеграм бот -
        # автоматом має запускатись" - лише коли галочка "Авто" увімкнена
        # (за замовчуванням так) і токен уже налаштований (_on_start_clicked
        # сам мовчки нічого не робить, якщо токена нема - перший запуск без
        # налаштувань не показує сирої помилки на порожньому екрані).
        if self._bot_auto_manage and self._read_telegram_token()[0]:
            self.after(300, self._on_start_clicked)
        # Задача користувача: "'Автовключение' - це по суті вмикання
        # таймерів". Рекурентний 60с таймер для бота - тікає завжди
        # (той самий ідіом, що й у форми), сама дія відбувається лише
        # коли _bot_auto_manage увімкнено (перевіряється всередині тіку).
        self._bot_next_auto_check_at = time.monotonic() + self._BOT_AUTO_CHECK_INTERVAL_MS / 1000
        self.after(self._BOT_AUTO_CHECK_INTERVAL_MS, self._bot_auto_check_tick)

    # ---------- маршалінг фонових потоків на головний Tk-потік ----------
    def _run_on_main_thread(self, callback):
        if self.is_closing:
            return
        try:
            self.after(0, callback)
        except tk.TclError:
            pass

    # main.py шле готові рядки на кшталт "Telegram подключен: @username" -
    # задача користувача: "@ назва боту подключен" (ім'я СПЕРЕДУ, не після
    # "подключен:") - той самий рядок, лише перевпорядкований для підпису
    # під статичним заголовком "Телеграм-Бот".
    def _format_bot_subtitle(self, text):
        match = re.match(r"^Telegram подключен: (@\S+)$", text)
        if match:
            return f"{match.group(1)} подключен"
        if text.startswith("Telegram "):
            return text[len("Telegram "):]
        return text

    def _set_status_text(self, text):
        self._run_on_main_thread(lambda: self._handle_bot_status_update(text))

    # Задача користувача: "при ВДАЛОМУ запуску (не при спробі), через 5 сек
    # має почати вмикатись форма" - main.py шле "Telegram подключен: @ім'я"
    # лише після РЕАЛЬНОГО успішного getMe, той самий regex, що й
    # _format_bot_subtitle вже розпізнає. _bot_connect_handled - один раз
    # на цей запуск (скинуто в _on_start_clicked), щоб повторні "подключен"
    # від наступних reconnect'ів циклу поллінгу не переплановували таймер
    # знову і знову.
    def _handle_bot_status_update(self, text):
        self._bot_subtitle_var.set(self._format_bot_subtitle(text))
        if self._bot_connect_handled:
            return
        if not re.match(r"^Telegram подключен: @\S+$", text):
            return
        self._bot_connect_handled = True
        # Задача користувача: "якщо у Форми 'Автовключение' не стоїть -
        # далі нічого не відбувається" - форма НЕ автостартує разом з
        # ботом, якщо її власна автоматика вимкнена; лишається лише ручний
        # клік "Вкл" на самій формі.
        if self._webapp_auto_manage:
            self._webapp_should_run = True
            # Задача користувача (2026-08-12): "спершу має запуститись
            # телеграм бот. а через 5 сек має почати запускатись форма" -
            # не технічна залежність (незалежні), а бажаний порядок
            # запуску. _webapp_not_before тримає watchdog нижче від
            # передчасного автозапуску, поки не спрацює цей явний відклад.
            # Задача користувача (2026-08-15): "перевірку форми... вона
            # робить першу перевірку зарано. ще не встигла підконектитись.
            # збільш перший час вдвічі" - 5с не вистачало cloudflared, щоб
            # реально встигнути видати URL тунелю до першої перевірки;
            # 5 -> 10.
            # Задача користувача (2026-08-16): "форма постійно дісконектиться.
            # зміни автоувімкнення форми через 15 секунд" - 10с досі іноді
            # не вистачало (той самий клас проблеми, тепер серйозніше) -
            # watchdog встигав побачити тунель ще не готовим і перезапустити
            # його зарано, що й виглядало як "постійно дісконектиться" -
            # 10 -> 15.
            self._webapp_not_before = time.monotonic() + 15
            self.after(15000, self._start_webapp_tunnel)

    def _set_indicator(self, color):
        def apply():
            if self.indicator_dot is not None:
                self.indicator_dot.configure(fg_color=color)
        self._run_on_main_thread(apply)

    def _set_webapp_indicator(self, color):
        def apply():
            if self.webapp_indicator_dot is not None:
                self.webapp_indicator_dot.configure(fg_color=color)
        self._run_on_main_thread(apply)

    # ---------- экран 1: главный ----------
    def _build_main_screen(self):
        self.main_frame = ctk.CTkFrame(self, fg_color="transparent")
        # Тестове оновлення (2026-08-16): "тепер підніми напис версії на
        # піксель" - повертає нижній зовнішній відступ назад до 16
        # (симетрично з верхнім) - версія піднімається на 1px вгору
        # відносно попереднього тестового стану (pady=(16,15)).
        self.main_frame.pack(fill="both", expand=True, padx=16, pady=(16, 16))

        self._build_header(self.main_frame)
        self._build_update_strip(self.main_frame)
        self._build_control_panel(self.main_frame)
        self._build_menu(self.main_frame)

        # Задача користувача (2026-08-15): "змісти кнопку вийти в самий
        # низ, аж до версії, прям над версією з відступом у пару пікселів" -
        # версія пакується ПЕРШОЮ (side="bottom" - займає сам нижній край),
        # "Выход" - ДРУГОЮ, теж side="bottom" - pack() складає послідовні
        # bottom-віджети СТОПКОЮ ВГОРУ від попереднього, тож він лягає
        # рівно над версією, а не деінде в потоці top-віджетів меню.
        ctk.CTkLabel(
            self.main_frame, text=f"ver. {__version__}",
            font=("", 10), text_color=COLOR_TEXT_MUTED,
        ).pack(side="bottom", pady=(0, 0))

        ctk.CTkButton(
            self.main_frame, text="Выход", image=_load_icon("power_red"), compound="left", anchor="w",
            fg_color=COLOR_DANGER_BG, text_color=COLOR_STOP_TEXT, hover_color=COLOR_DANGER_HOVER,
            command=self._on_exit_clicked, corner_radius=10, height=42, font=("", 13, "bold"),
        ).pack(side="bottom", fill="x", pady=(12, 2))

    # Задача користувача: "показує в головному меню зверху справа... синю
    # кнопку оновлення" + "тумблер десь всунь у головному меню красиво,
    # вмикачі гігантські, змінювати тему взагалі ніби випало по дорозі" -
    # тема тепер маленька іконка-кнопка тут-таки, поруч із заголовком, а не
    # гігантський перемикач у списку меню внизу.
    def _build_header(self, parent):
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.pack(fill="x", pady=(0, 10))

        title_col = ctk.CTkFrame(header, fg_color="transparent")
        title_col.pack(side="left")
        self.main_title_label = ctk.CTkLabel(title_col, text="AI Automation", font=("", 19, "bold"), text_color=COLOR_TEXT)
        self.main_title_label.pack(anchor="w")
        self._apply_main_title_style()

        self.theme_toggle_button = ctk.CTkButton(
            header, text="Светлая" if self._dark_mode else "Тёмная", width=64, height=26, font=("", 11),
            fg_color="transparent", border_width=1, border_color=COLOR_BORDER,
            text_color=COLOR_TEXT_MUTED, hover_color=COLOR_HOVER,
            command=self._on_theme_toggle,
        )
        self.theme_toggle_button.pack(side="right")

    # Задача користувача (2026-08-19): "можливість змінювати напис на
    # головному екрані... положення по х вправо/вліво... розмір тексту...
    # шрифт... колір" - той самий "читай з self.settings щоразу" ідіом, що
    # й решта прапорців тут. Порожні/відсутні поля - старий вигляд
    # ("AI Automation", розмір 19, стандартний шрифт/колір), нічого не
    # ламається для тих, хто це ніколи не чіпав.
    _MAIN_TITLE_FONT_CHOICES = (
        "Segoe UI", "Arial", "Calibri", "Tahoma", "Verdana", "Georgia", "Consolas", "Times New Roman",
    )
    _MAIN_TITLE_COLOR_CHOICES = ("#20242A", "#2F7BD9", "#3EA96E", "#B23B3B", "#D9A441", "#8452D5")

    def _main_title_style(self):
        raw = self.settings.get("main_title_style")
        style = raw if isinstance(raw, dict) else {}
        try:
            x_offset = int(style.get("x_offset") or 0)
        except (TypeError, ValueError):
            x_offset = 0
        try:
            font_size = int(style.get("font_size") or 19)
        except (TypeError, ValueError):
            font_size = 19
        return {
            "text": style.get("text") or "AI Automation",
            "x_offset": max(0, x_offset),
            "font_size": max(8, font_size),
            "font_family": style.get("font_family") or "",
            "color": style.get("color") or "",
        }

    # "положення по х вправо/вліво" - заголовок і так уже сидить у самому
    # лівому краю (title_col.pack(side="left") вище) - рухати ЛІВІШЕ
    # структурно нікуди, тож повзунок лише зсуває ПРАВОРУЧ від цієї точки
    # (padx), 0 = поточний вигляд без жодних змін.
    def _apply_main_title_style(self):
        label = getattr(self, "main_title_label", None)
        if label is None or not label.winfo_exists():
            return
        style = self._main_title_style()
        label.configure(
            text=style["text"],
            font=(style["font_family"], style["font_size"], "bold"),
            text_color=style["color"] or COLOR_TEXT,
        )
        label.pack_forget()
        label.pack(anchor="w", padx=(style["x_offset"], 0))

    def _open_main_title_style_window(self):
        if self.main_title_style_window is not None and self.main_title_style_window.winfo_exists():
            self.main_title_style_window.deiconify()
            self.main_title_style_window.lift()
            self.main_title_style_window.focus_force()
            return
        window = tk.Toplevel(self)
        window.title("Заголовок программы")
        window.geometry("380x600")
        window.configure(bg=self._tk_color(COLOR_BG))
        self.main_title_style_window = window

        top = ctk.CTkFrame(window, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(16, 4))
        ctk.CTkLabel(top, text="Заголовок программы", font=("", 16, "bold"), text_color=COLOR_TEXT).pack(side="left")

        body = ctk.CTkScrollableFrame(window, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=(4, 8))

        saved = self._main_title_style()
        state = dict(saved)

        preview_card = ctk.CTkFrame(body, fg_color=COLOR_CARD, corner_radius=10)
        preview_card.pack(fill="x", pady=(0, 16))
        preview_label = ctk.CTkLabel(preview_card, text=state["text"], font=("", state["font_size"], "bold"))
        preview_label.pack(anchor="w", padx=(14, 14), pady=16)

        def refresh_preview():
            preview_label.configure(
                text=state["text"] or "AI Automation",
                font=(state["font_family"], state["font_size"], "bold"),
                text_color=state["color"] or COLOR_TEXT,
            )
            preview_label.pack_forget()
            preview_label.pack(anchor="w", padx=(14 + state["x_offset"], 14), pady=16)

        refresh_preview()

        ctk.CTkLabel(body, text="Текст", font=("", 12), text_color=COLOR_TEXT_MUTED).pack(anchor="w", pady=(0, 4))
        text_var = ctk.StringVar(value=state["text"])

        def on_text_changed(*_args):
            state["text"] = text_var.get()
            refresh_preview()

        text_var.trace_add("write", on_text_changed)
        ctk.CTkEntry(body, textvariable=text_var).pack(fill="x", pady=(0, 14))

        ctk.CTkLabel(body, text="Сдвиг вправо (px)", font=("", 12), text_color=COLOR_TEXT_MUTED).pack(
            anchor="w", pady=(0, 4)
        )
        x_value_label = ctk.CTkLabel(body, text=str(state["x_offset"]), font=("", 11), text_color=COLOR_TEXT_MUTED)

        def on_x_changed(value):
            state["x_offset"] = int(float(value))
            x_value_label.configure(text=str(state["x_offset"]))
            refresh_preview()

        x_slider = ctk.CTkSlider(body, from_=0, to=250, number_of_steps=250, command=on_x_changed)
        x_slider.set(state["x_offset"])
        x_slider.pack(fill="x", pady=(0, 2))
        x_value_label.pack(anchor="e", pady=(0, 14))

        ctk.CTkLabel(body, text="Размер шрифта", font=("", 12), text_color=COLOR_TEXT_MUTED).pack(
            anchor="w", pady=(0, 4)
        )
        size_value_label = ctk.CTkLabel(body, text=str(state["font_size"]), font=("", 11), text_color=COLOR_TEXT_MUTED)

        def on_size_changed(value):
            state["font_size"] = int(float(value))
            size_value_label.configure(text=str(state["font_size"]))
            refresh_preview()

        size_slider = ctk.CTkSlider(body, from_=10, to=40, number_of_steps=30, command=on_size_changed)
        size_slider.set(state["font_size"])
        size_slider.pack(fill="x", pady=(0, 2))
        size_value_label.pack(anchor="e", pady=(0, 14))

        ctk.CTkLabel(body, text="Шрифт", font=("", 12), text_color=COLOR_TEXT_MUTED).pack(anchor="w", pady=(0, 4))
        is_custom_font = bool(state["font_family"]) and state["font_family"] not in self._MAIN_TITLE_FONT_CHOICES
        font_option_var = ctk.StringVar(
            value="Свой шрифт..." if is_custom_font else (state["font_family"] or "По умолчанию")
        )
        custom_font_var = ctk.StringVar(value=state["font_family"] if is_custom_font else "")
        custom_font_entry = ctk.CTkEntry(body, textvariable=custom_font_var, placeholder_text="Название шрифта")

        def on_custom_font_changed(*_args):
            state["font_family"] = custom_font_var.get()
            refresh_preview()

        custom_font_var.trace_add("write", on_custom_font_changed)

        def on_font_option_changed(choice):
            if choice == "Свой шрифт...":
                custom_font_entry.pack(fill="x", pady=(0, 14))
                state["font_family"] = custom_font_var.get()
            else:
                custom_font_entry.pack_forget()
                state["font_family"] = "" if choice == "По умолчанию" else choice
            refresh_preview()

        ctk.CTkOptionMenu(
            body, variable=font_option_var,
            values=["По умолчанию", *self._MAIN_TITLE_FONT_CHOICES, "Свой шрифт..."],
            command=on_font_option_changed,
        ).pack(fill="x")
        if is_custom_font:
            custom_font_entry.pack(fill="x", pady=(0, 14))
        else:
            ctk.CTkFrame(body, height=14, fg_color="transparent").pack()

        ctk.CTkLabel(body, text="Цвет", font=("", 12), text_color=COLOR_TEXT_MUTED).pack(anchor="w", pady=(0, 4))
        swatch_row = ctk.CTkFrame(body, fg_color="transparent")
        swatch_row.pack(fill="x", pady=(0, 14))

        def set_color(new_color):
            state["color"] = new_color
            refresh_preview()

        for swatch_color in self._MAIN_TITLE_COLOR_CHOICES:
            ctk.CTkButton(
                swatch_row, text="", width=28, height=28, corner_radius=14,
                fg_color=swatch_color, hover_color=swatch_color,
                border_width=1, border_color=COLOR_BORDER,
                command=lambda c=swatch_color: set_color(c),
            ).pack(side="left", padx=(0, 6))

        def pick_custom_color():
            initial = state["color"] or self._tk_color(COLOR_TEXT)
            chosen = colorchooser.askcolor(color=initial, title="Цвет заголовка")
            if chosen and chosen[1]:
                set_color(chosen[1])

        ctk.CTkButton(
            swatch_row, text="Свой...", width=64, height=28, fg_color="transparent",
            border_width=1, border_color=COLOR_BORDER, text_color=COLOR_TEXT_MUTED, hover_color=COLOR_HOVER,
            command=pick_custom_color,
        ).pack(side="left")

        bottom = ctk.CTkFrame(window, fg_color="transparent")
        bottom.pack(fill="x", padx=16, pady=(8, 16))

        def on_save():
            self.settings.set("main_title_style", dict(state))
            self._apply_main_title_style()
            window.destroy()

        def on_reset():
            self.settings.set("main_title_style", {})
            self._apply_main_title_style()
            window.destroy()

        ctk.CTkButton(
            bottom, text="Сохранить", fg_color=COLOR_UPDATE_BLUE, hover_color=COLOR_UPDATE_BLUE, command=on_save,
        ).pack(side="right")
        ctk.CTkButton(
            bottom, text="Сбросить", fg_color="transparent", border_width=1, border_color=COLOR_BORDER,
            text_color=COLOR_TEXT_MUTED, hover_color=COLOR_HOVER, command=on_reset,
        ).pack(side="right", padx=(0, 8))

    # Задача користувача: "опусти [статусні рядки] до низу. зверху буде
    # вільне місце... додай кнопку оновлення із кнопкою завантажування його.
    # меню не має їздити ніколи ні при яких випадках. зарезервуй місце для
    # кнопок" - окрема смуга під заголовком, фіксованої висоти (fixed-size
    # slot + pack_propagate(False), той самий прийом, що вже в
    # D:\IT\Python\planner\main.py's update_button_slot): чи є оновлення, чи
    # нема - висота смуги не міняється, тож усе нижче (статус бота/форми,
    # меню, Вихід) завжди сидить на тому самому місці.
    def _build_update_strip(self, parent):
        # Задача користувача (2026-08-15): "не має видвати спливаюче
        # вікно-повідомлення... просто тихесенько під кнопкою" - друга
        # (теж фіксованої висоти, той самий прийом) смуга-рядок під
        # кнопкою для результату ручної перевірки, замість messagebox.
        # Задача користувача (2026-08-15): "Установить и перезапустить" не
        # вміщався в один рядок (текст обрізався) - кнопка/слот тепер
        # ширші й вищі, під ДВА рядки тексту (див. _on_update_download_
        # finished нижче - там текст із "\n"). strip/
        # top_row - фіксована висота (pack_propagate(False)), тож їх теж
        # треба збільшити на ту саму різницю, інакше кнопка обрізалась би
        # знизу батьківським контейнером.
        #
        # Задача користувача (2026-08-16): "5. Компактний бар лише під
        # кнопкою" - слот/рядок/смуга ще раз вищі на ту саму різницю (+8px),
        # щоб під кнопкою завжди був зарезервований рядок для тонкої
        # смужки прогресу - той самий фіксовано-висотний принцип: бар
        # з'являється/зникає, а сама смуга-контейнер не "стрибає".
        strip = ctk.CTkFrame(parent, fg_color=COLOR_CARD, corner_radius=10, height=86)
        strip.pack(fill="x", pady=(0, 12))
        strip.pack_propagate(False)

        top_row = ctk.CTkFrame(strip, fg_color="transparent", height=64)
        top_row.pack(fill="x")
        top_row.pack_propagate(False)

        # Варіант 3 (обраний користувачем): лише іконка, без тексту -
        # той самий refresh_light/dark.png, що вже й "Обновить эксели" в меню.
        self.check_update_button = ctk.CTkButton(
            top_row, text="", image=_load_icon("refresh", size=16), width=30, height=30,
            fg_color=COLOR_ROW, hover_color=COLOR_HOVER,
            command=self._manual_check_for_update,
        )
        self.check_update_button.pack(side="left", padx=(6, 0), pady=6)

        self.update_button_slot = ctk.CTkFrame(top_row, fg_color="transparent", width=170, height=52)
        self.update_button_slot.pack(side="right", padx=(0, 6), pady=6)
        self.update_button_slot.pack_propagate(False)

        # Кнопка й смужка прогресу пакуються ЯК ОДНЕ ЦІЛЕ (side="right"
        # у слоті, як і раніше), а всередині - вертикально одна над одною
        # (side="top"), обидві шириною 158px - той самий "компактний бар,
        # не на всю ширину" вигляд, що й обраний користувачем мокап.
        self._update_button_inner = ctk.CTkFrame(
            self.update_button_slot, fg_color="transparent", width=158, height=48,
        )
        self._update_button_inner.pack(side="right")
        self._update_button_inner.pack_propagate(False)

        self.update_check_result_label = ctk.CTkLabel(
            strip, text="", font=("", 11), text_color=COLOR_TEXT_MUTED, anchor="w",
        )
        self.update_check_result_label.pack(fill="x", padx=(10, 10), pady=(0, 6))

        self.update_button = ctk.CTkButton(
            self._update_button_inner, text="Обновление", width=158, height=40, font=("", 11),
            fg_color=COLOR_UPDATE_BLUE, hover_color="#255FA8", compound="left",
            command=self._on_update_button_clicked,
        )
        self._update_spinner_step = 0

        self.update_progress_bar = ctk.CTkProgressBar(
            self._update_button_inner, width=158, height=4, corner_radius=2,
            fg_color=COLOR_ROW, progress_color="#FFFFFF",
        )
        self.update_progress_bar.set(0)

    def _build_control_panel(self, parent):
        panel = ctk.CTkFrame(parent, fg_color=COLOR_CARD, corner_radius=10)
        panel.pack(fill="x", pady=(0, 12))

        self._build_status_row(
            panel, "Телеграм-Бот", self._bot_subtitle_var, is_first=True,
            on_stop=self._on_stop_clicked, on_start=self._on_start_clicked, on_restart=self._on_restart_clicked,
            dot_attr="indicator_dot", off_attr="bot_stop_button", on_attr="bot_start_button",
            auto_var=self._bot_auto_var, on_auto_toggle=self._on_bot_auto_toggled, auto_attr="bot_auto_checkbox",
        )

        ctk.CTkFrame(panel, height=1, fg_color=COLOR_DIVIDER).pack(fill="x", padx=12)

        # Задача користувача: "а самі кнопки потім?" - ні, ці кнопки не про
        # окремі форми (ПРИХОД/РЕАЛИЗАЦИЯ тощо - ті вже готові в самому
        # боті), а про сам тунель, через який форми відкриваються в
        # Telegram. Той самий рядок-патерн, що й бот вище, лише для тунелю.
        self._build_status_row(
            panel, "Форма", self._webapp_subtitle_var, is_first=False,
            on_stop=lambda: self._toggle_webapp_form("stop"),
            on_start=self._on_webapp_start_clicked,
            on_restart=lambda: self._toggle_webapp_form("restart"),
            dot_attr="webapp_indicator_dot", off_attr="webapp_stop_button", on_attr="webapp_start_button",
            auto_var=self._webapp_auto_var, on_auto_toggle=self._on_webapp_auto_toggled, auto_attr="webapp_auto_checkbox",
        )

    # Задача користувача: "кнопки не мають їздити, вони стаціонарні" - grid
    # (не pack side=left/right) гарантує, що права колонка з кнопками
    # завжди сидить на ФІКСОВАНІЙ позиції, незалежно від довжини підпису
    # зліва чи довжини тексту в рядку деталей знизу. Заголовок ("Телеграм-
    # Бот"/"Форма") статичний, деталі (@ім'я, "подключена" тощо) - окремим,
    # приглушеним сірим рядком під ним ("покажи де крапка, а деталі
    # ненав'язливо нижче сірим").
    def _build_status_row(
        self, parent, label, subtitle_var, is_first, on_stop, on_start, on_restart,
        dot_attr, off_attr, on_attr, auto_var, on_auto_toggle, auto_attr,
    ):
        # Задача користувача: "тут не має бути такий простір... зроби їх
        # компактними" - чекбокс, доданий поруч із "Перезапустить", раніше
        # тіснив підпис "Телеграм-Бот" аж до обрізання тексту при 380px
        # ширині вікна - усі відступи/розміри в цьому рядку тепер щільніші.
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(10, 10) if is_first else (0, 10))
        row.grid_columnconfigure(0, weight=1)
        row.grid_columnconfigure(1, weight=0)

        top = ctk.CTkFrame(row, fg_color="transparent")
        top.grid(row=0, column=0, sticky="w")
        dot = ctk.CTkFrame(top, width=8, height=8, corner_radius=4, fg_color=COLOR_OFF)
        dot.pack(side="left", padx=(0, 6))
        setattr(self, dot_attr, dot)
        ctk.CTkLabel(top, text=label, font=("", 11), text_color=COLOR_TEXT).pack(side="left")

        buttons = ctk.CTkFrame(row, fg_color="transparent")
        buttons.grid(row=0, column=1, sticky="e")
        stop_button = ctk.CTkButton(
            buttons, text="Выкл", width=42, height=24, font=("", 10), command=on_stop,
        )
        stop_button.pack(side="left", padx=(0, 3))
        start_button = ctk.CTkButton(
            buttons, text="Вкл", width=42, height=24, font=("", 10), command=on_start,
        )
        start_button.pack(side="left", padx=(0, 3))
        ctk.CTkButton(
            buttons, text="Перезапустить", width=88, height=24, font=("", 10),
            fg_color="transparent", border_width=1, border_color=COLOR_BORDER,
            text_color=COLOR_TEXT, hover_color=COLOR_HOVER,
            command=on_restart,
        ).pack(side="left")
        # Задача користувача: "дві галочки в квадратах... якщо вони
        # увімкнені - програма їх автоматично вмикає/контролює" - маленький
        # чекбокс праворуч від "Перезапустить" (той самий, для обох рядків).
        auto_checkbox = ctk.CTkCheckBox(
            buttons, text="", variable=auto_var, width=16, height=16,
            checkbox_width=16, checkbox_height=16, corner_radius=4, border_width=1.5,
            command=on_auto_toggle,
        )
        auto_checkbox.pack(side="left", padx=(5, 0))
        # Задача користувача: "підпиши їх з правої сторони. 'Автовключение'".
        ctk.CTkLabel(
            buttons, text="Автовключение", font=("", 10), text_color=COLOR_TEXT_MUTED,
        ).pack(side="left", padx=(4, 0))
        setattr(self, auto_attr, auto_checkbox)
        setattr(self, off_attr, stop_button)
        setattr(self, on_attr, start_button)
        # Задача користувача (скріншот): "коли їх активуєш - має синій колір
        # переходити до них, щоб візуально було зрозуміло де зараз кнопка
        # увімкнена" - синій акцент відображає ПОТОЧНИЙ стан (Вкл/Выкл), не
        # прибитий до "Вкл" за замовчуванням. На старті - завжди вимкнено.
        self._set_toggle_buttons(stop_button, start_button, is_on=False)

        ctk.CTkLabel(
            row, textvariable=subtitle_var, font=("", 11), text_color=COLOR_TEXT_MUTED, anchor="w",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=(16, 0), pady=(2, 0))

    def _set_toggle_buttons(self, off_button, on_button, is_on):
        if is_on:
            on_button.configure(fg_color=COLOR_UPDATE_BLUE, text_color="white", hover_color="#255FA8", border_width=0)
            off_button.configure(fg_color=COLOR_STOP, text_color=COLOR_STOP_TEXT, hover_color=COLOR_HOVER, border_width=0)
        else:
            off_button.configure(fg_color=COLOR_UPDATE_BLUE, text_color="white", hover_color="#255FA8", border_width=0)
            on_button.configure(
                fg_color="transparent", text_color=COLOR_TEXT, hover_color=COLOR_HOVER,
                border_width=1, border_color=COLOR_BORDER,
            )

    # "якщо прибрана - то тільки вручну це можна змінювати" - персистуємо
    # одразу (settings.json), і вимикаємо саме ту автоматику, яку ця
    # галочка описує (не чіпаючи поточний стан бота/форми - лише майбутню
    # поведінку watchdog'ів).
    # Задача користувача (2026-08-13): "'Автовключение' - це по суті
    # вмикання таймерів, та робота програми по таймерам. якщо вимкнено -
    # таймери вимикаються" - вимкнення (False) само по собі нічого не
    # зупиняє ЗАРАЗ (лише гасить майбутні автоперевірки/автозапуски).
    # Увімкнення (True), навпаки, "і якщо користувач тисне на
    # Автовключение, то бот починає відразу вмикатись автоматом" - не
    # чекає наступного 60с тіку, перевіряє й діє негайно (та сама логіка,
    # що й сам тік нижче, викликана позачергово).
    def _on_bot_auto_toggled(self):
        self._bot_auto_manage = self._bot_auto_var.get()
        self.settings.set("client_bot_auto_manage", self._bot_auto_manage)
        if self._bot_auto_manage:
            self._bot_next_auto_check_at = time.monotonic() + self._BOT_AUTO_CHECK_INTERVAL_MS / 1000
            worker = self.telegram_worker
            bot_alive = bool(worker and worker.thread and worker.thread.is_alive())
            if not bot_alive and not self._bot_stop_in_progress:
                self._on_start_clicked()

    # Той самий рекурентний "таймер = автоматика" принцип - тікає завжди,
    # але ЩОСЬ РОБИТЬ лише коли галочка увімкнена. Не розрізняє ПРИЧИНУ,
    # чому бот зараз не працює (збій, втрата зв'язку, ручне "Выкл") -
    # галочка сама вирішує, чи можна діяти, а не історія попередньої дії.
    def _bot_auto_check_tick(self):
        self._bot_next_auto_check_at = time.monotonic() + self._BOT_AUTO_CHECK_INTERVAL_MS / 1000
        if self._bot_auto_manage:
            worker = self.telegram_worker
            bot_alive = bool(worker and worker.thread and worker.thread.is_alive())
            if not bot_alive and not self._bot_stop_in_progress:
                self._on_start_clicked()
        self.after(self._BOT_AUTO_CHECK_INTERVAL_MS, self._bot_auto_check_tick)

    # Реальний баг (2026-08-15): "форма сама не хоче вмикатись, коли
    # просто галочку на вимкненій формі проставив, без ручного вмикання" -
    # цей обробник мав лише половину логіки бота (_on_bot_auto_toggled
    # вище): гасіння МАЙБУТНЬОГО автозапуску при знятті галочки, але
    # ЖОДНОЇ дії при встановленні. "І якщо користувач тисне на
    # Автовключение, то [форма] починає відразу вмикатись автоматом" -
    # той самий принцип, що вже діє для бота: не чекати наступного тіку,
    # діяти негайно. Форму автоматично вмикаємо лише якщо бот реально
    # живий (той самий критерій, що вже питає підтвердження при РУЧНОМУ
    # запуску без бота, _on_webapp_start_clicked) - авто-шлях не показує
    # діалог, просто мовчки нічого не робить, якщо бота нема.
    def _on_webapp_auto_toggled(self):
        self._webapp_auto_manage = self._webapp_auto_var.get()
        self.settings.set("client_webapp_auto_manage", self._webapp_auto_manage)
        if self._webapp_auto_manage:
            worker = self.telegram_worker
            bot_alive = bool(worker and worker.thread and worker.thread.is_alive())
            if bot_alive and not self._webapp_should_run:
                self._webapp_should_run = True
                self._webapp_not_before = None
                self._start_webapp_tunnel()
            return
        # "вимкнення (False) само по собі нічого не зупиняє ЗАРАЗ (лише
        # гасить майбутні автоперевірки/автозапуски)" - той самий принцип,
        # що вже задокументований для бота нижче. Якщо форма ЩЕ НЕ
        # почала запускатись (немає ні процесу тунелю, ні "starting"),
        # то запланований автозапуск - саме "майбутній автозапуск", тож
        # знімається; якщо вона вже реально запускається/працює - той
        # самий принцип каже НЕ чіпати вже сущий процес.
        if not self._webapp_tunnel_starting and self.cloudflared_process is None:
            self._webapp_should_run = False
            self._webapp_not_before = None

    # Задача користувача (скріншот): "виход не інтуїтивний, завжди вихід -
    # це інтуїтивно остання кнопка" - реальна причина була в тому, що
    # тумблер теми пакувався ПІСЛЯ "Выход" у цьому самому меню; тема
    # переїхала в шапку (_build_header), тож "Выход" тепер і візуально, і
    # фактично останній пункт списку. Кнопки на світлішому фоні (COLOR_ROW),
    # не прозорі - "кнопи не видно" на фоні картки.
    def _build_menu(self, parent):
        menu = ctk.CTkFrame(parent, fg_color=COLOR_CARD, corner_radius=10)
        menu.pack(fill="x", pady=(0, 12))

        items = [
            ("settings", "Настройки", self._open_settings_screen),
            ("journals", "Журналы", self._open_journals_window),
            ("personnel", "Персонал", self._open_personnel_window),
            ("refresh", "Обновить эксели", self._on_refresh_excel_clicked),
            ("barchart", "Открыть данные в браузере", self._on_open_data_in_browser_clicked),
        ]
        for index, (icon, label, handler) in enumerate(items):
            button = self._build_row_button(menu, icon, label, handler, first=(index == 0))
            if label == "Обновить эксели":
                self.refresh_excel_button = button
            if index < len(items) - 1:
                ctk.CTkFrame(menu, height=1, fg_color=COLOR_DIVIDER).pack(fill="x")
        ctk.CTkFrame(menu, height=1, fg_color="transparent").pack(fill="x", pady=(0, 1))

    # Спільний рядок "іконка + підпис", що клацається цілком - і для
    # головного меню, і для рядків "Настройки" (задача користувача: "вон
    # норм же, роби точно так" - в еталонному ескізі рядки в Настройках
    # виглядають так само, як пункти головного меню, без окремої кнопки
    # "Изменить" і без підрядка з поточним шляхом).
    def _build_row_button(self, parent, icon_name, label, handler, first=False):
        button = ctk.CTkButton(
            parent, text=label, image=_load_icon(icon_name), compound="left", anchor="w",
            fg_color=COLOR_ROW, text_color=COLOR_TEXT, hover_color=COLOR_HOVER,
            command=handler, corner_radius=0, height=42, font=("", 13),
        )
        button.pack(fill="x", padx=1, pady=(1 if first else 0, 0))
        return button

    # ---------- экран 2: настройки ----------
    # Реальний баг (2026-08-15, "3 раз прошу... покажи як ти це бачиш"):
    # кнопка "Выход" внизу екрана БУЛА в коді й реально будувалась - але
    # цілий вміст Настройки вимірюється в 656px, а всередині фіксованого
    # вікна 440x640 доступно лише 608px (переміряно напряму, headless-
    # тестом: parent.winfo_reqheight()). Плаский CTkFrame + .pack() не
    # прокручує вміст, що не влазить - решта просто обрізалась невидимо,
    # без жодної помилки. CTkScrollableFrame - той самий контейнер, що
    # вже використовують Журнали/Персонал (journals_list_frame/
    # personnel_list_frame) - тепер і найпростіший спосіб пережити
    # майбутнє зростання цього екрану, а не фіксити конкретний недобір
    # пікселів щоразу заново.
    def _open_settings_screen(self):
        self.main_frame.pack_forget()
        if self.settings_frame is not None:
            self.settings_frame.destroy()
        self.settings_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.settings_frame.pack(fill="both", expand=True, padx=16, pady=16)
        self._build_settings_screen(self.settings_frame)

    def _close_settings_screen(self):
        self.settings_frame.pack_forget()
        self.main_frame.pack(fill="both", expand=True, padx=16, pady=16)

    def _build_settings_screen(self, parent):
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.pack(fill="x", pady=(0, 16))
        ctk.CTkButton(
            header, text="←", width=32, fg_color="transparent",
            text_color=COLOR_TEXT, hover_color=COLOR_HOVER,
            command=self._close_settings_screen,
        ).pack(side="left")
        ctk.CTkLabel(header, text="Настройки", font=("", 16, "bold"), text_color=COLOR_TEXT).pack(side="left", padx=(8, 0))

        self._build_autostart_settings_section(parent)

        self._build_auto_update_settings_section(parent)

        self._build_bot_settings_section(parent)

        self._build_data_settings_section(parent)

        self._build_settings_section(parent, "Интерфейс", [
            ("settings", "Заголовок программы", self._open_main_title_style_window),
        ])

        self._build_settings_section(parent, "Кнопки", [
            ("document", "Редактор кнопок", self._open_custom_buttons_window),
        ])

        self._build_settings_section(parent, "Форма", [
            ("save", "Резервные копии Excel", self._open_backup_window),
        ])

        # Задача користувача (2026-08-15): "тепер змінюй це на автоматичне
        # з'єднання між программами" - раніше тут була секція "Общая папка
        # для статуса" (ручний вибір спільної теки) - тепер, з фіксованою
        # адресою (paths.CLOUDFLARED_TUNNEL_HOSTNAME) і фіксованим ключем
        # (paths.REMOTE_CONTROL_TOKEN), налаштовувати вже нічого - зв'язок
        # встановлюється сам собою, без жодного кроку з боку людини.

        # Задача користувача (2026-08-15): "додай ще тут знизу таку саму
        # як в головном меню кнопку вихід з застосунку" - той самий
        # віджет 1-в-1 (стиль/іконка/команда), що й у _build_menu.
        ctk.CTkButton(
            parent, text="Выход", image=_load_icon("power_red"), compound="left", anchor="w",
            fg_color=COLOR_DANGER_BG, text_color=COLOR_STOP_TEXT, hover_color=COLOR_DANGER_HOVER,
            command=self._on_exit_clicked, corner_radius=10, height=42, font=("", 13, "bold"),
        ).pack(fill="x", pady=(0, 12))

    def _build_settings_section(self, parent, title, rows):
        ctk.CTkLabel(parent, text=title, font=("", 12), text_color=COLOR_TEXT_MUTED).pack(anchor="w", pady=(0, 6))
        card = ctk.CTkFrame(parent, fg_color=COLOR_CARD, corner_radius=10)
        card.pack(fill="x", pady=(0, 16))
        for index, (icon, label, handler) in enumerate(rows):
            self._build_row_button(card, icon, label, handler, first=(index == 0))
            if index < len(rows) - 1:
                ctk.CTkFrame(card, height=1, fg_color=COLOR_DIVIDER).pack(fill="x")
        ctk.CTkFrame(card, height=1, fg_color="transparent").pack(fill="x", pady=(0, 1))

    # Задача користувача (2026-08-16): "давай додай кнопку резервные
    # копии... зберігай 10 останніх копій онлайн" - той самий singleton-
    # Toplevel принцип, що й _open_custom_buttons_window вище.
    #
    # Задача користувача (2026-08-16): "вікно резервних копій - какаха,
    # нічого незрозуміло... розділи на заголовки, локальні, в хмарі. і
    # доступ має бути і до хмарних - щоб завантажити і відновити і до
    # локальних - відновити відразу" - перебудовано на два справжні
    # списки знімків (CTkSegmentedButton-перемикач Локально/В облаке, той
    # самий готовий CTk-віджет, що вже й у "Розмір" редактора таблиці) з
    # діями на кожному рядку, замість двох рядків узагальненого статусу.
    def _open_backup_window(self):
        if self.backup_window is not None and self.backup_window.winfo_exists():
            self.backup_window.deiconify()
            self.backup_window.lift()
            self.backup_window.focus_force()
            self._refresh_backup_lists()
            return
        window = tk.Toplevel(self)
        window.title("Резервные копии Excel")
        window.geometry("420x420")
        window.configure(bg=self._tk_color(COLOR_BG))
        self.backup_window = window
        self._backup_tab = "local"
        self._build_backup_window(window)

    def _build_backup_window(self, window):
        top = ctk.CTkFrame(window, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(16, 8))
        ctk.CTkLabel(top, text="Резервные копии Excel", font=("", 16, "bold"), text_color=COLOR_TEXT).pack(side="left")

        self.backup_tab_switch = ctk.CTkSegmentedButton(
            window, values=["Локально", "В облаке"], command=self._on_backup_tab_changed,
        )
        self.backup_tab_switch.set("Локально")
        self.backup_tab_switch.pack(fill="x", padx=16, pady=(0, 8))

        # Той самий рядковий стиль, що вже й журнал дій
        # (_build_action_log_row) - plain tk, не CTk, з тієї самої
        # причини (десятки рядків одразу відчутно гальмують на CTk).
        self.backup_list_frame = ctk.CTkScrollableFrame(window, fg_color="transparent")
        self.backup_list_frame.pack(fill="both", expand=True, padx=16, pady=(0, 4))

        self.backup_footer_label = ctk.CTkLabel(
            window, text="", font=("", 11), text_color=COLOR_TEXT_MUTED, anchor="w",
        )
        self.backup_footer_label.pack(fill="x", padx=16, pady=(0, 4))

        self.backup_now_button = ctk.CTkButton(
            window, text="Создать резервную копию сейчас", command=self._on_backup_now_clicked,
        )
        self.backup_now_button.pack(fill="x", padx=16, pady=(4, 16))

        self._refresh_backup_lists()

    def _on_backup_tab_changed(self, value):
        self._backup_tab = "local" if value == "Локально" else "cloud"
        self._refresh_backup_lists()

    def _refresh_backup_lists(self):
        if self.backup_list_frame is None:
            return
        for child in self.backup_list_frame.winfo_children():
            child.destroy()
        if self._backup_tab == "local":
            self._render_local_backup_rows()
        else:
            self._render_cloud_backup_rows()

    # Задача користувача (2026-08-16, аудит коду): "щоб відновлення було не
    # прихованим" - раніше pre_restore-знімок (той, що restore_db_snapshot
    # сама створює ПЕРЕД кожним відновленням, як страховку) виключався з
    # цього списку - діалог підтвердження обіцяє "завжди можна відновити
    # назад", але сам страховочний знімок був недосяжний через UI. Той
    # самий принцип, що вже й у gui.py.open_db_backups_dialog: показуємо
    # ВСІ знімки, з позначкою "(перед восстановлением)" на тому самому.
    def _render_local_backup_rows(self):
        snapshots = list_db_snapshots()
        if not snapshots:
            self._build_backup_empty_label("Резервных копий пока нет.")
            self.backup_footer_label.configure(text="")
            return
        for entry in snapshots:
            self._build_backup_row(
                entry["path"], entry["mtime"], cloud=False,
                is_pre_restore=entry["is_pre_restore"], is_encrypted=entry["is_encrypted"],
            )
        self.backup_footer_label.configure(text=f"Хранится локально: {len(snapshots)} копий.")

    def _render_cloud_backup_rows(self):
        backups_root = standard_menu_cloud.cloud_folder_path()
        if backups_root is None:
            self._build_backup_empty_label("OneDrive не найден на этом компьютере.")
            self.backup_footer_label.configure(text="")
            return
        online_dir = backups_root / "db_backups"
        online_files = sorted(
            online_dir.glob("app_data_*"), key=lambda path: path.stat().st_mtime, reverse=True,
        ) if online_dir.exists() else []
        if not online_files:
            self._build_backup_empty_label("В облаке пока нет копий.")
            self.backup_footer_label.configure(text="")
            return
        for path in online_files:
            self._build_backup_row(path, path.stat().st_mtime, cloud=True, is_encrypted=path.suffix == ".enc")
        self.backup_footer_label.configure(
            text=f"В облаке (OneDrive): {len(online_files)} из {self._ONEDRIVE_BACKUP_LIMIT} копий."
        )

    def _build_backup_empty_label(self, text):
        tk.Label(
            self.backup_list_frame, text=text,
            fg=self._tk_color(COLOR_TEXT_MUTED), bg=self._tk_color(COLOR_BG),
        ).pack(anchor="w", pady=8)

    def _build_backup_row(self, path, mtime, cloud, is_pre_restore=False, is_encrypted=False):
        row_bg = self._tk_color(COLOR_ROW)
        text_color = self._tk_color(COLOR_TEXT)
        when = datetime.fromtimestamp(mtime).strftime("%d.%m.%Y %H:%M")
        tag = " (перед восстановлением)" if is_pre_restore else ""
        tag += " (зашифрован)" if is_encrypted else ""

        card = tk.Frame(self.backup_list_frame, bg=row_bg)
        card.pack(fill="x", pady=(0, 6))
        tk.Label(
            card, text=f"{when}{tag}", font=("Segoe UI", 11), fg=text_color, bg=row_bg, anchor="w",
        ).pack(side="left", padx=12, pady=10)

        buttons = tk.Frame(card, bg=row_bg)
        buttons.pack(side="right", padx=12, pady=8)
        if cloud:
            tk.Button(
                buttons, text="Скачать", width=9, command=lambda p=path: self._on_backup_download_clicked(p),
            ).pack(side="left", padx=(0, 4))
        tk.Button(
            buttons, text="Восстановить", width=12,
            command=lambda p=path, w=when: self._on_backup_restore_clicked(p, w),
        ).pack(side="left")

    # Задача користувача (2026-08-16): "доступ... до хмарних - щоб
    # завантажити" - хмарний знімок уже лежить локально (це файл у
    # локально-синхронізованій теці OneDrive клієнта, не справжній
    # мережевий blob), тож "Скачать" - просто копія обраного файлу туди,
    # куди вкаже сам користувач (напр. на флешку чи в іншу теку), той
    # самий filedialog-принцип, що вже й у решті застосунку.
    def _on_backup_download_clicked(self, snapshot_path):
        snapshot_path = Path(snapshot_path)
        destination = filedialog.asksaveasfilename(
            parent=self.backup_window, initialfile=snapshot_path.name,
            defaultextension=snapshot_path.suffix, title="Сохранить копию как",
        )
        if not destination:
            return

        # Реальна знахідка (аудит коду, 2026-08-16): shutil.copyfile раніше
        # виконувався прямо на головному Tk-потоці - для великого файлу БД
        # це коротке, але реальне "підвисання" вікна, на відміну від УСІХ
        # інших файлових операцій цього застосунку (той самий фоновий-потік
        # принцип, що вже й у _on_backup_now_clicked поруч).
        def worker():
            try:
                shutil.copyfile(snapshot_path, destination)
            except OSError as exc:
                error_text = str(exc)
                self._run_on_main_thread(lambda: messagebox.showerror(
                    "Резервные копии Excel", f"Не удалось сохранить файл: {error_text}", parent=self.backup_window,
                ))
                return
            self._run_on_main_thread(lambda: messagebox.showinfo(
                "Резервные копии Excel", "Файл сохранён.", parent=self.backup_window,
            ))

        threading.Thread(target=worker, daemon=True).start()

    # Задача користувача (2026-08-16): "до локальних - відновити відразу" -
    # той самий порядок дій, що вже перевірений живим продакшном у
    # gui.py.restore_db_backup_confirm: перевірка, що бот зупинений
    # (жива база не пишеться саме зараз), підтвердження з попередженням
    # про автоматичний pre_restore-знімок (відновлення НІКОЛИ не є точкою
    # неповернення), фоновий потік + indeterminate прогрес-вікно (сама дія
    # займає кілька секунд - без індикатора вікно виглядало б завислим), і
    # regenerate_excel_after_restore (інакше наступний запуск gui.py
    # реімпортував би стару БД з Excel і звів би відновлення нанівець).
    def _on_backup_restore_clicked(self, snapshot_path, when_text):
        if self.telegram_worker is not None:
            messagebox.showerror(
                "AI Automation",
                "Сначала остановите Телеграм-бота на главном экране, затем попробуйте восстановить снова.",
                parent=self.backup_window,
            )
            return
        # Реальна знахідка (аудит коду, 2026-08-16): раніше тут перевірявся
        # ЛИШЕ бот - форма (Mini App) обслуговує запити через webapp_server,
        # який відкриває СВОЄ власне з'єднання до paths.DB_PATH на кожен
        # HTTP-запит, незалежно від бота (застосунок явно дозволяє форму
        # без бота - див. підтвердження в _on_webapp_start_clicked). Без
        # цієї перевірки можна перезаписати живу базу файлом (не через
        # SQLite - restore_db_snapshot копіює сирі байти) саме в момент,
        # коли форма читає/пише той самий файл.
        tunnel_alive = self.cloudflared_process is not None and self.cloudflared_process.poll() is None
        if self._webapp_should_run or tunnel_alive:
            messagebox.showerror(
                "AI Automation",
                "Сначала остановите форму (Mini App) на главном экране, затем попробуйте восстановить снова.",
                parent=self.backup_window,
            )
            return
        confirmed = messagebox.askyesno(
            "Восстановить снимок",
            f"Восстановить состояние базы на {when_text}?\n\n"
            "Текущее состояние будет автоматически сохранено отдельным снимком перед "
            "восстановлением — его всегда можно будет восстановить обратно.",
            parent=self.backup_window,
        )
        if not confirmed:
            return
        # Реальна знахідка (аудит коду, 2026-08-16): погодинний автознімок
        # (_schedule_db_backup_tick) нічим не блокувався від відновлення -
        # хоч і вузьке вікно (потрібно, щоб автотік стартував саме в ці
        # кілька секунд), обидва торкаються paths.DB_PATH, а відновлення -
        # сирий перезапис файлу, не через SQLite. Прапорець виставляється
        # ДО закриття self.store (тут, на головному потоці, без гонки).
        self._backup_restore_in_progress = True
        self.store.close()

        progress_window = tk.Toplevel(self.backup_window)
        progress_window.title("Восстановление")
        progress_window.transient(self.backup_window)
        progress_window.grab_set()
        progress_window.resizable(False, False)
        progress_window.protocol("WM_DELETE_WINDOW", lambda: None)
        progress_window.configure(bg=self._tk_color(COLOR_BG))
        tk.Label(
            progress_window, text="Восстановление базы данных, подождите...",
            fg=self._tk_color(COLOR_TEXT), bg=self._tk_color(COLOR_BG), padx=24,
        ).pack(pady=(20, 10))
        progress_bar = ttk.Progressbar(progress_window, mode="indeterminate", length=280)
        progress_bar.pack(padx=24, pady=(0, 20))
        progress_bar.start(12)

        def worker():
            try:
                restore_db_snapshot(paths.DB_PATH, snapshot_path)
                # Реальна знахідка (аудит коду, 2026-08-16): цей виклик
                # раніше стояв ПОЗА try/except - сама база вже відновлена
                # правильно на цьому кроці, але помилка тут (напр. Excel-
                # файл заблокований/диск повний) вилітала неспійманою з
                # daemon-потоку: жоден finish_*/success не викликався, тож
                # progress_window (grab_set + вимкнене закриття) лишалось
                # заблокованим НАЗАВЖДИ, self.store - закритим, а
                # _backup_restore_in_progress - True до кінця сесії. Єдиний
                # вихід був - примусово вбити програму.
                regenerate_excel_after_restore(paths.DB_PATH)
            except Exception as exc:
                error_text = str(exc)
                self._run_on_main_thread(lambda: finish_error(error_text))
                return
            self._run_on_main_thread(finish_success)

        def finish_error(error_text):
            progress_bar.stop()
            progress_window.destroy()
            self.store = ExcelSqliteStore(paths.DB_PATH)
            self._backup_restore_in_progress = False
            messagebox.showerror("Резервные копии Excel", error_text, parent=self.backup_window)

        def finish_success():
            progress_bar.stop()
            progress_window.destroy()
            self._backup_restore_in_progress = False
            messagebox.showinfo(
                "Восстановлено",
                "База восстановлена. Приложение сейчас закроется — запустите его снова.",
                parent=self.backup_window,
            )
            self._on_exit_clicked()

        threading.Thread(target=worker, daemon=True).start()

    def _on_backup_now_clicked(self):
        self.backup_now_button.configure(state="disabled", text="Создание копии...")

        def worker():
            error = None
            snapshot_path = None
            try:
                snapshot_path = create_db_snapshot(paths.DB_PATH)
            except Exception as exc:
                error = str(exc)
            if snapshot_path and not error:
                self._mirror_backup_to_onedrive(snapshot_path, "db_backups", "app_data_*")
            self._run_on_main_thread(lambda: self._on_backup_now_finished(error))

        threading.Thread(target=worker, daemon=True).start()

    def _on_backup_now_finished(self, error):
        self.backup_now_button.configure(state="normal", text="Создать резервную копию сейчас")
        self._refresh_backup_lists()
        if error:
            messagebox.showerror("AI Automation", f"Не удалось создать резервную копию: {error}")

    # Задача користувача (2026-08-15): "має писати під цією кнопкою адресу
    # до ключа і саму назву ключа (без самого ключа)" - той самий принцип,
    # що вже й _build_data_settings_section нижче (підпис ПІД конкретним
    # рядком "ТГ ключ", не під усією карткою) - лише шлях до файлу, НІКОЛИ
    # сам вміст токена.
    # Задача користувача (2026-08-17): "підготуй нове оновлення для
    # автозапуску... покажи як це виглядатиме у нашому стилі" - макет
    # погоджено (окрема секція "Автозапуск" перед "Бот", CTkSwitch
    # праворуч замість кнопки-навігації, той самий стиль картки, що й
    # решта Настроек). is_enabled() читається СВІЖО з реєстру щоразу при
    # відкритті екрана (settings_frame перебудовується наново, той самий
    # принцип, що й excel_source.current_source_label() поруч) - реальний
    # стан реєстру лишається єдиним джерелом правди, без окремого прапорця
    # в settings.json, який міг би розійтись (напр. якщо хтось прибрав
    # автозапуск через штатні "Параметры автозагрузки" Windows).
    def _build_autostart_settings_section(self, parent):
        ctk.CTkLabel(parent, text="Автозапуск", font=("", 12), text_color=COLOR_TEXT_MUTED).pack(anchor="w", pady=(0, 6))
        card = ctk.CTkFrame(parent, fg_color=COLOR_CARD, corner_radius=10)
        card.pack(fill="x", pady=(0, 16))

        row = ctk.CTkFrame(card, fg_color=COLOR_ROW, corner_radius=10)
        row.pack(fill="x", padx=1, pady=1)

        left = ctk.CTkFrame(row, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True, padx=(14, 8), pady=10)
        icon_row = ctk.CTkFrame(left, fg_color="transparent")
        icon_row.pack(anchor="w")
        ctk.CTkLabel(icon_row, text="", image=_load_icon("power")).pack(side="left")
        ctk.CTkLabel(
            icon_row, text="Запуск с Windows", font=("", 13), text_color=COLOR_TEXT,
        ).pack(side="left", padx=(10, 0))
        ctk.CTkLabel(
            left, text="Программа запустится сама при включении компьютера",
            font=("", 10), text_color=COLOR_TEXT_MUTED, anchor="w", justify="left", wraplength=220,
        ).pack(anchor="w", pady=(3, 0))

        self._autostart_switch_var = ctk.IntVar(value=1 if autostart.is_enabled() else 0)
        ctk.CTkSwitch(
            row, text="", variable=self._autostart_switch_var, onvalue=1, offvalue=0,
            command=self._on_autostart_toggle_clicked, width=36,
        ).pack(side="right", padx=(0, 14))

        ctk.CTkFrame(card, height=1, fg_color=COLOR_DIVIDER).pack(fill="x")

        # Задача користувача (2026-08-17): "якщо програма закриється - то
        # щоб запустилась знову, якщо включений ПК. щоб сам віндовс її
        # запускав чи намагався запустити" - другий рядок тієї самої
        # картки (той самий стиль/CTkSwitch, що й "Запуск с Windows" вище).
        row2 = ctk.CTkFrame(card, fg_color=COLOR_ROW, corner_radius=10)
        row2.pack(fill="x", padx=1, pady=(0, 1))

        left2 = ctk.CTkFrame(row2, fg_color="transparent")
        left2.pack(side="left", fill="x", expand=True, padx=(14, 8), pady=10)
        icon_row2 = ctk.CTkFrame(left2, fg_color="transparent")
        icon_row2.pack(anchor="w")
        ctk.CTkLabel(icon_row2, text="", image=_load_icon("refresh")).pack(side="left")
        ctk.CTkLabel(
            icon_row2, text="Перезапускать при закрытии", font=("", 13), text_color=COLOR_TEXT,
        ).pack(side="left", padx=(10, 0))
        ctk.CTkLabel(
            left2, text="Если программа закроется неожиданно - запустится снова сама",
            font=("", 10), text_color=COLOR_TEXT_MUTED, anchor="w", justify="left", wraplength=220,
        ).pack(anchor="w", pady=(3, 0))

        self._watchdog_switch_var = ctk.IntVar(value=1 if watchdog_task.is_enabled() else 0)
        ctk.CTkSwitch(
            row2, text="", variable=self._watchdog_switch_var, onvalue=1, offvalue=0,
            command=self._on_watchdog_toggle_clicked, width=36,
        ).pack(side="right", padx=(0, 14))

    # frozen-guard (той самий принцип, що й code_backup.py.create_code_
    # snapshot/reports.py PDF-підпроцес) - у dev-режимі (python client_app.
    # py) sys.executable вказує на сам python.exe, запуск якого при вході
    # у Windows не запустив би цю программу, а відкрив би голий інтерпретатор.
    #
    # Взаємовиключність з "Перезапускать при закрытии" (watchdog_task.py):
    # обидва вмикають запуск при вході в Windows (Run-ключ тут, LogonTrigger
    # там був би - тепер періодична перевірка щохвилини) - разом вони дали
    # б ДВА одночасні запуски одразу після входу в Windows, а це прямий
    # шлях до подвійного опитування Telegram (той самий клас бага, що вже
    # й у watchdog телеграм-бота, project_telegram_watchdog). Увімкнення
    # одного тому вимикає інший.
    def _on_autostart_toggle_clicked(self):
        enabled = bool(self._autostart_switch_var.get())
        if enabled and not getattr(sys, "frozen", False):
            self._autostart_switch_var.set(0)
            messagebox.showerror("Автозапуск", "Автозапуск доступен только в собранной версии программы.")
            return
        try:
            if enabled:
                autostart.enable(f'"{sys.executable}"')
                if self._watchdog_switch_var.get():
                    watchdog_task.disable()
                    self._watchdog_switch_var.set(0)
            else:
                autostart.disable()
        except OSError as exc:
            self._autostart_switch_var.set(0 if enabled else 1)
            messagebox.showerror("Автозапуск", f"Не удалось изменить автозапуск: {exc}")

    def _on_watchdog_toggle_clicked(self):
        enabled = bool(self._watchdog_switch_var.get())
        if enabled and not getattr(sys, "frozen", False):
            self._watchdog_switch_var.set(0)
            messagebox.showerror("Перезапуск", "Перезапуск доступен только в собранной версии программы.")
            return
        try:
            if enabled:
                watchdog_task.enable(f'"{sys.executable}" --watchdog-check')
                if self._autostart_switch_var.get():
                    autostart.disable()
                    self._autostart_switch_var.set(0)
            else:
                watchdog_task.disable()
        except OSError as exc:
            self._watchdog_switch_var.set(0 if enabled else 1)
            messagebox.showerror("Перезапуск", f"Не удалось изменить перезапуск: {exc}")

    # Задача користувача (2026-08-19, друга редакція): "саме меню вибору
    # має бути в кнопці... запихуй це меню в кнопку... варіанти мають
    # включати створення додаткових таймерів, незалежних. і також
    # видалення" - обраний варіант 5 з 5 показаних мокапів ("Список-
    # таблиця з діями"): рядок-КНОПКА "Автообновления" розгортає ПАНЕЛЬ із
    # тумблером "Включено" + списком НЕЗАЛЕЖНИХ часових вікон (кожне зі
    # своїми "з"/"до" і кнопкою видалення) + "+ Добавить временное окно".
    #
    # Дані: auto_update_windows - список {"after": "ЧЧ:ХХ", "before":
    # "ЧЧ:ХХ"} (замість двох окремих скалярних ключів попередньої версії -
    # див. _auto_update_windows нижче, з міграцією старих ключів). Час
    # "дозволено", якщо ПОТОЧНИЙ момент потрапляє в БУДЬ-ЯКЕ з вікон
    # (логічне АБО - кожен таймер справді незалежний і самодостатній).
    #
    # Розгортання/згортання панелі й списку - той самий прийом, що вже
    # перевірений і виправив реальний баг в "Історії" gui.py: Tk-Frame,
    # який ОДНОГО РАЗУ отримав великих дітей, НЕ повертає reqheight до
    # малого значення лише через destroy() дітей - тому і панель, і сам
    # список ЩОРАЗУ знищуються ПОВНІСТЮ (не лише їхній вміст) і будуються
    # заново як свіжий порожній контейнер.
    _AUTO_UPDATE_TIME_RE = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')
    _AUTO_UPDATE_DEFAULT_AFTER = "19:00"
    _AUTO_UPDATE_DEFAULT_BEFORE = "08:00"

    def _auto_update_windows(self):
        raw = self.settings.get("auto_update_windows")
        if isinstance(raw, list):
            cleaned = [
                {"after": w.get("after"), "before": w.get("before")}
                for w in raw
                if isinstance(w, dict) and w.get("after") and w.get("before")
            ]
            if cleaned:
                return cleaned
        # Міграція: попередня версія (client-v0.2.73-0.2.75) зберігала
        # ОДНЕ вікно двома скалярними ключами - якщо список ще не
        # створений, але старі ключі є, підхоплюємо їх як перший таймер
        # замість того, щоб мовчки загубити вже налаштований час.
        legacy_after = self.settings.get("auto_update_after")
        legacy_before = self.settings.get("auto_update_before")
        if legacy_after and legacy_before:
            return [{"after": legacy_after, "before": legacy_before}]
        return []

    def _build_auto_update_settings_section(self, parent):
        ctk.CTkLabel(parent, text="Автообновления", font=("", 12), text_color=COLOR_TEXT_MUTED).pack(
            anchor="w", pady=(0, 6)
        )
        card = ctk.CTkFrame(parent, fg_color=COLOR_CARD, corner_radius=10)
        card.pack(fill="x", pady=(0, 16))
        self._build_row_button(card, "refresh", "Автообновления", self._open_auto_update_window, first=True)
        # Задача користувача (2026-08-19): "тестова версія програми, де ми
        # будемо тестувати все спершу, а далі вже випускати оновлення" -
        # той самий singleton-Toplevel принцип, окрема кнопка в тій самій
        # картці ("Обновления"), бо семантично поруч з "Автообновления".
        self._build_row_button(card, "refresh", "Канал обновлений", self._open_update_channel_window)

    # Задача користувача (2026-08-19, третя редакція): "не працює на
    # клієнті кнопка. вона має спливаюче вікно відкривати з
    # налаштуваннями" - перший варіант (розгортання панелі на місці,
    # усередині Настроек) не відповідав очікуваному вигляду; замінено на
    # той самий singleton-Toplevel принцип, що вже й backup_window/
    # table_format_window/custom_buttons_window у цьому файлі.
    def _open_auto_update_window(self):
        if self.auto_update_window is not None and self.auto_update_window.winfo_exists():
            self.auto_update_window.deiconify()
            self.auto_update_window.lift()
            self.auto_update_window.focus_force()
            return
        window = tk.Toplevel(self)
        window.title("Автообновления")
        window.geometry("380x420")
        window.configure(bg=self._tk_color(COLOR_BG))
        self.auto_update_window = window

        top = ctk.CTkFrame(window, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(16, 4))
        ctk.CTkLabel(top, text="Автообновления", font=("", 16, "bold"), text_color=COLOR_TEXT).pack(side="left")

        body = ctk.CTkScrollableFrame(window, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=(4, 16))
        self._render_auto_update_panel(body)

    def _render_auto_update_panel(self, panel):
        inner = ctk.CTkFrame(panel, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=12)

        master_row = ctk.CTkFrame(inner, fg_color="transparent")
        master_row.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(master_row, text="Включено", font=("", 13), text_color=COLOR_TEXT).pack(side="left")
        enabled_var = ctk.IntVar(value=1 if self.settings.get("auto_update_enabled") else 0)

        def on_master_toggle():
            # persist-all-state - жодної кнопки "Зберегти".
            self.settings.set("auto_update_enabled", bool(enabled_var.get()))

        ctk.CTkSwitch(
            master_row, text="", variable=enabled_var, onvalue=1, offvalue=0,
            command=on_master_toggle, width=36,
        ).pack(side="right")

        ctk.CTkLabel(
            inner, text="РАЗРЕШЁННОЕ ВРЕМЯ", font=("", 10), text_color=COLOR_TEXT_MUTED,
        ).pack(anchor="w", pady=(0, 6))

        list_state = {"frame": None}

        def render_list():
            if list_state["frame"] is not None:
                list_state["frame"].destroy()
            new_frame = ctk.CTkFrame(inner, fg_color="transparent")
            new_frame.pack(fill="x")
            list_state["frame"] = new_frame

            windows = self._auto_update_windows()
            for index, window in enumerate(windows):
                self._build_auto_update_timer_row(new_frame, index, window, render_list)

            ctk.CTkButton(
                new_frame, text="+  Добавить временное окно", anchor="w",
                fg_color="transparent", text_color=COLOR_UPDATE_BLUE, hover_color=COLOR_HOVER,
                height=28, command=on_add,
            ).pack(fill="x", pady=(2, 0))

        def on_add():
            windows = self._auto_update_windows()
            windows.append({"after": self._AUTO_UPDATE_DEFAULT_AFTER, "before": self._AUTO_UPDATE_DEFAULT_BEFORE})
            self.settings.set("auto_update_windows", windows)
            render_list()

        render_list()

    def _build_auto_update_timer_row(self, parent, index, window, on_changed):
        row = ctk.CTkFrame(parent, fg_color=COLOR_ROW, corner_radius=8)
        row.pack(fill="x", pady=(0, 6))

        fields = ctk.CTkFrame(row, fg_color="transparent")
        fields.pack(side="left", padx=(10, 4), pady=6)

        after_var = ctk.StringVar(value=window.get("after") or self._AUTO_UPDATE_DEFAULT_AFTER)
        after_entry = ctk.CTkEntry(fields, textvariable=after_var, width=52, justify="center")
        after_entry.pack(side="left")
        ctk.CTkLabel(fields, text="–", font=("", 12), text_color=COLOR_TEXT_MUTED).pack(side="left", padx=6)
        before_var = ctk.StringVar(value=window.get("before") or self._AUTO_UPDATE_DEFAULT_BEFORE)
        before_entry = ctk.CTkEntry(fields, textvariable=before_var, width=52, justify="center")
        before_entry.pack(side="left")

        def save_time(event=None):
            self._on_auto_update_window_time_changed(index, after_var, before_var)

        after_entry.bind("<FocusOut>", save_time)
        before_entry.bind("<FocusOut>", save_time)

        def delete_row():
            windows = self._auto_update_windows()
            if 0 <= index < len(windows):
                del windows[index]
                self.settings.set("auto_update_windows", windows)
            on_changed()

        ctk.CTkButton(
            row, text="✕", width=28, height=24, fg_color="transparent",
            text_color=COLOR_STOP_TEXT, hover_color=COLOR_HOVER, command=delete_row,
        ).pack(side="right", padx=8)

    def _on_auto_update_window_time_changed(self, index, after_var, before_var):
        windows = self._auto_update_windows()
        if not (0 <= index < len(windows)):
            return
        after_val = after_var.get().strip()
        before_val = before_var.get().strip()
        if not self._AUTO_UPDATE_TIME_RE.match(after_val):
            # Невалідний ввід (не ЧЧ:ХХ 24-годинний формат) - тихо
            # повертаємо останнє збережене значення, без спливаючого
            # вікна-помилки (той самий "не турбувати дрібницею" принцип,
            # що й решта фонових налаштувань цього екрана).
            after_val = windows[index].get("after") or self._AUTO_UPDATE_DEFAULT_AFTER
            after_var.set(after_val)
        if not self._AUTO_UPDATE_TIME_RE.match(before_val):
            before_val = windows[index].get("before") or self._AUTO_UPDATE_DEFAULT_BEFORE
            before_var.set(before_val)
        windows[index] = {"after": after_val, "before": before_val}
        self.settings.set("auto_update_windows", windows)

    # Задача користувача: "після і до робочого часу" - кожне вікно може
    # переходити через північ (напр. 19:00 -> 08:00, типовий випадок) або
    # лежати в межах однієї доби (напр. 12:00 -> 13:00, обідня перерва) -
    # обидва варіанти підтримані тим самим порівнянням, лише різна гілка.
    # Кілька вікон - логічне АБО: досить, щоб поточний момент потрапляв
    # ХОЧА Б В ОДНЕ з них.
    def _auto_update_window_open(self):
        if not self.settings.get("auto_update_enabled"):
            return False
        windows = self._auto_update_windows()
        if not windows:
            return False
        now = datetime.now()
        for window in windows:
            after_str, before_str = window.get("after"), window.get("before")
            if not (
                after_str and before_str
                and self._AUTO_UPDATE_TIME_RE.match(after_str)
                and self._AUTO_UPDATE_TIME_RE.match(before_str)
            ):
                continue
            after_h, after_m = (int(x) for x in after_str.split(":"))
            before_h, before_m = (int(x) for x in before_str.split(":"))
            after_t = now.replace(hour=after_h, minute=after_m, second=0, microsecond=0)
            before_t = now.replace(hour=before_h, minute=before_m, second=0, microsecond=0)
            if after_t <= before_t:
                if after_t <= now < before_t:
                    return True
            elif now >= after_t or now < before_t:
                return True
        return False

    # Задача користувача (2026-08-19): "тестова версія програми, де ми
    # будемо тестувати все спершу, а далі вже випускати оновлення" - канал
    # "Тестова" бачить УСІ релізи (тестові й стабільні, завжди справді
    # найновіший), "Стабільна" (за замовчуванням) - лише не-тестові.
    # Прочитується щоразу свіжо з self.settings (той самий підхід, що й
    # решта прапорців тут) - _check_for_update_now читає це напряму, без
    # кешування, тож зміна діє з наступної ж перевірки оновлень.
    def _open_update_channel_window(self):
        if self.update_channel_window is not None and self.update_channel_window.winfo_exists():
            self.update_channel_window.deiconify()
            self.update_channel_window.lift()
            self.update_channel_window.focus_force()
            return
        window = tk.Toplevel(self)
        window.title("Канал обновлений")
        window.geometry("380x260")
        window.configure(bg=self._tk_color(COLOR_BG))
        self.update_channel_window = window

        top = ctk.CTkFrame(window, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(16, 4))
        ctk.CTkLabel(top, text="Канал обновлений", font=("", 16, "bold"), text_color=COLOR_TEXT).pack(side="left")

        ctk.CTkLabel(
            window,
            text=(
                "«Тестовая» — устанавливает и тестовые, и обычные обновления "
                "(всегда самое новое). «Стабильная» — только проверенные."
            ),
            font=("", 11), text_color=COLOR_TEXT_MUTED, justify="left", wraplength=340,
        ).pack(fill="x", padx=16, pady=(0, 12))

        body = ctk.CTkFrame(window, fg_color=COLOR_CARD, corner_radius=10)
        body.pack(fill="x", padx=16)

        channel_var = ctk.StringVar(
            value="test" if self.settings.get("update_channel") == "test" else "stable"
        )

        # Задача користувача (2026-08-19): "на зміну додай можливість
        # поставити пароль" (варіант 4 з 5 показаних мокапів - "Попередження
        # + пароль... з'являється на спробі перемкнути"). Перемикання назад
        # на "Стабільна" (безпечний напрямок) лишається миттєвим - пароль
        # питається ЛИШЕ на спробу увімкнути "Тестова" (ризикований напрямок,
        # той, що міг би поставити неперевірену збірку на робочий канал).
        # Пароль зберігається в settings.json (той самий рівень довіри, що й
        # уже наявний backup_encryption_password) - це захист від
        # випадкового кліку, не від зловмисника з доступом до файлів
        # програми. Якщо пароль ще не встановлений - перше введене значення
        # тут-таки СТАЄ паролем (і одразу підтверджує перемикання).
        warning_panel = {"frame": None}

        def hide_warning_panel():
            if warning_panel["frame"] is not None:
                warning_panel["frame"].destroy()
                warning_panel["frame"] = None

        def commit_test_channel():
            channel_var.set("test")
            self.settings.set("update_channel", "test")
            hide_warning_panel()

        def show_warning_panel():
            hide_warning_panel()
            panel = ctk.CTkFrame(body, fg_color=COLOR_ROW, corner_radius=8, border_width=1, border_color=COLOR_WARN)
            panel.pack(fill="x", padx=14, pady=(0, 14))
            warning_panel["frame"] = panel

            has_password = bool(self.settings.get("update_channel_password"))
            ctk.CTkLabel(
                panel,
                text=(
                    "⚠ Это повлияет на рабочий канал"
                    if has_password else
                    "⚠ Придумайте пароль для переключения"
                ),
                font=("", 12), text_color=COLOR_WARN,
            ).pack(anchor="w", padx=12, pady=(12, 8))

            password_var = ctk.StringVar()
            password_entry = ctk.CTkEntry(panel, placeholder_text="Пароль", show="•", textvariable=password_var)
            password_entry.pack(fill="x", padx=12, pady=(0, 6))

            error_label = ctk.CTkLabel(panel, text="", font=("", 11), text_color=COLOR_STOP_TEXT)
            error_label.pack(anchor="w", padx=12)

            def on_confirm():
                entered = password_var.get()
                if not entered:
                    error_label.configure(text="Введите пароль")
                    return
                stored_password = self.settings.get("update_channel_password")
                if not stored_password:
                    self.settings.set("update_channel_password", entered)
                    commit_test_channel()
                    return
                if entered == stored_password:
                    commit_test_channel()
                else:
                    error_label.configure(text="Неверный пароль")
                    password_var.set("")

            ctk.CTkButton(
                panel, text="Да, переключить", fg_color=COLOR_UPDATE_BLUE, hover_color=COLOR_UPDATE_BLUE,
                command=on_confirm,
            ).pack(fill="x", padx=12, pady=(0, 12))
            password_entry.bind("<Return>", lambda event: on_confirm())

        def on_channel_changed():
            if channel_var.get() == "test":
                # Радіо вже візуально "клацнуло" на Тестова (customtkinter
                # сам оновлює variable до command) - поки не підтверджено
                # паролем, повертаємо назад на Стабільна й показуємо
                # попередження замість реального перемикання.
                channel_var.set("stable")
                show_warning_panel()
            else:
                self.settings.set("update_channel", "stable")
                hide_warning_panel()

        ctk.CTkRadioButton(
            body, text="Стабильная", variable=channel_var, value="stable",
            text_color=COLOR_TEXT, command=on_channel_changed,
        ).pack(anchor="w", padx=14, pady=(14, 6))
        ctk.CTkRadioButton(
            body, text="Тестовая", variable=channel_var, value="test",
            text_color=COLOR_TEXT, command=on_channel_changed,
        ).pack(anchor="w", padx=14, pady=(0, 14))

    def _build_bot_settings_section(self, parent):
        ctk.CTkLabel(parent, text="Бот", font=("", 12), text_color=COLOR_TEXT_MUTED).pack(anchor="w", pady=(0, 6))
        card = ctk.CTkFrame(parent, fg_color=COLOR_CARD, corner_radius=10)
        card.pack(fill="x", pady=(0, 16))
        self._build_row_button(card, "key", "ТГ ключ", self._choose_telegram_token_file, first=True)
        token_file = self.settings.get("telegram_token_file")
        ctk.CTkLabel(
            card,
            text=token_file if token_file else "Файл токена ещё не выбран.",
            font=("", 10),
            text_color=COLOR_TEXT_MUTED,
            anchor="w",
            justify="left",
            wraplength=260,
        ).pack(fill="x", padx=(14, 10), pady=(0, 6))
        ctk.CTkFrame(card, height=1, fg_color=COLOR_DIVIDER).pack(fill="x")
        # Задача користувача (2026-08-17): "додамо в налаштування ID чату,
        # щоб через файл приєднувало тхт" - REPORT_BROADCAST_CHAT_ID
        # (paths.py) досі був захардкоджений у коді - зміна чату для
        # дублю звітів вимагала правки коду й нового релізу. Той самий
        # принцип, що й "ТГ ключ" вище - шлях до .txt-файлу зберігається в
        # налаштуваннях, сам ID читається з нього щоразу свіжо
        # (_notify_report_broadcast, telegram_dialog_core.py), а не один
        # раз при старті. Якщо файл не обрано - лишається старий
        # захардкоджений REPORT_BROADCAST_CHAT_ID як запасний варіант
        # (paths.py), тож нічого не ламається для тих, хто ще не встиг
        # обрати файл.
        self._build_row_button(card, "document", "ID чата", self._choose_report_chat_id_file)
        chat_id_file = self.settings.get("report_chat_id_file")
        ctk.CTkLabel(
            card,
            text=chat_id_file if chat_id_file else "Файл с ID чата ещё не выбран (используется значение по умолчанию).",
            font=("", 10),
            text_color=COLOR_TEXT_MUTED,
            anchor="w",
            justify="left",
            wraplength=260,
        ).pack(fill="x", padx=(14, 10), pady=(0, 6))
        ctk.CTkFrame(card, height=1, fg_color=COLOR_DIVIDER).pack(fill="x")
        self._build_row_button(card, "power", self._bot_toggle_label(), self._on_bot_toggle_row_clicked)
        ctk.CTkFrame(card, height=1, fg_color="transparent").pack(fill="x", pady=(0, 1))

    # Задача користувача (2026-08-14, скріншот): "під таблицею щоб писало
    # яка зараз використовується наразі таблиця. ненав'язливо. сірим" -
    # окремий (не через generic _build_settings_section) будівельник саме
    # для секції "Данные", бо підпис має йти ПІД конкретним рядком "Таблица
    # Excel", а не під усією карткою - той самий текст, що вже показує
    # excel_source.current_source_label() (gui.py, той самий підпис).
    # Екран настройок перебудовується наново щоразу при відкритті
    # (_open_settings_screen руйнує й перестворює settings_frame), тож
    # текст рахується заново тут - жодного окремого StringVar не треба.
    def _build_data_settings_section(self, parent):
        ctk.CTkLabel(parent, text="Данные", font=("", 12), text_color=COLOR_TEXT_MUTED).pack(anchor="w", pady=(0, 6))
        card = ctk.CTkFrame(parent, fg_color=COLOR_CARD, corner_radius=10)
        card.pack(fill="x", pady=(0, 16))
        self._build_row_button(card, "document", "Таблица Excel", self._choose_excel_file, first=True)
        ctk.CTkLabel(
            card,
            text=excel_source.current_source_label(),
            font=("", 10),
            text_color=COLOR_TEXT_MUTED,
            anchor="w",
            justify="left",
            wraplength=260,
        ).pack(fill="x", padx=(14, 10), pady=(0, 6))
        ctk.CTkFrame(card, height=1, fg_color=COLOR_DIVIDER).pack(fill="x")
        # Задача користувача (2026-08-14): "вирівняти таблицю" - рядок
        # відкриває екран редагування формату (колір/шрифт/ширина + живе
        # прев'ю, макет "1. Розділений вигляд" з 5 показаних варіантів),
        # а не одразу застосовує - сама дія "Вирівняти" тепер кнопка
        # ВСЕРЕДИНІ того екрану (_open_table_format_window нижче).
        # Значка "align"/"table" в icons/ поки немає - тимчасово document
        # (та сама, що й у "Таблица Excel" - обидві про таблицю).
        self._build_row_button(card, "document", "Формат таблицы", self._open_table_format_window)
        ctk.CTkFrame(card, height=1, fg_color=COLOR_DIVIDER).pack(fill="x")
        # Задача користувача (2026-08-15): "додай туди кнопку оновити
        # екселі" - той самий self._on_refresh_excel_clicked, що й у
        # головному меню; self.refresh_excel_button і далі стежить лише
        # за кнопкою з головного меню (текст "Обновление..."/блокування)
        # - тут окремого відстеження нема, але спільний прапорець
        # _excel_refresh_in_progress все одно захищає від подвійного
        # запуску незалежно від того, яку з двох кнопок натиснули.
        self._build_row_button(card, "refresh", "Обновить эксели", self._on_refresh_excel_clicked)
        ctk.CTkFrame(card, height=1, fg_color="transparent").pack(fill="x", pady=(0, 1))


    def _align_excel_table(self, trigger_button=None):
        confirmed = messagebox.askyesno(
            "Выровнять таблицу",
            "Ничего не будет удалено. Будут лишь сняты активные фильтры и "
            "выровнены строки и столбцы 5 управляемых листов (СКЛАД, ПРИХОД, "
            "ПРОДАЖА, СПИСАНИЕ, АНТИСЕПТИРОВАНИЕ) под единый стандарт. "
            "Продолжить?",
        )
        if not confirmed:
            return
        # Реальна знахідка (аудит коду, 2026-08-16): форматування великих
        # листів openpyxl на головному потоці "заморожує" вікно на весь час
        # роботи - той самий фон-потік + _run_on_main_thread паттерн, що вже
        # використовується для решти файлового I/O в цьому класі.
        if trigger_button is not None:
            trigger_button.configure(state="disabled")

        def worker():
            error = None
            try:
                apply_standard_table_format(self.settings)
            except Exception as exc:
                error = str(exc)

            def finish():
                if trigger_button is not None:
                    trigger_button.configure(state="normal")
                if error:
                    messagebox.showerror("Выровнять таблицу", error)
                else:
                    messagebox.showinfo("Выровнять таблицу", "Таблица выровнена.")

            self._run_on_main_thread(finish)

        threading.Thread(target=worker, daemon=True).start()

    # Задача користувача: "роби точно так" - в еталонному ескізі "Вкл.
    # бот"/"Выкл. бот" це ОДИН рядок (не дві окремі кнопки поруч) - підпис
    # сам відображає дію, доступну зараз (протилежну поточному стану).
    def _bot_toggle_label(self):
        worker = self.telegram_worker
        running = bool(worker and worker.thread and worker.thread.is_alive())
        return "Выкл. бот" if running else "Вкл. бот"

    def _on_bot_toggle_row_clicked(self):
        worker = self.telegram_worker
        running = bool(worker and worker.thread and worker.thread.is_alive())
        if running:
            self._on_stop_clicked()
        else:
            self._on_start_clicked()
        self._open_settings_screen()

    def _choose_telegram_token_file(self):
        selected_file = filedialog.askopenfilename(
            title="Выберите txt-файл с Telegram-токеном",
            filetypes=(("Text files", "*.txt"), ("All files", "*.*")),
        )
        if not selected_file:
            return
        self.settings.set("telegram_token_file", selected_file)
        self._open_settings_screen()
        self._on_restart_clicked()

    # Той самий принцип, що й _choose_telegram_token_file вище - лише шлях
    # до файлу зберігається в налаштуваннях, сам ID чату НЕ перечитується
    # тут (не показується, не парситься) - фактичне читання/парсинг лише
    # в _notify_report_broadcast (telegram_dialog_core.py), у момент
    # реальної відправки звіту. Не вимагає перезапуску бота (на відміну
    # від токена) - файл читається щоразу свіжо.
    def _choose_report_chat_id_file(self):
        selected_file = filedialog.askopenfilename(
            title="Выберите txt-файл с ID чата для дублей отчётов",
            filetypes=(("Text files", "*.txt"), ("All files", "*.*")),
        )
        if not selected_file:
            return
        self.settings.set("report_chat_id_file", selected_file)
        self._open_settings_screen()

    # Задача користувача (2026-08-14, одразу після file-scoped-isolation
    # фікса): "давай тепер зробимо коли новий підключаємо файл щоб
    # питало підтвердження" — той самий excel_source.is_real_source_switch,
    # що й gui.py's save_source, лише через messagebox.askyesno (тут нема
    # self._t()/i18n-обгортки — client_app.py й так увесь написаний
    # прямим російським текстом).
    def _choose_excel_file(self):
        selected_file = filedialog.askopenfilename(
            title="Выберите файл Excel",
            filetypes=(("Excel files", "*.xlsx"), ("All files", "*.*")),
        )
        if not selected_file:
            return
        new_identity = f"local:{selected_file}"
        if excel_source.is_real_source_switch(new_identity):
            confirmed = messagebox.askyesno(
                "Таблица Excel",
                "Вы подключаете другой файл. Нумерация документов, подсказки "
                "«последние использованные», выученные имена клиентов и "
                "история движений (приход/продажа/списание/антисептирование) "
                "относятся только к файлу, который был подключён раньше, и "
                "начнутся заново для нового файла. Само содержимое таблиц это "
                "не затрагивает. Продолжить?",
            )
            if not confirmed:
                return
        self.settings.set("excel_source_mode", "local")
        self.settings.set("excel_local_path", selected_file)
        self._open_settings_screen()

    # Задача користувача (2026-08-14): "давай зробимо той екран
    # редагування формату" - макет "1. Розділений вигляд" (прев'ю зліва,
    # список керування праворуч), обраний із 5 показаних варіантів. Кожна
    # зміна пишеться в settings.json одразу (стандартне правило цього
    # застосунку - жодного окремого "Зберегти") - лише "Выровнять" внизу
    # ЗАСТОСОВУЄ вже збережені параметри до реального Excel-файлу
    # (_align_excel_table, той самий метод, що й раніше).
    def _open_table_format_window(self):
        if self.table_format_window is not None and self.table_format_window.winfo_exists():
            self.table_format_window.deiconify()
            self.table_format_window.lift()
            self.table_format_window.focus_force()
            return
        window = tk.Toplevel(self)
        window.title("Формат таблицы")
        window.geometry("560x320")
        window.configure(bg=self._tk_color(COLOR_BG))
        window.resizable(False, False)
        self.table_format_window = window
        self._build_table_format_window(window)

    def _build_table_format_window(self, window):
        font_size = int(self.settings.get(TABLE_FORMAT_FONT_SIZE_KEY) or TABLE_FORMAT_DEFAULT_FONT_SIZE)
        header_font_size_raw = self.settings.get(TABLE_FORMAT_HEADER_FONT_SIZE_KEY)
        header_row_height_raw = self.settings.get(TABLE_FORMAT_HEADER_ROW_HEIGHT_KEY)
        state = {
            "color": self.settings.get(TABLE_FORMAT_COLOR_KEY) or TABLE_FORMAT_DEFAULT_COLOR,
            "font_size": font_size,
            "header_font_enabled": bool(header_font_size_raw),
            "header_font_size": int(header_font_size_raw) if header_font_size_raw else font_size,
            "width_mode": self.settings.get(TABLE_FORMAT_COLUMN_WIDTH_MODE_KEY) or TABLE_FORMAT_DEFAULT_COLUMN_WIDTH_MODE,
            "width": int(self.settings.get(TABLE_FORMAT_COLUMN_WIDTH_KEY) or TABLE_FORMAT_DEFAULT_COLUMN_WIDTH),
            "header_row_height_enabled": bool(header_row_height_raw),
            "header_row_height": int(header_row_height_raw) if header_row_height_raw else 20,
            "expanded": None,
        }

        ctk.CTkLabel(window, text="Формат таблицы", font=("", 15, "bold"), text_color=COLOR_TEXT).pack(
            anchor="w", padx=16, pady=(14, 10)
        )

        body = ctk.CTkFrame(window, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        preview_card = tk.Frame(body, bg=self._tk_color(COLOR_CARD))
        preview_card.pack(side="left", fill="y", padx=(0, 10))
        preview_frame = tk.Frame(preview_card, bg=self._tk_color(COLOR_CARD))
        preview_frame.pack(padx=10, pady=10)

        controls = ctk.CTkFrame(body, fg_color="transparent")
        controls.pack(side="left", fill="both", expand=True)

        # Задача користувача (2026-08-14): повзунки (CTkSlider) прибрані
        # цілком - "прибери повзунок. просто хай буде цифра". Причина не
        # лише в PermissionError-краху (CTkSlider викликає command на
        # кожен тік перетягування, десятки разів на секунду - навіть з
        # дебаунсом лишався клас проблем, яких просте текстове поле не
        # має взагалі). Тепер - CTkEntry із застосуванням значення ЛИШЕ по
        # Enter або втраті фокуса (не на кожен символ), з валідацією діа-
        # пазону; невалідне значення відкочується до попереднього.
        def make_number_field(parent, initial_value, min_value, max_value, on_commit):
            entry = ctk.CTkEntry(parent, width=56, justify="right")
            entry.insert(0, str(initial_value))
            last_valid = [initial_value]

            def commit(_event=None):
                text = entry.get().strip()
                try:
                    value = int(text)
                except ValueError:
                    value = last_valid[0]
                value = max(min_value, min(max_value, value))
                last_valid[0] = value
                entry.delete(0, "end")
                entry.insert(0, str(value))
                on_commit(value)

            entry.bind("<Return>", commit)
            entry.bind("<FocusOut>", commit)
            return entry

        def redraw_preview():
            for child in preview_frame.winfo_children():
                child.destroy()
            headers = ["№", "Продукт"]
            rows = [["1", "Доска"], ["2", "Брус"]]
            color_hex = f"#{state['color']}"
            font = ("Segoe UI", state["font_size"])
            header_size = state["header_font_size"] if state["header_font_enabled"] else state["font_size"]
            header_font = ("Segoe UI", header_size, "bold")
            # Задача користувача (той самий скріншот): реальна ширина
            # стовпця Excel (8-40) НАПРЯМУ як ширина tk.Label ламала
            # верстку - прев'ю розросталось ширше за фіксоване вікно й
            # виштовхувало панель керування праворуч ("прев'ю їздить").
            # Прев'ю - лише орієнтовне "вужче/ширше", не пікселно точне,
            # тож свідомо затиснуте у вузький видимий діапазон.
            cell_width = min(14, max(6, state["width"] // 3)) if state["width_mode"] == "fixed" else None
            # Задача користувача: висота рядка заголовків - ОКРЕМА вісь
            # від розміру шрифту (Excel-пункти, 10-100) - прев'ю показує
            # її як вертикальний відступ клітинки заголовка, теж
            # затиснутий у безпечний видимий діапазон, тим самим
            # принципом, що й cell_width вище.
            header_pady = (
                min(16, max(3, (state["header_row_height"] - 15) // 4))
                if state["header_row_height_enabled"] else 3
            )
            # Задача користувача: "текст в заголовках має бути завжди по
            # центру" - anchor="center" лише для заголовка (не для
            # рядків даних нижче, ті лишаються зліва, як і в Excel).
            # Задача користувача (2026-08-15): "внутрішні жирні лінії.
            # зроби їх тоншими. зовнішні залиш" - товста рамка тепер лише
            # по зовнішньому периметру реальної таблиці, внутрішні лінії
            # тонкі (сам движок нижче в _apply_table_format_to_worksheet
            # рахує це по позиції клітинки). tk.Label.highlightthickness
            # єдиний на всі 4 сторони віджета - немає способу показати
            # "товсто зовні/тонко всередині" на рівні ОДНОГО tk.Label у
            # цьому маленькому прев'ю, тож узято тонку (1) як домінантне
            # враження - у реальній таблиці внутрішніх ліній набагато
            # більше, ніж зовнішніх.
            for col_index, header in enumerate(headers):
                tk.Label(
                    preview_frame, text=header, font=header_font, fg="#FFFFFF", bg=color_hex,
                    highlightbackground=color_hex, highlightthickness=1,
                    padx=6, pady=header_pady, width=cell_width, anchor="center", justify="center",
                ).grid(row=0, column=col_index, sticky="nsew")
            # Задача користувача: "за замовчуванням у вирівнюванні також
            # у всіх клітинках текст по центру" - дані тепер теж по
            # центру (anchor="center"), не лише заголовок.
            for row_index, row_values in enumerate(rows, start=1):
                for col_index, value in enumerate(row_values):
                    tk.Label(
                        preview_frame, text=value, font=font, fg="#20242A", bg="#FFFFFF",
                        highlightbackground=color_hex, highlightthickness=1,
                        padx=6, pady=3, width=cell_width, anchor="center", justify="center",
                    ).grid(row=row_index, column=col_index, sticky="nsew")

        def build_row(parent, label_text, key):
            is_expanded = state["expanded"] == key
            row = ctk.CTkButton(
                parent, text=label_text, anchor="w",
                fg_color=COLOR_HOVER if is_expanded else COLOR_ROW, text_color=COLOR_TEXT,
                hover_color=COLOR_HOVER, corner_radius=6, height=38, font=("", 12.5),
                command=lambda: set_expanded(key),
            )
            row.pack(fill="x", pady=(0, 1))

        def set_expanded(key):
            state["expanded"] = None if state["expanded"] == key else key
            rebuild_controls()

        def choose_color(hex_value):
            state["color"] = hex_value
            self.settings.set(TABLE_FORMAT_COLOR_KEY, hex_value)
            redraw_preview()
            rebuild_controls()

        def rebuild_controls():
            for child in controls.winfo_children():
                child.destroy()

            build_row(controls, "Колір", "color")
            if state["expanded"] == "color":
                swatch_row = ctk.CTkFrame(controls, fg_color="transparent")
                swatch_row.pack(fill="x", padx=8, pady=(4, 8))
                for _name, hex_value in TABLE_FORMAT_COLOR_PRESETS:
                    is_selected = hex_value.lower() == state["color"].lower()
                    swatch = tk.Label(
                        swatch_row, bg=f"#{hex_value}", width=2, height=1, cursor="hand2",
                        highlightthickness=2,
                        highlightbackground="#FFFFFF" if is_selected else self._tk_color(COLOR_BG),
                    )
                    swatch.pack(side="left", padx=3)
                    swatch.bind("<Button-1>", lambda _event, hv=hex_value: choose_color(hv))

            build_row(controls, "Шрифт", "font")
            if state["expanded"] == "font":
                font_row = ctk.CTkFrame(controls, fg_color="transparent")
                font_row.pack(fill="x", padx=8, pady=(4, 8))
                ctk.CTkLabel(font_row, text="Розмір, pt", text_color=COLOR_TEXT_MUTED, font=("", 11)).pack(
                    side="left"
                )

                def commit_font_size(value):
                    state["font_size"] = value
                    self.settings.set(TABLE_FORMAT_FONT_SIZE_KEY, value)
                    redraw_preview()

                make_number_field(font_row, state["font_size"], 8, 30, commit_font_size).pack(side="right")

                # Задача користувача (2026-08-14): "не окремої строки, а
                # її налаштування, маю на увазі" - можливість окремо
                # налаштувати шрифт САМЕ рядка заголовків, не обов'язково
                # той самий розмір, що й у рядків даних. Вимкнено
                # (типово) = заголовок далі йде за основним розміром
                # шрифту вище, як і раніше.
                header_toggle_var = ctk.BooleanVar(value=state["header_font_enabled"])

                def on_header_font_toggle():
                    state["header_font_enabled"] = header_toggle_var.get()
                    if state["header_font_enabled"]:
                        self.settings.set(TABLE_FORMAT_HEADER_FONT_SIZE_KEY, state["header_font_size"])
                    else:
                        self.settings.set(TABLE_FORMAT_HEADER_FONT_SIZE_KEY, "")
                    redraw_preview()
                    rebuild_controls()

                ctk.CTkCheckBox(
                    controls, text="Окремий розмір заголовка", variable=header_toggle_var,
                    command=on_header_font_toggle, text_color=COLOR_TEXT, font=("", 11),
                ).pack(fill="x", padx=8, pady=(0, 4))

                if state["header_font_enabled"]:
                    header_font_row = ctk.CTkFrame(controls, fg_color="transparent")
                    header_font_row.pack(fill="x", padx=8, pady=(0, 4))
                    ctk.CTkLabel(
                        header_font_row, text="Розмір заголовка, pt", text_color=COLOR_TEXT_MUTED, font=("", 11)
                    ).pack(side="left")

                    def commit_header_font_size(value):
                        state["header_font_size"] = value
                        self.settings.set(TABLE_FORMAT_HEADER_FONT_SIZE_KEY, value)
                        redraw_preview()

                    make_number_field(
                        header_font_row, state["header_font_size"], 8, 40, commit_header_font_size
                    ).pack(side="right")

            # Задача користувача (2026-08-14): "мені потрібно не це, мені
            # потрібно мати змогу розширювати сам рядок без збільшування
            # тексту в пишину і в висоту окремо ОКРЕМО" - "Ширина"
            # перейменована на "Розмір", бо тепер тут дві незалежні осі:
            # ширина СТОВПЦЯ (вже була) і висота РЯДКА заголовків (нова) -
            # обидві незалежні від розміру шрифту в "Шрифт" вище.
            build_row(controls, "Розмір", "width")
            if state["expanded"] == "width":
                width_col = ctk.CTkFrame(controls, fg_color="transparent")
                width_col.pack(fill="x", padx=8, pady=(4, 8))

                def on_width_mode_change(value):
                    state["width_mode"] = "fixed" if value == "Фіксована" else "auto"
                    self.settings.set(TABLE_FORMAT_COLUMN_WIDTH_MODE_KEY, state["width_mode"])
                    redraw_preview()
                    rebuild_controls()

                segmented = ctk.CTkSegmentedButton(
                    width_col, values=["Авто", "Фіксована"], command=on_width_mode_change
                )
                segmented.set("Фіксована" if state["width_mode"] == "fixed" else "Авто")
                segmented.pack(fill="x")
                if state["width_mode"] == "fixed":
                    width_row = ctk.CTkFrame(width_col, fg_color="transparent")
                    width_row.pack(fill="x", pady=(8, 0))
                    ctk.CTkLabel(
                        width_row, text="Ширина стовпця", text_color=COLOR_TEXT_MUTED, font=("", 11)
                    ).pack(side="left")

                    def commit_width(value):
                        state["width"] = value
                        self.settings.set(TABLE_FORMAT_COLUMN_WIDTH_KEY, value)
                        redraw_preview()

                    make_number_field(width_row, state["width"], 5, 100, commit_width).pack(side="right")

                header_height_toggle_var = ctk.BooleanVar(value=state["header_row_height_enabled"])

                def on_header_height_toggle():
                    state["header_row_height_enabled"] = header_height_toggle_var.get()
                    if state["header_row_height_enabled"]:
                        self.settings.set(TABLE_FORMAT_HEADER_ROW_HEIGHT_KEY, state["header_row_height"])
                    else:
                        self.settings.set(TABLE_FORMAT_HEADER_ROW_HEIGHT_KEY, "")
                    redraw_preview()
                    rebuild_controls()

                ctk.CTkCheckBox(
                    width_col, text="Висота заголовка", variable=header_height_toggle_var,
                    command=on_header_height_toggle, text_color=COLOR_TEXT, font=("", 11),
                ).pack(fill="x", pady=(12, 0))

                if state["header_row_height_enabled"]:
                    height_row = ctk.CTkFrame(width_col, fg_color="transparent")
                    height_row.pack(fill="x", pady=(6, 0))
                    ctk.CTkLabel(
                        height_row, text="Висота, пт", text_color=COLOR_TEXT_MUTED, font=("", 11)
                    ).pack(side="left")

                    def commit_header_row_height(value):
                        state["header_row_height"] = value
                        self.settings.set(TABLE_FORMAT_HEADER_ROW_HEIGHT_KEY, value)
                        redraw_preview()

                    make_number_field(
                        height_row, state["header_row_height"], 10, 100, commit_header_row_height
                    ).pack(side="right")

        redraw_preview()
        rebuild_controls()

        bottom = ctk.CTkFrame(window, fg_color="transparent")
        bottom.pack(fill="x", padx=16, pady=(4, 16))
        align_button = ctk.CTkButton(bottom, text="Выровнять")
        align_button.configure(command=lambda: self._align_excel_table(align_button))
        align_button.pack(side="right")

        window.protocol("WM_DELETE_WINDOW", window.destroy)

    # ---------- Журнал действий (перенесено з gui.py, мінімальна версія) ----------
    # Задача користувача: "потрібно ще все інше доробити" - реальний перегляд
    # журналу дій бота (не заглушка). Простий tk.Toplevel (НЕ ctk.CTkToplevel
    # і БЕЗ .transient()) - той самий висновок, що вже закритий для Журналів/
    # Персоналу в gui.py: .transient() на немодальному вікні спричиняв
    # реальний баг z-order (вікно миттю опинялось позаду головного). "Журнал
    # виконаних робіт" (dev work log) свідомо НЕ переносимо - це внутрішні
    # нотатки розробника, не потрібні бізнес-клієнту.
    def _open_journals_window(self):
        if self.journals_window is not None and self.journals_window.winfo_exists():
            self.journals_window.deiconify()
            self.journals_window.lift()
            self.journals_window.focus_force()
            self._refresh_action_log()
            return
        window = tk.Toplevel(self)
        window.title("Журнал действий")
        window.geometry("820x560")
        window.configure(bg=self._tk_color(COLOR_BG))
        self.journals_window = window
        self._build_journals_window(window)

    def _build_journals_window(self, window):
        top = ctk.CTkFrame(window, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(16, 8))
        ctk.CTkLabel(top, text="Журнал действий", font=("", 16, "bold"), text_color=COLOR_TEXT).pack(side="left")
        ctk.CTkButton(
            top, text="Очистить журнал", width=140, fg_color=COLOR_STOP, text_color=COLOR_STOP_TEXT,
            hover_color=COLOR_HOVER, command=self._on_clear_action_log_clicked,
        ).pack(side="right")
        ctk.CTkButton(top, text="Обновить", width=100, command=self._refresh_action_log).pack(side="right", padx=(0, 8))

        self.journals_list_frame = ctk.CTkScrollableFrame(window, fg_color="transparent")
        self.journals_list_frame.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self._refresh_action_log()

    # Реальний баг (2026-08-13): "дуже лагає" - до 200 рядків по 5 CTk-
    # віджетів (CTkFrame/CTkLabel/CTkButton) кожен - CustomTkinter суттєво
    # важчий за звичайний tk (кожен заокруглений кут - окреме canvas-
    # малювання), сотні таких віджетів синхронно на відкритті/оновленні
    # відчутно "підвисають" інтерфейс. Рядки списку тепер звичайні tk-
    # віджети (та сама логіка, що вже й у самому gui.py - там теж plain
    # tk.Frame/tk.Label/tk.Button для journal/personnel рядків, не ttk чи
    # CTk), а не 200 - ліміт 50 за раз, з кнопкою "Показать ещё".
    def _refresh_action_log(self):
        if self.journals_list_frame is None:
            return
        for child in self.journals_list_frame.winfo_children():
            child.destroy()
        rows = self.store.list_action_log(self._journals_fetch_limit)
        if not rows:
            tk.Label(
                self.journals_list_frame, text="Журнал действий пока пуст.",
                fg=self._tk_color(COLOR_TEXT_MUTED), bg=self._tk_color(COLOR_BG),
            ).pack(anchor="w", pady=8)
            return
        for log_id, action_type, details_json, created_at in rows:
            self._build_action_log_row(log_id, action_type, details_json, created_at)
        if len(rows) >= self._journals_fetch_limit:
            tk.Button(
                self.journals_list_frame, text="Показать ещё", command=self._on_show_more_logs_clicked,
            ).pack(pady=8)

    def _on_show_more_logs_clicked(self):
        self._journals_fetch_limit += 50
        self._refresh_action_log()

    def _build_action_log_row(self, log_id, action_type, details_json, created_at):
        details = self._parse_action_log_details(details_json)
        summary = self._action_log_summary(action_type, details)
        row_bg = self._tk_color(COLOR_ROW)
        text_color = self._tk_color(COLOR_TEXT)
        muted_color = self._tk_color(COLOR_TEXT_MUTED)

        card = tk.Frame(self.journals_list_frame, bg=row_bg)
        card.pack(fill="x", pady=(0, 6))
        card.grid_columnconfigure(0, weight=1)
        card.grid_columnconfigure(1, weight=0)

        headline = f"{self._format_log_time(created_at)} — {summary['user']} — {summary['status']}"
        tk.Label(
            card, text=headline, font=("Segoe UI", 10), fg=text_color, bg=row_bg, anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(8, 0))

        buttons = tk.Frame(card, bg=row_bg)
        buttons.grid(row=0, column=1, rowspan=2, sticky="e", padx=12, pady=8)
        tk.Button(
            buttons, text="Детально", width=9, command=lambda: self._open_action_log_details(log_id),
        ).pack(side="left", padx=(0, 4))
        tk.Button(
            buttons, text="Удалить", width=9, fg="#B23B3B",
            command=lambda: self._on_delete_action_log_clicked(log_id),
        ).pack(side="left")

        detail_text = f"{summary['action']}: {summary['text']}" if summary["text"] else summary["action"]
        tk.Label(
            card, text=self._short_text(detail_text, 90), font=("Segoe UI", 9), fg=muted_color, bg=row_bg, anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=12, pady=(2, 8))

    def _on_delete_action_log_clicked(self, log_id):
        if not messagebox.askyesno("Журнал действий", f"Удалить запись журнала действий #{log_id}?", parent=self.journals_window):
            return
        self.store.delete_action_log(log_id)
        self._refresh_action_log()

    def _on_clear_action_log_clicked(self):
        if not messagebox.askyesno(
            "Журнал действий", "Удалить все записи журнала? Это действие нельзя отменить.", parent=self.journals_window,
        ):
            return
        self.store.clear_action_log()
        self._refresh_action_log()

    def _open_action_log_details(self, log_id):
        existing = self._action_log_detail_windows.get(log_id)
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            existing.focus_force()
            return
        row = self.store.get_action_log(log_id)
        if not row:
            messagebox.showinfo("Журнал действий", "Запись не найдена.")
            return
        _log_id, action_type, details_json, created_at = row
        details = self._parse_action_log_details(details_json)

        window = tk.Toplevel(self.journals_window)
        window.title(f"Детали записи #{log_id}")
        window.geometry("640x480")
        self._action_log_detail_windows[log_id] = window

        def close():
            if self._action_log_detail_windows.get(log_id) is window:
                del self._action_log_detail_windows[log_id]
            window.destroy()

        window.protocol("WM_DELETE_WINDOW", close)
        window.bind("<Escape>", lambda event: close())

        text_widget = tk.Text(window, wrap="word")
        scrollbar = ttk.Scrollbar(window, orient="vertical", command=text_widget.yview)
        text_widget.configure(yscrollcommand=scrollbar.set)
        text_widget.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=12)
        scrollbar.pack(side="right", fill="y", padx=(0, 12), pady=12)
        text_widget.insert("1.0", self._format_action_log_details_text(log_id, action_type, created_at, details))
        text_widget.configure(state="disabled")

    def _parse_action_log_details(self, details_json):
        try:
            data = json.loads(details_json) if details_json else {}
        except json.JSONDecodeError:
            data = {"raw": details_json}
        return data if isinstance(data, dict) else {"raw": data}

    def _action_log_summary(self, action_type, details):
        telegram = details.get("telegram") or {}
        text = details.get("incoming_text") or ""
        if not text and isinstance(details.get("reply"), dict):
            text = details["reply"].get("caption") or details["reply"].get("text") or ""
        return {
            "user": self._action_log_user_label(telegram),
            "status": self._action_log_status_label(details.get("status", "")),
            "action": self._action_log_action_label(details.get("recognized_command") or action_type),
            "text": str(text).replace("\n", " "),
        }

    def _action_log_user_label(self, telegram):
        full_name = telegram.get("full_name") or ""
        username = telegram.get("username") or ""
        user_id = telegram.get("user_id") or ""
        if full_name and username:
            return f"{full_name} / @{username}"
        if full_name:
            return str(full_name)
        if username:
            return f"@{username}"
        if user_id:
            return str(user_id)
        return "Неизвестно"

    def _action_log_status_label(self, status):
        labels = {
            "success": "Выполнено", "waiting": "Ожидает ответа", "error": "Ошибка",
            "cancelled": "Отменено", "unknown": "Не распознано",
        }
        return labels.get(str(status or ""), str(status or "Неизвестно"))

    def _action_log_action_label(self, action):
        labels = {
            "telegram_message": "Сообщение Telegram", "add_income": "Приход", "stock_balance": "Остаток",
            "cancel_operation": "Отмена", "bot_selection": "Выбор бота", "bot_explanation": "Пояснение режимов",
            "claude_key_saved": "Ключ Claude сохранен", "claude_key_rejected": "Ключ Claude не сохранен",
            "claude_key_help": "Инструкция Claude API", "claude_chat": "Разговор с Claude",
            "stock_income_history": "История прихода", "status": "Статус", "start": "Старт",
            "help": "Помощь", "sheets": "Список листов", "first": "Первые строки", "unknown": "Не распознано",
        }
        return labels.get(str(action or ""), str(action or "Неизвестно"))

    def _action_log_reply_label(self, reply):
        if not isinstance(reply, dict):
            return str(reply or "")
        if reply.get("type") == "document":
            path = reply.get("path", "")
            caption = reply.get("caption", "")
            return "\n".join(
                part for part in [
                    "Тип ответа: файл",
                    f"Файл: {path}" if path else "",
                    f"Подпись: {caption}" if caption else "",
                ] if part
            )
        return str(reply.get("text", ""))

    # Спрощена версія gui.py._format_action_log_details: без технічних
    # деталей (pipeline_version/duration_ms/сирий JSON) - зайве для бізнес-
    # клієнта, лишає лише те, що реально пояснює, що сталося.
    def _format_action_log_details_text(self, log_id, action_type, created_at, details):
        telegram = details.get("telegram") or {}
        reply = details.get("reply") or {}
        lines = [
            f"Запись журнала: #{log_id}",
            f"Время: {self._format_log_time(created_at)}",
            f"Пользователь: {self._action_log_user_label(telegram)}",
            "",
            "Запрос пользователя:",
            details.get("incoming_text") or "",
            "",
            f"Действие: {self._action_log_action_label(details.get('recognized_command') or action_type)}",
            f"Статус: {self._action_log_status_label(details.get('status'))}",
        ]
        pending_before = details.get("pending_before")
        pending_after = details.get("pending_after")
        if pending_before:
            lines.extend(["", "Операция до сообщения:", f"Тип: {pending_before.get('operation_type', '')}"])
        if pending_after:
            lines.extend(["", "Операция после сообщения:", f"Тип: {pending_after.get('operation_type', '')}"])
        lines.extend(["", "Ответ пользователю:", self._action_log_reply_label(reply)])
        if details.get("error"):
            lines.extend(["", "Ошибка:", str(details.get("error"))])
        return "\n".join(lines)

    def _format_log_time(self, created_at):
        try:
            value = datetime.fromisoformat(str(created_at))
        except (TypeError, ValueError):
            return str(created_at or "")
        return f"{value:%Y.%m.%d} {RU_WEEKDAYS[value.weekday()]} {value:%H:%M}"

    def _format_last_seen(self, created_at):
        try:
            value = datetime.fromisoformat(str(created_at))
        except (TypeError, ValueError):
            return str(created_at or "")
        days_ago = (datetime.now().date() - value.date()).days
        if days_ago == 0:
            return f"сегодня, {value:%H:%M}"
        if days_ago == 1:
            return f"вчера, {value:%H:%M}"
        if days_ago < 7:
            return self._format_log_time(created_at)
        if days_ago < 30:
            return "больше недели назад"
        if days_ago < 365:
            return "больше месяца назад"
        return "больше года назад"

    def _short_text(self, text, max_length):
        text = str(text or "")
        return text if len(text) <= max_length else text[: max_length - 1] + "…"

    def _tk_color(self, color_tuple):
        return color_tuple[1] if self._dark_mode else color_tuple[0]

    # ---------- Персонал (перенесено з gui.py, мінімальна версія) ----------
    # Задача аудиту (permissions.py): perm.has_permission() перевіряється
    # ТІЛЬКИ на боці Telegram-бота (за telegram_id співрозмовника) - у самій
    # десктоп-програмі (і в gui.py так само) немає поняття "хто зараз за
    # комп'ютером", тож саме керування персоналом тут навмисно без окремого
    # gate - той самий рівень довіри, що вже діє в gui.py.
    def _open_personnel_window(self):
        if self.personnel_window is not None and self.personnel_window.winfo_exists():
            self.personnel_window.deiconify()
            self.personnel_window.lift()
            self.personnel_window.focus_force()
            self._refresh_personnel()
            return
        window = tk.Toplevel(self)
        window.title("Персонал")
        window.geometry("640x520")
        window.configure(bg=self._tk_color(COLOR_BG))
        self.personnel_window = window
        self._build_personnel_window(window)

    def _build_personnel_window(self, window):
        top = ctk.CTkFrame(window, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(16, 8))
        ctk.CTkLabel(top, text="Персонал", font=("", 16, "bold"), text_color=COLOR_TEXT).pack(side="left")
        ctk.CTkButton(top, text="+ Добавить", width=110, command=self._on_add_user_clicked).pack(side="right")
        # Задача користувача (2026-08-16): "обновить додай, щоб оновлювало
        # дані" - той самий віджет 1-в-1, що й у Журналах (_build_journals_
        # window вище) - тут потрібен ще й тому, що інша сторона (gui.py
        # через set_role, або сам бот через реєстрацію нового Гостя) може
        # змінити персонал, поки це вікно вже відкрите.
        ctk.CTkButton(top, text="Обновить", width=100, command=self._refresh_personnel).pack(side="right", padx=(0, 8))

        self.personnel_list_frame = ctk.CTkScrollableFrame(window, fg_color="transparent")
        self.personnel_list_frame.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self._refresh_personnel()

    # Задача користувача (2026-08-17): "точно такий же вигляд зроби і в
    # клієнті. ролі - стаціонарні кнопци. відвідуваність теж. все не
    # рухається а стабільно рівне." - той самий фікс, що вже й у gui.py
    # (_render_personnel_list): раніше КОЖЕН рядок був окремим tk.Frame з
    # pack() - Tk рахує розкладку кожного pack()-контейнера незалежно, тож
    # однакові width= на бейджі/часі все одно не гарантували однакову
    # позицію між РІЗНИМИ Frame (звідси "стрибання"). grid() усередині
    # ОДНОГО спільного personnel_list_frame - усі рядки й шапка тепер
    # справжні комірки однієї таблиці, тому колонки структурно не можуть
    # розійтись. Кеш+рендер розділені так само, як у gui.py - сортування/
    # фільтр лише перемальовують уже прочитане, без повторного store.list_
    # users().
    def _refresh_personnel(self):
        if self.personnel_list_frame is None:
            return
        self._personnel_users_cache = self.store.list_users()
        self._render_personnel_list()

    def _render_personnel_list(self):
        for child in self.personnel_list_frame.winfo_children():
            child.destroy()
        users = self._personnel_users_cache
        if not users:
            tk.Label(
                self.personnel_list_frame, text="Пользователей пока нет.",
                fg=self._tk_color(COLOR_TEXT_MUTED), bg=self._tk_color(COLOR_BG),
            ).pack(anchor="w", pady=8)
            return

        text_color = self._tk_color(COLOR_TEXT)
        muted_color = self._tk_color(COLOR_TEXT_MUTED)
        row_bg = self._tk_color(COLOR_ROW)
        header_bg = self._tk_color(COLOR_BG)

        self.personnel_list_frame.grid_columnconfigure(0, weight=1)
        self.personnel_list_frame.grid_columnconfigure(1, weight=0)
        self.personnel_list_frame.grid_columnconfigure(2, weight=0)
        self.personnel_list_frame.grid_columnconfigure(3, weight=0)

        self._build_personnel_header_row(header_bg, muted_color)

        visible_users = self._personnel_filtered_sorted_users(users)
        for index, (user_id, telegram_id, username, full_name, role, last_seen_at) in enumerate(visible_users, start=1):
            display_name = full_name or username or str(telegram_id)
            username_text = f" @{username}" if username else ""
            normalized_role = perm.normalize_role(role)
            role_label = perm.ROLE_LABELS_RU.get(normalized_role, role)
            role_bg, role_fg = perm.ROLE_CHIP_COLORS.get(normalized_role, perm.ROLE_CHIP_COLORS[perm.GUEST])

            headline = f"{index}. {display_name}{username_text} — ID: {telegram_id}"
            tk.Label(
                self.personnel_list_frame, text=headline, font=("Segoe UI", 10),
                fg=text_color, bg=row_bg, anchor="w",
            ).grid(row=index, column=0, sticky="ew", padx=(6, 0), pady=6)

            # Задача користувача (2026-08-16): "роби такого адміна і в
            # домашній і в клієнті" - той самий клікабельний бейдж, що й у
            # gui.py (там - push через тунель; тут - напряму, БД під рукою).
            chip = tk.Label(
                self.personnel_list_frame, text=f"{role_label} ▾", font=("Segoe UI", 9, "bold"),
                bg=role_bg, fg=role_fg, padx=8, pady=3, cursor="hand2",
                width=ROLE_CHIP_WIDTH, anchor="center",
            )
            chip.grid(row=index, column=1, padx=8)
            chip.bind(
                "<Button-1>",
                lambda event, uid=user_id, tid=telegram_id, r=normalized_role, w=chip: self._open_role_menu(w, uid, tid, r),
            )

            last_seen_text = self._format_last_seen(last_seen_at) if last_seen_at else ""
            tk.Label(
                self.personnel_list_frame, text=last_seen_text, font=("Segoe UI", 8),
                fg=muted_color, bg=row_bg, width=LAST_SEEN_WIDTH, anchor="e",
            ).grid(row=index, column=2, sticky="e", padx=(8, 6))

            # Задача користувача (2026-08-17): "приберіть кнопку видалити -
            # спершу ховаєш, тестим і тоді видаляєм" - сховано (не
            # намальовано), код кнопки/handler'а поки лишається.
            # tk.Button(
            #     self.personnel_list_frame, text="Удалить", width=9, fg="#B23B3B",
            #     command=lambda uid=user_id, name=display_name: self._on_delete_user_clicked(uid, name),
            # ).grid(row=index, column=3, padx=(0, 6))

    def _personnel_sort_arrow(self, field):
        if self._personnel_sort_field != field:
            return ""
        return " ↓" if self._personnel_sort_reverse else " ↑"

    def _build_personnel_header_row(self, header_bg, muted_color):
        name_header = tk.Label(
            self.personnel_list_frame, text=f"Имя{self._personnel_sort_arrow('name')}",
            font=("Segoe UI", 8, "bold"), fg=muted_color, bg=header_bg,
            cursor="hand2", anchor="w",
        )
        name_header.grid(row=0, column=0, sticky="w", padx=(6, 0), pady=(0, 6))
        name_header.bind("<Button-1>", lambda event: self._toggle_personnel_sort("name"))

        if self._personnel_role_filter:
            role_header_text = f"Роль: {perm.ROLE_LABELS_RU.get(self._personnel_role_filter, self._personnel_role_filter)} ▾"
        else:
            role_header_text = "Роль ▾"
        role_header = tk.Label(
            self.personnel_list_frame, text=role_header_text,
            font=("Segoe UI", 8, "bold"), fg=muted_color, bg=header_bg,
            cursor="hand2", anchor="center", width=ROLE_CHIP_WIDTH,
        )
        role_header.grid(row=0, column=1, padx=8, pady=(0, 6))
        role_header.bind("<Button-1>", lambda event, w=role_header: self._open_personnel_role_filter_menu(w))

        time_header = tk.Label(
            self.personnel_list_frame, text=f"Время{self._personnel_sort_arrow('time')}",
            font=("Segoe UI", 8, "bold"), fg=muted_color, bg=header_bg,
            cursor="hand2", anchor="e", width=LAST_SEEN_WIDTH,
        )
        time_header.grid(row=0, column=2, sticky="e", padx=(8, 6), pady=(0, 6))
        time_header.bind("<Button-1>", lambda event: self._toggle_personnel_sort("time"))

    def _toggle_personnel_sort(self, field):
        if self._personnel_sort_field == field:
            self._personnel_sort_reverse = not self._personnel_sort_reverse
        else:
            self._personnel_sort_field = field
            self._personnel_sort_reverse = False
        self._render_personnel_list()

    def _open_personnel_role_filter_menu(self, header_widget):
        menu = tk.Menu(
            header_widget, tearoff=0,
            bg=self._tk_color(COLOR_ROW), fg=self._tk_color(COLOR_TEXT),
            activebackground=self._tk_color(COLOR_HOVER), activeforeground=self._tk_color(COLOR_TEXT),
            selectcolor=self._tk_color(COLOR_TEXT), bd=0,
        )
        filter_var = tk.StringVar(value=self._personnel_role_filter or "")
        menu.add_radiobutton(
            label="Все", variable=filter_var, value="",
            command=lambda: self._set_personnel_role_filter(None),
        )
        for role in perm.ROLES:
            menu.add_radiobutton(
                label=perm.ROLE_LABELS_RU[role], variable=filter_var, value=role,
                command=lambda r=role: self._set_personnel_role_filter(r),
            )
        x = header_widget.winfo_rootx()
        y = header_widget.winfo_rooty() + header_widget.winfo_height()
        menu.tk_popup(x, y)

    def _set_personnel_role_filter(self, role):
        self._personnel_role_filter = role
        self._render_personnel_list()

    def _personnel_filtered_sorted_users(self, users):
        result = list(users)
        if self._personnel_role_filter:
            result = [u for u in result if perm.normalize_role(u[4]) == self._personnel_role_filter]
        if self._personnel_sort_field == "name":
            def name_key(u):
                _user_id, telegram_id, username, full_name, _role, _last_seen_at = u
                return str(full_name or username or telegram_id).lower()
            result.sort(key=name_key, reverse=self._personnel_sort_reverse)
        elif self._personnel_sort_field == "time":
            # ISO-8601 рядки порівнюються лексикографічно = хронологічно.
            # Порожньо/None (ще ніколи не писав) - найменше значення,
            # природно опиняється скраю списку.
            result.sort(key=lambda u: u[5] or "", reverse=self._personnel_sort_reverse)
        return result

    def _open_role_menu(self, chip_widget, user_id, telegram_id, current_role):
        menu = tk.Menu(
            chip_widget, tearoff=0,
            bg=self._tk_color(COLOR_ROW), fg=self._tk_color(COLOR_TEXT),
            activebackground=self._tk_color(COLOR_HOVER), activeforeground=self._tk_color(COLOR_TEXT),
            selectcolor=self._tk_color(COLOR_TEXT), bd=0,
        )
        role_var = tk.StringVar(value=current_role)
        for role in perm.ROLES:
            menu.add_radiobutton(
                label=perm.ROLE_LABELS_RU[role], variable=role_var, value=role,
                command=lambda r=role: self._on_role_menu_selected(user_id, telegram_id, current_role, r),
            )
        x = chip_widget.winfo_rootx()
        y = chip_widget.winfo_rooty() + chip_widget.winfo_height()
        menu.tk_popup(x, y)

    # Реальна знахідка (аудит коду, 2026-08-16): раніше порівняння "чи
    # справді змінилась роль" і текст сповіщення в Telegram брали old_role
    # із замикання (стан на МОМЕНТ відкриття меню) - якщо роль встигла
    # змінитись десь-інде (напр. через gui.py) саме між відкриттям меню й
    # кліком, повідомлення показало б неправильне "було X" (сам запис у
    # БД лишався коректним - страждав лише текст). Свіжий рядок читається
    # ОДИН раз і використовується для ОБОХ - і перевірки, і сповіщення.
    def _on_role_menu_selected(self, user_id, telegram_id, old_role, new_role):
        row = self.store.get_user(user_id)
        if not row:
            return
        _id, _telegram_id, username, full_name, current_role, _last_seen_at = row
        if new_role == current_role:
            return
        self.store.update_user(user_id, username, full_name, new_role)
        self._notify_role_change(telegram_id, current_role, new_role)
        self._refresh_personnel()

    def _user_role_options(self):
        return [perm.ROLE_LABELS_RU[role] for role in perm.ROLES]

    def _user_role_to_label(self, role):
        normalized = perm.normalize_role(role) if role else perm.GUEST
        return perm.ROLE_LABELS_RU.get(normalized, perm.ROLE_LABELS_RU[perm.GUEST])

    def _user_role_label_to_code(self, label):
        for role in perm.ROLES:
            if perm.ROLE_LABELS_RU[role] == label:
                return role
        return None

    # Одне спливаюче вікно і для додавання, і для редагування (той самий
    # каркас, що й gui.py._ask_user_form) - СПРАВЖНЯ модальність тут
    # доречна (.transient()+.grab_set()) - на відміну від Журналів/
    # Персоналу самих по собі, це коротка форма, що блокує батьківське
    # вікно до збереження/відміни, саме так і задумано.
    def _ask_user_form(self, title, initial_telegram_id=None, initial_username="", initial_full_name="", initial_role=None):
        is_edit = initial_telegram_id is not None
        result = {"value": None}
        window = tk.Toplevel(self.personnel_window)
        window.title(title)
        window.transient(self.personnel_window)
        window.grab_set()
        window.resizable(False, False)

        form = tk.Frame(window)
        form.pack(padx=16, pady=16, fill="both", expand=True)

        tk.Label(form, text="Telegram ID пользователя:").pack(anchor="w")
        telegram_id_entry = None
        if is_edit:
            tk.Label(form, text=str(initial_telegram_id), font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(2, 12))
        else:
            telegram_id_entry = tk.Entry(form, width=44)
            telegram_id_entry.pack(anchor="w", pady=(2, 12))
            telegram_id_entry.focus_set()

        tk.Label(form, text="Имя пользователя:").pack(anchor="w")
        full_name_entry = tk.Entry(form, width=44)
        full_name_entry.insert(0, initial_full_name)
        full_name_entry.pack(anchor="w", pady=(2, 12))
        if is_edit:
            full_name_entry.focus_set()

        tk.Label(form, text="Telegram username без @ (необязательно):").pack(anchor="w")
        username_entry = tk.Entry(form, width=44)
        username_entry.insert(0, initial_username)
        username_entry.pack(anchor="w", pady=(2, 12))

        tk.Label(form, text="Роль пользователя:").pack(anchor="w")
        role_var = tk.StringVar(value=self._user_role_to_label(initial_role))
        role_combo = ttk.Combobox(form, textvariable=role_var, values=self._user_role_options(), state="readonly", width=38)
        role_combo.pack(anchor="w", pady=(2, 16))

        button_row = tk.Frame(form)
        button_row.pack(anchor="e", fill="x")

        def save():
            if is_edit:
                telegram_id = initial_telegram_id
            else:
                raw_id = telegram_id_entry.get().strip()
                if not raw_id.isdigit():
                    messagebox.showerror(title, "Telegram ID должен быть числом.")
                    return
                telegram_id = int(raw_id)
            role = self._user_role_label_to_code(role_var.get())
            if role is None:
                messagebox.showerror(title, "Выберите роль.")
                return
            full_name = full_name_entry.get().strip() or f"Гость {telegram_id}"
            result["value"] = {
                "telegram_id": telegram_id, "username": username_entry.get().strip(),
                "full_name": full_name, "role": role,
            }
            window.destroy()

        def cancel():
            window.destroy()

        tk.Button(button_row, text="Отменить", width=14, command=cancel).pack(side="right", padx=(8, 0))
        tk.Button(button_row, text="Сохранить", width=16, command=save).pack(side="right")
        window.bind("<Escape>", lambda event: cancel())
        window.protocol("WM_DELETE_WINDOW", cancel)
        self.wait_window(window)
        return result["value"]

    def _on_add_user_clicked(self):
        form = self._ask_user_form("Новый пользователь")
        if not form:
            return
        try:
            self.store.add_user(form["telegram_id"], form["username"], form["full_name"], form["role"])
        except sqlite3.IntegrityError:
            messagebox.showerror("Персонал", "Пользователь с таким Telegram ID уже существует.")
            return
        self._refresh_personnel()

    def _on_delete_user_clicked(self, user_id, display_name):
        if not messagebox.askyesno(
            "Персонал",
            f"Удалить пользователя '{display_name}'?\n"
            "Если этот человек снова напишет боту в личный чат, он автоматически "
            "зарегистрируется заново с ролью «Гость» (без прав).",
            parent=self.personnel_window,
        ):
            return
        self.store.delete_user(user_id)
        self._refresh_personnel()

    def _notify_role_change(self, telegram_id, old_role, new_role):
        old_label = perm.ROLE_LABELS_RU.get(old_role, old_role)
        new_label = perm.ROLE_LABELS_RU.get(new_role, new_role)
        text = f"Ваша роль изменена: {old_label} → {new_label}."

        def worker():
            token, _err = self._read_telegram_token()
            if not token:
                return
            try:
                from main import TelegramBotWorker
                TelegramBotWorker(token, str(paths.DB_PATH))._send_message(telegram_id, text)
            except Exception as exc:
                # Реальний баг (аудит коду, 2026-08-14): except ... as exc
                # автоматично видаляє exc з простору імен, щойно except-блок
                # завершується - lambda нижче виконується ПІЗНІШЕ (на
                # головному потоці, через _run_on_main_thread), тож на
                # момент реального виклику exc уже не існує - сам
                # попереджувальний діалог падав би з NameError замість
                # показу помилки. Той самий клас багу, що вже виправлений
                # для сповіщення про зміну ролі (Персонал) - тут пропущено
                # копію. Захоплюємо текст ПОКИ exc ще живий.
                error_text = str(exc)
                self._run_on_main_thread(lambda: messagebox.showwarning(
                    "Персонал", f"Не удалось уведомить пользователя об изменении роли: {error_text}",
                ))

        threading.Thread(target=worker, daemon=True).start()

    # ---------- Редактор кнопок (перенесено з gui.py, без вкладки "Дії" - ----
    # ---------- лише дерево/порядок/розмір кнопок головного меню бота) ------
    _NO_ACTION_LABEL = "Без действия — только сообщение"
    _OPERATION_LINK_SECTION_LABELS = {
        "start_income": "Приход",
        "start_sale": "Реализация",
        "start_stock_report": "Склад (отчёт)",
        "start_sales_report": "Продажи (отчёт)",
    }
    _NO_OPERATION_LINK_LABEL = "Без прямой ссылки"
    _CUSTOM_BUTTON_LAYOUT_LABELS = {
        "full": "Размер: одна сплошная (на всю строку)",
        "half": "Размер: вдвое меньше (парится с соседней по позиции)",
    }

    def _open_custom_buttons_window(self):
        if self.custom_buttons_window is not None and self.custom_buttons_window.winfo_exists():
            self.custom_buttons_window.deiconify()
            self.custom_buttons_window.lift()
            self.custom_buttons_window.focus_force()
            self._refresh_custom_buttons()
            return
        window = tk.Toplevel(self)
        window.title("Редактор кнопок")
        window.geometry("760x560")
        window.configure(bg=self._tk_color(COLOR_BG))
        self.custom_buttons_window = window
        self._build_custom_buttons_window(window)

    def _build_custom_buttons_window(self, window):
        top = ctk.CTkFrame(window, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(16, 8))
        ctk.CTkLabel(top, text="Редактор кнопок", font=("", 16, "bold"), text_color=COLOR_TEXT).pack(side="left")

        note = tk.Label(
            window,
            text="Кнопки, которые вы добавите здесь, появятся в главном меню бота в Telegram.",
            fg=self._tk_color(COLOR_TEXT_MUTED), bg=self._tk_color(COLOR_BG),
            wraplength=720, justify="left",
        )
        note.pack(anchor="w", padx=16, pady=(0, 8))

        ctk.CTkButton(
            window, text="+ Добавить корневую кнопку", width=220,
            command=lambda: self.add_custom_button_dialog(None),
        ).pack(anchor="w", padx=16, pady=(0, 8))

        content = tk.Frame(window, bg=self._tk_color(COLOR_BG))
        content.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        list_side = ctk.CTkScrollableFrame(content, fg_color="transparent")
        list_side.pack(side="left", fill="both", expand=True, padx=(0, 12))
        self.custom_buttons_list_frame = list_side

        preview_side = tk.Frame(content, width=240, bg=self._tk_color(COLOR_CARD), relief="groove", borderwidth=1)
        preview_side.pack(side="right", fill="y")
        preview_side.pack_propagate(False)
        tk.Label(
            preview_side, text="Превью", font=("Segoe UI", 11, "bold"),
            fg=self._tk_color(COLOR_TEXT), bg=self._tk_color(COLOR_CARD),
        ).pack(anchor="w", padx=12, pady=(12, 4))
        self.custom_button_preview_frame = tk.Frame(preview_side, bg=self._tk_color(COLOR_CARD))
        self.custom_button_preview_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        window.protocol("WM_DELETE_WINDOW", window.destroy)
        self._refresh_custom_buttons()

    def _refresh_custom_buttons(self):
        for child in self.custom_buttons_list_frame.winfo_children():
            child.destroy()
        roots = self.store.list_custom_buttons(None, include_disabled=True)
        if not roots:
            tk.Label(
                self.custom_buttons_list_frame, text="Кнопок пока нет.",
                fg=self._tk_color(COLOR_TEXT_MUTED), bg=self._tk_color(COLOR_BG),
            ).pack(anchor="w", pady=4)
        else:
            root_sides = self._half_pair_sides(roots)
            for row in roots:
                self._render_custom_button_row(row, depth=0, side=root_sides.get(row[0]))
        self._refresh_custom_button_preview()

    def _half_pair_sides(self, rows):
        sides = {}
        pending_id = None
        for row in rows:
            node_id, enabled, layout = row[0], row[5], row[6]
            if not enabled:
                continue
            if layout == "half":
                if pending_id is not None:
                    sides[pending_id] = "лево"
                    sides[node_id] = "право"
                    pending_id = None
                else:
                    pending_id = node_id
            else:
                pending_id = None
        return sides

    def _render_custom_button_row(self, row, depth, side=None):
        node_id, label, message_text, action_code, section, enabled, layout, operation_id = row
        is_selected = node_id == self.custom_buttons_selected_id
        row_bg = self._tk_color(("#D0E8FF", "#2A4A66")) if is_selected else self._tk_color(COLOR_ROW)

        row_frame = tk.Frame(self.custom_buttons_list_frame, bg=row_bg)
        row_frame.pack(fill="x", pady=1, padx=(depth * 24, 0))

        display_label = label + (f" ({side})" if side else "") + ("" if enabled else " (скрыта)")
        tk.Button(
            row_frame, text=display_label, anchor="w", bg=row_bg, fg=self._tk_color(COLOR_TEXT),
            font=("Segoe UI", 9), width=28,
            command=lambda nid=node_id: self.select_custom_button(nid),
        ).pack(side="left", fill="x", expand=True)

        tk.Button(
            row_frame, text="x", width=3, fg="#D1242F",
            command=lambda nid=node_id, lbl=label: self.delete_custom_button_confirm(nid, lbl),
        ).pack(side="right")
        tk.Button(
            row_frame, text="ред", width=5,
            command=lambda nid=node_id: self.edit_custom_button_dialog(nid),
        ).pack(side="right")
        tk.Button(
            row_frame, text="+", width=3, fg="#1A7F37",
            command=lambda nid=node_id: self.add_custom_button_dialog(nid),
        ).pack(side="right")

        child_rows = self.store.list_custom_buttons(node_id, include_disabled=True)
        child_sides = self._half_pair_sides(child_rows)
        for child_row in child_rows:
            self._render_custom_button_row(child_row, depth=depth + 1, side=child_sides.get(child_row[0]))

    def select_custom_button(self, node_id):
        self.custom_buttons_selected_id = node_id
        self._refresh_custom_buttons()

    def _custom_button_position_options(self, parent_id, exclude_node_id=None):
        siblings = self.store.list_custom_buttons(parent_id, include_disabled=True)
        ids_in_order = [row[0] for row in siblings]
        if exclude_node_id in ids_in_order:
            ids_in_order.remove(exclude_node_id)
        return [str(i) for i in range(1, len(ids_in_order) + 2)]

    def _refresh_custom_button_preview(self):
        for child in self.custom_button_preview_frame.winfo_children():
            child.destroy()
        node_id = self.custom_buttons_selected_id
        row = self.store.get_custom_button(node_id) if node_id else None
        text_color = self._tk_color(COLOR_TEXT)
        muted_color = self._tk_color(COLOR_TEXT_MUTED)
        card_bg = self._tk_color(COLOR_CARD)
        if not row:
            tk.Label(
                self.custom_button_preview_frame, text="Выберите кнопку слева.",
                fg=muted_color, bg=card_bg, wraplength=210, justify="left",
            ).pack(anchor="w")
            return

        _id, _parent_id, label, message_text, action_code, section, enabled, layout, operation_id = row
        tk.Label(
            self.custom_button_preview_frame, text=label, font=("Segoe UI", 10, "bold"),
            fg=text_color, bg=card_bg, wraplength=210, justify="left",
        ).pack(anchor="w", pady=(0, 8))
        tk.Label(
            self.custom_button_preview_frame, text=self._CUSTOM_BUTTON_LAYOUT_LABELS.get(layout, layout),
            fg=text_color, bg=card_bg, wraplength=210, justify="left",
        ).pack(anchor="w", pady=(0, 8))
        tk.Label(
            self.custom_button_preview_frame, text=message_text or "(без сообщения)",
            fg=text_color, bg=card_bg, wraplength=210, justify="left",
        ).pack(anchor="w", pady=(0, 8))

        tk.Label(
            self.custom_button_preview_frame, text="Далее:", font=("Segoe UI", 9, "bold"),
            fg=text_color, bg=card_bg,
        ).pack(anchor="w")
        children = self.store.list_custom_buttons(_id, include_disabled=True)
        if children:
            for _child_id, child_label, *_rest in children:
                tk.Label(
                    self.custom_button_preview_frame, text=f"• {child_label}",
                    fg=text_color, bg=card_bg, wraplength=210, justify="left",
                ).pack(anchor="w")
        elif operation_id is not None:
            tk.Label(
                self.custom_button_preview_frame,
                text=f"Прямая ссылка: {self._operation_link_id_to_label(operation_id)}",
                fg=text_color, bg=card_bg, wraplength=210, justify="left",
            ).pack(anchor="w")
        elif action_code:
            action_label = next(
                (action["label"] for action in CUSTOM_BUTTON_ACTIONS if action["code"] == action_code),
                action_code,
            )
            tk.Label(
                self.custom_button_preview_frame, text=f"Действие: {action_label}",
                fg=text_color, bg=card_bg, wraplength=210, justify="left",
            ).pack(anchor="w")
        else:
            tk.Label(self.custom_button_preview_frame, text="(нет действия)", fg=text_color, bg=card_bg).pack(anchor="w")

    def _custom_button_action_options(self):
        return [self._NO_ACTION_LABEL] + [action["label"] for action in CUSTOM_BUTTON_ACTIONS]

    def _custom_button_action_code_to_label(self, action_code):
        for action in CUSTOM_BUTTON_ACTIONS:
            if action["code"] == action_code:
                return action["label"]
        return self._NO_ACTION_LABEL

    def _custom_button_action_label_to_code(self, label):
        for action in CUSTOM_BUTTON_ACTIONS:
            if action["label"] == label:
                return action["code"]
        return None

    def _operation_link_catalog(self):
        catalog = []
        for operation in self.store.list_operations():
            operation_id, _code, _kind, _requires_identity, op_label, parent_action_code, *_rest = operation
            section_label = self._OPERATION_LINK_SECTION_LABELS.get(parent_action_code, parent_action_code)
            catalog.append((operation_id, f"{op_label} — {section_label}"))
        return catalog

    def _operation_link_options(self):
        return [self._NO_OPERATION_LINK_LABEL] + [display for _id, display in self._operation_link_catalog()]

    def _operation_link_id_to_label(self, operation_id):
        if operation_id is not None:
            for op_id, display in self._operation_link_catalog():
                if op_id == operation_id:
                    return display
        return self._NO_OPERATION_LINK_LABEL

    def _operation_link_label_to_id(self, label):
        for op_id, display in self._operation_link_catalog():
            if display == label:
                return op_id
        return None

    def _ask_custom_button_form(
        self, title, position_options=None, initial_position=None,
        initial_label="", initial_message="", initial_action_code=None, initial_layout="full",
        initial_operation_id=None,
    ):
        if position_options is None:
            position_options = ["1"]
        if initial_position is None:
            initial_position = position_options[0]
        result = {"value": None}
        window = tk.Toplevel(self.custom_buttons_window)
        window.title(title)
        window.resizable(False, False)
        window.configure(bg=self._tk_color(COLOR_BG))

        form = tk.Frame(window, bg=self._tk_color(COLOR_BG))
        form.pack(padx=16, pady=16, fill="both", expand=True)
        label_color = self._tk_color(COLOR_TEXT)
        bg = self._tk_color(COLOR_BG)

        tk.Label(form, text="Название кнопки:", fg=label_color, bg=bg).pack(anchor="w")
        label_entry = tk.Entry(form, width=44)
        label_entry.insert(0, initial_label)
        label_entry.pack(anchor="w", pady=(2, 12))
        label_entry.focus_set()

        tk.Label(form, text="Что бот отвечает при нажатии:", fg=label_color, bg=bg).pack(anchor="w")
        message_text_widget = tk.Text(form, width=44, height=5, wrap="word")
        message_text_widget.insert("1.0", initial_message or "")
        message_text_widget.pack(anchor="w", pady=(2, 4))
        tk.Label(
            form,
            text=(
                "Для стандартных действий (Приход, Реализация, Склад, Продажи,\n"
                "Калькулятор, Справка) этот текст игнорируется."
            ),
            justify="left", fg=self._tk_color(COLOR_TEXT_MUTED), bg=bg, font=("Segoe UI", 8),
        ).pack(anchor="w", pady=(0, 12))

        assignment_var = tk.StringVar(value="operation" if initial_operation_id is not None else "action")

        tk.Label(form, text="Назначение кнопки:", fg=label_color, bg=bg).pack(anchor="w")
        tk.Radiobutton(
            form, text="Стандартное действие:", variable=assignment_var, value="action",
            bg=bg, fg=label_color, selectcolor=bg, command=lambda: update_combo_states(),
        ).pack(anchor="w")
        action_var = tk.StringVar(value=self._custom_button_action_code_to_label(initial_action_code))
        action_combo = ttk.Combobox(
            form, textvariable=action_var, values=self._custom_button_action_options(), state="readonly", width=38,
        )
        action_combo.pack(anchor="w", padx=(20, 0), pady=(2, 10))

        tk.Radiobutton(
            form, text="Прямая ссылка на действие из «Действий»:", variable=assignment_var, value="operation",
            bg=bg, fg=label_color, selectcolor=bg, command=lambda: update_combo_states(),
        ).pack(anchor="w")
        operation_var = tk.StringVar(value=self._operation_link_id_to_label(initial_operation_id))
        operation_combo = ttk.Combobox(
            form, textvariable=operation_var, values=self._operation_link_options(), state="readonly", width=38,
        )
        operation_combo.pack(anchor="w", padx=(20, 0), pady=(2, 16))

        def update_combo_states():
            mode = assignment_var.get()
            action_combo.configure(state="readonly" if mode == "action" else "disabled")
            operation_combo.configure(state="readonly" if mode == "operation" else "disabled")

        update_combo_states()

        tk.Label(form, text="Позиция (номер среди соседних кнопок):", fg=label_color, bg=bg).pack(anchor="w")
        position_var = tk.StringVar(value=initial_position)
        ttk.Combobox(
            form, textvariable=position_var, values=position_options, state="readonly", width=10,
        ).pack(anchor="w", pady=(2, 16))

        tk.Label(form, text="Размер кнопки:", fg=label_color, bg=bg).pack(anchor="w")
        layout_var = tk.StringVar(value=initial_layout or "full")
        tk.Radiobutton(
            form, text="Одна сплошная (на всю строку)", variable=layout_var, value="full",
            bg=bg, fg=label_color, selectcolor=bg,
        ).pack(anchor="w")
        tk.Radiobutton(
            form, text="Вдвое меньше (парится с соседней по позиции)", variable=layout_var, value="half",
            bg=bg, fg=label_color, selectcolor=bg,
        ).pack(anchor="w", pady=(0, 16))

        button_row = tk.Frame(form, bg=bg)
        button_row.pack(anchor="e", fill="x")

        def save():
            label = label_entry.get().strip()
            if not label:
                messagebox.showerror(title, "Название кнопки не может быть пустым.", parent=window)
                return
            mode = assignment_var.get()
            result["value"] = {
                "label": label,
                "message_text": message_text_widget.get("1.0", "end").strip(),
                "action_code": self._custom_button_action_label_to_code(action_var.get()) if mode == "action" else None,
                "operation_id": self._operation_link_label_to_id(operation_var.get()) if mode == "operation" else None,
                "layout": layout_var.get(),
                "position_index": int(position_var.get()) - 1,
            }
            window.destroy()

        def cancel():
            window.destroy()

        tk.Button(button_row, text="Отменить", width=14, command=cancel).pack(side="right", padx=(8, 0))
        tk.Button(button_row, text="Сохранить изменения", width=18, command=save).pack(side="right")

        window.bind("<Escape>", lambda event: cancel())
        window.protocol("WM_DELETE_WINDOW", cancel)
        window.update_idletasks()
        width, height = 420, 640
        x = self.custom_buttons_window.winfo_rootx() + (self.custom_buttons_window.winfo_width() - width) // 2
        y = self.custom_buttons_window.winfo_rooty() + (self.custom_buttons_window.winfo_height() - height) // 2
        window.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")
        window.transient(self.custom_buttons_window)
        window.grab_set()
        self.custom_buttons_window.wait_window(window)
        return result["value"]

    def add_custom_button_dialog(self, parent_id=None):
        position_options = self._custom_button_position_options(parent_id)
        form = self._ask_custom_button_form(
            "Новая кнопка", position_options=position_options, initial_position=position_options[-1],
        )
        if not form:
            return
        if self.store.custom_button_label_collides(form["label"]):
            messagebox.showerror(
                "Редактор кнопок",
                f'Название "{form["label"]}" совпадает с уже существующей командой бота. Выберите другое название.',
                parent=self.custom_buttons_window,
            )
            return
        new_id = self.store.add_custom_button(
            form["label"], form["message_text"], form["action_code"], parent_id=parent_id, layout=form["layout"],
            operation_id=form["operation_id"],
        )
        self.store.set_custom_button_position(new_id, form["position_index"])
        self._refresh_custom_buttons()

    def edit_custom_button_dialog(self, node_id):
        row = self.store.get_custom_button(node_id)
        if not row:
            return
        _id, parent_id, label, message_text, action_code, section, enabled, layout, operation_id = row

        siblings = self.store.list_custom_buttons(parent_id, include_disabled=True)
        ids_in_order = [sibling_row[0] for sibling_row in siblings]
        current_index = ids_in_order.index(node_id) if node_id in ids_in_order else len(ids_in_order) - 1
        position_options = self._custom_button_position_options(parent_id, exclude_node_id=node_id)

        form = self._ask_custom_button_form(
            "Редактировать кнопку",
            position_options=position_options,
            initial_position=str(current_index + 1),
            initial_label=label,
            initial_message=message_text or "",
            initial_action_code=action_code,
            initial_layout=layout,
            initial_operation_id=operation_id,
        )
        if not form:
            return
        if form["label"].lower() != label.lower() and self.store.custom_button_label_collides(form["label"]):
            messagebox.showerror(
                "Редактор кнопок",
                f'Название "{form["label"]}" совпадает с уже существующей командой бота. Выберите другое название.',
                parent=self.custom_buttons_window,
            )
            return
        self.store.update_custom_button(
            node_id, form["label"], form["message_text"], form["action_code"], layout=form["layout"],
            operation_id=form["operation_id"],
        )
        self.store.set_custom_button_position(node_id, form["position_index"])
        self._refresh_custom_buttons()

    def delete_custom_button_confirm(self, node_id, label):
        descendant_count = self.store.count_custom_button_descendants(node_id)
        if descendant_count > 0:
            confirmed = messagebox.askyesno(
                "Удалить кнопку",
                f'Кнопка "{label}" имеет дочерние кнопки — вместе с ней удалятся ещё {descendant_count} '
                "дочерних кнопок (вся ветка). Продолжить?",
                parent=self.custom_buttons_window,
            )
        else:
            confirmed = messagebox.askyesno(
                "Удалить кнопку", f'Удалить кнопку "{label}"?', parent=self.custom_buttons_window,
            )
        if not confirmed:
            return
        self.store.delete_custom_button(node_id)
        if self.custom_buttons_selected_id is not None and not self.store.get_custom_button(self.custom_buttons_selected_id):
            self.custom_buttons_selected_id = None
        self._refresh_custom_buttons()

    # ---------- керування Telegram-ботом ----------
    def _read_telegram_token(self):
        token_file = self.settings.get("telegram_token_file")
        if not token_file:
            return None, "Файл токена ещё не выбран."
        token_path = Path(token_file)
        if not token_path.exists():
            return None, "Файл токена не найден."
        try:
            lines = token_path.read_text(encoding="utf-8-sig").splitlines()
        except UnicodeDecodeError:
            lines = token_path.read_text(encoding="cp1251").splitlines()
        except OSError as exc:
            return None, f"Не удалось прочитать файл токена: {exc}"
        token = next((line.strip() for line in lines if line.strip()), "")
        if not token:
            return None, "Файл токена пуст."
        return token, None

    def _on_start_clicked(self):
        worker = self.telegram_worker
        if worker and worker.thread and worker.thread.is_alive():
            return
        # Задача користувача: "кнопка відразу має реагувати на клік і
        # переходити на місце кліку відразу, щоб не чекати підключення" -
        # синій акцент рухається ОДРАЗУ на клік (намір користувача), а не
        # лише після підтвердженого стану з _poll_bot_status - той просто
        # підтвердить те саме трохи пізніше (чи поверне назад, якщо
        # запуск не вдався).
        self._set_toggle_buttons(self.bot_stop_button, self.bot_start_button, is_on=True)
        token, error = self._read_telegram_token()
        if error:
            self._bot_subtitle_var.set(error)
            self._set_toggle_buttons(self.bot_stop_button, self.bot_start_button, is_on=False)
            return
        self._bot_subtitle_var.set("запускается...")
        # Задача користувача (2026-08-13): "при ВДАЛОМУ запуску (ВАЖЛИВО,
        # саме при запуску, не при спробі), через 5 сек..." - раніше
        # відлік 5с планувався одразу тут, ще до того, як бот РЕАЛЬНО
        # підключився (worker.start() лише СПРОБА - фоновий потік може й
        # не підключитись узагалі). Тепер лише скидаємо прапорець - сам
        # 5с відлік планується в _set_status_text, побачивши СПРАВЖНЄ
        # підтвердження підключення від main.py, один раз на цей запуск.
        self._bot_connect_handled = False
        from main import TelegramBotWorker  # локально, щоб уникнути циклічного імпорту (той самий прийом, що й gui.py)
        self.telegram_worker = TelegramBotWorker(
            token, paths.DB_PATH, settings_path=paths.SETTINGS_PATH,
            status_callback=self._set_status_text,
        )
        # Той самий перенос уже відомої адреси на нового воркера, що й
        # gui.py._start_telegram_from_settings - тунель не прив'язаний до
        # конкретного воркера, тож не чекаємо нового збігу regex, якщо
        # адреса вже є з попереднього старту.
        if self.webapp_public_url:
            self.telegram_worker.webapp_public_url = self.webapp_public_url
        self.telegram_worker.start()

    def _stop_bot_async(self, on_stopped=None, stop_webapp_too=False):
        # НІКОЛИ .stop() напряму на Tk-потоці - воно блокує до ~7с
        # (thread.join у TelegramBotWorker.stop), той самий висновок
        # аудиту, що вже врахований у gui.py. stop_webapp_too=False для
        # перезапуску (той самий принцип, що й gui.py - тунель НЕ має
        # "блимати" адресою на звичайний reconnect бота, лише на явне
        # "Выкл."/вихід).
        if stop_webapp_too:
            self._webapp_should_run = False
            self._stop_webapp_tunnel()
        worker = self.telegram_worker
        if not worker or not worker.thread or not worker.thread.is_alive():
            self.telegram_worker = None
            self._set_status_text("выключен")
            self._set_indicator(COLOR_OFF)
            if on_stopped:
                self._run_on_main_thread(on_stopped)
            return

        # Реальний баг (2026-08-13): "бот... сам увімкнувся та вимкнувся" -
        # worker.stop() (thread.join, до ~7с) виконується у фоновому потоці,
        # а _poll_bot_status тим часом тікає кожні 5с і бачив ЩЕ живий
        # (ще не встиг приєднатись) worker.thread - і сам повертав кнопки
        # назад у "Вкл", борючись з щойно натиснутим "Выкл". Прапорець
        # нижче - той самий висновок аудиту, що вже й у gui.py._telegram_
        # stop_in_progress - гасить _poll_bot_status на час зупинки.
        self._bot_stop_in_progress = True

        def stop_worker():
            try:
                worker.stop()
                # Той самий висновок аудиту, що й у коментарі класу вище
                # (_BOT_STOP_EXTRA_WAIT_SECONDS) - worker.stop() міг
                # повернутись раніше, ніж потік реально завершився.
                deadline = time.monotonic() + self._BOT_STOP_EXTRA_WAIT_SECONDS
                while worker.thread.is_alive() and time.monotonic() < deadline:
                    time.sleep(0.2)
            finally:
                self._bot_stop_in_progress = False
            self.telegram_worker = None
            self._set_status_text("выключен")
            self._set_indicator(COLOR_OFF)
            self._run_on_main_thread(lambda: self._set_toggle_buttons(self.bot_stop_button, self.bot_start_button, is_on=False))
            if on_stopped:
                self._run_on_main_thread(on_stopped)

        threading.Thread(target=stop_worker, daemon=True).start()

    def _on_stop_clicked(self):
        self._set_toggle_buttons(self.bot_stop_button, self.bot_start_button, is_on=False)
        self._bot_subtitle_var.set("остановка...")
        self._stop_bot_async(stop_webapp_too=True)

    def _on_restart_clicked(self):
        self._set_toggle_buttons(self.bot_stop_button, self.bot_start_button, is_on=True)
        self._bot_subtitle_var.set("перезапуск...")
        self._stop_bot_async(on_stopped=self._on_start_clicked)

    def _poll_bot_status(self):
        # Реальний баг (аудит): worker.last_success_at - ISO-рядок
        # (datetime.now().isoformat() у main.py), не time.time()-число -
        # той самий формат, що вже парсить gui.py._is_timestamp_stale.
        worker = self.telegram_worker
        if worker and worker.thread and worker.thread.is_alive():
            recent = False
            if worker.last_success_at:
                try:
                    moment = datetime.fromisoformat(str(worker.last_success_at))
                    recent = (datetime.now() - moment).total_seconds() < self._STALE_THRESHOLD_SECONDS
                except (TypeError, ValueError):
                    recent = False
            self._set_indicator(COLOR_ON if recent else COLOR_WARN)
            # Поки триває фонова зупинка (_bot_stop_in_progress) - потік ще
            # технічно живий (join не встиг завершитись), але кнопки вже
            # мають лишатись "Выкл" (щойно натиснуто) - не воюємо з цим.
            if not self._bot_stop_in_progress:
                self._set_toggle_buttons(self.bot_stop_button, self.bot_start_button, is_on=True)
        else:
            self._set_indicator(COLOR_OFF)
            if not self._bot_stop_in_progress:
                self._set_toggle_buttons(self.bot_stop_button, self.bot_start_button, is_on=False)
        # Той самий тік, що й gui.py._telegram_watchdog_tick - "живий
        # процес" cloudflared/webapp_server перевіряється тут-таки, окремий
        # HTTP-пробник (_webapp_health_watchdog_tick) - на своєму, повільнішому.
        self._check_webapp_tunnel_health()
        self.after(self._STATUS_POLL_MS, self._poll_bot_status)

    # ---------- форма (Cloudflare Quick Tunnel) - той самий механізм, ----
    # ---------- що й gui.py, скопійований і адаптований під CTk-вікно ----
    def _start_webapp_tunnel(self):
        # Реальний баг (2026-08-15): "я прибрав галочку, а він всерівно
        # під'єднався, хоча був вимкнений" - _handle_bot_status_update
        # планує self.after(5000, self._start_webapp_tunnel) в момент
        # підключення бота, ЯКЩО _webapp_auto_manage тоді було True. Якщо
        # користувач знімає галочку "Автовключение" ПРОТЯГОМ цих 5с (до
        # того, як відкладений виклик спрацює), сам цей виклик і досі
        # виконувався б - жоден з call-сайтів self.after(5000,...)/
        # ретрай-циклу нижче не перевіряв актуальний стан ПОВТОРНО в
        # момент фактичного виконання. Тепер - перевірка тут, у самій
        # функції (єдина точка входу для запуску тунелю) - той самий
        # принцип, що вже діє в _check_webapp_tunnel_health.
        if not self._webapp_should_run:
            return
        if self._webapp_tunnel_starting:
            return
        if self.cloudflared_process is not None and self.cloudflared_process.poll() is None:
            return
        if not paths.CLOUDFLARED_EXE.exists():
            return
        self._webapp_tunnel_starting = True
        try:
            self.webapp_server.start()
        except OSError:
            pass

        def run_tunnel():
            started_at = time.monotonic()
            url_found = False
            process = None
            timer = None
            try:
                try:
                    # Задача користувача (2026-08-15): "яку папку вибрати
                    # одну у різних людей? так не піде" - іменований
                    # (persistent) Tunnel замість Quick Tunnel: адреса
                    # ФІКСОВАНА (paths.CLOUDFLARED_TUNNEL_HOSTNAME), більше
                    # не видається наново щоразу - жодного regex-пошуку
                    # URL у виводі більше не треба, лише дочекатись
                    # підтвердження, що з'єднання зареєстровано.
                    process = subprocess.Popen(
                        [
                            str(paths.CLOUDFLARED_EXE), "tunnel", "run",
                            "--credentials-file", str(paths.CLOUDFLARED_TUNNEL_CREDENTIALS_PATH),
                            "--url", f"http://localhost:{self.webapp_server.port}",
                            paths.CLOUDFLARED_TUNNEL_ID,
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        # Реальний баг (2026-08-15, знайдено користувачем на
                        # зібраній .exe версії): без цього Windows відкриває
                        # ОКРЕМЕ видиме вікно консолі для cloudflared.exe -
                        # у dev-режимі (python client_app.py) непомітно,
                        # оскільки є консоль батьківського процесу, яку
                        # дочірній процес ділить; --windowed .exe консолі
                        # не має, тож Windows створює НОВУ.
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                except OSError:
                    return
                self.cloudflared_process = process

                def cancel_if_stuck(target=process):
                    if target.poll() is None:
                        try:
                            target.terminate()
                        except OSError:
                            pass

                timer = threading.Timer(self._WEBAPP_TUNNEL_START_TIMEOUT_SECONDS, cancel_if_stuck)
                timer.daemon = True
                timer.start()
                try:
                    for line in process.stdout:
                        if "Registered tunnel connection" in line:
                            url_found = True
                            hostname = paths.CLOUDFLARED_TUNNEL_HOSTNAME
                            self._run_on_main_thread(
                                lambda hostname=hostname: self._apply_webapp_public_url(f"https://{hostname}")
                            )
                            break
                except (OSError, ValueError):
                    pass
            finally:
                if timer is not None:
                    timer.cancel()
                # Той самий фікс, що й у gui.py (2026-08-13): прапорець
                # лишається True на ВЕСЬ цикл "спроба + пауза" - інакше
                # швидший _check_webapp_tunnel_health (кожні 5с) встигав
                # запустити ЩЕ одну спробу задовго до задуманих 30с, і
                # реальний темп запитів на Cloudflare провокував бан 429.
                if not url_found:
                    self.cloudflared_process = None
                    if self._webapp_should_run:
                        remaining = self._WEBAPP_TUNNEL_START_TIMEOUT_SECONDS - (time.monotonic() - started_at)
                        if remaining > 0:
                            time.sleep(remaining)
                        self._webapp_tunnel_starting = False
                        self._run_on_main_thread(self._start_webapp_tunnel)
                    else:
                        self._webapp_tunnel_starting = False
                else:
                    self._webapp_tunnel_starting = False

        threading.Thread(target=run_tunnel, daemon=True).start()

    def _apply_webapp_public_url(self, url):
        self.webapp_public_url = url
        # Задача користувача (2026-08-13): "не підключає з першого разу" -
        # реальна причина: cloudflared сам попереджає в лозі "may take some
        # time to be reachable" одразу після видачі адреси - DNS/edge ще не
        # встигли розповсюдитись. Якщо тік health-watchdog (кожні 30с,
        # незалежний від моменту старту тунелю) влучав у це коротке вікно,
        # проба помилково вважала щойно піднятий, повністю здоровий тунель
        # "мертвим" і перезапускала його - отримуючи НОВУ адресу, яка сама
        # знову потрапляла в те саме вікно. Grace period нижче не дає
        # health-check взагалі щось робити, поки адреса ще занадто свіжа.
        self._webapp_url_assigned_at = time.monotonic()
        if self.telegram_worker is not None:
            self.telegram_worker.webapp_public_url = url
        self._refresh_webapp_status_text()

    def _atexit_kill_tunnel_process(self):
        process = self.cloudflared_process
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=3)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def _stop_webapp_tunnel(self):
        process = self.cloudflared_process
        self.cloudflared_process = None
        self.webapp_public_url = ""
        if process is not None:
            try:
                process.terminate()
                # Реальний баг (аудит коду, 2026-08-15): раніше terminate()
                # був "fire-and-forget" - без підтвердження, що cloudflared.exe
                # РЕАЛЬНО завершився й звільнив файлові дескриптори у своїй
                # теці. Викликається й безпосередньо ПЕРЕД robocopy в
                # _install_downloaded_update - без wait() є реальний шанс,
                # що robocopy спробує перезаписати ще заблокований файл
                # (лише 5 спроб по 1с у самому robocopy - вузький бюджет).
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                    process.wait(timeout=2)
                except OSError:
                    pass
            except OSError:
                pass
        try:
            self.webapp_server.stop()
        except OSError:
            pass
        self._refresh_webapp_status_text()

    # Задача користувача (скріншот): "прибери там довгий текст, достатньо
    # загального інформування" - без самої адреси в статус-рядку (лише
    # "подключена"/"выключена"/"запускается") - повна адреса й так уже не
    # потрібна людині на очах щодня, форма відкривається кнопкою в Telegram.
    def _refresh_webapp_status_text(self):
        def apply():
            if not paths.CLOUDFLARED_EXE.exists():
                self._webapp_subtitle_var.set("недоступна")
                self._set_toggle_buttons(self.webapp_stop_button, self.webapp_start_button, is_on=False)
                return
            tunnel_alive = self.cloudflared_process is not None and self.cloudflared_process.poll() is None
            # Тунель тепер може лишатись живим, поки контент вимкнено лише
            # ВІДДАЛЕНО (_set_webapp_content_enabled) - без цієї гілки
            # локальний статус хибно писав би "подключена", хоча відвідувач
            # за посиланням побачив би "форма временно отключена".
            if tunnel_alive and self.webapp_public_url and not self._webapp_content_enabled:
                self._webapp_subtitle_var.set("отключена (удалённо)")
                self._set_webapp_indicator(COLOR_OFF)
                self._set_toggle_buttons(self.webapp_stop_button, self.webapp_start_button, is_on=False)
            elif tunnel_alive and self.webapp_public_url:
                self._webapp_subtitle_var.set("подключена")
                self._set_webapp_indicator(COLOR_ON)
                self._set_toggle_buttons(self.webapp_stop_button, self.webapp_start_button, is_on=True)
            elif tunnel_alive or self._webapp_tunnel_starting:
                self._webapp_subtitle_var.set("запускается...")
                self._set_webapp_indicator(COLOR_WARN)
                self._set_toggle_buttons(self.webapp_stop_button, self.webapp_start_button, is_on=True)
            else:
                self._webapp_subtitle_var.set("выключена")
                self._set_webapp_indicator(COLOR_OFF)
                self._set_toggle_buttons(self.webapp_stop_button, self.webapp_start_button, is_on=False)
        self._run_on_main_thread(apply)

    # Задача користувача (2026-08-15): "налаштувати керування із старої
    # програми до нової... щоб статус сервера показувало" - викликається
    # НА ПОТОЦІ HTTP-сервера (webapp_server.py, /control/status), не на
    # головному Tk-потоці - читає лише прості атрибути (poll()/bool),
    # нічого не оновлює й не малює, тож окремого _run_on_main_thread тут
    # не треба (на відміну від _handle_remote_command нижче).
    def _get_remote_status(self):
        worker = self.telegram_worker
        bot_alive = bool(worker and worker.thread and worker.thread.is_alive())
        tunnel_alive = self.cloudflared_process is not None and self.cloudflared_process.poll() is None
        # Тунель може лишатись живим, поки контент вимкнено лише ВІДДАЛЕНО
        # (_webapp_content_enabled, той самий фікс 502 - див.
        # _handle_remote_command) - без цього gui.py показував би "форма:
        # підключено", хоча вона фактично не показує дані відвідувачам.
        return {
            "bot_alive": bot_alive,
            "webapp_alive": bool(tunnel_alive and self.webapp_public_url and self._webapp_content_enabled),
            "webapp_public_url": self.webapp_public_url,
        }

    # Те саме джерело правди, що вже мають кнопки в самому інтерфейсі
    # (_on_start_clicked/_on_stop_clicked/_on_restart_clicked/
    # _toggle_webapp_form) - віддалена команда лише ЗАПУСКАЄ той самий
    # шлях, нічого не дублює. Викликається на потоці HTTP-сервера, тож
    # усе, що торкається Tk-віджетів, іде через _run_on_main_thread (той
    # самий принцип, що вже й _on_excel_refresh_finished нижче). "ok":true
    # у відповіді означає "команду прийнято й заплановано", не "дію вже
    # завершено" - фактичний результат видно окремо через /control/status.
    def _handle_remote_command(self, action):
        if action == "start_bot":
            self._run_on_main_thread(self._on_start_clicked)
        elif action == "stop_bot":
            self._run_on_main_thread(self._on_stop_clicked)
        elif action == "restart_bot":
            self._run_on_main_thread(self._on_restart_clicked)
        # Реальний баг (2026-08-16, живий продакшн, скріншот "HTTP Error
        # 502: Bad Gateway"): _toggle_webapp_form("stop") зупиняє САМ тунель
        # (_stop_webapp_tunnel) - а /control/* маршрути, якими gui.py й шле
        # ЦЮ САМУ команду, йдуть ЧЕРЕЗ ТОЙ САМИЙ тунель. "Вимкнути форму"
        # віддалено назавжди відрізало канал, яким її можна було ввімкнути
        # назад - "Включить форму" одразу після "Отключить" гарантовано
        # 502 (тунелю вже нема, кому слати запит). Тут (лише для ВІДДАЛЕНИХ
        # команд - локальні кнопки на самому ПК лишаються без змін, там
        # людина фізично поруч і завжди може натиснути "Вкл" сама) - тунель
        # і сервер лишаються живими, гаситься лише КОНТЕНТ (get_form_content_
        # enabled, webapp_server.py) - /control/* й далі відповідає.
        elif action == "start_form":
            self._run_on_main_thread(lambda: self._set_webapp_content_enabled(True))
        elif action == "stop_form":
            self._run_on_main_thread(lambda: self._set_webapp_content_enabled(False))
        elif action == "restart_form":
            self._run_on_main_thread(lambda: self._set_webapp_content_enabled(True))

    # Задача користувача: "якщо у Форми стоїть 'Автовключение', а бота
    # немає - спроба увімкнути вручну має показати запитання спливаючим
    # вікном: 'Вы действительно хотите запустить форму без бота?'" - лише
    # для РУЧНОГО кліку на "Вкл" (не для автоматичного шляху після
    # підключення бота, де бот і так уже гарантовано працює).
    def _on_webapp_start_clicked(self):
        worker = self.telegram_worker
        bot_alive = bool(worker and worker.thread and worker.thread.is_alive())
        if not bot_alive and not messagebox.askyesno(
            "AI Automation", "Вы действительно хотите запустить форму без бота?",
        ):
            return
        self._toggle_webapp_form("start")

    def _toggle_webapp_form(self, action):
        # Локальні кнопки на самому ПК - навмисно БЕЗ ЗМІН (той самий повний
        # стоп тунелю/сервера, що й завжди): людина тут фізично поруч і
        # завжди може натиснути "Вкл" сама, жодного ризику "відрізати сама
        # собі канал" немає (на відміну від віддалених команд, див.
        # _set_webapp_content_enabled нижче).
        self._webapp_content_enabled = action != "stop"
        # Той самий "клік -> одразу видимий результат" фікс, що й для бота.
        self._set_toggle_buttons(self.webapp_stop_button, self.webapp_start_button, is_on=(action != "stop"))
        if action == "start":
            self._webapp_should_run = True
            self._start_webapp_tunnel()
        elif action == "stop":
            self._webapp_should_run = False
            self._stop_webapp_tunnel()
        elif action == "restart":
            self._webapp_should_run = True
            self._stop_webapp_tunnel()
            self._start_webapp_tunnel()
        self._refresh_webapp_status_text()

    # Задача користувача (2026-08-16, скріншот "HTTP Error 502: Bad
    # Gateway"): див. коментар у _handle_remote_command вище - ВІДДАЛЕНІ
    # команди "Включить/Отключить форму" більше не чіпають сам тунель,
    # лише цей прапорець (webapp_server.py читає його через
    # get_form_content_enabled на кожен запит, крім /control/*) - канал,
    # яким прийшла ця сама команда, лишається робочим для наступної.
    def _set_webapp_content_enabled(self, enabled):
        self._webapp_content_enabled = enabled
        self._webapp_should_run = enabled
        self._set_toggle_buttons(self.webapp_stop_button, self.webapp_start_button, is_on=enabled)
        self._refresh_webapp_status_text()

    def _check_webapp_tunnel_health(self):
        if not self._webapp_should_run:
            return
        # "якщо прибрана [галочка] - то тільки вручну це можна змінювати" -
        # жодного автовідновлення, навіть якщо форму запустили вручну.
        if not self._webapp_auto_manage:
            return
        if not paths.CLOUDFLARED_EXE.exists():
            return
        server_alive = self.webapp_server.is_alive()
        tunnel_alive = self.cloudflared_process is not None and self.cloudflared_process.poll() is None
        if server_alive and tunnel_alive:
            return
        if tunnel_alive and not server_alive:
            try:
                self.webapp_server.start()
            except OSError:
                pass
            return
        # Задача користувача: "спершу має запуститись телеграм бот. а через
        # 5 сек має почати запускатись форма" - без цього watchdog (кожні
        # 5с) сам одразу підхопив би тунель, ще до того, як спрацює явний
        # 5с відклад у _on_start_clicked вище.
        if self._webapp_not_before is not None and time.monotonic() < self._webapp_not_before:
            return
        if not self._webapp_tunnel_starting:
            self.webapp_public_url = ""
            self._start_webapp_tunnel()

    def _webapp_health_watchdog_tick(self):
        if self._webapp_should_run and not self._webapp_health_check_active:
            self._webapp_health_check_active = True
            threading.Thread(target=self._webapp_health_check_worker, daemon=True).start()
        # Задача користувача: "Просмотр таймеров"... "справжня цифра, а не
        # вигадування" - реальний момент наступного тіку, а не оцінка.
        self._webapp_next_watchdog_tick_at = time.monotonic() + self._webapp_check_interval_ms / 1000
        self.after(self._webapp_check_interval_ms, self._webapp_health_watchdog_tick)

    # Задача користувача (2026-08-16): "додай змогу редагувати ролі тут
    # теж... зміна ролі в мене - зміна ролі в клієнті" - webapp_server.py
    # вже записав нову роль у БД (свіжим з'єднанням у межах HTTP-запиту)
    # ДО виклику цього callback'а - тут лишається лише прикладна частина,
    # яка вже й так є в client_app.py: сповіщення користувача в Telegram
    # (_notify_role_change, той самий метод, що й локальне редагування
    # нижче) + оновлення вікна "Персонал", якщо воно зараз відкрите.
    # Викликається з потоку HTTP-сервера, тож _run_on_main_thread, той
    # самий принцип, що й _handle_remote_command/_handle_home_heartbeat.
    def _handle_set_role(self, telegram_id, old_role, new_role):
        self._run_on_main_thread(lambda: self._apply_set_role_notification(telegram_id, old_role, new_role))

    def _apply_set_role_notification(self, telegram_id, old_role, new_role):
        self._notify_role_change(telegram_id, old_role, new_role)
        if self.personnel_window is not None and self.personnel_window.winfo_exists():
            self._refresh_personnel()

    def _handle_home_heartbeat(self):
        # Викликається з потоку webapp_server (HTTP-запит), не головного
        # Tk-потоку - простий float-присвоєння, той самий рівень безпеки,
        # що вже й cloudflared_process/webapp_public_url тут (без locку).
        self._last_home_heartbeat_at = time.monotonic()

    def _sleep_interruptible(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if not self._webapp_should_run:
                return False
            time.sleep(min(1, deadline - time.monotonic()))
        return True

    def _probe_webapp_url(self):
        url = self.webapp_public_url
        if not url:
            self._webapp_last_probe_error = "нет активного адреса"
            return False
        try:
            with urllib.request.urlopen(f"{url.rstrip('/')}/index.html", timeout=6) as response:
                ok = 200 <= response.status < 400
                if not ok:
                    self._webapp_last_probe_error = f"HTTP {response.status}"
                return ok
        except urllib.error.HTTPError as exc:
            self._webapp_last_probe_error = f"HTTP {exc.code}"
            return False
        except urllib.error.URLError as exc:
            self._webapp_last_probe_error = f"сеть: {exc.reason}"
            return False
        except (OSError, ValueError) as exc:
            self._webapp_last_probe_error = str(exc)
            return False

    def _webapp_probe_confirms_down(self):
        for attempt in range(self._WEBAPP_PROBE_RETRY_ATTEMPTS):
            if self._probe_webapp_url():
                return False
            if attempt < self._WEBAPP_PROBE_RETRY_ATTEMPTS - 1:
                if not self._sleep_interruptible(self._WEBAPP_PROBE_RETRY_DELAY_SECONDS):
                    return False
        return True

    def _restart_and_wait_for_reconnect(self, timeout_seconds):
        self._run_on_main_thread(lambda: self._toggle_webapp_form("restart"))
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            time.sleep(1)
            if not self._webapp_should_run:
                return None
            if self._probe_webapp_url():
                return True
        return False

    def _restart_local_webapp_server(self):
        try:
            self.webapp_server.stop()
        except OSError:
            pass
        try:
            self.webapp_server.start()
        except OSError:
            pass

    def _webapp_health_check_worker(self):
        try:
            # "якщо прибрана [галочка] - то тільки вручну це можна
            # змінювати" - жодного автовідновлення, навіть для форми,
            # запущеної вручну, поки цей прапорець вимкнений.
            if not self._webapp_auto_manage:
                return
            # Захист навіть коли _webapp_should_run уже True (бот щойно
            # стартував) - цей фоновий воркер міг бути запланований ще ДО
            # того, як спрацював явний 5с відклад автозапуску форми.
            if self._webapp_not_before is not None and time.monotonic() < self._webapp_not_before:
                return
            # "не підключає з першого разу" - щойно видана адреса ще могла не
            # розповсюдитись по DNS/edge (cloudflared сам про це попереджає) -
            # не судимо тунель, поки він молодший за grace period.
            if (
                self._webapp_url_assigned_at is not None
                and time.monotonic() - self._webapp_url_assigned_at < self._WEBAPP_URL_GRACE_PERIOD_SECONDS
            ):
                return
            if not self._webapp_probe_confirms_down():
                return
            # Задача користувача (2026-08-13): "форма знову перепідключається
            # часто... має з першого разу вмикатись" - жодна проба не вдалась,
            # але це могло бути через тимчасове зависання ЛОКАЛЬНОГО сервера
            # (не самого тунелю Cloudflare) - _check_webapp_tunnel_health вище
            # вже розрізняє це для "процес живий/мертвий", але цей, повільніший
            # HTTP-пробник - ні: будь-яка невдача одразу рвала ВЕСЬ тунель і
            # видавала НОВУ адресу, хоча тунель міг бути цілком здоровим.
            # Спершу дешевий, непомітний крок - перезапустити лише локальний
            # сервер (та сама адреса) і перевірити ще раз, перш ніж рвати
            # публічну адресу.
            self._run_on_main_thread(self._restart_local_webapp_server)
            if self._sleep_interruptible(3) and self._probe_webapp_url():
                return
            attempt = 0
            while self._webapp_should_run:
                attempt += 1
                result = self._restart_and_wait_for_reconnect(self._WEBAPP_RECONNECT_TIMEOUT_SECONDS)
                if result is None:
                    return
                if result:
                    return
                if attempt >= self._WEBAPP_EXTENDED_FAILURE_THRESHOLD:
                    self._webapp_extended_failure_recovery()
                    return
        finally:
            self._webapp_health_check_active = False

    def _webapp_extended_failure_recovery(self):
        cooldown_seconds = self._WEBAPP_EXTENDED_COOLDOWN_SECONDS
        while self._webapp_should_run:
            if not self._sleep_interruptible(cooldown_seconds):
                return
            result = self._restart_and_wait_for_reconnect(self._WEBAPP_RECOVERY_PROBE_SECONDS)
            if result is None:
                return
            if result:
                self._webapp_check_interval_ms = self._WEBAPP_CALM_CHECK_INTERVAL_MS
                return
            cooldown_seconds = self._WEBAPP_SHORT_COOLDOWN_SECONDS

    # ---------- перевірка оновлень (раз в 5 хв) ----------
    # Задача користувача (2026-08-16): "щоб не в момент увімкненого серверу
    # це було... клієнт вимкнений, вранці увімкнув - отримав оновлення" -
    # локальний/тунельний манiфест замінено на GitHub Releases (github_
    # releases.py) - публічний, завжди доступний хостинг, не залежить від
    # того, чи я зараз онлайн. Сама перевірка тепер РЕАЛЬНИЙ мережевий
    # виклик (GET api.github.com), тож - на відміну від старої версії, що
    # читала локальний файл миттєво й синхронно - обов'язково у фоновому
    # потоці, інакше кожен тік/клік заморожував би все вікно.
    def _check_for_update_now(self, manual=False):
        """Спільна для періодичного тіку (_poll_for_update) і ручної
        кнопки-іконки (_manual_check_for_update). Результат приходить
        асинхронно через _apply_update_check_result."""
        # Той самий guard, що й раніше (2026-08-15, "напис і колір кнопки
        # розійшлись") - поки триває завантаження чи вже готово до
        # встановлення, нова перевірка - холостий хід. Реєплайнінг
        # періодичного ланцюжка все одно відбувається нижче, незалежно від
        # цього guard'а - інакше він назавжди зупинився б після першого ж
        # тіку, що застав завантаження в процесі.
        # Задача користувача (2026-08-19): "давай зробимо і завантаження і
        # встановлення автоматичним, але у визначений час" - готове до
        # встановлення оновлення чекало б тут БЕЗКІНЕЧНО (guard одразу
        # виходить), поки хтось не натисне кнопку вручну. Той самий
        # 5-хвилинний тік, що вже й так перевіряє нові версії, тепер
        # ЗАОДНО перевіряє "чи відкрилось дозволене вікно" для вже
        # ЗАВАНТАЖЕНОГО оновлення - окремий таймер не потрібен.
        if self._update_ready_to_install:
            if self._auto_update_window_open():
                self._install_downloaded_update()
                return
            if not manual:
                self.after(UPDATE_CHECK_INTERVAL_MS, self._poll_for_update)
            return
        if self._update_download_in_progress:
            if not manual:
                self.after(UPDATE_CHECK_INTERVAL_MS, self._poll_for_update)
            return
        # Задача користувача (2026-08-19, друга редакція): "все по
        # оновленню - тільки у відведений час" - раніше завантаження
        # стартувало одразу, як тільки знайдено новішу версію, незалежно
        # від часу доби; тепер і ЗАВАНТАЖЕННЯ теж чекає на дозволене
        # вікно, той самий тік, що вже й так перевіряє його для
        # встановлення вище. Ручний клік по кнопці (_on_update_button_
        # clicked напряму) як і раніше works будь-коли - це вікно гейтить
        # лише АВТОМАТИЧНИЙ шлях.
        if (
            self._pending_update_entry
            and self.settings.get("auto_update_enabled")
            and getattr(sys, "frozen", False)
            and self._auto_update_window_open()
        ):
            self._on_update_button_clicked()
            if not manual:
                self.after(UPDATE_CHECK_INTERVAL_MS, self._poll_for_update)
            return
        # Нитпік з аудиту коду (2026-08-16): швидкі повторні кліки по "⟳"
        # раніше плодили окремий мережевий запит на кожен клік - нешкідливо
        # (ідемпотентний GET), але марно. Той самий guard, що вже й для
        # завантаження/встановлення вище.
        if self._update_check_in_progress:
            return
        self._update_check_in_progress = True

        def worker():
            # Той самий фікс, що й у gui.py (2026-08-17, "оновлень не було.
            # нічого не прийшло" - реальна прогалина: помилка мережі/API
            # тут раніше тихо ставала тим самим "release = None", що й
            # "реально немає новішої версії" - користувач не міг
            # відрізнити збій перевірки від "ти вже на останній".
            check_error = None
            try:
                release = github_releases.get_latest_release(
                    paths.GITHUB_RELEASES_OWNER, paths.GITHUB_RELEASES_REPO, github_releases.CLIENT_TAG_PREFIX,
                    include_prerelease=(self.settings.get("update_channel") == "test"),
                )
            except RuntimeError as exc:
                release = None
                check_error = str(exc)
            entry = None
            if release:
                version = github_releases.release_version(release, github_releases.CLIENT_TAG_PREFIX)
                if version and update_check.is_newer(version, __version__):
                    entry = {"version": version, "release": release}
            self._run_on_main_thread(lambda: self._apply_update_check_result(entry, manual, check_error))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_update_check_result(self, entry, manual, check_error=None):
        self._update_check_in_progress = False
        # Реальний баг (аудит коду, 2026-08-15, той самий клас у gui.py):
        # ця перевірка могла СТАРТУВАТИ до завантаження/встановлення і
        # повернутись УЖЕ ПІСЛЯ - без guard'а вона переписала б кнопку
        # назад у "не завантажено", навіть коли реально готове до
        # встановлення оновлення вже чекає на клік.
        if not (self._update_ready_to_install or self._update_download_in_progress):
            self._pending_update_entry = entry
            if entry:
                self.update_button.configure(text=f"Обновление {entry['version']}")
                self.update_button.pack(side="top")
            elif self.update_button.winfo_ismapped():
                self.update_button.pack_forget()
            # Задача користувача (2026-08-19, друга редакція): "чомусь
            # скачало оновлення до встановленого часу. це вже мені не до
            # вподоби... все по оновленню - тільки у відведений час" -
            # ПЕРШИЙ варіант (завантаження одразу, лише встановлення
            # чекає вікна) прибрано за цим прямим запитом. Тепер ЖОДНОЇ
            # автоматичної дії тут - лише запис self._pending_update_
            # entry вище; сам старт завантаження чекає на дозволене вікно
            # в _check_for_update_now (періодичний тік) нижче.
            # Задача користувача (2026-08-15): "не має видвати спливаюче
            # вікно-повідомлення... просто тихесенько під кнопкою" - текст
            # замість messagebox; коли оновлення Є, сама кнопка "Обновление
            # X.X" вже все каже, тож тут текст лише очищається.
            if manual:
                if entry:
                    result_text = ""
                elif check_error:
                    result_text = f"Не удалось проверить обновления: {check_error}"
                else:
                    result_text = "Обновлений нет — установлена последняя версия."
                self.update_check_result_label.configure(text=result_text)
        if not manual:
            self.after(UPDATE_CHECK_INTERVAL_MS, self._poll_for_update)

    def _poll_for_update(self):
        self._check_for_update_now(manual=False)

    # Задача користувача: "кнопка оновити, яка показує чи є готове
    # оновлення, якщо перший сигнал якось пропустився" - ручна перевірка
    # ЗАРАЗ, не чекаючи наступного 5-хвилинного тіку. Не чіпає сам таймер
    # (_poll_for_update і далі йде своїм розкладом) - лише одноразово
    # виконує ту саму перевірку негайно.
    def _manual_check_for_update(self):
        self._check_for_update_now(manual=True)

    # Задача користувача (2026-08-14): "потрібен ще бекап версій програм.
    # щоб зберігався раз в 30 хв" - той самий принцип, що вже й у gui.py
    # (_schedule_code_backup_tick) - тут скопійований, бо client_app.py й
    # gui.py геть окремі процеси/вікна, кожен зі своїм власним циклом
    # root.after. create_code_snapshot сама вирішує, чи потрібен новий
    # архів (дедуплікація за хешем вмісту коду).
    def _schedule_code_backup_tick(self):
        def worker():
            try:
                code_backup.create_code_snapshot()
            except OSError as exc:
                # Реальний баг (аудит коду, 2026-08-14): той самий клас
                # except-var-clearing NameError, що й _notify_role_change
                # вище - error_text захоплюється ТУТ, поки exc ще живий.
                error_text = str(exc)
                self._run_on_main_thread(lambda: messagebox.showwarning(
                    "AI Automation", f"Не удалось создать автоматический снимок кода программы: {error_text}",
                ))

        threading.Thread(target=worker, daemon=True).start()
        self.after(1800000, self._schedule_code_backup_tick)

    # Задача користувача (2026-08-16): "зберігай 10 останніх копій онлайн" -
    # НЕ мій акаунт (той самий принцип, що вже пояснено в чаті для
    # оновлень client_app.py: "не можна змінювати" акаунти) - копіюємо у
    # OneDrive-теку САМОГО клієнта (os.environ["OneDrive"] на цій-таки
    # машині, та сама техніка, що вже перевірена наживо на моєму ПК для
    # AI_Automation_Updates) - жодного нового акаунта/API/токена, лише
    # локальний файловий запис, який OneDrive-клієнт сам вивантажує в
    # хмару. Викликається з фонового потоку (той самий, що й сам знімок) -
    # best-effort: якщо OneDrive не налаштований на цій машині, просто
    # тихо нічого не робимо (видно в _refresh_backup_status_text, не
    # спливаючим вікном на кожен тік).
    # Задача користувача (2026-08-18): "поств ліміти на бекапи. максимум
    # по 20 файлів" - було 10, окремо на кожен тип (db_backups/
    # config_backups рахуються незалежно, той самий принцип, що й раніше
    # для одної спільної теки).
    _ONEDRIVE_BACKUP_LIMIT = 20

    # Задача користувача (2026-08-16): "резервні копії будуть зберігатись
    # раз в годину" - той самий except Exception, що вже й _on_backup_now_
    # clicked вище (create_db_snapshot може впасти й на sqlite3-рівні, не
    # лише OSError), і той самий "покажи попередження на реальній помилці"
    # принцип, що вже перевірений у _schedule_code_backup_tick - раз на
    # годину не настільки часто, щоб попередження набридало, а мовчати про
    # реальний збій резервного копіювання - гірше.
    def _schedule_db_backup_tick(self):
        # Реальні знахідки (аудит коду, 2026-08-16):
        # 1. Погодинний автознімок нічим не блокувався від відновлення
        #    (_on_backup_restore_clicked) - обидва торкаються paths.DB_PATH,
        #    відновлення - сирим перезаписом файлу. Пропускаємо цей тік
        #    мовчки, якщо саме зараз триває відновлення - наступний тік
        #    (за годину) спробує знову.
        # 2. winfo_exists() - виклик Tk - раніше йшов НАПРЯМУ у фоновому
        #    потоці worker(), до _run_on_main_thread. Тепер уся перевірка
        #    "чи відкрите вікно" - на головному потоці, разом з оновленням.
        if not self._backup_restore_in_progress:
            def worker():
                try:
                    snapshot_path = create_db_snapshot(paths.DB_PATH)
                except Exception as exc:
                    error_text = str(exc)
                    self._run_on_main_thread(lambda: messagebox.showwarning(
                        "AI Automation", f"Не удалось создать автоматическую резервную копию: {error_text}",
                    ))
                    return
                self._mirror_backup_to_onedrive(snapshot_path, "db_backups", "app_data_*")
                self._create_and_mirror_config_snapshot()
                self._create_and_mirror_excel_backup()
                self._run_on_main_thread(self._refresh_backup_lists_if_open)

            threading.Thread(target=worker, daemon=True).start()
        self.after(_DB_BACKUP_TICK_MS, self._schedule_db_backup_tick)

    def _refresh_backup_lists_if_open(self):
        if self.backup_window is not None and self.backup_window.winfo_exists():
            self._refresh_backup_lists()

    # subfolder/glob_pattern тепер явні аргументи (2026-08-18, "всі файли
    # в своїх папках") — раніше subfolder вгадувався з glob_pattern
    # ("app_data_*"/інше), що ламалось би для Excel-бекапів (їхнє ім'я
    # починається з РЕАЛЬНОГО імені підключеного файлу, не фіксованого
    # префікса). Кожен тип пише у ВЛАСНУ підтеку, ротація рахується окремо
    # для кожної. standard_menu_cloud.cloud_folder_path() (не голий
    # os.environ.get("OneDrive")) — той самий пошук, що вже виправлений
    # для "Открыть папку": ця машина має ДВІ теки OneDrive під одним
    # акаунтом (особисту й тенантну), голий env var вказував би не на ту.
    def _mirror_backup_to_onedrive(self, snapshot_path, subfolder, glob_pattern):
        backups_root = standard_menu_cloud.cloud_folder_path()
        if backups_root is None:
            return
        target_dir = backups_root / subfolder
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(snapshot_path, target_dir / Path(snapshot_path).name)
            existing = sorted(
                target_dir.glob(glob_pattern), key=lambda path: path.stat().st_mtime,
            )
            excess = len(existing) - self._ONEDRIVE_BACKUP_LIMIT
            for old_path in existing[:max(excess, 0)]:
                old_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _create_and_mirror_config_snapshot(self):
        try:
            snapshot_path = config_backup.create_config_snapshot()
        except OSError:
            return
        if snapshot_path:
            self._mirror_backup_to_onedrive(snapshot_path, "config_backups", "config_*")

    # Задача користувача (2026-08-18): "дзеркалити Excel-бекапи в OneDrive
    # так само, як БД" - create_excel_backup() і так уже викликається на
    # кожен реальний запис у таблицю (sync_sheets_to_excel), але щогодинний
    # тік (той самий, що й БД/конфіг) гарантує СВІЖИЙ хмарний знімок навіть
    # у годину без жодного запису - той самий принцип, що вже й у БД-тіка.
    # glob_pattern - РЕАЛЬНЕ ім'я підключеного файлу (не фіксований
    # префікс, як у app_data_*/config_*), тож будується динамічно з
    # excel_source.backup_file_name_parts() - тим самим, що й сам
    # create_excel_backup() використовує для власного локального імені.
    def _create_and_mirror_excel_backup(self):
        try:
            snapshot_path = create_excel_backup()
        except Exception:
            return
        if not snapshot_path:
            return
        stem, suffix = excel_source.backup_file_name_parts()
        self._mirror_backup_to_onedrive(snapshot_path, "excel_backups", f"{stem}_*{suffix}")

    # Реальний баг (аудит коду, 2026-08-14): той самий клас зависання, що
    # вже знайшли й виправили для "Обновить эксели" (коментар нижче) - тут
    # цю ж помилку просто пропустили при копіюванні кнопки поруч.
    # update_check.download_update робить shutil.copytree/copyfile - для
    # "gui"-пакета це може бути весь проєктний каталог, синхронно на
    # головному Tk-потоці це виглядало б як повне зависання програми.
    # Задача користувача (2026-08-15): "давай вже працювати через
    # оновлення та автоперезапуск після підтвердження" - одна кнопка,
    # дві фази: перший клік ЗАВАНТАЖУЄ (як і раніше), другий (після
    # завантаження, кнопка вже каже "Установить и перезапустить") -
    # ВСТАНОВЛЮЄ. Сам другий клік - і є "підтвердження", жодного
    # додаткового спливаючого вікна (той самий принцип "клік завжди
    # свідомий", що й create_code_snapshot(force=True)).
    def _on_update_button_clicked(self):
        if self._update_ready_to_install:
            self._install_downloaded_update()
            return
        entry = self._pending_update_entry
        if not entry:
            return
        if self._update_download_in_progress:
            return
        self._update_download_in_progress = True
        self.update_button.configure(text="Загрузка обновления...", state="disabled")
        self._start_update_spinner()
        self.update_progress_bar.set(0)
        self.update_progress_bar.pack(side="top", pady=(4, 0))
        destination = Path(paths.BASE_DIR) / "updates"

        def worker():
            # Задача користувача (2026-08-14): "щоб створювався [бекап
            # коду] перед кожним оновленням" - force=True, той самий
            # принцип, що й у gui.py перед публікацією. Тут, а не на
            # головному потоці - і так уже у worker(), зайвий привід не
            # дублювати background-обгортку.
            #
            # Реальна знахідка (2026-08-15, живий продакшн): "шо це
            # вискакує при натисканні на оновлення клієнта?" - у зібраній
            # .exe версії (getattr(sys, "frozen", False)) create_code_
            # snapshot(force=True) ЗАВЖДИ кидає OSError (вихідних .py-
            # файлів у дистрибутиві просто немає - не рідкісний збій, а
            # постійний факт пакування). Попередження мало сенс для
            # РОЗРОБНИКА (gui.py, publish - там код дійсно є, dev-режим),
            # але тут - для звичайного бізнес-користувача, на КОЖНОМУ
            # оновленні, назавжди - лякаючий технічний попап без жодної
            # дії, яку людина могла б із ним зробити. Той самий принцип,
            # що вже застосований до решти повідомлень цього флоу ("не
            # має видвати спливаюче вікно... просто тихесенько") - у
            # зібраній версії спробу просто не робимо.
            if not getattr(sys, "frozen", False):
                try:
                    code_backup.create_code_snapshot(label="pre_update", force=True)
                except OSError as exc:
                    error_text = str(exc)
                    self._run_on_main_thread(lambda: messagebox.showwarning(
                        "AI Automation", f"Не удалось создать снимок кода перед обновлением: {error_text}",
                    ))
            error = None
            target = None

            def report_progress(fraction):
                self._run_on_main_thread(lambda: self.update_progress_bar.set(fraction))

            try:
                target = github_releases.download_and_extract_release(
                    entry["release"], destination, on_progress=report_progress,
                )
            except (RuntimeError, OSError) as exc:
                # Той самий фікс, що й у gui.py: download_and_extract_release()
                # не обгортає OSError від mkdir(parents=True, exist_ok=True) -
                # без цього ширшого except прапорець завантаження лишався б
                # True назавжди при переповненому диску/відсутності прав.
                error = str(exc)
            self._run_on_main_thread(lambda: self._on_update_download_finished(entry, target, error))

        threading.Thread(target=worker, daemon=True).start()

    def _start_update_spinner(self):
        self._update_spinner_step = 0
        self._advance_update_spinner()

    def _advance_update_spinner(self):
        if not self._update_download_in_progress:
            return
        self.update_button.configure(image=_spinner_frame(self._update_spinner_step))
        self._update_spinner_step = (self._update_spinner_step + 1) % _SPINNER_FRAME_COUNT
        self.after(_SPINNER_FRAME_MS, self._advance_update_spinner)

    def _on_update_download_finished(self, entry, target, error):
        self._update_download_in_progress = False
        self.update_button.configure(image=None)
        self.update_progress_bar.pack_forget()
        self.update_progress_bar.set(0)
        if error:
            self.update_button.configure(text=f"Обновление {entry['version']}", state="normal")
            # Задача користувача (2026-08-16): "як помилка описана - погано.
            # має бути зрозуміло користувачеві що робити" - сира технічна
            # помилка (напр. "[WinError 5] Access is denied: ...") нічого не
            # каже звичайному користувачу. Людська фраза з конкретною дією
            # ПЕРШОЮ, технічна деталь - ОСТАННЬОЮ, у дужках (не для
            # користувача, а щоб було що передати розробнику при зверненні).
            self.update_check_result_label.configure(
                text=f"Не удалось установить обновление. Попробуйте ещё раз позже "
                     f"или обратитесь к разработчику. ({error})"
            )
            return
        if not getattr(sys, "frozen", False):
            # dev-режим (python client_app.py): немає власної теки
            # зібраного .exe, яку можна безпечно замінити й перезапустити -
            # лишається старий ручний шлях, лише без спливаючого вікна.
            self.update_button.configure(text=f"Обновление {entry['version']}", state="normal")
            self.update_check_result_label.configure(
                text=f"Загружено в {target}. В dev-режиме примените вручную."
            )
            return
        self._downloaded_update_target = target
        self._update_ready_to_install = True
        self.update_check_result_label.configure(text="")
        self.update_button.configure(text="Установить и\nперезапустить", state="normal", fg_color=COLOR_ON)
        # Задача користувача (2026-08-19): якщо дозволене вікно вже
        # ВІДКРИТЕ саме в момент завершення завантаження - встановлюємо
        # одразу, не чекаючи наступного 5-хвилинного тіку
        # (_check_for_update_now, той самий _auto_update_window_open).
        if self._auto_update_window_open():
            self._install_downloaded_update()

    # Заміна файлів РЕАЛЬНО ЗАПУЩЕНОГО .exe напряму неможлива на Windows
    # (файл заблокований, поки процес живий) - стандартний прийом:
    # окремий .bat-скрипт чекає, поки цей PID зникне з tasklist, тоді
    # копіює нові файли поверх старої теки (robocopy БЕЗ /MIR - лише
    # накладання/перезапис файлів з нового пакета, нічого зайвого з теки
    # призначення НЕ видаляється, тож system/backups/db_backups/app_data.
    # sqlite3/test_sklad.xlsx - усе, чого свідомо немає в самому build -
    # лишається недоторканим), перезапускає .exe і сам себе видаляє.
    # Скрипт запускається ВІДОКРЕМЛЕНИМ процесом (DETACHED_PROCESS) - щоб
    # пережити закриття цієї програми, яке відбувається одразу після.
    def _install_downloaded_update(self):
        # Реальна знахідка (аудит коду, 2026-08-16): той самий guard, що вже
        # є в gui.py (2026-08-15) - без нього подвійний клік по "Установить
        # и перезапустить" (кнопка лишається активною весь час) запускав би
        # ДВА .bat-скрипти й, відповідно, два паралельних запуски щойно
        # оновленого .exe проти однієї й тієї ж БД - для client_app.py це
        # ще й два одночасних Telegram-бота на тому самому токені (409
        # конфлікт) і два тунелі.
        if self._update_install_in_progress:
            return
        source = self._downloaded_update_target
        if not source or not Path(source).exists():
            return
        self._update_install_in_progress = True
        self.update_button.configure(state="disabled")
        # Реальна знахідка (аудит коду, 2026-08-16): той самий is_closing,
        # що вже виставляє gui.py на цьому ж кроці - без нього фонові тіки
        # (статус бота, погодинний автознімок БД тощо) могли ще встигнути
        # спрацювати в ~200мс вікні до self.destroy() нижче й спробувати
        # звернутись до вже знищеного Tk-вікна (шумний, хоч і нешкідливий
        # TclError). _run_on_main_thread уже й так перевіряє is_closing.
        self.is_closing = True
        install_dir = paths.BASE_DIR
        exe_path = Path(sys.executable)
        pid = os.getpid()
        script_path = Path(tempfile.gettempdir()) / f"ai_automation_client_update_{pid}.bat"
        # %SystemRoot%\System32\ - явно, повними шляхами: якщо на ПК
        # користувача встановлено Git for Windows (чи щось інше, що кладе
        # свій власний tasklist/find у PATH попереду системних), звичайний
        # виклик "find" міг би зловити ЧУЖИЙ, несумісний бінарник - цикл
        # очікування тоді ламається мовчки (ERRORLEVEL ніколи не "0", тож
        # скрипт одразу біжить копіювати файли, поки .exe ще живий).
        script_lines = [
            # Реальний баг (2026-08-15, живий продакшн): шлях користувача
            # містив кирилицю (OneDrive-тека) - .bat писався як UTF-8, але
            # cmd.exe за замовчуванням читає файл системною кодовою
            # сторінкою (не UTF-8), тож кириличні символи в шляхах
            # перетворювались на "сміття" - robocopy/start отримували
            # НЕІСНУЮЧИЙ шлях ("Windows не может найти..."). chcp 65001 -
            # ПЕРШИМ рядком (до "@echo off"!) - перевірено реальним запуском:
            # BOM+chcp-другим-рядком лишає "сміття" перед першим токеном
            # ("@echo off" не розпізнається), a chcp-першим без BOM працює
            # чисто.
            "chcp 65001 >nul",
            "@echo off",
            "setlocal",
            ":waitloop",
            f'"%SystemRoot%\\System32\\tasklist.exe" /FI "PID eq {pid}" 2>NUL | "%SystemRoot%\\System32\\find.exe" "{pid}" >NUL',
            'if "%ERRORLEVEL%"=="0" (',
            # timeout.exe вимагає інтерактивної консолі й одразу падає, якщо
            # запущений без неї (саме наш випадок - detached-процес без
            # вікна) - ping на localhost, старий, але надійний трюк саме
            # для цього: 2 пакети ~= затримка ~1с, жодної залежності від
            # консолі.
            '    "%SystemRoot%\\System32\\ping.exe" -n 2 127.0.0.1 >nul',
            "    goto waitloop",
            ")",
            # Реальний баг (2026-08-17, живий продакшн): пакет, зібраний і
            # опублікований помилково (build_exe.py) містив system/settings.
            # json ІНШОЇ машини - robocopy без /XF це мовчки перезаписала б,
            # стерши реальний токен Telegram/шлях до Excel/усі налаштування
            # цього ПК (["ключ Telegram злітає після кожного оновлення"]).
            # Джерело вже виправлено (build_exe.py більше не підкладає
            # settings.json у клієнтський пакет), але це другий, незалежний
            # рубіж захисту: /XF settings.json означає, що ЦЕЙ файл НІКОЛИ
            # не буде перезаписаний оновленням, хоч би що опинилось у
            # завантаженому пакеті.
            #
            # Той самий клас бага, лише інший файл (2026-08-17, живий
            # продакшн, "куди дівається час відвідування"): тестовий запуск
            # dist/AI_Automation_Client.exe на машині розробника створив
            # СВІЖУ, майже порожню app_data.sqlite3 - опублікований пакет
            # client-v0.2.57 підхопив саме її (не було жодного /XF для
            # бази даних), і встановлення на робочому ПК затерло б реальний
            # склад/персонал/журнали. /XF app_data.sqlite3 - той самий
            # принцип, що й settings.json вище: файл користувача НІКОЛИ не
            # буде перезаписаний оновленням, незалежно від того, що опиниться
            # в пакеті.
            f'robocopy "{source}" "{install_dir}" /E /IS /IT /XF settings.json app_data.sqlite3 /R:5 /W:1 >NUL',
            # Реальний баг (аудит коду, 2026-08-15): раніше джерело
            # видалялось БЕЗУМОВНО, незалежно від того, чи robocopy реально
            # встиг скопіювати все (код виходу robocopy >=8 - це помилка,
            # 0-7 - різні варіанти успіху). Без цієї перевірки невдале
            # копіювання (файл ще заблокований cloudflared.exe тощо)
            # мовчки видаляло б завантажений пакет, лишаючи частково
            # оновлену/непошкоджену встановлену версію без жодного сліду
            # проблеми і без можливості повторити встановлення.
            'if %ERRORLEVEL% LSS 8 (',
            f'    rmdir /s /q "{source}" >nul 2>&1',
            ")",
            f'start "" "{exe_path}"',
            '(goto) 2>nul & del "%~f0"',
        ]
        # Реальна знахідка (аудит коду, 2026-08-16): без цього try/except
        # рідкісна, але можлива помилка запису .bat-файлу чи запуску
        # cmd.exe (диск повний, антивірус) лишала б is_closing=True (фонові
        # тіки вже мовчки відкидаються) і заблоковану кнопку, без жодного
        # повідомлення й без _stop_webapp_tunnel()/destroy() нижче.
        try:
            script_path.write_text("\r\n".join(script_lines) + "\r\n", encoding="utf-8")
            # Реальна знахідка (перевірено прямим запуском): DETACHED_PROCESS
            # (зовсім без консолі) - tasklist/find/robocopy мовчки НЕ
            # спрацьовують (errorlevel каже "0", але вивід порожній) - скрипт
            # ніколи не виходить з циклу очікування. CREATE_NEW_CONSOLE
            # (реальна, лише прихована консоль) - те, що дійсно працює.
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE
            subprocess.Popen(
                ["cmd", "/c", str(script_path)],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
                startupinfo=startupinfo,
                close_fds=True,
            )
        except OSError as exc:
            self.is_closing = False
            self._update_install_in_progress = False
            self.update_button.configure(state="normal")
            messagebox.showerror(
                "Установка обновления",
                f"Не удалось запустить установку обновления: {exc}\n\nПопробуйте ещё раз.",
            )
            return
        # Реальний баг (2026-08-15, "чому бот формує нові посилання"):
        # раніше тут одразу йшов self.destroy() - той самий Toplevel/CTk
        # .destroy(), що НЕ каскадно вбиває дочірні subprocess.Popen
        # (cloudflared.exe) - Windows не робить цього сама без Job Object.
        # Старий cloudflared.exe лишався живим ПІД ЧАС і ПІСЛЯ підміни
        # файлів (robocopy/relaunch у .bat вище), паралельно з новим,
        # щойно запущеним інстансом - обидва одночасно тримали з'єднання
        # до ОДНОГО іменованого тунелю, і Cloudflare плутано розподіляв
        # запити між живим і "зомбі" з'єднанням (звідси нестабільні
        # завантаження форми). _stop_webapp_tunnel (той самий виклик, що
        # й у _on_exit_clicked) явно термінує процес ПЕРЕД destroy().
        self._stop_webapp_tunnel()
        self.after(200, self.destroy)

    # ---------- Excel: оновити з джерела / переглянути копію ----------
    # Реальний баг (2026-08-13): "натиснув оновити - і зависає" - читання
    # Excel (openpyxl, data_only=True) і подальший import_workbook у SQLite
    # раніше виконувались ПРЯМО на головному Tk-потоці - для реальної
    # робочої таблиці (сотні/тисячі рядків по кількох листах) це легко
    # кілька секунд синхронного блокування event loop, що виглядає як
    # повне зависання (жодної різниці для користувача між "рахує" і
    # "зламалось"). У gui.py той самий виклик є лише РАЗ, на старті
    # програми (до появи вікна) - тому там ця вада не проявлялась. Тут же
    # кнопка натискається ПОВЕРХ уже робочого інтерфейсу - обов'язково
    # фоновий потік, з видимим "Обновление..." і заблокованою кнопкою на
    # час роботи (той самий принцип, що вже діє для старту/стопу бота).
    def _on_refresh_excel_clicked(self):
        if self._excel_refresh_in_progress:
            return
        self._excel_refresh_in_progress = True
        if self.refresh_excel_button is not None:
            self.refresh_excel_button.configure(text="\U0001F504  Обновление...", state="disabled")

        def worker():
            # Реальний баг (2026-08-13): "SQLite objects created in a thread
            # can only be used in that same thread" - self.store.conn
            # створений на головному потоці в __init__, тож фоновий потік не
            # може ним користуватись напряму. Той самий прийом, що вже й у
            # webapp_server.py - окреме, власне з'єднання ЦЬОГО потоку.
            error = None
            try:
                ensure_workbook_has_required_sheets()
                workbook = excel_source.open_workbook(data_only=True)
                try:
                    thread_store = ExcelSqliteStore(paths.DB_PATH)
                    try:
                        thread_store.import_workbook(workbook, READ_ONLY_SHEETS)
                    finally:
                        thread_store.close()
                finally:
                    workbook.close()
            except Exception as exc:
                error = str(exc)
            self._run_on_main_thread(lambda: self._on_excel_refresh_finished(error))

        threading.Thread(target=worker, daemon=True).start()

    def _on_excel_refresh_finished(self, error):
        self._excel_refresh_in_progress = False
        if self.refresh_excel_button is not None:
            self.refresh_excel_button.configure(text="\U0001F504  Обновить эксели", state="normal")
        if error:
            messagebox.showerror("AI Automation", f"Не удалось обновить: {error}")
            return
        messagebox.showinfo("AI Automation", "Таблица Excel обновлена.")

    # Задача користувача (2026-08-16): "прибери ту кнопку звідти, це реально
    # небезпечно. хай при цьому форму данные відкриває у браузері" - стара
    # реалізація (копія файлу + os.startfile, реальна MS Excel) була
    # видалена: відкритий у Excel файл-копія міг лишити хибне враження, що
    # ручні правки там щось означають, хоча це одноразова копія в темп-теці,
    # яка ніколи нікуди не зберігається назад - плутанина, не безпека даних
    # як така, але й далі зайвий, незрозумілий крок. Тепер - той самий
    # переглядач "Дані" (data.html), що вже відкривається з самого Telegram-
    # бота (_data_browser_webapp_button, telegram_dialog_core.py) - лише
    # ЧИТАЄ дані, ніякого ризику розсинхронізації з базою.
    #
    # Реальний баг (2026-08-16, знайдено користувачем): спершу тут стояло
    # is_admin=True ("той, хто натиснув кнопку, і так має повний доступ") -
    # проте is_admin вмикає РЕДАГУВАННЯ порогу "Низкий остаток"
    # (can_edit_low_stock_threshold), а це - реальний ЗАПИС на сервер, що
    # вимагає дійсного Telegram.WebApp.initData (HMAC-перевірка,
    # webapp_server.py._validate_init_data). Звичайна вкладка браузера,
    # відкрита через webbrowser.open() нижче, initData не має і НІКОЛИ не
    # матиме (це не WebView Telegram) - кнопка "Применить" показувалась,
    # але будь-яка спроба завжди падала з "пустой initData". is_admin=False
    # ховає цей редактор цілком (той самий client-side прапорець, що вже
    # ховає його для звичайних співробітників у самому боті) - сторінка
    # лишається тим, чим і задумана: чистий перегляд, без зламаних кнопок.
    def _on_open_data_in_browser_clicked(self):
        if not self.webapp_public_url:
            messagebox.showerror("AI Automation", "Форма сейчас не запущена — сначала включите её.")
            return
        worker = self.telegram_worker
        if worker is None:
            messagebox.showerror("AI Automation", "Бот не подключён — без него нельзя собрать данные для показа.")
            return
        try:
            ctx = worker._webapp_data_browser_context(self.store, is_admin=False)
        except Exception as exc:
            messagebox.showerror("AI Automation", f"Не удалось подготовить данные: {exc}")
            return
        # Реальна знахідка (аудит коду, 2026-08-16): на відміну від Mini App
        # кнопок (WebView Telegram), це посилання відкривається у ЗВИЧАЙНОМУ
        # браузері й лишається в його історії - коротша TTL (1 година замість
        # 12) обмежує, як довго "живе" токен з реальними даними складу там.
        token = webapp_server.register_context(ctx, ttl_seconds=webapp_server._BROWSER_VIEW_CONTEXT_TTL_SECONDS)
        url = f"{self.webapp_public_url.rstrip('/')}/data.html?t={token}"
        webbrowser.open(url)

    # ---------- тема ----------
    def _on_theme_toggle(self):
        self._dark_mode = not self._dark_mode
        ctk.set_appearance_mode("dark" if self._dark_mode else "light")
        self.theme_toggle_button.configure(text="Светлая" if self._dark_mode else "Тёмная")
        self.settings.set("client_dark_mode", self._dark_mode)

    # ---------- вихід ----------
    # Задача користувача (2026-08-18, живий продакшн - "не працює кнопка
    # перезапустить... я закрив хрестиком, і вона не відкрилась"): реальний
    # баг - WM_DELETE_WINDOW (хрестик вікна) і кнопка "Выход" ОБИДВІ вели в
    # ОДИН _on_exit_clicked, який БЕЗУМОВНО писав graceful-exit позначку.
    # Але задача користувача, яка ввела цю позначку (2026-08-17), явно
    # називала саме кнопку "Выход" ("вихід программи через кнопку вихід"),
    # а хрестик - інша дія: користувач очікує, що watchdog-перевірка
    # відрізнить "я справді хочу вийти" (кнопка) від "просто закрив вікно"
    # (хрестик, як і крах) і в другому випадку все одно перезапустить.
    # Тепер два окремі, тонкі входи в СПІЛЬНИЙ _shutdown - позначку пише
    # лише "Выход".
    def _on_exit_clicked(self):
        self._write_graceful_exit_marker()
        self._shutdown()

    def _on_window_close_clicked(self):
        self._shutdown()

    def _write_graceful_exit_marker(self):
        # Пишеться ЗАВЖДИ (не лише коли перезапуск-при-закритті увімкнено) -
        # дешево, і на випадок, якщо користувач увімкне цю опцію пізніше,
        # стара позначка не лишиться "зависла" з попереднього разу.
        try:
            paths.GRACEFUL_EXIT_MARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
            paths.GRACEFUL_EXIT_MARKER_PATH.write_text("", encoding="utf-8")
        except OSError:
            pass

    def _shutdown(self):
        # Той самий порядок, що й gui.py.on_close: is_closing=True ПЕРШИМ
        # (щоб жоден фоновий callback більше не чіплявся до вже знищеного
        # вікна), стоп бота - "запустив і забув" (daemon-потік), без
        # очікування завершення - інакше on_stopped через _run_on_main_
        # thread ніколи не спрацював би (is_closing вже True гасить його).
        self.is_closing = True
        self._webapp_should_run = False
        self._stop_webapp_tunnel()
        worker = self.telegram_worker
        if worker and worker.thread and worker.thread.is_alive():
            threading.Thread(target=worker.stop, daemon=True).start()
        self.store.close()
        self.destroy()


# "Хмарна істина" для стандартного меню — Задача користувача (2026-08-18),
# три уточнення того самого дня:
#   1. "якщо при запуску в застосунку не та істина що в хмарі -
#      вирівнюється з хмари" - хмара перемагає локальний стан.
#   2. "лише якщо кнопку натис - хмара оновилась... точний контроль" -
#      запис У хмару лише через explicit-кнопку в gui.py
#      (/control/save_standard_menu_to_cloud), НІКОЛИ звідси.
#   3. "розкладки кнопок більше не можуть братися нізвідки окрім як з
#      хмари. а точніше копіювання з хмари в локальний файл щоб лагів не
#      було" - крім підлаштування SQLite (для Редактора кнопок), хмарний
#      стан ще й КОПІЮЄТЬСЯ в окремий локальний файл-кеш
#      (paths.STANDARD_MENU_CACHE_PATH, звичайна тека system/) - реальна
#      копія на диску, а не лише рядок у БД, щоб показ меню бота ніколи не
#      залежав від стану синхронізації самого OneDrive-файлу.
#
# Викликається РІВНО ОДИН РАЗ одразу після відкриття self.store при старті
# (не з ExcelSqliteStore.__init__ у warehouse_data.py — див. коментар на
# початку standard_menu_cloud.py: gui.py теж створює ExcelSqliteStore
# локально, а її власне дерево custom_menu_buttons давно мертве, тож ця
# синхронізація мала б сидіти лише тут, у client_app.py, реальному хості
# живого бота).
#
# Хмара відсутня (адміністратор ще НІ РАЗУ не натискав кнопку "Зберегти
# стандарт у хмару") - НЕМА чого звіряти, локальний стан (уже вирішений
# безпечним фолбеком _apply_standard_menu_policy - 5 кнопок "(форма)")
# лишається як є, і САМЕ ВІН стає початковим вмістом локального кешу, щоб
# кеш ніколи не був порожнім. Якщо хмара вже є - для КОЖНОГО спільного
# ключа хмара перемагає (підлаштовуємо локальний стан під неї); ключі,
# яких хмара ще не знає (майбутні версії коду), лишаються локальними
# значеннями - потраплять у хмару, коли адміністратор наступного разу явно
# натисне кнопку.
def _reconcile_standard_menu_with_cloud(store):
    cloud_state = standard_menu_cloud.read_cloud_state()
    local_state = store.get_standard_menu_state()
    if cloud_state is None:
        standard_menu_cloud.write_local_cache(local_state)
        return
    merged_state = dict(local_state)
    merged_state.update({key: value for key, value in cloud_state.items() if key in local_state})
    if merged_state != local_state:
        store.apply_standard_menu_state(merged_state)
    standard_menu_cloud.write_local_cache(merged_state)


# Задача користувача (2026-08-17): "якщо програма закриється - то щоб
# запустилась знову, якщо включений ПК" - викликається watchdog_task.py's
# запланованим завданням (раз/хв), НЕ звичайним запуском - тому виконує
# перевірку й одразу виходить, без відкриття GUI. tasklist з двома
# фільтрами (IMAGENAME + PID ne <свій>) - інакше сама перевірка завжди
# "бачила б" себе саму серед процесів з тим самим іменем.
def _run_watchdog_check():
    if not getattr(sys, "frozen", False):
        return
    exe_path = Path(sys.executable)
    result = subprocess.run(
        [
            "tasklist", "/FI", f"IMAGENAME eq {exe_path.name}", "/FI", f"PID ne {os.getpid()}",
            "/FO", "CSV", "/NH",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    already_running = exe_path.name.lower() in (result.stdout or "").lower()
    if already_running:
        return
    marker = paths.GRACEFUL_EXIT_MARKER_PATH
    if marker.exists():
        # Останнє закриття було свідомим (кнопка "Выход") - споживаємо
        # позначку й нічого не запускаємо. Якщо процес зникне ЗНОВУ (без
        # нового кліку "Выход") - позначки вже не буде, і наступна
        # перевірка коректно перезапустить.
        marker.unlink(missing_ok=True)
        return
    subprocess.Popen([str(exe_path)])


if __name__ == "__main__":
    if "--watchdog-check" in sys.argv:
        _run_watchdog_check()
    else:
        app = ClientApp()
        app.mainloop()
