"""Вікно «Журнал операций» (Задача користувача, 2026-09-06) - спільне для
client_app.py (локальне сховище) і gui.py (через тунель).

Обраний макет: таблиця «Время · Операция · № · Кто · Товар · Размер · ± шт ·
± ед. · Остаток · Причина / клиент»; у кожному заголовку свій фільтр (клік по
заголовку); зверху період із календарем «С — По» + «Сегодня / 7 дней / 30 дней /
Все» і прапорці операцій із «Выбрать все» / «Снять все»; «Показать»,
«Сбросить фильтры»; «Показано N из M», «Показать ещё 50»; вивантаження Excel /
PDF. Видалити запис можна лише з домашки (колонка «✕»). Кнопка «‹ Назад»
угорі, Esc - назад, вікно з ручками зміни розміру.

Вигляд - як на затвердженому макеті (ui_kit.py: скруглені кнопки, таблиця на
полотні з кольоровими позначками операцій і зеленими/червоними ±).

UI-тексти - російською (мова користувачів програми)."""

import json
import threading
import tkinter as tk
import urllib.error
from calendar import monthrange
from datetime import date, datetime, timedelta
from tkinter import filedialog, messagebox

import customtkinter as ctk

import reports
from ui_kit import DEFAULT_COLORS, CanvasTable, MultiChoice, Popup, accent_button, caption, checkbox, entry, ghost_button
from utils import _display_bot_number, _number_value
from warehouse_data import JOURNAL_FILTER_GROUPS, journal_page

PAGE_SIZE = 50
EXPORT_LIMIT = 5000
MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

# Кольори операцій - ті самі, що на макеті і в формі: (тло, текст) для
# світлої й темної теми. Клієнт може підмінити їх своїми (налаштування
# кольорів) через colors["types"].
TYPE_COLORS = {
    "income": (("#DDF3EA", "#0F6E56"), ("#173A2E", "#5FCFAA")),
    "sale": (("#FDE7D8", "#8A3A05"), ("#3A2A10", "#F5B14C")),
    "writeoff": (("#FDE8E6", "#B42318"), ("#3B1F1C", "#F08A80")),
    "exchange_out": (("#E3E1F7", "#534AB7"), ("#2B2750", "#B9B3F2")),
    "exchange_in": (("#E3E1F7", "#534AB7"), ("#2B2750", "#B9B3F2")),
    "antiseptic": (("#D9F0F4", "#0E7490"), ("#123540", "#7CD4E6")),
    "correction": (("#DDE6FB", "#1D4ED8"), ("#1C2A4A", "#9DB9F7")),
    # Відкат (2026-09-10): сірий - скасування операції, а не операція.
    "rollback": (("#E9EBEE", "#4B5563"), ("#2A2F36", "#C5CAD3")),
}

COLUMNS = (
    {"key": "time", "label": "Время", "width": 122, "weight": 0, "anchor": "w", "muted": True},
    {"key": "type", "label": "Операция", "width": 132, "weight": 0, "anchor": "w", "kind": "badge"},
    {"key": "document", "label": "№", "width": 60, "weight": 0, "anchor": "e"},
    {"key": "who", "label": "Кто", "width": 120, "weight": 2, "anchor": "w"},
    {"key": "product", "label": "Товар", "width": 150, "weight": 3, "anchor": "w", "per_line": True, "prefixed": True},
    {"key": "size", "label": "Размер", "width": 104, "weight": 0, "anchor": "w", "per_line": True},
    {"key": "qty", "label": "± шт", "width": 72, "weight": 0, "anchor": "e", "kind": "signed", "per_line": True},
    {"key": "measure", "label": "± ед.", "width": 96, "weight": 0, "anchor": "e", "kind": "signed", "per_line": True},
    {"key": "balance", "label": "Остаток", "width": 74, "weight": 0, "anchor": "e", "per_line": True},
    {"key": "amount", "label": "Сумма, MDL", "width": 96, "weight": 0, "anchor": "e"},
    {"key": "reason", "label": "Причина / клиент", "width": 140, "weight": 3, "anchor": "w"},
)
DELETE_COLUMN = {"key": "delete", "label": "", "width": 34, "weight": 0, "anchor": "center", "kind": "delete"}


def type_colors(type_key, dark=False, overrides=None):
    """(тло, текст) позначки операції; overrides - {type: (bg, fg)} з налаштувань."""
    base = type_key.replace("exchange_out", "exchange").replace("exchange_in", "exchange") if type_key else ""
    if overrides:
        custom = overrides.get(type_key) or overrides.get(base)
        if custom:
            return tuple(custom)
    pair = TYPE_COLORS.get(type_key)
    if pair is None:
        return ("#2A2F36", "#E5E7EA") if dark else ("#E4E8EE", "#20242A")
    return pair[1] if dark else pair[0]


def _fmt_money(value):
    if value in (None, ""):
        return ""
    return _display_bot_number(round(_number_value(value), 2))


def _fmt_signed(value, unit=""):
    if value is None:
        return ""
    number = _number_value(value)
    text = ("-" if number < 0 else "+") + _display_bot_number(abs(number))
    return (text + " " + unit).strip()


class JournalSource:
    """Що вікно хоче від програми: page(filters) -> словник сторінки
    (warehouse_data.journal_page), delete(id) - лише там, де can_delete."""
    can_delete = False

    def page(self, filters):
        raise NotImplementedError

    def delete(self, entry_id):
        raise NotImplementedError

    def run(self, action, then, on_error):
        try:
            result = action()
        except ValueError as exc:
            on_error(str(exc))
            return
        then(result)


class LocalJournalSource(JournalSource):
    def __init__(self, store):
        self.store = store

    def page(self, filters):
        return journal_page(self.store, filters)


class RemoteJournalSource(JournalSource):
    """gui.py: сторінки й видалення - HTTP через тунель до живого client_app.py."""
    can_delete = True

    def __init__(self, remote, run_on_main_thread, actor=None):
        self.remote = remote
        self.run_on_main_thread = run_on_main_thread
        self.actor = actor

    def page(self, filters):
        payload = self.remote.fetch_remote_journal(filters)
        if payload is None:
            raise ValueError("Не удалось получить журнал с client_app.py. Проверьте соединение.")
        return payload

    def delete(self, entry_id):
        try:
            return self.remote.delete_remote_journal_entry(entry_id, actor=self.actor)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(detail).get("error") or detail
            except ValueError:
                pass
            raise ValueError(detail)
        except Exception as exc:
            raise ValueError(str(exc))

    def run(self, action, then, on_error):
        def worker():
            try:
                result = action()
            except Exception as exc:
                text = str(exc)
                self.run_on_main_thread(lambda: on_error(text))
                return
            self.run_on_main_thread(lambda: then(result))

        threading.Thread(target=worker, daemon=True).start()


class CalendarPopup(Popup):
    """Місяць сіткою, «‹ ›», клік по дню = вибір; «Готово» / «Очистить»."""

    def __init__(self, anchor, colors, initial=None, on_pick=None):
        super().__init__(anchor, colors)
        self.on_pick = on_pick
        self.picked = initial
        today = initial or date.today()
        self.year, self.month = today.year, today.month
        head = tk.Frame(self.frame, bg=colors["row"])
        head.pack(fill="x")
        ghost_button(head, colors, "‹", command=lambda: self._shift(-1), width=30, small=True).pack(side="left")
        self.title = tk.Label(head, text="", font=("Segoe UI", 10, "bold"), bg=colors["row"], fg=colors["fg"])
        self.title.pack(side="left", expand=True)
        ghost_button(head, colors, "›", command=lambda: self._shift(1), width=30, small=True).pack(side="right")
        self.grid = tk.Frame(self.frame, bg=colors["row"])
        self.grid.pack(pady=(8, 6))
        foot = tk.Frame(self.frame, bg=colors["row"])
        foot.pack(fill="x")
        accent_button(foot, colors, "Готово", command=self._done, width=86).pack(side="left")
        ghost_button(foot, colors, "Очистить", command=self._clear, width=86).pack(side="left", padx=(6, 0))
        self._draw()

    def _shift(self, delta):
        month = self.month + delta
        year = self.year
        if month < 1:
            month, year = 12, year - 1
        elif month > 12:
            month, year = 1, year + 1
        self.year, self.month = year, month
        self._draw()

    def _draw(self):
        for child in self.grid.winfo_children():
            child.destroy()
        self.title.configure(text="%s %d" % (MONTHS[self.month - 1], self.year))
        for column, name in enumerate(WEEKDAYS):
            tk.Label(self.grid, text=name, width=3, bg=self.colors["row"], fg=self.colors["muted"], font=("Segoe UI", 8)).grid(row=0, column=column)
        first_weekday, days = monthrange(self.year, self.month)
        row, column = 1, first_weekday
        today = date.today()
        for day in range(1, days + 1):
            current = date(self.year, self.month, day)
            selected = self.picked == current
            label = tk.Label(
                self.grid, text=str(day), width=3, cursor="hand2", pady=2,
                font=("Segoe UI", 9, "bold" if selected or current == today else "normal"),
                bg=self.colors["accent"] if selected else self.colors["row"],
                fg=self.colors["accent_fg"] if selected else self.colors["fg"],
            )
            label.grid(row=row, column=column, padx=1, pady=1)
            label.bind("<Button-1>", lambda event, d=current: self._pick(d))
            column += 1
            if column > 6:
                column = 0
                row += 1

    def _pick(self, day):
        self.picked = day
        self._draw()

    def _done(self):
        if self.on_pick is not None:
            self.on_pick(self.picked)
        self.close()

    def _clear(self):
        self.picked = None
        if self.on_pick is not None:
            self.on_pick(None)
        self.close()


class CustomPeriodDialog:
    """«Свой период»: «С даты», «По дату» (календар), «Показать результат»."""

    def __init__(self, parent, colors, date_from, date_to, on_apply):
        self.colors = colors
        self.on_apply = on_apply
        self.date_from = date_from
        self.date_to = date_to
        self.popup = None
        window = tk.Toplevel(parent)
        self.window = window
        window.title("Свой период")
        window.configure(bg=colors["bg"])
        window.transient(parent)
        window.resizable(False, False)
        top = tk.Frame(window, bg=colors["bg"])
        top.pack(fill="x", padx=12, pady=(10, 4))
        ghost_button(top, colors, "‹ Назад", command=self.close, width=84, small=True).pack(side="left")
        tk.Label(top, text="Свой период", font=("Segoe UI", 11, "bold"), bg=colors["bg"], fg=colors["fg"]).pack(side="left", padx=(10, 0))
        body = tk.Frame(window, bg=colors["bg"])
        body.pack(fill="both", expand=True, padx=14, pady=(4, 12))
        tk.Label(body, text="С даты", bg=colors["bg"], fg=colors["muted"], font=("Segoe UI", 9)).pack(anchor="w")
        self.from_button = ghost_button(body, colors, "", command=lambda: self._pick("from"), width=240)
        self.from_button.pack(anchor="w", pady=(2, 8))
        tk.Label(body, text="По дату", bg=colors["bg"], fg=colors["muted"], font=("Segoe UI", 9)).pack(anchor="w")
        self.to_button = ghost_button(body, colors, "", command=lambda: self._pick("to"), width=240)
        self.to_button.pack(anchor="w", pady=(2, 12))
        accent_button(body, colors, "Показать результат", command=self.apply, width=240).pack(anchor="w")
        window.bind("<Escape>", lambda event: self.close())
        window.protocol("WM_DELETE_WINDOW", self.close)
        self._render()

    def _render(self):
        self.from_button.configure(text="%s  📅" % (self.date_from.strftime("%d.%m.%Y") if self.date_from else "дд.мм.гггг"))
        self.to_button.configure(text="%s  📅" % (self.date_to.strftime("%d.%m.%Y") if self.date_to else "дд.мм.гггг"))

    def _pick(self, which):
        if self.popup is not None:
            self.popup.close()
        anchor = self.from_button if which == "from" else self.to_button
        initial = self.date_from if which == "from" else self.date_to
        self.popup = CalendarPopup(anchor, self.colors, initial=initial, on_pick=lambda day: self._picked(which, day))

    def _picked(self, which, day):
        if which == "from":
            self.set_from(day)
        else:
            self.set_to(day)

    def set_from(self, day):
        self.date_from = day
        self._render()

    def set_to(self, day):
        self.date_to = day
        self._render()

    def apply(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            self.date_from, self.date_to = self.date_to, self.date_from
        self.on_apply(self.date_from, self.date_to)
        self.close()

    def close(self):
        if self.popup is not None:
            self.popup.close()
            self.popup = None
        if self.window.winfo_exists():
            self.window.destroy()


class JournalWindow:
    def __init__(self, parent, source, colors=None, title="Журнал операций"):
        self.source = source
        self.colors = dict(DEFAULT_COLORS, **(colors or {}))
        colors = self.colors
        self.type_overrides = colors.get("types") or {}
        self.entries = []
        # Номери документів, які зараз розгорнуті. Операція типово
        # згорнута (рішення користувача 2026-09-07, вигляд 03).
        self.expanded = set()
        self.total = 0
        self.has_more = False
        self.facets = {"who": [], "products": []}
        self.loading = False
        self.popup = None
        self.date_from = None
        self.date_to = None
        self.active_preset = 7
        self.type_vars = {}
        self.column_filters = self._empty_column_filters()

        window = tk.Toplevel(parent)
        self.window = window
        window.title(title)
        window.geometry("1180x660")
        window.minsize(860, 480)
        window.configure(bg=colors["bg"])
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.bind("<Escape>", lambda event: self.close())

        top = ctk.CTkFrame(window, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(12, 6))
        ghost_button(top, colors, "‹ Назад", command=self.close, width=90).pack(side="left")
        ctk.CTkLabel(top, text=title, text_color=colors["fg"], font=ctk.CTkFont(size=16, weight="bold")).pack(side="left", padx=(12, 0))
        ctk.CTkLabel(top, text="Esc — назад", text_color=colors["muted"], font=ctk.CTkFont(size=10)).pack(side="left", padx=(10, 0))

        filters = ctk.CTkFrame(window, fg_color=colors["row"], corner_radius=10)
        filters.pack(fill="x", padx=16, pady=(0, 8))
        period = ctk.CTkFrame(filters, fg_color="transparent")
        period.pack(side="left", anchor="n", padx=(12, 0), pady=8)
        caption(period, colors, "ПЕРИОД").pack(anchor="w")
        period_row = ctk.CTkFrame(period, fg_color="transparent")
        period_row.pack(anchor="w", pady=(2, 0))
        # Період як у формі антисептирування (рішення користувача 2026-09-06):
        # швидкі кнопки + «Свой период…» з вікном «С даты / По дату».
        self.preset_buttons = {}
        for key, text in self.PRESETS:
            button = ghost_button(period_row, colors, text, command=lambda k=key: self._preset(k), width=88)
            button.pack(side="left", padx=(0, 4))
            self.preset_buttons[key] = button
        self.custom_button = ghost_button(period_row, colors, "Свой период…", command=self.open_custom_period, width=150)
        self.custom_button.pack(side="left", padx=(6, 0))
        self.period_dialog = None

        # Rows of operation checkboxes at the top were removed (user, 2026-09-06):
        # the same filter lives in the "Операция" column header.
        self.type_checkboxes = {}
        for label, _group in JOURNAL_FILTER_GROUPS:
            self.type_vars[label] = tk.BooleanVar(value=True)

        actions = ctk.CTkFrame(filters, fg_color="transparent")
        actions.pack(side="right", anchor="n", padx=(0, 12), pady=8)
        caption(actions, colors, " ").pack(anchor="w")
        actions_row = ctk.CTkFrame(actions, fg_color="transparent")
        actions_row.pack(anchor="e", pady=(2, 0))
        accent_button(actions_row, colors, "Обновить", command=self.refresh, width=110).pack(side="left")
        ghost_button(actions_row, colors, "Сбросить фильтры", command=self.reset_filters, width=140).pack(side="left", padx=(6, 0))

        columns = list(COLUMNS) + ([DELETE_COLUMN] if source.can_delete else [])
        self.column_keys = [column["key"] for column in columns]
        self.table = CanvasTable(window, colors, columns, on_heading_click=self._open_column_filter,
                                 on_cell_click=self._on_cell_click if source.can_delete else None)
        self.table.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        bottom = ctk.CTkFrame(window, fg_color="transparent")
        bottom.pack(fill="x", padx=16, pady=(0, 12))
        self.status = ctk.CTkLabel(bottom, text="", text_color=colors["muted"], font=ctk.CTkFont(size=12))
        self.status.pack(side="left")
        self.more_button = ghost_button(bottom, colors, "Показать ещё %d" % PAGE_SIZE, command=self.load_more, width=140, state="disabled")
        self.more_button.pack(side="left", padx=(12, 0))
        ghost_button(bottom, colors, "PDF", command=lambda: self.export("pdf"), width=70).pack(side="right")
        ghost_button(bottom, colors, "Excel", command=lambda: self.export("xlsx"), width=70).pack(side="right", padx=(0, 6))
        self._preset("week", refresh=False)
        self.refresh()

    PRESETS = (("today", "Сегодня"), ("yesterday", "Вчера"), ("week", "Неделя"), ("month", "Месяц"), ("all", "Весь период"))

    def _type_colors(self, type_key):
        return type_colors(type_key, self.colors.get("dark"), self.type_overrides)

    # Нові кольори операцій (з вікна «Цвета операций» або з клієнта через
    # тунель): перефарбувати прапорці й позначки вже завантажених рядків.
    def set_type_overrides(self, overrides):
        overrides = {key: tuple(pair) for key, pair in (overrides or {}).items()}
        if overrides == self.type_overrides:
            return
        self.type_overrides = overrides
        for label, group in JOURNAL_FILTER_GROUPS:
            box = self.type_checkboxes.get(label)
            if box is not None:
                _bg, fg = self._type_colors(group[0])
                box.configure(text_color=fg, fg_color=fg, hover_color=fg)
        self.table.set_rows(self._rows_for(self.entries))

    # ---------------- фільтри ----------------
    @staticmethod
    def _empty_column_filters():
        return {
            "sort": "desc", "documents": "", "who": None, "product": None, "size": None,
            "sign_plus": True, "sign_minus": True, "qty_min": "", "qty_max": "",
            "measure_min": "", "measure_max": "", "balance_min": "", "balance_max": "", "reason": None,
        }

    def _selected_types(self):
        types = []
        for label, group in JOURNAL_FILTER_GROUPS:
            if self.type_vars[label].get():
                types.extend(group)
        return types

    def _set_all_types(self, value):
        for var in self.type_vars.values():
            var.set(value)

    def _preset(self, key, refresh=True):
        today = date.today()
        if key == "today":
            self.date_from, self.date_to = today, today
        elif key == "yesterday":
            self.date_from, self.date_to = today - timedelta(days=1), today - timedelta(days=1)
        elif key == "week":
            self.date_from, self.date_to = today - timedelta(days=6), today
        elif key == "month":
            self.date_from, self.date_to = today - timedelta(days=29), today
        else:
            key = "all"
            self.date_from, self.date_to = None, None
        self.active_preset = key
        self._render_period()
        if refresh:
            self.refresh()

    def set_custom_period(self, date_from, date_to, refresh=True):
        self.date_from, self.date_to = date_from, date_to
        self.active_preset = "custom" if (date_from or date_to) else "all"
        self._render_period()
        if refresh:
            self.refresh()

    def _render_period(self):
        for key, button in self.preset_buttons.items():
            active = key == self.active_preset
            button.configure(fg_color=self.colors["hover"] if active else "transparent",
                             border_color=self.colors["accent"] if active else self.colors["line"])
        if self.active_preset == "custom":
            text = "Свой период: %s — %s" % (self.date_from.strftime("%d.%m.%y") if self.date_from else "…",
                                            self.date_to.strftime("%d.%m.%y") if self.date_to else "…")
            self.custom_button.configure(text=text, fg_color=self.colors["hover"], border_color=self.colors["accent"], width=210)
        else:
            self.custom_button.configure(text="Свой период…", fg_color="transparent", border_color=self.colors["line"], width=150)

    def open_custom_period(self):
        self._close_popup()
        existing = self.period_dialog
        if existing is not None and existing.window.winfo_exists():
            existing.window.lift()
            return existing
        self.period_dialog = CustomPeriodDialog(self.window, self.colors, self.date_from, self.date_to, self.set_custom_period)
        return self.period_dialog

    def reset_filters(self):
        self._set_all_types(True)
        self.column_filters = self._empty_column_filters()
        self._preset("week", refresh=False)
        self._render_headings()
        self.refresh()

    def build_filters(self, offset=0, limit=PAGE_SIZE, with_facets=False):
        cf = self.column_filters
        types = self._selected_types()
        filters = {
            "types": types if types else ["__none__"],
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "sort": cf["sort"],
            "limit": limit,
            "offset": offset,
            "with_facets": with_facets,
        }
        if cf["documents"].strip():
            filters["documents"] = [part.strip() for part in cf["documents"].replace(";", ",").split(",") if part.strip()]
        if cf["who"] is not None:
            filters["who_list"] = sorted(cf["who"])
        if cf["product"] is not None:
            filters["product_list"] = sorted(cf["product"])
        if cf["size"] is not None:
            filters["size_list"] = sorted(cf["size"])
        if not (cf["sign_plus"] and cf["sign_minus"]):
            filters["sign"] = "plus" if cf["sign_plus"] else ("minus" if cf["sign_minus"] else "none")
        for key in ("qty_min", "qty_max", "measure_min", "measure_max", "balance_min", "balance_max"):
            if str(cf[key]).strip():
                filters[key] = str(cf[key]).strip().replace(",", ".")
        if cf["reason"] is not None:
            filters["reason_list"] = sorted(cf["reason"])
        return filters

    def _column_active(self, key):
        cf = self.column_filters
        return {
            "time": cf["sort"] != "desc" or bool(self.date_from or self.date_to),
            "type": not all(var.get() for var in self.type_vars.values()),
            "document": bool(cf["documents"].strip()),
            "who": cf["who"] is not None,
            "product": cf["product"] is not None,
            "size": cf["size"] is not None,
            "qty": not (cf["sign_plus"] and cf["sign_minus"]) or bool(str(cf["qty_min"]).strip() or str(cf["qty_max"]).strip()),
            "measure": bool(str(cf["measure_min"]).strip() or str(cf["measure_max"]).strip()),
            "balance": bool(str(cf["balance_min"]).strip() or str(cf["balance_max"]).strip()),
            "reason": cf["reason"] is not None,
        }.get(key, False)

    def _render_headings(self):
        for column in COLUMNS:
            key = column["key"]
            if key == "amount":
                self.table.markers[key] = ""
                continue
            marker = " ●" if self._column_active(key) else " ▾"
            if key == "time":
                marker = (" ↑" if self.column_filters["sort"] == "asc" else " ↓") + marker
            self.table.markers[key] = marker
        self.table.redraw_header()

    def heading_text(self, key):
        return self.table.heading_text(key)

    # ---------------- завантаження ----------------
    def refresh(self):
        self._close_popup()
        self.entries = []
        self.table.set_rows([])
        self._render_headings()
        self._load(offset=0, with_facets=True)

    def load_more(self):
        self._load(offset=len(self.entries))

    def _load(self, offset, with_facets=False):
        if self.loading:
            return
        self.loading = True
        self.status.configure(text="Загрузка…")
        self.more_button.configure(state="disabled")
        filters = self.build_filters(offset=offset, with_facets=with_facets)
        self.source.run(lambda: self.source.page(filters), lambda page: self._apply_page(page, offset), self._show_error)

    def _apply_page(self, page, offset):
        self.loading = False
        if not self.window.winfo_exists():
            return
        if offset == 0:
            self.entries = []
            self.table.set_rows([])
        if page.get("facets"):
            self.facets = page["facets"]
        if isinstance(page.get("colors"), dict):
            self.set_type_overrides(page["colors"].get("dark" if self.colors.get("dark") else "light") or {})
        new_entries = page.get("entries") or []
        self.entries.extend(new_entries)
        self.total = int(page.get("total") or len(self.entries))
        self.has_more = bool(page.get("has_more"))
        self.table.set_rows(self._rows_for(self.entries))
        self.status.configure(text="Показано %d из %d" % (len(self.entries), self.total))
        self.more_button.configure(state="normal" if self.has_more else "disabled")

    # Скільки позицій - російською, як і решта підписів журналу.
    @staticmethod
    def _positions_text(count):
        tail = count % 100
        if 11 <= tail <= 14:
            word = "позиций"
        elif count % 10 == 1:
            word = "позиция"
        elif 2 <= count % 10 <= 4:
            word = "позиции"
        else:
            word = "позиций"
        return "%d %s" % (count, word)

    def _row_for(self, entry):
        quantity = _number_value(entry.get("quantity"))
        measure = _number_value(entry.get("measure")) if entry.get("measure") is not None else 0
        document = str(entry.get("document") or "")
        number = document.rsplit("№", 1)[-1].strip() if "№" in document else document
        product = entry.get("product") or ""
        if entry.get("breed"):
            product += " / " + entry["breed"]
        reason = entry.get("reason") or ""
        # Відкат (2026-09-10): у колонці «Причина / клиент» - яку операцію
        # скасовано, і коментар відкату, якщо він є.
        rolled = entry.get("rollback_of") if (entry.get("type") or "") == "rollback" else None
        if isinstance(rolled, dict):
            origin = " ".join(part for part in (
                rolled.get("document") or rolled.get("type_label") or "",
                rolled.get("time") or "",
            ) if part)
            if rolled.get("who"):
                origin += " · " + str(rolled["who"])
            reason = "откат: " + origin + (" · " + reason if reason else "")
        values = {
            # Знак «№» у самому рядку, не лише в заголовку (2026-09-06).
            "time": entry.get("time") or "", "type": entry.get("type_label") or "", "document": ("№" + number) if number else "",
            "amount": _fmt_money(entry.get("amount")),
            "who": entry.get("who") or "", "product": product, "size": (entry.get("size") or "").replace("x", "×"),
            "qty": _fmt_signed(entry.get("quantity")),
            "measure": _fmt_signed(entry.get("measure"), entry.get("unit") or "") if entry.get("measure") is not None else "",
            "balance": _display_bot_number(entry["balance_after"]) if entry.get("balance_after") not in (None, "") else "",
            "reason": reason, "delete": "✕",
        }
        sign = 1 if quantity > 0 else (-1 if quantity < 0 else 0)
        return {
            "id": entry.get("id"), "values": values,
            "badges": {"type": self._type_colors(entry.get("type") or "")},
            "signs": {"qty": sign, "measure": 1 if measure > 0 else (-1 if measure < 0 else 0)},
        }

    # Обмін (рішення користувача 2026-09-06): обидва боки одного документа -
    # один запис «Обмен» із підрядками «отдаём» / «получаем»; час, №, хто й
    # причина - один раз. Записи документа йдуть підряд (той самий час).
    _EXCHANGE_PREFIX = {"exchange_out": "отдаём", "exchange_in": "получаем"}

    def _rows_for(self, entries):
        """Рухи з тим самим номером документа - один запис журналу.

        ТЗ, пункти 7 і 9: «один приход с пятью строками внутри, а не пять
        отдельных приходов». Раніше так збирався лише обмін.
        """
        rows = []
        index = 0
        while index < len(entries):
            document = str(entries[index].get("document") or "")
            group = [entries[index]]
            if document:
                while index + len(group) < len(entries):
                    candidate = entries[index + len(group)]
                    if str(candidate.get("document") or "") == document:
                        group.append(candidate)
                    else:
                        break
            if len(group) > 1:
                rows.append(self._operation_row(group))
            else:
                rows.append(self._row_for(group[0]))
            index += len(group)
        return rows

    def _operation_row(self, group):
        """Шапка операції: скільки позицій, разом штук, разом одиниць, сума.
        Позиції під нею з'являються лише коли операцію розгорнули."""
        is_exchange = all((entry.get("type") or "") in self._EXCHANGE_PREFIX for entry in group)
        if is_exchange:
            group = sorted(group, key=lambda e: 0 if e.get("type") == "exchange_out" else 1)
        first = group[0]
        row = self._row_for(first)
        row["ids"] = [entry.get("id") for entry in group]
        document = str(first.get("document") or "")
        row["group_key"] = document
        expanded = document in self.expanded
        if is_exchange:
            row["values"]["type"] = "Обмен"

        values = row["values"]
        values["product"] = ("▾ " if expanded else "▸ ") + self._positions_text(len(group))
        values["size"] = ""
        # Залишок після операції в шапці не показуємо: він у кожної позиції
        # свій, спільного числа не існує.
        values["balance"] = ""

        quantities = [_number_value(entry.get("quantity")) for entry in group]
        measured = [entry for entry in group if entry.get("measure") is not None]
        units = {str(entry.get("unit") or "") for entry in measured}
        if is_exchange:
            # У обміну плюс і мінус не складаються - показуємо обидва боки.
            out_qty = sum(q for q in quantities if q < 0)
            in_qty = sum(q for q in quantities if q > 0)
            values["qty"] = "%s / %s" % (_fmt_signed(out_qty), _fmt_signed(in_qty))
            if len(units) == 1:
                unit = next(iter(units))
                out_measure = sum(_number_value(e.get("measure")) for e in measured if _number_value(e.get("measure")) < 0)
                in_measure = sum(_number_value(e.get("measure")) for e in measured if _number_value(e.get("measure")) > 0)
                values["measure"] = "%s / %s" % (_fmt_signed(out_measure), _fmt_signed(in_measure, unit))
            else:
                values["measure"] = ""
            row["signs"] = {"qty": 0, "measure": 0}
        else:
            qty_total = sum(quantities)
            values["qty"] = _fmt_signed(qty_total)
            if len(units) == 1:
                measure_total = sum(_number_value(entry.get("measure")) for entry in measured)
                values["measure"] = _fmt_signed(measure_total, next(iter(units)))
            else:
                # Різні одиниці (м³ і мп) не складаються в одне число.
                measure_total = 0
                values["measure"] = ""
            measure_sign = 1 if measure_total > 0 else (-1 if measure_total < 0 else 0)
            row["signs"] = {"qty": 1 if qty_total > 0 else (-1 if qty_total < 0 else 0), "measure": measure_sign}

        amounts = [_number_value(entry.get("amount")) for entry in group if entry.get("amount") not in (None, "")]
        values["amount"] = _fmt_money(sum(amounts)) if amounts else ""

        reasons = []
        for entry in group:
            reason = entry.get("reason") or ""
            if reason and reason not in reasons:
                reasons.append(reason)
        values["reason"] = " · ".join(reasons)

        row["lines"] = []
        if expanded:
            # Перший підрядок - та сама шапка: колонки з per_line малюються
            # на кожному підрядку, тож підсумок мусить бути одним із них.
            row["lines"].append({"values": dict(values), "signs": dict(row["signs"]), "prefix": ""})
            for entry in group:
                line = self._row_for(entry)
                row["lines"].append({
                    "values": line["values"], "signs": line["signs"],
                    "prefix": self._EXCHANGE_PREFIX.get(entry.get("type"), "") if is_exchange else "",
                })
        return row

    def rows(self):
        return self.table.rows

    def _show_error(self, text):
        self.loading = False
        if not self.window.winfo_exists():
            return
        self.status.configure(text="")
        messagebox.showerror("Журнал операций", text, parent=self.window)

    # ---------------- заголовки → фільтри ----------------
    def _on_cell_click(self, index, key):
        if not (0 <= index < len(self.table.rows)):
            return
        row = self.table.rows[index]
        if key == "delete":
            self._delete_entry(row.get("ids") or [row.get("id")])
            return
        # Обраний вигляд (2026-09-07): операція типово згорнута, натиск по
        # будь-якій її клітинці розгортає позиції й згортає назад.
        group_key = row.get("group_key")
        if group_key:
            if group_key in self.expanded:
                self.expanded.discard(group_key)
            else:
                self.expanded.add(group_key)
            self.table.set_rows(self._rows_for(self.entries))

    def _open_column_filter(self, key, x_root=None, y_root=None):
        self._close_popup()
        if key in ("delete", "amount"):
            return
        x = (x_root - 20) if x_root is not None else self.table.winfo_rootx()
        y = self.table.winfo_rooty() + CanvasTable.HEADER_H + 2
        popup = Popup(self.table, self.colors, x=x, y=y)
        self.popup = popup
        frame = popup.frame
        colors = self.colors
        cf = self.column_filters
        heading = next(column["label"] for column in COLUMNS if column["key"] == key)
        tk.Label(frame, text=heading, font=("Segoe UI", 11, "bold"), bg=colors["row"], fg=colors["fg"]).pack(anchor="w")
        apply_actions = []

        if key == "time":
            sort_var = tk.StringVar(value=cf["sort"])
            for text, value in (("Новые сверху", "desc"), ("Старые сверху", "asc")):
                ctk.CTkRadioButton(frame, text=text, variable=sort_var, value=value, text_color=colors["fg"], fg_color=colors["accent"],
                                   hover_color=colors["accent"], border_color=colors["line"], font=ctk.CTkFont(size=12)).pack(anchor="w", pady=2)
            row = tk.Frame(frame, bg=colors["row"])
            row.pack(anchor="w", pady=(6, 0))
            ghost_button(row, colors, "Свой период…", command=lambda: (popup.close(), self.open_custom_period()), width=150, small=True).pack(side="left")
            apply_actions.append(lambda: cf.__setitem__("sort", sort_var.get()))
        elif key == "type":
            for label, group in JOURNAL_FILTER_GROUPS:
                _bg, fg = self._type_colors(group[0])
                checkbox(frame, colors, label, self.type_vars[label], text_color=fg, accent=fg).pack(anchor="w", pady=2)
            row = tk.Frame(frame, bg=colors["row"])
            row.pack(anchor="w", pady=(6, 0))
            ghost_button(row, colors, "Выбрать все", command=lambda: self._set_all_types(True), small=True, width=96).pack(side="left")
            ghost_button(row, colors, "Снять все", command=lambda: self._set_all_types(False), small=True, width=84).pack(side="left", padx=(4, 0))
        elif key == "document":
            var = tk.StringVar(value=cf["documents"])
            field_entry = entry(frame, colors, var, placeholder="номер документа, напр. 12 или 12, 15")
            field_entry.pack(anchor="w", pady=(4, 0))
            field_entry.focus_set()
            apply_actions.append(lambda: cf.__setitem__("documents", var.get()))
            field_entry.bind("<Return>", lambda event: self._apply_popup(apply_actions))
        elif key in ("who", "product", "size", "reason"):
            # Рішення користувача (2026-09-06): закритий випадний список →
            # «Добавить» → вибране рядками з ✕; нічого зайвого не видно.
            values = self.facets.get({"who": "who", "product": "products", "size": "sizes", "reason": "reasons"}[key]) or []
            placeholder = {"who": "Выберите сотрудника…", "product": "Выберите товар…", "size": "Выберите размер…", "reason": "Выберите причину…"}[key]
            chooser = MultiChoice(frame, colors, values, cf[key], placeholder=placeholder,
                                  display=(lambda v: str(v).replace("x", "×")) if key == "size" else None)
            chooser.frame.pack(anchor="w", pady=(4, 0))
            self.chooser = chooser
            apply_actions.append(lambda: cf.__setitem__(key, chooser.result()))
        elif key in ("qty", "measure", "balance"):
            if key == "qty":
                plus_var = tk.BooleanVar(value=cf["sign_plus"])
                minus_var = tk.BooleanVar(value=cf["sign_minus"])
                checkbox(frame, colors, "Плюс (приход)", plus_var, text_color=colors["plus"], accent=colors["plus"]).pack(anchor="w", pady=2)
                checkbox(frame, colors, "Минус (расход)", minus_var, text_color=colors["minus"], accent=colors["minus"]).pack(anchor="w", pady=2)
                apply_actions.append(lambda: (cf.__setitem__("sign_plus", plus_var.get()), cf.__setitem__("sign_minus", minus_var.get())))
            min_var = tk.StringVar(value=str(cf[key + "_min"]))
            max_var = tk.StringVar(value=str(cf[key + "_max"]))
            row = tk.Frame(frame, bg=colors["row"])
            row.pack(anchor="w", pady=(6, 0))
            tk.Label(row, text="от:", bg=colors["row"], fg=colors["fg"]).pack(side="left")
            entry(row, colors, min_var, width=80).pack(side="left", padx=(4, 10))
            tk.Label(row, text="до:", bg=colors["row"], fg=colors["fg"]).pack(side="left")
            entry(row, colors, max_var, width=80).pack(side="left", padx=(4, 0))
            apply_actions.append(lambda: (cf.__setitem__(key + "_min", min_var.get()), cf.__setitem__(key + "_max", max_var.get())))

        foot = tk.Frame(frame, bg=colors["row"])
        foot.pack(anchor="w", pady=(10, 0))
        accent_button(foot, colors, "Применить", command=lambda: self._apply_popup(apply_actions), width=110).pack(side="left")
        clear_text = "Все" if key in ("who", "product", "size", "reason", "type") else "Очистить"
        ghost_button(foot, colors, clear_text, command=lambda: self._clear_column(key), width=96).pack(side="left", padx=(6, 0))

    def _apply_popup(self, actions):
        for action in actions:
            action()
        self._close_popup()
        self.refresh()

    def _clear_column(self, key):
        cf = self.column_filters
        if key == "time":
            cf["sort"] = "desc"
            self._preset("all", refresh=False)
        elif key == "type":
            self._set_all_types(True)
        elif key == "document":
            cf["documents"] = ""
        elif key in ("who", "product", "size", "reason"):
            cf[key] = None
        elif key == "qty":
            cf.update({"sign_plus": True, "sign_minus": True, "qty_min": "", "qty_max": ""})
        elif key == "measure":
            cf.update({"measure_min": "", "measure_max": ""})
        elif key == "balance":
            cf.update({"balance_min": "", "balance_max": ""})
        self._close_popup()
        self.refresh()

    def _close_popup(self):
        if self.popup is not None:
            self.popup.close()
            self.popup = None

    # ---------------- видалення (лише домашка) ----------------
    def _delete_entry(self, entry_ids):
        if not isinstance(entry_ids, (list, tuple)):
            entry_ids = [entry_ids]
        chosen = [e for e in self.entries if e.get("id") in entry_ids]
        if not chosen:
            return
        lines = ["%s · %s · %s %s — %s" % (e.get("time"), e.get("type_label"), e.get("product"), e.get("size"), _fmt_signed(e.get("quantity"), "шт")) for e in chosen]
        text = (
            "Удалить запись журнала?" if len(chosen) == 1
            else "Удалить операцию целиком (%s)?" % self._positions_text(len(chosen))
        ) + "\n\n" + "\n".join(lines)
        if not messagebox.askyesno("Журнал операций", text + "\n\nОстаток склада не изменится; след останется в журнале действий.", parent=self.window):
            return
        ids = [e.get("id") for e in chosen]

        def delete_next(_result=None):
            if not ids:
                self.refresh()
                return
            entry_id = ids.pop(0)
            self.source.run(lambda: self.source.delete(entry_id), delete_next, self._show_error)

        delete_next()

    # ---------------- вивантаження ----------------
    def export(self, fmt):
        self._close_popup()
        ext = "pdf" if fmt == "pdf" else "xlsx"
        path = filedialog.asksaveasfilename(
            parent=self.window, title="Сохранить журнал", defaultextension="." + ext,
            initialfile="Журнал операций %s.%s" % (date.today().strftime("%d-%m-%Y"), ext),
            filetypes=[("Excel", "*.xlsx")] if ext == "xlsx" else [("PDF", "*.pdf")],
        )
        if not path:
            return
        filters = self.build_filters(offset=0, limit=EXPORT_LIMIT)

        def render(page):
            try:
                reports.render_report(self.export_spec(page.get("entries") or []), reports.FORMAT_PDF if ext == "pdf" else reports.FORMAT_EXCEL, path=path)
            except PermissionError:
                self._show_error("Файл уже открыт в другой программе. Закройте его и попробуйте ещё раз.")
                return
            except Exception as exc:
                self._show_error("Не удалось сохранить: %s" % exc)
                return
            messagebox.showinfo("Журнал операций", "Сохранено:\n%s" % path, parent=self.window)

        self.source.run(lambda: self.source.page(filters), render, self._show_error)

    def export_spec(self, entries):
        columns = [
            {"key": "time", "label": "Время"}, {"key": "type", "label": "Операция"}, {"key": "document", "label": "№"},
            {"key": "who", "label": "Кто"}, {"key": "product", "label": "Товар"}, {"key": "size", "label": "Размер"},
            {"key": "qty", "label": "± шт"}, {"key": "measure", "label": "± ед."}, {"key": "balance", "label": "Остаток, шт", "numeric": True},
            {"key": "amount", "label": "Сумма, MDL", "numeric": True}, {"key": "reason", "label": "Причина / клиент"},
        ]
        rows = []
        for entry_data in entries:
            values = self._row_for(entry_data)["values"]
            row = {key: values.get(key, "") for key in ("time", "type", "document", "who", "product", "size", "qty", "measure", "reason")}
            row["amount"] = entry_data.get("amount") if entry_data.get("amount") not in (None, "") else ""
            row["balance"] = entry_data.get("balance_after") if entry_data.get("balance_after") not in (None, "") else ""
            rows.append(row)
        period = ""
        if self.date_from or self.date_to:
            period = " за %s — %s" % (self.date_from.strftime("%d.%m.%Y") if self.date_from else "…", self.date_to.strftime("%d.%m.%Y") if self.date_to else "…")
        return {
            "title": "Журнал операций" + period,
            "generated_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
            "columns": columns,
            "rows": rows,
        }

    def close(self):
        self._close_popup()
        if self.window.winfo_exists():
            self.window.destroy()


def open_journal_window(owner, attr, parent, source, colors=None, title="Журнал операций"):
    existing = getattr(owner, attr, None)
    if existing is not None and existing.window.winfo_exists():
        existing.window.deiconify()
        existing.window.lift()
        existing.window.focus_force()
        existing.refresh()
        return existing
    window = JournalWindow(parent, source, colors=colors, title=title)
    setattr(owner, attr, window)
    return window
