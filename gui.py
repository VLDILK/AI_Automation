"""Десктопний GUI на Tkinter: головне меню, перегляд/редагування таблиці,
налаштування, журнал дій, керування Telegram-ботом з інтерфейсу.

Тримає власний ExcelSqliteStore(DB_PATH) — окреме з'єднання від того, яке
використовує TelegramBotWorker; обидва пишуть в один і той самий файл БД.

TelegramBotWorker імпортується не на рівні модуля, а локально всередині
_start_telegram_from_settings — інакше вийшов би циклічний імпорт: main.py
імпортує ExcelViewerApp з цього файлу (у своєму launcher-блоці), а цей файл
імпортував би TelegramBotWorker з main.py. Локальний імпорт спрацьовує,
бо на момент виклику _start_telegram_from_settings (уже після запуску
програми) main.py вже повністю завантажений.
"""

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
import urllib.error
import urllib.request
import webbrowser
from tkinter import ttk, messagebox, filedialog, simpledialog, colorchooser
from datetime import datetime
from pathlib import Path

import excel_source
import github_releases
import onedrive_sync
import paths
import permissions as perm
import code_backup
import config_backup
import remote_control_client
import servers_registry
import standard_menu_cloud
import update_check
from i18n import DEFAULT_LANGUAGE, translate
from paths import BASE_DIR, CLOUDFLARED_EXE, DB_PATH, DISPLAY_SETTINGS_PATH, FILE_PATH, SETTINGS_PATH
from settings import DisplaySettingsStore, REQUEST_PROCESSING_MODES, SettingsStore
from utils import _display_bot_number, _display_value
from webapp_server import WebappServer
from warehouse_data import (
    ExcelSqliteStore,
    ANTISEPTIC_SHEET_NAME,
    BOT_MESSAGE_DEFAULTS,
    DB_BACKUP_LIMIT,
    SALES_SHEET_NAME,
    CUSTOM_BUTTON_ACTIONS,
    antiseptic_columns,
    apply_standard_table_format,
    create_db_snapshot,
    ensure_workbook_has_required_sheets,
    list_db_snapshots,
    maybe_create_scheduled_snapshot,
    regenerate_excel_after_restore,
    restore_db_snapshot,
    _backup_encryption_password,
    _set_backup_encryption_password,
    sales_columns,
    sync_sheet_to_excel,
    sync_sheets_to_excel,
    warehouse_columns,
)

# Задача користувача (2026-08-12): перша версія, з якої тепер відлічуються
# оновлення (update_check.py) - до цього номер версії ніде не фіксувався.
__version__ = "1.0.69"
UPDATE_CHECK_INTERVAL_MS = 5 * 60 * 1000

PAGE_SIZE = 100

READ_ONLY_SHEETS = {
    "АНАЛИТИКА ПРОДАЖ",
    "АНАЛИТИКА КЛИЕНТОВ",
    "АНАЛИТИКА МЕНЕДЖЕРОВ",
}

DISPLAY_DATE_FORMATS = [
    ("yyyy.mm.dd_dow_hhmm", "Год.Месяц.День + день недели", "2026.07.07 ВТ 16:58"),
    ("dd.mm.yyyy_hhmm", "День.Месяц.Год", "07.07.2026 16:58"),
    ("yyyy-mm-dd_hhmm", "Год-Месяц-День", "2026-07-07 16:58"),
    ("dd_slash_mm_slash_yyyy_hhmm", "День/Месяц/Год", "07/07/2026 16:58"),
    ("dd_month_yyyy_hhmm", "День месяц год", "07 июля 2026 16:58"),
    ("dow_dd.mm.yyyy_hhmm", "День недели перед датой", "ВТ 07.07.2026 16:58"),
    ("yyyy.mm.dd_hhmmss", "С секундами", "2026.07.07 16:58:47"),
    ("iso_minutes", "ISO короткий", "2026-07-07T16:58"),
    ("hhmm_dd.mm.yyyy", "Время перед датой", "16:58 07.07.2026"),
    ("dd.mm.yy_hhmm", "Короткий год", "07.07.26 16:58"),
]

RU_WEEKDAYS = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]
RU_MONTHS = [
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]


class ExcelViewerApp:
    # Реальна скарга користувача: Ctrl+C/V/X (копіювати/вставити/вирізати) не
    # працювали в ЖОДНОМУ полі програми, коли активна розкладка клавіатури
    # НЕ англійська (укр./рос.). Причина: стандартні прив'язки Tkinter
    # спрацьовують за keysym — СИМВОЛОМ, який видає розкладка — а не-латинська
    # розкладка на фізичній клавіші C/V/X видає зовсім інший символ (напр.
    # кирилицю), тож "<Control-c>" ніколи не збігається. event.keycode —
    # віртуальний код фізичної клавіші (у Windows незалежний від активної
    # розкладки), тож ловимо Ctrl+KeyPress за keycode і самі викликаємо
    # потрібну вбудовану віртуальну подію Tk. На англійській розкладці Tk сам
    # обирає БІЛЬШ СПЕЦИФІЧНУ стандартну прив'язку ("<Control-c>" специфічніша
    # за загальний "<Control-KeyPress>"), тож дублювання дії не станеться.
    _CLIPBOARD_SHORTCUT_KEYCODES = {
        67: "<<Copy>>",
        86: "<<Paste>>",
        88: "<<Cut>>",
        65: "<<SelectAll>>",
    }

    # Задача користувача (2026-08-15): "додай темну тему тумблер... на всю
    # прогу... і щоб вибір зберігався" - на відміну від client_app.py
    # (customtkinter, вбудована light/dark система), тут звичайний Tkinter -
    # немає жодного централізованого поняття теми, кожен колір досі
    # хардкоджений окремо на кожному віджеті. Замість переписувати ВСІ
    # виклики tk.Frame/tk.Label/tk.Button по всьому файлу (тисячі місць,
    # непід'ємний ризик щось зламати) - рекурсивний "перепофарбовувач"
    # (_apply_theme нижче), що йде по вже ПОБУДОВАНОМУ дереву віджетів і
    # виставляє bg/fg за роллю кожного класу. _MUTED_FG_COLORS -
    # "приглушений підпис" (сірий вторинний текст) ПЕРЕФАРБОВУЄТЬСЯ у
    # тему-відповідний приглушений колір (інакше "gray40" на темному тлі
    # був би майже невидимий). _SEMANTIC_FG_COLORS - навпаки, ЛИШАЄТЬСЯ
    # без змін (червоне "x" видалення/зелене "+" додавання/синя кнопка
    # оновлення/кольорові індикатори статусу несуть смисл, а не оформлення).
    _LIGHT_THEME = {
        "bg": "#F2F3F5",
        "panel_bg": "#FFFFFF",
        "fg": "#1F2328",
        "muted_fg": "#5B6470",
        "entry_bg": "#FFFFFF",
        "button_bg": "#F6F8FA",
        "button_fg": "#1F2328",
        "select_bg": "#CFE3FF",
        "border": "#D8DDE3",
    }
    _DARK_THEME = {
        "bg": "#1A1D21",
        "panel_bg": "#25282D",
        "fg": "#E5E7EA",
        "muted_fg": "#9AA1AB",
        "entry_bg": "#2C3036",
        "button_bg": "#33383F",
        "button_fg": "#E5E7EA",
        "select_bg": "#3A5478",
        "border": "#3A3F46",
    }
    _MUTED_FG_COLORS = {"gray40", "gray50", "#666666", "#8c959f", "#555555", "#333333", "#57606a"}
    # Задача користувача: "Персонал... роль-чіп" - кольорова плашка ролі
    # (перевага_адміну = найпомітніший колір, вниз по рангу прав). Один
    # набір, незалежний від теми (як і решта смислових кольорів вище) -
    # значення ролі має лишатись впізнаваним однаково світлим і темним.
    # Задача користувача (2026-08-16): "роби такого адміна і в домашній і
    # в клієнті" - те саме має виглядати однаково в client_app.py, тож
    # сам набір кольорів переїхав у permissions.py (спільний для обох
    # программ), тут лише посилання.
    _ROLE_CHIP_COLORS = perm.ROLE_CHIP_COLORS
    # Задача користувача (2026-08-16): "зроби нерухомими ролі" - фіксована
    # ширина чіпа ролі в "Персонал", підібрана під найдовший підпис із
    # perm.ROLE_LABELS ("Адміністратор" = 14 симв. + " ▾" = 16).
    _ROLE_CHIP_WIDTH = max(len(f"{label} ▾") for label in perm.ROLE_LABELS.values())
    # Той самий фікс для "останньої активності" поруч - покриває всі
    # відносні варіанти _format_last_seen ("сьогодні, 14:32" - найдовший
    # серед типових; повний формат дати для рідкісного вікна 2-6 днів тому
    # МОЖЕ вийти за межі - Label з width= це лише мінімум, не жорстка межа,
    # тож той один рядок просто трохи розшириться, а не обріжеться).
    _LAST_SEEN_WIDTH = 16
    _SEMANTIC_FG_COLORS = {
        "white", "#d1242f", "#1a7f37", "#0969da", "#2F7BD9", "#255FA8",
        "#1D9E75", "#B23B3B", "red", "green", "darkgreen",
        "#8a5a00",  # бейдж "виняток" (одиниця виміру) - лишається впізнаваним у обох темах
    } | {fg for _bg, fg in _ROLE_CHIP_COLORS.values()}
    _SEMANTIC_BG_COLORS = {bg.lower() for bg, _fg in _ROLE_CHIP_COLORS.values()} | {
        "#fff3d6",
        "#ddf4ff",  # бейдж "gui" (журнал оновлень)
        "#dafbe1",  # бейдж "client" (журнал оновлень)
    }

    def _theme(self):
        return self._DARK_THEME if self._dark_mode else self._LIGHT_THEME

    # Рекурсивний прохід від кореня (за замовчуванням self.root) - охоплює
    # ВСЕ, що вже побудоване, включно з уже відкритими Toplevel-вікнами
    # (Персонал/Журнали/діалоги) - вони теж числяться дітьми свого master у
    # winfo_children(), той самий обхід дерева, що вже використовує
    # _clear_frame. Викликається (1) один раз наприкінці __init__ (охоплює
    # все, що будується одразу при старті), (2) одразу після перемикання
    # тумблера, (3) наприкінці кожного білдера нового Toplevel/попапу
    # (список - _restyle_after_window_open нижче).
    def _apply_theme(self, widget=None):
        if widget is None:
            # Реальна скарга (2026-08-15): "світлі полоси" - ttk.Scrollbar
            # (і будь-який інший ttk-віджет: Combobox/Notebook) НЕ підпадає
            # під жоден isinstance-розгалуження нижче (окремий, style-based
            # рушій оформлення, не звичайні bg/fg атрибути tk-віджетів) -
            # без цього смуга прокрутки лишалась світлою навіть у темному
            # режимі. Викликається лише РАЗ на кожен виклик _apply_theme
            # (не в рекурсії нижче - там widget завжди явний, ніколи None).
            self._apply_ttk_theme()
            self._draw_theme_toggle_switch()
        theme = self._theme()
        widget = widget or self.root
        try:
            self._apply_theme_to_widget(widget, theme)
        except tk.TclError:
            return
        for child in widget.winfo_children():
            self._apply_theme(child)

    # "vista"/"winnative" (типовий ttk-рушій на Windows) ІГНОРУЄ ttk.Style-
    # налаштування кольору для Scrollbar/Combobox/Notebook - лише "clam"
    # (кросплатформний, плаский) справді малює задані кольори. Тому
    # перемикаємось на "clam" ЛИШЕ в темному режимі (де це необхідно, бо
    # інакше смуга прокрутки лишається світлою) - у світлому лишаємо
    # рідний Windows-вигляд незмінним, як і завжди був.
    _ORIGINAL_TTK_THEME = None

    def _apply_ttk_theme(self):
        style = ttk.Style()
        if self._ORIGINAL_TTK_THEME is None:
            ExcelViewerApp._ORIGINAL_TTK_THEME = style.theme_use()
        theme = self._theme()
        if self._dark_mode:
            style.theme_use("clam")
            style.configure(
                "Vertical.TScrollbar", background=theme["button_bg"], troughcolor=theme["bg"],
                bordercolor=theme["border"], arrowcolor=theme["fg"],
                darkcolor=theme["button_bg"], lightcolor=theme["button_bg"],
            )
            style.configure(
                "Horizontal.TScrollbar", background=theme["button_bg"], troughcolor=theme["bg"],
                bordercolor=theme["border"], arrowcolor=theme["fg"],
                darkcolor=theme["button_bg"], lightcolor=theme["button_bg"],
            )
            style.configure(
                "TCombobox", fieldbackground=theme["entry_bg"], background=theme["button_bg"],
                foreground=theme["fg"], arrowcolor=theme["fg"],
            )
            style.map(
                "TCombobox",
                fieldbackground=[("readonly", theme["entry_bg"])],
                foreground=[("readonly", theme["fg"])],
                selectbackground=[("readonly", theme["entry_bg"])],
                selectforeground=[("readonly", theme["fg"])],
            )
            style.configure("TNotebook", background=theme["bg"], bordercolor=theme["border"])
            style.configure("TNotebook.Tab", background=theme["button_bg"], foreground=theme["fg"])
            style.map("TNotebook.Tab", background=[("selected", theme["panel_bg"])])
            # Реальний баг (аудит коду, 2026-08-15): головна таблиця даних
            # (self.tree) - ttk.Treeview, той самий style-based рушій, що й
            # Scrollbar/Combobox вище - без цього блоку лишалась системною
            # світлою навіть у темному режимі, хоча це основний екран програми.
            style.configure(
                "Treeview", background=theme["entry_bg"], fieldbackground=theme["entry_bg"],
                foreground=theme["fg"], bordercolor=theme["border"], rowheight=22,
            )
            style.map(
                "Treeview",
                background=[("selected", theme["select_bg"])],
                foreground=[("selected", theme["fg"])],
            )
            style.configure(
                "Treeview.Heading", background=theme["button_bg"], foreground=theme["fg"],
                bordercolor=theme["border"], relief="flat",
            )
            style.map("Treeview.Heading", background=[("active", theme["select_bg"])])
        else:
            style.theme_use(self._ORIGINAL_TTK_THEME)

    def _apply_theme_to_widget(self, widget, theme):
        # Реальний баг, знайдений тестом: tk.Toplevel/tk.Tk - ОКРЕМІ класи
        # (Wm-мішанина), НЕ підклас tk.Frame - без цього фон САМОГО вікна
        # (не вмісту всередині) лишався системним сірим, навіть коли всі
        # дочірні віджети вже правильно перефарбовувались рекурсією нижче.
        if isinstance(widget, (tk.Frame, tk.LabelFrame, tk.Toplevel, tk.Tk)):
            # Задача користувача: "тонший рядок" (Персонал) - картка-рядок
            # навмисно отримує ТРОХИ інший фон (panel_bg), не той самий, що
            # й сторінка (bg) - без цієї перевірки наступний-таки виклик
            # _apply_theme() одразу стирав би цю різницю назад до плоского
            # bg (обидва Frame, без жодного маркера "я особливий" крім
            # самого поточного кольору).
            current_bg = str(widget.cget("bg") or "").lower()
            if current_bg in (self._LIGHT_THEME["panel_bg"].lower(), self._DARK_THEME["panel_bg"].lower()):
                widget.configure(bg=theme["panel_bg"])
            else:
                widget.configure(bg=theme["bg"])
            return
        if isinstance(widget, tk.Canvas):
            widget.configure(bg=theme["bg"], highlightthickness=0)
            return
        if isinstance(widget, (tk.Entry, tk.Text)):
            widget.configure(
                bg=theme["entry_bg"], fg=theme["fg"],
                insertbackground=theme["fg"], selectbackground=theme["select_bg"],
            )
            return
        if isinstance(widget, tk.Listbox):
            widget.configure(bg=theme["entry_bg"], fg=theme["fg"], selectbackground=theme["select_bg"])
            return
        if isinstance(widget, tk.Spinbox):
            widget.configure(
                bg=theme["entry_bg"], fg=theme["fg"], buttonbackground=theme["button_bg"],
                insertbackground=theme["fg"], highlightthickness=0,
            )
            return
        if isinstance(widget, (tk.Label, tk.Button, tk.Radiobutton, tk.Checkbutton)):
            # Реальний баг (аудит коду, 2026-08-15): "Формат кнопок" -
            # користувацький колір фону/тексту чіп-кнопок (display_settings,
            # окреме від теми налаштування) - без цієї перевірки кожен виклик
            # _apply_theme() (СТАРТ програми і КОЖЕН тоггл теми) тихо стирав
            # би його назад до звичайного theme["button_bg"]/theme["fg"],
            # бо ці кольори не входять до жодного з "смислових" наборів
            # нижче. Непомітно у світлій темі лише тому, що дефолтний
            # button_bg_color збігається з _LIGHT_THEME["button_bg"].
            # Реальна скарга (2026-08-18, "щоб не скидався темний колір"):
            # button_bg_color/button_text_color за замовчуванням дорівнюють
            # РІВНО _LIGHT_THEME["button_bg"]/["fg"] (settings.py) - якщо
            # адмін НІКОЛИ не міняв "Формат кнопок", ця перевірка раніше
            # трактувала звичайнісінький світлий дефолт як "навмисний
            # кастомний колір" і назавжди "заморожувала" кнопки світлими
            # навіть у темній темі. Тепер кастомним вважається лише колір,
            # який РЕАЛЬНО відрізняється від світлого дефолту - лише тоді
            # адмін дійсно щось обирав у "Формат кнопок".
            custom_button_bg = str(self.display_settings.get("button_bg_color") or "").lower()
            custom_button_fg = str(self.display_settings.get("button_text_color") or "").lower()
            if custom_button_bg == self._LIGHT_THEME["button_bg"].lower():
                custom_button_bg = ""
            if custom_button_fg == self._LIGHT_THEME["fg"].lower():
                custom_button_fg = ""
            current_fg = str(widget.cget("fg") or "")
            is_custom_fg = (
                isinstance(widget, tk.Button) and bool(custom_button_fg)
                and current_fg.lower() == custom_button_fg
            )
            if current_fg.lower() in self._MUTED_FG_COLORS:
                fg = theme["muted_fg"]
            elif current_fg.lower() in {c.lower() for c in self._SEMANTIC_FG_COLORS}:
                fg = current_fg
            elif is_custom_fg:
                fg = current_fg
            else:
                fg = theme["fg"]
            current_bg = str(widget.cget("bg") or "")
            has_semantic_bg = current_bg.lower() in self._SEMANTIC_BG_COLORS or current_bg in (
                "#2F7BD9", "#255FA8",
            )
            has_custom_bg = (
                isinstance(widget, tk.Button) and bool(custom_button_bg)
                and current_bg.lower() == custom_button_bg
            )
            # check_update_button ("⟳") - навмисно tk.Label, не tk.Button
            # (див. коментар у _build_main_menu, 2026-08-15) - але візуально
            # має й далі виглядати як кнопка (button_bg тла), тому бере
            # участь у тій самій гілці розфарбування за ідентичністю.
            is_button_like = isinstance(widget, tk.Button) or widget is getattr(self, "check_update_button", None)
            if is_button_like:
                # Реальна скарга (2026-08-15, "чому криве?"): tk.Button на
                # Windows за замовчуванням малює highlightthickness-рамку
                # СИСТЕМНИМ (світлим) кольором незалежно від bg/fg - на
                # темному тлі це виглядало як світлий "надкус" у кутку
                # кнопки. highlightbackground/highlightcolor теж мають
                # збігатись із фактичним фоном кнопки, інакше рамка й далі
                # світла навіть при highlightthickness=1. tk.Label не має
                # highlightthickness/highlightbackground взагалі - configure
                # нижче просто ігнорує ці ключі для нього без помилки.
                button_bg = current_bg if (has_semantic_bg or has_custom_bg) else theme["button_bg"]
                if isinstance(widget, tk.Button):
                    widget.configure(highlightthickness=0, highlightbackground=button_bg, highlightcolor=button_bg)
                # Кнопки з навмисним смисловим чи користувацьким фоном (синя
                # "Оновлення" тощо, або "Формат кнопок") мають фон ПОЗА і
                # button_bg, і "звичайним" системним сірим - той самий
                # SEMANTIC-принцип, що й для fg вище.
                if has_semantic_bg or has_custom_bg:
                    widget.configure(fg=fg)
                else:
                    widget.configure(bg=theme["button_bg"], fg=fg, activebackground=theme["select_bg"])
            elif has_semantic_bg:
                # Задача користувача: "роль-чіп" (Персонал) - кольорова
                # плашка ролі має власний смисловий фон (той самий принцип,
                # що вже й у кнопок вище) - лишається незмінною, теми не
                # чіпають лише fg (той теж смисловий, з _SEMANTIC_FG_COLORS).
                widget.configure(fg=fg)
            else:
                widget.configure(bg=theme["bg"], fg=fg)
            return

    # Canvas-повзунок (не tk.Button/ttk-стиль - Windows-рушій "vista"/
    # "winnative" у світлому режимі однаково ігнорує спроби намалювати
    # справжній тумблер через ttk.Style). Праворуч (dark_mode=True) -
    # акцентний колір доріжки + білий повзунок; ліворуч (світла тема) -
    # нейтральна доріжка theme["border"]. Викликається (1) одразу після
    # побудови в _build_main_menu, (2) з кожного _on_theme_toggle,
    # (3) з КОЖНОГО повного проходу _apply_theme() (widget=None) - інакше
    # колір доріжки застряг би на кольорах теми, що діяла в момент
    # побудови кнопки.
    _THEME_TOGGLE_ON_COLOR = "#0969DA"

    def _draw_theme_toggle_switch(self):
        canvas = getattr(self, "theme_toggle_switch", None)
        if canvas is None:
            return
        theme = self._theme()
        canvas.configure(bg=theme["bg"])
        canvas.delete("all")
        track_color = self._THEME_TOGGLE_ON_COLOR if self._dark_mode else theme["border"]
        canvas.create_oval(0, 0, 22, 22, fill=track_color, outline=track_color)
        canvas.create_oval(22, 0, 44, 22, fill=track_color, outline=track_color)
        canvas.create_rectangle(11, 0, 33, 22, fill=track_color, outline=track_color)
        thumb_x = 33 if self._dark_mode else 11
        canvas.create_oval(thumb_x - 9, 2, thumb_x + 9, 20, fill="#FFFFFF", outline="#FFFFFF")

    def _on_theme_toggle(self):
        self._dark_mode = not self._dark_mode
        self.display_settings.set("dark_mode", self._dark_mode)
        self.root.configure(bg=self._theme()["bg"])
        self._apply_theme()

    # Задача користувача: "скрізь зроби їх видимими кнопками, бо зараз не
    # видно що це те, на що можна натиснути" — стандартний tk.Button з
    # relief="flat" і без фону зливається з вікном. Один спільний "чіп"-
    # стиль (видимий тонкий контур + фон), застосований до КОЖНОЇ
    # "ред"/"x"/"+"-кнопки в програмі, не лише в попапах "Дії".
    #
    # Наступна задача користувача: "зроби один формат відображення кнопок,
    # і дай змогу вибрати йому колір і текст в налаштуваннях" — фон і колір
    # тексту більше НЕ жорстко вшиті, а читаються з display_settings
    # (той самий персональний, "суто вигляд" файл, що й формат дати) —
    # тому метод, а не клас-атрибут: кожен виклик бере АКТУАЛЬНЕ значення,
    # без потреби перезапускати програму після зміни в "Формат кнопок".
    # Фон PNG-картинкою — свідомо НЕ зараз ("пізніше додасиш" — власні
    # слова користувача про наступний, окремий крок).
    def _chip_button_style(self):
        bg = self.display_settings.get("button_bg_color")
        return dict(
            relief="solid", bd=1, highlightthickness=0, bg=bg,
            activebackground=self._darken_hex_color(bg), cursor="hand2",
        )

    # Окремо від фону/рамки — колір ТЕКСТУ, теж з display_settings.
    # Свідомо НЕ входить у _chip_button_style(): кнопки "+"/"x" мають власний
    # смисловий колір (зелений/червоний, додати/видалити) — цей колір не має
    # переписуватись загальним налаштуванням тексту. Застосовується лише до
    # нейтральних кнопок без власного смислового кольору (напр. "ред",
    # клікабельний рядок навігації).
    def _chip_text_color(self):
        return self.display_settings.get("button_text_color")

    # Локалізація GUI. Задача користувача: "перейменуй тепер всю програму
    # російською мовою із подальшою можливістю додати потім і англійську
    # мову і Українську" — цей метод обгортає КОЖЕН статичний текст у
    # програмі; сам переклад лежить у i18n.py (TRANSLATIONS), тут лише
    # активна мова (display_settings, за тим самим принципом, що й
    # button_bg_color/date_format — суто вигляд, не критично для бота).
    def _t(self, text):
        return translate(self.display_settings.get("language") or DEFAULT_LANGUAGE, text)

    # Проста, без зовнішніх залежностей, темна тінь фону для стану "наведено
    # мишею" — вираховується з ОБРАНОГО кольору (не фіксована), інакше
    # довільний темний фон користувача виглядав би зламаним на наведенні.
    def _darken_hex_color(self, hex_color, factor=0.9):
        hex_color = (hex_color or "#f6f8fa").lstrip("#")
        if len(hex_color) != 6:
            return "#eaeef2"
        try:
            r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return "#eaeef2"
        r, g, b = (max(0, int(channel * factor)) for channel in (r, g, b))
        return f"#{r:02x}{g:02x}{b:02x}"

    # 3 "+"-кнопки НЕ входять до жодного _refresh_* — рядки списків так
    # оновлюються одразу після збереження "Формат кнопок", а ці статичні
    # кнопки без цього виклику лишились би зі старим фоном до перезапуску.
    # Усі три будуються ОДИН РАЗ при старті програми (_build_commands_view/
    # _build_payment_methods_view/_build_custom_buttons_view).
    def _restyle_static_chip_buttons(self):
        style = self._chip_button_style()
        for button in (
            self.add_root_button,
            self.add_command_button,
            self.add_payment_method_button,
            self.save_standard_menu_cloud_button,
        ):
            if button is not None:
                button.configure(**style)

    def _handle_layout_independent_clipboard_shortcut(self, event):
        action = self._CLIPBOARD_SHORTCUT_KEYCODES.get(event.keycode)
        if action:
            event.widget.event_generate(action)
            return "break"
        return None

    # --- Ініціалізація вікна, побудова всіх екранів, головне меню/навігація ---
    def __init__(self, root, file_path, on_ready=None):
        # Задача користувача (2026-08-15): "не справжнє, а бутафорію... хай
        # завантаження відбувається, а вікно хай буде відокремлене... щоб
        # не лагало і плавно ходило" - раніше splash "пумпався" вручну з
        # контрольних точок усередині цього ж __init__ (щоб хоч якось
        # перемальовуватись, поки Excel-імпорт блокує головний потік) - і
        # все одно смикався: між точками пумпу нічого не малювалось.
        # Тепер справжнє рішення - Excel-імпорт (реально повільна частина,
        # пропорційна кількості рядків) переїжджає у ФОНОВИЙ потік
        # (_start_background_data_load нижче), а головний потік (і його Tk-
        # event loop) лишається вільним увесь час - тому indeterminate
        # ttk.Progressbar у main.py тепер анімується САМА, вбудованим
        # Tk-таймером, без жодного ручного втручання звідси. on_ready() -
        # виконується ПІСЛЯ повного завершення (і фонового завантаження,
        # і побудови всіх екранів) - main.py гасить splash саме тоді.
        self._on_ready = on_ready
        self.root = root
        self.file_path = Path(file_path)
        self.db_path = DB_PATH
        self.settings = SettingsStore(SETTINGS_PATH)
        # Задача користувача (2026-08-19): "щоб я міг перемикатись між
        # цими серверами" - відновлює ОСТАННІЙ обраний сервер одразу на
        # старті, до того, як хтось встигне відкрити Персонал/Редактор
        # кнопок і випадково піти на дефолтний (основний) сервер замість
        # того, що реально обирали минулого разу.
        last_active_hostname = self.settings.get("active_remote_server_hostname")
        if last_active_hostname:
            remote_control_client.set_active_server(last_active_hostname)
        # display_settings мусить існувати ДО перших messagebox.showwarning
        # нижче - self._t() читає self.display_settings.get("language").
        self.display_settings = DisplaySettingsStore(DISPLAY_SETTINGS_PATH)
        # Задача користувача (2026-08-15): "темна тема... щоб вибір
        # зберігався" - завантажується РАНО (до першого _build_*), щоб
        # усе будувалось одразу з правильним кольором фону self.root, а не
        # блимало світлим на мить перед _apply_theme() наприкінці __init__.
        self._dark_mode = bool(self.display_settings.get("dark_mode"))
        self.root.configure(bg=self._theme()["bg"])
        # Важлива знахідка нового аудиту (28.07.2026, #9): показ ОДИН раз
        # одразу після створення обох сховищ - той самий messagebox.
        # showwarning-шаблон, що вже є для збою автознімка БД нижче.
        if self.settings.load_error:
            messagebox.showwarning(
                self._t("Налаштування"),
                self._t("Файл настроек был повреждён и сброшен к значениям по умолчанию: {value}").format(
                    value=self.settings.load_error
                ),
            )
        if self.display_settings.load_error:
            messagebox.showwarning(
                self._t("Налаштування"),
                self._t("Файл персональных настроек вида был повреждён и сброшен к значениям по умолчанию: {value}").format(
                    value=self.display_settings.load_error
                ),
            )
        # Задача користувача: "зроби так щоб працював лише вибраний ексель
        # один... вибрав і вибір зай в джсоні десь запам'ятовується... і
        # більше ніяких інших файлів не читає, ні внутрепроєктних" —
        # excel_local_path більше не має мовчазного запасного варіанту на
        # paths.FILE_PATH (excel_source.py тепер вимагає явний вибір).
        # Одноразова міграція: якщо файл ще не обрано явно, записуємо ПОТОЧНИЙ
        # файл проєкту як явний вибір — щоб уже працююче встановлення не
        # зламалось, а обраний файл відтоді був явним записом у JSON.
        if self.settings.get("excel_source_mode") == "local" and not self.settings.get("excel_local_path"):
            self.settings.set("excel_local_path", str(FILE_PATH))
        self.root.title(self._t("AI Automation"))
        self.root.geometry("1000x600")
        for widget_class in ("Entry", "Text", "TEntry", "TCombobox"):
            self.root.bind_class(widget_class, "<Control-KeyPress>", self._handle_layout_independent_clipboard_shortcut)

        self.telegram_worker = None
        self.telegram_status_text = tk.StringVar(value=self._t("Telegram не подключен"))
        self.telegram_heartbeat_text = tk.StringVar(value="")
        # Watchdog-пункт 2 ("сильні місця"): та сама пасивна ідея, що й
        # telegram_heartbeat_text вище - коли справді востаннє спрацював
        # автоматичний DB-снапшот, видно одразу в Налаштуваннях, без
        # переходу на окремий екран "Резервні копії".
        self.db_snapshot_heartbeat_text = tk.StringVar(value="")
        # Задача користувача (2026-08-14, скріншот): "щоб писало яка зараз
        # використовується наразі таблиця" - реальний привід: користувач
        # підключив новий Excel-файл через цей самий діалог, але програма
        # довго показувала старі дані, бо перечитування Excel відбувається
        # лише при СТАРТІ програми — саме джерело помилки (обраний не той
        # файл / реімпорт ще не стався) неможливо було побачити, не
        # відкриваючи сам діалог. excel_source.current_source_label() —
        # та сама функція, що вже показує це усередині діалогу "Таблиця
        # Excel", тепер видно одразу в бічній панелі, без відкриття діалогу.
        self.excel_source_status_text = tk.StringVar(value=excel_source.current_source_label())
        self.telegram_file_text = tk.StringVar(value="")
        self.is_closing = False
        # Нагляд за ботом (watchdog): _telegram_should_run — людина ХОЧЕ, щоб
        # бот працював (True після старту, False після явного "Зупинити") —
        # відрізняє "сам зупинив" від "несподівано впав/завис", щоб watchdog
        # не заважав явній зупинці. _telegram_reconnect_attempts/_telegram_
        # next_attempt_at — наростаючий backoff між спробами перепідключення.
        self._telegram_should_run = False
        self._telegram_reconnect_attempts = 0
        self._telegram_next_attempt_at = 0.0
        # Аудит коду: форсований стоп завислого воркера тепер у фоновому
        # потоці (не блокує вікно) — цей прапорець не дає запустити другий
        # такий потік, поки перший ще не завершив worker.stop().
        self._telegram_stop_in_progress = False
        # Свіжий пере-аудит (New-Minor #5): подвійний клік "Увійти через
        # Microsoft" міг би запустити другий одночасний device-flow, поки
        # перший ще триває (весь флоу — 2 послідовних фонових потоки).
        self._onedrive_sign_in_in_progress = False

        # Форма введення даних (Telegram Mini App) — локальний сервер +
        # Cloudflare Quick Tunnel, повністю автоматично, без акаунту/домену.
        # Тунель НЕ прив'язаний до конкретного TelegramBotWorker-екземпляра
        # (на відміну від бота, він не перезапускається на кожен reconnect/
        # watchdog-цикл — лише при явній зупинці "Зупинити Telegram", щоб не
        # "мигтіти" адресою форми під час звичайних перепідключень бота).
        self.webapp_server = WebappServer(
            db_path=self.db_path,
            get_token=lambda: self._read_telegram_token()[0],
            get_fresh_context=lambda store, is_admin: (
                self.telegram_worker._webapp_data_browser_context(store, is_admin)
                if self.telegram_worker else None
            ),
        )
        self.cloudflared_process = None
        self.webapp_public_url = ""
        self._webapp_tunnel_starting = False
        # Задача користувача (скріншот "ERR_NAME_NOT_RESOLVED"): _check_
        # webapp_tunnel_health вище перевіряє лише що ЛОКАЛЬНИЙ процес
        # cloudflared/webapp_server живий - Cloudflare Quick Tunnel може
        # "тихо" відвалитись на своєму боці, лишаючи локальний процес живим,
        # але публічну адресу - недоступною. Окремий, справжній HTTP-пробник
        # (нижче, _webapp_health_watchdog_tick) - НЕ на тому самому тіку, що
        # й бот (щоб не чіпати частоту бот-watchdog'а).
        self._webapp_health_check_active = False
        # Задача користувача (2026-08-12): "оновлення, кнопку" - результат
        # останньої перевірки update_check.check_for_update (None, якщо
        # оновлення немає) - читає _on_update_button_clicked нижче.
        self._pending_update_entry = None
        # Задача користувача (2026-08-15): "роби через оновлення" - домашня
        # программа (AI_Automation_Home.exe, тепер реально зібраний .exe,
        # не лише dev-скрипт) отримує ТОЙ САМИЙ двофазний download->install
        # флоу, що вже давно є в client_app.py - раніше кнопка "Оновлення"
        # тут лише ПУБЛІКУВАЛА власну поточну версію (для гіпотетичних
        # ІНШИХ інсталяцій gui.py, яких у цьому розгортанні просто немає) -
        # тепер натомість реально завантажує й встановлює.
        self._update_download_in_progress = False
        self._update_check_in_progress = False
        self._update_ready_to_install = False
        self._downloaded_update_target = None
        self._update_install_in_progress = False
        # Реальна знахідка (аудит коду, 2026-08-16): guard-прапорці для
        # фонового переносу двох синхронних мережевих викликів нижче
        # (_on_remote_command_clicked/_on_role_menu_selected) - без них
        # звільнене від "фрiзу" вікно давало б реально клікнути кнопку
        # вдруге, поки перший запит ще в польоті.
        self._remote_command_in_progress = False
        self._remote_role_change_in_progress = False
        self._standard_menu_cloud_save_in_progress = False
        # Задача користувача (2026-08-12): після виходу з тривалого збою
        # (10+ невдалих спроб поспіль) наступна перевірка йде рідше (раз в
        # 30 хв, не раз в 30с) - "довіра" до щойно відновленого з'єднання
        # ще не заслужена одразу. _webapp_health_watchdog_tick читає це
        # значення на кожному тіку (не жорстко self._WEBAPP_HEALTH_CHECK_
        # INTERVAL_MS), _webapp_health_check_worker міняє його лише після
        # успішного відновлення з розширеного простою.
        self._webapp_check_interval_ms = self._WEBAPP_HEALTH_CHECK_INTERVAL_MS
        self._webapp_last_probe_error = None
        self._webapp_not_before = None
        self._webapp_url_assigned_at = None
        # Задача користувача: явні кнопки "Увімкнути"/"Вимкнути"/"Перезапустити"
        # форми (Mini App) у Налаштуваннях — незалежно від того, підключений
        # зараз сам бот чи ні. _webapp_should_run — той самий watchdog-принцип,
        # що й _telegram_should_run: без нього ручне "Вимкнути" тут само
        # скасувалось би вже на наступному watchdog-тіку (_check_webapp_
        # tunnel_health бачить тунель мертвим і сам його піднімає знову).
        #
        # Реальний баг (2026-08-13): "спершу вмикається форма, а потім бот" -
        # True тут ЗАВЖДИ, з моменту запуску програми, ще до старту бота -
        # watchdog бачив це й сам піднімав тунель через ВЛАСНИЙ, незалежний
        # probe-цикл (_webapp_health_check_worker), що не перевіряв _webapp_
        # not_before. False за замовчуванням - _start_telegram_from_settings
        # виставляє True лише коли бот реально стартував (силою чи вручну).
        self._webapp_should_run = False
        self.webapp_status_text = tk.StringVar(value="")
        self._refresh_webapp_status_text()

        self.store = ExcelSqliteStore(self.db_path)
        # Решта __init__ (Excel-імпорт + побудова екранів) продовжується в
        # _finish_startup, ПІСЛЯ фонового завантаження - див. коментар над
        # _start_background_data_load нижче.
        self._start_background_data_load()

    # Задача користувача (2026-08-15): "не справжнє, а бутафорію... хай
    # завантаження відбувається, а вікно хай буде відокремлене... щоб не
    # лагало і плавно ходило" - реально повільна частина (Excel-імпорт,
    # пропорційний кількості рядків, і знімок БД) переїжджає у фоновий
    # потік, щоб головний Tk-потік НІКОЛИ не блокувався - indeterminate
    # ttk.Progressbar у main.py тоді анімується сама, вбудованим Tk-
    # таймером, без жодних ручних "пумпів" звідси.
    #
    # SQLite-застереження (реальний баг, вже знайдений і виправлений раніше
    # цієї сесії в _on_refresh_excel_clicked, client_app.py): з'єднання,
    # створене на ОДНОМУ потоці, не можна використовувати з ІНШОГО. self.
    # store.conn належить головному потоку (створений вище, синхронно) -
    # фоновий потік працює з ВЛАСНИМ, окремим з'єднанням (thread_store),
    # що пише в той самий файл на диску; self.store бачить свіжі дані
    # просто тому, що це один SQLite-файл, без потреби ділити сам об'єкт
    # з'єднання між потоками.
    def _start_background_data_load(self):
        def worker():
            excel_error = None
            try:
                thread_store = ExcelSqliteStore(self.db_path)
                try:
                    self._load_excel_into_store(thread_store)
                finally:
                    thread_store.close()
            except Exception as exc:
                # Реальний баг (аудит коду, 2026-08-15): лише RuntimeError
                # ловився тут раніше, але openpyxl.load_workbook (усередині
                # _load_excel_into_store -> excel_source.open_workbook) кидає
                # BadZipFile/InvalidFileException на пошкоджений файл і
                # PermissionError, якщо Excel тримає його відкритим - жоден з
                # них не RuntimeError. Без широкого except виняток вилітав би
                # з воркера ДО self.root.after(0, ..._finish_startup...) -
                # UI ніколи не будувався б, застосунок висів би назавжди на
                # безрамковому splash-вікні без жодної помилки на екрані.
                excel_error = exc
            snapshot_error = None
            try:
                maybe_create_scheduled_snapshot(self.db_path)
            except Exception as exc:
                snapshot_error = exc
            self.root.after(0, lambda: self._finish_startup(excel_error, snapshot_error))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_startup(self, excel_error, snapshot_error):
        # Задача користувача: "більше ніяких інших файлів не читає" —
        # excel_source.open_workbook() тепер кидає RuntimeError, якщо
        # джерело (онлайн без підключення, чи локальний файл, якого раптом
        # не існує) не готове до читання. Якщо просто дати цьому винятку
        # впасти з __init__, застосунок узагалі не запуститься — і
        # користувач не зможе дістатись до "Таблиця Excel", щоб це
        # виправити. Тому лише ТУТ (старт застосунку) ловимо помилку й
        # показуємо її, замість падіння — решта викликів excel_source
        # (ручне "Оновити"/збереження) свідомо НЕ ловлять цей виняток, бо
        # там користувач і так бачить результат дії напряму.
        if excel_error is not None:
            messagebox.showerror(self._t("Таблиця Excel"), self._t(str(excel_error)))
        if snapshot_error is not None:
            messagebox.showwarning(
                self._t("Резервные копии"),
                self._t("Не удалось создать автоматический снимок базы данных: {error}").format(error=snapshot_error),
            )
        self._update_db_snapshot_heartbeat()
        self.root.after(1800000, self._schedule_db_backup_tick)
        self.root.after(1800000, self._schedule_code_backup_tick)
        self.root.after(2000, self._poll_for_update)
        # Задача користувача (2026-08-15): "тоді стару програму потрібно
        # відімкнути від увімкнення форми та телеграм-чату... вимкни
        # обов'язково. це дуже важливо щоб не було у нас з цим проблем" -
        # ця програма (gui.py, "стара") більше НЕ запускає власного бота
        # чи webapp-тунель НІКОЛИ, ні на старті, ні саму по собі (лише
        # ВІДДАЛЕНІ команди до реального сервера - _remote_control.py).
        # Свідомо прибрано:
        #   - self.root.after(10000, self._telegram_watchdog_tick) - без
        #     цього планування ватчдог із backoff-перепідключенням НІКОЛИ
        #     не тікає, тож не намагається сам підняти бота після збою.
        #   - self.root.after(5000, self._webapp_health_watchdog_tick) -
        #     та сама причина для локального webapp/cloudflared-тунелю.
        #   - self._start_telegram_from_settings(silent=True) (нижче за
        #     show_main_menu) - той самий рядок, що РЕАЛЬНО запускав
        #     локальний TelegramBotWorker при кожному старті програми.
        # Увесь цей код НЕ видалений (лишається доступним функціям нижче,
        # про всяк випадок), лише НІЧИМ більше не викликається з UI/тіка -
        # два незалежні боти з ОДНИМ токеном одночасно (якщо забути це
        # вимкнути) - реальна, а не гіпотетична проблема (конфліктне
        # опитування getUpdates, подвійна обробка повідомлень).
        self.current_sheet = None
        self.current_headers = []
        self.current_page = 0
        self.total_rows = 0
        # Задача користувача (2026-08-14): "всі ці колонки мають бути з
        # фільтрами в таблиці" - активний текстовий фільтр по кожному
        # стовпцю (клік на заголовок відкриває маленьке поле вводу),
        # персистентно в settings.json ("table_column_filters", той самий
        # принцип, що й table_column_widths вище) - за стандартним правилом
        # цього застосунку жоден фільтр не має губитись при перезапуску.
        self.column_filters = {}
        self.filter_popup_window = None
        # Реальна знахідка (аудит коду, 2026-08-16): (лист, підпис фільтрів,
        # вже відфільтровані рядки) - кеш для next_page/previous_page нижче,
        # щоб перегорт сторінки під активним фільтром не тягнув і не
        # фільтрував ВЕСЬ лист заново на кожен клік. Заповнюється лише
        # ПОВНИМ _refresh_page() (збереження/оновлення/зміна фільтра/листа
        # завжди йдуть через нього), тому застарілим не буває.
        self._filtered_page_cache = None
        self.edit_mode = False
        self.has_unsaved_changes = False
        # Журнали (Задача користувача: хоче лишати журнал відкритим окремим
        # вікном і одночасно працювати в решті застосунку) відкриваються як
        # tk.Toplevel, побудований ЛІНИВО при першому відкритті (не тут) —
        # persists між відкриттями, поки користувач сам його не закриє.
        self.journals_window = None
        self._action_log_refresh_generation = 0
        self._personnel_refresh_generation = 0
        self._payment_methods_refresh_generation = 0
        # Задача користувача (2026-08-17): "вирівняй це... ролі не мають
        # їздити... додай сортування за часом та за алфавітом. фільтри
        # мають бути як в данних формі" - кешуємо СИРИЙ (нефільтрований,
        # несортований) список з останнього мережевого запиту, щоб клік по
        # заголовку сортування/фільтра просто перемальовував з кешу, а не
        # тягнув дані через тунель заново. None = нема ще жодних даних.
        self._personnel_users_cache = None
        self._personnel_sort_field = None
        self._personnel_sort_reverse = False
        self._personnel_role_filter = None
        # "Персонал" (Задача користувача: "винеси кнопку керування персоналом
        # до головного меню... має відкриватись окреме вікно") — той самий
        # ліниво-побудований tk.Toplevel патерн, що й журнали, замість
        # колишнього перемикання в межах головного вікна (_show_only).
        self.personnel_window = None
        # Аудит коду: редактор синонімів команди (open_command_alias_editor)
        # відкривався заново щоразу, без жодного трекінгу — на відміну від
        # journals_window/personnel_window вище. Тут словник (не одна
        # змінна), бо редактор відкривається ПО КОНКРЕТНІЙ команді.
        self._command_alias_editor_windows = {}
        # Свіжий пере-аудит (2026-08-02, Minor #5): той самий клас багу, що
        # й command_alias_editor вище (task #244), поширений і на вікна
        # деталей журналів - "Детально" двічі на той самий запис відкривало
        # ДРУГЕ незалежне вікно. Той самий dict-keyed патерн, за log_id.
        self._work_log_detail_windows = {}
        self._action_log_detail_windows = {}
        # Задача користувача (2026-08-15): "синхронізація" - Персонал/
        # Журнали дій тепер тягнуть РЕАЛЬНІ дані client_app.py через тунель
        # (read-only, remote_control_client.fetch_remote_action_log/
        # fetch_remote_personnel), а не власну порожню локальну базу.
        # Кеш останнього fetch за log_id - "Детально" використовує вже
        # отримані рядки замість повторного округлого запиту через тунель.
        self._remote_action_log_rows = {}

        self._build_main_menu()
        self._build_layout()
        self._build_sheet_buttons()
        self._build_settings_view()
        self._build_commands_view()
        self._build_custom_buttons_view()
        self._build_payment_methods_view()

        sheet_names = self.store.sheet_names()
        if sheet_names:
            self.show_sheet(sheet_names[0])

        self._update_telegram_settings_labels()
        # self._start_telegram_from_settings(silent=True) - свідомо
        # прибрано, див. коментар вище (2026-08-15, "вимкни обов'язково").
        self._start_remote_control_polling()
        self.show_main_menu()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Escape>", self._on_escape)
        # Задача користувача: темна тема застосовується ОСТАННІМ кроком -
        # охоплює геть усе, що вже побудоване вище (усі _build_*_view/
        # _build_main_menu), одним рекурсивним проходом.
        self._apply_theme()
        if self._on_ready:
            self._on_ready()

    def _load_excel_into_store(self, store=None):
        # Задача користувача: "автоматична перевірка при приєднанні нової
        # таблиці чи є ця вкладка, якщо нема - програма має створити сама" -
        # ОКРЕМИЙ прохід (не data_only=True, як читання для імпорту нижче -
        # інакше збереження стерло б формули на інших листах, коментар у
        # ensure_workbook_has_required_sheets) ПЕРЕД імпортом, щоб
        # СПИСАНИЕ вже існувало на момент import_workbook нижче.
        ensure_workbook_has_required_sheets()
        workbook = excel_source.open_workbook(data_only=True)
        try:
            (store or self.store).import_workbook(workbook, READ_ONLY_SHEETS)
        finally:
            workbook.close()

    def _build_main_menu(self):
        self.main_menu_frame = tk.Frame(self.root)

        # Задача користувача: "показує в головному меню зверху справа...
        # синю кнопку оновлення" - .place() (не pack), навмисно: єдиний
        # спосіб покласти елемент у верхній правий кут ПОВЕРХ уже готового
        # центрованого menu_panel нижче, не чіпаючи його існуючий pack-
        # layout. Прихована (place_forget) до реальної знахідки оновлення.
        self.update_button = tk.Button(
            self.main_menu_frame,
            text=self._t("Оновлення"),
            bg="#2F7BD9", fg="white", activebackground="#255FA8", activeforeground="white",
            relief="flat", padx=10, pady=4,
            command=self._on_update_button_clicked,
        )

        # Задача користувача: "кнопка оновити, яка показує чи є готове
        # оновлення, якщо перший сигнал якось пропустився" - завжди видима
        # кнопка-значок ручної перевірки ЗАРАЗ (варіант 3 з обраних макетів
        # - лише іконка, без тексту; символ "⟳" замість PNG - у gui.py немає
        # системи іконок, на відміну від client_app.py). Розміщена нижче
        # update_button (y=52 проти y=12, той самий висота 32px, що й у
        # update_button) - навіть коли update_button показаний, вони ніколи
        # не накладаються одна на одну.
        # Реальна, повторювана скарга (2026-08-15, "чому криве?"/"це правий
        # нижній кут"): попри highlightthickness=0 (нижче, _apply_theme_
        # to_widget), користувач і далі бачив округлу світлу пляму саме в
        # куті САМЕ цього tk.Button - навіть у ІЗОЛЬОВАНОМУ тестовому вікні
        # (не в самій програмі) вона не відтворювалась, що вказує на
        # щось специфічне для РЕАЛЬНОГО native Windows tk.Button chrome
        # (hover/focus-glow через uxtheme.dll), а не на помилку в
        # bg/fg/highlight-параметрах. tk.Label замість tk.Button повністю
        # усуває будь-яке нативне промальовування кнопки Windows - лишається
        # голий прямокутник, який сам контролює кожен піксель.
        self.check_update_button = tk.Label(
            self.main_menu_frame,
            text="⟳",
            font=("Segoe UI", 12),
            cursor="hand2",
        )
        self.check_update_button.bind("<Button-1>", lambda _event: self._manual_check_for_update())
        self.check_update_button.place(relx=1.0, x=-16, y=52, anchor="ne", width=32, height=32)

        # Задача користувача (2026-08-15): "не має видвати спливаюче
        # вікно-повідомлення... просто тихесенько під кнопкою" - текст під
        # кнопкою-значком замість messagebox (_apply_update_poll_result).
        self.update_check_result_text = tk.StringVar()
        tk.Label(
            self.main_menu_frame,
            textvariable=self.update_check_result_text,
            font=("Segoe UI", 9), fg="gray40",
            wraplength=220, justify="right",
        ).place(relx=1.0, x=-16, y=88, anchor="ne")

        # Задача користувача: "додай на головне меню... датчик" статусу
        # віддаленого сервера (client_app.py) - дзеркально до update_button
        # вище (той самий y=12, лише лівий верхній кут замість правого).
        # Заміряно реально (winfo_x/y при 1000x600): центрований menu_panel
        # займає x=397..603, update_button - x=886..984 - лівий верхній кут
        # повністю порожній за будь-якого розміру вікна (menu_panel завжди
        # горизонтально центрований), тож тут ніколи не накладеться на
        # заголовок/кнопки/саму "Оновлення". Задача користувача (2026-08-15):
        # "перекидає в налаштування чомусь. прибери це" - клік у Настройки
        # прибрано (розділ "Дистанційне керування" туди вже не веде, звідти
        # прибрано разом із переходом на автоматичне з'єднання) - лише
        # пасивний індикатор, без переходу.
        self.main_menu_status_label = tk.Label(
            self.main_menu_frame,
            textvariable=self.telegram_status_text,
            font=("Segoe UI", 10),
            fg="gray40",
        )
        self.main_menu_status_label.place(relx=0.0, x=16, y=12, anchor="nw")

        # Задача користувача (2026-08-18): "кнопку зміни теми перенес
        # праворуч... і перероби на тумблер" - справжній повзунок-перемикач
        # (Canvas, не tk.Button). Задача користувача (наступного дня):
        # "тумблер теми змісти нижче на 150 пікселів" - тепер нижче "⟳"
        # (check_update_button, y=52), а не над ним.
        theme_toggle_row = tk.Frame(self.main_menu_frame)
        theme_toggle_row.place(relx=1.0, x=-16, y=162, anchor="ne")
        self.theme_toggle_label = tk.Label(
            theme_toggle_row, text=self._t("Тёмная тема"), font=("Segoe UI", 9),
        )
        self.theme_toggle_label.pack(side="left", padx=(0, 6))
        self.theme_toggle_switch = tk.Canvas(
            theme_toggle_row, width=44, height=22, highlightthickness=0, bd=0, cursor="hand2",
        )
        self.theme_toggle_switch.bind("<Button-1>", lambda _event: self._on_theme_toggle())
        self.theme_toggle_switch.pack(side="left")
        self._draw_theme_toggle_switch()

        menu_panel = tk.Frame(self.main_menu_frame)
        menu_panel.pack(expand=True)

        title = tk.Label(menu_panel, text=self._t("Головне меню"), font=("Segoe UI", 18, "bold"))
        title.pack(pady=(0, 24))

        settings_button = tk.Button(
            menu_panel,
            text=self._t("Налаштування"),
            width=28,
            height=2,
            command=self.show_settings,
        )
        settings_button.pack(pady=8)

        journals_button = tk.Button(
            menu_panel,
            text=self._t("Журнали"),
            width=28,
            height=2,
            command=self.show_journals,
        )
        journals_button.pack(pady=8)

        # Задача користувача: "винеси кнопку керування персоналом до
        # головного меню. при відкритті керув. персоналом - має відкриватись
        # окреме вікно" - той самий tk.Toplevel-патерн, що й "Журнали" вище.
        personnel_button = tk.Button(
            menu_panel,
            text=self._t("Персонал"),
            width=28,
            height=2,
            command=self.show_personnel,
        )
        personnel_button.pack(pady=8)

        custom_buttons_button = tk.Button(
            menu_panel,
            text=self._t("Редактор кнопок"),
            width=28,
            height=2,
            command=self.show_custom_buttons,
        )
        custom_buttons_button.pack(pady=8)

        sync_excel_button = tk.Button(
            menu_panel,
            text=self._t("Обновити Excel"),
            width=28,
            height=2,
            command=self.sync_excel_manually,
        )
        sync_excel_button.pack(pady=8)

        # Задача користувача (2026-08-19): "винеси кнопку публікації
        # оновлень на головний екран, замість зарезервованої кнопки" -
        # той самий "В разработке"-слот, що вже колись зайняла "Персонал"
        # (коментар вище), тепер займає ця кнопка - перенесена сюди з
        # side_panel (нижче), не продубльована.
        publish_updates_button = tk.Button(
            menu_panel,
            text=self._t("Публікація оновлень"),
            width=28,
            height=2,
            command=self.open_publish_updates_dialog,
        )
        publish_updates_button.pack(pady=8)

        # Задача користувача (2026-08-19): "потрібно бачити всі сервера що
        # доступні. всі тестові... і всі не тестові" - той самий "головне
        # меню, спливаюче вікно" підхід, що й "Публікація оновлень" вище.
        servers_button = tk.Button(
            menu_panel,
            text=self._t("Сервери"),
            width=28,
            height=2,
            command=self.open_servers_dialog,
        )
        servers_button.pack(pady=8)

        exit_button = tk.Button(
            menu_panel,
            text=self._t("Вихід"),
            width=28,
            height=2,
            command=self.on_close,
        )
        exit_button.pack(pady=8)

        # Задача користувача (2026-08-17): "додай знизу версію програми" -
        # той самий "ver. X" напис, що вже є в client_app.py (main_frame,
        # side="bottom") - тут той самий трюк, лише на main_menu_frame
        # (зовнішній, на всю висоту вікна), а не на menu_panel (внутрішній,
        # центрований по вертикалі) - інакше напис опинився б одразу під
        # "Вихід" замість справжнього нижнього краю вікна.
        version_label = tk.Label(
            self.main_menu_frame, text=f"ver. {__version__}",
            font=("Segoe UI", 8), fg="#8c959f",
        )
        version_label.pack(side="bottom", pady=(0, 8))

    # ---------- перевірка оновлень (раз в 5 хв) ----------
    # Реальний баг (аудит коду, 2026-08-14): update_manifest_path навмисно
    # розрахований і на мережеву папку (докстрінг update_check.py) - раніше
    # читання файлу відбувалось СИНХРОННО в головному потоці Tk, кожні 5
    # хвилин, весь час роботи програми. Недоступний мережевий шлях (кабель
    # відключили, VPN впав) блокує звичайне файлове читання Windows на
    # секунди-хвилини - усе вікно замерзало б на цей час, повторюючись
    # знову й знову. Той самий фоновий-потік патерн, що вже є в sign_in()/
    # connect_link() (OneDrive) - _run_on_main_thread сам перевіряє
    # is_closing і ковтає TclError, тож закриття вікна поки перевірка ще
    # триває у фоні безпечне.
    def _poll_for_update(self):
        self._check_for_update_now(manual=False)

    # Задача користувача: "додай кнопку оновити, яка показує чи є готове
    # оновлення, якщо перший сигнал якось пропустився" - ручна перевірка
    # ЗАРАЗ, той самий принцип, що й у client_app.py
    # (_manual_check_for_update). manual=True лише вирішує (нижче,
    # _apply_update_poll_result): показати "остання версія" повідомлення,
    # якщо нічого не знайдено, і НЕ перепланувати таймер (щоб кожен ручний
    # клік не плодив окремий паралельний 5-хвилинний ланцюжок поверх уже
    # наявного - лише один automatic-ланцюжок coздано ОДИН РАЗ, у __init__).
    def _manual_check_for_update(self):
        self._check_for_update_now(manual=True)

    # Задача користувача (2026-08-16): "стосовно домашньої версії, щоб вона
    # не заважала процесам" - перевірка/завантаження оновлень gui.py тепер
    # тим самим шляхом, що вже перевірений на client_app.py (GitHub
    # Releases, github_releases.GUI_TAG_PREFIX - окремий від client-v,
    # інакше /releases/latest сплутав би оновлення двох программ, див.
    # коментар над CLIENT_TAG_PREFIX у github_releases.py). Встановлення
    # (_install_downloaded_update нижче) лишається БЕЗ ЗМІН - той самий
    # .bat-механізм, що вже і є, лише джерело файлів тепер інше.
    def _check_for_update_now(self, manual):
        # Той самий баг/фікс, що вже застосований у client_app.py
        # (2026-08-15, "кнопка оновлення не скидалась"): поки завантаження
        # вже триває чи готове до встановлення, повторна перевірка не має
        # переписувати вигляд кнопки назад у "ще не завантажено".
        # Реальний баг (2026-08-16, знайдений при цьому ж переписуванні):
        # раніше ранній return тут НЕ перепланував автотаймер - якщо
        # автотік застав завантаження в процесі, увесь ланцюжок автопере-
        # вірки мовчки помирав назавжди. Той самий фікс, що вже давно є в
        # client_app.py, тут просто був пропущений - виправлено заразом.
        if self._update_ready_to_install or self._update_download_in_progress:
            if not manual:
                self.root.after(UPDATE_CHECK_INTERVAL_MS, self._poll_for_update)
            return
        # Нитпік з аудиту коду (2026-08-16): швидкі повторні кліки по "⟳"
        # раніше плодили окремий мережевий запит на кожен клік - нешкідливо
        # (ідемпотентний GET), але марно.
        if self._update_check_in_progress:
            return
        self._update_check_in_progress = True
        publish_token = self._read_github_publish_token()

        def worker():
            # Задача користувача (2026-08-17): "оновлень не було. нічого не
            # прийшло" - виявилось, що мережева/API-помилка (RuntimeError
            # від github_releases._request - обірваний зв'язок, ліміт
            # запитів GitHub, тимчасова недоступність) і "реально немає
            # новішої версії" раніше виглядали для користувача ІДЕНТИЧНО
            # ("Оновлень немає.") - помилку тихо ковтали тут, тож при
            # реальному збої перевірки взагалі не було способу відрізнити
            # "перевірив - новіших нема" від "не зміг перевірити". Тепер
            # текст помилки передається далі й показується окремо.
            check_error = None
            try:
                # Задача користувача (2026-08-19, "можливо зареєструватися
                # варто якось?"): анонімні перевірки обмежені 60/годину на
                # IP - gui.py вже й так тримає PAT-токен для публікації
                # (той самий, що вводиться на вкладці "Токен"), той самий
                # токен підвищує ліміт і для звичайних GET-перевірок теж,
                # без жодного нового поля вводу. Читається на ГОЛОВНОМУ
                # потоці (Tk-змінні не для доступу з фонового worker нижче) -
                # той самий принцип, що вже й token/client_version в "Публікація".
                release = github_releases.get_latest_release(
                    paths.GITHUB_RELEASES_OWNER, paths.GITHUB_RELEASES_REPO, github_releases.GUI_TAG_PREFIX,
                    token=publish_token or None,
                )
            except RuntimeError as exc:
                release = None
                check_error = str(exc)
            entry = None
            if release:
                version = github_releases.release_version(release, github_releases.GUI_TAG_PREFIX)
                if version and update_check.is_newer(version, __version__):
                    entry = {"version": version, "release": release}
            self._run_on_main_thread(lambda: self._apply_update_poll_result(entry, manual, check_error))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_update_poll_result(self, entry, manual=False, check_error=None):
        self._update_check_in_progress = False
        # Реальний баг (аудит коду, 2026-08-15): ця перевірка могла СТАРТУВАТИ
        # до того, як завантаження/встановлення розпочалось, і повернутись
        # (мережа/маніфест на повільній мережевій теці) вже ПІСЛЯ - без цього
        # guard вона тоді переписувала б кнопку назад у "не завантажено" або
        # ховала б її, навіть коли реально готове до встановлення оновлення
        # вже чекає на клік.
        if not (self._update_ready_to_install or self._update_download_in_progress):
            self._pending_update_entry = entry
            if entry:
                self.update_button.config(text=self._t("Оновлення {value}").format(value=entry["version"]))
                self.update_button.place(relx=1.0, x=-16, y=12, anchor="ne")
                if manual:
                    self.update_check_result_text.set("")
            else:
                self.update_button.place_forget()
                # Задача користувача (2026-08-15): "не має видвати спливаюче
                # вікно-повідомлення... просто тихесенько" - текст під кнопкою
                # замість messagebox.
                if manual:
                    if check_error:
                        self.update_check_result_text.set(
                            self._t("Не вдалося перевірити оновлення: {value}").format(value=check_error)
                        )
                    else:
                        self.update_check_result_text.set(self._t("Оновлень немає."))
        if not manual:
            self.root.after(UPDATE_CHECK_INTERVAL_MS, self._poll_for_update)

    # Задача користувача (2026-08-15): "роби через оновлення" - той самий
    # двофазний download->install флоу, що вже перевірений у client_app.py
    # (перший клік ЗАВАНТАЖУЄ, другий - уже написаний "Встановити і
    # перезапустити" - ВСТАНОВЛЮЄ). Публікація ВЛАСНОЇ версії gui.py для
    # гіпотетичних ІНШИХ інсталяцій прибрана - у цьому розгортанні є лише
    # одна, і їй потрібно РЕАЛЬНО встановлювати оновлення, а не публікувати
    # їх самій собі.
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
        self.update_button.config(text=self._t("Завантаження оновлення..."), state="disabled")
        destination = Path(BASE_DIR) / "updates"

        def worker():
            if not getattr(sys, "frozen", False):
                try:
                    code_backup.create_code_snapshot(label="pre_update", force=True)
                except OSError as exc:
                    error_text = str(exc)
                    self._run_on_main_thread(lambda: messagebox.showwarning(
                        self._t("Резервные копии"),
                        self._t("Не удалось создать снимок кода перед обновлением: {error}").format(error=error_text),
                    ))
            error = None
            target = None
            try:
                target = github_releases.download_and_extract_release(
                    entry["release"], destination, target_name="AI_Automation_Home",
                )
            except (RuntimeError, OSError) as exc:
                # Реальна знахідка (аудит коду, 2026-08-16): download_and_
                # extract_release() загортає більшість помилок у RuntimeError,
                # але mkdir(parents=True, exist_ok=True) усередині НІЧИМ не
                # обгорнутий - диск переповнений/немає прав кидає сирий
                # OSError, який раніше пролітав повз цей except. Флаг
                # _update_download_in_progress тоді лишався True назавжди
                # (_on_update_download_finished, що його скидає, просто
                # ніколи не викликається) - кнопка блокувалась до перезапуску.
                error = str(exc)
            self._run_on_main_thread(lambda: self._on_update_download_finished(entry, target, error))

        threading.Thread(target=worker, daemon=True).start()

    def _on_update_download_finished(self, entry, target, error):
        self._update_download_in_progress = False
        if error:
            self.update_button.config(text=self._t("Оновлення {value}").format(value=entry["version"]), state="normal")
            self.update_check_result_text.set(self._t("Не вдалось завантажити оновлення: {value}").format(value=error))
            return
        if not getattr(sys, "frozen", False):
            # dev-режим (python main.py): немає власної теки зібраного
            # .exe, яку можна безпечно замінити й перезапустити.
            self.update_button.config(text=self._t("Оновлення {value}").format(value=entry["version"]), state="normal")
            self.update_check_result_text.set(
                self._t("Завантажено в {value}. У dev-режимі застосуйте вручну.").format(value=target)
            )
            return
        self._downloaded_update_target = target
        self._update_ready_to_install = True
        self.update_check_result_text.set("")
        self.update_button.config(text=self._t("Встановити і перезапустити"), state="normal", bg="#1D9E75", fg="white")

    # Той самий прийом, що й client_app.py._install_downloaded_update:
    # заміна файлів РЕАЛЬНО ЗАПУЩЕНОГО .exe напряму неможлива на Windows
    # (файл заблокований, поки процес живий) - окремий .bat-скрипт чекає,
    # поки цей PID зникне з tasklist, тоді копіює нові файли поверх старої
    # теки (robocopy БЕЗ /MIR - system/backups/app_data.sqlite3/тест-Excel
    # лишаються недоторканими), перезапускає .exe і сам себе видаляє.
    def _install_downloaded_update(self):
        # Реальний баг (аудит коду, 2026-08-15): без цього guard подвійний
        # клік по "Встановити і перезапустити" (кнопка лишається активною
        # весь час) запускав би ДВА .bat-скрипти й, відповідно, два
        # паралельних запуски щойно оновленого .exe проти однієї й тієї ж БД.
        if self._update_install_in_progress:
            return
        source = self._downloaded_update_target
        if not source or not Path(source).exists():
            return
        # Той самий guard, що й on_close()/show_main_menu() - без нього
        # встановлення оновлення тихо відкидало б незбережені правки в
        # таблиці, на відміну від УСІХ інших шляхів виходу з програми.
        if self.edit_mode and self.has_unsaved_changes:
            if not messagebox.askyesno(
                self._t("Незбережені зміни"),
                self._t("Є незбережені зміни в таблиці. Встановити оновлення без збереження?"),
            ):
                return
        self._update_install_in_progress = True
        self.update_button.config(state="disabled")
        self.is_closing = True
        self.stop_telegram_bot(update_status=False)
        self.store.close()
        install_dir = BASE_DIR
        exe_path = Path(sys.executable)
        pid = os.getpid()
        script_path = Path(tempfile.gettempdir()) / f"ai_automation_home_update_{pid}.bat"
        script_lines = [
            # Реальний баг (2026-08-15, живий продакшн, client_app.py):
            # шлях користувача з кирилицею (OneDrive-тека) - .bat писався
            # як UTF-8, але cmd.exe за замовчуванням читає файл системною
            # кодовою сторінкою (не UTF-8), тож кириличні символи в шляхах
            # перетворювались на "сміття" ("Windows не может найти...").
            # chcp 65001 - ПЕРШИМ рядком (до "@echo off"!) - перевірено
            # реальним запуском: BOM+chcp-другим-рядком лишає "сміття"
            # перед першим токеном ("@echo off" не розпізнається), a
            # chcp-першим без BOM працює чисто.
            "chcp 65001 >nul",
            "@echo off",
            "setlocal",
            ":waitloop",
            f'"%SystemRoot%\\System32\\tasklist.exe" /FI "PID eq {pid}" 2>NUL | "%SystemRoot%\\System32\\find.exe" "{pid}" >NUL',
            'if "%ERRORLEVEL%"=="0" (',
            '    "%SystemRoot%\\System32\\ping.exe" -n 2 127.0.0.1 >nul',
            "    goto waitloop",
            ")",
            # Той самий другий рубіж захисту, що й у client_app.py (2026-08-
            # 17, живий продакшн): /XF settings.json означає, що оновлення
            # НІКОЛИ не перезапише локальні налаштування цієї машини, хоч
            # би що опинилось у завантаженому пакеті - незалежно від того,
            # чи build_exe.py колись знову помилково підкладе туди чужий
            # settings.json. app_data.sqlite3 додано тим самим рубежем
            # (2026-08-17, живий продакшн - client_app.py постраждав від
            # ЦЬОГО САМОГО класу бага з тестовою базою даних) - для
            # симетрії з клієнтом, хоч власна збірка gui.py й захищена
            # backup_runtime_data вище.
            f'robocopy "{source}" "{install_dir}" /E /IS /IT /XF settings.json app_data.sqlite3 /R:5 /W:1 >NUL',
            # Реальний баг (аудит коду, 2026-08-15): раніше джерело
            # видалялось безумовно - код виходу robocopy >=8 означає реальну
            # помилку копіювання (0-7 - різні варіанти успіху). Без цієї
            # перевірки невдале копіювання мовчки видаляло б завантажений
            # пакет без жодного сліду проблеми.
            'if %ERRORLEVEL% LSS 8 (',
            f'    rmdir /s /q "{source}" >nul 2>&1',
            ")",
            f'start "" "{exe_path}"',
            '(goto) 2>nul & del "%~f0"',
        ]
        # Реальна знахідка (аудит коду, 2026-08-16): бот і БД (рядки вище)
        # зупиняються БЕЗУМОВНО, до того, як хоч щось тут гарантовано
        # спрацює - без цього try/except рідкісна, але можлива помилка
        # запису .bat-файлу чи запуску cmd.exe (диск повний, антивірус)
        # лишала б вікно відкритим, але вже непрацездатним (бот зупинено,
        # БД закрита) - без жодного повідомлення, чому.
        try:
            script_path.write_text("\r\n".join(script_lines) + "\r\n", encoding="utf-8")
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
            messagebox.showerror(
                self._t("Встановлення оновлення"),
                self._t(
                    "Не вдалося запустити встановлення оновлення: {error}\n\n"
                    "Бот і база даних уже зупинені - закрийте программу "
                    "вручну і запустіть знову."
                ).format(error=str(exc)),
            )
        self.root.after(200, self.root.destroy)

    def _show_only(self, frame, view_name):
        for child in (
            self.main_menu_frame,
            self.table_frame,
            self.settings_frame,
            self.commands_frame,
            self.custom_buttons_frame,
            self.payment_methods_frame,
        ):
            child.pack_forget()
        frame.pack(fill="both", expand=True)
        self.current_view = view_name

    def show_main_menu(self):
        if self.edit_mode and self.has_unsaved_changes:
            if not messagebox.askyesno(
                self._t("Незбережені зміни"),
                self._t("Повернутися до меню без збереження змін?"),
            ):
                return
            if not self._discard_current_sheet_changes():
                return
            self._exit_edit_mode()
            self.show_sheet(self.current_sheet)
        self._show_only(self.main_menu_frame, "main")

    def show_table(self):
        self._show_only(self.table_frame, "table")

    def show_settings(self):
        self._update_telegram_settings_labels()
        self._show_only(self.settings_frame, "settings")

    # Журнали (Задача користувача) — відкриваються ОКРЕМИМ tk.Toplevel
    # (не через _show_only), щоб можна було лишити журнал відкритим і далі
    # працювати в решті застосунку (не модальне вікно — без grab_set).
    # Задача користувача: "основне вікно програми завжди має нижчий
    # приорітет... кожне наступне вікно... завжди буде вище основного" —
    # .transient(self.root) (як і на всіх НЕ модальних попапах нижче) саме
    # й змушує Windows тримати вікно НАЗАВЖДИ над власником у z-порядку
    # (owned-window behavior), незалежно від кліку — прибрано тут, бо це
    # прямо суперечить власному задуму "не модальне, можна лишити відкритим
    # і працювати поруч". Модальні (з grab_set) вікна transient() НЕ
    # чіпали — там "завжди над батьком, поки відкрите" саме й потрібне.
    def show_journals(self):
        self._open_journals_window()

    def _open_journals_window(self):
        if getattr(self, "journals_window", None) is not None and self.journals_window.winfo_exists():
            self.journals_window.deiconify()
            self.journals_window.lift()
            self.journals_window.focus_force()
            return
        self._build_journals_window()

    def _build_journals_window(self):
        window = tk.Toplevel(self.root)
        window.title(self._t("Журнали"))
        window.protocol("WM_DELETE_WINDOW", lambda: self._close_journals_window(window))
        window.bind("<Escape>", lambda event: self._close_journals_window(window))
        self.journals_window = window

        self._build_journals_hub_view(window)
        self._build_action_log_view(window)
        self._build_work_log_view(window)
        self._show_journals_view("hub")
        self._center_window(window, width=820, height=560)

    def _close_journals_window(self, window):
        window.destroy()
        self.journals_window = None
        # Реальний баг (аудит коду, 2026-08-15): без цього скидання
        # action_log_list_frame лишався б посиланням на вже знищений віджет -
        # закриття вікна ДО завершення фонового HTTP-запиту (_refresh_action_log)
        # кидало б TclError у відкладеному callback'у (_apply_action_log_rows),
        # той самий клас бага, що вже виправлений для Персоналу нижче.
        self.action_log_list_frame = None

    def _show_journals_view(self, view_name):
        for frame in (self.journals_hub_frame, self.action_log_frame, self.work_log_frame):
            frame.pack_forget()
        if view_name == "action_log":
            self._refresh_action_log()
            self.action_log_frame.pack(fill="both", expand=True)
        elif view_name == "work_log":
            self._refresh_work_log()
            self.work_log_frame.pack(fill="both", expand=True)
        else:
            self.journals_hub_frame.pack(fill="both", expand=True)

    def show_action_log(self):
        self._show_journals_view("action_log")

    def show_work_log(self):
        self._show_journals_view("work_log")

    def show_commands(self):
        self._refresh_commands()
        self._show_only(self.commands_frame, "commands")

    def show_custom_buttons(self):
        self._refresh_custom_buttons()
        self._refresh_actions_view()
        self._show_only(self.custom_buttons_frame, "custom_buttons")

    # "Персонал" - той самий ліниво-побудований tk.Toplevel патерн, що й
    # "Журнали" (show_journals/_open_journals_window вище): відкривається
    # ОКРЕМИМ немодальним вікном, а не через _show_only, тож головне вікно
    # й далі лишається доступним поруч.
    def show_personnel(self):
        self._open_personnel_window()

    def _open_personnel_window(self):
        if getattr(self, "personnel_window", None) is not None and self.personnel_window.winfo_exists():
            self._refresh_personnel()
            self.personnel_window.deiconify()
            self.personnel_window.lift()
            self.personnel_window.focus_force()
            return
        self._build_personnel_window()

    def _build_personnel_window(self):
        window = tk.Toplevel(self.root)
        window.title(self._t("Персонал"))
        window.protocol("WM_DELETE_WINDOW", lambda: self._close_personnel_window(window))
        window.bind("<Escape>", lambda event: self._close_personnel_window(window))
        self.personnel_window = window

        self._build_personnel_view(window)
        self._refresh_personnel()
        self._center_window(window, width=720, height=520)

    def _close_personnel_window(self, window):
        window.destroy()
        self.personnel_window = None
        # Свіжий пере-аудит (New-Minor #6, побічна знахідка): без цього
        # скидання personnel_list_frame лишався б посиланням на вже
        # знищений віджет - збереження стилю кнопок ПІСЛЯ закриття
        # "Персонал" (без повторного відкриття) кидало б TclError усередині
        # _clear_frame (_refresh_personnel перевіряє саме цей атрибут).
        self.personnel_list_frame = None

    def show_payment_methods(self):
        self._refresh_payment_methods()
        self._show_only(self.payment_methods_frame, "payment_methods")

    def _on_escape(self, event=None):
        if getattr(self, "current_view", "main") in {"commands", "payment_methods"}:
            self.show_settings()
        elif getattr(self, "current_view", "main") != "main":
            self.show_main_menu()

    def _build_layout(self):
        self.table_frame = tk.Frame(self.root)

        top_bar = tk.Frame(self.table_frame)
        top_bar.pack(side="top", fill="x", padx=8, pady=6)

        back_button = tk.Button(top_bar, text=self._t("← Назад"), command=self.show_main_menu)
        back_button.pack(side="left")

        title = tk.Label(top_bar, text=self._t("Дані / Таблиця"), font=("Segoe UI", 12, "bold"))
        title.pack(side="left", padx=12)

        self.refresh_table_button = tk.Button(
            top_bar,
            text=self._t("Оновити"),
            command=self.refresh_current_sheet,
        )
        self.refresh_table_button.pack(side="left", padx=(0, 8))

        table_body = tk.Frame(self.table_frame)
        table_body.pack(side="top", fill="both", expand=True)

        # ліва панель (кнопки вкладок), без скролу
        left_container = tk.Frame(table_body, width=180)
        left_container.pack(side="left", fill="y")
        left_container.pack_propagate(False)

        self.buttons_frame = tk.Frame(left_container)
        self.buttons_frame.pack(side="top", fill="both", expand=True)

        # права панель — таблиця + панель редагування знизу
        right_container = tk.Frame(table_body)
        right_container.pack(side="left", fill="both", expand=True)

        self.tree = ttk.Treeview(right_container, show="headings")
        # Задача користувача (2026-08-14, скріншот обрізаних заголовків
        # "Толщина, мм"/"Ширина, мм"/"Длина, мм"): "зроби окремо
        # налаштовувану [ширину кожного стовпця]" - show_sheet() раніше
        # примусово ставив width=120 УСІМ стовпцям на кожен switch_sheet/
        # рефреш, тож навіть перетягнута вручну межа стовпця миттю
        # скидалась назад. ButtonRelease-1 - той самий подієвий гачок, що
        # й Treeview вже використовує для клітинок нижче; порівняння зі
        # збереженим станом ПЕРЕД записом - щоб не писати settings.json на
        # кожен звичайний клік по рядку, лише коли ширина справді змінилась.
        self.tree.bind("<ButtonRelease-1>", self._save_current_column_widths, add="+")
        self.tree.bind("<Button-1>", self._on_tree_header_click, add="+")
        vsb = ttk.Scrollbar(right_container, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(right_container, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        bottom_frame = tk.Frame(right_container)
        bottom_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=6)

        right_container.rowconfigure(0, weight=1)
        right_container.columnconfigure(0, weight=1)

        self.edit_button = tk.Button(bottom_frame, text=self._t("Редагувати"), command=self.toggle_edit_mode)
        self.edit_button.pack(side="left", padx=4)

        self.add_row_button = tk.Button(bottom_frame, text=self._t("Додати рядок"), command=self.add_row)
        self.delete_row_button = tk.Button(bottom_frame, text=self._t("Видалити рядок"), command=self.delete_row)
        self.save_button = tk.Button(bottom_frame, text=self._t("Зберегти зміни"), command=self.save_changes)

        self.next_page_button = tk.Button(bottom_frame, text=self._t("Далі"), command=self.next_page)
        self.next_page_button.pack(side="right", padx=4)

        self.page_label = tk.Label(bottom_frame, text="")
        self.page_label.pack(side="right", padx=8)

        self.prev_page_button = tk.Button(bottom_frame, text=self._t("Назад"), command=self.previous_page)
        self.prev_page_button.pack(side="right", padx=4)

    # --- Екран налаштувань: режим ШИ, формат дати, довідка ---
    def _build_settings_view(self):
        self.settings_frame = tk.Frame(self.root)

        top_bar = tk.Frame(self.settings_frame)
        top_bar.pack(side="top", fill="x", padx=8, pady=6)

        back_button = tk.Button(top_bar, text=self._t("← Назад"), command=self.show_main_menu)
        back_button.pack(side="left")

        title = tk.Label(top_bar, text=self._t("Налаштування"), font=("Segoe UI", 12, "bold"))
        title.pack(side="left", padx=12)

        content = tk.Frame(self.settings_frame)
        content.pack(side="top", fill="both", expand=True, padx=40, pady=40)

        main_settings = tk.Frame(content)
        main_settings.pack(side="left", fill="both", expand=True)

        side_panel = tk.Frame(content, width=220)
        side_panel.pack(side="right", fill="y", padx=(32, 0))
        side_panel.pack_propagate(False)

        choose_token_button = tk.Button(
            main_settings,
            text=self._t("Додати шлях до ТГ-ключа"),
            width=28,
            height=2,
            command=self.choose_telegram_token_file,
        )
        choose_token_button.pack(anchor="w", pady=(0, 12))

        token_file_label = tk.Label(
            main_settings,
            textvariable=self.telegram_file_text,
            anchor="w",
            justify="left",
            wraplength=620,
        )
        token_file_label.pack(anchor="w", fill="x", pady=(0, 16))

        # Задача користувача (2026-08-15): "налаштувати керування із
        # старої програми до нової... в старій має бути показник онлайну
        # сервера" - ці самі 2 рядки (status_label/heartbeat_label) і ці
        # самі 2 кнопки лишаються на тому самому місці, що й були - тепер
        # показують стан ВІДДАЛЕНОГО сервера (client_app.py, інший ПК,
        # _remote_control_tick нижче) і шлють йому команди замість
        # локального запуску (_on_remote_start_bot_clicked/_on_remote_
        # stop_bot_clicked, remote_control_client.py).
        connect_button = tk.Button(
            main_settings,
            text=self._t("Підключити Telegram"),
            width=28,
            command=lambda: self._on_remote_command_clicked("start_bot"),
        )
        connect_button.pack(anchor="w", pady=(0, 8))

        stop_button = tk.Button(
            main_settings,
            text=self._t("Зупинити Telegram"),
            width=28,
            command=lambda: self._on_remote_command_clicked("stop_bot"),
        )
        stop_button.pack(anchor="w", pady=(0, 16))

        status_label = tk.Label(
            main_settings,
            textvariable=self.telegram_status_text,
            anchor="w",
            justify="left",
            wraplength=620,
        )
        status_label.pack(anchor="w", fill="x", pady=(0, 2))
        self.telegram_status_label = status_label

        # Ненав'язливий рядок - коли востаннє реально приходив статус від
        # ВІДДАЛЕНОГО сервера (_remote_control_tick, кожні 15с), а не
        # застиглий текст, що міг лишитись давно.
        heartbeat_label = tk.Label(
            main_settings,
            textvariable=self.telegram_heartbeat_text,
            anchor="w",
            justify="left",
            wraplength=620,
            fg="gray40",
        )
        heartbeat_label.pack(anchor="w", fill="x", pady=(0, 18))

        # Задача користувача (2026-08-08): окремі, явні кнопки увімк/вимк/
        # перезапуск форми (Telegram Mini App) внизу зліва Налаштувань,
        # незалежно від підключення самого бота — pack(side="bottom") у
        # цьому ж лівому стовпці (main_settings) притискає їх до самого
        # низу вікна, а не одразу під heartbeat_label.
        webapp_form_frame = tk.Frame(main_settings)
        webapp_form_frame.pack(side="bottom", fill="x", anchor="w")

        webapp_form_status_label = tk.Label(
            webapp_form_frame,
            textvariable=self.webapp_status_text,
            anchor="w",
            justify="left",
            wraplength=620,
            fg="gray40",
        )
        webapp_form_status_label.pack(anchor="w", fill="x", pady=(8, 0))

        webapp_form_buttons = tk.Frame(webapp_form_frame)
        webapp_form_buttons.pack(anchor="w")

        start_webapp_form_button = tk.Button(
            webapp_form_buttons,
            text=self._t("Увімкнути форму (Mini App)"),
            command=lambda: self._on_remote_command_clicked("start_form"),
        )
        start_webapp_form_button.pack(side="left", padx=(0, 8))

        stop_webapp_form_button = tk.Button(
            webapp_form_buttons,
            text=self._t("Вимкнути форму (Mini App)"),
            command=lambda: self._on_remote_command_clicked("stop_form"),
        )
        stop_webapp_form_button.pack(side="left", padx=(0, 8))

        restart_webapp_form_button = tk.Button(
            webapp_form_buttons,
            text=self._t("Перезапустити форму (Mini App)"),
            command=lambda: self._on_remote_command_clicked("restart_form"),
        )
        restart_webapp_form_button.pack(side="left")

        commands_button = tk.Button(
            side_panel,
            text=self._t("Команди"),
            width=20,
            height=2,
            command=self.show_commands,
        )
        commands_button.pack(anchor="n", fill="x", pady=(0, 12))

        payment_methods_button = tk.Button(
            side_panel,
            text=self._t("Способи оплати"),
            width=20,
            height=2,
            command=self.show_payment_methods,
        )
        payment_methods_button.pack(anchor="n", fill="x", pady=(0, 12))

        display_format_button = tk.Button(
            side_panel,
            text=self._t("Формат отображения"),
            width=20,
            height=2,
            command=self.open_display_format_dialog,
        )
        display_format_button.pack(anchor="n", fill="x", pady=(0, 12))

        button_style_button = tk.Button(
            side_panel,
            text=self._t("Формат кнопок"),
            width=20,
            height=2,
            command=self.open_button_style_dialog,
        )
        button_style_button.pack(anchor="n", fill="x", pady=(0, 12))

        webapp_style_button = tk.Button(
            side_panel,
            text=self._t("Оформлення форми Telegram"),
            width=20,
            height=2,
            command=self.open_webapp_style_dialog,
        )
        webapp_style_button.pack(anchor="n", fill="x", pady=(0, 12))

        request_mode_button = tk.Button(
            side_panel,
            text=self._t("Режим обработки запросов"),
            width=20,
            height=2,
            command=self.open_request_processing_mode_dialog,
        )
        request_mode_button.pack(anchor="n", fill="x", pady=(0, 12))

        # Задача користувача (2026-08-19): "додай кнопку системні команди
        # чат-боту... галочки на ввімкнення" - /status, /sheets, /first,
        # /chatid (DEBUG_TOOLS-команди, telegram_dialog_core.py) - можна
        # вимкнути поштучно, без зміни коду.
        system_commands_button = tk.Button(
            side_panel,
            text=self._t("Системные команды чат-бота"),
            width=20,
            height=2,
            command=self.open_system_commands_dialog,
        )
        system_commands_button.pack(anchor="n", fill="x", pady=(0, 12))

        # Задача користувача (2026-08-16): "сховай це поки і скрізь це
        # відключи це важливо" - кнопка "Таблиця Excel" (вибір локального/
        # онлайн джерела) прибрана з UI. Єдина точка виклику
        # open_excel_source_dialog в усьому файлі (перевірено грепом) -
        # прибираючи саме цю кнопку, диспетчер лишається структурно
        # недосяжним "скрізь", без потреби чіпати сам метод чи
        # excel_source_status_text. Тимчасово ("поки") - лишено як
        # закоментований блок нижче для швидкого повернення.
        # excel_source_button = tk.Button(
        #     side_panel, text=self._t("Таблиця Excel"), width=20, height=2,
        #     command=self.open_excel_source_dialog,
        # )
        # excel_source_button.pack(anchor="n", fill="x")
        # excel_source_status_label = tk.Label(
        #     side_panel, textvariable=self.excel_source_status_text, anchor="w",
        #     justify="left", wraplength=200, fg="gray40", font=("Segoe UI", 8),
        # )
        # excel_source_status_label.pack(anchor="n", fill="x", pady=(2, 12))

        align_table_button = tk.Button(
            side_panel,
            text=self._t("Вирівняти таблицю"),
            width=20,
            height=2,
            command=self.align_excel_table,
        )
        align_table_button.pack(anchor="n", fill="x", pady=(0, 12))
        self.align_table_button = align_table_button

        # Задача користувача (2026-08-15): "тепер змінюй це на автоматичне
        # з'єднання між программами" - раніше тут була кнопка "Дистанційне
        # керування" (обрати спільну теку + вставити ключ). Тепер адреса й
        # ключ - фіксовані константи (paths.py), налаштовувати вже нічого -
        # кнопку прибрано разом з усім діалогом.

        # Задача користувача (2026-08-19): "Публікація оновлень" перенесена
        # на головний екран (menu_panel вище, замість "В разработке") -
        # тут більше не дублюється.
        db_backups_button = tk.Button(
            side_panel,
            text=self._t("Резервные копии"),
            width=20,
            height=2,
            command=self.open_db_backups_dialog,
        )
        db_backups_button.pack(anchor="n", fill="x")

        db_snapshot_heartbeat_label = tk.Label(
            side_panel,
            textvariable=self.db_snapshot_heartbeat_text,
            anchor="w",
            justify="left",
            wraplength=200,
            fg="gray40",
            font=("Segoe UI", 8),
        )
        db_snapshot_heartbeat_label.pack(anchor="n", fill="x", pady=(2, 0))

        # Задача користувача (2026-08-08, реальний баг живого тестування):
        # розбіжність "кількість, шт" vs "фізичний вимір" (м3/м2/мп) для
        # рядка складу вирішується ТІЛЬКИ тут, у GUI — "переходимо загально
        # на програму де це можливо і зручно робити" (пряма вказівка
        # користувача, не в бот-чаті).
        mismatch_check_button = tk.Button(
            side_panel,
            text=self._t("Перевірка залишків (шт/кубатура)"),
            width=20,
            height=2,
            command=self.open_quantity_measure_mismatch_dialog,
        )
        mismatch_check_button.pack(anchor="n", fill="x", pady=(12, 0))

    def _build_journals_hub_view(self, parent):
        self.journals_hub_frame = tk.Frame(parent)

        top_bar = tk.Frame(self.journals_hub_frame)
        top_bar.pack(side="top", fill="x", padx=8, pady=6)

        back_button = tk.Button(
            top_bar, text=self._t("Закрити"), command=lambda: self._close_journals_window(self.journals_window),
        )
        back_button.pack(side="left")

        title = tk.Label(top_bar, text=self._t("Журнали"), font=("Segoe UI", 12, "bold"))
        title.pack(side="left", padx=12)

        menu_panel = tk.Frame(self.journals_hub_frame)
        menu_panel.pack(expand=True)

        action_log_button = tk.Button(
            menu_panel,
            text=self._t("Журнал дій"),
            width=28,
            height=2,
            command=self.show_action_log,
        )
        action_log_button.pack(pady=8)

        work_log_button = tk.Button(
            menu_panel,
            text=self._t("Журнал виконаних робіт"),
            width=28,
            height=2,
            command=self.show_work_log,
        )
        work_log_button.pack(pady=8)

    def _build_action_log_view(self, parent):
        self.action_log_frame = tk.Frame(parent)

        top_bar = tk.Frame(self.action_log_frame)
        top_bar.pack(side="top", fill="x", padx=8, pady=6)

        back_button = tk.Button(top_bar, text=self._t("← Назад"), command=lambda: self._show_journals_view("hub"))
        back_button.pack(side="left")

        title = tk.Label(top_bar, text=self._t("Журнал действий"), font=("Segoe UI", 12, "bold"))
        title.pack(side="left", padx=12)

        refresh_button = tk.Button(top_bar, text=self._t("Обновить"), command=self._refresh_action_log)
        refresh_button.pack(side="left", padx=8)

        # Задача користувача (2026-08-15): "синхронізація" - журнал тепер
        # ЛИШЕ показує реальні дії client_app.py через тунель - "Очистити
        # журнал"/"Удалить" запис прибрані (не приховані - видалені), не
        # лишені непрацюючими проти власної порожньої локальної бази.
        tk.Label(
            top_bar,
            text=self._t("Перегляд лише для читання - дані тягнуться напряму з client_app.py."),
            fg="#666666",
        ).pack(side="left", padx=8)

        content = tk.Frame(self.action_log_frame)
        content.pack(side="top", fill="both", expand=True, padx=20, pady=20)

        header = tk.Frame(content)
        header.pack(fill="x", pady=(0, 8))

        for text, width in (
            ("Пользователь", 20),
            ("Действие", 18),
            ("Статус", 16),
            ("Время", 22),
            ("Кратко", 46),
            ("Действия", 18),
        ):
            tk.Label(header, text=text, width=width, anchor="w", font=("Segoe UI", 9, "bold")).pack(side="left")

        self.action_log_list_frame = self._create_scrollable_list(content)

    def _build_work_log_view(self, parent):
        self.work_log_frame = tk.Frame(parent)

        top_bar = tk.Frame(self.work_log_frame)
        top_bar.pack(side="top", fill="x", padx=8, pady=6)

        back_button = tk.Button(top_bar, text=self._t("← Назад"), command=lambda: self._show_journals_view("hub"))
        back_button.pack(side="left")

        title = tk.Label(top_bar, text=self._t("Журнал виконаних робіт"), font=("Segoe UI", 12, "bold"))
        title.pack(side="left", padx=12)

        refresh_button = tk.Button(top_bar, text=self._t("Обновити"), command=self._refresh_work_log)
        refresh_button.pack(side="left", padx=8)

        clear_button = tk.Button(top_bar, text=self._t("Очистити журнал"), command=self.clear_work_log)
        clear_button.pack(side="left", padx=4)

        content = tk.Frame(self.work_log_frame)
        content.pack(side="top", fill="both", expand=True, padx=20, pady=20)

        header = tk.Frame(content)
        header.pack(fill="x", pady=(0, 8))

        for text, width in (
            ("Дата", 20),
            ("Назва", 30),
            ("Коротко", 50),
            ("Дії", 18),
        ):
            tk.Label(header, text=self._t(text), width=width, anchor="w", font=("Segoe UI", 9, "bold")).pack(side="left")

        self.work_log_list_frame = self._create_scrollable_list(content)

    # Редактор кнопок — показує ВЕСЬ список/дерево вже створених кнопок
    # (зліва, скролиться) + прев'ю обраної кнопки (справа). Кожен рядок:
    # "+" (додати дочірню САМЕ до цієї кнопки), "ред" (редагувати),
    # "x" (видалити, з підтвердженням; попередження про каскад, якщо є
    # дочірні). Клік по мітці — вибір кнопки, що оновлює прев'ю.
    def _build_custom_buttons_view(self):
        self.custom_buttons_frame = tk.Frame(self.root)
        self.custom_buttons_selected_id = None
        # Задача користувача (2026-08-17): "редактор кнопок зроби
        # синхронним" - None означає "ще не завантажено/не вдалось
        # отримати з client_app.py" (той самий контракт, що вже й
        # self._personnel_users_cache) - НЕ "кнопок немає". Плаский список
        # 9-елементних рядків list_all_custom_buttons(); _custom_buttons_
        # children/_custom_button_by_id нижче читають з нього замість
        # окремого запиту до self.store на кожен вузол дерева.
        self._custom_buttons_cache = None
        self._custom_buttons_refresh_generation = 0
        # Крок "Дії" remote-sync (2026-08-18): той самий None-контракт
        # ("ще не завантажено/немає зв'язку", НЕ "дій немає") для дерева
        # operations+fields+columns, що показує сусідня вкладка "Дії".
        self._operations_tree_cache = None
        self._actions_view_refresh_generation = 0

        top_bar = tk.Frame(self.custom_buttons_frame)
        top_bar.pack(side="top", fill="x", padx=8, pady=6)

        back_button = tk.Button(top_bar, text=self._t("← Назад"), command=self.show_main_menu)
        back_button.pack(side="left")

        title = tk.Label(top_bar, text=self._t("Редактор кнопок"), font=("Segoe UI", 12, "bold"))
        title.pack(side="left", padx=12)

        # Задача користувача (2026-08-18): "додай кнопку яка буде
        # перезберігати ці дані у хмарі... лише якщо кнопку натис - хмара
        # оновилась... точний контроль" - єдиний спосіб записати хмарну
        # істину "стандартного меню" (11 кореневих мігрованих кнопок).
        # Автоматичного запису НЕМАЄ ніде більше - client_app.py при старті
        # лише ЧИТАЄ хмару (_reconcile_standard_menu_with_cloud), ніколи
        # сам туди не пише.
        self.save_standard_menu_cloud_button = tk.Button(
            top_bar,
            text=self._t("Сохранить стандарт в облако"),
            command=self._on_save_standard_menu_to_cloud_clicked,
            **self._chip_button_style(),
        )
        self.save_standard_menu_cloud_button.pack(side="right")

        # Задача користувача (2026-08-18): "ця кнопка має бути видима
        # завжди, щоб кожен раз з програми міг глянути" - ПОСТІЙНИЙ рядок
        # (не лише одразу після збереження), щоб у будь-який момент можна
        # було перевірити реальний вміст теки, а не вірити на слово
        # діалогу успіху.
        #
        # Реальна знахідка того самого дня ("поправ там шлях"): домашня
        # программа (gui.py) й client_app.py можуть працювати на РІЗНИХ
        # машинах/OneDrive-акаунтах (тут-таки живий випадок: gui.py бачила
        # СВІЙ особистий, а client_app.py насправді пише в "Vladimir2\
        # OneDrive - Diverus, UAB") - показувати ЛОКАЛЬНИЙ шлях gui.py тут
        # означає майже гарантовано брехати користувачу. Тому текст рядка й
        # сама кнопка тепер питають РЕАЛЬНИЙ шлях у client_app.py через
        # /control/standard_menu_cloud_path (фон-потік, не блокує вікно).
        cloud_status_row = tk.Frame(self.custom_buttons_frame)
        cloud_status_row.pack(side="top", fill="x", padx=8, pady=(0, 6))

        self._standard_menu_cloud_path_var = tk.StringVar(
            value=self._t("Облако стандартного меню: {value}").format(value=self._t("Проверка пути..."))
        )
        tk.Label(
            cloud_status_row, textvariable=self._standard_menu_cloud_path_var,
            font=("Segoe UI", 8), fg="#57606a",
        ).pack(side="left")

        tk.Button(
            cloud_status_row,
            text=self._t("Открыть папку"),
            command=self._open_standard_menu_cloud_folder,
            **self._chip_button_style(),
        ).pack(side="right")

        self._refresh_standard_menu_cloud_path_label()

        # Дві вкладки (Задача користувача): "Налаштування гілок" — те саме
        # дерево кастомних кнопок, що й раніше; "Дії" — перегляд усіх дій
        # бота і того, з якими колонками таблиці кожна працює (перший крок —
        # лише перегляд, без редагування мапінгу).
        notebook = ttk.Notebook(self.custom_buttons_frame)
        notebook.pack(side="top", fill="both", expand=True, padx=20, pady=(0, 20))

        content = tk.Frame(notebook)
        actions_tab = tk.Frame(notebook)
        notebook.add(content, text=self._t("Налаштування гілок"))
        notebook.add(actions_tab, text=self._t("Дії"))
        self._build_actions_view(actions_tab)

        list_side = tk.Frame(content)
        list_side.pack(side="left", fill="both", expand=True, padx=(0, 16))

        tk.Label(
            list_side,
            text=self._t("Кнопки, які ви додасте тут, з'являться в головному меню бота в Telegram."),
            wraplength=520,
            justify="left",
        ).pack(anchor="w", pady=(0, 12))

        self.add_root_button = tk.Button(
            list_side,
            text=self._t("+ Додати кореневу кнопку"),
            width=26,
            command=lambda: self.add_custom_button_dialog(None),
            fg="#1a7f37",
            **self._chip_button_style(),
        )
        self.add_root_button.pack(anchor="w", pady=(0, 8))

        self.custom_buttons_list_frame = self._create_scrollable_list(list_side)

        preview_side = tk.Frame(content, width=260, relief="groove", borderwidth=1)
        preview_side.pack(side="right", fill="y")
        preview_side.pack_propagate(False)

        tk.Label(preview_side, text=self._t("Прев'ю"), font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=12, pady=(12, 4))
        self.custom_button_preview_frame = tk.Frame(preview_side)
        self.custom_button_preview_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    # Той самий фон-потік + _run_on_main_thread + guard-прапорець, що вже й
    # _on_role_menu_selected вище (не блокувати вікно на весь мережевий
    # timeout, захист від подвійного кліку). На відміну від решти дій
    # редактора кнопок (кожна - миттєвий push однієї зміни) - ця кнопка
    # явно й одноразово перезаписує ВЕСЬ хмарний файл станом "стандартного
    # меню" ПРЯМО ЗАРАЗ, єдиний спосіб змінити хмару (Задача користувача,
    # 2026-08-18: "точний контроль").
    def _on_save_standard_menu_to_cloud_clicked(self):
        if self._standard_menu_cloud_save_in_progress:
            return
        self._standard_menu_cloud_save_in_progress = True
        self.save_standard_menu_cloud_button.configure(state="disabled")

        def worker():
            error = None
            try:
                result = remote_control_client.save_standard_menu_to_cloud()
                if not result.get("ok"):
                    error = result.get("error") or self._t("Не удалось сохранить.")
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                try:
                    detail = json.loads(detail).get("error") or detail
                except ValueError:
                    pass
                error = detail
            except Exception as exc:
                error = str(exc)

            def finish():
                self._standard_menu_cloud_save_in_progress = False
                self.save_standard_menu_cloud_button.configure(state="normal")
                if error:
                    messagebox.showerror(self._t("Редактор кнопок"), error)
                    return
                messagebox.showinfo(
                    self._t("Редактор кнопок"),
                    self._t("Стандартное меню сохранено в облако."),
                )

            self._run_on_main_thread(finish)

        threading.Thread(target=worker, daemon=True).start()

    # Реальна знахідка (2026-08-18, "поправ там шлях"): раніше тут просто
    # os.startfile()'ився ЛОКАЛЬНИЙ (gui.py-машини) шлях - хибне
    # припущення, що обидві программи завжди на тому самому OneDrive-
    # акаунті (спростовано живим випадком: "Vladimir2\OneDrive - Diverus,
    # UAB" на робочому ПК, зовсім не особистий акаунт тут). Тепер завжди
    # питає РЕАЛЬНИЙ шлях у client_app.py - і чесно каже, коли ЦЮ теку
    # неможливо відкрити локально (шлях лежить на ІНШІЙ машині), замість
    # мовчки відкривати НЕПРАВИЛЬНУ (свою) теку.
    # Задача користувача (2026-08-18, остаточно підтверджено): "домашня
    # версія" (gui.py) і client_app.py працюють на ОДНІЙ і тій самій машині
    # — тож правильний шлях резолвиться ЛОКАЛЬНО, у власному процесі gui.py
    # (той самий standard_menu_cloud.cloud_folder_path(), що й client_app.py
    # використовує для запису), а не запитом через тунель до client_app.py.
    # Попередня "різні машини/акаунти" гіпотеза була хибною.
    def _refresh_standard_menu_cloud_path_label(self):
        if not hasattr(self, "_standard_menu_cloud_path_var"):
            return
        folder = standard_menu_cloud.cloud_folder_path()
        if folder is None:
            text = self._t("OneDrive не найден на этом компьютере")
        else:
            text = str(folder / "standard_menu.json")
        self._standard_menu_cloud_path_var.set(
            self._t("Облако стандартного меню: {value}").format(value=text)
        )

    def _open_standard_menu_cloud_folder(self):
        folder = standard_menu_cloud.cloud_folder_path()
        if folder is None:
            messagebox.showerror(
                self._t("Редактор кнопок"),
                self._t("OneDrive не найден на этом компьютере."),
            )
            return
        try:
            os.startfile(folder)
        except OSError as error:
            messagebox.showerror(
                self._t("Редактор кнопок"),
                self._t("Не удалось открыть папку:\n{value}\n\n{error}").format(
                    value=folder, error=error
                ),
            )

    # "+" зелений — дія ЗБІЛЬШУЄ число в цій колонці; "−" червоний —
    # ЗМЕНШУЄ; "■" синій — лише інформує/ідентифікує, число не змінюється.
    _ACTION_FIELD_MARKERS = {
        "add": ("+", "#1a7f37"),
        "subtract": ("−", "#d1242f"),
        "info": ("■", "#0969da"),
    }

    # Крок 3+ "Дії", Етап 2: яка вкладка -> яке "джерело" в
    # _actions_columns_by_source (щоб знайти живі заголовки/колонки цієї
    # вкладки при редагуванні прив'язки).
    _SHEET_TO_SOURCE = {
        "СКЛАД": "warehouse",
        SALES_SHEET_NAME: "sales",
        ANTISEPTIC_SHEET_NAME: "antiseptic",
    }

    # bot_operations.prefill_json зберігає англомовні семантичні ключі
    # payload'а (product/condition) — для показу в попапі перекладаємо на
    # ті самі україномовні підписи, що були раніше в статичному словнику.
    _PREFILL_FIELD_LABELS = {"product": "Товар", "condition": "Тип"}

    # Крок "Дії" remote-sync (2026-08-18): 6 лукапів над self._operations_
    # tree_cache (id, code, kind, requires_row_identity, label,
    # parent_action_code, prefill_json, position, enabled, builtin_key для
    # operations; id, operation_id, field_key, label, is_identity, position,
    # enabled, builtin_key для fields; id, operation_field_id, sheet,
    # column_key, marker, write_mode, position, builtin_key для columns —
    # ТОЧНО ті самі позиції колонок, що вже повертали self.store.list_
    # operations/get_operation/list_operation_fields/... — тож решта коду
    # (розпаковка кортежів на місці виклику) лишається незмінною. Сервер уже
    # віддає рядки відсортованими (ORDER BY у list_all_operations_tree) —
    # фільтрація тут зберігає порядок, повторне сортування не потрібне.
    def _ops_list_operations(self, parent_action_code=None, include_disabled=False):
        tree = self._operations_tree_cache
        rows = list(tree["operations"]) if tree else []
        if parent_action_code is not None:
            rows = [row for row in rows if row[5] == parent_action_code]
        if not include_disabled:
            rows = [row for row in rows if row[8]]
        return rows

    def _ops_get_operation(self, operation_id):
        tree = self._operations_tree_cache
        if not tree:
            return None
        return next((row for row in tree["operations"] if row[0] == operation_id), None)

    def _ops_get_operation_by_code(self, code):
        tree = self._operations_tree_cache
        if not tree:
            return None
        return next((row for row in tree["operations"] if row[1] == code), None)

    def _ops_list_operation_fields(self, operation_id, include_disabled=False):
        tree = self._operations_tree_cache
        rows = [row for row in tree["fields"] if row[1] == operation_id] if tree else []
        if not include_disabled:
            rows = [row for row in rows if row[6]]
        return rows

    def _ops_get_operation_field(self, field_id):
        tree = self._operations_tree_cache
        if not tree:
            return None
        return next((row for row in tree["fields"] if row[0] == field_id), None)

    def _ops_list_operation_field_columns(self, operation_field_id):
        tree = self._operations_tree_cache
        if not tree:
            return []
        return [row for row in tree["columns"] if row[1] == operation_field_id]

    # Крок "Дії" редизайн (2026-08-18): "заборони редагувати їх, і просто
    # зроби як доступні для огляду, щоб бачити що звідки йде" — CRUD-попапи
    # (додати/перейменувати/видалити категорію чи поле-запит, редагувати
    # прив'язку) прибрані з коду повністю (gui.py/warehouse_data.py/
    # webapp_server.py/remote_control_client.py), лишається лише перегляд.
    # Замість дерева з попапами — плаский реєстр-таблиця (Дія | Поле | Куди
    # пише | Знак): кожен рядок — ОДНА реальна прив'язка bot_operation_
    # field_columns, той самий факт, що раніше показувався всередині попапу
    # дії, тепер видно одразу, без відкриття/порівняння кількох вікон.
    def _build_actions_view(self, parent):
        top_note = tk.Label(
            parent,
            text=self._t(
                "Реєстр усіх дій бота: кожен рядок — одне поле-запит і колонка таблиці, "
                "куди воно записує значення. Лише перегляд."
            ),
            wraplength=640,
            justify="left",
        )
        top_note.pack(anchor="w", padx=16, pady=(16, 8))

        search_row = tk.Frame(parent)
        search_row.pack(fill="x", padx=16, pady=(0, 8))
        tk.Label(search_row, text=self._t("Пошук:")).pack(side="left")
        self._actions_search_var = tk.StringVar()
        tk.Entry(search_row, textvariable=self._actions_search_var).pack(
            side="left", fill="x", expand=True, padx=(6, 0)
        )
        self._actions_search_var.trace_add("write", lambda *_args: self._render_actions_registry_rows())

        self.actions_list_frame = self._create_scrollable_list(parent)
        self._refresh_actions_view()

    def _actions_columns_by_source(self):
        warehouse_headers = self.store.get_headers("СКЛАД")
        sales_headers = self.store.get_headers(SALES_SHEET_NAME)
        antiseptic_headers = self.store.get_headers(ANTISEPTIC_SHEET_NAME)
        return {
            "warehouse": (warehouse_headers, warehouse_columns(warehouse_headers) if warehouse_headers else {}),
            "sales": (sales_headers, sales_columns(sales_headers) if sales_headers else {}),
            "antiseptic": (antiseptic_headers, antiseptic_columns(antiseptic_headers) if antiseptic_headers else {}),
        }

    # Крок "Дії" remote-sync (2026-08-18, аудит "у всього є істина?"),
    # адаптовано під редизайн на реєстр-таблицю: той самий фон-потік +
    # generation-guard + "Завантаження..." патерн, що й раніше — НЕЗАЛЕЖНИЙ
    # від _refresh_custom_buttons (окремий, власний запит; обидва можуть
    # виконуватись одночасно, кожен зі своїм generation-лічильником, як і
    # Персонал/Способи оплати одне від одного). Тепер тягне лише operations_
    # tree (custom_menu_buttons тут більше не потрібні — реєстр будується
    # напряму з bot_operations/fields/columns, не з дерева кнопок).
    def _refresh_actions_view(self):
        if not hasattr(self, "actions_list_frame"):
            return
        self._clear_frame(self.actions_list_frame)
        tk.Label(self.actions_list_frame, text=self._t("Завантаження..."), anchor="w").pack(
            anchor="w", fill="x", pady=4
        )
        self._apply_theme(self.actions_list_frame)

        self._actions_view_refresh_generation += 1
        generation = self._actions_view_refresh_generation

        def worker():
            tree = remote_control_client.fetch_remote_operations_tree()
            self._run_on_main_thread(lambda: self._apply_actions_view_data(tree, generation))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_actions_view_data(self, tree, generation=None):
        if not hasattr(self, "actions_list_frame"):
            return
        if generation is not None and generation != self._actions_view_refresh_generation:
            return
        self._operations_tree_cache = tree
        self._render_actions_registry_rows()

    # Плаский реєстр (Дія | Поле | Куди пише | Знак) — одна прив'язка
    # bot_operation_field_columns на рядок, зібрана НАПРЯМУ з self._
    # operations_tree_cache (без фільтрації write_mode='generic'/'ledger':
    # старий _binding_is_editable ховав ledger-прив'язки, бо цей редактор
    # не міг їх змінити — тут мета інша, показати ПОВНУ правду про те, що
    # реально записує кожна дія, тож ledger-рядки теж входять). Ідентичність-
    # поля (Товар/Порода/...) природно не дають жодного рядка — сідінг
    # (_seed_warehouse_identity_fields) ніколи не створює для них bot_
    # operation_field_columns, спеціальна фільтрація тут не потрібна.
    def _actions_registry_rows(self):
        tree = self._operations_tree_cache
        if not tree:
            return None
        columns_by_source = self._actions_columns_by_source()
        fields_by_operation = {}
        for field in tree["fields"]:
            fields_by_operation.setdefault(field[1], []).append(field)
        columns_by_field = {}
        for column in tree["columns"]:
            columns_by_field.setdefault(column[1], []).append(column)

        rows = []
        for operation in tree["operations"]:
            operation_id, _code, _kind, _requires_identity, operation_label, _parent, _prefill, _position, enabled, _builtin = operation
            if not enabled:
                continue
            for field in fields_by_operation.get(operation_id, []):
                field_id, _op_id, _field_key, field_label, _is_identity, _position2, field_enabled, _builtin2 = field
                if not field_enabled:
                    continue
                for column in columns_by_field.get(field_id, []):
                    _column_id, _field_id2, sheet, column_key, marker, _write_mode, _position3, _builtin3 = column
                    headers, resolved_columns = columns_by_source.get(self._SHEET_TO_SOURCE.get(sheet), ([], {}))
                    index = resolved_columns.get(column_key)
                    column_name = headers[index] if index is not None and index < len(headers) else None
                    destination = (
                        self._t("{sheet} → «{column}»").format(sheet=sheet, column=column_name)
                        if column_name
                        else self._t("{sheet} → колонка не знайдена").format(sheet=sheet)
                    )
                    rows.append((operation_label, field_label, destination, marker))
        return rows

    def _render_actions_registry_rows(self):
        if not hasattr(self, "actions_list_frame"):
            return
        self._clear_frame(self.actions_list_frame)
        rows = self._actions_registry_rows()
        if rows is None:
            tk.Label(
                self.actions_list_frame,
                text=self._t("Не удалось получить реестр действий — нет связи с client_app.py."),
                anchor="w", fg="#d1242f",
            ).pack(anchor="w", fill="x", pady=4)
            self._apply_theme(self.actions_list_frame)
            return

        query = self._actions_search_var.get().strip().lower() if hasattr(self, "_actions_search_var") else ""
        if query:
            rows = [
                row for row in rows
                if query in row[0].lower() or query in row[1].lower() or query in row[2].lower()
            ]

        if not rows:
            tk.Label(
                self.actions_list_frame, text=self._t("Нічого не знайдено."), anchor="w", fg="#57606a",
            ).pack(anchor="w", fill="x", pady=4)
            self._apply_theme(self.actions_list_frame)
            return

        theme = self._theme()
        self.actions_list_frame.grid_columnconfigure(0, weight=1)
        self.actions_list_frame.grid_columnconfigure(1, weight=1)
        self.actions_list_frame.grid_columnconfigure(2, weight=2)
        self.actions_list_frame.grid_columnconfigure(3, weight=0)

        headers = (self._t("Дія"), self._t("Поле"), self._t("Куди пише"), self._t("Знак"))
        for col, header_text in enumerate(headers):
            tk.Label(
                self.actions_list_frame, text=header_text, font=("Segoe UI", 8, "bold"),
                fg="#8c959f", bg=theme["panel_bg"], anchor="w" if col < 3 else "center",
            ).grid(row=0, column=col, sticky="w", padx=(6 if col == 0 else 8, 0), pady=(0, 6))

        for index, (action_label, field_label, destination, marker) in enumerate(rows, start=1):
            marker_symbol, marker_color = self._ACTION_FIELD_MARKERS.get(marker, ("•", "#57606a"))
            tk.Label(self.actions_list_frame, text=action_label, anchor="w", bg=theme["panel_bg"]).grid(
                row=index, column=0, sticky="ew", padx=(6, 0), pady=3
            )
            tk.Label(self.actions_list_frame, text=field_label, anchor="w", bg=theme["panel_bg"]).grid(
                row=index, column=1, sticky="ew", padx=8, pady=3
            )
            tk.Label(self.actions_list_frame, text=destination, anchor="w", bg=theme["panel_bg"]).grid(
                row=index, column=2, sticky="ew", padx=8, pady=3
            )
            tk.Label(
                self.actions_list_frame, text=marker_symbol, fg=marker_color, font=("Segoe UI", 10, "bold"),
                bg=theme["panel_bg"], anchor="center",
            ).grid(row=index, column=3, padx=(8, 6), pady=3)
        self._apply_theme(self.actions_list_frame)

    # Задача користувача (2026-08-17): "редактор кнопок зроби синхронним" -
    # той самий фон-потік-fetch + generation-guard патерн, що вже й
    # _refresh_personnel (self.store тут - ВЛАСНА, окрема й порожня локальна
    # база gui.py, ніяк не пов'язана з реальним ботом; тепер тягнеться живе
    # дерево напряму з client_app.py через remote_control_client.
    # fetch_remote_custom_buttons).
    def _refresh_custom_buttons(self):
        self._clear_frame(self.custom_buttons_list_frame)
        tk.Label(self.custom_buttons_list_frame, text=self._t("Завантаження..."), anchor="w").pack(
            anchor="w", fill="x", pady=4
        )
        self._apply_theme(self.custom_buttons_list_frame)

        self._custom_buttons_refresh_generation += 1
        generation = self._custom_buttons_refresh_generation

        def worker():
            rows = remote_control_client.fetch_remote_custom_buttons()
            self._run_on_main_thread(lambda: self._apply_custom_buttons_rows(rows, generation))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_custom_buttons_rows(self, rows, generation=None):
        if getattr(self, "custom_buttons_list_frame", None) is None:
            return
        if generation is not None and generation != self._custom_buttons_refresh_generation:
            return
        self._custom_buttons_cache = rows
        self._render_custom_buttons_tree()

    # rows у кеші - 9-елементні (id, parent_id, label, message_text,
    # action_code, section, enabled, layout, operation_id), як їх віддає
    # list_all_custom_buttons(). Рендер-код нижче (_render_custom_button_
    # row/_half_pair_sides) написаний під СТАРУ 8-елементну форму без
    # parent_id (та, що раніше повертав self.store.list_custom_buttons(
    # parent_id, ...)) - _custom_buttons_children конвертує, щоб не
    # переписувати сам рендер.
    def _custom_buttons_children(self, parent_id):
        if not self._custom_buttons_cache:
            return []
        return [
            (row[0], row[2], row[3], row[4], row[5], row[6], row[7], row[8])
            for row in self._custom_buttons_cache
            if row[1] == parent_id
        ]

    def _custom_button_by_id(self, node_id):
        if not self._custom_buttons_cache:
            return None
        for row in self._custom_buttons_cache:
            if row[0] == node_id:
                return row
        return None

    # Той самий обхід у ширину, що раніше й self.store.count_custom_button_
    # descendants (warehouse_data.py) робив прямо в SQLite - тут над уже
    # завантаженим кешем, не окремим запитом.
    def _custom_button_descendant_count(self, node_id):
        total = 0
        frontier = [node_id]
        while frontier:
            next_frontier = []
            for current_id in frontier:
                next_frontier.extend(row[0] for row in self._custom_buttons_children(current_id))
            total += len(next_frontier)
            frontier = next_frontier
        return total

    def _render_custom_buttons_tree(self):
        self._clear_frame(self.custom_buttons_list_frame)
        if self._custom_buttons_cache is None:
            tk.Label(
                self.custom_buttons_list_frame,
                text=self._t("Не вдалось отримати кнопки з client_app.py. Перевірте з'єднання."),
                fg="#d1242f",
                anchor="w",
                wraplength=460,
                justify="left",
            ).pack(anchor="w", fill="x", pady=4)
            self._apply_theme(self.custom_buttons_list_frame)
            self._refresh_custom_button_preview()
            return
        roots = self._custom_buttons_children(None)
        if not roots:
            tk.Label(self.custom_buttons_list_frame, text=self._t("Кнопок поки немає.")).pack(anchor="w", pady=4)
        else:
            root_sides = self._half_pair_sides(roots)
            for row in roots:
                self._render_custom_button_row(row, depth=0, side=root_sides.get(row[0]))
        self._apply_theme(self.custom_buttons_list_frame)
        self._refresh_custom_button_preview()

    # Ліво/Право для layout="half" рядків — та сама пара сусідів за
    # позицією, яку реально збирає в один рядок клавіатури
    # _pack_custom_button_rows (telegram_dialog.py); лише enabled рядки
    # беруть участь (вимкнені в живому боті взагалі не пакуються, тож і тут
    # не отримують мітки — інакше підпис міг би збрехати про те, з ким
    # рядок насправді парується наживо).
    def _half_pair_sides(self, rows):
        sides = {}
        pending_id = None
        for row in rows:
            node_id, enabled, layout = row[0], row[5], row[6]
            if not enabled:
                continue
            if layout == "half":
                if pending_id is not None:
                    sides[pending_id] = self._t("ліво")
                    sides[node_id] = self._t("право")
                    pending_id = None
                else:
                    pending_id = node_id
            else:
                pending_id = None
        return sides

    def _render_custom_button_row(self, row, depth, side=None):
        node_id, label, message_text, action_code, section, enabled, layout, operation_id = row
        is_selected = node_id == self.custom_buttons_selected_id
        bg = "#d0e8ff" if is_selected else self.custom_buttons_list_frame.cget("bg")

        row_frame = tk.Frame(self.custom_buttons_list_frame, bg=bg)
        row_frame.pack(fill="x", pady=1, padx=(depth * 24, 0))

        display_label = label + (f" ({side})" if side else "")
        tk.Button(
            row_frame, text=display_label, anchor="center", bg=bg, font=("Segoe UI", 9),
            width=24,
            command=lambda nid=node_id: self.select_custom_button(nid),
        ).pack(side="left")

        # Задача користувача: "іконки замість тексту" (обраний варіант A) -
        # ✕/✎/+ замість "x"/"ред"/"+", той самий 3-кнопковий набір.
        tk.Button(
            row_frame, text="✕", width=3, fg="#d1242f",
            command=lambda nid=node_id, lbl=label: self.delete_custom_button_confirm(nid, lbl),
            **self._chip_button_style(),
        ).pack(side="right")
        tk.Button(
            row_frame, text="✎", width=3, fg=self._chip_text_color(),
            command=lambda nid=node_id: self.edit_custom_button_dialog(nid),
            **self._chip_button_style(),
        ).pack(side="right")
        tk.Button(
            row_frame, text="+", width=3, fg="#1a7f37",
            command=lambda nid=node_id: self.add_custom_button_dialog(nid),
            **self._chip_button_style(),
        ).pack(side="right")

        child_rows = self._custom_buttons_children(node_id)
        child_sides = self._half_pair_sides(child_rows)
        for child_row in child_rows:
            self._render_custom_button_row(child_row, depth=depth + 1, side=child_sides.get(child_row[0]))

    def select_custom_button(self, node_id):
        self.custom_buttons_selected_id = node_id
        # Лише перемальовує з уже завантаженого кешу (не мережевий похід
        # через тунель на кожен клік вибору рядка) - _refresh_custom_buttons
        # (реальний fetch) викликається лише при відкритті екрана й після
        # add/edit/delete.
        self._render_custom_buttons_tree()

    # Список позицій для випадаючого списку у формі (Задача користувача:
    # обрати слот номером замість стрілочок ↑/↓). exclude_node_id — при
    # редагуванні кнопка не рахує сама себе серед "братів" (інакше N було б
    # на один більше, ніж реальних вільних слотів). Індекс — 0-based, як і
    # очікує store.set_custom_button_position.
    def _custom_button_position_options(self, parent_id, exclude_node_id=None):
        siblings = self._custom_buttons_children(parent_id)
        ids_in_order = [row[0] for row in siblings]
        if exclude_node_id in ids_in_order:
            ids_in_order.remove(exclude_node_id)
        return [str(i) for i in range(1, len(ids_in_order) + 2)]

    def _refresh_custom_button_preview(self):
        self._clear_frame(self.custom_button_preview_frame)
        node_id = self.custom_buttons_selected_id
        row = self._custom_button_by_id(node_id) if node_id else None
        if not row:
            tk.Label(
                self.custom_button_preview_frame,
                text=self._t("Оберіть кнопку зліва."),
                wraplength=220,
                justify="left",
            ).pack(anchor="w")
            return

        _id, _parent_id, label, message_text, action_code, section, enabled, layout, operation_id = row
        tk.Label(
            self.custom_button_preview_frame,
            text=label,
            font=("Segoe UI", 10, "bold"),
            wraplength=220,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))
        tk.Label(
            self.custom_button_preview_frame,
            text=self._t(self._CUSTOM_BUTTON_LAYOUT_LABELS.get(layout, layout)),
            wraplength=220,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))
        tk.Label(
            self.custom_button_preview_frame,
            text=message_text or self._t("(без повідомлення)"),
            wraplength=220,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))

        tk.Label(self.custom_button_preview_frame, text=self._t("Далі:"), font=("Segoe UI", 9, "bold")).pack(anchor="w")
        children = self._custom_buttons_children(_id)
        if children:
            for _child_id, child_label, *_rest in children:
                tk.Label(
                    self.custom_button_preview_frame,
                    text=f"• {child_label}",
                    wraplength=220,
                    justify="left",
                ).pack(anchor="w")
        elif operation_id is not None:
            tk.Label(
                self.custom_button_preview_frame,
                text=self._t("Пряме посилання: {value}").format(value=self._operation_link_id_to_label(operation_id)),
                wraplength=220,
                justify="left",
            ).pack(anchor="w")
        elif action_code:
            action_label = next(
                (action["label"] for action in CUSTOM_BUTTON_ACTIONS if action["code"] == action_code),
                action_code,
            )
            tk.Label(
                self.custom_button_preview_frame,
                text=self._t("Дія: {value}").format(value=action_label),
                wraplength=220,
                justify="left",
            ).pack(anchor="w")
        else:
            tk.Label(self.custom_button_preview_frame, text=self._t("(немає дії)")).pack(anchor="w")

    # Одне спливаюче вікно на 3 поля (назва / текст відповіді бота / дія —
    # випадаючий список) + Зберегти/Відмінити. Той самий Toplevel для
    # додавання (initial_* порожні) і редагування (initial_* — поточні
    # значення). Повертає {"label", "message_text", "action_code"} або
    # None, якщо натиснуто "Відмінити"/Escape/закрито хрестиком.
    _NO_ACTION_LABEL = "Без дії — лише повідомлення"

    def _custom_button_action_options(self):
        return [self._t(self._NO_ACTION_LABEL)] + [action["label"] for action in CUSTOM_BUTTON_ACTIONS]

    def _custom_button_action_code_to_label(self, action_code):
        for action in CUSTOM_BUTTON_ACTIONS:
            if action["code"] == action_code:
                return action["label"]
        return self._t(self._NO_ACTION_LABEL)

    def _custom_button_action_label_to_code(self, label):
        for action in CUSTOM_BUTTON_ACTIONS:
            if action["label"] == label:
                return action["code"]
        return None

    # Крок 4.3: "Пряме посилання на дію з 'Дії'" — окремий каталог, ПОВЕРХ
    # 6 стандартних дій вище (action_code): не флоу, а КОНКРЕТНИЙ рядок
    # bot_operations (ДОСКА AD/KD/ОСБ/ВАГОНКА/Антисептирование/звіти).
    # Мітка може повторюватись між приходом і продажем ("ДОСКА AD" є в
    # обох) — тому в списку завжди додається розділ, щоб не переплутати.
    _OPERATION_LINK_SECTION_LABELS = {
        "start_income": "Приход",
        "start_sale": "Реализация",
        "start_stock_report": "Склад (звіт)",
        "start_sales_report": "Продажи (звіт)",
    }
    _NO_OPERATION_LINK_LABEL = "Без прямого посилання"

    def _operation_link_catalog(self):
        catalog = []
        for operation in self._ops_list_operations():
            operation_id, _code, _kind, _requires_identity, op_label, parent_action_code, *_rest = operation
            section_label = self._t(self._OPERATION_LINK_SECTION_LABELS.get(parent_action_code, parent_action_code))
            catalog.append((operation_id, f"{op_label} — {section_label}"))
        return catalog

    def _operation_link_options(self):
        return [self._t(self._NO_OPERATION_LINK_LABEL)] + [display for _id, display in self._operation_link_catalog()]

    def _operation_link_id_to_label(self, operation_id):
        if operation_id is not None:
            for op_id, display in self._operation_link_catalog():
                if op_id == operation_id:
                    return display
        return self._t(self._NO_OPERATION_LINK_LABEL)

    def _operation_link_label_to_id(self, label):
        for op_id, display in self._operation_link_catalog():
            if display == label:
                return op_id
        return None

    # Розмір кнопки в клавіатурі бота (Задача користувача): суцільна (на весь
    # рядок) або вдвічі менша — половинна кнопка спарюється з СУСІДНЬОЮ (за
    # позицією) половинною кнопкою в один рядок клавіатури; немає окремого
    # вибору "боку" — зліва/справа визначає сама позиція (менший індекс —
    # зліва), яку й так задає поле "Позиція" вище (телеграм-бот,
    # _pack_custom_button_rows у telegram_dialog.py).
    _CUSTOM_BUTTON_LAYOUT_LABELS = {
        "full": "Розмір: одна суцільна (на весь рядок)",
        "half": "Розмір: вдвічі менша (парується із сусідньою за позицією)",
    }
    _CUSTOM_BUTTON_LAYOUT_OPTIONS = ["full", "half"]

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
        window = tk.Toplevel(self.root)
        window.title(self._t(title))
        window.transient(self.root)
        window.grab_set()
        window.resizable(False, False)

        form = tk.Frame(window)
        form.pack(padx=16, pady=16, fill="both", expand=True)

        tk.Label(form, text=self._t("Назва кнопки:")).pack(anchor="w")
        label_entry = tk.Entry(form, width=44)
        label_entry.insert(0, initial_label)
        label_entry.pack(anchor="w", pady=(2, 12))
        label_entry.focus_set()

        tk.Label(form, text=self._t("Що бот відповідає при натисканні:")).pack(anchor="w")
        message_text_widget = tk.Text(form, width=44, height=5, wrap="word")
        message_text_widget.insert("1.0", initial_message or "")
        message_text_widget.pack(anchor="w", pady=(2, 4))
        tk.Label(
            form,
            text=self._t("Для стандартних дій (Приход, Реализация, Склад, Продажи,\n"
                 "Калькулятор, Довідка) цей текст ігнорується — його\n"
                 "редагування знаходиться у вкладці «Дії»."),
            justify="left",
            fg="#666666",
            font=("Segoe UI", 8),
        ).pack(anchor="w", pady=(0, 12))

        # Крок 4.3: два взаємовиключні призначення кнопки — обираються
        # радіо-перемикачем, не мовчазним пріоритетом (Задача користувача,
        # план "не обидва одночасно"). "Стандартна дія" — той самий
        # 6-пунктовий каталог, що й раніше (CUSTOM_BUTTON_ACTIONS, включно
        # з "Без дії"). "Пряме посилання" — НОВЕ: конкретний рядок
        # bot_operations (ДОСКА AD і т.д.) — вузол одразу запускає САМЕ цю
        # дію, в обхід action_code+_category_from_text.
        assignment_var = tk.StringVar(value="operation" if initial_operation_id is not None else "action")

        tk.Label(form, text=self._t("Призначення кнопки:")).pack(anchor="w")
        tk.Radiobutton(
            form, text=self._t("Стандартна дія:"), variable=assignment_var, value="action",
            command=lambda: update_combo_states(),
        ).pack(anchor="w")
        action_var = tk.StringVar(value=self._custom_button_action_code_to_label(initial_action_code))
        action_combo = ttk.Combobox(
            form,
            textvariable=action_var,
            values=self._custom_button_action_options(),
            state="readonly",
            width=38,
        )
        action_combo.pack(anchor="w", padx=(20, 0), pady=(2, 10))

        tk.Radiobutton(
            form, text=self._t("Пряме посилання на дію з «Дії»:"), variable=assignment_var, value="operation",
            command=lambda: update_combo_states(),
        ).pack(anchor="w")
        operation_var = tk.StringVar(value=self._operation_link_id_to_label(initial_operation_id))
        operation_combo = ttk.Combobox(
            form,
            textvariable=operation_var,
            values=self._operation_link_options(),
            state="readonly",
            width=38,
        )
        operation_combo.pack(anchor="w", padx=(20, 0), pady=(2, 16))

        def update_combo_states():
            mode = assignment_var.get()
            action_combo.configure(state="readonly" if mode == "action" else "disabled")
            operation_combo.configure(state="readonly" if mode == "operation" else "disabled")

        update_combo_states()

        tk.Label(form, text=self._t("Позиція (номер серед сусідніх кнопок):")).pack(anchor="w")
        position_var = tk.StringVar(value=initial_position)
        position_combo = ttk.Combobox(
            form,
            textvariable=position_var,
            values=position_options,
            state="readonly",
            width=10,
        )
        position_combo.pack(anchor="w", pady=(2, 16))

        tk.Label(form, text=self._t("Розмір кнопки:")).pack(anchor="w")
        layout_var = tk.StringVar(value=initial_layout or "full")
        tk.Radiobutton(
            form, text=self._t("Одна суцільна (на весь рядок)"), variable=layout_var, value="full",
        ).pack(anchor="w")
        tk.Radiobutton(
            form, text=self._t("Вдвічі менша (парується із сусідньою за позицією)"), variable=layout_var, value="half",
        ).pack(anchor="w", pady=(0, 16))

        button_row = tk.Frame(form)
        button_row.pack(anchor="e", fill="x")

        def save():
            label = label_entry.get().strip()
            if not label:
                messagebox.showerror(title, self._t("Назва кнопки не може бути порожньою."))
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

        tk.Button(button_row, text=self._t("Відмінити"), width=14, command=cancel).pack(side="right", padx=(8, 0))
        tk.Button(button_row, text=self._t("Зберегти зміни"), width=16, command=save).pack(side="right")

        window.bind("<Escape>", lambda event: cancel())
        window.protocol("WM_DELETE_WINDOW", cancel)
        self._center_window(window, width=420, height=640)
        self.root.wait_window(window)
        return result["value"]

    # Задача користувача (2026-08-17): "редактор кнопок зроби синхронним" -
    # фон-потік + HTTPError-detail-парсинг, той самий патерн, що вже й
    # _on_role_menu_selected (webapp_server вже повертає зрозумілий текст
    # у JSON-тілі помилки - "Название совпадает..." тощо - без цього
    # str(exc) на HTTPError дав би лише голе "HTTP Error 409: Conflict").
    # Спільна для add/edit/delete - action() лише виконує саму
    # remote_control_client-функцію, тут - лише мережа/помилки/оновлення
    # екрана.
    def _push_custom_button_action(self, action, on_success=None):
        def worker():
            error = None
            try:
                action()
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                try:
                    detail = json.loads(detail).get("error") or detail
                except ValueError:
                    pass
                error = detail
            except Exception as exc:
                error = str(exc)

            def finish():
                if error:
                    messagebox.showerror(self._t("Редактор кнопок"), error)
                    return
                if on_success:
                    on_success()
                self._refresh_custom_buttons()

            self._run_on_main_thread(finish)

        threading.Thread(target=worker, daemon=True).start()

    def add_custom_button_dialog(self, parent_id=None):
        position_options = self._custom_button_position_options(parent_id)
        form = self._ask_custom_button_form(
            "Нова кнопка", position_options=position_options, initial_position=position_options[-1],
        )
        if not form:
            return
        # Перевірка збігу назви тепер лише на сервері (client_app.py) -
        # локальний self.store тут все одно порожній/сторонній, повторювати
        # перевірку на ньому було б безглуздо; помилку 409 показує
        # _push_custom_button_action.
        self._push_custom_button_action(lambda: remote_control_client.add_remote_custom_button(
            form["label"], form["message_text"], form["action_code"], parent_id=parent_id,
            layout=form["layout"], operation_id=form["operation_id"], position_index=form["position_index"],
        ))

    def edit_custom_button_dialog(self, node_id):
        row = self._custom_button_by_id(node_id)
        if not row:
            return
        _id, parent_id, label, message_text, action_code, section, enabled, layout, operation_id = row

        siblings = self._custom_buttons_children(parent_id)
        ids_in_order = [sibling_row[0] for sibling_row in siblings]
        current_index = ids_in_order.index(node_id) if node_id in ids_in_order else len(ids_in_order) - 1
        position_options = self._custom_button_position_options(parent_id, exclude_node_id=node_id)

        form = self._ask_custom_button_form(
            "Редагувати кнопку",
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
        self._push_custom_button_action(lambda: remote_control_client.update_remote_custom_button(
            node_id, form["label"], form["message_text"], form["action_code"], layout=form["layout"],
            operation_id=form["operation_id"], position_index=form["position_index"],
        ))

    # Задача користувача: підтвердження при видаленні — ЗАВЖДИ, і явне
    # попередження, якщо разом з кнопкою видалиться ціла гілка нащадків.
    # Кількість нащадків тепер рахується з уже завантаженого кешу (BFS у
    # Python, як і раніше рахував сам self.store.count_custom_button_
    # descendants) - жодного окремого мережевого запиту заради самого лише
    # попередження.
    def delete_custom_button_confirm(self, node_id, label):
        descendant_count = self._custom_button_descendant_count(node_id)
        if descendant_count > 0:
            confirmed = messagebox.askyesno(
                self._t("Видалити кнопку"),
                self._t(
                    'Кнопка "{label}" має дочірні кнопки — разом з нею видаляться ще {count} '
                    "дочірніх кнопок (уся гілка). Продовжити?"
                ).format(label=label, count=descendant_count),
            )
        else:
            confirmed = messagebox.askyesno(self._t("Видалити кнопку"), self._t('Видалити кнопку "{value}"?').format(value=label))
        if not confirmed:
            return

        def clear_selection_if_needed():
            if self.custom_buttons_selected_id == node_id:
                self.custom_buttons_selected_id = None

        self._push_custom_button_action(
            lambda: remote_control_client.delete_remote_custom_button(node_id),
            on_success=clear_selection_if_needed,
        )

    def _build_commands_view(self):
        self.commands_frame = tk.Frame(self.root)

        top_bar = tk.Frame(self.commands_frame)
        top_bar.pack(side="top", fill="x", padx=8, pady=6)

        back_button = tk.Button(top_bar, text=self._t("← Назад"), command=self.show_settings)
        back_button.pack(side="left")

        title = tk.Label(top_bar, text=self._t("Команди"), font=("Segoe UI", 12, "bold"))
        title.pack(side="left", padx=12)

        content = tk.Frame(self.commands_frame)
        content.pack(side="top", fill="both", expand=True, padx=40, pady=30)

        self.commands_list_frame = self._create_scrollable_list(content)

        self.add_command_button = tk.Button(
            content, text=self._t("+"), width=4, command=self.add_command_dialog, fg="#1a7f37", **self._chip_button_style(),
        )
        self.add_command_button.pack(anchor="w", pady=(12, 0))

    def _build_personnel_view(self, window):
        top_bar = tk.Frame(window)
        top_bar.pack(side="top", fill="x", padx=8, pady=6)

        close_button = tk.Button(
            top_bar, text=self._t("Закрити"),
            command=lambda: self._close_personnel_window(self.personnel_window),
        )
        close_button.pack(side="left")

        title = tk.Label(top_bar, text=self._t("Персонал"), font=("Segoe UI", 12, "bold"))
        title.pack(side="left", padx=12)

        # Задача користувача: "і кнопку оновити. буде оновлювати дані." -
        # автореєстрація гостей і час останнього повідомлення міняються
        # ботом у фоні, поки цей екран відкритий, тож потрібен ручний
        # спосіб перечитати актуальні дані без виходу з екрана.
        refresh_button = tk.Button(top_bar, text=self._t("Обновити"), command=self._refresh_personnel)
        refresh_button.pack(side="left", padx=8)

        # Задача користувача (2026-08-15): "синхронізація" - список тепер
        # ЛИШЕ показує реальний персонал client_app.py (де реально живе
        # бот) через тунель - додавання/редагування/видалення переїхало
        # туди ж, де це реально впливає на живий бот. Кнопки "+"/"ред"/"x"
        # тут навмисно прибрані (не приховані - видалені), а не залишені
        # непрацюючими проти власної порожньої локальної бази.
        tk.Label(
            window,
            text=self._t(
                "Перегляд лише для читання - дані тягнуться напряму з client_app.py. "
                "Додавання/редагування персоналу - там же, де реально працює бот."
            ),
            fg="#666666", wraplength=640, justify="left",
        ).pack(anchor="w", padx=40, pady=(0, 8))

        content = tk.Frame(window)
        content.pack(side="top", fill="both", expand=True, padx=40, pady=(0, 30))

        self.personnel_list_frame = self._create_scrollable_list(content)

    # Крок 4.4 "Дії": реальний CRUD над способами оплати (раніше — жорсткий
    # 3-елементний PAYMENT_METHODS). Той самий екран-патерн, що й "Команди".
    def _build_payment_methods_view(self):
        self.payment_methods_frame = tk.Frame(self.root)

        top_bar = tk.Frame(self.payment_methods_frame)
        top_bar.pack(side="top", fill="x", padx=8, pady=6)

        back_button = tk.Button(top_bar, text=self._t("← Назад"), command=self.show_settings)
        back_button.pack(side="left")

        title = tk.Label(top_bar, text=self._t("Способи оплати"), font=("Segoe UI", 12, "bold"))
        title.pack(side="left", padx=12)

        content = tk.Frame(self.payment_methods_frame)
        content.pack(side="top", fill="both", expand=True, padx=40, pady=30)

        tk.Label(
            content,
            text=(
                self._t("Перейменування зберігає стару назву — бот і далі розпізнає її "
                "в тексті клієнта. Видалення прибирає варіант повністю.")
            ),
            anchor="w",
            justify="left",
            fg="#57606a",
            wraplength=560,
        ).pack(anchor="w", pady=(0, 10))

        self.payment_methods_list_frame = self._create_scrollable_list(content)

        self.add_payment_method_button = tk.Button(
            content, text=self._t("+"), width=4, command=self.add_payment_method_dialog,
            fg="#1a7f37", **self._chip_button_style(),
        )
        self.add_payment_method_button.pack(anchor="w", pady=(12, 0))

    # Задача користувача (2026-08-18, аудит "у всього є істина?"): "Способи
    # оплати" редагувались у ВЛАСНІЙ локальній self.store gui.py - той
    # самий мертвий-запис клас багу, що вже виправлено для дерева кнопок.
    # Той самий фон-потік + generation-guard + "Завантаження...", що вже й
    # _refresh_personnel вище - реальний живий стан client_app.py, не
    # локальна (і давно нерелевантна) копія.
    def _refresh_payment_methods(self):
        self._clear_frame(self.payment_methods_list_frame)
        tk.Label(self.payment_methods_list_frame, text=self._t("Завантаження..."), anchor="w").pack(
            anchor="w", fill="x", pady=4
        )
        self._apply_theme(self.payment_methods_list_frame)

        self._payment_methods_refresh_generation += 1
        generation = self._payment_methods_refresh_generation

        def worker():
            options = remote_control_client.fetch_remote_payment_methods()
            self._run_on_main_thread(lambda: self._apply_payment_method_rows(options, generation))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_payment_method_rows(self, options, generation=None):
        if generation is not None and generation != self._payment_methods_refresh_generation:
            return
        self._clear_frame(self.payment_methods_list_frame)
        if options is None:
            tk.Label(
                self.payment_methods_list_frame,
                text=self._t("Не удалось получить способы оплаты — нет связи с client_app.py."),
                anchor="w", fg="#d1242f",
            ).pack(anchor="w", fill="x", pady=4)
            self._apply_theme(self.payment_methods_list_frame)
            return
        if not options:
            tk.Label(
                self.payment_methods_list_frame,
                text=self._t("Способів оплати поки немає."),
                anchor="w",
            ).pack(anchor="w", fill="x", pady=4)
            return

        for index, (option_id, label, kind) in enumerate(options, start=1):
            row = tk.Frame(self.payment_methods_list_frame)
            row.pack(anchor="w", fill="x", pady=4)

            display_label = f"{index}. {label}" + (self._t(" (банківський)") if kind == "bank" else "")
            tk.Label(row, text=display_label, anchor="w", justify="left").pack(
                side="left", fill="x", expand=True
            )

            tk.Button(
                row, text=self._t("x"), width=3, fg="#d1242f",
                command=lambda item_id=option_id, item_label=label: self.delete_payment_method_dialog(
                    item_id, item_label
                ),
                **self._chip_button_style(),
            ).pack(side="right", padx=(8, 0))

            tk.Button(
                row, text=self._t("ред"), width=5, fg=self._chip_text_color(),
                command=lambda item_id=option_id, item_label=label: self.edit_payment_method_dialog(
                    item_id, item_label
                ),
                **self._chip_button_style(),
            ).pack(side="right", padx=(8, 0))

            # Задача аудиту: antiseptic-розподіл готівка/банк раніше звіряв
            # ЖОРСТКИЙ текст "ЕФАКТУРА Б/Н" - перейменування тихо ламало
            # облік. Тепер адмін сам позначає РІВНО один варіант як банк
            # (перемикач знімає позначку з інших автоматично).
            bank_toggle_text = self._t("Зняти «банк»") if kind == "bank" else self._t("Це банк")
            tk.Button(
                row, text=bank_toggle_text, fg=self._chip_text_color(),
                command=lambda item_id=option_id, was_bank=(kind == "bank"): self._toggle_payment_method_bank(
                    item_id, was_bank
                ),
                **self._chip_button_style(),
            ).pack(side="right", padx=(8, 0))

    # Той самий _push_custom_button_action-патерн (фон-потік, HTTPError->
    # JSON "error", messagebox при помилці, оновлення списку при успіху) -
    # лише інший ендпоінт і власний refresh.
    def _push_payment_method_action(self, action):
        def worker():
            error = None
            try:
                action()
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                try:
                    detail = json.loads(detail).get("error") or detail
                except ValueError:
                    pass
                error = detail
            except Exception as exc:
                error = str(exc)

            def finish():
                if error:
                    messagebox.showerror(self._t("Способи оплати"), error)
                    return
                self._refresh_payment_methods()

            self._run_on_main_thread(finish)

        threading.Thread(target=worker, daemon=True).start()

    def _toggle_payment_method_bank(self, option_id, was_bank):
        self._push_payment_method_action(
            lambda: remote_control_client.set_remote_payment_method_kind(option_id, None if was_bank else "bank")
        )

    def add_payment_method_dialog(self):
        label = simpledialog.askstring(self._t("Новий спосіб оплати"), self._t("Назва способу оплати:"))
        if not label:
            return
        label = label.strip()
        if not label:
            return
        # Перевірка збігу назви тепер лише на сервері (client_app.py) -
        # локальний self.store тут все одно порожній/сторонній.
        self._push_payment_method_action(lambda: remote_control_client.add_remote_payment_method(label))

    def edit_payment_method_dialog(self, option_id, current_label):
        new_label = simpledialog.askstring(
            self._t("Перейменувати спосіб оплати"), self._t("Нова назва:"), initialvalue=current_label
        )
        if not new_label:
            return
        new_label = new_label.strip()
        if not new_label:
            return
        self._push_payment_method_action(
            lambda: remote_control_client.update_remote_payment_method(option_id, new_label)
        )

    def delete_payment_method_dialog(self, option_id, label):
        # "Має лишитись хоча б один спосіб" — тепер теж перевіряється на
        # сервері (409), не окремим попереднім read-запитом звідси.
        if not messagebox.askyesno(
            self._t("Видалити спосіб оплати"),
            self._t(
                'Видалити "{value}"? Кнопка й розпізнавання в тексті клієнта зникнуть повністю '
                "(на відміну від перейменування)."
            ).format(value=label),
        ):
            return
        self._push_payment_method_action(lambda: remote_control_client.delete_remote_payment_method(option_id))

    # Задача користувача: "те віконце що скролиться якось виділи, щоб було
    # оку видно... не видно скільки там взагалі вікон" — bordered=True додає
    # видиму рамку навколо прокручуваної області (лише там, де явно
    # попросили — інші виклики цієї функції з-поза "Дії" не чіпаємо).
    def _create_scrollable_list(self, parent, bordered=False):
        container = tk.Frame(parent)
        container.pack(side="top", fill="both", expand=True)

        canvas = tk.Canvas(
            container,
            highlightthickness=2 if bordered else 0,
            highlightbackground="#8c959f",
        )
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        list_frame = tk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=list_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)

        def update_scroll_region(event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            content_height = list_frame.winfo_reqheight()
            canvas_height = canvas.winfo_height()
            if content_height > canvas_height:
                if not scrollbar.winfo_manager():
                    scrollbar.pack(side="right", fill="y")
            else:
                if scrollbar.winfo_manager():
                    scrollbar.pack_forget()

        def resize_window(event):
            canvas.itemconfigure(window_id, width=event.width)
            update_scroll_region()

        def on_mousewheel(event):
            if scrollbar.winfo_manager():
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def unbind_mousewheel(event=None):
            canvas.unbind_all("<MouseWheel>")

        list_frame.bind("<Configure>", update_scroll_region)
        canvas.bind("<Configure>", resize_window)
        canvas.bind("<Enter>", lambda event: canvas.bind_all("<MouseWheel>", on_mousewheel))
        canvas.bind("<Leave>", unbind_mousewheel)
        # Свіжий пере-аудит (New-Notable #4): Escape-закриття вікна без
        # попереднього виходу миші з canvas лишало глобальну прив'язку
        # живою, вказуючи на вже знищений canvas - наступний скрол
        # будь-де в програмі кидав TclError. <Destroy> спрацьовує на
        # будь-яке знищення canvas незалежно від причини (Escape, WM_
        # DELETE_WINDOW, прямий .destroy()).
        canvas.bind("<Destroy>", unbind_mousewheel)
        # Задача користувача (2026-08-19, "Історія"): "2й клік - залишає
        # місце зарезервованим під розгортання, але текст ховається" -
        # <Configure> на list_frame надійно спрацьовує, коли контент
        # ЗРОСТАЄ, але не завжди - коли ЗМЕНШУЄТЬСЯ (звичайні
        # dictdate/idletasks не допомагають, перевірено). Прикріплюємо
        # update_scroll_region як атрибут на самому list_frame - виклик
        # напряму (без event, як звичайна функція) для КОДУ, що явно
        # знищує власні дочірні віджети (напр. згортання рядка), не
        # чекаючи на Configure, який може не прийти.
        list_frame.refresh_scroll_region = update_scroll_region
        return list_frame

    def _clear_frame(self, frame):
        for child in frame.winfo_children():
            child.destroy()

    def _request_processing_mode_title(self, mode=None):
        mode = mode or self.settings.get("request_processing_mode")
        if mode == "not_selected":
            return "Не выбран"
        for code, title, _ in REQUEST_PROCESSING_MODES:
            if code == mode:
                return title
        return REQUEST_PROCESSING_MODES[0][1]

    def open_request_processing_mode_dialog(self):
        window = tk.Toplevel(self.root)
        window.title(self._t("Режим обработки запросов"))
        window.geometry("820x500")
        window.transient(self.root)
        window.grab_set()

        selected_mode = tk.StringVar(value=self.settings.get("request_processing_mode"))

        top = tk.Frame(window)
        top.pack(side="top", fill="x", padx=18, pady=(16, 8))
        tk.Label(
            top,
            text=self._t("Режим обработки запросов"),
            font=("Segoe UI", 13, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            top,
            text=(
                self._t("Выберите, кто будет понимать сообщения из Telegram. "
                "Если пользователь не разбирается в ШИ, самый безопасный вариант — Без ШИ.")
            ),
            anchor="w",
            justify="left",
            wraplength=760,
            fg="#555555",
        ).pack(anchor="w", pady=(4, 0))

        body = tk.Frame(window)
        body.pack(side="top", fill="both", expand=True, padx=18, pady=8)

        for code, title, description in REQUEST_PROCESSING_MODES:
            row = tk.Frame(body, bd=1, relief="groove", padx=14, pady=12)
            row.pack(fill="x", pady=7)

            radio = tk.Radiobutton(
                row,
                variable=selected_mode,
                value=code,
                text=title,
                anchor="w",
                width=18,
                font=("Segoe UI", 10, "bold"),
            )
            radio.pack(side="left", anchor="n")

            tk.Label(
                row,
                text=description,
                anchor="w",
                justify="left",
                wraplength=560,
                fg="#555555",
            ).pack(side="left", fill="x", expand=True, padx=(18, 0))

        bottom = tk.Frame(window)
        bottom.pack(side="bottom", fill="x", padx=18, pady=(8, 16))

        def save_mode():
            self.settings.set("request_processing_mode", selected_mode.get())
            window.destroy()

        tk.Button(bottom, text=self._t("Сохранить"), width=14, command=save_mode).pack(side="right", padx=(8, 0))
        tk.Button(bottom, text=self._t("Отмена"), width=14, command=window.destroy).pack(side="right")
        window.bind("<Escape>", lambda event: window.destroy())
        self._center_window(window, width=820, height=500)

    # Задача користувача (2026-08-19): "додай кнопку системні команди
    # чат-боту... галочки на ввімкнення... кнопка зберегти яка закриває
    # вікно і зберігає команди" - той самий read-then-write через
    # remote_control_client, що вже й "Способи оплати"/редактор кнопок -
    # домашня программа сама нічого не зберігає локально, лише тягне й
    # штовхає стан client_app.py.
    _SYSTEM_COMMANDS_INFO = (
        ("status", "Статус базы", "Показывает количество листов и строк в кэше."),
        ("sheets", "Список листов", "Показывает названия всех листов Excel."),
        ("first", "Первые строки", "Показывает первые строки указанного листа (сырые данные)."),
        ("chatid", "ID чата", "Показывает ID текущего чата или группы."),
    )

    def open_system_commands_dialog(self):
        window = tk.Toplevel(self.root)
        window.title(self._t("Системные команды чат-бота"))
        window.geometry("620x440")
        window.transient(self.root)
        window.grab_set()

        top = tk.Frame(window)
        top.pack(side="top", fill="x", padx=18, pady=(16, 8))
        tk.Label(
            top,
            text=self._t("Системные команды чат-бота"),
            font=("Segoe UI", 13, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            top,
            text=self._t(
                "Служебные команды бота (доступны только администратору). "
                "Выключенную команду бот игнорирует, будто её не существует."
            ),
            anchor="w",
            justify="left",
            wraplength=560,
            fg="#555555",
        ).pack(anchor="w", pady=(4, 0))

        body = tk.Frame(window)
        body.pack(side="top", fill="both", expand=True, padx=18, pady=8)

        loading_label = tk.Label(body, text=self._t("Загрузка..."), anchor="w")
        loading_label.pack(anchor="w", pady=4)

        checkbox_vars = {}

        def build_rows(commands):
            if not loading_label.winfo_exists():
                return
            loading_label.destroy()
            for name, title, description in self._SYSTEM_COMMANDS_INFO:
                row = tk.Frame(body, bd=1, relief="groove", padx=14, pady=10)
                row.pack(fill="x", pady=5)
                var = tk.BooleanVar(value=bool(commands.get(name, True)))
                checkbox_vars[name] = var
                tk.Checkbutton(
                    row,
                    variable=var,
                    text=f"/{name} — {title}",
                    anchor="w",
                    font=("Segoe UI", 10, "bold"),
                    width=22,
                ).pack(side="left", anchor="n")
                tk.Label(
                    row,
                    text=description,
                    anchor="w",
                    justify="left",
                    wraplength=340,
                    fg="#555555",
                ).pack(side="left", fill="x", expand=True, padx=(18, 0))

        def worker():
            commands = remote_control_client.fetch_remote_system_commands() or {}
            self._run_on_main_thread(lambda: build_rows(commands))

        threading.Thread(target=worker, daemon=True).start()

        bottom = tk.Frame(window)
        bottom.pack(side="bottom", fill="x", padx=18, pady=(8, 16))

        def save_and_close():
            if not checkbox_vars:
                window.destroy()
                return
            commands = {name: var.get() for name, var in checkbox_vars.items()}
            try:
                remote_control_client.save_remote_system_commands(commands)
            except Exception as exc:
                messagebox.showerror(
                    self._t("Ошибка"),
                    self._t("Не удалось сохранить: {value}").format(value=exc),
                )
                return
            window.destroy()

        tk.Button(bottom, text=self._t("Сохранить"), width=14, command=save_and_close).pack(side="right", padx=(8, 0))
        tk.Button(bottom, text=self._t("Отмена"), width=14, command=window.destroy).pack(side="right")
        window.bind("<Escape>", lambda event: window.destroy())
        self._center_window(window, width=620, height=440)

    def open_display_format_dialog(self):
        window = tk.Toplevel(self.root)
        window.title(self._t("Формат отображения"))
        window.geometry("680x520")
        window.transient(self.root)
        window.grab_set()

        selected_format = tk.StringVar(value=self.display_settings.get("date_format"))
        sample_dt = datetime.now()

        top = tk.Frame(window)
        top.pack(side="top", fill="x", padx=18, pady=(16, 8))
        tk.Label(
            top,
            text=self._t("Формат отображения даты и времени"),
            font=("Segoe UI", 13, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            top,
            text=self._t("Выберите, как дата будет отображаться в журнале и отчетах программы."),
            anchor="w",
            fg="#555555",
        ).pack(anchor="w", pady=(4, 0))

        body = tk.Frame(window)
        body.pack(side="top", fill="both", expand=True, padx=18, pady=8)

        canvas = tk.Canvas(body, highlightthickness=0)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        list_frame = tk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=list_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def update_scroll_region(event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(window_id, width=canvas.winfo_width())

        list_frame.bind("<Configure>", update_scroll_region)
        canvas.bind("<Configure>", update_scroll_region)

        for key, title, _ in DISPLAY_DATE_FORMATS:
            row = tk.Frame(list_frame, bd=1, relief="groove", padx=10, pady=8)
            row.pack(fill="x", pady=5)

            radio = tk.Radiobutton(
                row,
                variable=selected_format,
                value=key,
                text=title,
                anchor="w",
                width=28,
            )
            radio.pack(side="left", fill="x")

            preview = tk.Label(
                row,
                text=self._format_datetime_for_display(sample_dt, key),
                font=("Segoe UI", 10, "bold"),
                anchor="w",
                fg="#1f4e79",
            )
            preview.pack(side="left", fill="x", expand=True, padx=(16, 0))

        bottom = tk.Frame(window)
        bottom.pack(side="bottom", fill="x", padx=18, pady=(8, 16))

        def save_format():
            self.display_settings.set("date_format", selected_format.get())
            if getattr(self, "current_view", "") == "action_log":
                self._refresh_action_log()
            window.destroy()

        tk.Button(bottom, text=self._t("Сохранить"), width=14, command=save_format).pack(side="right", padx=(8, 0))
        tk.Button(bottom, text=self._t("Отмена"), width=14, command=window.destroy).pack(side="right")
        window.bind("<Escape>", lambda event: window.destroy())
        self._center_window(window, width=680, height=520)

    # Задача користувача: "зроби один формат відображення кнопок, і дай
    # змогу вибрати йому колір і текст в налаштуваннях. пізніше додасиш
    # можливість прикріплювати фон із пнг картинок" — фон PNG свідомо НЕ
    # тут (окреме прохання на майбутнє). Живе прев'ю оновлюється одразу при
    # виборі кольору, ДО збереження — щоб було видно результат перед тим,
    # як він реально застосується до всієї програми.
    def open_button_style_dialog(self):
        window = tk.Toplevel(self.root)
        window.title(self._t("Формат кнопок"))
        window.transient(self.root)
        window.grab_set()

        selected_bg = tk.StringVar(value=self.display_settings.get("button_bg_color"))
        selected_fg = tk.StringVar(value=self.display_settings.get("button_text_color"))

        top = tk.Frame(window)
        top.pack(side="top", fill="x", padx=18, pady=(16, 8))
        tk.Label(top, text=self._t("Формат кнопок"), font=("Segoe UI", 13, "bold"), anchor="w").pack(anchor="w")
        tk.Label(
            top,
            text=self._t('Один спільний вигляд для всіх кнопок "ред"/"x"/"+" у програмі — фон і колір тексту.'),
            anchor="w", fg="#555555", justify="left", wraplength=460,
        ).pack(anchor="w", pady=(4, 0))

        body = tk.Frame(window)
        body.pack(side="top", fill="both", expand=True, padx=18, pady=8)

        preview_row = tk.Frame(body)
        preview_row.pack(fill="x", pady=(0, 16))
        tk.Label(preview_row, text=self._t("Прев'ю:"), anchor="w").pack(side="left", padx=(0, 12))
        preview_button = tk.Button(
            preview_row, text=self._t("ред"), width=8, relief="solid", bd=1, highlightthickness=0, cursor="hand2",
            bg=selected_bg.get(), fg=selected_fg.get(),
        )
        preview_button.pack(side="left")

        def refresh_preview():
            bg = selected_bg.get()
            preview_button.configure(bg=bg, fg=selected_fg.get(), activebackground=self._darken_hex_color(bg))

        bg_row = tk.Frame(body)
        bg_row.pack(fill="x", pady=(0, 10))
        tk.Label(bg_row, text=self._t("Фон кнопки:"), anchor="w", width=16).pack(side="left")
        bg_swatch = tk.Label(bg_row, text=self._t("  "), bg=selected_bg.get(), relief="solid", bd=1, width=4)
        bg_swatch.pack(side="left", padx=(0, 8))

        def choose_bg_color():
            result = colorchooser.askcolor(color=selected_bg.get(), title=self._t("Фон кнопки"))
            if result and result[1]:
                selected_bg.set(result[1])
                bg_swatch.configure(bg=result[1])
                refresh_preview()

        tk.Button(bg_row, text=self._t("Обрати колір"), command=choose_bg_color).pack(side="left")

        fg_row = tk.Frame(body)
        fg_row.pack(fill="x", pady=(0, 10))
        tk.Label(fg_row, text=self._t("Колір тексту:"), anchor="w", width=16).pack(side="left")
        fg_swatch = tk.Label(fg_row, text=self._t("  "), bg=selected_fg.get(), relief="solid", bd=1, width=4)
        fg_swatch.pack(side="left", padx=(0, 8))

        def choose_fg_color():
            result = colorchooser.askcolor(color=selected_fg.get(), title=self._t("Колір тексту кнопки"))
            if result and result[1]:
                selected_fg.set(result[1])
                fg_swatch.configure(bg=result[1])
                refresh_preview()

        tk.Button(fg_row, text=self._t("Обрати колір"), command=choose_fg_color).pack(side="left")

        tk.Label(
            body,
            text=(
                self._t('Кнопки "+"/"x" (додати/видалити) зберігають власний зелений/червоний '
                "колір тексту незалежно від цього налаштування.")
            ),
            anchor="w", fg="#57606a", justify="left", wraplength=460,
        ).pack(anchor="w", pady=(8, 0))

        bottom = tk.Frame(window)
        bottom.pack(side="bottom", fill="x", padx=18, pady=(8, 16))

        def save_style():
            self.display_settings.set("button_bg_color", selected_bg.get())
            self.display_settings.set("button_text_color", selected_fg.get())
            self._refresh_actions_view()
            self._refresh_custom_buttons()
            self._refresh_payment_methods()
            self._refresh_commands()
            self._refresh_personnel()
            # Свіжий пере-аудит (New-Minor #6): "Журнали" і відкритий редактор
            # синонімів команди — єдині 2 вікна, що досі НЕ перефарбовувались
            # тут (на відміну від Персонал/Редактор кнопок/Команди/Способи
            # оплати вище, які вже коректно оновлюються).
            if getattr(self, "journals_window", None) is not None and self.journals_window.winfo_exists():
                self._refresh_action_log()
                self._refresh_work_log()
            for command_id, entry in list(self._command_alias_editor_windows.items()):
                editor, alias_title, list_frame = entry
                if editor.winfo_exists():
                    self._refresh_command_alias_editor(command_id, alias_title, editor, list_frame)
            self._restyle_static_chip_buttons()
            window.destroy()

        tk.Button(bottom, text=self._t("Зберегти"), width=14, command=save_style).pack(side="right", padx=(8, 0))
        tk.Button(bottom, text=self._t("Відмінити"), width=14, command=window.destroy).pack(side="right")
        window.bind("<Escape>", lambda event: window.destroy())
        self._center_window(window, width=520, height=360)

    # Задача користувача (2026-08-09): екран "Проверьте данные" в Mini App
    # (webapp/) — колір/розмір/жирність окремо для заголовка форми, назви
    # категорії, рядків даних і блоку клієнт/адреса/оплата + текст самого
    # заголовка "Проверьте данные" + колір фону картки, з живим прев'ю. Той
    # самий каркас (Toplevel + colorchooser + refresh_preview-замикання), що
    # й у open_button_style_dialog вище, лише більше груп полів.
    #
    # Жирність (bold) — тристанова, не звичайний чекбокс: None ("Типово") —
    # не перевизначати, лишити CSS-фолбек (сьогоднішній хардкод у
    # style.css); True/False — явно "Жирний"/"Звичайний". Причина: типове
    # значення заголовка форми — font-weight 600 (напівжирний), яке не можна
    # відтворити звичайним булевим чекбоксом (лише 400/700) — тристановий
    # вибір єдиний спосіб зберегти "нуль зміни вигляду, поки не торкались
    # налаштувань" і водночас дати можливість поставити явний
    # жирний/звичайний.
    def open_webapp_style_dialog(self):
        window = tk.Toplevel(self.root)
        window.title(self._t("Оформлення форми Telegram"))
        window.transient(self.root)
        window.grab_set()

        SIZE_DEFAULTS = {"title": 20, "category": 15, "body": 15, "common": 14}
        GROUP_LABELS = {
            "title": self._t("Заголовок форми"),
            "category": self._t("Назва категорії (ДОСКА КД тощо)"),
            "body": self._t("Рядки даних (порода, розмір, кількість...)"),
            "common": self._t("Клієнт, адреса, спосіб оплати"),
        }
        DEFAULT_HEADING_TEXT = "Проверьте данные"

        heading_text_var = tk.StringVar(
            value=self.display_settings.get("webapp_confirm_heading_text") or DEFAULT_HEADING_TEXT
        )
        color_vars = {}
        size_vars = {}
        bold_vars = {}
        for group in GROUP_LABELS:
            color_vars[group] = tk.StringVar(value=self.display_settings.get(f"webapp_{group}_color") or "")
            size_vars[group] = tk.IntVar(
                value=self.display_settings.get(f"webapp_{group}_size") or SIZE_DEFAULTS[group]
            )
            bold_value = self.display_settings.get(f"webapp_{group}_bold")
            bold_vars[group] = tk.StringVar(value="" if bold_value is None else ("true" if bold_value else "false"))
        card_bg_var = tk.StringVar(value=self.display_settings.get("webapp_card_bg_color") or "")
        # Задача користувача: "потрібен в окремій вкладці... такий же
        # редактор і для введення тексту... до двох редакторів створи також
        # можливість змінювати кольори задніх фонів" - друга вкладка
        # редагує ЕКРАН ВВЕДЕННЯ (сама форма з полями), на відміну від
        # першої (екран "Проверьте данные" перед відправкою). Фон "Проверьте
        # данные" (card_bg_var) уже існував - тут лише новий, окремий фон
        # для карток полів уводу (.row-block у webapp/style.css).
        entry_bg_var = tk.StringVar(value=self.display_settings.get("webapp_entry_bg_color") or "")

        top = tk.Frame(window)
        top.pack(side="top", fill="x", padx=18, pady=(16, 8))
        tk.Label(top, text=self._t("Оформлення форми Telegram"), font=("Segoe UI", 13, "bold"), anchor="w").pack(
            anchor="w"
        )
        tk.Label(
            top,
            text=self._t(
                'Колір, розмір, жирність і фон тексту у формі Telegram (webapp/) - '
                "окремо для екрана введення даних і екрана підсумкової перевірки перед відправкою."
            ),
            anchor="w", fg="#555555", justify="left", wraplength=620,
        ).pack(anchor="w", pady=(4, 0))

        notebook = ttk.Notebook(window)
        notebook.pack(side="top", fill="both", expand=True, padx=18, pady=8)

        tab_confirm = tk.Frame(notebook)
        tab_entry = tk.Frame(notebook)
        notebook.add(tab_confirm, text=self._t("Екран перевірки"))
        notebook.add(tab_entry, text=self._t("Екран введення даних"))

        body = tk.Frame(tab_confirm)
        body.pack(side="top", fill="both", expand=True)

        # 4 групи + заголовок + фон картки + Зберегти/Відмінити не влазять
        # у типову висоту вікна — за прямим свідченням користувача, знизу
        # обрізалась ціла 4-та група ("Клієнт, адреса...") і сама кнопка
        # "Зберегти". Скролована колонка (той самий Canvas+Scrollbar+
        # мишача-коліщатко патерн, що й _create_scrollable_list, лише
        # докована зліва, а не згори — той хелпер завжди пакує себе
        # side="top", що конфліктувало б із прев'ю-панеллю праворуч).
        controls_container = tk.Frame(body)
        controls_container.pack(side="left", fill="both", expand=True)

        controls_canvas = tk.Canvas(controls_container, highlightthickness=0)
        controls_scrollbar = ttk.Scrollbar(controls_container, orient="vertical", command=controls_canvas.yview)
        controls = tk.Frame(controls_canvas)
        controls_window_id = controls_canvas.create_window((0, 0), window=controls, anchor="nw")
        controls_canvas.configure(yscrollcommand=controls_scrollbar.set)
        controls_canvas.pack(side="left", fill="both", expand=True)
        controls_scrollbar.pack(side="right", fill="y")

        def _update_controls_scroll_region(event=None):
            controls_canvas.configure(scrollregion=controls_canvas.bbox("all"))

        def _resize_controls_window(event):
            controls_canvas.itemconfigure(controls_window_id, width=event.width)
            _update_controls_scroll_region()

        def _on_controls_mousewheel(event):
            controls_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _unbind_controls_mousewheel(event=None):
            controls_canvas.unbind_all("<MouseWheel>")

        controls.bind("<Configure>", _update_controls_scroll_region)
        controls_canvas.bind("<Configure>", _resize_controls_window)
        controls_canvas.bind("<Enter>", lambda event: controls_canvas.bind_all("<MouseWheel>", _on_controls_mousewheel))
        controls_canvas.bind("<Leave>", _unbind_controls_mousewheel)
        controls_canvas.bind("<Destroy>", _unbind_controls_mousewheel)

        # Задача користувача (2026-08-09, скріншот реальної форми поруч із
        # прев'ю): "прев'ю має бути максимально схожим по кольорам із
        # реальною програмою" — реальний Mini App у користувача відкритий у
        # ТЕМНІЙ темі Telegram (колір фону/картки/тексту беруться з
        # tg.themeParams у реальному застосунку), тож прев'ю фарбуємо
        # темною темою-наближенням (не білою) — це й дає порівнянність, яку
        # просив користувач.
        PREVIEW_BG = "#1c2733"
        PREVIEW_CARD_BG = "#242f3d"
        PREVIEW_TEXT = "#ffffff"
        PREVIEW_HINT = "#8a97a3"
        PREVIEW_BORDER = "#3a4a5b"

        preview_panel = tk.Frame(body, relief="solid", bd=1, width=260, bg=PREVIEW_BG)
        preview_panel.pack(side="right", fill="y", padx=(16, 0))
        preview_panel.pack_propagate(False)

        preview_inner = tk.Frame(preview_panel, bg=PREVIEW_BG)
        preview_inner.pack(fill="both", expand=True, padx=1, pady=1)

        preview_title = tk.Label(
            preview_inner, text=self._t("Продажа одной формой"), bg=PREVIEW_BG, fg=PREVIEW_TEXT, anchor="w"
        )
        preview_title.pack(fill="x", padx=12, pady=(12, 8))

        preview_card = tk.Frame(preview_inner, bg=PREVIEW_CARD_BG)
        preview_card.pack(fill="x", padx=12, pady=(0, 12))

        preview_heading = tk.Label(preview_card, bg=PREVIEW_CARD_BG, anchor="w", justify="left", wraplength=210)
        preview_heading.pack(fill="x", padx=10, pady=(10, 6))

        preview_category = tk.Label(preview_card, text=self._t("ДОСКА KD"), bg=PREVIEW_CARD_BG, anchor="w")
        preview_category.pack(fill="x", padx=10)

        preview_body1 = tk.Label(
            preview_card, text=self._t("Порода: Сосна, 25x100x6000"), bg=PREVIEW_CARD_BG, anchor="w"
        )
        preview_body1.pack(fill="x", padx=10, pady=(2, 0))

        preview_body2 = tk.Label(preview_card, text=self._t("Количество: 10 шт"), bg=PREVIEW_CARD_BG, anchor="w")
        preview_body2.pack(fill="x", padx=10, pady=(2, 0))

        preview_separator = tk.Frame(preview_card, bg=PREVIEW_BORDER, height=1)
        preview_separator.pack(fill="x", padx=10, pady=(10, 6))

        preview_common = tk.Label(
            preview_card, text=self._t("Клиент: LEBS & CO\nСпособ оплаты: ЕФАКТУРА"),
            bg=PREVIEW_CARD_BG, anchor="w", justify="left",
        )
        preview_common.pack(fill="x", padx=10, pady=(0, 10))

        FALLBACK_COLOR = {
            "title": PREVIEW_TEXT, "category": PREVIEW_TEXT, "body": PREVIEW_HINT, "common": PREVIEW_HINT,
        }
        FALLBACK_WEIGHT = {"title": "600", "category": "700", "body": "400", "common": "400"}

        def effective_color(group):
            return color_vars[group].get() or FALLBACK_COLOR[group]

        def effective_weight(group):
            bold_value = bold_vars[group].get()
            if bold_value == "true":
                return "bold"
            if bold_value == "false":
                return "normal"
            return "bold" if FALLBACK_WEIGHT[group] in ("600", "700") else "normal"

        def refresh_preview():
            heading_text = heading_text_var.get() or DEFAULT_HEADING_TEXT
            preview_heading.configure(
                text=heading_text, fg=effective_color("title"),
                font=("Segoe UI", max(8, size_vars["title"].get() - 4), effective_weight("title")),
            )
            for widget in (preview_category,):
                widget.configure(
                    fg=effective_color("category"),
                    font=("Segoe UI", max(8, size_vars["category"].get() - 4), effective_weight("category")),
                )
            for widget in (preview_body1, preview_body2):
                widget.configure(
                    fg=effective_color("body"),
                    font=("Segoe UI", max(8, size_vars["body"].get() - 4), effective_weight("body")),
                )
            preview_common.configure(
                fg=effective_color("common"),
                font=("Segoe UI", max(8, size_vars["common"].get() - 4), effective_weight("common")),
            )
            card_bg = card_bg_var.get() or PREVIEW_CARD_BG
            preview_card.configure(bg=card_bg)
            for widget in (preview_heading, preview_category, preview_body1, preview_body2, preview_common):
                widget.configure(bg=card_bg)

        heading_row = tk.Frame(controls)
        heading_row.pack(fill="x", pady=(0, 10))
        tk.Label(heading_row, text=self._t('Текст заголовка "Проверьте данные":'), anchor="w").pack(anchor="w")
        heading_entry = tk.Entry(heading_row, textvariable=heading_text_var, width=40)
        heading_entry.pack(side="left", fill="x", expand=True, pady=(2, 0))
        heading_entry.bind("<KeyRelease>", lambda event: refresh_preview())

        def reset_heading():
            heading_text_var.set(DEFAULT_HEADING_TEXT)
            refresh_preview()

        tk.Button(heading_row, text=self._t("Скинути"), command=reset_heading).pack(side="left", padx=(8, 0))

        for group, group_label in GROUP_LABELS.items():
            group_frame = tk.LabelFrame(controls, text=group_label)
            group_frame.pack(fill="x", pady=(0, 10))

            color_row = tk.Frame(group_frame)
            color_row.pack(fill="x", padx=8, pady=(6, 4))
            tk.Label(color_row, text=self._t("Колір:"), width=10, anchor="w").pack(side="left")
            swatch = tk.Label(
                color_row, text="  ", bg=color_vars[group].get() or "#ffffff", relief="solid", bd=1, width=4
            )
            swatch.pack(side="left", padx=(0, 8))

            def choose_color(group=group, swatch=swatch):
                result = colorchooser.askcolor(color=color_vars[group].get() or None, title=self._t("Колір тексту"))
                if result and result[1]:
                    color_vars[group].set(result[1])
                    swatch.configure(bg=result[1])
                    refresh_preview()

            tk.Button(color_row, text=self._t("Обрати колір"), command=choose_color).pack(side="left")

            def reset_color(group=group, swatch=swatch):
                color_vars[group].set("")
                swatch.configure(bg="#ffffff")
                refresh_preview()

            tk.Button(color_row, text=self._t("Скинути"), command=reset_color).pack(side="left", padx=(8, 0))

            size_row = tk.Frame(group_frame)
            size_row.pack(fill="x", padx=8, pady=(0, 4))
            tk.Label(size_row, text=self._t("Розмір, px:"), width=10, anchor="w").pack(side="left")
            size_spin = tk.Spinbox(
                size_row, from_=10, to=40, width=5, textvariable=size_vars[group],
                command=refresh_preview,
            )
            size_spin.pack(side="left")
            size_spin.bind("<KeyRelease>", lambda event: refresh_preview())

            def reset_size(group=group):
                size_vars[group].set(SIZE_DEFAULTS[group])
                refresh_preview()

            tk.Button(size_row, text=self._t("Скинути"), command=reset_size).pack(side="left", padx=(8, 0))

            bold_row = tk.Frame(group_frame)
            bold_row.pack(fill="x", padx=8, pady=(0, 8))
            tk.Label(bold_row, text=self._t("Жирність:"), width=10, anchor="w").pack(side="left")
            for value, label in (("", self._t("Типово")), ("true", self._t("Жирний")), ("false", self._t("Звичайний"))):
                tk.Radiobutton(
                    bold_row, text=label, value=value, variable=bold_vars[group],
                    command=refresh_preview,
                ).pack(side="left")

        # Задача користувача (2026-08-09): "щоб я окремо міг кожному
        # заголовку міг вибрати колір, товщину, розмір" - на відміну від 4
        # груп вище (екран "Проверьте данные"), тут кожне РЕАЛЬНЕ поле
        # самої форми (Категорія/Порода/Товщина/.../Спосіб оплати) отримує
        # ВЛАСНИЙ, незалежний контрол. Компактний grid-рядок на кожне поле
        # (не LabelFrame, як групи вище) - 12 повноцінних груп не влізли
        # б навіть у скрол розумної висоти. Сам підпис поля в лівій колонці
        # ще й служить власним живим прев'ю (перефарбовується разом із
        # контролами свого рядка) - без окремого мокапу форми праворуч.
        # Друга вкладка ("Екран введення даних") - той самий скрол-патерн,
        # що й controls_canvas вище, плюс жива прев'ю-панель праворуч
        # (той самий принцип, що вже є у вкладці "Екран перевірки").
        entry_body = tk.Frame(tab_entry)
        entry_body.pack(side="top", fill="both", expand=True)

        entry_container = tk.Frame(entry_body)
        entry_container.pack(side="left", fill="both", expand=True)

        # Задача користувача: "прибери [список підписів по-полю] і додай
        # прев'ю таке як на першій вкладці, де тестово буде показано
        # максимально тексту" - жива прев'ю-панель форми праворуч, замість
        # видаленого списку "Підписи полів форми". Колір/розмір/жирність
        # per-field скасовано - лишились лише 3 групи (вище) + фон сторінки.
        entry_preview_panel = tk.Frame(entry_body, relief="solid", bd=1, width=260, bg=PREVIEW_BG)
        entry_preview_panel.pack(side="right", fill="y", padx=(16, 0))
        entry_preview_panel.pack_propagate(False)

        entry_preview_inner = tk.Frame(entry_preview_panel, bg=PREVIEW_BG)
        entry_preview_inner.pack(fill="both", expand=True, padx=1, pady=1)

        tk.Label(
            entry_preview_inner, text=self._t("Форма ввода данных"),
            bg=PREVIEW_BG, fg=PREVIEW_TEXT, anchor="w",
        ).pack(fill="x", padx=12, pady=(12, 8))

        _entry_preview_refresh_holder = {"fn": lambda: None}

        def _make_entry_preview_row(parent, label_text, sample_text):
            row = tk.Frame(parent, bg=PREVIEW_BG)
            row.pack(fill="x", padx=12, pady=2)
            label_widget = tk.Label(row, text=label_text, bg=PREVIEW_BG, fg=PREVIEW_TEXT, anchor="w", width=14)
            label_widget.pack(side="left")
            border_wrap = tk.Frame(row, bg=PREVIEW_BORDER, padx=1, pady=1)
            border_wrap.pack(side="left", fill="x", expand=True)
            value_label = tk.Label(
                border_wrap, text=sample_text, bg=PREVIEW_CARD_BG, fg=PREVIEW_TEXT, anchor="w", padx=6, pady=2,
            )
            value_label.pack(fill="x")
            return label_widget, border_wrap, value_label

        tk.Label(
            entry_preview_inner, text=self._t("Група 1"), bg=PREVIEW_BG, fg=PREVIEW_HINT, anchor="w",
            font=("Segoe UI", 8, "bold"),
        ).pack(fill="x", padx=12, pady=(6, 2))
        _entry_preview_group1_rows = [
            _make_entry_preview_row(entry_preview_inner, self._t("Категория"), self._t("ДОСКА KD")),
            _make_entry_preview_row(entry_preview_inner, self._t("Порода"), self._t("Сосна")),
            _make_entry_preview_row(entry_preview_inner, self._t("Клиент"), self._t("LEBS & CO")),
            _make_entry_preview_row(entry_preview_inner, self._t("Адрес выгрузки"), self._t("г. Кишинев")),
            _make_entry_preview_row(entry_preview_inner, self._t("Способ оплаты"), self._t("ЕФАКТУРА")),
        ]

        tk.Label(
            entry_preview_inner, text=self._t("Група 2"), bg=PREVIEW_BG, fg=PREVIEW_HINT, anchor="w",
            font=("Segoe UI", 8, "bold"),
        ).pack(fill="x", padx=12, pady=(10, 2))
        _entry_preview_group2_rows = [
            _make_entry_preview_row(entry_preview_inner, self._t("Толщина"), "25"),
            _make_entry_preview_row(entry_preview_inner, self._t("Ширина"), "100"),
            _make_entry_preview_row(entry_preview_inner, self._t("Длина"), "6000"),
            _make_entry_preview_row(entry_preview_inner, self._t("Количество"), "10"),
            _make_entry_preview_row(entry_preview_inner, self._t("Цена"), "4500"),
        ]

        tk.Label(
            entry_preview_inner, text=self._t("Група 3"), bg=PREVIEW_BG, fg=PREVIEW_HINT, anchor="w",
            font=("Segoe UI", 8, "bold"),
        ).pack(fill="x", padx=12, pady=(10, 2))
        _entry_preview_group3_buttons = []
        for button_text in (self._t("Сохранить как шаблон"), self._t("Продолжить продажу")):
            button_wrap = tk.Frame(entry_preview_inner, bg=PREVIEW_BORDER, padx=1, pady=1)
            button_wrap.pack(fill="x", padx=12, pady=2)
            button_label = tk.Label(
                button_wrap, text=button_text, bg=PREVIEW_CARD_BG, fg=PREVIEW_TEXT, anchor="center", pady=4,
            )
            button_label.pack(fill="x")
            _entry_preview_group3_buttons.append((button_wrap, button_label))

        def _refresh_entry_preview():
            entry_preview_panel.configure(bg=page_bg_var.get() or PREVIEW_BG)
            entry_preview_inner.configure(bg=page_bg_var.get() or PREVIEW_BG)
            group_rows = {
                1: _entry_preview_group1_rows,
                2: _entry_preview_group2_rows,
            }
            for group_num, rows in group_rows.items():
                text_color = webapp_group_vars[f"group{group_num}_text"].get() or PREVIEW_TEXT
                border_color = webapp_group_vars[f"group{group_num}_border"].get() or PREVIEW_BORDER
                fill_color = webapp_group_vars[f"group{group_num}_fill"].get() or PREVIEW_CARD_BG
                for label_widget, border_wrap, value_label in rows:
                    label_widget.configure(fg=text_color, bg=page_bg_var.get() or PREVIEW_BG)
                    border_wrap.configure(bg=border_color)
                    value_label.configure(bg=fill_color)
            text_color3 = webapp_group_vars["group3_text"].get() or PREVIEW_TEXT
            border_color3 = webapp_group_vars["group3_border"].get() or PREVIEW_BORDER
            fill_color3 = webapp_group_vars["group3_fill"].get() or PREVIEW_CARD_BG
            for button_wrap, button_label in _entry_preview_group3_buttons:
                button_wrap.configure(bg=border_color3)
                button_label.configure(bg=fill_color3, fg=text_color3)

        _entry_preview_refresh_holder["fn"] = _refresh_entry_preview

        entry_canvas = tk.Canvas(entry_container, highlightthickness=0)
        entry_scrollbar = ttk.Scrollbar(entry_container, orient="vertical", command=entry_canvas.yview)
        entry_controls = tk.Frame(entry_canvas)
        entry_window_id = entry_canvas.create_window((0, 0), window=entry_controls, anchor="nw")
        entry_canvas.configure(yscrollcommand=entry_scrollbar.set)
        entry_canvas.pack(side="left", fill="both", expand=True)
        entry_scrollbar.pack(side="right", fill="y")

        def _update_entry_scroll_region(event=None):
            entry_canvas.configure(scrollregion=entry_canvas.bbox("all"))

        def _resize_entry_window(event):
            entry_canvas.itemconfigure(entry_window_id, width=event.width)
            _update_entry_scroll_region()

        def _on_entry_mousewheel(event):
            entry_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _unbind_entry_mousewheel(event=None):
            entry_canvas.unbind_all("<MouseWheel>")

        entry_controls.bind("<Configure>", _update_entry_scroll_region)
        entry_canvas.bind("<Configure>", _resize_entry_window)
        entry_canvas.bind("<Enter>", lambda event: entry_canvas.bind_all("<MouseWheel>", _on_entry_mousewheel))
        entry_canvas.bind("<Leave>", _unbind_entry_mousewheel)
        entry_canvas.bind("<Destroy>", _unbind_entry_mousewheel)

        entry_bg_row = tk.Frame(entry_controls)
        entry_bg_row.pack(fill="x", padx=4, pady=(0, 10))
        tk.Label(entry_bg_row, text=self._t("Фон карток форми:"), width=16, anchor="w").pack(side="left")
        entry_bg_swatch = tk.Label(
            entry_bg_row, text="  ", bg=entry_bg_var.get() or PREVIEW_CARD_BG, relief="solid", bd=1, width=4
        )
        entry_bg_swatch.pack(side="left", padx=(0, 8))

        def choose_entry_bg():
            result = colorchooser.askcolor(color=entry_bg_var.get() or None, title=self._t("Фон карток форми"))
            if result and result[1]:
                entry_bg_var.set(result[1])
                entry_bg_swatch.configure(bg=result[1])

        tk.Button(entry_bg_row, text=self._t("Обрати колір"), command=choose_entry_bg).pack(side="left")

        def reset_entry_bg():
            entry_bg_var.set("")
            entry_bg_swatch.configure(bg=PREVIEW_CARD_BG)

        tk.Button(entry_bg_row, text=self._t("Скинути"), command=reset_entry_bg).pack(side="left", padx=(8, 0))

        # Задача користувача (ескіз): фон усієї сторінки форми + 3 групи
        # полів (клієнт/адреса/оплата; товщина/ширина/довжина/шт/ціна;
        # кнопки Сохранить/Продолжить/Отправить), кожна з власним кольором
        # букв, кольором обведення й кольором всередині поля вводу.
        def _make_webapp_color_row(parent, initial_value, label_text):
            var = tk.StringVar(value=initial_value or "")
            row = tk.Frame(parent)
            row.pack(fill="x", padx=4, pady=(0, 6))
            tk.Label(row, text=label_text, width=20, anchor="w").pack(side="left")
            swatch = tk.Label(
                row, text="  ", bg=var.get() or PREVIEW_CARD_BG, relief="solid", bd=1, width=4
            )
            swatch.pack(side="left", padx=(0, 8))

            def choose():
                result = colorchooser.askcolor(color=var.get() or None, title=label_text)
                if result and result[1]:
                    var.set(result[1])
                    swatch.configure(bg=result[1])
                    _entry_preview_refresh_holder["fn"]()

            tk.Button(row, text=self._t("Обрати колір"), command=choose).pack(side="left")

            def reset():
                var.set("")
                swatch.configure(bg=PREVIEW_CARD_BG)
                _entry_preview_refresh_holder["fn"]()

            tk.Button(row, text=self._t("Скинути"), command=reset).pack(side="left", padx=(8, 0))
            return var

        page_bg_var = _make_webapp_color_row(
            entry_controls,
            self.display_settings.get("webapp_page_bg_color"),
            self._t("Фон усієї сторінки:"),
        )

        webapp_group_vars = {}
        _WEBAPP_GROUP_TITLES = (
            (1, self._t("Група 1: клієнт / адреса / оплата")),
            (2, self._t("Група 2: товщина / ширина / довжина / кількість / ціна")),
            (3, self._t("Група 3: кнопки (Сохранить/Продолжить/Отправить)")),
        )
        _WEBAPP_COLOR_KINDS = (
            ("text", self._t("Колір букв:")),
            ("border", self._t("Колір обведення:")),
            ("fill", self._t("Колір всередині:")),
        )
        for group_num, group_title in _WEBAPP_GROUP_TITLES:
            tk.Label(entry_controls, text=group_title, font=("Segoe UI", 9, "bold"), anchor="w").pack(
                fill="x", padx=4, pady=(8, 2)
            )
            for kind, kind_label in _WEBAPP_COLOR_KINDS:
                key = f"webapp_group{group_num}_{kind}_color"
                webapp_group_vars[f"group{group_num}_{kind}"] = _make_webapp_color_row(
                    entry_controls, self.display_settings.get(key), kind_label
                )

        _entry_preview_refresh_holder["fn"]()

        card_bg_row = tk.Frame(controls)
        card_bg_row.pack(fill="x", pady=(0, 10))
        tk.Label(card_bg_row, text=self._t("Фон картки даних:"), width=16, anchor="w").pack(side="left")
        card_bg_swatch = tk.Label(
            card_bg_row, text="  ", bg=card_bg_var.get() or PREVIEW_CARD_BG, relief="solid", bd=1, width=4
        )
        card_bg_swatch.pack(side="left", padx=(0, 8))

        def choose_card_bg():
            result = colorchooser.askcolor(color=card_bg_var.get() or None, title=self._t("Фон картки даних"))
            if result and result[1]:
                card_bg_var.set(result[1])
                card_bg_swatch.configure(bg=result[1])
                refresh_preview()

        tk.Button(card_bg_row, text=self._t("Обрати колір"), command=choose_card_bg).pack(side="left")

        def reset_card_bg():
            card_bg_var.set("")
            card_bg_swatch.configure(bg=PREVIEW_CARD_BG)
            refresh_preview()

        tk.Button(card_bg_row, text=self._t("Скинути"), command=reset_card_bg).pack(side="left", padx=(8, 0))

        refresh_preview()

        bottom = tk.Frame(window)
        bottom.pack(side="bottom", fill="x", padx=18, pady=(8, 16))

        def save_webapp_style():
            self.display_settings.set("webapp_confirm_heading_text", heading_text_var.get() or DEFAULT_HEADING_TEXT)
            for group in GROUP_LABELS:
                self.display_settings.set(f"webapp_{group}_color", color_vars[group].get())
                self.display_settings.set(f"webapp_{group}_size", size_vars[group].get())
                bold_value = bold_vars[group].get()
                self.display_settings.set(
                    f"webapp_{group}_bold", None if bold_value == "" else (bold_value == "true")
                )
            self.display_settings.set("webapp_card_bg_color", card_bg_var.get())
            self.display_settings.set("webapp_entry_bg_color", entry_bg_var.get())
            self.display_settings.set("webapp_page_bg_color", page_bg_var.get())
            for group_num in (1, 2, 3):
                for kind in ("text", "border", "fill"):
                    self.display_settings.set(
                        f"webapp_group{group_num}_{kind}_color",
                        webapp_group_vars[f"group{group_num}_{kind}"].get(),
                    )
            # Реальний баг (аудит коду, 2026-08-14): цей рядок тихо стирав
            # webapp_field_label_styles (окремий стиль на КОЖНЕ поле форми,
            # ctx.field_label_styles - webapp/app.js:171-177) при КОЖНОМУ
            # збереженні - хоча в цьому діалозі немає жодного контролу, який
            # би його реально заповнював (лише групові кольори GROUP_LABELS
            # вище). Прибрано - нема сенсу стирати те, чого й так завжди
            # порожньо, а якщо колись з'явиться реальний per-поле редактор,
            # його дані більше не зникатимуть при першому ж збереженні цього
            # діалогу.
            window.destroy()
            # Задача користувача: "немає розуміння зберегло чи ні" — явне
            # підтвердження ПІСЛЯ закриття вікна (не блокує саме
            # збереження, лише повідомляє про вже виконаний факт).
            messagebox.showinfo(
                self._t("Оформлення форми Telegram"),
                self._t(
                    "Налаштування збережено. Кнопка \"Заповнити дані\" в новому "
                    "повідомленні бота вже покаже оновлений вигляд — старі, вже "
                    "надіслані повідомлення з формою лишаються зі своїм попереднім "
                    "вибором, самі вони не оновлюються."
                ),
            )

        tk.Button(bottom, text=self._t("Зберегти"), width=14, command=save_webapp_style).pack(
            side="right", padx=(8, 0)
        )
        tk.Button(bottom, text=self._t("Відмінити"), width=14, command=window.destroy).pack(side="right")
        window.bind("<Escape>", lambda event: window.destroy())
        self._center_window(window, width=780, height=660)

    # Задача користувача (2026-08-08, скріншоти реальної продажі, де хінт
    # "На складе: 7428 шт" фактично відповідав лише ~2590 шт за реальною
    # кубатурою): "потрібно щоб в середині програми був у користувача при
    # такій різниці - вибір що саме зберегти, чи штуки і відповідно
    # змінюється кубатура під штуки, або кубатура залишається, і тоді вже
    # штуки змінюються під кубатуру... переходимо загально на програму де це
    # можливо і зручно робити" — це виправлення вже наявних даних складу,
    # тому живе виключно тут (GUI), не в бот-чаті. `store.
    # find_quantity_measure_mismatches()` — той самий, вже перевірений
    # сканер, що бот-хінт неявно спирається на "реально сельабельну"
    # кількість (той фікс лишається окремою, незалежною підстраховкою — ця
    # форма виправляє КОРІНЬ, а не лише ховає симптом).
    def open_quantity_measure_mismatch_dialog(self):
        mismatches = self.store.find_quantity_measure_mismatches()
        if not mismatches:
            messagebox.showinfo(
                self._t("Перевірка залишків"),
                self._t("Розбіжностей кількість/кубатура не знайдено."),
            )
            return

        window = tk.Toplevel(self.root)
        window.title(self._t("Перевірка залишків (шт/кубатура)"))
        window.transient(self.root)

        top = tk.Frame(window)
        top.pack(side="top", fill="x", padx=18, pady=(16, 8))
        tk.Label(
            top, text=self._t("Перевірка залишків (шт/кубатура)"),
            font=("Segoe UI", 13, "bold"), anchor="w",
        ).pack(anchor="w")
        tk.Label(
            top,
            text=self._t(
                "Для цих позицій кількість, шт і фізичний вимір (м3/м2/мп) не відповідають "
                "одне одному за розміром рядка. Оберіть, яке значення правильне — друге "
                "буде перераховане."
            ),
            anchor="w", fg="#555555", justify="left", wraplength=520,
        ).pack(anchor="w", pady=(4, 0))

        body = tk.Frame(window)
        body.pack(side="top", fill="both", expand=True, padx=18, pady=8)
        list_frame = self._create_scrollable_list(body, bordered=True)

        def refresh():
            for child in list_frame.winfo_children():
                child.destroy()
            current = self.store.find_quantity_measure_mismatches()
            if not current:
                window.destroy()
                messagebox.showinfo(
                    self._t("Перевірка залишків"),
                    self._t("Розбіжностей кількість/кубатура не знайдено."),
                )
                return
            for mismatch in current:
                row_frame = tk.Frame(list_frame)
                row_frame.pack(fill="x", padx=6, pady=4)
                sku_text = mismatch.get("sku") or "?"
                info_text = sku_text + "\n" + self._t(
                    "Кількість: {qty} шт   {unit}: {measure}   (за {unit} ≈ {implied} шт)"
                ).format(
                    qty=_display_bot_number(mismatch["balance_qty"]),
                    unit=mismatch["measure_unit"],
                    measure=_display_bot_number(mismatch["balance_measure"]),
                    implied=_display_bot_number(round(mismatch["implied_qty_from_measure"], 2)),
                )
                tk.Label(row_frame, text=info_text, anchor="w", justify="left", wraplength=380).pack(
                    side="left", fill="x", expand=True
                )
                tk.Button(
                    row_frame, text=self._t("Виправити"),
                    command=lambda m=mismatch: self._open_mismatch_resolution_dialog(window, m, refresh),
                ).pack(side="right")

        refresh()

        bottom = tk.Frame(window)
        bottom.pack(side="bottom", fill="x", padx=18, pady=(8, 16))
        tk.Button(bottom, text=self._t("Закрити"), width=14, command=window.destroy).pack(side="right")
        window.bind("<Escape>", lambda event: window.destroy())
        self._center_window(window, width=620, height=440)

    # Другий, вкладений (не .transient() модальний) попап — сам вибір
    # "яке значення правильне" плюс, за прямою вказівкою користувача,
    # додаткове ГІД-лише питання про округлення, коли перерахована з
    # кубатури кількість не ціла (напр. "372 чи 373 штуки").
    def _open_mismatch_resolution_dialog(self, parent_window, mismatch, on_resolved):
        window = tk.Toplevel(parent_window)
        window.title(self._t("Розбіжність кількість/кубатура"))
        window.transient(parent_window)
        window.grab_set()

        unit = mismatch["measure_unit"]
        sku_text = mismatch.get("sku") or "?"

        top = tk.Frame(window)
        top.pack(side="top", fill="both", expand=True, padx=18, pady=(16, 8))
        tk.Label(top, text=sku_text, font=("Segoe UI", 11, "bold"), anchor="w", wraplength=420).pack(anchor="w")
        tk.Label(
            top,
            text=self._t("Кількість: {qty} шт\n{unit}: {measure}\nЗа {unit} це ≈ {implied} шт.").format(
                qty=_display_bot_number(mismatch["balance_qty"]),
                unit=unit,
                measure=_display_bot_number(mismatch["balance_measure"]),
                implied=_display_bot_number(round(mismatch["implied_qty_from_measure"], 4)),
            ),
            anchor="w", justify="left",
        ).pack(anchor="w", pady=(8, 0))

        def apply_and_close(new_qty, new_measure):
            self.store.resolve_quantity_measure_mismatch(
                mismatch["row_id"],
                mismatch["balance_qty_column_index"],
                mismatch["measure_column_index"],
                new_qty,
                new_measure,
            )
            # Реальний ризик (аудит коду, 2026-08-14): це вже пряме
            # (BEGIN IMMEDIATE) підтверджене записування у SQLite, не
            # буферизована правка таблиці — але раніше тут все одно
            # виставлявся той самий self.has_unsaved_changes, яким
            # користується _discard_current_sheet_changes (кнопка "Оновити
            # без збереження"). Якщо на момент цього виправлення в
            # edit_mode відкрито саме лист СКЛАД, вибір "без збереження"
            # перезаписав би СКЛАД застарілим Excel-файлом — і щойно
            # виправлена розбіжність мовчки відкотилась би назад. Синхронізуємо
            # СКЛАД в Excel одразу (той самий механізм, що й ручна кнопка
            # "Синхронізувати з Excel"), щоб такого застарілого вікна не
            # існувало — а не просто позначаємо стан "незбережено".
            try:
                sync_sheet_to_excel(self.store, "СКЛАД")
            except (PermissionError, OSError, RuntimeError):
                # Excel-файл зараз недоступний (відкритий в іншій програмі
                # тощо) — сам фікс уже надійно збережений у SQLite, але
                # Excel-копія тепер справді відстає, тому тут (і лише тут)
                # прапорець "незбережено" відповідає дійсності.
                self.has_unsaved_changes = True
                messagebox.showwarning(
                    self._t("Excel-файл відкритий"),
                    self._t(
                        "Виправлення збережено в базі, але не вдалося одразу "
                        "оновити Excel-файл (можливо, він відкритий в іншій "
                        "програмі). Синхронізуйте вручну пізніше."
                    ),
                )
            window.destroy()
            on_resolved()

        def keep_qty():
            new_measure = round(mismatch["balance_qty"] * mismatch["piece_measure"], 6)
            apply_and_close(mismatch["balance_qty"], new_measure)

        def keep_measure():
            implied = mismatch["implied_qty_from_measure"]
            rounded = round(implied)
            # Дробова кількість (напр. 372,56 → 372/373) — саме той випадок,
            # де користувач попросив ДОДАТКОВЕ питання, і ЛИШЕ тут (GUI), не
            # в боті: округла кількість не завжди означає "нема сумніву", бо
            # обидва сусідні цілих однаково правдоподібні.
            if abs(implied - rounded) > 0.01:
                _ask_round_choice(implied)
                return
            apply_and_close(rounded, mismatch["balance_measure"])

        def _ask_round_choice(implied):
            round_window = tk.Toplevel(window)
            round_window.title(self._t("Округлення кількості"))
            round_window.transient(window)
            round_window.grab_set()
            lower = int(implied)
            upper = lower + 1
            tk.Label(
                round_window,
                text=self._t(
                    "За {value} {unit} кількість виходить дробова (≈{implied}). "
                    "Округлити до:"
                ).format(value=_display_bot_number(mismatch["balance_measure"]), unit=unit, implied=_display_bot_number(round(implied, 4))),
                anchor="w", justify="left", wraplength=340, padx=16, pady=12,
            ).pack(anchor="w")
            choice_row = tk.Frame(round_window)
            choice_row.pack(pady=(0, 16), padx=16)

            def choose(value):
                round_window.destroy()
                apply_and_close(value, mismatch["balance_measure"])

            tk.Button(choice_row, text=f"{lower} шт", width=10, command=lambda: choose(lower)).pack(
                side="left", padx=(0, 8)
            )
            tk.Button(choice_row, text=f"{upper} шт", width=10, command=lambda: choose(upper)).pack(side="left")
            round_window.bind("<Escape>", lambda event: round_window.destroy())
            self._center_window(round_window, width=380, height=160)

        bottom = tk.Frame(window)
        bottom.pack(side="bottom", fill="x", padx=18, pady=(8, 16))
        tk.Button(
            bottom,
            text=self._t("Зберегти кількість → перерахувати {unit}").format(unit=unit),
            command=keep_qty,
        ).pack(fill="x", pady=(0, 6))
        tk.Button(
            bottom,
            text=self._t("Зберегти {unit} → перерахувати кількість").format(unit=unit),
            command=keep_measure,
        ).pack(fill="x", pady=(0, 6))
        tk.Button(bottom, text=self._t("Скасувати"), command=window.destroy).pack(fill="x")
        window.bind("<Escape>", lambda event: window.destroy())
        self._center_window(window, width=460, height=340)

    # Задача користувача: "додай змогу додавати таблицю ексель до роботи.
    # можна як локальний так і онлайн. потрібно вибрати або або" + "мені
    # потрібно щоб це було просто для користувача у программі" — два радіо
    # (взаємовиключно), локально: звичайний filedialog; онлайн: один раз
    # "Увійти через Microsoft" (device-code, MSAL кешує токен — наступні
    # запуски входу не питають) + вставка посилання на файл ("Копіювати
    # посилання" в OneDrive/SharePoint). Реальна робота з Excel іде через
    # excel_source.py (open_workbook/save_workbook) — цей діалог лише пише
    # обраний режим/дані в settings.json.
    def open_excel_source_dialog(self):
        window = tk.Toplevel(self.root)
        window.title(self._t("Таблиця Excel"))
        window.transient(self.root)
        window.grab_set()

        top = tk.Frame(window)
        top.pack(side="top", fill="x", padx=18, pady=(16, 8))
        tk.Label(top, text=self._t("Таблиця Excel"), font=("Segoe UI", 13, "bold"), anchor="w").pack(anchor="w")
        tk.Label(
            top,
            text=self._t(
                "Оберіть, звідки програма читає й куди зберігає таблицю — локальний файл на "
                "цьому ПК, або файл на OneDrive/SharePoint."
            ),
            anchor="w", fg="#555555", justify="left", wraplength=520,
        ).pack(anchor="w", pady=(4, 0))

        body = tk.Frame(window)
        body.pack(side="top", fill="both", expand=True, padx=18, pady=8)

        mode_var = tk.StringVar(value=self.settings.get("excel_source_mode"))
        local_path_state = {"value": self.settings.get("excel_local_path")}

        # Задача користувача (2026-08-14): "давай тепер зробимо коли новий
        # підключаємо файл щоб питало підтвердження" — питання ЛИШЕ коли
        # це справді ЗМІНА вже підключеного файлу (excel_source.
        # is_real_source_switch), а не найперше підключення.
        _SOURCE_SWITCH_WARNING = self._t(
            "Ви підключаєте інший файл. Нумерація документів, підказки "
            "«останні використані», вивчені імена клієнтів і історія рухів "
            "(приход/продажа/списання/антисептирування) стосуються лише "
            "файлу, який був підключений раніше, і почнуться заново для "
            "нового файлу. Сам вміст таблиць це не зачіпає. Продовжити?"
        )

        mode_row = tk.Frame(body)
        mode_row.pack(fill="x", pady=(0, 12))

        local_frame = tk.Frame(body)
        online_frame = tk.Frame(body)

        def refresh_mode():
            if mode_var.get() == "local":
                online_frame.pack_forget()
                local_frame.pack(fill="x")
            else:
                local_frame.pack_forget()
                online_frame.pack(fill="x")

        tk.Radiobutton(
            mode_row, text=self._t("Локально"), variable=mode_var, value="local", command=refresh_mode,
        ).pack(side="left")
        tk.Radiobutton(
            mode_row, text=self._t("Онлайн (OneDrive/SharePoint)"), variable=mode_var, value="online",
            command=refresh_mode,
        ).pack(side="left", padx=(16, 0))

        local_path_label = tk.Label(local_frame, anchor="w", justify="left", wraplength=480, fg="#333333")
        local_path_label.pack(anchor="w", pady=(0, 8))

        def refresh_local_label():
            path = local_path_state["value"]
            local_path_label.configure(
                text=self._t("Обрано: {value}").format(value=path)
                if path else self._t("Типовий файл програми (test_sklad.xlsx).")
            )

        def choose_local_file():
            initial_dir = self.settings.get("last_file_dialog_dir") or "C:\\"
            if not Path(initial_dir).exists():
                initial_dir = "C:\\"
            selected_file = filedialog.askopenfilename(
                title=self._t("Оберіть Excel-файл"),
                initialdir=initial_dir,
                filetypes=(("Excel files", "*.xlsx"), ("All files", "*.*")),
            )
            if not selected_file:
                return
            selected_path = Path(selected_file)
            local_path_state["value"] = str(selected_path)
            self.settings.set("last_file_dialog_dir", str(selected_path.parent))
            refresh_local_label()

        tk.Button(local_frame, text=self._t("Оберіть файл"), command=choose_local_file).pack(anchor="w")
        refresh_local_label()

        online_status_var = tk.StringVar()

        def refresh_online_status():
            if self.settings.get("excel_online_file_name"):
                online_status_var.set(excel_source.current_source_label())
            else:
                online_status_var.set(self._t("Не підключено."))

        tk.Label(
            online_frame, textvariable=online_status_var, anchor="w", justify="left", wraplength=480,
            fg="#333333",
        ).pack(anchor="w", pady=(0, 10))

        def reset_onedrive_sign_in_state():
            self._onedrive_sign_in_in_progress = False
            if sign_in_button.winfo_exists():
                sign_in_button.config(state="normal")

        def show_device_code_popup(flow, cache):
            code_window = tk.Toplevel(window)
            code_window.title(self._t("Вхід через Microsoft"))
            code_window.transient(window)
            # Свіжий пере-аудит (2026-08-02): виявлено при написанні тесту на
            # Notable #8 - виджет-опція pady (не .pack()'ова) не приймає
            # кортеж (це вже ЗОВНІШНІЙ відступ, а не текстовий padding) -
            # TclError "bad screen distance" на кожному РЕАЛЬНОМУ показі
            # цього попапу (ніколи не траплялось раніше, бо CLIENT_ID ще
            # плейсхолдер - фіча ніколи не доходила до реального виклику).
            # Кортеж переїжджає в .pack(pady=...), де асиметричний відступ
            # дійсно підтримується.
            tk.Label(
                code_window, text=self._t("Код: {value}").format(value=flow["user_code"]),
                font=("Segoe UI", 14, "bold"), padx=20,
            ).pack(pady=(20, 8))
            tk.Label(code_window, text=flow["verification_uri"], padx=20).pack()
            tk.Button(
                code_window, text=self._t("Відкрити сторінку входу"),
                command=lambda: webbrowser.open(flow["verification_uri"]),
            ).pack(pady=12)
            tk.Label(code_window, text=self._t("Очікування входу..."), padx=20).pack(pady=(0, 16))
            self._center_window(code_window, width=360, height=220)

            def wait_for_login():
                try:
                    _token, username = onedrive_sync.complete_device_flow(flow, cache)
                except Exception as exc:
                    error_text = str(exc)
                    self._run_on_main_thread(lambda: (
                        reset_onedrive_sign_in_state(), code_window.destroy(), messagebox.showerror(
                            self._t("Таблиця Excel"), error_text,
                        ),
                    ))
                    return

                # Свіжий пере-аудит (2026-08-02, Notable #8): settings.set(...)
                # раніше викликався напряму з фонового потоку, на відміну від
                # сусіднього connect_link()'s worker(), що вже коректно
                # переносить збереження в root.after(0, ...) - вирівняно.
                def apply():
                    self.settings.set("excel_online_account", username)
                    reset_onedrive_sign_in_state()
                    code_window.destroy()
                    refresh_online_status()

                self._run_on_main_thread(apply)

            threading.Thread(target=wait_for_login, daemon=True).start()

        def sign_in():
            # Свіжий пере-аудит (New-Minor #5): без цього гварда повторний
            # клік поки перший вхід ще триває запускав би ДРУГИЙ одночасний
            # device-flow - реальний потік роботи ширший за сам цей потік
            # (show_device_code_popup/wait_for_login запускає ДРУГИЙ, довший
            # фоновий потік), тож прапорець/кнопка скидаються на КОЖНІЙ
            # термінальній гілці всього флоу, не лише тут.
            if self._onedrive_sign_in_in_progress:
                return
            self._onedrive_sign_in_in_progress = True
            sign_in_button.config(state="disabled")

            def worker():
                try:
                    flow, cache = onedrive_sync.start_device_flow()
                except Exception as exc:
                    error_text = str(exc)
                    self._run_on_main_thread(lambda: (
                        reset_onedrive_sign_in_state(),
                        messagebox.showerror(self._t("Таблиця Excel"), error_text),
                    ))
                    return
                self._run_on_main_thread(lambda: show_device_code_popup(flow, cache))

            threading.Thread(target=worker, daemon=True).start()

        sign_in_button = tk.Button(online_frame, text=self._t("Увійти через Microsoft"), command=sign_in)
        sign_in_button.pack(anchor="w", pady=(0, 12))

        link_row = tk.Frame(online_frame)
        link_row.pack(fill="x", pady=(0, 8))
        tk.Label(link_row, text=self._t("Посилання на файл:"), anchor="w").pack(side="left")
        link_entry = tk.Entry(link_row, width=40)
        link_entry.pack(side="left", padx=(8, 0), fill="x", expand=True)

        def connect_link():
            share_url = link_entry.get().strip()
            if not share_url:
                return

            # Аудит коду: resolve_share_link — реальний HTTP-запит до Microsoft
            # Graph, раніше виконувався напряму в головному потоці й міг на мить
            # "підвісити" вікно — той самий фоновий-потік патерн, що вже є в
            # sign_in() вище.
            # Свіжий пере-аудит (2026-08-02, Notable #8): get_access_token_
            # silent() (теж мережевий виклик — MSAL оновлює прострочений
            # токен через token-endpoint) раніше лишався СИНХРОННИМ прямо тут,
            # ПЕРЕД стартом фонового потоку — той самий клас "підвисання",
            # який цей фікс мав закрити. Обидва мережеві виклики (токен +
            # resolve_share_link) тепер разом усередині ОДНОГО фонового
            # потоку — половинчастий фікс (лише одне з двох у фоні) створив
            # би або те саме зависання, або гонку "потік стартував, але
            # результат читається одразу й синхронно після старту".
            def worker():
                try:
                    token, _username = onedrive_sync.get_access_token_silent()
                    if not token:
                        self._run_on_main_thread(
                            lambda: messagebox.showerror(
                                self._t("Таблиця Excel"), self._t("Спочатку увійдіть через Microsoft.")
                            ),
                        )
                        return
                    drive_id, item_id, file_name = onedrive_sync.resolve_share_link(token, share_url)
                except Exception as exc:
                    error_text = str(exc)
                    self._run_on_main_thread(lambda: messagebox.showerror(self._t("Таблиця Excel"), error_text))
                    return

                def apply():
                    new_identity = f"online:{drive_id}:{item_id}"
                    if excel_source.is_real_source_switch(new_identity):
                        if not messagebox.askyesno(self._t("Таблиця Excel"), _SOURCE_SWITCH_WARNING):
                            return
                    self.settings.set("excel_online_drive_id", drive_id)
                    self.settings.set("excel_online_item_id", item_id)
                    self.settings.set("excel_online_file_name", file_name)
                    refresh_online_status()

                self._run_on_main_thread(apply)

            threading.Thread(target=worker, daemon=True).start()

        tk.Button(online_frame, text=self._t("Підключити файл"), command=connect_link).pack(anchor="w")

        def sign_out():
            onedrive_sync.sign_out()
            self.settings.set("excel_online_account", "")
            self.settings.set("excel_online_drive_id", "")
            self.settings.set("excel_online_item_id", "")
            self.settings.set("excel_online_file_name", "")
            refresh_online_status()

        tk.Button(online_frame, text=self._t("Відключити"), command=sign_out).pack(anchor="w", pady=(12, 0))

        refresh_online_status()
        refresh_mode()

        bottom = tk.Frame(window)
        bottom.pack(side="bottom", fill="x", padx=18, pady=(8, 16))

        def save_source():
            new_mode = mode_var.get()
            if new_mode == "online":
                new_identity = (
                    f"online:{self.settings.get('excel_online_drive_id') or ''}:"
                    f"{self.settings.get('excel_online_item_id') or ''}"
                )
            else:
                new_identity = f"local:{local_path_state['value'] or ''}"
            if excel_source.is_real_source_switch(new_identity):
                if not messagebox.askyesno(self._t("Таблиця Excel"), _SOURCE_SWITCH_WARNING):
                    return
            self.settings.set("excel_local_path", local_path_state["value"] or "")
            self.settings.set("excel_source_mode", new_mode)
            # Реальний привід (2026-08-14): показуємо ОДРАЗУ, який шлях
            # реально збережено - незалежно від того, чи вже стався реімпорт
            # (той вимагає перезапуску окремо, повідомлення нижче про це й
            # попереджає). Так користувач одразу бачить: сам вибір файлу
            # зберігся правильно, а не губиться десь по дорозі.
            self.excel_source_status_text.set(excel_source.current_source_label())
            window.destroy()
            messagebox.showinfo(
                self._t("Таблиця Excel"),
                self._t("Перезапустіть програму, щоб застосувати нове джерело таблиці."),
            )

        tk.Button(bottom, text=self._t("Зберегти"), width=14, command=save_source).pack(side="right", padx=(8, 0))
        tk.Button(bottom, text=self._t("Відмінити"), width=14, command=window.destroy).pack(side="right")
        window.bind("<Escape>", lambda event: window.destroy())
        self._center_window(window, width=560, height=480)

    # Задача користувача (2026-08-15): "давай налаштуємо публікацію
    # 'client' оновлень через gui.py" - раніше update_manifest_path не мав
    # ЖОДНОГО місця в UI, де його можна поставити (лише читався - тут і в
    # client_app.py) - справжня причина, чому вся система перевірки
    # оновлень (в обох програмах) мовчки нічого не знаходила. "Опублікувати"
    # для client_app.py публікує ЗІБРАНИЙ .exe-пакет (dist/AI_Automation_
    # Client/, той самий, що вже перевіряє свій download_update/robocopy-
    # заміна на боці client_app.py), не сирий код - той шлях, для якого цей
    # пакувальний і механізм самооновлення взагалі будувались.
    # Той самий баг/фікс, що й gui_release_dir/client_dist_dir у
    # open_publish_updates_dialog (2026-08-18, знайдено користувачем на
    # живому зібраному .exe) - client_app.py (вихідний код, не зібраний
    # .exe) лежить у корені проєкту, а НЕ поруч із самим запущеним .exe.
    def _read_client_app_version(self):
        root = self._project_git_root() or BASE_DIR
        try:
            source = (root / "client_app.py").read_text(encoding="utf-8")
        except OSError:
            return None
        match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
        return match.group(1) if match else None

    # Реальна знахідка (аудит коду, 2026-08-16): GitHub PAT (право запису в
    # репозиторій) раніше зберігався ПРЯМО в settings.json - той самий файл,
    # який config_backup.py регулярно копіює в OneDrive, тож токен
    # автоматично поїхав би в хмару. Той самий принцип, що вже й
    # BACKUP_PASSWORD_PATH (warehouse_data.py._backup_encryption_password) -
    # окремий, НЕ включений у жоден бекап файл, з одноразовою міграцією
    # старого значення (і негайним очищенням старого ключа).
    def _read_github_publish_token(self):
        if paths.GITHUB_TOKEN_PATH.exists():
            try:
                return paths.GITHUB_TOKEN_PATH.read_text(encoding="utf-8").strip()
            except OSError:
                return ""
        legacy_token = (self.settings.get("github_publish_token") or "").strip()
        if legacy_token:
            self._write_github_publish_token(legacy_token)
            self.settings.set("github_publish_token", "")
        return legacy_token

    def _write_github_publish_token(self, token):
        paths.GITHUB_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        token = (token or "").strip()
        if not token:
            paths.GITHUB_TOKEN_PATH.unlink(missing_ok=True)
            return
        tmp_path = paths.GITHUB_TOKEN_PATH.with_name(paths.GITHUB_TOKEN_PATH.name + ".tmp")
        tmp_path.write_text(token, encoding="utf-8")
        os.replace(tmp_path, paths.GITHUB_TOKEN_PATH)

    # Задача користувача (2026-08-18): "перегляд того що саме оновлюється...
    # як для ІТ фахівця" - технічний бік перегляду оновлення в діалозі
    # публікації. У зібраній версії BASE_DIR - це dist/AI_Automation_Home/
    # (без .git поруч) - тому шукаємо .git або тут, або на 2 рівні вище
    # (структура репозиторію: <корінь>/dist/AI_Automation_Home/), інакше
    # git-історія просто недоступна (немає сенсу падати з помилкою - це
    # лише додатковий, необов'язковий контекст для публікації).
    def _project_git_root(self):
        for candidate in (BASE_DIR, BASE_DIR.parent.parent):
            if (candidate / ".git").exists():
                return candidate
        return None

    _PUBLISH_HISTORY_MARKER_NAME = "last_published_sha.txt"

    def _read_last_published_sha(self):
        root = self._project_git_root()
        if root is None:
            return None
        marker_path = root / "system" / self._PUBLISH_HISTORY_MARKER_NAME
        if not marker_path.exists():
            return None
        try:
            return marker_path.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def _write_last_published_sha(self, sha):
        root = self._project_git_root()
        if root is None or not sha:
            return
        marker_path = root / "system" / self._PUBLISH_HISTORY_MARKER_NAME
        try:
            marker_path.parent.mkdir(parents=True, exist_ok=True)
            marker_path.write_text(sha, encoding="utf-8")
        except OSError:
            pass

    # Задача користувача, кілька раундів: "два додай" (коміти + diff --stat)
    # -> "1 в 1" повний кольоровий код-diff у detach-вікні -> "зовсім
    # погано, купа інформації" -> прибрано diff ПОВНІСТЮ, лишились лише
    # коміти -> "гарний мінімум, але я не про такий - хочу бачити і код і
    # пояснення, тільки стисло і влучно". Остаточна форма: коміти
    # (пояснення, людською мовою) + ОЧИЩЕНИЙ diff (код) під ними.
    # Задача користувача (2026-08-18): "покажи як приховані рядки, зі
    # змогою відкрити" - _DIFF_LINE_LIMIT (15) БІЛЬШЕ НЕ обрізає дані -
    # це лише поріг, скільки рядків ВИДНО одразу в компактному блоці,
    # доки решту не розгорнуть кліком (elide-тег у open_publish_updates_
    # dialog, варіант 3 з мокапів). _DIFF_HARD_CAP - окремий, набагато
    # більший запобіжник ЛИШЕ проти патологічно величезного diff-у (сотні
    # файлів одразу) - у звичайній роботі цієї программи ніколи не
    # спрацьовує. _clean_diff_lines прибирає git-шум (diff --git/index) і
    # version-bump-hunks (той самий фільтр, що вже довів корисність у
    # попередньому раунді).
    _DIFF_LINE_LIMIT = 15
    _DIFF_HARD_CAP = 500
    _VERSION_LINE_RE = re.compile(r'^[+-]\s*__version__\s*=')
    # Задача користувача (2026-08-19): "ти знову змішав код і пояснення" -
    # у цьому репо КОЖНА реальна зміна супроводжується великим поясненням-
    # коментарем ("Задача користувача: ..."); те саме пояснення вже видно
    # в "Коміти:" (git log) і в полі "Просто" - у секції "Код" воно лише
    # затуляє РЕАЛЬНІ рядки логіки. Тому змінені (+/-) рядки, що є ЛИШЕ
    # коментарем, тут прибираються з diff-у; НЕзмінені (контекстні) рядки-
    # коментарі лишаються як є.
    _COMMENT_LINE_RE = re.compile(r'^[+-]\s*#')

    def _clean_diff_lines(self, raw_lines):
        stage = []
        i, n = 0, len(raw_lines)
        while i < n:
            line = raw_lines[i]
            if line.startswith("diff --git ") or line.startswith("index ") or line.startswith("--- "):
                i += 1
                continue
            if line.startswith("+++ "):
                path = line[6:] if line.startswith("+++ b/") else line[4:]
                stage.append(f"=== {path} ===")
                i += 1
                continue
            if line.startswith("@@ "):
                hunk = [line]
                j = i + 1
                while j < n and not raw_lines[j].startswith(("@@ ", "diff --git ", "--- ", "+++ ")):
                    hunk.append(raw_lines[j])
                    j += 1
                body = hunk[1:]
                changed = [l for l in body if l.startswith("+") or l.startswith("-")]
                is_version_only = len(changed) == 2 and all(self._VERSION_LINE_RE.match(l) for l in changed)
                if not is_version_only:
                    filtered_body = [l for l in body if not self._COMMENT_LINE_RE.match(l)]
                    remaining_changed = [l for l in filtered_body if l.startswith("+") or l.startswith("-")]
                    if remaining_changed:
                        stage.append(hunk[0])
                        stage.extend(filtered_body)
                i = j
                continue
            stage.append(line)
            i += 1

        cleaned = []
        n2 = len(stage)
        for idx, line in enumerate(stage):
            if line.startswith("=== ") and line.endswith(" ==="):
                nxt = idx + 1
                if nxt >= n2 or (stage[nxt].startswith("=== ") and stage[nxt].endswith(" ===")):
                    continue
            cleaned.append(line)
        return cleaned

    # Задача користувача (2026-08-19): "журнал оновлень" - винесено з
    # populate_diff (open_publish_updates_dialog) у звичайний метод, бо
    # тепер його треба ще й у секції "Історія" (розфарбувати ```diff-блок
    # всередині вже опублікованих нотаток релізу) - той самий класифікатор
    # рядка, дві різні секції.
    def _diff_line_tag(self, line):
        if line.startswith("=== ") and line.endswith(" ==="):
            return "meta"
        if line.startswith("@@ "):
            return "meta"
        if line.startswith("+"):
            return "added"
        if line.startswith("-"):
            return "removed"
        return None

    # Розбирає нотатки релізу (compose_release_notes нижче: [Просто?] \n\n
    # "Коміти:\n- ..." \n\n "Ключові зміни в коді:\n```diff\n...\n```") на
    # ОДИН короткий рядок для журналу - "Просто", якщо було введено, інакше
    # перший коміт зі списку.
    def _release_summary_line(self, notes):
        text = (notes or "").strip()
        if not text:
            return ""
        commits_marker = self._t("Коміти:")
        idx = text.find(commits_marker)
        plain = text[:idx].strip() if idx > 0 else (text if idx == -1 else "")
        if plain:
            return plain.splitlines()[0]
        if idx >= 0:
            after = text[idx + len(commits_marker):].lstrip("\n")
            for line in after.splitlines():
                line = line.strip()
                if line.startswith("- "):
                    return line[2:]
        return text.splitlines()[0]

    # Задача користувача (2026-08-19): "при відкритті старих оновлень я
    # маю бачити всю інформацію. повну. як в описі змін" - розгортання в
    # "Історії" раніше кидало ВЕСЬ raw-текст нотаток в один Text - тепер
    # розбирає його НАЗАД на ті самі три частини, які compose_release_
    # notes (нижче) туди поклав (Просто / Коміти / diff-огорожа), щоб
    # показати структуровано, тим самим виглядом, що й на вкладці "Опис
    # змін" (окремі підписані секції, кольоровий код).
    def _parse_release_notes(self, notes):
        text = (notes or "").strip()
        commits_marker = self._t("Коміти:")
        code_marker = self._t("Ключові зміни в коді:")
        commits_idx = text.find(commits_marker)
        code_idx = text.find(code_marker)

        plain = text[:commits_idx].strip() if commits_idx > 0 else (text if commits_idx == -1 else "")

        commits_text = ""
        if commits_idx != -1:
            end = code_idx if code_idx != -1 else len(text)
            commits_text = text[commits_idx + len(commits_marker) : end].strip()

        diff_lines = []
        if code_idx != -1:
            code_block = text[code_idx + len(code_marker) :]
            fence_start = code_block.find("```diff")
            if fence_start != -1:
                after_fence = code_block[fence_start + len("```diff") :]
                fence_end = after_fence.rfind("```")
                diff_text = after_fence[:fence_end] if fence_end != -1 else after_fence
                diff_lines = diff_text.strip("\n").splitlines()

        return plain, commits_text, diff_lines

    # since=None (немає збереженої мітки останньої публікації) -> останні 5
    # комітів, той самий "щось краще за нічого" запасний варіант. Мітка
    # оновлюється лише ПІСЛЯ успішної публікації (_write_last_published_sha,
    # у on_gui_publish_finished/on_publish_finished нижче) - невдала спроба
    # не повинна "з'їдати" історію змін, яку так і не опублікували.
    def _compute_git_release_preview(self):
        root = self._project_git_root()
        if root is None:
            no_git_text = self._t("(Немає доступу до git-історії - запущено поза папкою проєкту.)")
            return no_git_text, [], None
        since = self._read_last_published_sha()
        range_spec = f"{since}..HEAD" if since else None

        def run_git(args):
            try:
                result = subprocess.run(
                    ["git", "-C", str(root)] + args,
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                return result.stdout.strip()
            except (OSError, subprocess.SubprocessError):
                return ""

        commits_raw = run_git(["log", "--format=%s", range_spec] if range_spec else ["log", "--format=%s", "-5"])
        diff_raw = run_git(["diff", "--no-color", range_spec] if range_spec else ["diff", "--no-color", "HEAD~5..HEAD"])
        current_sha = run_git(["rev-parse", "HEAD"]) or None

        commits_text = (
            "\n".join(f"- {line}" for line in commits_raw.splitlines())
            if commits_raw
            else self._t("(Немає нових комітів з моменту останньої публікації.)")
        )
        diff_lines = self._clean_diff_lines(diff_raw.splitlines()) if diff_raw else []
        if len(diff_lines) > self._DIFF_HARD_CAP:
            hidden = len(diff_lines) - self._DIFF_HARD_CAP
            diff_lines = diff_lines[: self._DIFF_HARD_CAP]
            diff_lines.append(self._t("… ще {value} рядків - diff занадто великий для перегляду тут").format(value=hidden))
        return commits_text, diff_lines, current_sha

    # Задача користувача (2026-08-19, друга редакція): "що за ручне
    # налаштування? ...щоб автоматом бачив... і щоб я міг перемикатись між
    # цими серверами, бачучи персонал і налаштування данного клієнта" -
    # ручне "+ Додати сервер" прибрано повністю: кожен client_app.py сам
    # вписує себе у спільний OneDrive-реєстр (servers_registry.py), тут
    # лише читається. Клік по рядку - remote_control_client.set_active_
    # server(hostname) - усі ІНШІ вкладки/діалоги (Персонал, Редактор
    # кнопок, Способи оплати, Дії, Стандартне меню) вже автоматично йдуть
    # на новообраний сервер, бо самі функції remote_control_client читають
    # _BASE_URL "наживо" на кожен виклик.
    _SERVER_KIND_LABELS = {"main": "Основна", "test": "Тестова"}

    # Реальна скарга (2026-08-19, живий скріншот): "поправ кнопки, не
    # видно... зроби як показував взагалі 1 в 1" - gui.py's тема (світла/
    # темна перемикачка, _apply_theme) не дала того самого вигляду, що й у
    # мокапі, і кнопки "Оновити"/"Закрити" загубились на темному тлі. Цей
    # діалог тепер НАВМИСНО не йде через _apply_theme - фіксований темний
    # вигляд 1 в 1 з мокапом (варіант 3 з 5 показаних), незалежно від
    # світлої/темної теми решти программи, ті самі кольори, що вже й у
    # client_app.py (COLOR_BG/COLOR_ROW/COLOR_TEXT).
    _SRV_BG = "#1A1D21"
    _SRV_ROW_BG = "#25282D"
    _SRV_ROW_ACTIVE_BG = "#2C3036"
    _SRV_TEXT = "#E5E7EA"
    _SRV_MUTED = "#9AA1AB"
    _SRV_BORDER = "#3A3F46"
    _SRV_ACCENT = "#2F7BD9"
    _SRV_ONLINE = "#3EA96E"
    _SRV_BADGE = {"main": ("#B5D4F4", "#0C447C"), "test": ("#FAC775", "#633806")}

    def open_servers_dialog(self):
        window = tk.Toplevel(self.root)
        window.title(self._t("Сервери"))
        window.geometry("520x420")
        window.configure(bg=self._SRV_BG)
        window.transient(self.root)
        window.grab_set()

        top = tk.Frame(window, bg=self._SRV_BG)
        top.pack(side="top", fill="x", padx=18, pady=(16, 8))
        tk.Label(
            top, text=self._t("Сервери"), font=("Segoe UI", 13, "bold"), anchor="w",
            bg=self._SRV_BG, fg=self._SRV_TEXT,
        ).pack(anchor="w")
        tk.Label(
            top,
            text=self._t(
                "Сервери, що вже самі повідомили про себе - основні й тестові. "
                "Клік по рядку - перемкнутись на нього (Персонал, Редактор кнопок і решта підуть за ним)."
            ),
            anchor="w", justify="left", wraplength=480, bg=self._SRV_BG, fg=self._SRV_MUTED,
        ).pack(anchor="w", pady=(4, 0))

        header_row = tk.Frame(window, bg=self._SRV_BG)
        header_row.pack(fill="x", padx=18, pady=(8, 2))
        tk.Label(
            header_row, text=self._t("Имя"), anchor="w", bg=self._SRV_BG, fg=self._SRV_MUTED, font=("Segoe UI", 9),
        ).pack(side="left", fill="x", expand=True)
        tk.Label(
            header_row, text=self._t("Тип"), width=9, anchor="w", bg=self._SRV_BG, fg=self._SRV_MUTED,
            font=("Segoe UI", 9),
        ).pack(side="left")
        tk.Label(
            header_row, text="●", width=2, anchor="w", bg=self._SRV_BG, fg=self._SRV_MUTED, font=("Segoe UI", 9),
        ).pack(side="left")
        tk.Label(
            header_row, text=self._t("Версия"), width=9, anchor="w", bg=self._SRV_BG, fg=self._SRV_MUTED,
            font=("Segoe UI", 9),
        ).pack(side="left")
        tk.Label(header_row, text="", width=2, bg=self._SRV_BG).pack(side="left")

        list_body = tk.Frame(window, bg=self._SRV_BG)
        list_body.pack(side="top", fill="both", expand=True, padx=18, pady=(0, 4))
        servers_list = self._create_scrollable_list(list_body)
        # _create_scrollable_list повертає лише внутрішній list_frame -
        # видимий фон навколо/між рядками малює CANVAS (list_frame.master)
        # і його контейнер (canvas.master), інакше темні рядки "плавали" б
        # на світлому тлі полотна цього конкретного (фіксовано темного)
        # діалогу.
        servers_list.configure(bg=self._SRV_BG)
        servers_list.master.configure(bg=self._SRV_BG, highlightthickness=0)
        servers_list.master.master.configure(bg=self._SRV_BG)

        def make_server_row(parent, name, server, dot_var, version_var):
            is_active = server["hostname"] == remote_control_client.active_hostname()
            row_bg = self._SRV_ROW_ACTIVE_BG if is_active else self._SRV_ROW_BG
            row = tk.Frame(
                parent, bg=row_bg, highlightthickness=1,
                highlightbackground=self._SRV_ACCENT if is_active else self._SRV_BORDER,
                highlightcolor=self._SRV_ACCENT if is_active else self._SRV_BORDER,
                padx=10, pady=8, cursor="hand2",
            )
            row.pack(fill="x", pady=3)
            name_label = tk.Label(
                row, text=(f"✓ {name}" if is_active else name), anchor="w",
                font=("Segoe UI", 10, "bold" if is_active else "normal"), bg=row_bg, fg=self._SRV_TEXT,
            )
            name_label.pack(side="left", fill="x", expand=True)
            badge_bg, badge_fg = self._SRV_BADGE.get(server["kind"], self._SRV_BADGE["main"])
            badge = tk.Label(
                row, text=self._t(self._SERVER_KIND_LABELS.get(server["kind"], "Основна")), font=("Segoe UI", 8),
                bg=badge_bg, fg=badge_fg, padx=6, pady=1,
            )
            badge.pack(side="left", padx=(0, 6))
            dot = tk.Label(row, textvariable=dot_var, font=("Segoe UI", 10), bg=row_bg, fg=self._SRV_MUTED, width=2)
            dot.pack(side="left")
            tk.Label(
                row, textvariable=version_var, anchor="w", bg=row_bg, fg=self._SRV_MUTED, width=9,
            ).pack(side="left")

            def switch_to_this(event=None):
                remote_control_client.set_active_server(server["hostname"])
                self.settings.set("active_remote_server_hostname", server["hostname"])
                refresh_list()

            for clickable in (row, name_label):
                clickable.bind("<Button-1>", switch_to_this)

            def delete_server():
                if not messagebox.askyesno(
                    self._t("Сервери"), self._t("Прибрати «{value}» зі спільного списку?").format(value=name),
                ):
                    return
                servers_registry.remove_server(name)
                refresh_list()

            tk.Button(
                row, text="✕", command=delete_server, width=2, bg=row_bg, fg=self._SRV_MUTED, relief="flat",
                activebackground=self._SRV_ROW_ACTIVE_BG, activeforeground=self._SRV_TEXT,
                highlightthickness=0, bd=0,
            ).pack(side="left")
            return row

        def refresh_statuses(rows_state):
            def worker():
                for state in rows_state:
                    status = remote_control_client.fetch_remote_status_from(state["hostname"])
                    def apply(state=state, status=status):
                        if status is None:
                            state["dot_var"].set("○")
                            state["version_var"].set(self._t("нет связи"))
                        else:
                            state["dot_var"].set("●")
                            state["version_var"].set(status.get("version") or "?")
                    self._run_on_main_thread(apply)

            threading.Thread(target=worker, daemon=True).start()

        def refresh_list():
            self._clear_frame(servers_list)

            def on_loaded(servers, generation):
                if generation != refresh_list.generation:
                    return
                self._clear_frame(servers_list)
                if not servers:
                    tk.Label(
                        servers_list,
                        text=self._t("Серверів ще не видно. Кожен client_app.py сам зʼявляється тут протягом 2 хв після старту."),
                        anchor="w", justify="left", wraplength=460, bg=self._SRV_BG, fg=self._SRV_MUTED,
                    ).pack(anchor="w", pady=8)
                    return
                rows_state = []
                for name, server in sorted(servers.items()):
                    dot_var = tk.StringVar(value="…")
                    version_var = tk.StringVar(value="…")
                    make_server_row(servers_list, name, server, dot_var, version_var)
                    rows_state.append({"hostname": server["hostname"], "dot_var": dot_var, "version_var": version_var})
                refresh_statuses(rows_state)

            refresh_list.generation = getattr(refresh_list, "generation", 0) + 1
            generation = refresh_list.generation

            def worker():
                servers = servers_registry.read_servers()
                self._run_on_main_thread(lambda: on_loaded(servers, generation))

            threading.Thread(target=worker, daemon=True).start()

        bottom = tk.Frame(window, bg=self._SRV_BG)
        bottom.pack(side="bottom", fill="x", padx=18, pady=(4, 16))
        tk.Button(
            bottom, text=self._t("Оновити"), command=refresh_list,
            bg=self._SRV_ROW_BG, fg=self._SRV_TEXT, activebackground=self._SRV_ROW_ACTIVE_BG,
            activeforeground=self._SRV_TEXT, relief="flat", highlightthickness=1,
            highlightbackground=self._SRV_BORDER, highlightcolor=self._SRV_BORDER, padx=14, pady=6,
        ).pack(side="left")
        tk.Button(
            bottom, text=self._t("Закрити"), command=window.destroy,
            bg=self._SRV_ROW_BG, fg=self._SRV_TEXT, activebackground=self._SRV_ROW_ACTIVE_BG,
            activeforeground=self._SRV_TEXT, relief="flat", highlightthickness=1,
            highlightbackground=self._SRV_BORDER, highlightcolor=self._SRV_BORDER, padx=14, pady=6,
        ).pack(side="right")

        window.bind("<Escape>", lambda event: window.destroy())
        refresh_list()
        # НЕ self._center_window(...) - той метод завжди викликає
        # _apply_theme(window) як частину свого контракту (спільний для
        # 23 інших діалогів, які САМЕ цього й хочуть) - для ЦЬОГО діалогу
        # це якраз стирало б фіксовані темні кольори назад у поточну
        # світлу/темну тему програми. Той самий розрахунок позиції, без
        # виклику теми.
        self.root.update_idletasks()
        window.update_idletasks()
        width, height = 520, 420
        root_x, root_y = self.root.winfo_rootx(), self.root.winfo_rooty()
        root_width, root_height = self.root.winfo_width(), self.root.winfo_height()
        x = root_x + max((root_width - width) // 2, 0)
        y = root_y + max((root_height - height) // 2, 0)
        window.geometry(f"{width}x{height}+{x}+{y}")

    def open_publish_updates_dialog(self):
        window = tk.Toplevel(self.root)
        window.title(self._t("Публікація оновлень"))
        window.transient(self.root)
        # Задача користувача: "вікно падає ззаду і не можна його
        # розширювати" - grab_set() тут раніше робив вікно МОДАЛЬНИМ на
        # рівні всього застосунку (той самий патерн, що й у простих
        # діалогів на кшталт add_operation_field_add_dialog), що заважало
        # detached-вікнам "⤢" (нижче): будь-яке ІНШЕ вікно, відкрите поки
        # цей grab активний, не отримувало миші/фокусу нормально - звідси
        # й "падає ззаду", і "не розширюється" (зміна розміру теж вимагає
        # взаємодії мишею). Це ЄДИНИЙ діалог у программі, звідки тепер
        # можна відкрити ДОДАТКОВІ незалежні вікна, тож він свідомо НЕ
        # модальний (transient лишається - зв'язок із головним вікном для
        # мінімізації/закриття разом, без блокування інших вікон).

        # Задача користувача (2026-08-18): "зроби щоб вікно можна було
        # від'єднувати, приєднувати назад" - список УСІХ зараз відкритих
        # detached-вікон (обох секцій), щоб закрити їх разом із головним
        # діалогом (кнопка "Закрити", Escape, чи "X") - інакше вони б
        # лишались висіти сиротами після закриття батьківського вікна.
        detached_windows = []

        top = tk.Frame(window)
        top.pack(side="top", fill="x", padx=18, pady=(16, 8))
        tk.Label(
            top, text=self._t("Публікація оновлень"), font=("Segoe UI", 13, "bold"), anchor="w",
        ).pack(anchor="w")
        tk.Label(
            top,
            text=self._t(
                "Обидві программи (gui.py й client_app.py) публікуються через "
                "публічний GitHub-реліз - не потребує, щоб обидва ПК були "
                "онлайн одночасно."
            ),
            anchor="w", fg="#555555", justify="left", wraplength=520,
        ).pack(anchor="w", pady=(4, 0))

        # Задача користувача (2026-08-19): "зроби це в вкладках якось.
        # вкладка токен, вкладка опис змін, вкладка історія, вкладка
        # публікація" - діалог розрісся (токен + Просто/коміти/код +
        # тепер ще й журнал оновлень + дві секції публікації) настільки,
        # що все одним суцільним списком стало важко сканувати - чотири
        # вкладки групують за призначенням, а не за хронологією побудови.
        # ttk.Notebook вже теміться через _apply_ttk_theme (TNotebook/
        # TNotebook.Tab), окремого фіксу не треба.
        notebook = ttk.Notebook(window)
        notebook.pack(side="top", fill="both", expand=True, padx=18, pady=8)

        tab_token = tk.Frame(notebook)
        tab_changes = tk.Frame(notebook)
        tab_history = tk.Frame(notebook)
        tab_publish = tk.Frame(notebook)
        notebook.add(tab_token, text=self._t("Токен"))
        notebook.add(tab_changes, text=self._t("Опис змін"))
        notebook.add(tab_history, text=self._t("Історія"))
        notebook.add(tab_publish, text=self._t("Публікація"))

        body = tk.Frame(tab_token)
        body.pack(side="top", fill="both", expand=True, padx=4, pady=8)

        # Задача користувача (2026-08-16): "щоб не в момент увімкненого
        # серверу це було... клієнт вимкнений, вранці увімкнув - отримав
        # оновлення" - публікація тепер іде через публічний GitHub-реліз
        # (github_releases.py), а не спільну мережеву теку чи прямий push
        # через тунель - обидва попередні варіанти вимагали, щоб МІЙ ПК і
        # клієнтський були онлайн одночасно. Публікація ВСЕ ОДНО потребує
        # PAT-токена (лише в МЕНЕ, ніколи не потрапляє в жоден дистрибутив) -
        # перевірка/завантаження з боку client_app.py/gui.py повністю
        # публічні, без токена. Один спільний токен - той самий репозиторій.
        tk.Label(
            body,
            text=self._t(
                "Personal access token (потрібен лише для публікації - "
                "клієнт качає готові релізи без токена)."
            ),
            anchor="w", fg="#555555", justify="left", wraplength=480,
        ).pack(anchor="w", pady=(0, 4))

        github_token_var = tk.StringVar(value=self._read_github_publish_token())
        github_token_row = tk.Frame(body)
        github_token_row.pack(anchor="w", pady=(0, 12), fill="x")
        github_token_entry = tk.Entry(github_token_row, textvariable=github_token_var, show="*", width=60)
        github_token_entry.pack(side="left", fill="x", expand=True)

        # Задача користувача (2026-08-18): "я виходжу і він не зберігається" -
        # раніше ключ зберігався на диск ЛИШЕ в момент натискання
        # "Опублікувати" - закриття вікна без публікації губило щойно
        # прикріплений/введений ключ. Той самий принцип, що й скрізь у цій
        # программі (feedback_persist_all_ui_state) - жодних кнопок
        # "Зберегти", збереження одразу при зміні поля.
        github_token_var.trace_add("write", lambda *_args: self._write_github_publish_token(github_token_var.get()))

        # Задача користувача (2026-08-18): "додай змогу приєднати ключ
        # вручну" - довгий випадковий рядок незручно передруковувати чи
        # вставляти в маленьке поле без помилок. Файл обирається ЛОКАЛЬНО
        # (стандартний filedialog, той самий принцип, що й вибір Excel-
        # файлу/OneDrive нижче) - вміст лише читається в цей самий процес і
        # одразу лягає в github_token_var, як і ручне введення; збереження
        # на диск (_write_github_publish_token) і далі відбувається лише
        # при натисканні "Опублікувати", без жодної зміни цього контракту.
        def attach_github_token_file():
            selected_file = filedialog.askopenfilename(
                title=self._t("Прикріпити файл з ключем"),
                filetypes=[(self._t("Текстові файли"), "*.txt"), (self._t("Усі файли"), "*.*")],
            )
            if not selected_file:
                return
            try:
                token_text = Path(selected_file).read_text(encoding="utf-8").strip()
            except OSError as exc:
                messagebox.showerror(
                    self._t("Публікація оновлень"),
                    self._t("Не удалось прочитать файл:\n{value}\n\n{error}").format(value=selected_file, error=exc),
                )
                return
            if not token_text:
                messagebox.showerror(self._t("Публікація оновлень"), self._t("Файл порожній."))
                return
            github_token_var.set(token_text)

        tk.Button(
            github_token_row, text=self._t("Прикріпити файл..."), command=attach_github_token_file,
        ).pack(side="left", padx=(8, 0))

        # Задача користувача (2026-08-19): окрема вкладка "Опис змін" -
        # той самий вміст, що раніше йшов одразу під токеном, тепер має
        # СВІЙ контент-фрейм у tab_changes замість body (=tab_token).
        changes_body = tk.Frame(tab_changes)
        changes_body.pack(side="top", fill="both", expand=True, padx=4, pady=8)
        body = changes_body

        # Задача користувача, підсумок кількох раундів: "хочу бачити і код і
        # пояснення, тільки стисло і влучно" - "Просто" (вручну, пояснення)
        # + короткий список комітів (пояснення з git) + короткий, ОЧИЩЕНИЙ
        # diff (сам код, _DIFF_LINE_LIMIT=15 рядків - "влучно", не "книга").
        tk.Label(body, text=self._t("Просто (кілька слів для клієнта):"), anchor="w", fg="#555555").pack(anchor="w")
        plain_summary_var = tk.StringVar()
        tk.Entry(body, textvariable=plain_summary_var, width=60).pack(anchor="w", fill="x", pady=(2, 8))

        commits_text, diff_lines, current_git_sha = self._compute_git_release_preview()
        diff_text = "\n".join(diff_lines)

        # Задача користувача (2026-08-18): "зроби щоб вікно можна було
        # від'єднувати, приєднувати назад одне і друге, а також щоб ті
        # вікна можна було розширювати та звужувати" - обидві секції
        # (коміти й diff) отримують кнопку "⤢": відкриває СВІЙ окремий,
        # вільно змінюваний за розміром (resizable(True, True)) Toplevel
        # зі скролом і кнопкою "Приєднати назад" (плюс закриття через "X" -
        # прирівняне до приєднання назад, а не сирітського зникнення).
        # Секція сама лишається на місці (окремий Frame, що НЕ переставляє
        # порядок при від'єднанні/приєднанні) - лише її вміст перебудовується
        # inline<->detached через populate_fn (той самий підхід, що вже
        # був опрацьований раніше цієї сесії для detached-вікон).
        def make_detachable_section(label_text, populate_fn, *, is_diff, inline_height, extra_actions=None):
            section = tk.Frame(body)
            section.pack(anchor="w", fill="x", pady=(0, 12 if is_diff else 8))

            header = tk.Frame(section)
            header.pack(anchor="w", fill="x")
            tk.Label(header, text=label_text, anchor="w", fg="#555555").pack(side="left")
            # Задача користувача (2026-08-18): "дозволити відкрити код в
            # пайтоні чи в окремому вікні зі змогою копіювати із різною
            # розкладкою" - кнопки, а НЕ покладання на Ctrl+C (у минулому
            # вже був окремий баг саме з Ctrl+C/V/X на нерозкладці, gui.py),
            # тож клік копіює напряму через clipboard_append, незалежно від
            # розкладки клавіатури. "Відкрити у файлі" - той самий
            # os.startfile(), що вже використовується в программі (напр.
            # "Відкрити теку" код-бекапів) - справжнє ЗОВНІШНЄ вікно
            # текстового редактора, де копіювання завжди працює нативно.
            for action_text, action_cmd in reversed(extra_actions or []):
                tk.Button(header, text=action_text, command=action_cmd).pack(side="right", padx=(0, 4))

            holder = tk.Frame(section)
            holder.pack(anchor="w", fill="x", pady=(2, 0))

            state = {"detached": None}

            def configure_tags(widget):
                if is_diff:
                    widget.tag_configure("added", foreground="#1a7f37")
                    widget.tag_configure("removed", foreground="#d1242f")
                    widget.tag_configure("meta", foreground="#57606a")

            def build_inline():
                for child in holder.winfo_children():
                    child.destroy()
                widget = tk.Text(
                    holder, height=inline_height, wrap="none" if is_diff else "word",
                    relief="flat", borderwidth=0, font=("Consolas", 9) if is_diff else None,
                )
                configure_tags(widget)
                populate_fn(widget)
                widget.configure(state="disabled")
                widget.pack(fill="x")

            def reattach():
                win = state["detached"]
                if win is not None:
                    state["detached"] = None
                    if win in detached_windows:
                        detached_windows.remove(win)
                    win.destroy()
                build_inline()
                detach_button.configure(text="⤢")

            def detach():
                for child in holder.winfo_children():
                    child.destroy()

                win = tk.Toplevel(window)
                win.title(label_text)
                win.transient(window)
                win.resizable(True, True)
                win.minsize(360, 220)
                win.geometry("560x360")

                win_top = tk.Frame(win)
                win_top.pack(side="top", fill="x", padx=10, pady=(10, 4))
                tk.Button(win_top, text=self._t("Приєднати назад"), command=reattach).pack(side="right")
                for action_text, action_cmd in reversed(extra_actions or []):
                    tk.Button(win_top, text=action_text, command=action_cmd).pack(side="right", padx=(0, 4))

                win_body = tk.Frame(win)
                win_body.pack(side="top", fill="both", expand=True, padx=10, pady=(0, 10))
                scrollbar = tk.Scrollbar(win_body, orient="vertical")
                widget = tk.Text(
                    win_body, wrap="none" if is_diff else "word", relief="flat", borderwidth=0,
                    font=("Consolas", 10) if is_diff else None, yscrollcommand=scrollbar.set,
                )
                scrollbar.configure(command=widget.yview)
                scrollbar.pack(side="right", fill="y")
                widget.pack(side="left", fill="both", expand=True)
                configure_tags(widget)
                populate_fn(widget)
                widget.configure(state="disabled")

                win.protocol("WM_DELETE_WINDOW", reattach)
                state["detached"] = win
                detached_windows.append(win)
                detach_button.configure(text=self._t("Приєднати назад"))
                # Реальний баг (2026-08-18, знайдено на живому скріншоті):
                # detached-вікно лишалось білим при увімкненій темній темі -
                # на відміну від головного діалогу (theming якого йде через
                # спільну точку _center_window -> _apply_theme, 23 місця в
                # программі), тут ЖОДЕН виклик _apply_theme раніше не робився,
                # адже вікно будується вже ПІСЛЯ початкового проходу теми при
                # старті. Той самий фікс, що вже перевірений на 23 інших
                # діалогах - просто явний виклик тут.
                self._apply_theme(win)

            def toggle_detach():
                if state["detached"] is not None:
                    reattach()
                else:
                    detach()

            detach_button = tk.Button(header, text="⤢", width=3, command=toggle_detach)
            detach_button.pack(side="right")

            build_inline()
            return section

        def populate_commits(widget):
            widget.insert("1.0", commits_text)

        # Задача користувача (2026-08-18), обраний варіант 3 з мокапів:
        # "розгорнути на місці (без нового тексту)" - рядки понад
        # _DIFF_LINE_LIMIT ВСТАВЛЕНІ одразу, але приховані тегом "hidden_
        # extra" (elide=True); клік по рядку-перемикачу лише перемикає
        # elide того самого тегу і переписує СВІЙ ОДИН рядок - решта
        # вмісту не переставляється, тож розгортання відбувається саме
        # там, де стоїть перемикач, без прокрутки вниз.
        def populate_diff(widget):
            if not diff_lines:
                widget.insert("end", self._t("(Немає змінених файлів з моменту останньої публікації.)"))
                return

            visible = diff_lines[: self._DIFF_LINE_LIMIT]
            hidden = diff_lines[self._DIFF_LINE_LIMIT :]

            for line in visible:
                tag = self._diff_line_tag(line)
                widget.insert("end", line + "\n", tag if tag else ())

            if not hidden:
                return

            widget.tag_configure("toggle", foreground="#0969da", underline=True)
            widget.tag_configure("hidden_extra", elide=True)
            toggle_state = {"expanded": False}
            toggle_line_no = int(widget.index("end").split(".")[0])

            def toggle_label():
                arrow = "▾" if toggle_state["expanded"] else "▸"
                action = self._t("згорнути") if toggle_state["expanded"] else self._t("розгорнути")
                return self._t("{arrow} ще {count} рядків ({action})").format(
                    arrow=arrow, count=len(hidden), action=action
                )

            widget.insert("end", toggle_label() + "\n", ("toggle",))
            for line in hidden:
                tag = self._diff_line_tag(line)
                tags = tuple(t for t in (tag, "hidden_extra") if t)
                widget.insert("end", line + "\n", tags)

            def on_toggle(event=None):
                toggle_state["expanded"] = not toggle_state["expanded"]
                widget.configure(state="normal")
                widget.delete(f"{toggle_line_no}.0", f"{toggle_line_no}.end")
                widget.insert(f"{toggle_line_no}.0", toggle_label(), ("toggle",))
                widget.tag_configure("hidden_extra", elide=not toggle_state["expanded"])
                widget.configure(state="disabled")

            widget.tag_bind("toggle", "<Button-1>", on_toggle)
            widget.tag_bind("toggle", "<Enter>", lambda event: widget.configure(cursor="hand2"))
            widget.tag_bind("toggle", "<Leave>", lambda event: widget.configure(cursor=""))

        def copy_diff_to_clipboard():
            # Реальний баг (2026-08-18, знайдено тестом на справжньому
            # ~18КБ diff-і цієї ж сесії): один clipboard_append() кирилиці
            # довшої за кілька тисяч символів псує байти РІВНО на межі
            # внутрішнього Tcl/Tk-буфера конверсії в Windows (символ
            # приходив назад як "Ð¾" замість "о" - класичний UTF-8-байти-
            # прочитані-як-Latin-1 артефакт). Реєструємо ОБИДВА формати:
            # UTF8_STRING (без цього багу, але не всі застосунки-цілі
            # вставки його розуміють) і звичайний STRING (типовий, розуміє
            # практично все, страждає від багу лише на дуже великих
            # diff-ах - для звичайного розміру публікації це не спрацьовує).
            window.clipboard_clear()
            window.clipboard_append(diff_text, type="UTF8_STRING")
            window.clipboard_append(diff_text, type="STRING")

        def open_diff_in_file():
            # Реальний баг (2026-08-19, знайдено користувачем): ".diff"
            # розширення відкривало PyCharm-ом його ВЛАСНИЙ спеціалізований
            # в'юер патчів, а не звичайний текстовий редактор - наш
            # _clean_diff_lines-очищений текст не є застосовним патчем
            # (без diff --git/index/---), тож PyCharm показував "Invalid
            # patch file" замість самого тексту. ".txt" відкривається як
            # звичайний текст будь-де, без спеціалізованої обробки формату.
            try:
                preview_dir = Path(tempfile.gettempdir()) / "ai_automation_diff_preview"
                preview_dir.mkdir(parents=True, exist_ok=True)
                diff_file = preview_dir / "diff_preview.txt"
                diff_file.write_text(diff_text, encoding="utf-8")
                os.startfile(str(diff_file))
            except OSError as exc:
                messagebox.showerror(
                    self._t("Публікація оновлень"),
                    self._t("Не удалось відкрити файл: {error}").format(error=exc),
                )

        make_detachable_section(
            self._t("З git (коміти з моменту останньої публікації):"), populate_commits,
            is_diff=False, inline_height=5,
        )
        make_detachable_section(
            self._t("Ключові зміни в коді:"), populate_diff, is_diff=True, inline_height=8,
            extra_actions=[
                (self._t("Копіювати"), copy_diff_to_clipboard),
                (self._t("Відкрити у файлі"), open_diff_in_file),
            ],
        )

        # Задача користувача (2026-08-19): "додай туди зверху журнал
        # оновлень. де до кожної версії оновлень буде прикріплено такий
        # файл з даними. чи просто дані" - окреме сховище НЕ потрібне:
        # compose_release_notes() вище УЖЕ пише повні нотатки (коміти +
        # очищений diff) у ТІЛО кожного релізу під час публікації - GitHub
        # Releases сам є архівом. Тут лише читаємо назад (публічний GET,
        # без токена) через github_releases.list_recent_releases.
        history_body = tk.Frame(tab_history)
        history_body.pack(side="top", fill="both", expand=True, padx=4, pady=8)

        history_status_var = tk.StringVar(value=self._t("Завантаження журналу оновлень..."))
        tk.Label(history_body, textvariable=history_status_var, anchor="w", fg="#555555").pack(
            anchor="w", pady=(0, 6)
        )

        # Реальний баг (2026-08-19, знайдено користувачем): "віконце не
        # скролиться із історією" + "історія очищається при натисканні
        # згорнути" + "текст зникає" - усі три скарги були ОДНИМ і тим
        # самим коренем: history_list був звичайним tk.Frame БЕЗ жодного
        # скролу. При 15 рядках (+ розгорнутий вміст будь-якого з них)
        # контент виходив за межі фіксованої висоти вкладки - усе, що не
        # влізало, просто ОБРІЗАЛОСЬ (не зникало насправді, було там же,
        # просто недоступне без скролу), а згортання/розгортання МІНЯЛО
        # висоту контенту - те, що раніше було видно, після цього
        # опинялось за межею обрізання, виглядаючи як "очистилось/
        # зникло". _create_scrollable_list - той самий Canvas+Scrollbar+
        # коліщатко-миші патерн, що вже перевірений в кількох інших
        # місцях программи (Персонал/Журнали/редактор кнопок).
        history_list = self._create_scrollable_list(history_body)

        # Задача користувача (2026-08-19): "при відкритті старих оновлень я
        # маю бачити всю інформацію. повну. як в описі змін" - раніше сюди
        # вивалювався ВЕСЬ raw-текст нотаток одним суцільним Text -
        # тепер розбирає його (self._parse_release_notes) на ТІ САМІ три
        # частини, які "Опис змін" показує для НОВОЇ публікації (Просто /
        # Коміти / кольоровий код), і показує кожну своєю підписаною
        # секцією - той самий вигляд для старого й нового.
        def render_history_notes(parent, notes):
            plain, commits_text, diff_lines = self._parse_release_notes(notes)

            if plain:
                tk.Label(parent, text=self._t("Просто:"), anchor="w", fg="#8c959f", font=("Segoe UI", 8)).pack(
                    anchor="w", padx=(26, 0)
                )
                tk.Label(parent, text=plain, anchor="w", justify="left", wraplength=460).pack(
                    anchor="w", padx=(26, 0), pady=(0, 6), fill="x"
                )

            if commits_text:
                tk.Label(parent, text=self._t("Коміти:"), anchor="w", fg="#8c959f", font=("Segoe UI", 8)).pack(
                    anchor="w", padx=(26, 0)
                )
                commits_widget = tk.Text(parent, wrap="word", relief="flat", borderwidth=0, height=1)
                commits_widget.insert("1.0", commits_text)
                commit_line_count = int(commits_widget.index("end-1c").split(".")[0])
                commits_widget.configure(height=min(max(commit_line_count, 1), 10), state="disabled")
                commits_widget.pack(fill="x", padx=(26, 0), pady=(0, 6))

            if diff_lines:
                tk.Label(parent, text=self._t("Код:"), anchor="w", fg="#8c959f", font=("Segoe UI", 8)).pack(
                    anchor="w", padx=(26, 0)
                )
                diff_widget = tk.Text(
                    parent, wrap="none", relief="flat", borderwidth=0, font=("Consolas", 9), height=1,
                )
                diff_widget.tag_configure("added", foreground="#1a7f37")
                diff_widget.tag_configure("removed", foreground="#d1242f")
                diff_widget.tag_configure("meta", foreground="#57606a")
                for line in diff_lines:
                    tag = self._diff_line_tag(line)
                    diff_widget.insert("end", line + "\n", tag if tag else ())
                diff_line_count = int(diff_widget.index("end-1c").split(".")[0])
                diff_widget.configure(height=min(max(diff_line_count, 1), 20), state="disabled")
                diff_widget.pack(fill="x", padx=(26, 0), pady=(0, 8))

            if not (plain or commits_text or diff_lines):
                tk.Label(parent, text=self._t("(Немає деталей.)"), anchor="w", fg="#8c959f").pack(
                    anchor="w", padx=(26, 0), pady=(0, 8)
                )

            self._apply_theme(parent)

        def make_history_row(parent, entry):
            entry_frame = tk.Frame(parent)
            entry_frame.pack(anchor="w", fill="x")

            header = tk.Frame(entry_frame, cursor="hand2")
            header.pack(anchor="w", fill="x", pady=2)

            is_gui = entry["kind"] == "gui"
            badge = tk.Label(
                header, text=entry["kind"], font=("Segoe UI", 8),
                bg="#ddf4ff" if is_gui else "#dafbe1", fg="#0969da" if is_gui else "#1a7f37",
                padx=6, pady=1,
            )
            badge.pack(side="left")

            # Задача користувача (2026-08-19): "канал оновлень... коли все
            # ок - окрема кнопка 'Просунути в стабільну'" - тестові релізи
            # (prerelease=True на GitHub) позначені окремим бейджем тут-таки,
            # у "Історія", і мають власну кнопку промоції - без пошуку, чи
            # взагалі був якийсь тестовий реліз й де саме.
            if entry.get("prerelease"):
                test_badge = tk.Label(
                    header, text=self._t("тестовий"), font=("Segoe UI", 8),
                    bg="#fff8c5", fg="#9a6700", padx=6, pady=1,
                )
                test_badge.pack(side="left", padx=(4, 0))

            version_label = tk.Label(header, text=entry["version"], font=("Segoe UI", 9, "bold"), width=10, anchor="w")
            version_label.pack(side="left", padx=(6, 0))

            summary_label = tk.Label(
                header, text=self._release_summary_line(entry["notes"]), anchor="w", fg="#555555",
            )
            summary_label.pack(side="left", fill="x", expand=True, padx=(4, 4))

            date_label = tk.Label(header, text=self._format_last_seen(entry["published_at"]), fg="#8c959f")
            date_label.pack(side="right", padx=(4, 0))

            chevron = tk.Label(header, text="▸", width=2)
            chevron.pack(side="right")

            if entry.get("prerelease"):
                tag_name = entry["tag_name"]
                # promote_button читається ВСЕРЕДИНІ promote_release лише в
                # момент кліку (звичайне замикання Python) - на той час він
                # уже точно призначений нижче, порядок визначення тут не
                # важливий.
                def promote_release(tag_name=tag_name):
                    token = github_token_var.get().strip()
                    if not token:
                        messagebox.showerror(self._t("Публікація оновлень"), self._t("Спершу вкажіть GitHub-токен."))
                        return
                    promote_button.config(state="disabled", text=self._t("Просування..."))

                    def worker():
                        error = None
                        try:
                            github_releases.promote_release_to_stable(
                                token, paths.GITHUB_RELEASES_OWNER, paths.GITHUB_RELEASES_REPO, tag_name,
                            )
                        except Exception as exc:
                            error = str(exc)
                        self._run_on_main_thread(lambda: on_promote_finished(error))

                    def on_promote_finished(error):
                        if error:
                            promote_button.config(state="normal", text=self._t("Просунути в стабільну"))
                            messagebox.showerror(self._t("Публікація оновлень"), error)
                            return
                        # Успіх - перезавантажуємо всю "Історія": цей рядок
                        # більше не тестовий, бейдж і кнопка мають зникнути.
                        load_history()

                    threading.Thread(target=worker, daemon=True).start()

                promote_button = tk.Button(
                    header, text=self._t("Просунути в стабільну"), font=("Segoe UI", 8),
                    command=promote_release,
                )
                promote_button.pack(side="right", padx=(0, 6))

            state = {"expanded": False, "holder": tk.Frame(entry_frame)}
            state["holder"].pack(anchor="w", fill="x")

            # Реальний баг (2026-08-19, знайдено користувачем НАЖИВО, не
            # лише в моєму пісочному середовищі без екрана): "2й клік -
            # залишає місце зарезервованим... текст ховається". Перший
            # фікс (виклик refresh_scroll_region напряму) НЕ спрацював -
            # проблема глибша: Tk-Frame, який ОДНОГО РАЗУ отримав великих
            # дітей (winfo_reqheight виріс), НЕ повертає reqheight назад
            # до малого значення після їхнього destroy(), навіть коли
            # canvas.bbox("all") перераховується напряму (перевірено
            # окремо, без event - той самий результат). Тому замість
            # ЗМЕНШЕННЯ існуючого content_holder - ЩОРАЗУ знищуємо його
            # ПОВНІСТЮ і створюємо СВІЖИЙ порожній Frame: у нового Frame
            # просто НЕМАЄ старого "запам'ятованого" розміру, тож
            # проблема "не зменшується" не виникає в принципі.
            def toggle(event=None):
                state["holder"].destroy()
                state["expanded"] = not state["expanded"]
                new_holder = tk.Frame(entry_frame)
                new_holder.pack(anchor="w", fill="x")
                state["holder"] = new_holder
                if state["expanded"]:
                    chevron.configure(text="▾")
                    render_history_notes(new_holder, entry["notes"])
                else:
                    chevron.configure(text="▸")
                refresh = getattr(parent, "refresh_scroll_region", None)
                if refresh:
                    refresh()

            for clickable in (header, badge, version_label, summary_label, date_label, chevron):
                clickable.bind("<Button-1>", toggle)

            return entry_frame

        def on_history_loaded(entries, error):
            for child in history_list.winfo_children():
                child.destroy()
            if error:
                history_status_var.set(
                    self._t("Не удалось загрузить журнал оновлень: {error}").format(error=error)
                )
                return
            if not entries:
                history_status_var.set(self._t("Ще немає жодного опублікованого релізу."))
                return
            history_status_var.set("")
            for entry in entries:
                make_history_row(history_list, entry)
            self._apply_theme(history_list)

        def load_history():
            # Реальний баг (2026-08-19, живий продакшн): "Не удалось
            # загрузить журнал оновлень: GitHub API 403: rate limit
            # exceeded" - неавтентифіковані запити обмежені 60/годину на
            # IP (проти 5000 з токеном). Токен, якщо вже введений на
            # вкладці "Токен" (потрібен для публікації однаково), передає
            # ці самі публічні GET-запити автентифікованими - читаємо
            # StringVar тут, на головному потоці (Tk-змінні не для
            # доступу з фонового), і передаємо вже звичайний рядок у worker.
            token = github_token_var.get().strip() or None

            def worker():
                try:
                    entries = github_releases.list_recent_releases(
                        paths.GITHUB_RELEASES_OWNER, paths.GITHUB_RELEASES_REPO, limit=15, token=token,
                    )
                    error = None
                except Exception as exc:
                    entries = []
                    error = str(exc)
                self._run_on_main_thread(lambda: on_history_loaded(entries, error))

            threading.Thread(target=worker, daemon=True).start()

        load_history()

        def compose_release_notes():
            plain = plain_summary_var.get().strip()
            parts = [plain] if plain else []
            parts.append(self._t("Коміти:\n{value}").format(value=commits_text))
            parts.append(self._t("Ключові зміни в коді:\n```diff\n{value}\n```").format(value=diff_text))
            return "\n\n".join(parts)

        # Задача користувача (2026-08-19): окрема вкладка "Публікація" -
        # обидві секції (gui.py + client_app.py) нижче тепер у tab_publish
        # замість tab_token/tab_changes.
        publish_body = tk.Frame(tab_publish)
        publish_body.pack(side="top", fill="both", expand=True, padx=4, pady=8)
        body = publish_body

        # Задача користувача (2026-08-16): "стосовно домашньої версії, щоб
        # вона не заважала процесам" - публікація gui.py тепер теж через
        # GitHub Releases (github_releases.GUI_TAG_PREFIX), той самий
        # перевірений .bat-механізм самовстановлення (_install_downloaded_
        # update вище), що вже давно є в gui.py, лише джерело файлів
        # тепер оновлюється поза мережею. Джерело - ІЗОЛЬОВАНА
        # release/AI_Automation_Home/ (НЕ dist/AI_Automation_Home, яку
        # запущений процес міг би заблокувати) - зберіть її окремо:
        # python -m PyInstaller main.py --name AI_Automation_Home --onedir
        # --windowed --contents-directory . --collect-data certifi
        # --distpath release --workpath build_release_gui
        # --specpath build_specs_release_gui --clean --noconfirm
        #
        # Реальний баг (2026-08-18, знайдено користувачем на живому
        # зібраному .exe): БЕЗ _project_git_root() тут стояв голий
        # BASE_DIR - у зібраній версії BASE_DIR = сама dist/AI_Automation_
        # Home/ (де й лежить запущений .exe), тож "BASE_DIR / release /
        # AI_Automation_Home" рахував ВКЛАДЕНИЙ шлях dist/AI_Automation_
        # Home/release/AI_Automation_Home/ (якого не існує) замість
        # сусіднього release/AI_Automation_Home/ у корені проєкту -
        # "Зібраної версії не знайдено" ЗАВЖДИ, коли діалог відкритий із
        # зібраного .exe (єдиний реальний спосіб його використання).
        # _project_git_root() (вище, вже існує для git-превью) вирішує
        # той самий "де насправді корінь проєкту" пошук через .git.
        publish_root = self._project_git_root() or BASE_DIR
        gui_release_dir = publish_root / "release" / "AI_Automation_Home"
        gui_status_text = (
            self._t("Знайдено зібрану версію {value} ({path}).").format(value=__version__, path=gui_release_dir)
            if gui_release_dir.exists()
            else self._t("Зібраної версії не знайдено в release/AI_Automation_Home/.")
        )
        tk.Label(body, text=self._t("Публікація gui.py (GitHub Releases):"), anchor="w").pack(anchor="w", pady=(0, 4))
        tk.Label(body, text=gui_status_text, anchor="w", justify="left", wraplength=480, fg="#333333").pack(
            anchor="w", pady=(0, 4)
        )

        gui_publish_result_text = tk.StringVar()
        tk.Label(body, textvariable=gui_publish_result_text, anchor="w", fg="#555555", justify="left", wraplength=480).pack(
            anchor="w", pady=(0, 4)
        )

        def on_gui_publish_finished(error):
            publish_gui_button.config(state="normal", text=self._t("Опублікувати оновлення gui.py (GitHub)"))
            if error:
                gui_publish_result_text.set("")
                messagebox.showerror(self._t("Публікація оновлень"), error)
                return
            self._write_last_published_sha(current_git_sha)
            gui_publish_result_text.set(
                self._t("Версію {value} gui.py опубліковано на GitHub.").format(value=__version__)
            )

        def publish_gui_update():
            token = github_token_var.get().strip()
            if not token:
                messagebox.showerror(self._t("Публікація оновлень"), self._t("Спершу вкажіть GitHub-токен."))
                return
            if not gui_release_dir.exists():
                messagebox.showerror(
                    self._t("Публікація оновлень"),
                    self._t("Зібраної версії не знайдено в release/AI_Automation_Home/."),
                )
                return
            self._write_github_publish_token(token)
            if not getattr(sys, "frozen", False):
                try:
                    code_backup.create_code_snapshot(label="pre_publish_gui", force=True)
                except OSError as exc:
                    messagebox.showwarning(
                        self._t("Резервные копии"),
                        self._t("Не удалось создать снимок кода перед публикацией: {error}").format(error=exc),
                    )
            publish_gui_button.config(state="disabled", text=self._t("Публікація..."))
            gui_publish_result_text.set("")

            def worker():
                error = None
                tmp_dir = Path(tempfile.mkdtemp())
                try:
                    # Той самий рубіж захисту, що й у publish_client_update
                    # нижче (2026-08-17, живий продакшн) - жоден пакет із
                    # чужим settings.json чи тестовою app_data.sqlite3 не
                    # публікується (друге додано того ж дня - client_app.py
                    # постраждав саме від тестової бази даних, не settings.json).
                    stray_settings = list(gui_release_dir.rglob("settings.json")) + list(
                        gui_release_dir.rglob("app_data.sqlite3")
                    )
                    if stray_settings:
                        raise RuntimeError(
                            self._t(
                                "У зібраному пакеті знайдено сторонній файл ({path}) - публікація скасована, "
                                "щоб не затерти чиїсь дані чи налаштування."
                            ).format(path=stray_settings[0])
                        )
                    zip_base = tmp_dir / gui_release_dir.name
                    zip_path = shutil.make_archive(
                        str(zip_base), "zip", root_dir=str(gui_release_dir.parent), base_dir=gui_release_dir.name,
                    )
                    github_releases.publish_gui_release(
                        token, paths.GITHUB_RELEASES_OWNER, paths.GITHUB_RELEASES_REPO, __version__, zip_path,
                        notes=compose_release_notes(),
                    )
                except Exception as exc:
                    # Реальна знахідка (аудит коду, 2026-08-16): вузький
                    # except тут міг лишити кнопку назавжди заблокованою в
                    # стані "Публікація..." без жодного повідомлення, якщо
                    # GitHub API/zip повернув щось несподіване (напр.
                    # AttributeError на порожньому upload_url чи
                    # json.JSONDecodeError) - той самий клас "always notify
                    # the UI", що вже перевірений у відновленні БД нижче
                    # (finish_with_unexpected_error).
                    error = str(exc)
                finally:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                self._run_on_main_thread(lambda: on_gui_publish_finished(error))

            threading.Thread(target=worker, daemon=True).start()

        publish_gui_button = tk.Button(
            body, text=self._t("Опублікувати оновлення gui.py (GitHub)"), command=publish_gui_update,
        )
        publish_gui_button.pack(anchor="w")

        tk.Frame(body, height=1, bg="#dddddd").pack(fill="x", pady=(12, 12))

        # Той самий баг/фікс, що й gui_release_dir вище - client_dist_dir
        # теж сусід dist/AI_Automation_Home/, не вкладена в неї тека.
        client_dist_dir = publish_root / "dist" / "AI_Automation_Client"
        client_version = self._read_client_app_version()
        status_text = (
            self._t("Знайдено зібрану версію {value} ({path}).").format(value=client_version, path=client_dist_dir)
            if client_dist_dir.exists() and client_version
            else self._t("Зібраної програми не знайдено. Спершу виконайте build_exe.py.")
        )
        tk.Label(body, text=status_text, anchor="w", justify="left", wraplength=480, fg="#333333").pack(
            anchor="w", pady=(0, 8)
        )

        tk.Frame(body, height=1, bg="#dddddd").pack(fill="x", pady=(4, 12))

        tk.Label(body, text=self._t("Публікація client_app.py (GitHub Releases):"), anchor="w").pack(
            anchor="w", pady=(0, 4)
        )

        # Задача користувача (2026-08-19): "канал оновлень... я буду
        # тестити, і оновлення потрібно публікувати в тестову версію
        # спершу" - те саме, що на GitHub зветься prerelease. Клієнт із
        # каналом "Тестова" (Налаштування) бачить УСІ релізи, "Стабільна"
        # (за замовчуванням) - лише не-тестові. "Просунути в стабільну" (у
        # "Історія" вище) перемикає prerelease заднім числом, без нового
        # білда.
        # Реальна скарга (2026-08-19, живе тестування): "не закріплюється
        # тут вибір" - дві окремі причини одразу. (1) Дефолтний Tk selectcolor
        # ("SystemWindow", майже білий) занадто близький до theme["bg"]
        # світлої теми (#F2F3F5) - галочка технічно ставилась, але візуально
        # це майже не було видно. (2) Вибір ніде не зберігався - кожне
        # відкриття діалогу заново скидало його на невідмічений, тож
        # повторне тестування (кілька сесій діалогу поспіль) щоразу
        # вимагало знову ставити галочку вручну. Обидва фікси тут: явний
        # selectcolor (той самий принцип, що вже й у _open_role_menu/
        # _open_personnel_role_filter_menu вище) + збереження в settings.json.
        is_test_release_var = tk.BooleanVar(value=bool(self.settings.get("client_publish_as_test")))

        def on_test_release_toggled():
            self.settings.set("client_publish_as_test", bool(is_test_release_var.get()))

        theme = self._theme()
        tk.Checkbutton(
            body, text=self._t("Тестовий реліз (не піде на стабільний канал)"), variable=is_test_release_var,
            selectcolor=theme["select_bg"], command=on_test_release_toggled,
        ).pack(anchor="w", pady=(0, 6))

        publish_result_text = tk.StringVar()
        tk.Label(body, textvariable=publish_result_text, anchor="w", fg="#555555", justify="left", wraplength=480).pack(
            anchor="w", pady=(0, 4)
        )

        def on_publish_finished(error):
            publish_client_button.config(state="normal", text=self._t("Опублікувати оновлення client_app.py (GitHub)"))
            if error:
                publish_result_text.set("")
                messagebox.showerror(self._t("Публікація оновлень"), error)
                return
            self._write_last_published_sha(current_git_sha)
            publish_result_text.set(
                self._t("Версію {value} client_app.py опубліковано як ТЕСТОВИЙ реліз на GitHub.").format(value=client_version)
                if is_test_release_var.get()
                else self._t("Версію {value} client_app.py опубліковано на GitHub.").format(value=client_version)
            )

        def publish_client_update():
            token = github_token_var.get().strip()
            # Читається тут, на головному потоці (Tk-змінні не для доступу
            # з фонового worker() нижче) - той самий принцип, що вже й
            # token/client_version тут-таки.
            is_test_release = is_test_release_var.get()
            if not token:
                messagebox.showerror(self._t("Публікація оновлень"), self._t("Спершу вкажіть GitHub-токен."))
                return
            if not client_dist_dir.exists() or not client_version:
                messagebox.showerror(
                    self._t("Публікація оновлень"),
                    self._t("Зібраної програми не знайдено. Спершу виконайте build_exe.py."),
                )
                return
            self._write_github_publish_token(token)
            # Реальна знахідка (2026-08-15, живий продакшн): "не удалось
            # создать снимок кода перед публикацией" - той самий клас багу,
            # що вже виправлений у client_app.py - у зібраній версії
            # вихідних .py-файлів немає, спроба ЗАВЖДИ провалюється -
            # у зібраній версії спробу просто не робимо.
            if not getattr(sys, "frozen", False):
                try:
                    code_backup.create_code_snapshot(label="pre_publish_client", force=True)
                except OSError as exc:
                    messagebox.showwarning(
                        self._t("Резервные копии"),
                        self._t("Не удалось создать снимок кода перед публикацией: {error}").format(error=exc),
                    )
            publish_client_button.config(state="disabled", text=self._t("Публікація..."))
            publish_result_text.set("")

            def worker():
                error = None
                tmp_dir = Path(tempfile.mkdtemp())
                try:
                    # Реальний баг (2026-08-17, живий продакшн): "ключ
                    # Telegram злітає після кожного оновлення" - причиною
                    # був МІСЦЕВИЙ settings.json цієї машини, що потрапив у
                    # client_dist_dir через build_exe.py і опублікувався.
                    # Другий, незалежний рубіж - той самий /XF у .bat, що
                    # тепер захищає ВЖЕ ВСТАНОВЛЕНУ версію - але тут, ДО
                    # публікації, найдешевше просто відмовитись публікувати
                    # пакет, що взагалі містить чужий settings.json.
                    #
                    # Той самий клас бага, інший файл (2026-08-17, живий
                    # продакшн): смок-тест зібраного .exe на машині
                    # розробника (--watchdog-check) створив свіжу, майже
                    # порожню app_data.sqlite3 у dist/AI_Automation_Client/
                    # - без цієї перевірки вона потрапила б в опублікований
                    # пакет і затерла б реальну базу (склад/персонал/
                    # журнали) робочого ПК при оновленні. Публікація
                    # client-v0.2.57 з цим файлом уже сталась ДО того, як
                    # цю перевірку додано - виправлено republish'ом v0.2.58.
                    stray_settings = list(client_dist_dir.rglob("settings.json")) + list(
                        client_dist_dir.rglob("app_data.sqlite3")
                    )
                    if stray_settings:
                        raise RuntimeError(
                            self._t(
                                "У зібраному пакеті знайдено сторонній файл ({path}) - публікація скасована, "
                                "щоб не затерти дані чи налаштування робочого ПК. Перезберіть client_app.py через "
                                "build_exe.py (без ручного копіювання system/ чи запуску зібраного .exe) і "
                                "спробуйте ще раз."
                            ).format(path=stray_settings[0])
                        )
                    zip_base = tmp_dir / client_dist_dir.name
                    zip_path = shutil.make_archive(
                        str(zip_base), "zip", root_dir=str(client_dist_dir.parent), base_dir=client_dist_dir.name,
                    )
                    github_releases.publish_client_release(
                        token, paths.GITHUB_RELEASES_OWNER, paths.GITHUB_RELEASES_REPO, client_version, zip_path,
                        notes=compose_release_notes(), prerelease=is_test_release,
                    )
                except Exception as exc:
                    # Реальна знахідка (аудит коду, 2026-08-16): вузький
                    # except тут міг лишити кнопку назавжди заблокованою в
                    # стані "Публікація..." без жодного повідомлення, якщо
                    # GitHub API/zip повернув щось несподіване (напр.
                    # AttributeError на порожньому upload_url чи
                    # json.JSONDecodeError) - той самий клас "always notify
                    # the UI", що вже перевірений у відновленні БД нижче
                    # (finish_with_unexpected_error).
                    error = str(exc)
                finally:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                self._run_on_main_thread(lambda: on_publish_finished(error))

            threading.Thread(target=worker, daemon=True).start()

        publish_client_button = tk.Button(
            body, text=self._t("Опублікувати оновлення client_app.py (GitHub)"), command=publish_client_update,
        )
        publish_client_button.pack(anchor="w")

        def close_dialog():
            for win in list(detached_windows):
                win.destroy()
            window.destroy()

        bottom = tk.Frame(window)
        bottom.pack(side="bottom", fill="x", padx=18, pady=(8, 16))
        tk.Button(bottom, text=self._t("Закрити"), width=14, command=close_dialog).pack(side="right")
        window.bind("<Escape>", lambda event: close_dialog())
        window.protocol("WM_DELETE_WINDOW", close_dialog)
        self._center_window(window, width=560, height=720)

    # Задача користувача (2026-08-14): "вирівняти таблицю... де має бути
    # попередження, що нічого видалено не буде, будуть просто зняті всі
    # фільтри, та вирівняні всі стовпці та рядки під стандарт" - синхронно
    # (як і save_source/sync_excel_manually поруч - локальний файл, це
    # швидко), із чітким текстом попередження ПЕРЕД дією, як і скрізь
    # інде в цьому застосунку для дій, що торкаються реального Excel-файлу.
    def align_excel_table(self):
        confirmed = messagebox.askyesno(
            self._t("Вирівняти таблицю"),
            self._t(
                "Нічого не буде видалено. Будуть лише зняті активні фільтри та "
                "вирівняні рядки й стовпці 5 керованих листів (СКЛАД, ПРИХОД, "
                "ПРОДАЖА, СПИСАНИЕ, АНТИСЕПТИРОВАНИЕ) під єдиний стандарт. "
                "Продовжити?"
            ),
        )
        if not confirmed:
            return
        # Реальна знахідка (аудит коду, 2026-08-16): форматування великих
        # листів openpyxl на головному потоці "заморожує" вікно на весь час
        # роботи - той самий фон-потік + _run_on_main_thread паттерн, що вже
        # використовується для решти файлового I/O в цьому класі (коментар
        # вище про "локальный файл, це швидко" не враховував великі таблиці).
        self.align_table_button.config(state="disabled")

        def worker():
            error = None
            try:
                apply_standard_table_format(self.settings)
            except Exception as exc:
                error = str(exc)

            def finish():
                self.align_table_button.config(state="normal")
                if error:
                    messagebox.showerror(self._t("Вирівняти таблицю"), error)
                else:
                    messagebox.showinfo(self._t("Вирівняти таблицю"), self._t("Таблицю вирівняно."))

            self._run_on_main_thread(finish)

        threading.Thread(target=worker, daemon=True).start()

    # --- Резервные копии базы даних + вихідного коду: два розділи в одному
    # вікні (Задача користувача, 2026-08-14: "резервні копії даних та
    # версій програми" - той самий екран, окремі вкладки замість
    # об'єднаного суцільного списку, щоб не змішувати два різних поняття
    # (дані складу vs версія коду) в одній стіні тексту). ---
    def open_db_backups_dialog(self):
        window = tk.Toplevel(self.root)
        window.title(self._t("Резервные копии"))
        window.transient(self.root)
        window.grab_set()

        notebook = ttk.Notebook(window)
        notebook.pack(side="top", fill="both", expand=True, padx=12, pady=(12, 0))
        db_tab = tk.Frame(notebook)
        code_tab = tk.Frame(notebook)
        notebook.add(db_tab, text=self._t("Данные (база)"))
        notebook.add(code_tab, text=self._t("Код программы"))

        top = tk.Frame(db_tab)
        top.pack(side="top", fill="x", padx=6, pady=(12, 8))
        tk.Label(
            top,
            text=self._t(
                "Программа каждый день сама сохраняет снимок базы данных. "
                "Хранятся последние {limit} снимков, более старые удаляются автоматически."
            ).format(limit=DB_BACKUP_LIMIT),
            anchor="w", fg="#555555", justify="left", wraplength=460,
        ).pack(anchor="w")

        # Задача користувача (2026-08-14): "може паролем краще захистити?" -
        # шифрування відбувається автоматично, і для щоденного знімка (гілка
        # бота/gui), і для відновлення, тому тут лише поле для встановлення/
        # зміни/видалення пароля, без жодного окремого запиту при самому
        # відновленні. Реальний ризик (аудит коду, той самий день): пароль
        # раніше лежав ПРЯМО в settings.json - тепер в окремому файлі
        # (paths.BACKUP_PASSWORD_PATH, _backup_encryption_password/
        # _set_backup_encryption_password, warehouse_data.py) - той самий
        # принцип, що й telegram_token_file.
        password_frame = tk.Frame(db_tab)
        password_frame.pack(side="top", fill="x", padx=6, pady=(4, 0))
        current_password = _backup_encryption_password()
        password_status = tk.Label(
            password_frame,
            text=self._t("Пароль установлен.") if current_password else self._t("Пароль не установлен."),
            anchor="w", fg="#555555",
        )
        password_status.pack(anchor="w")
        password_row = tk.Frame(password_frame)
        password_row.pack(anchor="w", fill="x", pady=(4, 8))
        tk.Label(password_row, text=self._t("Пароль для новых снимков:")).pack(side="left")
        password_entry = tk.Entry(password_row, show="*", width=22)
        password_entry.pack(side="left", padx=(6, 6))

        # Реальний ризик (аудит коду, 2026-08-14): пароль навмисно без
        # окремого файлу-солі (щоб не загубити його й не заблокувати ВСІ
        # знімки одразу) - але зміна/видалення пароля все одно назавжди
        # робить нечитабельними ВЖЕ ЗРОБЛЕНІ знімки, зашифровані попереднім
        # паролем, а раніше про це не попереджали в момент самої зміни
        # (лише статичний напис нижче, який легко проґавити).
        def confirm_password_change(warning_text):
            current = _backup_encryption_password()
            if not current:
                return True
            return messagebox.askyesno(self._t("Резервные копии"), self._t(warning_text))

        def save_password():
            new_value = password_entry.get().strip()
            if new_value == _backup_encryption_password():
                return
            if not confirm_password_change(
                "Старые снимки, зашифрованные текущим паролем, станут нечитаемыми новым "
                "паролем — чтобы их восстановить, понадобится именно прежний пароль. "
                "Изменить пароль?"
            ):
                return
            _set_backup_encryption_password(new_value)
            password_entry.delete(0, "end")
            password_status.config(
                text=self._t("Пароль установлен.") if _backup_encryption_password()
                else self._t("Пароль не установлен.")
            )

        def clear_password():
            if not confirm_password_change(
                "Старые снимки, зашифрованные текущим паролем, останутся зашифрованы — без "
                "него их будет не восстановить. Убрать пароль?"
            ):
                return
            _set_backup_encryption_password("")
            password_entry.delete(0, "end")
            password_status.config(text=self._t("Пароль не установлен."))

        tk.Button(password_row, text=self._t("Сохранить"), command=save_password).pack(side="left", padx=(0, 4))
        tk.Button(password_row, text=self._t("Убрать пароль"), command=clear_password).pack(side="left")
        tk.Label(
            password_frame,
            text=self._t(
                "Если пароль задан — новые снимки шифруются им автоматически. Старый пароль "
                "нужен, чтобы восстановить снимки, сделанные до его смены."
            ),
            anchor="w", fg="#555555", justify="left", wraplength=460,
        ).pack(anchor="w")

        body = tk.Frame(db_tab)
        body.pack(side="top", fill="both", expand=True, padx=6, pady=8)
        list_frame = self._create_scrollable_list(body, bordered=True)

        def refresh_list():
            self._clear_frame(list_frame)
            snapshots = list_db_snapshots()
            if not snapshots:
                tk.Label(list_frame, text=self._t("Снимков пока нет."), anchor="w", fg="#555555").pack(
                    anchor="w", pady=4
                )
            for entry in snapshots:
                row = tk.Frame(list_frame)
                row.pack(anchor="w", fill="x", pady=4)
                when_text = datetime.fromtimestamp(entry["mtime"]).strftime("%d.%m.%Y %H:%M:%S")
                size_mb = entry["size"] / (1024 * 1024)
                tag = self._t(" (перед восстановлением)") if entry["is_pre_restore"] else ""
                tag += self._t(" (зашифрован)") if entry["is_encrypted"] else ""
                label = tk.Label(row, text=f"{when_text} — {size_mb:.1f} МБ{tag}", anchor="w", justify="left")
                label.pack(side="left", fill="x", expand=True)
                restore_button = tk.Button(
                    row,
                    text=self._t("Восстановить"),
                    fg=self._chip_text_color(),
                    command=lambda path=entry["path"], when=when_text: self.restore_db_backup_confirm(path, when),
                    **self._chip_button_style(),
                )
                restore_button.pack(side="right", padx=(8, 0))

        def make_snapshot_now():
            create_db_snapshot(self.db_path)
            refresh_list()

        snapshot_button = tk.Button(top, text=self._t("Сделать снимок сейчас"), command=make_snapshot_now)
        snapshot_button.pack(anchor="w", pady=(8, 0))

        refresh_list()

        # --- Вкладка "Код программы" ---
        code_top = tk.Frame(code_tab)
        code_top.pack(side="top", fill="x", padx=6, pady=(12, 8))
        tk.Label(
            code_top,
            text=self._t(
                "Программа сама сохраняет снимок исходного кода каждые 30 минут (только "
                "если код реально изменился) и перед каждой публикацией/загрузкой обновления. "
                "Хранятся последние {limit} снимков."
            ).format(limit=code_backup.CODE_BACKUP_LIMIT),
            anchor="w", fg="#555555", justify="left", wraplength=460,
        ).pack(anchor="w")

        code_body = tk.Frame(code_tab)
        code_body.pack(side="top", fill="both", expand=True, padx=6, pady=8)
        code_list_frame = self._create_scrollable_list(code_body, bordered=True)

        def refresh_code_list():
            self._clear_frame(code_list_frame)
            snapshots = code_backup.list_code_snapshots()
            if not snapshots:
                tk.Label(code_list_frame, text=self._t("Снимков пока нет."), anchor="w", fg="#555555").pack(
                    anchor="w", pady=4
                )
            label_text = {"pre_publish": self._t(" (перед публикацией)"), "pre_update": self._t(" (перед обновлением)")}
            for entry in snapshots:
                row = tk.Frame(code_list_frame)
                row.pack(anchor="w", fill="x", pady=4)
                when_text = datetime.fromtimestamp(entry["mtime"]).strftime("%d.%m.%Y %H:%M:%S")
                size_kb = entry["size"] / 1024
                tag = label_text.get(entry["label"], "")
                tk.Label(row, text=f"{when_text} — {size_kb:.0f} КБ{tag}", anchor="w", justify="left").pack(
                    anchor="w", fill="x", expand=True
                )

        def make_code_snapshot_now():
            # force=True - той самий принцип, що й ручна кнопка для БД
            # (create_db_snapshot вище): натискання кнопки - завжди свідомий
            # запит "зробити знімок ЗАРАЗ", дедуплікація за хешем стосується
            # лише автоматичного 30-хвилинного тіку, не ручної дії.
            try:
                code_backup.create_code_snapshot(force=True)
            except OSError as exc:
                messagebox.showwarning(
                    self._t("Резервные копии"),
                    self._t("Не удалось создать снимок кода: {error}").format(error=exc),
                )
                return
            refresh_code_list()

        tk.Button(code_top, text=self._t("Сделать снимок кода сейчас"), command=make_code_snapshot_now).pack(
            anchor="w", pady=(8, 0)
        )

        def open_code_backup_folder():
            code_backup.CODE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(code_backup.CODE_BACKUP_DIR)

        tk.Button(code_top, text=self._t("Открыть папку со снимками"), command=open_code_backup_folder).pack(
            anchor="w", pady=(4, 0)
        )

        refresh_code_list()

        bottom = tk.Frame(window)
        bottom.pack(side="bottom", fill="x", padx=18, pady=(8, 16))
        tk.Button(bottom, text=self._t("Закрыть"), width=14, command=window.destroy).pack(side="right")
        window.bind("<Escape>", lambda event: window.destroy())
        self._center_window(window, width=600, height=600)

    def restore_db_backup_confirm(self, snapshot_path, when_text):
        if self.telegram_worker is not None:
            messagebox.showerror(
                self._t("Резервные копии"),
                self._t(
                    "Сначала остановите Telegram-бота (кнопка «Остановить Telegram» в "
                    "Настройках), затем попробуйте восстановить снова."
                ),
            )
            return
        confirmed = messagebox.askyesno(
            self._t("Восстановить снимок"),
            self._t(
                "Восстановить состояние базы на {when}?\n\n"
                "Текущее состояние будет автоматически сохранено отдельным снимком перед "
                "восстановлением — его всегда можно будет восстановить обратно."
            ).format(when=when_text),
        )
        if not confirmed:
            return
        self.store.close()

        # Реальний ризик (аудит коду, 2026-08-14): restore_db_snapshot
        # (pre_restore-знімок + опційне розшифрування + копіювання файлу) і
        # regenerate_excel_after_restore (повний перезапис 3 листів Excel)
        # разом можуть тривати кілька секунд - раніше все це виконувалось
        # СИНХРОННО прямо тут, без жодної візуальної ознаки, що щось
        # відбувається: вікно виглядало завислим рівно там, де користувач
        # найбільше хоче впевненості, що нічого не зламалось. Той самий
        # фоновий-потік + _run_on_main_thread патерн, що вже є в
        # sign_in()/connect_link() (open_webapp_style_dialog вище) — з
        # індикатором прогресу (indeterminate, бо точної частки виконання
        # немає), поки триває сама робота.
        progress_window = tk.Toplevel(self.root)
        progress_window.title(self._t("Восстановление"))
        progress_window.transient(self.root)
        progress_window.grab_set()
        progress_window.resizable(False, False)
        progress_window.protocol("WM_DELETE_WINDOW", lambda: None)
        tk.Label(
            progress_window,
            text=self._t("Восстановление базы данных, подождите..."),
            padx=24,
        ).pack(pady=(20, 10))
        progress_bar = ttk.Progressbar(progress_window, mode="indeterminate", length=280)
        progress_bar.pack(padx=24, pady=(0, 20))
        progress_bar.start(12)
        self._center_window(progress_window, width=340, height=110)

        def worker():
            try:
                restore_db_snapshot(self.db_path, snapshot_path)
            except ValueError as exc:
                # Реальний баг (аудит коду, 2026-08-14): except ... as exc
                # видаляється, щойно except-блок завершується - lambda тут
                # виконується ПІЗНІШЕ (на головному потоці), тож str(exc)
                # усередині lambda на той момент упав би з NameError. Текст
                # помилки треба захопити ТУТ, поки exc ще живий.
                error_text = str(exc)
                self._run_on_main_thread(lambda: finish_with_password_error(error_text))
                return
            except Exception as exc:
                error_text = str(exc)
                self._run_on_main_thread(lambda: finish_with_unexpected_error(error_text))
                return
            regenerate_excel_after_restore(self.db_path)
            self._run_on_main_thread(finish_success)

        def finish_with_password_error(error_text):
            progress_bar.stop()
            progress_window.destroy()
            # Задача користувача (2026-08-14): пароль зашифрованого знімка не
            # співпав (або зараз не заданий) - жива база НЕ зачеплена
            # (restore_db_snapshot падає ДО запису), тож достатньо просто
            # знову відкрити те саме з'єднання, а не перезапускати програму.
            self.store = ExcelSqliteStore(self.db_path)
            messagebox.showerror(self._t("Резервные копии"), error_text)

        def finish_with_unexpected_error(error_text):
            progress_bar.stop()
            progress_window.destroy()
            self.store = ExcelSqliteStore(self.db_path)
            messagebox.showerror(self._t("Резервные копии"), error_text)

        def finish_success():
            progress_bar.stop()
            progress_window.destroy()
            messagebox.showinfo(
                self._t("Восстановлено"),
                self._t("База восстановлена. Перезапустите программу, чтобы применить изменения."),
            )
            self.root.destroy()

        threading.Thread(target=worker, daemon=True).start()

    # Само-перепланований тик (той самий ідіом, що й preview-тики popup'ів,
    # gui.py:1680-1690, лише на рівні self.root/is_closing замість одного
    # віджета — бо це має жити всю сесію застосунку, а не поки відкритий
    # один popup) — дає щоденний знімок і для довгої сесії GUI без
    # перезапуску, не лише для чека при старті вище.
    # Watchdog-пункт 2: жодного нового стану заводити не треба - час уже
    # надійно записаний у самій назві/mtime кожного файлу знімка
    # (create_db_snapshot), тож просто читаємо найновіший через уже наявний
    # list_db_snapshots() (найновіші першими), фільтруючи pre_restore-знімки
    # (той самий розподіл, що вже ввів New-Important #6 - це окрема
    # категорія, не "звичайний" щоденний знімок).
    def _update_db_snapshot_heartbeat(self):
        snapshots = [s for s in list_db_snapshots() if not s["is_pre_restore"]]
        if not snapshots:
            return
        iso_value = datetime.fromtimestamp(snapshots[0]["mtime"]).isoformat()
        self.db_snapshot_heartbeat_text.set(
            self._t("Останній знімок: {value}").format(value=self._format_action_log_time(iso_value))
        )

    def _schedule_db_backup_tick(self):
        if self.is_closing:
            return
        try:
            maybe_create_scheduled_snapshot(self.db_path)
        except Exception as exc:
            # Аудит коду: раніше мовчки "pass" — якщо щоденний знімок не
            # вдавався на періодичному тику, ніхто про це не дізнавався (на
            # відміну від стартового виклику в __init__, який уже попереджає).
            # Свіжий пере-аудит (watchdog-пункт 2): вузький except OSError
            # тут особливо небезпечний - непійманий sqlite3.OperationalError
            # ("database is locked", GUI+бот пишуть в один файл одночасно)
            # НЕ просто пропускав би один тик, а НАЗАВЖДИ вбивав би весь
            # цикл self.root.after нижче до кінця сесії застосунку (той
            # самий клас бага, від якого захищає Telegram-watchdog для
            # свого власного циклу опитування).
            messagebox.showwarning(
                self._t("Резервные копии"),
                self._t("Не удалось создать автоматический снимок базы данных: {error}").format(error=exc),
            )
        self._update_db_snapshot_heartbeat()
        self.root.after(1800000, self._schedule_db_backup_tick)

    # Задача користувача (2026-08-14): "потрібен ще бекап версій програм.
    # щоб зберігався раз в 30 хв" - той самий 30-хвилинний тік, що вже має
    # _schedule_db_backup_tick вище (той самий root.after(1800000, ...)
    # інтервал), лише для ВИХІДНОГО КОДУ, не даних (code_backup.py).
    # create_code_snapshot сама вирішує, чи справді потрібен новий архів
    # (дедуплікація за хешем вмісту) - тік лише регулярно дає їй шанс це
    # перевірити. У фоновому потоці - хоч архівування невеликого проєкту й
    # так швидке, той самий принцип обережності, що вже застосований до
    # кожного файлового/мережевого виклику в цьому класі.
    def _schedule_code_backup_tick(self):
        if self.is_closing:
            return

        def worker():
            try:
                code_backup.create_code_snapshot()
            except OSError as exc:
                # Реальний баг (аудит коду, 2026-08-14): той самий клас
                # except-var-clearing NameError, що й у restore_db_backup_
                # confirm вище - error_text захоплюється ТУТ, поки exc ще живий.
                error_text = str(exc)
                self._run_on_main_thread(lambda: messagebox.showwarning(
                    self._t("Резервные копии"),
                    self._t("Не удалось создать автоматический снимок кода программы: {error}").format(error=error_text),
                ))
            # Задача користувача (2026-08-16): "бекап і на хмару" для
            # settings.json/ключа тунелю - той самий 30-хвилинний фоновий
            # тік (config змінюється так само рідко, як і код), мовчки (той
            # самий контракт, що й "код не змінився" вище - OSError тут
            # означає лише "нема прав на запис", не критично щотику попереджати).
            try:
                config_snapshot_path = config_backup.create_config_snapshot()
            except OSError:
                config_snapshot_path = None
            if config_snapshot_path:
                self._mirror_backup_to_onedrive(config_snapshot_path, glob_pattern="config_*")

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(1800000, self._schedule_code_backup_tick)

    # Той самий принцип, що вже перевірений у client_app.py
    # (_mirror_backup_to_onedrive) - НЕ мій акаунт, копіюємо у OneDrive-теку
    # САМОГО користувача (os.environ["OneDrive"] на цій-таки машині) - жодного
    # нового акаунта/API/токена, лише локальний файловий запис, який
    # OneDrive-клієнт сам вивантажує в хмару. Best-effort: якщо OneDrive не
    # налаштований на цій машині, просто тихо нічого не робимо.
    _ONEDRIVE_BACKUP_LIMIT = 10

    def _mirror_backup_to_onedrive(self, snapshot_path, glob_pattern="app_data_*"):
        onedrive_root = os.environ.get("OneDrive")
        if not onedrive_root:
            return
        target_dir = Path(onedrive_root) / "AI_Automation_Backups"
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

    def _format_datetime_for_display(self, value, format_key=None):
        if not isinstance(value, datetime):
            return str(value or "")
        key = format_key or self.display_settings.get("date_format")
        weekday = RU_WEEKDAYS[value.weekday()]
        month_name = RU_MONTHS[value.month]
        if key == "yyyy.mm.dd_dow_hhmm":
            return f"{value:%Y.%m.%d} {weekday} {value:%H:%M}"
        if key == "dd.mm.yyyy_hhmm":
            return f"{value:%d.%m.%Y %H:%M}"
        if key == "yyyy-mm-dd_hhmm":
            return f"{value:%Y-%m-%d %H:%M}"
        if key == "dd_slash_mm_slash_yyyy_hhmm":
            return f"{value:%d/%m/%Y %H:%M}"
        if key == "dd_month_yyyy_hhmm":
            return f"{value.day:02d} {month_name} {value:%Y %H:%M}"
        if key == "dow_dd.mm.yyyy_hhmm":
            return f"{weekday} {value:%d.%m.%Y %H:%M}"
        if key == "yyyy.mm.dd_hhmmss":
            return f"{value:%Y.%m.%d %H:%M:%S}"
        if key == "iso_minutes":
            return f"{value:%Y-%m-%dT%H:%M}"
        if key == "hhmm_dd.mm.yyyy":
            return f"{value:%H:%M %d.%m.%Y}"
        if key == "dd.mm.yy_hhmm":
            return f"{value:%d.%m.%y %H:%M}"
        return f"{value:%Y.%m.%d} {weekday} {value:%H:%M}"

    def _format_action_log_time(self, created_at):
        try:
            value = datetime.fromisoformat(str(created_at))
        except (TypeError, ValueError):
            return str(created_at or "")
        return self._format_datetime_for_display(value)

    # Задача користувача: "час останнього..." має бути відносним, не голою
    # датою - сьогодні/вчора замість числа, і чим давніше, тим грубіше
    # округлення (тиждень -> місяць -> рік), щоб давні дати не муляли око
    # точністю, яка вже не має значення. Місяць=30/рік=365 днів - той самий
    # наближений поріг, що вже використовує _sales_period_from_text для
    # рухомого "месяц". НЕ чіпає _format_action_log_time/_format_datetime_for_display
    # (Журнал дій і далі показує повну дату завжди, як і зараз).
    def _format_last_seen(self, created_at):
        try:
            value = datetime.fromisoformat(str(created_at))
        except (TypeError, ValueError):
            return str(created_at or "")
        days_ago = (datetime.now().date() - value.date()).days
        if days_ago == 0:
            return f"{self._t('сьогодні')}, {value:%H:%M}"
        if days_ago == 1:
            return f"{self._t('вчора')}, {value:%H:%M}"
        if days_ago < 7:
            return self._format_action_log_time(created_at)
        if days_ago < 30:
            return self._t("більше неділі")
        if days_ago < 365:
            return self._t("більше місяця")
        return self._t("більше року")

    # --- Журнал дій: показ, деталі (read-only, синхронізовано з client_app.py) ---
    # Задача користувача (2026-08-15): "синхронізація" - той самий принцип,
    # що й _refresh_personnel вище: реальні дії тягнуться через тунель
    # напряму з client_app.py, не з власної порожньої локальної бази.
    # "Видалити"/"Очистити журнал" прибрані разом з відповідними методами -
    # керування журналом тепер лише там, де він реально ведеться.
    # Реальний баг (2026-08-15, живий продакшн): "журнал дуже довго
    # запускався... і закрити не міг, залагало" - fetch_remote_action_log
    # (urllib, timeout=10) викликався ПРЯМО тут, на головному Tk-потоці -
    # мережевий запит через тунель БЛОКУВАВ увесь event loop на весь час
    # відповіді (секунди, іноді й усі 10) - вікно не могло ні домалюватись,
    # ні відреагувати на клік "Закрити"/X. Той самий клас багу, що вже
    # виправлений скрізь інде в цьому файлі (_on_refresh_excel_clicked,
    # _notify_role_change тощо) - тепер запит іде у фоновому потоці,
    # результат повертається через _run_on_main_thread.
    def _refresh_action_log(self):
        self._clear_frame(self.action_log_list_frame)
        tk.Label(
            self.action_log_list_frame, text=self._t("Завантаження..."), anchor="w",
        ).pack(anchor="w", fill="x", pady=4)
        self._apply_theme(self.action_log_list_frame)

        # Реальний баг (аудит коду, 2026-08-15): без лічильника поколінь
        # запізніла відповідь РАНІШЕ розпочатого (але повільнішого через
        # мережеву затримку тунелю) запиту могла б прийти ПІСЛЯ свіжішого й
        # тихо перезаписати щойно оновлені рядки застарілими - можливо і
        # для "Обновити", і для повторного відкриття вікна.
        self._action_log_refresh_generation += 1
        generation = self._action_log_refresh_generation

        def worker():
            rows = remote_control_client.fetch_remote_action_log(limit=200)
            self._run_on_main_thread(lambda: self._apply_action_log_rows(rows, generation))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_action_log_rows(self, rows, generation=None):
        if getattr(self, "action_log_list_frame", None) is None:
            return
        if generation is not None and generation != self._action_log_refresh_generation:
            return
        self._clear_frame(self.action_log_list_frame)
        if rows is None:
            tk.Label(
                self.action_log_list_frame,
                text=self._t("Не вдалось отримати журнал з client_app.py. Перевірте з'єднання."),
                fg="#d1242f",
                anchor="w",
            ).pack(anchor="w", fill="x", pady=4)
            self._apply_theme(self.action_log_list_frame)
            return
        self._remote_action_log_rows = {row[0]: row for row in rows}
        if not rows:
            tk.Label(
                self.action_log_list_frame,
                text=self._t("Журнал действий пока пуст."),
                anchor="w",
            ).pack(anchor="w", fill="x", pady=4)
            self._apply_theme(self.action_log_list_frame)
            return

        for log_id, action_type, details_json, created_at in rows:
            details = self._parse_action_log_details(details_json)
            summary = self._action_log_summary(action_type, details)
            row = tk.Frame(self.action_log_list_frame)
            row.pack(fill="x", pady=2)

            values = (
                self._short_text(summary["user"], 20),
                self._short_text(summary["action"], 18),
                self._short_text(summary["status"], 16),
                self._short_text(self._format_action_log_time(created_at), 22),
                self._short_text(summary["text"], 46),
            )
            widths = (20, 18, 16, 22, 46)
            for value, width in zip(values, widths):
                tk.Label(row, text=value, width=width, anchor="w").pack(side="left")

            detail_button = tk.Button(
                row,
                text=self._t("Детально"),
                command=lambda item_id=log_id: self.open_action_log_details(item_id),
            )
            detail_button.pack(side="left", padx=(8, 0))
        self._apply_theme(self.action_log_list_frame)

    def _refresh_work_log(self):
        self._clear_frame(self.work_log_list_frame)
        rows = self.store.list_work_log(200)
        if not rows:
            tk.Label(
                self.work_log_list_frame,
                text=self._t("Журнал виконаних робіт поки порожній."),
                anchor="w",
            ).pack(anchor="w", fill="x", pady=4)
            return

        for log_id, title, summary, benefit, future_impact, created_at in rows:
            row = tk.Frame(self.work_log_list_frame)
            row.pack(fill="x", pady=2)

            values = (
                self._short_text(self._format_action_log_time(created_at), 20),
                self._short_text(title, 30),
                self._short_text(summary.replace("\n", " "), 50),
            )
            widths = (20, 30, 50)
            for value, width in zip(values, widths):
                tk.Label(row, text=value, width=width, anchor="w").pack(side="left")

            detail_button = tk.Button(
                row,
                text=self._t("Детально"),
                command=lambda item_id=log_id: self.open_work_log_details(item_id),
            )
            detail_button.pack(side="left", padx=(8, 0))

            delete_button = tk.Button(
                row,
                text=self._t("Видалити"),
                command=lambda item_id=log_id: self.delete_work_log_record(item_id),
            )
            delete_button.pack(side="left", padx=(4, 0))

    def delete_work_log_record(self, log_id):
        if not messagebox.askyesno(
            self._t("Журнал виконаних робіт"),
            self._t("Видалити запис #{value}?").format(value=log_id),
            parent=self.root,
        ):
            return
        self.store.delete_work_log_entry(log_id)
        self._refresh_work_log()

    def clear_work_log(self):
        if not messagebox.askyesno(
            self._t("Журнал виконаних робіт"),
            self._t("Видалити всі записи журналу? Цю дію не можна скасувати."),
            parent=self.root,
        ):
            return
        self.store.clear_work_log()
        self._refresh_work_log()

    def open_work_log_details(self, log_id):
        existing = self._work_log_detail_windows.get(log_id)
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            existing.focus_force()
            return

        row = self.store.get_work_log_entry(log_id)
        if not row:
            messagebox.showinfo(self._t("Журнал виконаних робіт"), self._t("Запис не знайдено."))
            return

        log_id, title, summary, benefit, future_impact, created_at = row
        window = tk.Toplevel(self.root)
        window.title(self._t("Деталі запису #{value}").format(value=log_id))
        window.geometry("760x560")
        self._work_log_detail_windows[log_id] = window

        top = tk.Frame(window)
        top.pack(side="top", fill="x", padx=12, pady=8)
        tk.Label(
            top,
            text=f"{self._format_action_log_time(created_at)} | {title}",
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        tk.Button(
            top, text=self._t("Закрити"),
            command=lambda: self._close_work_log_detail_window(log_id, window),
        ).pack(side="right")
        window.protocol("WM_DELETE_WINDOW", lambda: self._close_work_log_detail_window(log_id, window))
        window.bind("<Escape>", lambda event: self._close_work_log_detail_window(log_id, window))

        text_widget = tk.Text(window, wrap="word")
        scrollbar = ttk.Scrollbar(window, orient="vertical", command=text_widget.yview)
        text_widget.configure(yscrollcommand=scrollbar.set)
        text_widget.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=(0, 12))
        scrollbar.pack(side="right", fill="y", padx=(0, 12), pady=(0, 12))

        lines = [self._t("Що зроблено:"), summary, ""]
        if benefit:
            lines.extend([self._t("Що це дає:"), benefit, ""])
        if future_impact:
            lines.extend([self._t("На що вплине в майбутньому:"), future_impact])
        text_widget.insert("1.0", "\n".join(lines))
        text_widget.configure(state="normal")
        self._center_window(window, width=760, height=560)

    def _close_work_log_detail_window(self, log_id, window):
        if self._work_log_detail_windows.get(log_id) is window:
            del self._work_log_detail_windows[log_id]
        window.destroy()

    def _parse_action_log_details(self, details_json):
        try:
            data = json.loads(details_json) if details_json else {}
        except json.JSONDecodeError:
            data = {"raw": details_json}
        return data if isinstance(data, dict) else {"raw": data}

    def _action_log_summary(self, action_type, details):
        telegram = details.get("telegram") or {}
        user = self._action_log_user_label(telegram)
        status = self._action_log_status_label(details.get("status", ""))
        command = self._action_log_action_label(details.get("recognized_command") or action_type)
        text = details.get("incoming_text") or ""
        if not text and isinstance(details.get("reply"), dict):
            text = details["reply"].get("caption") or details["reply"].get("text") or ""
        return {
            "user": user,
            "status": status,
            "action": command,
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
            "success": "Выполнено",
            "waiting": "Ожидает ответа",
            "error": "Ошибка",
            "cancelled": "Отменено",
            "unknown": "Не распознано",
        }
        return labels.get(str(status or ""), str(status or "Неизвестно"))

    def _action_log_action_label(self, action):
        labels = {
            "telegram_message": "Сообщение Telegram",
            "add_income": "Приход",
            "stock_balance": "Остаток",
            "cancel_operation": "Отмена",
            "bot_selection": "Выбор бота",
            "bot_explanation": "Пояснение режимов",
            "claude_key_saved": "Ключ Claude сохранен",
            "claude_key_rejected": "Ключ Claude не сохранен",
            "claude_key_help": "Инструкция Claude API",
            "claude_chat": "Разговор с Claude",
            "stock_income_history": "История прихода",
            "status": "Статус",
            "start": "Старт",
            "help": "Помощь",
            "sheets": "Список листов",
            "first": "Первые строки",
            "unknown": "Не распознано",
        }
        return labels.get(str(action or ""), str(action or "Неизвестно"))

    def _action_log_reply_label(self, reply):
        if not isinstance(reply, dict):
            return str(reply or "")
        if reply.get("type") == "document":
            path = reply.get("path", "")
            caption = reply.get("caption", "")
            return "\n".join(
                part
                for part in [
                    "Тип ответа: файл",
                    f"Файл: {path}" if path else "",
                    f"Подпись: {caption}" if caption else "",
                ]
                if part
            )
        return str(reply.get("text", ""))

    def _format_action_log_details(self, log_id, action_type, created_at, details):
        telegram = details.get("telegram") or {}
        reply = details.get("reply") or {}
        pending_before = details.get("pending_before")
        pending_after = details.get("pending_after")
        lines = [
            f"Запись журнала: #{log_id}",
            f"Время: {self._format_action_log_time(created_at)}",
            f"Пользователь: {self._action_log_user_label(telegram)}",
            f"Telegram user_id: {telegram.get('user_id', '')}",
            f"Telegram chat_id: {telegram.get('chat_id', '')}",
            "",
            "Запрос пользователя:",
            details.get("incoming_text") or "",
            "",
            f"Действие: {self._action_log_action_label(details.get('recognized_command') or action_type)}",
            f"Статус: {self._action_log_status_label(details.get('status'))}",
            f"Режим обработки: {self._request_processing_mode_title(details.get('mode'))}",
            f"Версия pipeline: {details.get('pipeline_version', '')}",
            f"Время обработки: {details.get('duration_ms', '')} мс",
        ]
        if pending_before:
            lines.extend(
                [
                    "",
                    "Операция до сообщения:",
                    f"Тип: {pending_before.get('operation_type', '')}",
                    f"Этап: {pending_before.get('status', '')}",
                ]
            )
        if pending_after:
            lines.extend(
                [
                    "",
                    "Операция после сообщения:",
                    f"Тип: {pending_after.get('operation_type', '')}",
                    f"Этап: {pending_after.get('status', '')}",
                ]
            )
        lines.extend(
            [
                "",
                "Ответ пользователю:",
                self._action_log_reply_label(reply),
            ]
        )
        if details.get("error"):
            lines.extend(["", "Ошибка:", str(details.get("error"))])

        lines.extend(
            [
                "",
                "Технические данные:",
                json.dumps(details, ensure_ascii=False, indent=2),
            ]
        )
        return "\n".join(lines)

    def _short_text(self, text, max_length):
        text = str(text or "")
        return text if len(text) <= max_length else text[: max_length - 1] + "…"

    def _center_window(self, window, width=None, height=None):
        # Задача користувача: темна тема - _center_window викликається як
        # ОСТАННІЙ крок практично КОЖНОГО діалогу/спливаючого вікна в
        # програмі (23 місця) - єдина спільна точка, звідки можна
        # перефарбувати щойно побудоване вікно, не чіпаючи всі 23 виклики
        # tk.Toplevel окремо.
        self._apply_theme(window)
        self.root.update_idletasks()
        window.update_idletasks()
        width = width or window.winfo_width()
        height = height or window.winfo_height()
        root_x = self.root.winfo_rootx()
        root_y = self.root.winfo_rooty()
        root_width = self.root.winfo_width()
        root_height = self.root.winfo_height()
        x = root_x + max((root_width - width) // 2, 0)
        y = root_y + max((root_height - height) // 2, 0)
        window.geometry(f"{width}x{height}+{x}+{y}")

    def open_action_log_details(self, log_id):
        existing = self._action_log_detail_windows.get(log_id)
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            existing.focus_force()
            return

        # Задача користувача (2026-08-15): "синхронізація" - рядок уже
        # прийшов через тунель у _refresh_action_log вище (закешований у
        # _remote_action_log_rows) - повторний round-trip тут не потрібен.
        row = self._remote_action_log_rows.get(log_id)
        if not row:
            messagebox.showinfo(self._t("Журнал действий"), self._t("Запись не найдена."))
            return

        log_id, action_type, details_json, created_at = row
        details = self._parse_action_log_details(details_json)
        window = tk.Toplevel(self.root)
        window.title(self._t("Деталі журналу дій #{value}").format(value=log_id))
        window.geometry("760x560")
        self._action_log_detail_windows[log_id] = window

        top = tk.Frame(window)
        top.pack(side="top", fill="x", padx=12, pady=8)
        tk.Label(
            top,
            text=(
                f"{self._format_action_log_time(created_at)} | "
                f"{self._action_log_action_label(details.get('recognized_command') or action_type)}"
            ),
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        tk.Button(
            top, text=self._t("Закрыть"),
            command=lambda: self._close_action_log_detail_window(log_id, window),
        ).pack(side="right")
        window.protocol("WM_DELETE_WINDOW", lambda: self._close_action_log_detail_window(log_id, window))
        window.bind("<Escape>", lambda event: self._close_action_log_detail_window(log_id, window))

        text_widget = tk.Text(window, wrap="word")
        scrollbar = ttk.Scrollbar(window, orient="vertical", command=text_widget.yview)
        text_widget.configure(yscrollcommand=scrollbar.set)
        text_widget.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=(0, 12))
        scrollbar.pack(side="right", fill="y", padx=(0, 12), pady=(0, 12))
        text_widget.insert(
            "1.0",
            self._format_action_log_details(log_id, action_type, created_at, details),
        )
        text_widget.configure(state="normal")
        self._center_window(window, width=760, height=560)

    def _close_action_log_detail_window(self, log_id, window):
        if self._action_log_detail_windows.get(log_id) is window:
            del self._action_log_detail_windows[log_id]
        window.destroy()

    # --- Команди бота і персонал (список Telegram-користувачів) ---
    def _refresh_commands(self):
        self._clear_frame(self.commands_list_frame)
        commands = self.store.list_commands()
        if not commands:
            tk.Label(
                self.commands_list_frame,
                text=self._t("Команд поки немає."),
                anchor="w",
            ).pack(anchor="w", fill="x", pady=4)
            return

        for index, (command_id, code, title, description, enabled) in enumerate(commands, start=1):
            row = tk.Frame(self.commands_list_frame)
            row.pack(anchor="w", fill="x", pady=4)

            state_text = "" if enabled else self._t(" [вимкнено]")
            description_text = f" — {description}" if description else ""
            label = tk.Label(
                row,
                text=f"{index}. {title} ({code}){state_text}{description_text}",
                anchor="w",
                justify="left",
            )
            label.pack(side="left", fill="x", expand=True)

            edit_button = tk.Button(
                row,
                text=self._t("ред"),
                width=5,
                fg=self._chip_text_color(),
                command=lambda item_id=command_id, item_title=title: self.open_command_alias_editor(
                    item_id,
                    item_title,
                ),
                **self._chip_button_style(),
            )
            edit_button.pack(side="right", padx=(8, 0))

            delete_button = tk.Button(
                row,
                text=self._t("x"),
                width=3,
                fg="#d1242f",
                command=lambda item_id=command_id, item_title=title: self.delete_command(item_id, item_title),
                **self._chip_button_style(),
            )
            delete_button.pack(side="right", padx=(8, 0))

    def add_command_dialog(self):
        code = simpledialog.askstring(self._t("Нова команда"), self._t("Код команди латиницею, наприклад show_stock:"))
        if not code:
            return
        code = code.strip()
        title = simpledialog.askstring(self._t("Нова команда"), self._t("Назва команди:"))
        if not title:
            return
        description = simpledialog.askstring(
            self._t("Нова команда"),
            self._t("Опис команди (необов'язково):"),
        ) or ""

        try:
            self.store.add_command(code, title.strip(), description.strip())
        except sqlite3.IntegrityError:
            messagebox.showerror(self._t("Команди"), self._t("Команда з таким кодом уже існує."))
            return
        self._refresh_commands()

    def open_command_alias_editor(self, command_id, title):
        # Аудит коду: раніше подвійний клік/повторний виклик відкривав
        # ДРУГЕ вікно для тієї самої команди — той самий винфо_ексістс-
        # трекінг, що вже є в журналах/персоналі, тут за command_id.
        existing_entry = self._command_alias_editor_windows.get(command_id)
        if existing_entry is not None and existing_entry[0].winfo_exists():
            existing_entry[0].deiconify()
            existing_entry[0].lift()
            existing_entry[0].focus_force()
            return

        editor = tk.Toplevel(self.root)
        editor.title(self._t("Команда: {value}").format(value=title))
        editor.geometry("520x420")

        top_bar = tk.Frame(editor)
        top_bar.pack(side="top", fill="x", padx=12, pady=10)

        tk.Label(
            top_bar,
            text=self._t("Команда: {value}").format(value=title),
            font=("Segoe UI", 12, "bold"),
        ).pack(side="left")

        content = tk.Frame(editor)
        content.pack(side="top", fill="both", expand=True, padx=16, pady=(0, 16))

        list_frame = self._create_scrollable_list(content)
        # Свіжий пере-аудит (New-Minor #6): зберігаємо title/list_frame
        # поруч із самим Toplevel - інакше save_style() не мав би чим
        # викликати _refresh_command_alias_editor для вже відкритого вікна.
        self._command_alias_editor_windows[command_id] = (editor, title, list_frame)

        add_button = tk.Button(
            top_bar,
            text=self._t("+"),
            width=4,
            command=lambda: self.add_command_alias_dialog(command_id, title, editor, list_frame),
            fg="#1a7f37",
            **self._chip_button_style(),
        )
        add_button.pack(side="right")

        editor.protocol(
            "WM_DELETE_WINDOW", lambda: self._close_command_alias_editor(command_id, editor)
        )
        editor.bind("<Escape>", lambda event: self._close_command_alias_editor(command_id, editor))
        self._refresh_command_alias_editor(command_id, title, editor, list_frame)
        self._apply_theme(editor)

    def _close_command_alias_editor(self, command_id, editor):
        entry = self._command_alias_editor_windows.get(command_id)
        if entry is not None and entry[0] is editor:
            del self._command_alias_editor_windows[command_id]
        editor.destroy()

    def _refresh_command_alias_editor(self, command_id, title, editor, list_frame):
        if not editor.winfo_exists():
            return
        self._clear_frame(list_frame)
        aliases = self.store.list_command_aliases(command_id)
        if not aliases:
            tk.Label(
                list_frame,
                text=self._t("Слова ще не додані."),
                anchor="w",
                fg="#666666",
            ).pack(anchor="w", fill="x", pady=4)
            return

        for index, (alias_id, phrase) in enumerate(aliases, start=1):
            row = tk.Frame(list_frame)
            row.pack(anchor="w", fill="x", pady=4)

            tk.Label(
                row,
                text=f"{index}. {phrase}",
                anchor="w",
                justify="left",
            ).pack(side="left", fill="x", expand=True)

            edit_button = tk.Button(
                row,
                text=self._t("ред"),
                width=5,
                fg=self._chip_text_color(),
                command=lambda item_id=alias_id, old_phrase=phrase: self.edit_command_alias_dialog(
                    command_id,
                    title,
                    item_id,
                    old_phrase,
                    editor,
                    list_frame,
                ),
                **self._chip_button_style(),
            )
            edit_button.pack(side="right", padx=(8, 0))

            delete_button = tk.Button(
                row,
                text=self._t("x"),
                width=3,
                fg="#d1242f",
                command=lambda item_id=alias_id: self.delete_command_alias(
                    command_id,
                    title,
                    item_id,
                    editor,
                    list_frame,
                ),
                **self._chip_button_style(),
            )
            delete_button.pack(side="right", padx=(8, 0))

    def add_command_alias_dialog(self, command_id, title, editor=None, list_frame=None):
        phrase = simpledialog.askstring(self._t("Слово команди"), self._t("Додати слово або фразу для '{value}':").format(value=title))
        if not phrase:
            return
        try:
            self.store.add_command_alias(command_id, phrase)
        except sqlite3.IntegrityError:
            messagebox.showerror(self._t("Команди"), self._t("Таке слово або фраза вже прив'язані до команди."))
            return
        if editor and list_frame:
            self._refresh_command_alias_editor(command_id, title, editor, list_frame)

    def edit_command_alias_dialog(self, command_id, title, alias_id, old_phrase, editor, list_frame):
        phrase = simpledialog.askstring(
            self._t("Слово команди"),
            self._t("Змінити слово або фразу:"),
            initialvalue=old_phrase,
        )
        if not phrase:
            return
        try:
            self.store.update_command_alias(alias_id, phrase)
        except sqlite3.IntegrityError:
            messagebox.showerror(self._t("Команди"), self._t("Таке слово або фраза вже прив'язані до команди."))
            return
        self._refresh_command_alias_editor(command_id, title, editor, list_frame)

    def delete_command_alias(self, command_id, title, alias_id, editor=None, list_frame=None):
        if not messagebox.askyesno(self._t("Команди"), self._t("Видалити це слово з команди?")):
            return
        self.store.delete_command_alias(alias_id)
        if editor and list_frame:
            self._refresh_command_alias_editor(command_id, title, editor, list_frame)

    def delete_command(self, command_id, title):
        if not messagebox.askyesno(self._t("Команди"), self._t("Видалити команду '{value}'?").format(value=title)):
            return
        self.store.delete_command(command_id)
        # Свіжий пере-аудит (New-Notable #3): якщо редактор синонімів цієї
        # команди відкритий (вікно не модальне), він лишався б живим і
        # вказував на щойно видалений command_id - наступна спроба додати
        # синонім давала б сплутувальну помилку "вже прив'язано" замість
        # реальної причини (команди більше немає).
        entry = self._command_alias_editor_windows.get(command_id)
        if entry is not None and entry[0].winfo_exists():
            self._close_command_alias_editor(command_id, entry[0])
        self._refresh_commands()

    # Задача користувача (2026-08-15): "синхронізація" - раніше тут читалась
    # ВЛАСНА, окрема й порожня локальна база (gui.py більше не хостить
    # бота) - тепер реальні дані тягнуться через тунель напряму з
    # client_app.py (remote_control_client.fetch_remote_personnel,
    # read-only, той самий принцип, що й /control/status). None означає
    # "не вдалось отримати" (сервер офлайн/мережевий збій) - показуємо це
    # ЯВНО, а не порожній список (той самий клас багу, що вже виправлений
    # для статусу сервера - "не бачив нічого" не має виглядати як "нема
    # персоналу").
    # Той самий блокуючий-мережевий-запит-на-головному-потоці баг, що й
    # _refresh_action_log вище ("персонал те саме" - користувач) - фікс
    # ідентичний: fetch у фоновому потоці, побудова рядків - через
    # _run_on_main_thread.
    def _refresh_personnel(self):
        # Вікно "Персонал" тепер ліниво-побудоване (tk.Toplevel, як і
        # "Журнали") - personnel_list_frame існує лише ПІСЛЯ першого
        # відкриття; викликається безумовно й з "Формат кнопок" (save_style),
        # тому потрібен захист від відсутнього вікна.
        if getattr(self, "personnel_list_frame", None) is None:
            return
        self._clear_frame(self.personnel_list_frame)
        tk.Label(self.personnel_list_frame, text=self._t("Завантаження..."), anchor="w").pack(
            anchor="w", fill="x", pady=4
        )
        self._apply_theme(self.personnel_list_frame)

        # Той самий guard від застарілої відповіді, що й у _refresh_action_log.
        self._personnel_refresh_generation += 1
        generation = self._personnel_refresh_generation

        def worker():
            users = remote_control_client.fetch_remote_personnel()
            self._run_on_main_thread(lambda: self._apply_personnel_rows(users, generation))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_personnel_rows(self, users, generation=None):
        if getattr(self, "personnel_list_frame", None) is None:
            return
        if generation is not None and generation != self._personnel_refresh_generation:
            return
        # Задача користувача (2026-08-17): кешуємо СИРИЙ список - сортування/
        # фільтр/повторний рендер нижче більше НЕ тягнуть дані через тунель
        # заново, лише перемальовують з того, що вже маємо.
        self._personnel_users_cache = users
        self._render_personnel_list()

    # Задача користувача (2026-08-17): "вирівняй це в домашній версії. ролі
    # не мають їздити. мають стаціонарно стояти. також і час відвідування.
    # додай сортування за часом та за алфавітом. фільтри мають бути як в
    # данних формі." - обраний варіант (5 з показаних): заголовки-тригери
    # в шапці таблиці (Ім'я - сортування за алфавітом, Роль - фільтр
    # списком, Час - сортування за часом), клікабельний бейдж ролі в
    # самому рядку (зміна ролі людині) лишається без змін, як і раніше.
    #
    # Реальна причина попереднього фіксу (тільки width= на pack()) не
    # спрацювала повністю: кожен рядок був ОКРЕМИМ tk.Frame з pack() -
    # Tk рахує розкладку кожного pack()-контейнера незалежно, тож навіть
    # однакові width= не гарантують однакову позицію між РІЗНИМИ Frame.
    # grid() усередині ОДНОГО спільного personnel_list_frame - усі рядки й
    # шапка тепер справжні комірки однієї таблиці, тому колонки
    # структурно не можуть розійтись.
    def _render_personnel_list(self):
        self._clear_frame(self.personnel_list_frame)
        users = self._personnel_users_cache
        if users is None:
            tk.Label(
                self.personnel_list_frame,
                text=self._t("Не вдалось отримати персонал з client_app.py. Перевірте з'єднання."),
                fg="#d1242f",
                anchor="w",
            ).pack(anchor="w", fill="x", pady=4)
            self._apply_theme(self.personnel_list_frame)
            return
        if not users:
            tk.Label(
                self.personnel_list_frame,
                text=self._t("Користувачів поки немає."),
                anchor="w",
            ).pack(anchor="w", fill="x", pady=4)
            self._apply_theme(self.personnel_list_frame)
            return

        theme = self._theme()
        self.personnel_list_frame.grid_columnconfigure(0, weight=1)
        self.personnel_list_frame.grid_columnconfigure(1, weight=0)
        self.personnel_list_frame.grid_columnconfigure(2, weight=0)

        self._build_personnel_header_row(theme)

        # Задача користувача: "тонший рядок, роль-чіп" (обраний варіант A) -
        # тонша картка (менший pady, приглушений фон рядка) + роль винесена
        # в окрему кольорову плашку замість "— Роль —" суцільним текстом.
        visible_users = self._personnel_filtered_sorted_users(users)
        for index, (user_id, telegram_id, username, full_name, role, last_seen_at) in enumerate(visible_users, start=1):
            display_name = full_name or username or str(telegram_id)
            username_text = f" @{username}" if username else ""
            normalized_role = perm.normalize_role(role)
            role_label = perm.ROLE_LABELS.get(normalized_role, role)
            role_bg, role_fg = self._ROLE_CHIP_COLORS.get(normalized_role, self._ROLE_CHIP_COLORS["guest"])

            label = tk.Label(
                self.personnel_list_frame,
                text=f"{index}. {display_name}{username_text} — ID: {telegram_id}",
                anchor="w",
                justify="left",
                bg=theme["panel_bg"],
            )
            label.grid(row=index, column=0, sticky="ew", padx=(6, 0), pady=5)

            # Задача користувача (2026-08-16): "додай змогу редагувати ролі
            # тут теж... зміна ролі в мене - зміна ролі в клієнті" - бейдж
            # тепер клікабельний: відкриває список ролей, вибір одразу шле
            # HTTP push у client_app.py (єдине джерело правди - див.
            # remote_control_client.set_remote_role/_on_role_menu_selected
            # нижче). "▾" - той самий сигнал "тут є вибір", що вже й на
            # кнопці теми ("Тёмная"/"Светлая"). Ця функціональність
            # (клікабельний бейдж міняє РОЛЬ) навмисно НЕ змінюється -
            # окремий заголовок "Роль ▾" вище лише ФІЛЬТРУЄ список.
            chip = tk.Label(
                self.personnel_list_frame, text=f"{role_label} ▾", font=("Segoe UI", 8, "bold"),
                bg=role_bg, fg=role_fg, padx=8, pady=2, cursor="hand2",
                width=self._ROLE_CHIP_WIDTH, anchor="center",
            )
            chip.grid(row=index, column=1, padx=8)
            chip.bind(
                "<Button-1>",
                lambda event, uid=user_id, r=normalized_role, w=chip: self._open_role_menu(w, uid, r),
            )

            # Задача користувача: "час останнього відправленого повідомлення
            # користувачем в чат.. має бути ненав'язчиво" - малий сірий
            # текст, не впадає в очі поруч з рештою рядка. Порожньо, якщо
            # ще ніколи не писав (last_seen_at NULL).
            last_seen_text = self._format_last_seen(last_seen_at) if last_seen_at else ""
            last_seen_label = tk.Label(
                self.personnel_list_frame,
                text=last_seen_text,
                font=("Segoe UI", 8),
                fg="#8c959f",
                bg=theme["panel_bg"],
                width=self._LAST_SEEN_WIDTH, anchor="e",
            )
            last_seen_label.grid(row=index, column=2, sticky="e", padx=(8, 6))
        self._apply_theme(self.personnel_list_frame)

    def _personnel_sort_arrow(self, field):
        if self._personnel_sort_field != field:
            return ""
        return " ↓" if self._personnel_sort_reverse else " ↑"

    def _build_personnel_header_row(self, theme):
        name_column_title = self._t("Ім'я")
        name_header = tk.Label(
            self.personnel_list_frame,
            text=f"{name_column_title}{self._personnel_sort_arrow('name')}",
            font=("Segoe UI", 8, "bold"), fg="#8c959f", bg=theme["panel_bg"],
            cursor="hand2", anchor="w",
        )
        name_header.grid(row=0, column=0, sticky="w", padx=(6, 0), pady=(0, 6))
        name_header.bind("<Button-1>", lambda event: self._toggle_personnel_sort("name"))

        if self._personnel_role_filter:
            role_header_text = f"{self._t('Роль')}: {perm.ROLE_LABELS.get(self._personnel_role_filter, self._personnel_role_filter)} ▾"
        else:
            role_header_text = f"{self._t('Роль')} ▾"
        role_header = tk.Label(
            self.personnel_list_frame, text=role_header_text,
            font=("Segoe UI", 8, "bold"), fg="#8c959f", bg=theme["panel_bg"],
            cursor="hand2", anchor="center", width=self._ROLE_CHIP_WIDTH,
        )
        role_header.grid(row=0, column=1, padx=8, pady=(0, 6))
        role_header.bind("<Button-1>", lambda event, w=role_header: self._open_personnel_role_filter_menu(w))

        time_header = tk.Label(
            self.personnel_list_frame,
            text=f"{self._t('Час')}{self._personnel_sort_arrow('time')}",
            font=("Segoe UI", 8, "bold"), fg="#8c959f", bg=theme["panel_bg"],
            cursor="hand2", anchor="e", width=self._LAST_SEEN_WIDTH,
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
        theme = self._theme()
        menu = tk.Menu(
            header_widget, tearoff=0,
            bg=theme["panel_bg"], fg=theme["fg"],
            activebackground=theme["select_bg"], activeforeground=theme["fg"],
            selectcolor=theme["fg"], bd=0,
        )
        filter_var = tk.StringVar(value=self._personnel_role_filter or "")
        menu.add_radiobutton(
            label=self._t("Всі"), variable=filter_var, value="",
            command=lambda: self._set_personnel_role_filter(None),
        )
        for role in perm.ROLES:
            menu.add_radiobutton(
                label=perm.ROLE_LABELS[role], variable=filter_var, value=role,
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
            # ISO-8601 рядки порівнюються лексикографічно = хронологічно
            # (той самий прийом, що вже перевірений у github_releases.py
            # для published_at). Порожньо/None (ще ніколи не писав) -
            # найменше значення, природно опиняється скраю списку.
            result.sort(key=lambda u: u[5] or "", reverse=self._personnel_sort_reverse)
        return result

    # Задача користувача (2026-08-16): "додай змогу редагувати ролі тут
    # теж" - tk.Menu (не власний Toplevel-попап - нативний dismiss-on-
    # click-away/Escape вже вбудований, тут це важливіше за піксель-
    # ідеальний вигляд) розфарбований під поточну тему, спливає прямо під
    # бейджем. add_radiobutton - той самий "позначено поточне" ефект, що й
    # у мокапі, без ручної побудови галочки.
    def _open_role_menu(self, chip_widget, user_id, current_role):
        theme = self._theme()
        menu = tk.Menu(
            chip_widget, tearoff=0,
            bg=theme["panel_bg"], fg=theme["fg"],
            activebackground=theme["select_bg"], activeforeground=theme["fg"],
            selectcolor=theme["fg"], bd=0,
        )
        role_var = tk.StringVar(value=current_role)
        for role in perm.ROLES:
            menu.add_radiobutton(
                label=perm.ROLE_LABELS[role], variable=role_var, value=role,
                command=lambda r=role: self._on_role_menu_selected(user_id, current_role, r),
            )
        x = chip_widget.winfo_rootx()
        y = chip_widget.winfo_rooty() + chip_widget.winfo_height()
        menu.tk_popup(x, y)

    # Реальна знахідка (аудит коду, 2026-08-16): раніше синхронний виклик
    # на головному потоці (той самий "компроміс", що й нижче в
    # _on_remote_command_clicked) - при повільному/недоступному тунелі
    # блокував вікно на весь timeout (10с). Тепер фон-потік +
    # _run_on_main_thread, guard-прапорець замінює "фрiз" як природний
    # захист від подвійного кліку.
    def _on_role_menu_selected(self, user_id, old_role, new_role):
        if new_role == old_role:
            return
        if self._remote_role_change_in_progress:
            return
        self._remote_role_change_in_progress = True

        def worker():
            error = None
            try:
                remote_control_client.set_remote_role(user_id, new_role)
            except urllib.error.HTTPError as exc:
                # Реальна знахідка (аудит коду, 2026-08-16): str(exc) на
                # HTTPError дає лише "HTTP Error 404: Not Found" -
                # webapp_server вже повертає зрозумілий текст ("Пользователь
                # не найден." тощо) у JSON-тілі відповіді, але воно раніше
                # ніколи не читалось тут.
                detail = exc.read().decode("utf-8", errors="replace")
                try:
                    detail = json.loads(detail).get("error") or detail
                except ValueError:
                    pass
                error = detail
            except Exception as exc:
                error = str(exc)

            def finish():
                self._remote_role_change_in_progress = False
                if error:
                    messagebox.showerror(self._t("Персонал"), error)
                    return
                self._refresh_personnel()

            self._run_on_main_thread(finish)

        threading.Thread(target=worker, daemon=True).start()

    # Роль — випадаючий список (perm.ROLES/ROLE_LABELS), той самий трюк
    # Задача користувача (2026-08-15): "синхронізація" - додавання/
    # редагування/видалення персоналу (_ask_user_form/add_user_dialog/
    # edit_user_dialog/delete_user, і допоміжні _user_role_*) переїхали в
    # client_app.py (де реально живе бот) - тут лишився лише read-only
    # перегляд (_refresh_personnel вище). Ці методи писали у ВЛАСНУ,
    # ніким не читану локальну базу gui.py - видалені повністю, не
    # приховані (жодного виклику більше немає).

    # --- Керування Telegram-ботом з інтерфейсу (запуск/зупинка, токен) ---
    def choose_telegram_token_file(self):
        initial_dir = self.settings.get("last_file_dialog_dir") or "C:\\"
        if not Path(initial_dir).exists():
            initial_dir = "C:\\"

        selected_file = filedialog.askopenfilename(
            title=self._t("Оберіть txt-файл з Telegram-токеном"),
            initialdir=initial_dir,
            filetypes=(("Text files", "*.txt"), ("All files", "*.*")),
        )
        if not selected_file:
            return

        selected_path = Path(selected_file)
        self.settings.set("telegram_token_file", str(selected_path))
        self.settings.set("last_file_dialog_dir", str(selected_path.parent))
        self._update_telegram_settings_labels()
        self.restart_telegram_bot()

    def _update_telegram_settings_labels(self):
        token_file = self.settings.get("telegram_token_file")
        if token_file:
            self.telegram_file_text.set(self._t("Файл токена: {value}").format(value=token_file))
        else:
            self.telegram_file_text.set(self._t("Файл токена ще не вибрано."))

    def _read_telegram_token(self):
        token_file = self.settings.get("telegram_token_file")
        if not token_file:
            return None, self._t("Файл токена ще не вибрано.")

        token_path = Path(token_file)
        if not token_path.exists():
            return None, self._t("Файл токена не знайдено.")

        try:
            lines = token_path.read_text(encoding="utf-8-sig").splitlines()
        except UnicodeDecodeError:
            lines = token_path.read_text(encoding="cp1251").splitlines()
        except OSError as exc:
            return None, self._t("Не удалось прочитать файл токена: {value}").format(value=exc)

        token = next((line.strip() for line in lines if line.strip()), "")
        if not token:
            return None, self._t("Файл токена порожній.")
        return token, None

    # ===== Віддалене керування (2026-08-15) =====
    # Задача користувача: "налаштувати керування із старої програми до
    # нової. відімкни зараз запуск бота в старій та форму щоб не
    # підключало. та щоб статус сервера показувало" - ця програма (gui.py)
    # більше не запускає нічого локально (див. коментар у __init__ про
    # прибрані self.root.after(...)/_start_telegram_from_settings) - лише
    # читає статус НОВОЇ програми (client_app.py, інший ПК) зі спільної
    # теки і шле їй команди через remote_control_client.py. Методи нижче
    # ЄДИНЕ, що тепер керує тими самими 5 кнопками/2 текстовими рядками,
    # що були й раніше (той самий UI, новий сенс).
    def _start_remote_control_polling(self):
        self._remote_control_status = None
        self._remote_control_status_failures = 0
        self._remote_control_tick()

    _REMOTE_CONTROL_POLL_INTERVAL_MS = 15000
    # Задача користувача (2026-08-17): "чому форма не тримається стабільно
    # та часто відлітає (на секунду-дві)" - реальна причина НЕ в самому
    # сервері/тунелі (client_app.py вже має багатоступеневий захист від
    # зайвих перезапусків - _webapp_probe_confirms_down вимагає кілька
    # невдалих проб поспіль перш ніж узагалі щось робити), а в самому
    # ІНДИКАТОРІ тут: кожен тік - ОДИН HTTP-запит через публічний тунель
    # (240 разів/год при 15с інтервалі) - одна випадкова мережева гикавка
    # на ЦЬОМУ запиті (fetch_remote_status повертає None) миттєво показувала
    # "Статус сервера невідомий", хоча сервер увесь час працював нормально;
    # наступний тік за 15с знову показував "Сервер онлайн" - звідси
    # "відлітає на секунду-дві". Той самий принцип підтвердження, що вже й
    # _webapp_probe_confirms_down - показуємо "невідомо" лише після
    # ДЕКІЛЬКОХ поспіль невдалих тіків, не після одного.
    _REMOTE_CONTROL_STATUS_FAILURE_THRESHOLD = 2

    # Задача користувача (2026-08-15): "тепер змінюй це на автоматичне
    # з'єднання між программами" - жодного файлу-посередника більше немає:
    # фіксована адреса (paths.CLOUDFLARED_TUNNEL_HOSTNAME) і фіксований
    # ключ (paths.REMOTE_CONTROL_TOKEN) означають, що gui.py просто стукає
    # напряму щотіку, без жодного ручного налаштування.
    # Реальний баг (аудит коду, 2026-08-15): цей тік раніше робив ОБИДВА
    # HTTP-запити (fetch_remote_status/send_home_heartbeat, timeout=10 кожен)
    # напряму в головному потоці Tk, кожні 15с - той самий клас "заморозки",
    # що вже виправлений для Персоналу/Журналів (див. коментар вище про
    # _run_on_main_thread), тут просто пропущений. При повільному/недоступному
    # тунелі це блокувало б усе вікно до ~20с щоразу.
    def _remote_control_tick(self):
        if self.is_closing:
            return

        def worker():
            status = remote_control_client.fetch_remote_status()
            # Задача користувача: "щоб у клієнта був датчик який слухає завжди
            # домашня программа" - зворотний heartbeat, той самий тік, що й
            # читання чужого статусу вище.
            remote_control_client.send_home_heartbeat()
            self._run_on_main_thread(lambda: self._apply_remote_control_tick_result(status))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_remote_control_tick_result(self, status):
        if status is None:
            self._remote_control_status_failures += 1
            if self._remote_control_status_failures < self._REMOTE_CONTROL_STATUS_FAILURE_THRESHOLD:
                # Один випадковий збій - лишаємо попередній (ще актуальний)
                # напис на екрані, замість блимання "невідомо" на 15с і
                # назад "онлайн". self._remote_control_status теж НЕ
                # оновлюємо - решта коду (кнопки дій тощо) і далі бачить
                # останній справді отриманий статус.
                self.root.after(self._REMOTE_CONTROL_POLL_INTERVAL_MS, self._remote_control_tick)
                return
        else:
            self._remote_control_status_failures = 0
        self._remote_control_status = status
        self._update_remote_control_labels(status)
        self.root.after(self._REMOTE_CONTROL_POLL_INTERVAL_MS, self._remote_control_tick)

    def _update_remote_control_labels(self, status):
        if not status:
            self.telegram_status_text.set(self._t("Статус сервера невідомий"))
            self.telegram_heartbeat_text.set("")
            self.webapp_status_text.set("")
            if self.telegram_status_label.winfo_exists():
                self.telegram_status_label.configure(fg="gray40")
            if self.main_menu_status_label.winfo_exists():
                self.main_menu_status_label.configure(fg="gray40")
            return

        # Задача користувача (2026-08-15): "тепер змінюй це на автоматичне
        # з'єднання" - статус тепер приходить ЖИВИМ HTTP-запитом (не з
        # файлу, який міг застаріти), тож "свіжість" тут уже не окреме
        # поняття: сам факт успішної відповіді і є "онлайн", а мережевий
        # збій/недоступність сервера вже оброблені гілкою "if not status"
        # вище (fetch_remote_status повертає None).
        self.telegram_status_text.set(self._t("Сервер онлайн"))
        status_color = "#1D9E75"
        if self.telegram_status_label.winfo_exists():
            self.telegram_status_label.configure(fg=status_color)
        if self.main_menu_status_label.winfo_exists():
            self.main_menu_status_label.configure(fg=status_color)

        bot_word = self._t("підключено") if status.get("bot_alive") else self._t("вимкнено")
        form_word = self._t("підключено") if status.get("webapp_alive") else self._t("вимкнено")
        self.telegram_heartbeat_text.set(
            self._t("бот: {bot} · форма: {form}").format(bot=bot_word, form=form_word)
        )
        self.webapp_status_text.set(status.get("webapp_public_url") or "")

    # Реальна знахідка (аудит коду, 2026-08-16): синхронний HTTP-запит
    # (до 10с timeout) на головному потоці заморожував вікно на весь час
    # очікування при повільному/недоступному тунелі - фон-потік +
    # _run_on_main_thread, той самий паттерн, що вже скрізь у цьому класі.
    def _on_remote_command_clicked(self, action):
        if self._remote_command_in_progress:
            return
        self._remote_command_in_progress = True

        def worker():
            error = None
            try:
                remote_control_client.send_remote_command(action)
            except Exception as exc:
                error = str(exc)

            def finish():
                self._remote_command_in_progress = False
                if error:
                    messagebox.showerror(self._t("Дистанційне керування"), error)
                    return
                # Команда лише ЗАПЛАНОВАНА на боці сервера (той самий
                # принцип, що й client_app.py._handle_remote_command) - не
                # чекаємо на завершення тут, наступний _remote_control_tick
                # (до 15с) сам покаже фактичний результат.
                self.root.after(2000, self._remote_control_tick)

            self._run_on_main_thread(finish)

        threading.Thread(target=worker, daemon=True).start()

    # ===== Кінець блоку віддаленого керування =====

    # Критична знахідка аудиту 28.07.2026 (#3): раніше ці 2 ручні кнопки
    # викликали TelegramBotWorker.stop() напряму в Tk-колбеку — блокуючий
    # thread.join(timeout=TELEGRAM_POLL_TIMEOUT+2), до ~17с заморожування
    # вікна — і одразу після цього стартували нового воркера, НЕ
    # перевіряючи, чи старий потік дійсно завершився (якщо join вичерпав
    # таймаут, а потік лишився живим — коротко існували ДВА живих воркери
    # на одному токені/БД). Обидві функції тепер перевикористовують ТОЙ
    # САМИЙ безпечний, неблокуючий механізм, що вже має watchdog
    # (_force_stop_hung_telegram_worker у фоновому потоці +
    # _telegram_stop_in_progress проти дублю) - див. _safe_stop_telegram_worker.
    def restart_telegram_bot(self):
        self._telegram_reconnect_attempts = 0
        self._telegram_next_attempt_at = 0.0
        self._telegram_should_run = True
        worker = self.telegram_worker
        alive = bool(worker and worker.thread and worker.thread.is_alive())
        if not alive:
            # Живого воркера немає (бот уже вимкнений) - дублювати
            # нічого, старт одразу так само безпечний, як і раніше.
            self._start_telegram_from_settings(silent=False)
            return
        # Живий воркер є (можливо, завислий) - НЕ стартуємо новий у цьому
        # ж виклику. Фоновий стоп нижче лише сигналізує намір; сам новий
        # TelegramBotWorker створює вже наявна, перевірена гілка
        # _telegram_watchdog_tick (worker.thread.is_alive()==False на
        # одному з наступних тіків, бо _telegram_should_run тепер True) -
        # це і усуває перегони, без дублювання логіки "чекай і стартуй".
        self.telegram_status_text.set(self._t("Перезапуск Telegram..."))
        self._safe_stop_telegram_worker()

    def stop_telegram_bot(self, update_status=True):
        # Явна зупинка (людина) - watchdog більше НЕ повинен сам
        # перепідключати, поки хтось знову не натисне "Підключити".
        self._telegram_should_run = False
        if update_status:
            self.telegram_status_text.set(self._t("Зупинка Telegram..."))
        self._webapp_should_run = False
        self._stop_webapp_tunnel()

        def on_stopped():
            self.telegram_worker = None
            if update_status:
                self.telegram_status_text.set(self._t("Telegram зупинено"))

        self._safe_stop_telegram_worker(on_stopped=on_stopped)

    # on_stopped (якщо переданий) завжди виконується на головному Tk-потоці
    # (через root.after) - ніколи не запускає новий TelegramBotWorker сам,
    # щоб не повторити ту саму гонку, яку цей метод і виправляє.
    def _safe_stop_telegram_worker(self, on_stopped=None):
        worker = self.telegram_worker
        alive = bool(worker and worker.thread and worker.thread.is_alive())
        if not alive:
            if on_stopped is not None:
                on_stopped()
            return
        if self._telegram_stop_in_progress:
            # Стоп уже виконується (запущений раніше - іншим кліком чи
            # самим watchdog'ом) - не дублюємо потік, лише чекаємо його
            # завершення (той самий прапорець, що й у watchdog-тіку).
            if on_stopped is not None:
                self._poll_until_telegram_stop_finished(on_stopped)
            return
        self._telegram_stop_in_progress = True

        def stop_worker():
            self._force_stop_hung_telegram_worker(worker)
            if on_stopped is not None:
                self._run_on_main_thread(on_stopped)

        threading.Thread(target=stop_worker, daemon=True).start()

    def _poll_until_telegram_stop_finished(self, on_stopped):
        if self.is_closing:
            return
        if self._telegram_stop_in_progress:
            self.root.after(200, lambda: self._poll_until_telegram_stop_finished(on_stopped))
        else:
            on_stopped()

    def _start_telegram_from_settings(self, silent=False):
        token, error = self._read_telegram_token()
        if error:
            if not silent:
                self.telegram_status_text.set(error)
            return

        self.telegram_status_text.set(self._t("Telegram запускається..."))
        from main import TelegramBotWorker  # локально, щоб уникнути циклічного імпорту

        self._telegram_should_run = True
        self.telegram_worker = TelegramBotWorker(
            token,
            self.db_path,
            settings_path=SETTINGS_PATH,
            status_callback=self._set_telegram_status_threadsafe,
        )
        # Тунель уже може бути піднятий з попереднього старту (він не
        # зупиняється на кожен reconnect, лише на явне "Зупинити Telegram") -
        # переносимо вже відому адресу одразу на НОВИЙ екземпляр воркера, не
        # чекаючи нового збігу regex у виводі cloudflared (якого не буде,
        # якщо сам тунель не перезапускався).
        if self.webapp_public_url:
            self.telegram_worker.webapp_public_url = self.webapp_public_url
        self.telegram_worker.start()
        self._webapp_should_run = True
        # Задача користувача: "спершу має запуститись телеграм бот. а через
        # 5 сек має почати запускатись форма" - не технічна залежність (бот
        # і тунель незалежні), а бажаний порядок запуску за спостереженнями
        # користувача. _webapp_not_before тримає watchdog вище від
        # передчасного автозапуску, поки не спрацює цей явний відклад.
        self._webapp_not_before = time.monotonic() + 5
        self.root.after(5000, self._start_webapp_tunnel)

    # Нагляд за ботом (watchdog): сам-перепланований тик (той самий ідіом,
    # що й _schedule_db_backup_tick вище) - перевіряє, чи потік бота ще
    # реально живий/рухається, і сам перепідключає, якщо ні, без участі
    # людини. Задача користувача: "якщо бот зависне чи впаде - ніхто про
    # це не дізнається автоматично".
    _TELEGRAM_HANG_THRESHOLD_SECONDS = 90
    _TELEGRAM_WATCHDOG_TICK_MS = 10000
    _TELEGRAM_RECONNECT_BACKOFF_CAP_SECONDS = 60

    @staticmethod
    def _is_timestamp_stale(iso_text, threshold_seconds):
        if not iso_text:
            return True
        try:
            moment = datetime.fromisoformat(str(iso_text))
        except (TypeError, ValueError):
            return True
        return (datetime.now() - moment).total_seconds() > threshold_seconds

    def _telegram_watchdog_tick(self):
        if self.is_closing:
            return
        if not self._telegram_should_run:
            self._telegram_reconnect_attempts = 0
        else:
            worker = self.telegram_worker
            alive = bool(worker and worker.thread and worker.thread.is_alive())
            hung = alive and self._is_timestamp_stale(
                getattr(worker, "last_loop_tick", None), self._TELEGRAM_HANG_THRESHOLD_SECONDS
            )
            if alive and not hung:
                self._telegram_reconnect_attempts = 0
                heartbeat = getattr(worker, "last_success_at", None)
                if heartbeat:
                    self.telegram_heartbeat_text.set(
                        self._t("Последний контакт: {value}").format(value=self._format_action_log_time(heartbeat))
                    )
            elif hung:
                # Аудит коду: worker.stop() блокує (thread.join(timeout=
                # TELEGRAM_POLL_TIMEOUT+2), до ~17с) — виклик напряму тут
                # заморожував усе вікно на цей час, бо тік виконується в
                # головному Tk-потоці. Форсований стоп тепер у фоновому
                # потоці; сам тік у ЦЬОМУ разі НЕ перепідключається одразу —
                # чекаємо, поки worker.thread.is_alive() природньо стане
                # False на ОДНІЙ з наступних ітерацій (гілка "не живий, не
                # завис" нижче вже й так перепідключає). Інакше (стара
                # поведінка): якщо join не встиг дочекатись реального
                # завершення потоку, новий воркер стартував би в ТОМУ Ж
                # тіку — два живих потоки на одному токені/БД одночасно.
                if not self._telegram_stop_in_progress:
                    self._telegram_stop_in_progress = True
                    threading.Thread(
                        target=self._force_stop_hung_telegram_worker, args=(worker,), daemon=True
                    ).start()
            else:
                if time.monotonic() >= self._telegram_next_attempt_at:
                    self._telegram_reconnect_attempts += 1
                    delay = min(
                        self._TELEGRAM_RECONNECT_BACKOFF_CAP_SECONDS,
                        5 * (2 ** (self._telegram_reconnect_attempts - 1)),
                    )
                    self._telegram_next_attempt_at = time.monotonic() + delay
                    self._start_telegram_from_settings(silent=False)
            self._check_webapp_tunnel_health()
        self.root.after(self._TELEGRAM_WATCHDOG_TICK_MS, self._telegram_watchdog_tick)

    def _force_stop_hung_telegram_worker(self, worker):
        try:
            worker.stop()
        finally:
            self._telegram_stop_in_progress = False

    # Форма введення даних (Telegram Mini App): локальний сервер +
    # Cloudflare Quick Tunnel, повністю автоматично. НЕ прив'язаний до
    # конкретного TelegramBotWorker-екземпляра (на відміну від самого бота,
    # цей тунель НЕ перезапускається на кожен reconnect/watchdog-цикл — лише
    # коли людина явно натискає "Зупинити Telegram", щоб адреса форми не
    # "мигтіла" під час звичайних перепідключень бота). cloudflared.exe -
    # офіційний, безкоштовний бінарник Cloudflare, бандлиться поруч із
    # проєктом (paths.CLOUDFLARED_EXE) - жодного акаунту/домену/команди від
    # людини, яка запускає програму.
    _WEBAPP_TUNNEL_URL_PATTERN = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")

    # Задача користувача: "якщо форма не може запуститись довше 10 секунд,
    # відміняй запуск, і заново запускай" - раніше `for line in
    # process.stdout` нижче міг чекати НАЗАВЖДИ (якщо cloudflared стартував,
    # але жодного разу не вивів URL і сам не завершився) - і жоден watchdog
    # це не лагодив, бо цей самий метод вище одразу виходить, поки
    # _webapp_tunnel_starting лишається True. Задача користувача
    # (2026-08-12): "прибери автозапуск через 5 сек... загалом через 30 чек
    # завжди - не попаду в тимчасові бани" - те саме число (30с), що й
    # _webapp_health_check_worker використовує для reconnect-циклу вже
    # ЖИВОГО тунелю - тут той самий ліміт, але для самого СТАРТУ. Часті
    # перезапуски (кожен - новий запит на Cloudflare) і провокували
    # тимчасовий бан 429.
    _WEBAPP_TUNNEL_START_TIMEOUT_SECONDS = 30

    def _start_webapp_tunnel(self, silent=True):
        if self._webapp_tunnel_starting:
            return
        if self.cloudflared_process is not None and self.cloudflared_process.poll() is None:
            return
        if not CLOUDFLARED_EXE.exists():
            # Бінарник відсутній (напр. видалений вручну) - форма просто
            # недоступна, старий текстовий шлях лишається єдиним і надалі
            # працює без жодної зміни.
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
                    process = subprocess.Popen(
                        [str(CLOUDFLARED_EXE), "tunnel", "--url", f"http://localhost:{self.webapp_server.port}"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                    )
                except OSError:
                    return
                self.cloudflared_process = process

                # Якщо за 10с URL так і не з'явився - вбиваємо процес, це
                # розблоковує `for line in process.stdout` нижче (закритий
                # stdout завершує ітерацію) замість вічного очікування.
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
                        match = self._WEBAPP_TUNNEL_URL_PATTERN.search(line)
                        if match:
                            url_found = True
                            url = match.group(0)
                            self._run_on_main_thread(lambda url=url: self._apply_webapp_public_url(url))
                            break
                except (OSError, ValueError):
                    pass
            finally:
                if timer is not None:
                    timer.cancel()
                # Реальний баг (2026-08-13): раніше _webapp_tunnel_starting
                # скидався в False ТУТ, ще ДО паузи нижче - тож швидший
                # _check_webapp_tunnel_health (кожні 10с) встигав побачити
                # "не стартує, процесу нема" і сам запускав ЩЕ одну спробу
                # значно раніше, ніж минали заплановані 30с. Тому реальний
                # темп запитів на Cloudflare був у рази частішим за задумане
                # "раз на 30с" - саме це й провокувало бан 429, не сам факт
                # тимчасової недоступності. Прапорець тепер лишається True
                # на ВЕСЬ цикл "спроба + пауза", інакше жоден інший
                # watchdog не підхопить його завчасно.
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
        # cloudflared сам попереджає в лозі "may take some time to be
        # reachable" одразу після видачі адреси - DNS/edge ще не встигли
        # розповсюдитись. Grace period нижче не дає health-check судити
        # тунель, поки адреса ще занадто свіжа.
        self._webapp_url_assigned_at = time.monotonic()
        if self.telegram_worker is not None:
            self.telegram_worker.webapp_public_url = url
        self._refresh_webapp_status_text()

    def _stop_webapp_tunnel(self):
        process = self.cloudflared_process
        self.cloudflared_process = None
        self.webapp_public_url = ""
        if process is not None:
            try:
                process.terminate()
            except OSError:
                pass
        try:
            self.webapp_server.stop()
        except OSError:
            pass
        self._refresh_webapp_status_text()

    # Пасивний, ненав'язливий текст стану під новими кнопками "Увімкнути"/
    # "Вимкнути"/"Перезапустити форму" — той самий принцип, що й telegram_
    # heartbeat_text/db_snapshot_heartbeat_text вище (видно одразу, без
    # окремого екрана).
    def _refresh_webapp_status_text(self):
        if not CLOUDFLARED_EXE.exists():
            self.webapp_status_text.set(self._t("Форма недоступна (бінарник cloudflared не знайдено)."))
            return
        tunnel_alive = self.cloudflared_process is not None and self.cloudflared_process.poll() is None
        if tunnel_alive and self.webapp_public_url:
            self.webapp_status_text.set(self._t("Форма підключена: {value}").format(value=self.webapp_public_url))
        elif tunnel_alive or self._webapp_tunnel_starting:
            self.webapp_status_text.set(self._t("Форма запускається..."))
        else:
            self.webapp_status_text.set(self._t("Форма вимкнена."))

    # Задача користувача: явні кнопки "Увімкнути"/"Вимкнути"/"Перезапустити"
    # форми (Mini App) у Налаштуваннях, незалежно від стану самого бота.
    # "Перезапустити" - найкорисніше: Cloudflare Quick Tunnel щоразу видає
    # НОВУ випадкову адресу, тож це єдиний спосіб форсувати нову адресу
    # (напр. коли стара з живого чат-повідомлення перестала резолвитись),
    # не чіпаючи підключення Telegram узагалі.
    def _toggle_webapp_form(self, action):
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

    # Той самий watchdog-принцип, що й для самого бота (перевірка "живий/
    # мертвий" на кожному тіку _telegram_watchdog_tick, а не окремий
    # паралельний self.root.after-ланцюжок) - якщо локальний сервер чи
    # cloudflared-підпроцес "впав" поки бот мав би працювати, перезапускає.
    def _check_webapp_tunnel_health(self):
        if not self._webapp_should_run:
            return
        if not CLOUDFLARED_EXE.exists():
            return
        server_alive = self.webapp_server.is_alive()
        tunnel_alive = self.cloudflared_process is not None and self.cloudflared_process.poll() is None
        if server_alive and tunnel_alive:
            return
        if tunnel_alive and not server_alive:
            # Тунель ще живий, помер лише локальний сервер - _start_webapp_tunnel()
            # тут нічого не зробить (його власна idempotency-перевірка бачить живий
            # cloudflared_process і виходить одразу), тож перезапускаємо сервер напряму.
            try:
                self.webapp_server.start()
            except OSError:
                pass
            return
        # Задача користувача (2026-08-13): "спершу має запуститись телеграм
        # бот. а через 5 сек має почати запускатись форма" - watchdog вище
        # (кожні 10с) інакше сам одразу підхопив би тунель, ще до того, як
        # спрацював явний 5с відклад у _start_telegram_from_settings нижче.
        if self._webapp_not_before is not None and time.monotonic() < self._webapp_not_before:
            return
        if not self._webapp_tunnel_starting:
            self.webapp_public_url = ""
            self._start_webapp_tunnel()

    # Задача користувача (скріншот "ERR_NAME_NOT_RESOLVED" у Mini App):
    # перевіряти чи міні апс відповідає, якщо ні - перезапуск, і так аж
    # поки не запрацює. _check_webapp_tunnel_health вище бачить лише "живий
    # процес", не "публічна адреса реально відповідає" - справжній,
    # ізольований від бот-watchdog'а HTTP-пробник на СВОЄМУ тіку.
    #
    # Задача користувача (2026-08-12): "чому по 10 разів підключає та
    # відключає? прибери автозапуск через 5 сек, зроби щоб чекав 30 сек і
    # тільки тоді перезапускав... загалом через 30 чек завжди - не попаду
    # в тимчасові бани" - швидкий (5с) режим і адаптивне "10 вдалих
    # перевірок -> 30с" ЗНЯТІ повністю: кожен реальний перезапуск - це
    # новий запит на Cloudflare quick-tunnel, і саме часті перезапуски (не
    # самі проби) провокували тимчасовий бан 429, з яким боролись раніше
    # цієї ж сесії. Тепер один-єдиний, завжди однаковий інтервал - і для
    # звичайних проб, і для очікування між спробами перезапуску нижче.
    _WEBAPP_HEALTH_CHECK_INTERVAL_MS = 30000
    _WEBAPP_RECONNECT_TIMEOUT_SECONDS = 30
    # Задача користувача (2026-08-12): "якщо форма після 10 спроб, на 11
    # спробу не запускається - вимкнути на 30 хв і не вмикати. потім через
    # 30 хв увімкнути на 20 секунд, якщо не буде конекту - тоді ще на 15
    # хвилин. а якщо все ок, тоді продовжити перевірку раз в 30 хв" -
    # окремий, повільніший цикл ПОВЕРХ звичайного 30с reconnect-циклу
    # вище - вмикається лише коли той не допоміг 10 разів поспіль.
    _WEBAPP_EXTENDED_FAILURE_THRESHOLD = 11
    _WEBAPP_EXTENDED_COOLDOWN_SECONDS = 30 * 60
    _WEBAPP_RECOVERY_PROBE_SECONDS = 20
    _WEBAPP_SHORT_COOLDOWN_SECONDS = 15 * 60
    _WEBAPP_CALM_CHECK_INTERVAL_MS = 30 * 60 * 1000

    # Задача користувача (2026-08-12): "форма постійно перепід'єднується,
    # це якийсь прикол... це ж не радіозв'язок що втратив контакт" - раніше
    # ОДНА невдала HTTP-проба (миттєва затримка на боці Cloudflare edge -
    # звичайна річ для quick tunnel, не ознака справжньої відмови) одразу
    # вважалась "тунель мертвий" і викликала повний перезапуск (кожен
    # перезапуск - НОВА публічна адреса) - тому цілком здорове з'єднання
    # виглядало як постійні перепідключення. Тепер "мертвим" тунель
    # вважається лише якщо кілька спроб поспіль (з паузою між ними) не
    # пройшли жодного разу.
    _WEBAPP_PROBE_RETRY_ATTEMPTS = 3
    _WEBAPP_PROBE_RETRY_DELAY_SECONDS = 3

    # Задача користувача (2026-08-13): "не підключає з першого разу" -
    # cloudflared сам попереджає, що щойно видана адреса "may take some time
    # to be reachable" - grace period нижче не дає health-check хибно
    # "хоронити" тунель ще до того, як DNS/edge встигли розповсюдитись.
    _WEBAPP_URL_GRACE_PERIOD_SECONDS = 20

    def _webapp_probe_confirms_down(self):
        for attempt in range(self._WEBAPP_PROBE_RETRY_ATTEMPTS):
            if self._probe_webapp_url():
                return False
            if attempt < self._WEBAPP_PROBE_RETRY_ATTEMPTS - 1:
                if not self._sleep_interruptible(self._WEBAPP_PROBE_RETRY_DELAY_SECONDS):
                    return False
        return True

    def _webapp_health_watchdog_tick(self):
        # Реальний ризик (аудит коду, 2026-08-14): цей "вартовий" був єдиним
        # із чотирьох (db-бекап/код-бекап/telegram/webapp), хто мовчки
        # перепланував себе через self.root.after БЕЗ перевірки is_closing
        # - на відміну від інших трьох. При закритті вікна це могло
        # запустити фоновий health-check потік уже ПІСЛЯ знищення self.root,
        # ризикуючи TclError замість чистого завершення.
        if self.is_closing:
            return
        if self._webapp_should_run and not self._webapp_health_check_active:
            self._webapp_health_check_active = True
            threading.Thread(target=self._webapp_health_check_worker, daemon=True).start()
        self.root.after(self._webapp_check_interval_ms, self._webapp_health_watchdog_tick)

    # Перериваний сон - та сама причина, що й короткий reconnect-цикл нижче
    # (time.sleep(1) у циклі): 30-хвилинна пауза не має тримати фоновий
    # потік мертвим для _webapp_should_run, якщо людина натисне "Вимкнути"
    # посеред очікування.
    def _sleep_interruptible(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if not self._webapp_should_run:
                return False
            time.sleep(min(1, deadline - time.monotonic()))
        return True

    # Реальний HTTP-запит на публічну (trycloudflare) адресу - /index.html
    # завжди статично роздається webapp_server незалежно від токена форми,
    # тож не потребує дійсного/свіжого токена лише для перевірки "чи взагалі
    # доходить трафік крізь тунель". Короткий timeout (4с) - сам пробник не
    # має "зависати" довше, ніж інтервал між тіками.
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

    # Один прохід "перезапуск -> дочекатись підключення протягом timeout
    # секунд" - спільний і для звичайного 30с reconnect-циклу, і для
    # 20с тест-спроби після розширеного простою нижче (лише timeout різний).
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

    # Фоновий потік (мережеві виклики + сон - НІКОЛИ на головному Tk-потоці).
    # Один прохід: якщо зараз усе гаразд - просто виходить (наступний тік
    # за 30с перевірить знову). Якщо ні - заходить у власний цикл "перезапуск
    # -> до 30с очікування нового підключення -> знову перезапуск", що не
    # чекає наступного зовнішнього тіка, а тримається сам, поки не запрацює
    # або поки форму не вимкнули вручну ("Вимкнути"). Задача користувача
    # (2026-08-12): якщо це не допомогло 10 разів поспіль (11-та спроба теж
    # невдала) - переходить у _webapp_extended_failure_recovery нижче
    # (набагато повільніший цикл), замість продовжувати битись у той самий
    # 30с ритм нескінченно.
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
            # (не самого тунелю Cloudflare), не справжню відмову тунелю - і
            # раніше ЄДИНОЮ відповіддю був повний перезапуск тунелю (нова
            # адреса). Спершу дешевий, непомітний крок - перезапустити лише
            # локальний сервер (та сама адреса) і перевірити ще раз.
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

    # Задача користувача (2026-08-12): "вимкнути на 30 хв і не вмикати.
    # потім через 30 хв увімкнути на 20 секунд, якщо не буде конекту -
    # тоді ще на 15 хвилин. а якщо все ок, тоді продовжити перевірку раз
    # в 30 хв" - жодних спроб/проб протягом самої паузи (на відміну від
    # звичайного reconnect-циклу вище, який пробує кожні 30с) - лише один
    # короткий тест-запуск ПІСЛЯ паузи, потім знову пауза (коротша, 15 хв),
    # і так по колу, поки тест не пройде.
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

    # Свіжий пере-аудит (2026-08-02, New-Important #1): спільний хелпер для
    # УСІХ фонових потоків, що маршалять callback на головний Tk-потік -
    # раніше кожен callback викликав self.root.after(...) напряму, без
    # перевірки is_closing/TclError, тож close() посеред мережевого виклику
    # (стоп Telegram, вхід OneDrive, сповіщення про роль) міг торкнутись уже
    # знищеного root. Той самий захист, що вже мала лише ця функція, тепер
    # спільний для всіх 5 фонових callback'ів.
    def _run_on_main_thread(self, callback):
        if self.is_closing:
            return
        try:
            self.root.after(0, callback)
        except tk.TclError:
            pass

    def _set_telegram_status_threadsafe(self, text):
        self._run_on_main_thread(lambda: self.telegram_status_text.set(text))

    # --- Таблиця даних: перегляд, редагування, збереження в Excel ---
    def _build_sheet_buttons(self):
        for sheet_name in self.store.sheet_names():
            btn = tk.Button(
                self.buttons_frame,
                text=sheet_name,
                width=18,
                command=lambda name=sheet_name: self.switch_sheet(name),
            )
            btn.pack(pady=4, padx=4)

    def switch_sheet(self, sheet_name):
        if self.edit_mode and self.has_unsaved_changes:
            if not messagebox.askyesno(
                self._t("Незбережені зміни"),
                self._t("У вас є незбережені зміни в поточній вкладці. Перейти без збереження?"),
            ):
                return
            if not self._discard_current_sheet_changes():
                return
        if self.edit_mode:
            self._exit_edit_mode()
        self.show_sheet(sheet_name)

    def show_sheet(self, sheet_name):
        self.current_sheet = sheet_name
        self.current_page = 0
        self.current_headers = self.store.get_headers(sheet_name)
        self.total_rows = self.store.count_rows(sheet_name)

        saved_widths = (self.settings.get("table_column_widths") or {}).get(sheet_name, {})
        self.column_filters[sheet_name] = dict((self.settings.get("table_column_filters") or {}).get(sheet_name, {}))
        columns = [f"col{i}" for i in range(len(self.current_headers))]
        self.tree["columns"] = columns
        for col_id, header in zip(columns, self.current_headers):
            title = str(header) if header is not None else ""
            self.tree.heading(col_id, text=self._column_heading_text(sheet_name, title))
            width = saved_widths.get(title) or self._default_column_width(title)
            self.tree.column(col_id, width=width, anchor="w")

        if self.store.is_read_only(sheet_name):
            self.edit_button.config(state="disabled")
        else:
            self.edit_button.config(state="normal")

        self._update_refresh_button_state()
        self._refresh_page()

    def _default_column_width(self, title):
        return max(80, min(280, len(title) * 8 + 30))

    def _save_current_column_widths(self, _event=None):
        if not self.current_sheet or not self.current_headers:
            return
        columns = self.tree["columns"]
        widths = {
            (str(header) if header is not None else ""): self.tree.column(col_id, "width")
            for col_id, header in zip(columns, self.current_headers)
        }
        all_widths = self.settings.get("table_column_widths") or {}
        if all_widths.get(self.current_sheet) == widths:
            return
        all_widths[self.current_sheet] = widths
        self.settings.set("table_column_widths", all_widths)

    def _column_heading_text(self, sheet_name, title):
        value = self.column_filters.get(sheet_name, {}).get(title)
        return f"{title}  [{value}]" if value else title

    # Клік у ЗАГОЛОВОК стовпця (не в саму клітинку - identify_region
    # відрізняє, "heading" саме той рядок з назвами стовпців, який
    # користувач мав на увазі скріншотом обрізаних заголовків) відкриває
    # маленьке поле вводу для текстового фільтра по цьому стовпцю.
    def _on_tree_header_click(self, event):
        if not self.current_sheet or self.tree.identify_region(event.x, event.y) != "heading":
            return
        col_ref = self.tree.identify_column(event.x)
        if not col_ref.startswith("#"):
            return
        col_index = int(col_ref[1:]) - 1
        if col_index < 0 or col_index >= len(self.current_headers):
            return
        header = self.current_headers[col_index]
        title = str(header) if header is not None else ""
        self._open_column_filter_popup(event, title)

    # Простий tk.Toplevel БЕЗ .transient() - той самий, уже закритий
    # висновок про немодальні вікна в цьому застосунку (журнали/персонал/
    # таймери): .transient() на немодальному вікні спричиняв реальний
    # z-order баг.
    def _open_column_filter_popup(self, event, title):
        if self.filter_popup_window is not None and self.filter_popup_window.winfo_exists():
            self.filter_popup_window.destroy()
        popup = tk.Toplevel(self.root)
        popup.title(self._t("Фільтр"))
        popup.geometry(f"220x104+{event.x_root}+{event.y_root}")
        popup.resizable(False, False)
        self.filter_popup_window = popup

        tk.Label(popup, text=title, anchor="w", wraplength=200, font=("Segoe UI", 9, "bold")).pack(
            fill="x", padx=8, pady=(8, 4)
        )
        entry = tk.Entry(popup)
        entry.insert(0, self.column_filters.get(self.current_sheet, {}).get(title, ""))
        entry.pack(fill="x", padx=8)
        entry.focus_set()
        entry.select_range(0, "end")

        def apply_filter(_event=None):
            self._set_column_filter(title, entry.get().strip())
            popup.destroy()

        def clear_filter():
            self._set_column_filter(title, "")
            popup.destroy()

        entry.bind("<Return>", apply_filter)
        popup.bind("<Escape>", lambda _event: popup.destroy())

        buttons = tk.Frame(popup)
        buttons.pack(fill="x", padx=8, pady=8)
        tk.Button(buttons, text=self._t("Очистити"), command=clear_filter).pack(side="left")
        tk.Button(buttons, text=self._t("Застосувати"), command=apply_filter).pack(side="right")

        # Реальний баг (аудит коду, 2026-08-15): цей попап - один з небагатьох
        # у програмі, що НЕ йде через _center_window (позиція прив'язана до
        # кліку на заголовку колонки, не до центру вікна) - без прямого
        # виклику лишався б незатемізованим у темному режимі.
        self._apply_theme(popup)

    def _set_column_filter(self, title, value):
        if not self.current_sheet:
            return
        sheet_filters = self.column_filters.setdefault(self.current_sheet, {})
        if value:
            sheet_filters[title] = value
        else:
            sheet_filters.pop(title, None)

        all_filters = self.settings.get("table_column_filters") or {}
        if sheet_filters:
            all_filters[self.current_sheet] = sheet_filters
        else:
            all_filters.pop(self.current_sheet, None)
        self.settings.set("table_column_filters", all_filters)

        for col_id, header in zip(self.tree["columns"], self.current_headers):
            header_title = str(header) if header is not None else ""
            self.tree.heading(col_id, text=self._column_heading_text(self.current_sheet, header_title))

        self.current_page = 0
        self._refresh_page()

    # Фільтр - підрядок, регістронезалежно, ПО ВСІХ активних стовпцях
    # одночасно (AND, не OR) - той самий принцип, що вже усталений у
    # фільтрах "Данные" (webapp).
    @staticmethod
    def _filter_rows(rows, active_filters):
        needles = [(index, text.lower()) for index, text in active_filters]
        result = []
        for row_id, row_values in rows:
            matched = True
            for index, needle in needles:
                cell_value = row_values[index] if index < len(row_values) else None
                if needle not in str(cell_value if cell_value is not None else "").lower():
                    matched = False
                    break
            if matched:
                result.append((row_id, row_values))
        return result

    def _update_refresh_button_state(self):
        if not hasattr(self, "refresh_table_button"):
            return
        if not self.current_sheet or self._is_statistics_sheet(self.current_sheet):
            self.refresh_table_button.pack_forget()
            return
        if not self.refresh_table_button.winfo_manager():
            self.refresh_table_button.pack(side="left", padx=(0, 8))
        self.refresh_table_button.config(state="normal")

    def refresh_current_sheet(self):
        if not self.current_sheet or self._is_statistics_sheet(self.current_sheet):
            return

        if self.edit_mode and self.has_unsaved_changes:
            action = self._ask_refresh_unsaved_action()
            if action == "cancel":
                return
            if action == "save":
                if not self._save_current_sheet_to_excel(show_success=False):
                    return
                self._exit_edit_mode()
            elif action == "discard":
                if not self._discard_current_sheet_changes():
                    return
                self._exit_edit_mode()

        self.total_rows = self.store.count_rows(self.current_sheet)
        self._refresh_page()

    def _is_statistics_sheet(self, sheet_name):
        name = str(sheet_name or "").upper()
        return sheet_name in READ_ONLY_SHEETS or "АНАЛИТИКА" in name or "СТАТИСТ" in name

    def _ask_refresh_unsaved_action(self):
        dialog = tk.Toplevel(self.root)
        dialog.title(self._t("Незбережені зміни"))
        dialog.geometry("520x210")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        result = {"action": "cancel"}

        content = tk.Frame(dialog, padx=18, pady=16)
        content.pack(fill="both", expand=True)

        tk.Label(
            content,
            text=self._t("У поточній вкладці є незбережені зміни."),
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        ).pack(anchor="w", fill="x")
        tk.Label(
            content,
            text=self._t("Що зробити перед оновленням таблиці?"),
            anchor="w",
            justify="left",
        ).pack(anchor="w", fill="x", pady=(6, 16))

        buttons = tk.Frame(content)
        buttons.pack(side="bottom", fill="x")

        def choose(action):
            result["action"] = action
            dialog.destroy()

        tk.Button(
            buttons,
            text=self._t("Зберегти та оновити"),
            width=20,
            command=lambda: choose("save"),
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            buttons,
            text=self._t("Оновити без збереження"),
            width=22,
            command=lambda: choose("discard"),
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            buttons,
            text=self._t("Скасувати"),
            width=12,
            command=lambda: choose("cancel"),
        ).pack(side="right")

        dialog.bind("<Escape>", lambda event: choose("cancel"))
        self._center_window(dialog, width=520, height=210)
        dialog.wait_window()
        return result["action"]

    def _page_count(self):
        if not self.total_rows:
            return 1
        return (self.total_rows + PAGE_SIZE - 1) // PAGE_SIZE

    def _refresh_page(self, cached_filtered_rows=None):
        self.tree.delete(*self.tree.get_children())

        active_filters = self.column_filters.get(self.current_sheet) if self.current_sheet else None
        if active_filters:
            if cached_filtered_rows is not None:
                filtered_rows = cached_filtered_rows
            else:
                indexed_filters = [
                    (index, text)
                    for index, header in enumerate(self.current_headers)
                    for text in [active_filters.get(str(header) if header is not None else "")]
                    if text
                ]
                all_rows = self.store.fetch_all_rows_with_ids(self.current_sheet)
                filtered_rows = self._filter_rows(all_rows, indexed_filters)
            self._filtered_page_cache = (self.current_sheet, tuple(sorted(active_filters.items())), filtered_rows)
            self.total_rows = len(filtered_rows)
            page_count = self._page_count()
            if self.current_page >= page_count:
                self.current_page = max(0, page_count - 1)
            offset = self.current_page * PAGE_SIZE
            rows = filtered_rows[offset:offset + PAGE_SIZE]
        else:
            self._filtered_page_cache = None
            self.total_rows = self.store.count_rows(self.current_sheet) if self.current_sheet else 0
            page_count = self._page_count()
            if self.current_page >= page_count:
                self.current_page = max(0, page_count - 1)
            offset = self.current_page * PAGE_SIZE
            rows = self.store.fetch_rows(self.current_sheet, PAGE_SIZE, offset) if self.current_sheet else []

        for row_id, row_values in rows:
            values = [_display_value(value) for value in row_values]
            self.tree.insert("", "end", iid=str(row_id), values=values)

        if self.total_rows:
            first_row = offset + 1
            last_row = offset + len(rows)
            self.page_label.config(
                text=self._t("Сторінка {page}/{page_count} · рядки {first_row}-{last_row} з {total_rows}").format(
                    page=self.current_page + 1, page_count=page_count, first_row=first_row, last_row=last_row,
                    total_rows=self.total_rows,
                )
            )
        else:
            self.page_label.config(text=self._t("Нет строк"))

        self.prev_page_button.config(state="normal" if self.current_page > 0 else "disabled")
        self.next_page_button.config(
            state="normal" if self.current_page < page_count - 1 else "disabled"
        )

    def _matching_filtered_page_cache(self):
        if self._filtered_page_cache is None:
            return None
        cached_sheet, cached_signature, cached_rows = self._filtered_page_cache
        active_filters = self.column_filters.get(self.current_sheet) if self.current_sheet else None
        if not active_filters or cached_sheet != self.current_sheet:
            return None
        if cached_signature != tuple(sorted(active_filters.items())):
            return None
        return cached_rows

    def previous_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self._refresh_page(cached_filtered_rows=self._matching_filtered_page_cache())

    def next_page(self):
        if self.current_page < self._page_count() - 1:
            self.current_page += 1
            self._refresh_page(cached_filtered_rows=self._matching_filtered_page_cache())

    # ---- режим редагування ----

    def toggle_edit_mode(self):
        if self.edit_mode:
            if self.has_unsaved_changes and not messagebox.askyesno(
                self._t("Скасувати редагування"),
                self._t("Скасувати незбережені зміни в поточній вкладці?"),
            ):
                return
            if not self._discard_current_sheet_changes():
                return
            self._exit_edit_mode()
            self.show_sheet(self.current_sheet)
        else:
            if not self.current_sheet or self.store.is_read_only(self.current_sheet):
                messagebox.showinfo(
                    self._t("Лише перегляд"),
                    self._t("Цей лист поки доступний тільки для перегляду."),
                )
                return
            self.edit_mode = True
            self.has_unsaved_changes = False
            self.edit_button.config(text=self._t("Скасувати редагування"))
            self.add_row_button.pack(side="left", padx=4)
            self.delete_row_button.pack(side="left", padx=4)
            self.save_button.pack(side="left", padx=4)
            # add="+" - без цього другий .bind на ту саму подію ПОВНІСТЮ
            # заміняв би перший (Задача користувача 2026-08-14, ширина
            # стовпців вище) замість того, щоб обидва спрацьовували разом.
            self.tree.bind("<ButtonRelease-1>", self._on_double_click, add="+")

    def _exit_edit_mode(self):
        self.edit_mode = False
        self.has_unsaved_changes = False
        self.edit_button.config(text=self._t("Редагувати"))
        self.add_row_button.pack_forget()
        self.delete_row_button.pack_forget()
        self.save_button.pack_forget()
        self.tree.unbind("<ButtonRelease-1>")

    def _discard_current_sheet_changes(self):
        if not self.current_sheet:
            return True
        try:
            workbook = excel_source.open_workbook(data_only=True)
        except RuntimeError as exc:
            # Аудит коду: раніше тут не було жодного перехоплення — виняток
            # летів непійманим у Tkinter callback (тихий провал, користувач
            # не бачив нічого), якщо джерело Excel не налаштоване.
            messagebox.showerror(self._t("Таблиця Excel"), self._t(str(exc)))
            return False
        try:
            worksheet = workbook[self.current_sheet]
            self.store.import_sheet(
                worksheet,
                self.current_sheet in READ_ONLY_SHEETS,
            )
        finally:
            workbook.close()
        self.has_unsaved_changes = False
        return True

    @staticmethod
    def _looks_like_number(text):
        text = str(text or "").strip()
        if not text:
            return False
        try:
            float(text.replace(",", "."))
            return True
        except ValueError:
            return False

    def _on_double_click(self, event):
        if not self.edit_mode or not self.current_sheet:
            return
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        column = self.tree.identify_column(event.x)
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return

        x, y, width, height = self.tree.bbox(row_id, column)
        current_value = self.tree.set(row_id, column)
        column_index = int(column.replace("#", "")) - 1

        entry = tk.Entry(self.tree)
        entry.place(x=x, y=y, width=width, height=height)
        entry.insert(0, current_value)
        entry.focus()
        committed = {"done": False}

        def commit(event=None):
            if committed["done"]:
                return
            committed["done"] = True
            new_value = entry.get()
            entry.destroy()
            # Аудит коду: раніше текст замість числа в раніше числовій
            # клітинці мовчки обнулявся (utils._number_value("текст") == 0)
            # — одна випадкова літера в залишку/ціні губила реальне значення
            # без жодного попередження. Перевіряємо лише коли стара клітинка
            # ВЖЕ була числом (текстові колонки на кшталт "Порода"/"Клиент"
            # це не зачіпає) і нове значення непорожнє й нечислове —
            # порожнє значення й далі приймається як 0, як і раніше.
            if (
                self._looks_like_number(current_value)
                and new_value.strip()
                and not self._looks_like_number(new_value)
            ):
                messagebox.showerror(
                    self._t("Некоректне значення"),
                    self._t('Очікується число, введено «{value}» — зміну скасовано.').format(value=new_value),
                )
                return
            # Реальна гонка з аудиту: читання всього рядка й запис усього
            # рядка назад раніше не мали жодного блокування між ними — якщо
            # бот саме тоді комітив продаж/прихід у ЦЕЙ САМИЙ рядок (Telegram
            # і GUI тримають ОКРЕМІ з'єднання до одного файлу), запис тут міг
            # тихо відкотити щойно оновлений ботом залишок застарілою копією
            # (той самий клас багу, що й виправлений TOCTOU в
            # apply_sale_operation, warehouse_data.py). BEGIN IMMEDIATE
            # одразу набуває блокування — читання й запис тепер один
            # нероздільний крок.
            with self.store.conn:
                self.store.conn.execute("BEGIN IMMEDIATE")
                row_values = self.store.get_row(int(row_id))
                while len(row_values) < len(self.current_headers):
                    row_values.append("")
                row_values[column_index] = new_value
                self.store.update_row(int(row_id), row_values)
            self.tree.set(row_id, column, new_value)
            self.has_unsaved_changes = True

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)

    def add_row(self):
        if not self.current_sheet or self.store.is_read_only(self.current_sheet):
            return
        ncols = len(self.current_headers)
        self.store.add_row(self.current_sheet, [""] * ncols)
        self.has_unsaved_changes = True
        self.total_rows = self.store.count_rows(self.current_sheet)
        self.current_page = self._page_count() - 1
        self._refresh_page()

    def delete_row(self):
        if not self.current_sheet or self.store.is_read_only(self.current_sheet):
            return
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo(self._t("Видалення рядка"), self._t("Оберіть рядок для видалення."))
            return
        # Аудит коду: усі ІНШІ видалення в програмі (кнопка, спосіб оплати,
        # поле-запит, користувач) мають підтвердження — тут його чомусь не було.
        if len(selected) == 1:
            confirmed = messagebox.askyesno(self._t("Видалення рядка"), self._t("Видалити обраний рядок?"))
        else:
            confirmed = messagebox.askyesno(
                self._t("Видалення рядка"),
                self._t("Видалити обрані рядки ({count})?").format(count=len(selected)),
            )
        if not confirmed:
            return
        self.store.delete_rows([int(item) for item in selected])
        self.has_unsaved_changes = True
        self._refresh_page()

    def save_changes(self):
        if not self._save_current_sheet_to_excel(show_success=True):
            return
        self._exit_edit_mode()
        self.show_sheet(self.current_sheet)

    def _save_current_sheet_to_excel(self, show_success=True):
        if not self.current_sheet:
            return False
        if self.store.is_read_only(self.current_sheet):
            messagebox.showinfo(self._t("Лише перегляд"), self._t("Цей лист не синхронізується назад в Excel."))
            return False

        try:
            self._sync_current_sheet_to_excel()
        except PermissionError:
            messagebox.showerror(
                self._t("Excel-файл відкритий"),
                self._t("Не удалось сохранить файл. Закройте Excel-файл и попробуйте еще раз."),
            )
            return False
        except OSError as exc:
            messagebox.showerror(self._t("Ошибка сохранения"), f"Не удалось сохранить файл:\n{exc}")
            return False
        except RuntimeError as exc:
            messagebox.showerror(self._t("Таблиця Excel"), self._t(str(exc)))
            return False

        self.has_unsaved_changes = False
        if show_success:
            messagebox.showinfo(self._t("Збережено"), self._t("Зміни збережено у файл."))
        return True

    def _sync_current_sheet_to_excel(self):
        sync_sheet_to_excel(self.store, self.current_sheet)

    def sync_excel_manually(self):
        try:
            sync_sheets_to_excel(self.store, ["СКЛАД", SALES_SHEET_NAME])
        except PermissionError:
            messagebox.showerror(
                self._t("Excel-файл відкритий"),
                self._t("Не удалось обновить файл. Закройте Excel-файл и попробуйте еще раз."),
            )
            return
        except OSError as exc:
            messagebox.showerror(self._t("Ошибка обновления"), f"Не удалось обновить файл:\n{exc}")
            return
        except RuntimeError as exc:
            messagebox.showerror(self._t("Таблиця Excel"), self._t(str(exc)))
            return

        # Задача користувача: "якщо користувач оновив вручну - тоді таймер
        # відліку скидається на початок" - ці два листи щойно вручну
        # синхронізовані, тож фоновий відкладений запис (TelegramBotWorker.
        # _excel_sync_tick) не повинен зайво повторювати те саме одразу.
        if self.telegram_worker is not None:
            self.telegram_worker.clear_excel_dirty(["СКЛАД", SALES_SHEET_NAME])

        messagebox.showinfo(self._t("Excel оновлено"), self._t("Дані з SQLite записано в Excel."))

    def on_close(self):
        if self.edit_mode and self.has_unsaved_changes:
            if not messagebox.askyesno(
                self._t("Незбережені зміни"),
                self._t("Закрити програму без збереження змін?"),
            ):
                return
        self.is_closing = True
        self.stop_telegram_bot(update_status=False)
        self.store.close()
        self.root.destroy()

