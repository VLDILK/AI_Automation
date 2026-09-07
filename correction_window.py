"""Вікно «Коррекция остатков» у клієнті (Задача користувача, 2026-09-06).

Рішення користувача: інвентаризація вноситься всім списком, людина міняє лише
штуки («Изменить на: шт»), одиниці рахує програма, одна інвентаризація -
один документ «Коррекция №N». Вигляд - як у журналу операцій (ui_kit.py):
таблиця на полотні, у кожному заголовку свій фільтр (клік по заголовку),
без верхніх полів пошуку; правка числа - клік у клітинці «Изменить на».
Кнопка «‹ Назад» угорі, Esc - назад.

Дані й запис - зворотні виклики з програми (client_app.py:
_correction_stock_rows / _apply_stock_corrections), тут лише вигляд."""

import re
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from ui_kit import DEFAULT_COLORS, CanvasTable, MultiChoice, Popup, accent_button, add_window_grips, checkbox, entry, ghost_button
from utils import LATH_MARK, normalize_length_mm, piece_measure, plain_product_name, row_measure_kind
from utils import _number_value

# Ширина: зайве місце при розширенні вікна розходиться по ВСІХ текстових
# колонках (рішення користувача 2026-09-07: «великий порожній простір між
# продуктом і породою»), а не тільки в «Продукт». Числові колонки не
# розтягуються - інакше число відлітає від свого заголовка.
COLUMNS = (
    {"key": "product", "label": "Продукт", "width": 130, "weight": 2, "anchor": "w"},
    {"key": "breed", "label": "Порода", "width": 92, "weight": 1, "anchor": "w"},
    {"key": "condition", "label": "Состояние", "width": 84, "weight": 1, "anchor": "w"},
    {"key": "size", "label": "Размер", "width": 112, "weight": 1, "anchor": "w"},
    {"key": "now", "label": "Сейчас, шт", "width": 88, "weight": 0, "anchor": "e"},
    {"key": "now_measure", "label": "Сейчас, ед.", "width": 96, "weight": 0, "anchor": "e", "muted": True},
    {"key": "new", "label": "Изменить на: шт", "width": 120, "weight": 0, "anchor": "e", "kind": "edit"},
    {"key": "new_measure", "label": "Будет, ед.", "width": 96, "weight": 0, "anchor": "e", "muted": True},
    {"key": "delta", "label": "±", "width": 72, "weight": 0, "anchor": "e", "kind": "signed"},
)
_UNIT_LABELS = {"volume": "м3", "area": "м2", "linear": "мп"}


def _fmt(value):
    number = _number_value(value)
    return str(int(number)) if float(number).is_integer() else ("%g" % number)


def _signed(value):
    number = round(_number_value(value), 4)
    return ("-" if number < 0 else "+") + _fmt(abs(number))


def _measure_text(row, quantity):
    """«12,5 мп» - вимір рядка для заданої кількості штук; порожньо, коли
    вимір для цього товару не рахується."""
    if quantity is None:
        return ""
    product = row.get("product") or ""
    kind = row_measure_kind(plain_product_name(product), row.get("thickness"), row.get("width"))
    if kind is None:
        return ""
    piece = piece_measure(row.get("thickness"), row.get("width"), row.get("length"), kind)
    if piece <= 0:
        return ""
    # Кома, а не крапка - як в усіх числах програми.
    return "%s %s" % (_fmt(round(piece * _number_value(quantity), 3)).replace(".", ","), _UNIT_LABELS[kind])


def _plural(count):
    if count % 10 == 1 and count % 100 != 11:
        return "коррекцию"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return "коррекции"
    return "коррекций"


class CorrectionWindow:
    def __init__(self, parent, load_rows, apply_edits, colors=None):
        """load_rows() -> [{"row_id","product","breed","condition","size","now",...}];
        apply_edits(rows_by_id, edits, reason) -> {"ok","message",...}."""
        self.load_rows = load_rows
        self.apply_edits = apply_edits
        self.colors = dict(DEFAULT_COLORS, **(colors or {}))
        colors = self.colors
        self.all_rows = []
        self.rows_by_id = {}
        self.visible = []
        self.edits = {}
        self.popup = None
        self.editor = None
        self.filters = self._empty_filters()

        window = tk.Toplevel(parent)
        self.window = window
        window.title("Коррекция остатков")
        window.geometry("1120x620")
        window.minsize(860, 440)
        window.configure(bg=colors["bg"])
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.bind("<Escape>", lambda event: self._on_escape())

        top = ctk.CTkFrame(window, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(12, 4))
        ghost_button(top, colors, "‹ Назад", command=self.close, width=90).pack(side="left")
        ctk.CTkLabel(top, text="Коррекция остатков", text_color=colors["fg"], font=ctk.CTkFont(size=16, weight="bold")).pack(side="left", padx=(12, 0))
        ctk.CTkLabel(top, text="Esc — назад", text_color=colors["muted"], font=ctk.CTkFont(size=10)).pack(side="left", padx=(10, 0))
        ctk.CTkLabel(
            window,
            text="Клик по «Изменить на: шт» — ввод нового количества; разницу в штуках и в м3/м2/мп программа посчитает сама. "
                 "Фильтры — в заголовках столбцов. Одна запись — один документ «Коррекция №N».",
            text_color=colors["muted"], font=ctk.CTkFont(size=11), justify="left", wraplength=900, anchor="w",
        ).pack(fill="x", padx=16, pady=(0, 8))

        # Варіант 05 (2026-09-07): між «Назад» і таблицею смуга з кнопкою,
        # яка відкриває окреме вікно; таблиця під ним не рухається.
        strip = ctk.CTkFrame(window, fg_color="transparent")
        strip.pack(fill="x", padx=16, pady=(0, 8))
        ghost_button(strip, colors, "+ Новый размер…", command=self.open_new_size, width=170).pack(side="left")
        self.new_size_dialog = None
        self.new_counter = 0
        # Клік будь-де поза рядком фіксує набрану цифру (2026-09-07).
        window.bind("<Button-1>", self._on_any_click, add="+")

        self.table = CanvasTable(window, colors, COLUMNS, on_heading_click=self._open_column_filter, on_cell_click=self._on_cell_click)
        self.table.before_scroll = self._on_table_scroll
        self.table.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        bottom = ctk.CTkFrame(window, fg_color="transparent")
        bottom.pack(fill="x", padx=16, pady=(0, 12))
        self.reason_var = tk.StringVar(value="инвентаризация")
        entry(bottom, colors, self.reason_var, width=260, placeholder="Причина").pack(side="left")
        self.status = ctk.CTkLabel(bottom, text="", text_color=colors["muted"], font=ctk.CTkFont(size=12), anchor="w")
        self.status.pack(side="left", padx=(12, 0), fill="x", expand=True)
        self.write_button = accent_button(bottom, colors, "Записать", command=self.write, width=200, state="disabled")
        self.write_button.pack(side="right")
        ghost_button(bottom, colors, "Сбросить фильтры", command=self.reset_filters, width=140).pack(side="right", padx=(0, 8))
        self.reload()

    # ---------------- дані ----------------
    @staticmethod
    def _empty_filters():
        return {"product": None, "breed": None, "condition": None, "size": None, "now_min": "", "now_max": "",
                "only_edited": False, "sign_plus": True, "sign_minus": True}

    def reload(self):
        self.all_rows = list(self.load_rows())
        conditions = {str(row.get("condition") or "").strip() for row in self.all_rows} - {""}
        for row in self.all_rows:
            row["product_label"] = _product_label(row, conditions)
        self.rows_by_id = {row["row_id"]: row for row in self.all_rows}
        self.render()

    @staticmethod
    def _field(row, key):
        # «Продукт» показується без стану: «Доска», а не «Доска AD» (стан у
        # своїй колонці) - рішення користувача 2026-09-07. Для запису
        # rows_by_id зберігає первинну назву в row["product"].
        return row.get("product_label") if key == "product" else row.get(key)

    def facet(self, key):
        return sorted({str(self._field(row, key) or "") for row in self.all_rows if self._field(row, key) not in (None, "")})

    def _passes(self, row):
        f = self.filters
        for key in ("product", "breed", "condition", "size"):
            if f[key] is not None and str(self._field(row, key) or "") not in f[key]:
                return False
        now = _number_value(row.get("now"))
        if str(f["now_min"]).strip() and now < _number_value(f["now_min"]):
            return False
        if str(f["now_max"]).strip() and now > _number_value(f["now_max"]):
            return False
        edited = row["row_id"] in self.edits and abs(self.edits[row["row_id"]] - now) > 1e-9
        if f["only_edited"] and not edited:
            return False
        if not (f["sign_plus"] and f["sign_minus"]):
            delta = (self.edits[row["row_id"]] - now) if edited else 0
            if not f["sign_plus"] and delta > 0:
                return False
            if not f["sign_minus"] and delta < 0:
                return False
            if not f["sign_plus"] and not f["sign_minus"]:
                return False
        return True

    def _on_any_click(self, event):
        """Клік будь-де ПОЗА таблицею фіксує набране й закриває поле.

        Кліки по самій таблиці сюди не доходять свідомо: полотно обробляє
        свій <Button-1> ПЕРШИМ (_on_body_click -> _on_cell_click), і якби цей
        обробник відпрацьовував після нього, він закривав би щойно відкрите
        поле - у рядок неможливо було б нічого ввести (живий випадок
        2026-09-07 після 0.3.35). Збереження при кліку по таблиці робить сам
        _on_cell_click.
        """
        if self.editor is None:
            return
        widget = str(event.widget)
        for owner in (self.editor, self.table.body, self.table.header):
            path = str(owner)
            if widget == path or widget.startswith(path + "."):
                return
        self._close_editor()
        self.render()

    # ---------------- новий розмір / продукт ----------------
    def open_new_size(self):
        self._close_editor()
        self._close_popup()
        existing = self.new_size_dialog
        if existing is not None and existing.window.winfo_exists():
            existing.window.lift()
            return existing
        self.new_size_dialog = NewSizeDialog(self)
        return self.new_size_dialog

    @staticmethod
    def _identity_label(row):
        return (str(row.get("product_label") or row.get("product") or "").strip().lower(), str(row.get("breed") or "").strip().lower(),
                str(row.get("condition") or "").strip().lower(), str(row.get("size") or "").replace("×", "x").strip().lower())

    def add_new_row(self, product, breed, condition, thickness, width, length, quantity, unit_kind=None, new_product=False):
        """Рядок «новая» в кінець таблиці (нічого не пересортовується). Якщо
        такий розмір уже є - «Изменить на» підставляється в наявний рядок."""
        size = "x".join(_fmt(value) for value in (thickness, width, length))
        wanted = (product.strip().lower(), breed.strip().lower(), condition.strip().lower(), size.lower())
        for row in self.all_rows:
            if self._identity_label(row) == wanted:
                self.edits[row["row_id"]] = float(quantity)
                self.render()
                self.status.configure(text="Такой размер уже есть — «Изменить на» подставлено в его строку.")
                return row["row_id"]
        self.new_counter += 1
        row_id = "new:%d" % self.new_counter
        row = {"row_id": row_id, "product": product, "product_label": product, "breed": breed, "condition": condition,
               "thickness": thickness, "width": width, "length": length, "size": size, "now": 0,
               "new": True, "new_product": new_product, "unit_kind": unit_kind}
        self.all_rows.append(row)
        self.rows_by_id[row_id] = row
        self.edits[row_id] = float(quantity)
        self.render()
        return row_id

    def render(self):
        self._close_editor()
        # Рядок «новая» без цифри (стерли «Изменить на») зникає.
        dropped = [row for row in self.all_rows if row.get("new") and row["row_id"] not in self.edits]
        if dropped:
            self.all_rows = [row for row in self.all_rows if row not in dropped]
            for row in dropped:
                self.rows_by_id.pop(row["row_id"], None)
        self.visible = [row for row in self.all_rows if self._passes(row)]
        table_rows = []
        for row in self.visible:
            new = self.edits.get(row["row_id"])
            now = _number_value(row.get("now"))
            delta = (new - now) if new is not None else 0
            table_rows.append({
                "id": row["row_id"],
                "values": {
                    "product": (row.get("product_label") or row.get("product") or "") + ("   новая" if row.get("new") else ""),
                    "breed": row.get("breed") or "", "condition": row.get("condition") or "",
                    "size": str(row.get("size") or "").replace("x", "×"), "now": _fmt(now),
                    "now_measure": _measure_text(row, now),
                    "new": _fmt(new) if new is not None else "",
                    "new_measure": _measure_text(row, new) if new is not None else "",
                    "delta": _signed(delta) if new is not None and abs(delta) > 1e-9 else "",
                },
                "signs": {"delta": 1 if delta > 0 else (-1 if delta < 0 else 0)},
            })
        self.table.set_rows(table_rows)
        self._render_headings()
        self._refresh_button()

    def changed(self):
        return [(row_id, new) for row_id, new in self.edits.items()
                if row_id in self.rows_by_id and abs(new - _number_value(self.rows_by_id[row_id]["now"])) > 1e-9]

    def _refresh_button(self):
        count = len(self.changed())
        if not count:
            self.write_button.configure(text="Записать", state="disabled")
        else:
            self.write_button.configure(text="Записать %d %s" % (count, _plural(count)), state="normal")
        self.status.configure(text="Показано %d из %d" % (len(self.visible), len(self.all_rows)) + ("" if not count else " · изменено: %d" % count))

    # ---------------- правка клітинки ----------------
    def _on_cell_click(self, index, key):
        if key != "new" or index >= len(self.visible):
            # Клік по іншій колонці - те саме, що клік поза таблицею:
            # набране зберігається, поле закривається.
            if self.editor is not None:
                self._close_editor()
                self.render()
            return
        self.start_edit(index)

    def start_edit(self, index):
        self._close_editor()
        self._close_popup()
        row = self.visible[index]
        box = self.table.cell_box(index, "new")
        if box is None:
            return
        x, y, width, height = box
        var = tk.StringVar(value=_fmt(self.edits[row["row_id"]]) if row["row_id"] in self.edits else "")
        editor = ctk.CTkEntry(self.table.body, textvariable=var, width=width - 8, height=height - 6, corner_radius=6,
                              fg_color=self.colors["row"], border_color=self.colors["accent"], text_color=self.colors["fg"], justify="right")
        editor.place(x=x + 4, y=y + 3)
        editor.focus_set()
        row_id = row["row_id"]
        self.editor = editor
        self.editor_var = var
        self.editor_row_id = row_id
        self.editor_previous = self.edits.get(row_id)

        # Рішення користувача (2026-09-07): «потрібно ввести і натиснути
        # ентер - безтолкова дія; не натиснув - не зберігає». Цифра
        # зберігається з кожною клавішею, а будь-яке закриття редактора
        # (Enter, Tab, клік по іншій клітинці, «Записать», фільтр) її
        # лишає. Лише Esc повертає, як було.
        def commit(_event=None):
            if self.editor is not editor:
                return
            self._close_editor()
            self.render()

        def cancel(_event=None):
            if self.editor is editor:
                self._cancel_editor()
                self.render()
            return "break"

        editor.bind("<Return>", commit)
        editor.bind("<Tab>", commit)
        editor.bind("<FocusOut>", commit)
        editor.bind("<Escape>", cancel)
        editor.bind("<KeyRelease>", lambda _event: self._on_editor_typed())

    def _on_table_scroll(self):
        # Редактор не їде за прокруткою: перед нею закривається, зберігши
        # цифру в своєму рядку (2026-09-07).
        if self.editor is not None:
            self._close_editor()
            self.render()

    def _on_editor_typed(self):
        if self.editor is None:
            return
        self.set_edit(self.editor_row_id, self.editor_var.get())
        self._refresh_button()

    def set_edit(self, row_id, text):
        """Порожньо - прибрати правку; число ≥ 0 - запам'ятати."""
        text = str(text).strip().replace(",", ".")
        if text == "":
            self.edits.pop(row_id, None)
            return True
        try:
            value = float(text)
        except ValueError:
            return False
        if value < 0:
            return False
        self.edits[row_id] = value
        return True

    def _close_editor(self):
        """Закрити редактор, ЗБЕРІГШИ набране."""
        if self.editor is not None:
            editor, self.editor = self.editor, None
            self.set_edit(self.editor_row_id, self.editor_var.get())
            editor.destroy()

    def _cancel_editor(self):
        """Закрити редактор, повернувши значення, яке було до нього."""
        if self.editor is not None:
            editor, self.editor = self.editor, None
            if self.editor_previous is None:
                self.edits.pop(self.editor_row_id, None)
            else:
                self.edits[self.editor_row_id] = self.editor_previous
            editor.destroy()

    def _on_escape(self):
        if self.editor is not None:
            self._cancel_editor()
            self.render()
            return
        if self.popup is not None:
            self._close_popup()
            return
        self.close()

    # ---------------- фільтри в заголовках ----------------
    def _column_active(self, key):
        f = self.filters
        return {
            "product": f["product"] is not None, "breed": f["breed"] is not None, "condition": f["condition"] is not None,
            "size": f["size"] is not None, "now": bool(str(f["now_min"]).strip() or str(f["now_max"]).strip()),
            "new": f["only_edited"], "delta": not (f["sign_plus"] and f["sign_minus"]),
        }.get(key, False)

    def _render_headings(self):
        for column in COLUMNS:
            self.table.markers[column["key"]] = " ●" if self._column_active(column["key"]) else " ▾"
        self.table.redraw_header()

    def heading_text(self, key):
        return self.table.heading_text(key)

    def reset_filters(self):
        self.filters = self._empty_filters()
        self._close_popup()
        self.render()

    def _open_column_filter(self, key, x_root=None, y_root=None):
        self._close_editor()
        self._close_popup()
        colors = self.colors
        f = self.filters
        x = (x_root - 20) if x_root is not None else self.table.winfo_rootx()
        y = self.table.winfo_rooty() + CanvasTable.HEADER_H + 2
        popup = Popup(self.table, colors, x=x, y=y)
        self.popup = popup
        frame = popup.frame
        heading = next(column["label"] for column in COLUMNS if column["key"] == key)
        tk.Label(frame, text=heading, font=("Segoe UI", 11, "bold"), bg=colors["row"], fg=colors["fg"]).pack(anchor="w")
        actions = []
        if key in ("product", "breed", "condition", "size"):
            values = self.facet(key)
            placeholder = {"product": "Выберите продукт…", "breed": "Выберите породу…", "condition": "Выберите состояние…", "size": "Выберите размер…"}[key]
            chooser = MultiChoice(frame, colors, values, f[key], placeholder=placeholder,
                                  display=(lambda v: str(v).replace("x", "×")) if key == "size" else None)
            chooser.frame.pack(anchor="w", pady=(4, 0))
            self.chooser = chooser
            actions.append(lambda: f.__setitem__(key, chooser.result()))
        elif key == "now":
            min_var = tk.StringVar(value=str(f["now_min"]))
            max_var = tk.StringVar(value=str(f["now_max"]))
            row = tk.Frame(frame, bg=colors["row"])
            row.pack(anchor="w", pady=(6, 0))
            tk.Label(row, text="от:", bg=colors["row"], fg=colors["fg"]).pack(side="left")
            entry(row, colors, min_var, width=80).pack(side="left", padx=(4, 10))
            tk.Label(row, text="до:", bg=colors["row"], fg=colors["fg"]).pack(side="left")
            entry(row, colors, max_var, width=80).pack(side="left", padx=(4, 0))
            actions.append(lambda: (f.__setitem__("now_min", min_var.get()), f.__setitem__("now_max", max_var.get())))
        elif key == "new":
            only_var = tk.BooleanVar(value=f["only_edited"])
            checkbox(frame, colors, "Только изменённые", only_var).pack(anchor="w", pady=(4, 0))
            actions.append(lambda: f.__setitem__("only_edited", only_var.get()))
        elif key == "delta":
            plus_var = tk.BooleanVar(value=f["sign_plus"])
            minus_var = tk.BooleanVar(value=f["sign_minus"])
            checkbox(frame, colors, "Плюс", plus_var, text_color=colors["plus"], accent=colors["plus"]).pack(anchor="w", pady=2)
            checkbox(frame, colors, "Минус", minus_var, text_color=colors["minus"], accent=colors["minus"]).pack(anchor="w", pady=2)
            actions.append(lambda: (f.__setitem__("sign_plus", plus_var.get()), f.__setitem__("sign_minus", minus_var.get())))
        foot = tk.Frame(frame, bg=colors["row"])
        foot.pack(anchor="w", pady=(10, 0))
        accent_button(foot, colors, "Применить", command=lambda: self._apply_popup(actions), width=110).pack(side="left")
        clear_text = "Все" if key in ("product", "breed", "condition", "size") else "Очистить"
        ghost_button(foot, colors, clear_text, command=lambda: self._clear_column(key), width=96).pack(side="left", padx=(6, 0))

    def _apply_popup(self, actions):
        for action in actions:
            action()
        self._close_popup()
        self.render()

    def _clear_column(self, key):
        empty = self._empty_filters()
        f = self.filters
        if key in ("product", "breed", "condition", "size"):
            f[key] = None
        elif key == "now":
            f["now_min"], f["now_max"] = "", ""
        elif key == "new":
            f["only_edited"] = False
        elif key == "delta":
            f["sign_plus"], f["sign_minus"] = empty["sign_plus"], empty["sign_minus"]
        self._close_popup()
        self.render()

    def _close_popup(self):
        if self.popup is not None:
            self.popup.close()
            self.popup = None

    # ---------------- запис ----------------
    @staticmethod
    def _identity(row):
        return (str(row.get("product") or "").strip().lower(), str(row.get("breed") or "").strip().lower(),
                str(row.get("condition") or "").strip().lower(), str(row.get("size") or "").replace("×", "x").strip().lower())

    def _refresh_row_ids(self):
        """Перед записом: номери рядків складу могли змінитись (перечитування
        Excel створює рядки заново) - переносимо правки на свіжі номери за
        ознаками рядка. Повертає список правок, чиї рядки зникли."""
        fresh = list(self.load_rows())
        conditions = {str(row.get("condition") or "").strip() for row in fresh} - {""}
        for row in fresh:
            row["product_label"] = _product_label(row, conditions)
        pending = [row for row in self.all_rows if row.get("new")]
        by_identity = {self._identity(row): row["row_id"] for row in fresh}
        edits = {}
        lost = []
        for old_id, value in self.edits.items():
            old_row = self.rows_by_id.get(old_id)
            if old_row and old_row.get("new"):
                edits[old_id] = value
                continue
            new_id = by_identity.get(self._identity(old_row)) if old_row else None
            if new_id is None:
                lost.append(old_row or {"row_id": old_id})
            else:
                edits[new_id] = value
        self.edits = edits
        self.all_rows = fresh + pending
        self.rows_by_id = {row["row_id"]: row for row in self.all_rows}
        return lost

    def write(self):
        self._close_editor()
        lost = self._refresh_row_ids()
        self.render()
        if lost:
            lines = ["%s %s %s" % (row.get("product_label") or row.get("product") or "", row.get("breed") or "", str(row.get("size") or "").replace("x", "×")) for row in lost]
            messagebox.showerror("Коррекция остатков", "Эти позиции исчезли со склада после обновления таблицы:\n" + "\n".join(lines), parent=self.window)
            return
        changed = self.changed()
        if not changed:
            return
        if not messagebox.askyesno("Коррекция остатков", "Записать %d изменений одним документом «Коррекция №N»?" % len(changed), parent=self.window):
            return
        try:
            result = self.apply_edits(self.rows_by_id, dict(self.edits), self.reason_var.get().strip())
        except Exception as exc:
            messagebox.showerror("Коррекция остатков", str(exc), parent=self.window)
            return
        text = re.sub(r"</?b>", "", result.get("message") or "")
        if not result.get("ok"):
            messagebox.showerror("Коррекция остатков", text, parent=self.window)
            return
        self.edits.clear()
        self.reload()
        self.status.configure(text=text.split("\n")[0])
        messagebox.showinfo("Коррекция остатков", text, parent=self.window)

    def close(self):
        self._close_editor()
        self._close_popup()
        if self.window.winfo_exists():
            self.window.destroy()


class NewSizeDialog:
    """Окреме вікно «Новый размер» (варіант 05): продукт/порода/стан зі
    списків або свої, розмір, кількість, одиниця для нового продукту.
    «‹ Назад» угорі, Esc, ручки в чотирьох кутах, перетягування за шапку."""

    OWN = "+ свой…"
    NONE = "— нет —"
    UNITS = (("шт", "quantity"), ("м3", "volume"), ("м2", "area"), ("мп", "linear"))

    def __init__(self, owner):
        self.owner = owner
        colors = owner.colors
        self.colors = colors
        window = tk.Toplevel(owner.window)
        self.window = window
        window.title("Новый размер")
        window.configure(bg=colors["bg"])
        window.transient(owner.window)
        window.geometry("400x440")
        window.minsize(340, 400)
        bar = tk.Frame(window, bg=colors["bg"])
        bar.pack(fill="x", padx=14, pady=(12, 4))
        ghost_button(bar, colors, "‹ Назад", command=self.close, width=84, small=True).pack(side="left")
        title = tk.Label(bar, text="Новый размер", font=("Segoe UI", 11, "bold"), bg=colors["bg"], fg=colors["fg"])
        title.pack(side="left", padx=(10, 0))
        body = tk.Frame(window, bg=colors["bg"])
        body.pack(fill="both", expand=True, padx=16, pady=(4, 12))
        self.products = owner.facet("product")
        self.breeds = owner.facet("breed")
        self.conditions = owner.facet("condition")
        self.product_combo, self.product_own, self.product_own_entry = self._choice(body, "Продукт", self.products, "Выберите продукт…")
        self.breed_combo, self.breed_own, self.breed_own_entry = self._choice(body, "Порода", self.breeds, "Выберите породу…")
        self.condition_combo, self.condition_own, self.condition_own_entry = self._choice(body, "Состояние", self.conditions, self.NONE)
        tk.Label(body, text="Т × Ш × Д, мм", bg=colors["bg"], fg=colors["muted"], font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 0))
        dims = tk.Frame(body, bg=colors["bg"])
        dims.pack(anchor="w", pady=(2, 0))
        self.dim_vars = []
        for index in range(3):
            var = tk.StringVar()
            entry(dims, colors, var, width=76).pack(side="left", padx=(0, 6))
            var.trace_add("write", lambda *_a: self._update_unit_default())
            self.dim_vars.append(var)
        tk.Label(body, text="Количество, шт", bg=colors["bg"], fg=colors["muted"], font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 0))
        self.quantity_var = tk.StringVar()
        entry(body, colors, self.quantity_var, width=110).pack(anchor="w", pady=(2, 0))
        self.unit_row = tk.Frame(body, bg=colors["bg"])
        tk.Label(self.unit_row, text="Единица нового продукта", bg=colors["bg"], fg=colors["muted"], font=("Segoe UI", 9)).pack(anchor="w")
        self.unit_control = ctk.CTkSegmentedButton(self.unit_row, values=[label for label, _kind in self.UNITS], height=28,
                                                   selected_color=colors["accent"], selected_hover_color=colors["accent"],
                                                   unselected_color=colors["row"], unselected_hover_color=colors["hover"],
                                                   text_color=colors["fg"], font=ctk.CTkFont(size=12))
        self.unit_control.pack(anchor="w", pady=(2, 0))
        self.unit_control.set("м3")
        self.error = tk.Label(body, text="", bg=colors["bg"], fg=colors["minus"], font=("Segoe UI", 9), anchor="w", wraplength=340, justify="left")
        self.error.pack(anchor="w", pady=(8, 0))
        buttons = tk.Frame(body, bg=colors["bg"])
        buttons.pack(fill="x", pady=(8, 0), side="bottom")
        accent_button(buttons, colors, "Добавить в таблицу", command=self.add, width=170).pack(side="right")
        ghost_button(buttons, colors, "Отмена", command=self.close, width=90).pack(side="right", padx=(0, 8))
        add_window_grips(window, colors, handle=bar)
        window.bind("<Escape>", lambda event: self.close())
        window.protocol("WM_DELETE_WINDOW", self.close)
        self._update_unit_default()

    def _choice(self, parent, label, values, placeholder):
        colors = self.colors
        tk.Label(parent, text=label, bg=colors["bg"], fg=colors["muted"], font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 0))
        own_var = tk.StringVar()
        own_entry = entry(parent, colors, own_var, width=250, placeholder="новое значение")
        combo = ctk.CTkComboBox(
            parent, values=[placeholder] + list(values) + [self.OWN], width=250, height=30, corner_radius=8, state="readonly",
            fg_color=colors["row"], border_color=colors["line"], text_color=colors["fg"], button_color=colors["accent"],
            button_hover_color=colors["accent"], dropdown_fg_color=colors["row"], dropdown_text_color=colors["fg"],
            dropdown_hover_color=colors["hover"], command=lambda value, e=own_entry: self._on_choice(value, e),
        )
        combo.set(placeholder)
        combo.pack(anchor="w", pady=(2, 0))
        own_var.trace_add("write", lambda *_a: self._update_unit_default())
        return combo, own_var, own_entry

    def _on_choice(self, value, own_entry):
        if value == self.OWN:
            own_entry.pack(anchor="w", pady=(4, 0))
        else:
            own_entry.pack_forget()
        self._update_unit_default()

    def _value(self, combo, own_var, placeholder):
        text = combo.get()
        if text == self.OWN:
            return own_var.get().strip()
        return "" if text == placeholder else text.strip()

    def product_text(self):
        return self._value(self.product_combo, self.product_own, "Выберите продукт…")

    def product_is_new(self):
        product = self.product_text()
        return bool(product) and product.lower() not in {p.lower() for p in self.products}

    def dims(self):
        values = []
        for index, var in enumerate(self.dim_vars):
            raw = var.get().strip().replace(",", ".")
            if raw == "":
                return None
            try:
                number = float(raw)
            except ValueError:
                return None
            if index == 2:
                number = _number_value(normalize_length_mm(number))
            if number <= 0:
                return None
            values.append(int(number) if float(number).is_integer() else number)
        return values

    def unit_visible(self):
        return bool(self.unit_row.winfo_manager())

    def unit_kind(self):
        label = self.unit_control.get()
        return dict(self.UNITS).get(label, "volume")

    def _update_unit_default(self):
        if not self.product_is_new():
            self.unit_row.pack_forget()
            return
        if not self.unit_visible():
            self.unit_row.pack(anchor="w", pady=(8, 0))
        dims = self.dims() or [None, None, None]
        kind = row_measure_kind(self.product_text(), dims[0], dims[1])
        self.unit_control.set({"volume": "м3", "area": "м2", "linear": "мп"}.get(kind, "шт"))

    def set_values(self, product=None, breed=None, condition=None, thickness=None, width=None, length=None, quantity=None, unit=None):
        for combo, own_var, own_entry, values, value in (
            (self.product_combo, self.product_own, self.product_own_entry, self.products, product),
            (self.breed_combo, self.breed_own, self.breed_own_entry, self.breeds, breed),
            (self.condition_combo, self.condition_own, self.condition_own_entry, self.conditions, condition),
        ):
            if value is None:
                continue
            if value in values:
                combo.set(value)
                self._on_choice(value, own_entry)
            elif value == "":
                combo.set(self.NONE if combo is self.condition_combo else combo.cget("values")[0])
                self._on_choice(combo.get(), own_entry)
            else:
                combo.set(self.OWN)
                self._on_choice(self.OWN, own_entry)
                own_var.set(value)
        for var, value in zip(self.dim_vars, (thickness, width, length)):
            if value is not None:
                var.set(str(value))
        if quantity is not None:
            self.quantity_var.set(str(quantity))
        self._update_unit_default()
        if unit is not None:
            self.unit_control.set(unit)

    def add(self):
        product = self.product_text()
        breed = self._value(self.breed_combo, self.breed_own, "Выберите породу…")
        condition = self._value(self.condition_combo, self.condition_own, self.NONE)
        dims = self.dims()
        raw_quantity = self.quantity_var.get().strip().replace(",", ".")
        try:
            quantity = float(raw_quantity)
        except ValueError:
            quantity = -1
        if not product:
            self.error.configure(text="Укажите продукт.")
            return None
        if dims is None:
            self.error.configure(text="Толщина, ширина и длина — числа больше нуля.")
            return None
        if quantity <= 0:
            self.error.configure(text="Количество — число больше нуля.")
            return None
        new_product = self.product_is_new()
        row_id = self.owner.add_new_row(product, breed, condition, dims[0], dims[1], dims[2], quantity,
                                        unit_kind=self.unit_kind() if new_product else None, new_product=new_product)
        self.close()
        return row_id

    def close(self):
        if self.window.winfo_exists():
            self.window.destroy()


def _product_label(row, conditions=()):
    """«Доска AD» → «Доска»; «Доска AD (рейка)» → «Доска (рейка)». Зрізається
    будь-який відомий стан (AD/KD…), бо продукт стан не носить - він у своїй
    колонці (рішення користувача 2026-09-07)."""
    product = str(row.get("product") or "")
    base = plain_product_name(product) or ""
    had_mark = base != product
    known = {str(row.get("condition") or "").strip()} | {str(value).strip() for value in conditions}
    for condition in sorted((c for c in known if c), key=len, reverse=True):
        if base.lower().endswith(" " + condition.lower()):
            base = base[: -len(condition)].rstrip()
            break
    return (base + " " + LATH_MARK) if had_mark else base


def open_correction_window(owner, attr, parent, load_rows, apply_edits, colors=None):
    existing = getattr(owner, attr, None)
    if existing is not None and existing.window.winfo_exists():
        existing.window.deiconify()
        existing.window.lift()
        existing.window.focus_force()
        existing.reload()
        return existing
    window = CorrectionWindow(parent, load_rows, apply_edits, colors=colors)
    setattr(owner, attr, window)
    return window
