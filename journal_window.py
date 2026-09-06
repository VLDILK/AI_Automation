"""Вікно «Журнал операций» (Задача користувача, 2026-09-06) - спільне для
client_app.py (локальне сховище) і gui.py (через тунель).

Обраний макет: таблиця «Время · Операция · № · Кто · Товар · Размер · ± шт ·
± ед. · Остаток · Причина / клиент»; у кожному заголовку свій фільтр (клік по
заголовку); зверху період із календарем «С — По» + «Сегодня / 7 дней / 30 дней /
Все» і прапорці операцій із «Выбрать все» / «Снять все»; «Показать»,
«Сбросить фильтры»; «Показано N из M», «Показать ещё 50»; вивантаження Excel /
PDF. Видалити запис можна лише з домашки (колонка «✕»). Кнопка «‹ Назад»
угорі, Esc - назад, вікно з ручками зміни розміру.

Вигляд (2026-09-06, «по візуалу 1999 рік»): як на затвердженому макеті -
скруглені кнопки CustomTkinter, кольорові позначки операцій (приход зелений,
продажа помаранчевий, списание червоний, обмен фіолетовий, антисептирование
бірюзовий, коррекция синій), зелені/червоні ±, таблиця малюється на полотні
(ttk.Treeview не вміє кольорову клітинку).

UI-тексти - російською (мова користувачів програми)."""

import json
import threading
import tkinter as tk
import tkinter.font as tkfont
import urllib.error
from calendar import monthrange
from datetime import date, datetime, timedelta
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

import reports
from utils import _display_bot_number, _number_value
from warehouse_data import JOURNAL_FILTER_GROUPS, journal_page

PAGE_SIZE = 50
EXPORT_LIMIT = 5000
MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

DEFAULT_COLORS = {
    "bg": "#EDEFF2", "fg": "#20242A", "muted": "#5B6470", "row": "#FFFFFF", "zebra": "#F7F8FA", "line": "#D5D9DF",
    "head": "#E9ECF0", "accent": "#3B6EA5", "accent_fg": "#FFFFFF", "hover": "#DCE8F6",
    "plus": "#0F6E56", "minus": "#B42318", "dark": False,
}

# Кольори операцій - ті самі, що на макеті і в формі: (тло, текст) для
# світлої й темної теми.
TYPE_COLORS = {
    "income": (("#DDF3EA", "#0F6E56"), ("#173A2E", "#5FCFAA")),
    "sale": (("#FDE7D8", "#8A3A05"), ("#3A2A10", "#F5B14C")),
    "writeoff": (("#FDE8E6", "#B42318"), ("#3B1F1C", "#F08A80")),
    "exchange_out": (("#E3E1F7", "#534AB7"), ("#2B2750", "#B9B3F2")),
    "exchange_in": (("#E3E1F7", "#534AB7"), ("#2B2750", "#B9B3F2")),
    "antiseptic": (("#D9F0F4", "#0E7490"), ("#123540", "#7CD4E6")),
    "correction": (("#DDE6FB", "#1D4ED8"), ("#1C2A4A", "#9DB9F7")),
}
GROUP_TYPE = {label: group[0] for label, group in JOURNAL_FILTER_GROUPS}

COLUMNS = (
    {"key": "time", "label": "Время", "width": 122, "weight": 0, "anchor": "w"},
    {"key": "type", "label": "Операция", "width": 132, "weight": 0, "anchor": "w"},
    {"key": "document", "label": "№", "width": 46, "weight": 0, "anchor": "e"},
    {"key": "who", "label": "Кто", "width": 120, "weight": 2, "anchor": "w"},
    {"key": "product", "label": "Товар", "width": 140, "weight": 3, "anchor": "w"},
    {"key": "size", "label": "Размер", "width": 104, "weight": 0, "anchor": "w"},
    {"key": "qty", "label": "± шт", "width": 72, "weight": 0, "anchor": "e"},
    {"key": "measure", "label": "± ед.", "width": 96, "weight": 0, "anchor": "e"},
    {"key": "balance", "label": "Остаток", "width": 74, "weight": 0, "anchor": "e"},
    {"key": "reason", "label": "Причина / клиент", "width": 140, "weight": 3, "anchor": "w"},
)
DELETE_COLUMN = {"key": "delete", "label": "", "width": 34, "weight": 0, "anchor": "center"}


def type_colors(type_key, dark=False):
    pair = TYPE_COLORS.get(type_key)
    if pair is None:
        return ("#2A2F36", "#E5E7EA") if dark else ("#E4E8EE", "#20242A")
    return pair[1] if dark else pair[0]


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


# ---------------- віджети вигляду ----------------
def ghost_button(parent, colors, text, command=None, width=None, small=False, **kwargs):
    options = dict(
        text=text, command=command, fg_color="transparent", hover_color=colors["hover"], text_color=colors["fg"],
        border_width=1, border_color=colors["line"], corner_radius=8, height=26 if small else 32,
        font=ctk.CTkFont(size=11 if small else 12),
    )
    if width is not None:
        options["width"] = width
    options.update(kwargs)
    return ctk.CTkButton(parent, **options)


def accent_button(parent, colors, text, command=None, width=None):
    options = dict(text=text, command=command, fg_color=colors["accent"], hover_color=colors["accent"],
                   text_color=colors["accent_fg"], corner_radius=8, height=32, font=ctk.CTkFont(size=12, weight="bold"))
    if width is not None:
        options["width"] = width
    return ctk.CTkButton(parent, **options)


def caption(parent, colors, text):
    return ctk.CTkLabel(parent, text=text, text_color=colors["muted"], font=ctk.CTkFont(size=10))


class JournalTable(tk.Frame):
    """Таблиця на полотні: шапка з клікабельними заголовками, рядки із
    зеброю, кольорова позначка операції, зелені/червоні ±, «✕» для домашки."""
    HEADER_H = 34
    ROW_H = 30

    def __init__(self, parent, colors, columns, on_heading_click, on_cell_click=None):
        super().__init__(parent, bg=colors["line"], bd=0, highlightthickness=0)
        self.colors = colors
        self.columns = list(columns)
        self.on_heading_click = on_heading_click
        self.on_cell_click = on_cell_click
        self.markers = {column["key"]: " ▾" for column in self.columns}
        self.rows = []
        self.spans = []
        self.font = tkfont.Font(family="Segoe UI", size=10)
        self.bold = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        self.head_font = tkfont.Font(family="Segoe UI", size=9, weight="bold")
        inner = tk.Frame(self, bg=colors["row"], bd=0, highlightthickness=0)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        inner.grid_rowconfigure(1, weight=1)
        inner.grid_columnconfigure(0, weight=1)
        self.header = tk.Canvas(inner, height=self.HEADER_H, bg=colors["head"], highlightthickness=0, bd=0)
        self.header.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.body = tk.Canvas(inner, bg=colors["row"], highlightthickness=0, bd=0)
        self.body.grid(row=1, column=0, sticky="nsew")
        self.scroll = ttk.Scrollbar(inner, orient="vertical", command=self.body.yview)
        self.scroll.grid(row=1, column=1, sticky="ns")
        self.body.configure(yscrollcommand=self.scroll.set)
        self.body.bind("<Configure>", lambda event: self._layout())
        self.header.bind("<Button-1>", self._on_header_click)
        self.body.bind("<Button-1>", self._on_body_click)
        self.body.bind("<MouseWheel>", lambda event: self.body.yview_scroll(-1 if event.delta > 0 else 1, "units"))

    # --- геометрія ---
    def _layout(self):
        total = max(self.body.winfo_width(), 200)
        fixed = sum(column["width"] for column in self.columns if not column["weight"])
        weights = sum(column["weight"] for column in self.columns) or 1
        flex = max(total - fixed, 0)
        self.spans = []
        x = 0
        for column in self.columns:
            width = column["width"] if not column["weight"] else max(column["width"], int(flex * column["weight"] / weights))
            self.spans.append((x, width))
            x += width
        self.redraw_header()
        self.redraw()

    def _column_at(self, x):
        for column, (x0, width) in zip(self.columns, self.spans):
            if x0 <= x < x0 + width:
                return column["key"]
        return None

    def _on_header_click(self, event):
        key = self._column_at(event.x)
        if key and self.on_heading_click is not None:
            self.on_heading_click(key, event.x_root, event.y_root)

    def _on_body_click(self, event):
        if self.on_cell_click is None:
            return
        index = int(self.body.canvasy(event.y) // self.ROW_H)
        key = self._column_at(event.x)
        if 0 <= index < len(self.rows) and key:
            self.on_cell_click(index, key)

    # --- малювання ---
    def _ellipsis(self, text, max_width, font=None):
        font = font or self.font
        text = str(text or "")
        if font.measure(text) <= max_width:
            return text
        while text and font.measure(text + "…") > max_width:
            text = text[:-1]
        return text + "…"

    def heading_text(self, key):
        column = next(c for c in self.columns if c["key"] == key)
        return column["label"] + self.markers.get(key, "")

    def set_marker(self, key, marker):
        self.markers[key] = marker
        self.redraw_header()

    def redraw_header(self):
        self.header.delete("all")
        colors = self.colors
        for column, (x0, width) in zip(self.columns, self.spans):
            text = self.heading_text(column["key"])
            active = "●" in self.markers.get(column["key"], "")
            fill = colors["accent"] if active else colors["muted"]
            if column["anchor"] == "e":
                self.header.create_text(x0 + width - 8, self.HEADER_H / 2, text=text, anchor="e", font=self.head_font, fill=fill)
            elif column["anchor"] == "center":
                self.header.create_text(x0 + width / 2, self.HEADER_H / 2, text=text, anchor="center", font=self.head_font, fill=fill)
            else:
                self.header.create_text(x0 + 8, self.HEADER_H / 2, text=self._ellipsis(text, width - 12, self.head_font), anchor="w", font=self.head_font, fill=fill)
        self.header.create_line(0, self.HEADER_H - 1, max(self.header.winfo_width(), 10), self.HEADER_H - 1, fill=colors["line"])

    def set_rows(self, rows):
        self.rows = list(rows)
        self.redraw()

    def append_rows(self, rows):
        self.rows.extend(rows)
        self.redraw()

    def redraw(self):
        body = self.body
        body.delete("all")
        colors = self.colors
        width = max(body.winfo_width(), sum(w for _x, w in self.spans) if self.spans else 200)
        for index, row in enumerate(self.rows):
            y0 = index * self.ROW_H
            if index % 2:
                body.create_rectangle(0, y0, width, y0 + self.ROW_H, fill=colors["zebra"], outline="")
            for column, (x0, span) in zip(self.columns, self.spans):
                key = column["key"]
                value = row["values"].get(key, "")
                cy = y0 + self.ROW_H / 2
                if key == "type":
                    bg, fg = type_colors(row.get("type_key"), colors.get("dark"))
                    label = self._ellipsis(value, span - 24, self.bold)
                    text_width = self.bold.measure(label)
                    body.create_rectangle(x0 + 8, y0 + 6, x0 + 8 + text_width + 14, y0 + self.ROW_H - 6, fill=bg, outline="")
                    body.create_text(x0 + 15, cy, text=label, anchor="w", font=self.bold, fill=fg)
                elif key in ("qty", "measure"):
                    sign = row.get("sign", 0)
                    fill = colors["plus"] if sign > 0 else (colors["minus"] if sign < 0 else colors["fg"])
                    body.create_text(x0 + span - 8, cy, text=value, anchor="e", font=self.bold if sign else self.font, fill=fill)
                elif key == "delete":
                    body.create_text(x0 + span / 2, cy, text="✕", anchor="center", font=self.bold, fill=colors["minus"])
                elif column["anchor"] == "e":
                    body.create_text(x0 + span - 8, cy, text=value, anchor="e", font=self.font, fill=colors["fg"])
                else:
                    muted = key == "time"
                    body.create_text(x0 + 8, cy, text=self._ellipsis(value, span - 14), anchor="w", font=self.font,
                                     fill=colors["muted"] if muted else colors["fg"])
        height = max(len(self.rows) * self.ROW_H, 1)
        body.configure(scrollregion=(0, 0, width, height))


class Popup:
    """Спливаюче вікно біля елемента: без рамки, закривається по Esc, кліку
    поза ним або «Применить»."""

    def __init__(self, anchor, colors, x=None, y=None):
        self.colors = colors
        self.window = tk.Toplevel(anchor)
        self.window.overrideredirect(True)
        self.window.configure(bg=colors["line"])
        self.frame = tk.Frame(self.window, bg=colors["row"], padx=12, pady=10)
        self.frame.pack(padx=1, pady=1)
        if x is None:
            x = anchor.winfo_rootx()
        if y is None:
            y = anchor.winfo_rooty() + anchor.winfo_height()
        self.window.geometry("+%d+%d" % (x, y))
        self.window.bind("<Escape>", lambda event: self.close())
        self.window.bind("<FocusOut>", self._on_focus_out)
        self.window.after(50, lambda: self.window.focus_force() if self.window.winfo_exists() else None)

    def _focused_inside(self):
        try:
            focused = self.window.focus_get()
        except (KeyError, tk.TclError):
            return False
        return focused is not None and str(focused).startswith(str(self.window))

    def _on_focus_out(self, event):
        if self._focused_inside():
            return
        self.window.after(150, self._close_if_unfocused)

    def _close_if_unfocused(self):
        if self.window.winfo_exists() and not self._focused_inside():
            self.close()

    def close(self):
        if self.window.winfo_exists():
            self.window.destroy()


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


class JournalWindow:
    def __init__(self, parent, source, colors=None, title="Журнал операций"):
        self.source = source
        self.colors = dict(DEFAULT_COLORS, **(colors or {}))
        colors = self.colors
        self.entries = []
        self.total = 0
        self.has_more = False
        self.facets = {"who": [], "products": []}
        self.loading = False
        self.popup = None
        self.date_from = None
        self.date_to = None
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
        self.from_button = ghost_button(period_row, colors, "С: —  📅", command=lambda: self._open_calendar("from"), width=136)
        self.from_button.pack(side="left")
        self.to_button = ghost_button(period_row, colors, "По: —  📅", command=lambda: self._open_calendar("to"), width=136)
        self.to_button.pack(side="left", padx=(6, 10))
        self.preset_buttons = {}
        for text, days in (("Сегодня", 0), ("7 дней", 7), ("30 дней", 30), ("Все", None)):
            button = ghost_button(period_row, colors, text, command=lambda d=days: self._preset(d), small=True, width=66)
            button.pack(side="left", padx=(0, 4))
            self.preset_buttons[days] = button

        ops = ctk.CTkFrame(filters, fg_color="transparent")
        ops.pack(side="left", anchor="n", padx=(18, 0), pady=8)
        caption(ops, colors, "ОПЕРАЦИЯ").pack(anchor="w")
        ops_row = ctk.CTkFrame(ops, fg_color="transparent")
        ops_row.pack(anchor="w", pady=(4, 0))
        for label, group in JOURNAL_FILTER_GROUPS:
            var = tk.BooleanVar(value=True)
            self.type_vars[label] = var
            bg, fg = type_colors(group[0], colors.get("dark"))
            ctk.CTkCheckBox(
                ops_row, text=label, variable=var, text_color=fg, fg_color=fg, hover_color=fg, border_color=colors["line"],
                checkbox_width=18, checkbox_height=18, corner_radius=5, font=ctk.CTkFont(size=12, weight="bold"),
            ).pack(side="left", padx=(0, 10))
        ghost_button(ops_row, colors, "Выбрать все", command=lambda: self._set_all_types(True), small=True, width=96).pack(side="left", padx=(4, 4))
        ghost_button(ops_row, colors, "Снять все", command=lambda: self._set_all_types(False), small=True, width=84).pack(side="left")

        actions = ctk.CTkFrame(filters, fg_color="transparent")
        actions.pack(side="right", anchor="n", padx=(0, 12), pady=8)
        caption(actions, colors, " ").pack(anchor="w")
        actions_row = ctk.CTkFrame(actions, fg_color="transparent")
        actions_row.pack(anchor="e", pady=(2, 0))
        accent_button(actions_row, colors, "Показать", command=self.refresh, width=110).pack(side="left")
        ghost_button(actions_row, colors, "Сбросить фильтры", command=self.reset_filters, width=140).pack(side="left", padx=(6, 0))

        columns = list(COLUMNS) + ([DELETE_COLUMN] if source.can_delete else [])
        self.column_keys = [column["key"] for column in columns]
        self.table = JournalTable(window, colors, columns, on_heading_click=self._open_column_filter,
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
        self._preset(7, refresh=False)
        self.refresh()

    # ---------------- фільтри ----------------
    @staticmethod
    def _empty_column_filters():
        return {
            "sort": "desc", "documents": "", "who": None, "product": None, "size": "",
            "sign_plus": True, "sign_minus": True, "qty_min": "", "qty_max": "",
            "measure_min": "", "measure_max": "", "balance_min": "", "balance_max": "", "reason": "",
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

    def _preset(self, days, refresh=True):
        today = date.today()
        if days is None:
            self.date_from, self.date_to = None, None
        else:
            self.date_from, self.date_to = today - timedelta(days=days), today
        self.active_preset = days
        self._render_period()
        if refresh:
            self.refresh()

    def _render_period(self):
        self.from_button.configure(text="С: %s  📅" % (self.date_from.strftime("%d.%m.%Y") if self.date_from else "—"))
        self.to_button.configure(text="По: %s  📅" % (self.date_to.strftime("%d.%m.%Y") if self.date_to else "—"))
        for days, button in self.preset_buttons.items():
            active = days == getattr(self, "active_preset", 7)
            button.configure(fg_color=self.colors["hover"] if active else "transparent",
                             border_color=self.colors["accent"] if active else self.colors["line"])

    def _open_calendar(self, which):
        self._close_popup()
        anchor = self.from_button if which == "from" else self.to_button
        initial = self.date_from if which == "from" else self.date_to

        def picked(day):
            if which == "from":
                self.date_from = day
            else:
                self.date_to = day
            self.active_preset = "custom"
            self._render_period()
            self.refresh()

        self.popup = CalendarPopup(anchor, self.colors, initial=initial, on_pick=picked)

    def reset_filters(self):
        self._set_all_types(True)
        self.column_filters = self._empty_column_filters()
        self._preset(7, refresh=False)
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
        if cf["size"].strip():
            filters["size"] = cf["size"].strip()
        if not (cf["sign_plus"] and cf["sign_minus"]):
            filters["sign"] = "plus" if cf["sign_plus"] else ("minus" if cf["sign_minus"] else "none")
        for key in ("qty_min", "qty_max", "measure_min", "measure_max", "balance_min", "balance_max"):
            if str(cf[key]).strip():
                filters[key] = str(cf[key]).strip().replace(",", ".")
        if cf["reason"].strip():
            filters["reason"] = cf["reason"].strip()
        return filters

    def _column_active(self, key):
        cf = self.column_filters
        return {
            "time": cf["sort"] != "desc" or bool(self.date_from or self.date_to),
            "type": not all(var.get() for var in self.type_vars.values()),
            "document": bool(cf["documents"].strip()),
            "who": cf["who"] is not None,
            "product": cf["product"] is not None,
            "size": bool(cf["size"].strip()),
            "qty": not (cf["sign_plus"] and cf["sign_minus"]) or bool(str(cf["qty_min"]).strip() or str(cf["qty_max"]).strip()),
            "measure": bool(str(cf["measure_min"]).strip() or str(cf["measure_max"]).strip()),
            "balance": bool(str(cf["balance_min"]).strip() or str(cf["balance_max"]).strip()),
            "reason": bool(cf["reason"].strip()),
        }.get(key, False)

    def _render_headings(self):
        for column in COLUMNS:
            key = column["key"]
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
        new_entries = page.get("entries") or []
        self.entries.extend(new_entries)
        self.total = int(page.get("total") or len(self.entries))
        self.has_more = bool(page.get("has_more"))
        self.table.append_rows([self._row_for(entry) for entry in new_entries])
        self.status.configure(text="Показано %d из %d" % (len(self.entries), self.total))
        self.more_button.configure(state="normal" if self.has_more else "disabled")

    @staticmethod
    def _row_for(entry):
        quantity = _number_value(entry.get("quantity"))
        document = str(entry.get("document") or "")
        number = document.rsplit("№", 1)[-1].strip() if "№" in document else document
        product = entry.get("product") or ""
        if entry.get("breed"):
            product += " / " + entry["breed"]
        values = {
            "time": entry.get("time") or "", "type": entry.get("type_label") or "", "document": number,
            "who": entry.get("who") or "", "product": product, "size": (entry.get("size") or "").replace("x", "×"),
            "qty": _fmt_signed(entry.get("quantity")),
            "measure": _fmt_signed(entry.get("measure"), entry.get("unit") or "") if entry.get("measure") is not None else "",
            "balance": _display_bot_number(entry["balance_after"]) if entry.get("balance_after") not in (None, "") else "",
            "reason": entry.get("reason") or "", "delete": "✕",
        }
        return {"id": entry.get("id"), "values": values, "type_key": entry.get("type"), "sign": 1 if quantity > 0 else (-1 if quantity < 0 else 0)}

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
        if key == "delete" and 0 <= index < len(self.entries):
            self._delete_entry(self.entries[index].get("id"))

    def _open_column_filter(self, key, x_root=None, y_root=None):
        self._close_popup()
        if key == "delete":
            return
        x = (x_root - 20) if x_root is not None else self.table.winfo_rootx()
        y = self.table.winfo_rooty() + JournalTable.HEADER_H + 2
        popup = Popup(self.table, self.colors, x=x, y=y)
        self.popup = popup
        frame = popup.frame
        colors = self.colors
        cf = self.column_filters
        heading = next(column["label"] for column in COLUMNS if column["key"] == key)
        tk.Label(frame, text=heading, font=("Segoe UI", 11, "bold"), bg=colors["row"], fg=colors["fg"]).pack(anchor="w")
        apply_actions = []

        def add_apply(func):
            apply_actions.append(func)

        def checkbox(parent, text, variable, text_color=None):
            return ctk.CTkCheckBox(parent, text=text, variable=variable, text_color=text_color or colors["fg"], fg_color=colors["accent"],
                                   hover_color=colors["accent"], border_color=colors["line"], checkbox_width=18, checkbox_height=18,
                                   corner_radius=5, font=ctk.CTkFont(size=12))

        def entry(parent, variable, width=220, placeholder=""):
            return ctk.CTkEntry(parent, textvariable=variable, width=width, height=30, corner_radius=8, fg_color=colors["row"],
                                border_color=colors["line"], text_color=colors["fg"], placeholder_text=placeholder)

        if key == "time":
            sort_var = tk.StringVar(value=cf["sort"])
            for text, value in (("Новые сверху", "desc"), ("Старые сверху", "asc")):
                ctk.CTkRadioButton(frame, text=text, variable=sort_var, value=value, text_color=colors["fg"], fg_color=colors["accent"],
                                   hover_color=colors["accent"], border_color=colors["line"], font=ctk.CTkFont(size=12)).pack(anchor="w", pady=2)
            row = tk.Frame(frame, bg=colors["row"])
            row.pack(anchor="w", pady=(6, 0))
            ghost_button(row, colors, "С: %s" % (self.date_from.strftime("%d.%m.%Y") if self.date_from else "—"),
                         command=lambda: (popup.close(), self._open_calendar("from")), width=120, small=True).pack(side="left")
            ghost_button(row, colors, "По: %s" % (self.date_to.strftime("%d.%m.%Y") if self.date_to else "—"),
                         command=lambda: (popup.close(), self._open_calendar("to")), width=120, small=True).pack(side="left", padx=(4, 0))
            add_apply(lambda: cf.__setitem__("sort", sort_var.get()))
        elif key == "type":
            for label, group in JOURNAL_FILTER_GROUPS:
                _bg, fg = type_colors(group[0], colors.get("dark"))
                checkbox(frame, label, self.type_vars[label], text_color=fg).pack(anchor="w", pady=2)
            row = tk.Frame(frame, bg=colors["row"])
            row.pack(anchor="w", pady=(6, 0))
            ghost_button(row, colors, "Выбрать все", command=lambda: self._set_all_types(True), small=True, width=96).pack(side="left")
            ghost_button(row, colors, "Снять все", command=lambda: self._set_all_types(False), small=True, width=84).pack(side="left", padx=(4, 0))
        elif key in ("document", "size", "reason"):
            field = {"document": "documents", "size": "size", "reason": "reason"}[key]
            hint = {"document": "номер документа, напр. 12 или 12, 15", "size": "напр. 47x150", "reason": "клиент, поставщик, причина"}[key]
            var = tk.StringVar(value=cf[field])
            field_entry = entry(frame, var, placeholder=hint)
            field_entry.pack(anchor="w", pady=(4, 0))
            field_entry.focus_set()
            add_apply(lambda: cf.__setitem__(field, var.get()))
            field_entry.bind("<Return>", lambda event: self._apply_popup(apply_actions))
        elif key in ("who", "product"):
            values = self.facets.get("who" if key == "who" else "products") or []
            chosen = cf[key]
            search_var = tk.StringVar()
            entry(frame, search_var, placeholder="поиск…").pack(anchor="w", pady=(4, 0))
            box = tk.Frame(frame, bg=colors["row"])
            box.pack(anchor="w", pady=(6, 0))
            vars_by_value = {value: tk.BooleanVar(value=(chosen is None or value in chosen)) for value in values}

            def draw(*_args):
                for child in box.winfo_children():
                    child.destroy()
                needle = search_var.get().strip().lower()
                shown = 0
                for value in values:
                    if needle and needle not in value.lower():
                        continue
                    checkbox(box, value, vars_by_value[value]).pack(anchor="w", pady=1)
                    shown += 1
                    if shown >= 12:
                        tk.Label(box, text="… уточните поиском", bg=colors["row"], fg=colors["muted"], font=("Segoe UI", 8)).pack(anchor="w")
                        break

            search_var.trace_add("write", draw)
            draw()
            row = tk.Frame(frame, bg=colors["row"])
            row.pack(anchor="w", pady=(6, 0))
            ghost_button(row, colors, "Выбрать все", command=lambda: [v.set(True) for v in vars_by_value.values()], small=True, width=96).pack(side="left")
            ghost_button(row, colors, "Снять все", command=lambda: [v.set(False) for v in vars_by_value.values()], small=True, width=84).pack(side="left", padx=(4, 0))

            def apply_choice():
                selected = {value for value, var in vars_by_value.items() if var.get()}
                cf[key] = None if len(selected) == len(values) else selected

            add_apply(apply_choice)
        elif key in ("qty", "measure", "balance"):
            if key == "qty":
                plus_var = tk.BooleanVar(value=cf["sign_plus"])
                minus_var = tk.BooleanVar(value=cf["sign_minus"])
                checkbox(frame, "Плюс (приход)", plus_var, text_color=colors["plus"]).pack(anchor="w", pady=2)
                checkbox(frame, "Минус (расход)", minus_var, text_color=colors["minus"]).pack(anchor="w", pady=2)
                add_apply(lambda: (cf.__setitem__("sign_plus", plus_var.get()), cf.__setitem__("sign_minus", minus_var.get())))
            min_var = tk.StringVar(value=str(cf[key + "_min"]))
            max_var = tk.StringVar(value=str(cf[key + "_max"]))
            row = tk.Frame(frame, bg=colors["row"])
            row.pack(anchor="w", pady=(6, 0))
            tk.Label(row, text="от:", bg=colors["row"], fg=colors["fg"]).pack(side="left")
            entry(row, min_var, width=80).pack(side="left", padx=(4, 10))
            tk.Label(row, text="до:", bg=colors["row"], fg=colors["fg"]).pack(side="left")
            entry(row, max_var, width=80).pack(side="left", padx=(4, 0))
            add_apply(lambda: (cf.__setitem__(key + "_min", min_var.get()), cf.__setitem__(key + "_max", max_var.get())))

        foot = tk.Frame(frame, bg=colors["row"])
        foot.pack(anchor="w", pady=(10, 0))
        accent_button(foot, colors, "Применить", command=lambda: self._apply_popup(apply_actions), width=110).pack(side="left")
        ghost_button(foot, colors, "Очистить", command=lambda: self._clear_column(key), width=96).pack(side="left", padx=(6, 0))

    def _apply_popup(self, actions):
        for action in actions:
            action()
        self._close_popup()
        self.refresh()

    def _clear_column(self, key):
        cf = self.column_filters
        if key == "time":
            cf["sort"] = "desc"
            self.date_from, self.date_to = None, None
            self.active_preset = None
            self._render_period()
        elif key == "type":
            self._set_all_types(True)
        elif key == "document":
            cf["documents"] = ""
        elif key in ("who", "product"):
            cf[key] = None
        elif key == "size":
            cf["size"] = ""
        elif key == "qty":
            cf.update({"sign_plus": True, "sign_minus": True, "qty_min": "", "qty_max": ""})
        elif key == "measure":
            cf.update({"measure_min": "", "measure_max": ""})
        elif key == "balance":
            cf.update({"balance_min": "", "balance_max": ""})
        elif key == "reason":
            cf["reason"] = ""
        self._close_popup()
        self.refresh()

    def _close_popup(self):
        if self.popup is not None:
            self.popup.close()
            self.popup = None

    # ---------------- видалення (лише домашка) ----------------
    def _delete_entry(self, entry_id):
        entry = next((e for e in self.entries if e.get("id") == entry_id), None)
        if entry is None:
            return
        text = "Удалить запись журнала?\n\n%s · %s · %s %s — %s?" % (
            entry.get("time"), entry.get("type_label"), entry.get("product"), entry.get("size"), _fmt_signed(entry.get("quantity"), "шт"),
        )
        if not messagebox.askyesno("Журнал операций", text + "\n\nОстаток склада не изменится; след останется в журнале действий.", parent=self.window):
            return
        self.source.run(lambda: self.source.delete(entry_id), lambda _result: self.refresh(), self._show_error)

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
            {"key": "reason", "label": "Причина / клиент"},
        ]
        rows = []
        for entry in entries:
            values = self._row_for(entry)["values"]
            rows.append({key: values.get(key, "") for key in ("time", "type", "document", "who", "product", "size", "qty", "measure", "reason")}
                        | {"balance": entry.get("balance_after") if entry.get("balance_after") not in (None, "") else ""})
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
