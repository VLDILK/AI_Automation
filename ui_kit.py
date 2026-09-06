"""Спільний набір вигляду для вікон клієнта й домашки (2026-09-06): скруглені
кнопки CustomTkinter, підписи, спливаюче вікно без рамки та таблиця на
полотні з кольоровими позначками (журнал операцій, корекція залишків,
прев'ю кольорів). Один вигляд - одне місце."""

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

import customtkinter as ctk

DEFAULT_COLORS = {
    "bg": "#EDEFF2", "fg": "#20242A", "muted": "#5B6470", "row": "#FFFFFF", "zebra": "#F7F8FA", "line": "#D5D9DF",
    "head": "#E9ECF0", "accent": "#3B6EA5", "accent_fg": "#FFFFFF", "hover": "#DCE8F6",
    "plus": "#0F6E56", "minus": "#B42318", "dark": False,
}


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


def accent_button(parent, colors, text, command=None, width=None, **kwargs):
    options = dict(text=text, command=command, fg_color=colors["accent"], hover_color=colors["accent"],
                   text_color=colors["accent_fg"], corner_radius=8, height=32, font=ctk.CTkFont(size=12, weight="bold"))
    if width is not None:
        options["width"] = width
    options.update(kwargs)
    return ctk.CTkButton(parent, **options)


def caption(parent, colors, text):
    return ctk.CTkLabel(parent, text=text, text_color=colors["muted"], font=ctk.CTkFont(size=10))


def checkbox(parent, colors, text, variable, text_color=None, accent=None):
    color = accent or colors["accent"]
    return ctk.CTkCheckBox(parent, text=text, variable=variable, text_color=text_color or colors["fg"], fg_color=color,
                           hover_color=color, border_color=colors["line"], checkbox_width=18, checkbox_height=18,
                           corner_radius=5, font=ctk.CTkFont(size=12))


def entry(parent, colors, variable, width=220, placeholder=""):
    return ctk.CTkEntry(parent, textvariable=variable, width=width, height=30, corner_radius=8, fg_color=colors["row"],
                        border_color=colors["line"], text_color=colors["fg"], placeholder_text=placeholder)


class MultiChoice:
    """Вибір кількох значень (рішення користувача 2026-09-06): закритий
    випадний список «Выберите…», під ним «Добавить», нижче - вибране рядками
    з ✕. Поки список не відкрити, інших значень не видно. values - усі
    доступні, chosen - множина або None (= усі); display - як показувати."""

    def __init__(self, parent, colors, values, chosen=None, placeholder="Выберите…", display=None, width=250):
        self.colors = colors
        self.values = list(values)
        self.display = display or (lambda value: str(value))
        self.placeholder = placeholder
        self.chosen = [value for value in self.values if chosen and value in chosen] if chosen else []
        self.frame = tk.Frame(parent, bg=colors["row"])
        self.combo = ctk.CTkComboBox(
            self.frame, values=self._available(), width=width, height=30, corner_radius=8, state="readonly",
            fg_color=colors["row"], border_color=colors["line"], text_color=colors["fg"], button_color=colors["accent"],
            button_hover_color=colors["accent"], dropdown_fg_color=colors["row"], dropdown_text_color=colors["fg"],
            dropdown_hover_color=colors["hover"],
        )
        self.combo.set(placeholder)
        self.combo.pack(anchor="w")
        self.add_button = ghost_button(self.frame, colors, "Добавить", command=self.add, small=True, width=100)
        self.add_button.pack(anchor="w", pady=(6, 0))
        self.rows = tk.Frame(self.frame, bg=colors["row"])
        self.rows.pack(anchor="w", fill="x", pady=(4, 0))
        self._render_rows()

    def _available(self):
        shown = [self.display(value) for value in self.values if value not in self.chosen]
        return shown or [""]

    def add(self, value=None):
        if value is None:
            text = self.combo.get()
            value = next((v for v in self.values if self.display(v) == text), None)
        if value is None or value in self.chosen or value not in self.values:
            return False
        self.chosen.append(value)
        self._refresh()
        return True

    def remove(self, value):
        if value in self.chosen:
            self.chosen.remove(value)
            self._refresh()

    def _refresh(self):
        self.combo.configure(values=self._available())
        self.combo.set(self.placeholder)
        self._render_rows()

    def _render_rows(self):
        for child in self.rows.winfo_children():
            child.destroy()
        colors = self.colors
        for value in self.chosen:
            row = tk.Frame(self.rows, bg=colors["row"], highlightthickness=1, highlightbackground=colors["line"])
            row.pack(fill="x", pady=2)
            tk.Label(row, text=self.display(value), bg=colors["row"], fg=colors["fg"], font=("Segoe UI", 10), anchor="w").pack(side="left", padx=(8, 6), pady=3)
            cross = tk.Label(row, text="✕", bg=colors["row"], fg=colors["minus"], font=("Segoe UI", 10, "bold"), cursor="hand2")
            cross.pack(side="right", padx=(0, 8))
            cross.bind("<Button-1>", lambda event, v=value: self.remove(v))

    def result(self):
        """Множина вибраних або None, коли нічого не додано (= усі).
        Вибране у списку, але ще не додане кнопкою, теж рахується
        (рішення користувача 2026-09-07: «потрібен додатковий рух - помилка»)."""
        self.add()
        return set(self.chosen) if self.chosen else None


class Popup:
    """Спливаюче вікно біля елемента: без рамки, закривається по Esc, кліку
    поза ним або кнопкою всередині."""

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


class CanvasTable(tk.Frame):
    """Таблиця на полотні: шапка з клікабельними заголовками, рядки із
    зеброю. Колонка описується словником {key, label, width, weight, anchor,
    kind}: kind = "text" | "badge" (тло/текст із row["badges"][key]) |
    "signed" (колір за row["signs"][key]) | "edit" (поле для правки, значення
    підсвічене) | "delete" («✕»). Рядок: {"values": {key: text}, ...}."""
    HEADER_H = 34
    ROW_H = 30

    def __init__(self, parent, colors, columns, on_heading_click=None, on_cell_click=None):
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
        self.scroll = ttk.Scrollbar(inner, orient="vertical", command=self._yview)
        # Гачок «перед прокруткою» (2026-09-07): вікно корекції закриває
        # редактор клітинки, інакше поле, покладене на полотно за пікселями,
        # «їде» на інший рядок разом із прокруткою.
        self.before_scroll = None
        self.scroll.grid(row=1, column=1, sticky="ns")
        self.body.configure(yscrollcommand=self.scroll.set)
        self.body.bind("<Configure>", lambda event: self._layout())
        self.header.bind("<Button-1>", self._on_header_click)
        self.body.bind("<Button-1>", self._on_body_click)
        self.body.bind("<MouseWheel>", self._on_wheel)

    # Колесо не веде за межі вмісту (2026-09-06: «при прокрутці вгору
    # з'являється порожній простір») - вище першого рядка й нижче
    # останнього не крутиться.
    def _yview(self, *args):
        if self.before_scroll is not None:
            self.before_scroll()
        return self.body.yview(*args)

    def _on_wheel(self, event):
        if self.before_scroll is not None:
            self.before_scroll()
        first, last = self.body.yview()
        if event.delta > 0 and first <= 0:
            return "break"
        if event.delta < 0 and last >= 1:
            return "break"
        self.body.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    # --- геометрія ---
    def _layout(self):
        total = max(self.body.winfo_width(), 200)
        fixed = sum(column["width"] for column in self.columns if not column.get("weight"))
        weights = sum(column.get("weight", 0) for column in self.columns) or 1
        flex = max(total - fixed, 0)
        self.spans = []
        x = 0
        for column in self.columns:
            weight = column.get("weight", 0)
            width = column["width"] if not weight else max(column["width"], int(flex * weight / weights))
            self.spans.append((x, width))
            x += width
        self.redraw_header()
        self.redraw()

    def column_at(self, x):
        for column, (x0, width) in zip(self.columns, self.spans):
            if x0 <= x < x0 + width:
                return column["key"]
        return None

    # Рядок може мати підрядки (row["lines"] - список {values, signs,
    # prefix}): висота = ROW_H × кількість підрядків; колонки з per_line
    # малюються на кожному підрядку, решта - один раз, на першому.
    def row_height(self, row):
        return self.ROW_H * max(1, len(row.get("lines") or []))

    def row_top(self, index):
        top = 0
        for row in self.rows[:index]:
            top += self.row_height(row)
        return top

    def row_index_at(self, y):
        top = 0
        for index, row in enumerate(self.rows):
            height = self.row_height(row)
            if top <= y < top + height:
                return index
            top += height
        return None

    def cell_box(self, index, key):
        """(x, y, width, height) клітинки в координатах полотна тіла."""
        if not self.spans:
            self._layout()
        for column, (x0, width) in zip(self.columns, self.spans):
            if column["key"] == key:
                y0 = self.row_top(index) - self.body.canvasy(0)
                return x0, y0, width, self.ROW_H
        return None

    def _on_header_click(self, event):
        key = self.column_at(event.x)
        if key and self.on_heading_click is not None:
            self.on_heading_click(key, event.x_root, event.y_root)

    def _on_body_click(self, event):
        if self.on_cell_click is None:
            return
        index = self.row_index_at(self.body.canvasy(event.y))
        key = self.column_at(event.x)
        if index is not None and key:
            self.on_cell_click(index, key)

    # --- малювання ---
    def ellipsis(self, text, max_width, font=None):
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
            anchor = column.get("anchor", "w")
            if anchor == "e":
                self.header.create_text(x0 + width - 8, self.HEADER_H / 2, text=text, anchor="e", font=self.head_font, fill=fill)
            elif anchor == "center":
                self.header.create_text(x0 + width / 2, self.HEADER_H / 2, text=text, anchor="center", font=self.head_font, fill=fill)
            else:
                self.header.create_text(x0 + 8, self.HEADER_H / 2, text=self.ellipsis(text, width - 12, self.head_font), anchor="w", font=self.head_font, fill=fill)
        self.header.create_line(0, self.HEADER_H - 1, max(self.header.winfo_width(), 10), self.HEADER_H - 1, fill=colors["line"])

    def set_rows(self, rows):
        self.rows = list(rows)
        self.redraw()

    def append_rows(self, rows):
        self.rows.extend(rows)
        self.redraw()

    def update_row(self, index, values):
        self.rows[index]["values"].update(values)
        self.redraw()

    def _draw_cell(self, column, x0, span, y0, values, signs, badges, prefix=None):
        body = self.body
        colors = self.colors
        key = column["key"]
        kind = column.get("kind", "text")
        value = values.get(key, "")
        cy = y0 + self.ROW_H / 2
        anchor = column.get("anchor", "w")
        if kind == "badge":
            bg, fg = (badges or {}).get(key, (colors["head"], colors["fg"]))
            label = self.ellipsis(value, span - 24, self.bold)
            text_width = self.bold.measure(label)
            body.create_rectangle(x0 + 8, y0 + 6, x0 + 8 + text_width + 14, y0 + self.ROW_H - 6, fill=bg, outline="")
            body.create_text(x0 + 15, cy, text=label, anchor="w", font=self.bold, fill=fg)
        elif kind == "signed":
            sign = (signs or {}).get(key, 0)
            fill = colors["plus"] if sign > 0 else (colors["minus"] if sign < 0 else colors["fg"])
            body.create_text(x0 + span - 8, cy, text=value, anchor="e", font=self.bold if sign else self.font, fill=fill)
        elif kind == "edit":
            body.create_rectangle(x0 + 4, y0 + 4, x0 + span - 4, y0 + self.ROW_H - 4, fill=colors["hover"] if value else "", outline=colors["line"])
            body.create_text(x0 + span - 10, cy, text=value if value else "…", anchor="e",
                             font=self.bold if value else self.font, fill=colors["fg"] if value else colors["muted"])
        elif kind == "delete":
            body.create_text(x0 + span / 2, cy, text="✕", anchor="center", font=self.bold, fill=colors["minus"])
        elif anchor == "e":
            body.create_text(x0 + span - 8, cy, text=value, anchor="e", font=self.font, fill=colors["fg"])
        elif anchor == "center":
            body.create_text(x0 + span / 2, cy, text=value, anchor="center", font=self.font, fill=colors["fg"])
        else:
            x = x0 + 8
            if prefix and column.get("prefixed"):
                # «отдаём» / «получаем» - тихим кольором перед товаром.
                body.create_text(x, cy, text=prefix, anchor="w", font=self.font, fill=colors["muted"])
                x += self.font.measure(prefix) + 6
            muted = column.get("muted", False)
            body.create_text(x, cy, text=self.ellipsis(value, span - (x - x0) - 6), anchor="w", font=self.font,
                             fill=colors["muted"] if muted else colors["fg"])

    def redraw(self):
        body = self.body
        body.delete("all")
        colors = self.colors
        width = max(body.winfo_width(), sum(w for _x, w in self.spans) if self.spans else 200)
        top = 0
        for index, row in enumerate(self.rows):
            height = self.row_height(row)
            if index % 2:
                body.create_rectangle(0, top, width, top + height, fill=colors["zebra"], outline="")
            lines = row.get("lines") or []
            for column, (x0, span) in zip(self.columns, self.spans):
                if lines and column.get("per_line"):
                    for line_index, line in enumerate(lines):
                        self._draw_cell(column, x0, span, top + line_index * self.ROW_H, line.get("values") or {},
                                        line.get("signs") or {}, row.get("badges"), prefix=line.get("prefix"))
                else:
                    self._draw_cell(column, x0, span, top, row.get("values") or {}, row.get("signs") or {}, row.get("badges"))
            top += height
        # Область прокрутки не менша за видиму частину: коротка таблиця не
        # «пливе» вниз, лишаючи порожнє місце над першим рядком.
        body.configure(scrollregion=(0, 0, width, max(top, body.winfo_height(), 1)))
