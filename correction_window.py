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

from ui_kit import DEFAULT_COLORS, CanvasTable, MultiChoice, Popup, accent_button, checkbox, entry, ghost_button
from utils import _number_value

COLUMNS = (
    {"key": "product", "label": "Продукт", "width": 140, "weight": 3, "anchor": "w"},
    {"key": "breed", "label": "Порода", "width": 100, "weight": 1, "anchor": "w"},
    {"key": "condition", "label": "Состояние", "width": 96, "weight": 0, "anchor": "w"},
    {"key": "size", "label": "Размер", "width": 120, "weight": 1, "anchor": "w"},
    {"key": "now", "label": "Сейчас, шт", "width": 96, "weight": 0, "anchor": "e"},
    {"key": "new", "label": "Изменить на: шт", "width": 132, "weight": 0, "anchor": "e", "kind": "edit"},
    {"key": "delta", "label": "±", "width": 80, "weight": 0, "anchor": "e", "kind": "signed"},
)


def _fmt(value):
    number = _number_value(value)
    return str(int(number)) if float(number).is_integer() else ("%g" % number)


def _signed(value):
    number = round(_number_value(value), 4)
    return ("-" if number < 0 else "+") + _fmt(abs(number))


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
        window.geometry("960x620")
        window.minsize(720, 440)
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

        self.table = CanvasTable(window, colors, COLUMNS, on_heading_click=self._open_column_filter, on_cell_click=self._on_cell_click)
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
        self.rows_by_id = {row["row_id"]: row for row in self.all_rows}
        self.render()

    def facet(self, key):
        return sorted({str(row.get(key) or "") for row in self.all_rows if row.get(key) not in (None, "")})

    def _passes(self, row):
        f = self.filters
        for key in ("product", "breed", "condition", "size"):
            if f[key] is not None and str(row.get(key) or "") not in f[key]:
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

    def render(self):
        self._close_editor()
        self.visible = [row for row in self.all_rows if self._passes(row)]
        table_rows = []
        for row in self.visible:
            new = self.edits.get(row["row_id"])
            now = _number_value(row.get("now"))
            delta = (new - now) if new is not None else 0
            table_rows.append({
                "id": row["row_id"],
                "values": {
                    "product": row.get("product") or "", "breed": row.get("breed") or "", "condition": row.get("condition") or "",
                    "size": str(row.get("size") or "").replace("x", "×"), "now": _fmt(now),
                    "new": _fmt(new) if new is not None else "",
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
        self.editor = editor
        row_id = row["row_id"]

        def commit(_event=None):
            if self.editor is not editor:
                return
            self.set_edit(row_id, var.get())
            self._close_editor()
            self.render()

        def cancel(_event=None):
            self._close_editor()
            return "break"

        editor.bind("<Return>", commit)
        editor.bind("<Tab>", commit)
        editor.bind("<FocusOut>", commit)
        editor.bind("<Escape>", cancel)

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
        if self.editor is not None:
            editor, self.editor = self.editor, None
            editor.destroy()

    def _on_escape(self):
        if self.editor is not None:
            self._close_editor()
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
    def write(self):
        self._close_editor()
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
