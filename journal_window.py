"""Вікно «Журнал операций» (Задача користувача, 2026-09-06) - спільне для
client_app.py (локальне сховище) і gui.py (через тунель).

Обраний макет: таблиця «Время · Операция · № · Кто · Товар · Размер · ± шт ·
± ед. · Остаток · Причина / клиент»; у кожному заголовку свій фільтр (клік по
заголовку); зверху період із календарем «С — По» + «Сегодня / 7 дней / 30 дней /
Все» і прапорці операцій із «Выбрать все» / «Снять все»; «Показать»,
«Сбросить фильтры»; «Показано N из M», «Показать ещё 50»; вивантаження Excel /
PDF. Видалити запис можна лише з домашки (колонка «✕»). Кнопка «‹ Назад»
угорі, Esc - назад, вікно з ручками зміни розміру.

UI-тексти - російською (мова користувачів програми)."""

import json
import threading
import tkinter as tk
import urllib.error
from calendar import monthrange
from datetime import date, datetime, timedelta
from tkinter import filedialog, messagebox, ttk

import reports
from utils import _display_bot_number, _number_value
from warehouse_data import JOURNAL_FILTER_GROUPS, journal_page

PAGE_SIZE = 50
EXPORT_LIMIT = 5000
MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

DEFAULT_COLORS = {
    "bg": "#EDEFF2", "fg": "#20242A", "muted": "#5B6470", "row": "#FFFFFF", "line": "#D5D9DF",
    "accent": "#3B6EA5", "accent_fg": "#FFFFFF", "plus": "#0F6E56", "minus": "#B42318", "sel": "#DCE8F6",
}

COLUMNS = (
    ("time", "Время", 120, "w"),
    ("type", "Операция", 110, "w"),
    ("document", "№", 50, "e"),
    ("who", "Кто", 130, "w"),
    ("product", "Товар", 150, "w"),
    ("size", "Размер", 105, "w"),
    ("qty", "± шт", 70, "e"),
    ("measure", "± ед.", 90, "e"),
    ("balance", "Остаток", 70, "e"),
    ("reason", "Причина / клиент", 170, "w"),
)


def _fmt_signed(value, unit=""):
    if value is None:
        return ""
    number = _number_value(value)
    text = ("-" if number < 0 else "+") + _display_bot_number(abs(number))
    return (text + " " + unit).strip()


def _parse_date(text):
    text = str(text or "").strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


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


class Popup:
    """Спливаюче вікно під елементом: без рамки, закривається по Esc, кліку
    поза ним або «Применить»."""

    def __init__(self, anchor, colors, width=None):
        self.colors = colors
        self.window = tk.Toplevel(anchor)
        self.window.overrideredirect(True)
        self.window.configure(bg=colors["line"])
        self.frame = tk.Frame(self.window, bg=colors["row"], padx=10, pady=8)
        self.frame.pack(padx=1, pady=1)
        x = anchor.winfo_rootx()
        y = anchor.winfo_rooty() + anchor.winfo_height()
        self.window.geometry("+%d+%d" % (x, y))
        self.window.bind("<Escape>", lambda event: self.close())
        self.window.bind("<FocusOut>", self._on_focus_out)
        self.window.after(50, lambda: self.window.focus_force() if self.window.winfo_exists() else None)

    def _on_focus_out(self, event):
        # Фокус пішов на дитину попапу (поле/список) - це не закриття.
        try:
            focused = self.window.focus_get()
        except (KeyError, tk.TclError):
            focused = None
        if focused is not None and str(focused).startswith(str(self.window)):
            return
        self.window.after(120, self._close_if_unfocused)

    def _close_if_unfocused(self):
        if not self.window.winfo_exists():
            return
        try:
            focused = self.window.focus_get()
        except (KeyError, tk.TclError):
            focused = None
        if focused is None or not str(focused).startswith(str(self.window)):
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
        tk.Button(head, text="‹", width=3, command=lambda: self._shift(-1)).pack(side="left")
        self.title = tk.Label(head, text="", font=("Segoe UI", 10, "bold"), bg=colors["row"], fg=colors["fg"])
        self.title.pack(side="left", expand=True)
        tk.Button(head, text="›", width=3, command=lambda: self._shift(1)).pack(side="right")
        self.grid = tk.Frame(self.frame, bg=colors["row"])
        self.grid.pack(pady=(6, 4))
        foot = tk.Frame(self.frame, bg=colors["row"])
        foot.pack(fill="x")
        tk.Button(foot, text="Готово", width=9, command=self._done).pack(side="left")
        tk.Button(foot, text="Очистить", width=9, command=self._clear).pack(side="left", padx=(6, 0))
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
        for day in range(1, days + 1):
            current = date(self.year, self.month, day)
            selected = self.picked == current
            label = tk.Label(
                self.grid, text=str(day), width=3, cursor="hand2", font=("Segoe UI", 9, "bold" if selected else "normal"),
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
        window.geometry("1180x640")
        window.minsize(820, 460)
        window.configure(bg=colors["bg"])
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.bind("<Escape>", lambda event: self.close())

        top = tk.Frame(window, bg=colors["bg"])
        top.pack(fill="x", padx=14, pady=(12, 4))
        tk.Button(top, text="‹ Назад", command=self.close).pack(side="left")
        tk.Label(top, text=title, font=("Segoe UI", 13, "bold"), bg=colors["bg"], fg=colors["fg"]).pack(side="left", padx=(10, 0))
        tk.Label(top, text="Esc — назад", bg=colors["bg"], fg=colors["muted"], font=("Segoe UI", 8)).pack(side="left", padx=(10, 0))

        filters = tk.Frame(window, bg=colors["bg"])
        filters.pack(fill="x", padx=14, pady=(4, 6))
        period = tk.Frame(filters, bg=colors["bg"])
        period.pack(side="left", anchor="n")
        tk.Label(period, text="ПЕРИОД", bg=colors["bg"], fg=colors["muted"], font=("Segoe UI", 8)).pack(anchor="w")
        period_row = tk.Frame(period, bg=colors["bg"])
        period_row.pack(anchor="w")
        self.from_button = tk.Button(period_row, text="С: —  📅", width=16, command=lambda: self._open_calendar("from"))
        self.from_button.pack(side="left")
        self.to_button = tk.Button(period_row, text="По: —  📅", width=16, command=lambda: self._open_calendar("to"))
        self.to_button.pack(side="left", padx=(4, 8))
        for text, days in (("Сегодня", 0), ("7 дней", 7), ("30 дней", 30), ("Все", None)):
            tk.Button(period_row, text=text, command=lambda d=days: self._preset(d)).pack(side="left", padx=(0, 4))

        ops = tk.Frame(filters, bg=colors["bg"])
        ops.pack(side="left", anchor="n", padx=(18, 0))
        tk.Label(ops, text="ОПЕРАЦИЯ", bg=colors["bg"], fg=colors["muted"], font=("Segoe UI", 8)).pack(anchor="w")
        ops_row = tk.Frame(ops, bg=colors["bg"])
        ops_row.pack(anchor="w")
        for label, _types in JOURNAL_FILTER_GROUPS:
            var = tk.BooleanVar(value=True)
            self.type_vars[label] = var
            tk.Checkbutton(ops_row, text=label, variable=var, bg=colors["bg"], fg=colors["fg"], selectcolor=colors["row"],
                           activebackground=colors["bg"]).pack(side="left")
        tk.Button(ops_row, text="Выбрать все", command=lambda: self._set_all_types(True)).pack(side="left", padx=(8, 4))
        tk.Button(ops_row, text="Снять все", command=lambda: self._set_all_types(False)).pack(side="left")

        actions = tk.Frame(filters, bg=colors["bg"])
        actions.pack(side="right", anchor="n")
        tk.Label(actions, text=" ", bg=colors["bg"], font=("Segoe UI", 8)).pack(anchor="w")
        actions_row = tk.Frame(actions, bg=colors["bg"])
        actions_row.pack(anchor="e")
        tk.Button(actions_row, text="Показать", width=12, command=self.refresh, bg=colors["accent"], fg=colors["accent_fg"],
                  activebackground=colors["accent"]).pack(side="left")
        tk.Button(actions_row, text="Сбросить фильтры", command=self.reset_filters).pack(side="left", padx=(6, 0))

        table = tk.Frame(window, bg=colors["bg"])
        table.pack(fill="both", expand=True, padx=14, pady=(0, 6))
        self.column_keys = [key for key, _t, _w, _a in COLUMNS] + (["delete"] if source.can_delete else [])
        self.tree = ttk.Treeview(table, columns=self.column_keys, show="headings", selectmode="browse")
        for key, heading, width, anchor in COLUMNS:
            self.tree.heading(key, text=heading + " ▾", anchor="w")
            self.tree.column(key, width=width, anchor=anchor, stretch=key in ("product", "reason", "who"))
        if source.can_delete:
            self.tree.heading("delete", text="✕")
            self.tree.column("delete", width=32, anchor="center", stretch=False)
        self.tree.tag_configure("plus", foreground=colors["plus"])
        self.tree.tag_configure("minus", foreground=colors["minus"])
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._on_tree_click)

        bottom = tk.Frame(window, bg=colors["bg"])
        bottom.pack(fill="x", padx=14, pady=(0, 12))
        self.status = tk.Label(bottom, text="", bg=colors["bg"], fg=colors["muted"], anchor="w")
        self.status.pack(side="left")
        self.more_button = tk.Button(bottom, text="Показать ещё %d" % PAGE_SIZE, command=self.load_more, state="disabled")
        self.more_button.pack(side="left", padx=(10, 0))
        tk.Button(bottom, text="PDF", command=lambda: self.export("pdf")).pack(side="right")
        tk.Button(bottom, text="Excel", command=lambda: self.export("xlsx")).pack(side="right", padx=(0, 6))
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
        self._render_period()
        if refresh:
            self.refresh()

    def _render_period(self):
        self.from_button.configure(text="С: %s  📅" % (self.date_from.strftime("%d.%m.%Y") if self.date_from else "—"))
        self.to_button.configure(text="По: %s  📅" % (self.date_to.strftime("%d.%m.%Y") if self.date_to else "—"))

    def _open_calendar(self, which):
        self._close_popup()
        anchor = self.from_button if which == "from" else self.to_button
        initial = self.date_from if which == "from" else self.date_to

        def picked(day):
            if which == "from":
                self.date_from = day
            else:
                self.date_to = day
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
        for key, heading, _width, _anchor in COLUMNS:
            marker = " ●" if self._column_active(key) else " ▾"
            if key == "time":
                marker = (" ↑" if self.column_filters["sort"] == "asc" else " ↓") + marker
            self.tree.heading(key, text=heading + marker)

    # ---------------- завантаження ----------------
    def refresh(self):
        self._close_popup()
        self.entries = []
        self.tree.delete(*self.tree.get_children())
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
            self.tree.delete(*self.tree.get_children())
        if page.get("facets"):
            self.facets = page["facets"]
        new_entries = page.get("entries") or []
        self.entries.extend(new_entries)
        self.total = int(page.get("total") or len(self.entries))
        self.has_more = bool(page.get("has_more"))
        for entry in new_entries:
            self._insert_entry(entry)
        self.status.configure(text="Показано %d из %d" % (len(self.entries), self.total))
        self.more_button.configure(state="normal" if self.has_more else "disabled")

    def _insert_entry(self, entry):
        quantity = _number_value(entry.get("quantity"))
        tag = "plus" if quantity > 0 else ("minus" if quantity < 0 else "")
        document = str(entry.get("document") or "")
        number = document.rsplit("№", 1)[-1].strip() if "№" in document else document
        who_product = entry.get("product") or ""
        if entry.get("breed"):
            who_product += " / " + entry["breed"]
        values = [
            entry.get("time") or "", entry.get("type_label") or "", number, entry.get("who") or "", who_product,
            (entry.get("size") or "").replace("x", "×"), _fmt_signed(entry.get("quantity")),
            _fmt_signed(entry.get("measure"), entry.get("unit") or "") if entry.get("measure") is not None else "",
            _display_bot_number(entry["balance_after"]) if entry.get("balance_after") not in (None, "") else "",
            entry.get("reason") or "",
        ]
        if self.source.can_delete:
            values.append("✕")
        self.tree.insert("", "end", iid=str(entry.get("id")), values=values, tags=(tag,) if tag else ())

    def _show_error(self, text):
        self.loading = False
        if not self.window.winfo_exists():
            return
        self.status.configure(text="")
        messagebox.showerror("Журнал операций", text, parent=self.window)

    # ---------------- заголовки → фільтри ----------------
    def _on_tree_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        column_id = self.tree.identify_column(event.x)
        try:
            key = self.column_keys[int(column_id.replace("#", "")) - 1]
        except (ValueError, IndexError):
            return
        if region == "heading":
            self._open_column_filter(key, event)
            return "break"
        if region == "cell" and key == "delete" and self.source.can_delete:
            item = self.tree.identify_row(event.y)
            if item:
                self._delete_entry(int(item))
            return "break"
        return None

    def _header_anchor(self):
        # Попап стає під шапкою таблиці (сама шапка не є окремим віджетом).
        return self.tree

    def _open_column_filter(self, key, event):
        self._close_popup()
        if key == "delete":
            return
        popup = Popup(self.tree, self.colors)
        popup.window.geometry("+%d+%d" % (self.tree.winfo_rootx() + event.x - 20, self.tree.winfo_rooty() + 24))
        self.popup = popup
        frame = popup.frame
        colors = self.colors
        cf = self.column_filters
        heading = dict((k, h) for k, h, _w, _a in COLUMNS)[key]
        tk.Label(frame, text=heading, font=("Segoe UI", 10, "bold"), bg=colors["row"], fg=colors["fg"]).pack(anchor="w")
        apply_actions = []

        def add_apply(func):
            apply_actions.append(func)

        if key == "time":
            sort_var = tk.StringVar(value=cf["sort"])
            for text, value in (("Новые сверху", "desc"), ("Старые сверху", "asc")):
                tk.Radiobutton(frame, text=text, variable=sort_var, value=value, bg=colors["row"], fg=colors["fg"],
                               selectcolor=colors["row"], activebackground=colors["row"]).pack(anchor="w")
            row = tk.Frame(frame, bg=colors["row"])
            row.pack(anchor="w", pady=(4, 0))
            tk.Button(row, text="С: %s" % (self.date_from.strftime("%d.%m.%Y") if self.date_from else "—"),
                      command=lambda: (popup.close(), self._open_calendar("from"))).pack(side="left")
            tk.Button(row, text="По: %s" % (self.date_to.strftime("%d.%m.%Y") if self.date_to else "—"),
                      command=lambda: (popup.close(), self._open_calendar("to"))).pack(side="left", padx=(4, 0))
            add_apply(lambda: cf.__setitem__("sort", sort_var.get()))
        elif key == "type":
            for label, _group in JOURNAL_FILTER_GROUPS:
                tk.Checkbutton(frame, text=label, variable=self.type_vars[label], bg=colors["row"], fg=colors["fg"],
                               selectcolor=colors["row"], activebackground=colors["row"]).pack(anchor="w")
            row = tk.Frame(frame, bg=colors["row"])
            row.pack(anchor="w", pady=(4, 0))
            tk.Button(row, text="Выбрать все", command=lambda: self._set_all_types(True)).pack(side="left")
            tk.Button(row, text="Снять все", command=lambda: self._set_all_types(False)).pack(side="left", padx=(4, 0))
        elif key in ("document", "size", "reason"):
            field = {"document": "documents", "size": "size", "reason": "reason"}[key]
            hint = {"document": "номер документа, напр. 12 или 12, 15", "size": "напр. 47x150", "reason": "клиент, поставщик, причина"}[key]
            tk.Label(frame, text=hint, bg=colors["row"], fg=colors["muted"], font=("Segoe UI", 8)).pack(anchor="w")
            var = tk.StringVar(value=cf[field])
            entry = tk.Entry(frame, textvariable=var, width=28)
            entry.pack(anchor="w", pady=(2, 0))
            entry.focus_set()
            add_apply(lambda: cf.__setitem__(field, var.get()))
            entry.bind("<Return>", lambda event: self._apply_popup(apply_actions))
        elif key in ("who", "product"):
            values = self.facets.get("who" if key == "who" else "products") or []
            chosen = cf[key]
            search_var = tk.StringVar()
            tk.Entry(frame, textvariable=search_var, width=28).pack(anchor="w")
            box = tk.Frame(frame, bg=colors["row"])
            box.pack(anchor="w", pady=(4, 0))
            vars_by_value = {value: tk.BooleanVar(value=(chosen is None or value in chosen)) for value in values}

            def draw(*_args):
                for child in box.winfo_children():
                    child.destroy()
                needle = search_var.get().strip().lower()
                shown = 0
                for value in values:
                    if needle and needle not in value.lower():
                        continue
                    tk.Checkbutton(box, text=value, variable=vars_by_value[value], bg=colors["row"], fg=colors["fg"],
                                   selectcolor=colors["row"], activebackground=colors["row"], anchor="w").pack(anchor="w")
                    shown += 1
                    if shown >= 12:
                        tk.Label(box, text="… уточните поиском", bg=colors["row"], fg=colors["muted"], font=("Segoe UI", 8)).pack(anchor="w")
                        break

            search_var.trace_add("write", draw)
            draw()
            row = tk.Frame(frame, bg=colors["row"])
            row.pack(anchor="w", pady=(4, 0))
            tk.Button(row, text="Выбрать все", command=lambda: [v.set(True) for v in vars_by_value.values()]).pack(side="left")
            tk.Button(row, text="Снять все", command=lambda: [v.set(False) for v in vars_by_value.values()]).pack(side="left", padx=(4, 0))

            def apply_choice():
                selected = {value for value, var in vars_by_value.items() if var.get()}
                cf[key] = None if len(selected) == len(values) else selected

            add_apply(apply_choice)
        elif key in ("qty", "measure", "balance"):
            if key == "qty":
                plus_var = tk.BooleanVar(value=cf["sign_plus"])
                minus_var = tk.BooleanVar(value=cf["sign_minus"])
                tk.Checkbutton(frame, text="Плюс (приход)", variable=plus_var, bg=colors["row"], fg=colors["fg"], selectcolor=colors["row"], activebackground=colors["row"]).pack(anchor="w")
                tk.Checkbutton(frame, text="Минус (расход)", variable=minus_var, bg=colors["row"], fg=colors["fg"], selectcolor=colors["row"], activebackground=colors["row"]).pack(anchor="w")
                add_apply(lambda: (cf.__setitem__("sign_plus", plus_var.get()), cf.__setitem__("sign_minus", minus_var.get())))
            prefix = key
            min_var = tk.StringVar(value=str(cf[prefix + "_min"]))
            max_var = tk.StringVar(value=str(cf[prefix + "_max"]))
            row = tk.Frame(frame, bg=colors["row"])
            row.pack(anchor="w", pady=(4, 0))
            tk.Label(row, text="от:", bg=colors["row"], fg=colors["fg"]).pack(side="left")
            tk.Entry(row, textvariable=min_var, width=8).pack(side="left", padx=(2, 8))
            tk.Label(row, text="до:", bg=colors["row"], fg=colors["fg"]).pack(side="left")
            tk.Entry(row, textvariable=max_var, width=8).pack(side="left", padx=(2, 0))
            add_apply(lambda: (cf.__setitem__(prefix + "_min", min_var.get()), cf.__setitem__(prefix + "_max", max_var.get())))

        foot = tk.Frame(frame, bg=colors["row"])
        foot.pack(anchor="w", pady=(8, 0))
        tk.Button(foot, text="Применить", width=11, command=lambda: self._apply_popup(apply_actions)).pack(side="left")
        tk.Button(foot, text="Очистить", width=9, command=lambda: self._clear_column(key)).pack(side="left", padx=(6, 0))

    def _apply_popup(self, actions):
        for action in actions:
            action()
        self._close_popup()
        self.refresh()

    def _clear_column(self, key):
        empty = self._empty_column_filters()
        cf = self.column_filters
        if key == "time":
            cf["sort"] = "desc"
            self.date_from, self.date_to = None, None
            self._render_period()
        elif key == "type":
            self._set_all_types(True)
        elif key == "document":
            cf["documents"] = empty["documents"]
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
            document = str(entry.get("document") or "")
            rows.append({
                "time": entry.get("time") or "", "type": entry.get("type_label") or "",
                "document": document.rsplit("№", 1)[-1].strip() if "№" in document else document,
                "who": entry.get("who") or "", "product": (entry.get("product") or "") + (" / " + entry["breed"] if entry.get("breed") else ""),
                "size": entry.get("size") or "", "qty": _fmt_signed(entry.get("quantity")),
                "measure": _fmt_signed(entry.get("measure"), entry.get("unit") or "") if entry.get("measure") is not None else "",
                "balance": entry.get("balance_after") if entry.get("balance_after") not in (None, "") else "",
                "reason": entry.get("reason") or "",
            })
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
