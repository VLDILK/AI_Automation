"""Вікно «Цвета операций» у клієнті (Задача користувача, 2026-09-06, варіант
02): ліворуч список операцій із поточними позначками, праворуч палітра для
обраної й прев'ю (журнал програми + картка форми) у світлій і темній темі
одразу - людина бачить читабельність в обох. «Сохранить», «Сбросить к
стандартным», «‹ Назад», Esc, ручки зміни розміру."""

import tkinter as tk
from tkinter import colorchooser, messagebox

import customtkinter as ctk

from operation_colors import (
    DEFAULT_OPERATION_COLORS, MIN_CONTRAST, OPERATION_LABELS, OPERATION_TYPES, PALETTE, base_colors, contrast,
    derive_pair, palette_for, parse_hex, readable,
)
from ui_kit import DEFAULT_COLORS, CanvasTable, accent_button, caption, ghost_button

PREVIEW_ROWS = (
    ("income", "11:05", "Приход", "Доска AD (рейка) 30×50×3000", "+643", 1),
    ("sale", "14:32", "Продажа", "Доска KD 47×150×6000", "-10", -1),
    ("writeoff", "09:12", "Списание", "Доска KD 47×150×6000", "-3", -1),
    ("exchange", "18:43", "Обмен", "Доска AD 60×150×6000", "-12", -1),
    ("antiseptic", "14:00", "Антисептирование", "Доска AD 50×150×6000", "2,1 м3", 0),
    ("correction", "09:41", "Коррекция", "Доска KD 47×100×6000", "+4", 1),
    ("rollback", "16:40", "Откат", "Доска AD 25×150×6000", "+120", 1),
)
LIGHT_PREVIEW = dict(DEFAULT_COLORS)
DARK_PREVIEW = dict(DEFAULT_COLORS, bg="#16181C", fg="#E5E7EA", muted="#9AA1AB", row="#1E2126", zebra="#23272D",
                    line="#353A42", head="#2A2F36", hover="#2C3E55", plus="#5FCFAA", minus="#F08A80", dark=True)


class ColorsWindow:
    def __init__(self, parent, current, on_save, colors=None):
        """current - {type: "#hex"} з налаштувань; on_save(dict) - записати."""
        self.on_save = on_save
        self.colors = dict(DEFAULT_COLORS, **(colors or {}))
        self.chosen = base_colors(current)
        self.selected = OPERATION_TYPES[0]
        self.saved_snapshot = dict(self.chosen)
        colors = self.colors

        window = tk.Toplevel(parent)
        self.window = window
        window.title("Цвета операций")
        window.geometry("980x640")
        window.minsize(820, 520)
        window.configure(bg=colors["bg"])
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.bind("<Escape>", lambda event: self.close())

        top = ctk.CTkFrame(window, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(12, 6))
        ghost_button(top, colors, "‹ Назад", command=self.close, width=90).pack(side="left")
        ctk.CTkLabel(top, text="Цвета операций", text_color=colors["fg"], font=ctk.CTkFont(size=16, weight="bold")).pack(side="left", padx=(12, 0))
        ctk.CTkLabel(top, text="Esc — назад", text_color=colors["muted"], font=ctk.CTkFont(size=10)).pack(side="left", padx=(10, 0))

        body = ctk.CTkFrame(window, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        self.list_frame = ctk.CTkFrame(body, fg_color=colors["row"], corner_radius=10, width=200)
        self.list_frame.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        self.op_buttons = {}
        for type_key in OPERATION_TYPES:
            button = ctk.CTkButton(
                self.list_frame, text=OPERATION_LABELS[type_key], corner_radius=8, height=36, width=180, anchor="w",
                font=ctk.CTkFont(size=13, weight="bold"), command=lambda key=type_key: self.select(key),
            )
            button.pack(padx=10, pady=(10 if type_key == OPERATION_TYPES[0] else 4, 0))
            self.op_buttons[type_key] = button
        ctk.CTkLabel(self.list_frame, text=" ", font=ctk.CTkFont(size=6)).pack(pady=(0, 6))

        right = ctk.CTkFrame(body, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        palette_card = ctk.CTkFrame(right, fg_color=colors["row"], corner_radius=10)
        palette_card.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.palette_caption = caption(palette_card, colors, "")
        self.palette_caption.pack(anchor="w", padx=12, pady=(8, 2))
        palette_row = ctk.CTkFrame(palette_card, fg_color="transparent")
        palette_row.pack(anchor="w", padx=12, pady=(0, 10))
        self.swatches = {}
        for color in PALETTE:
            swatch = tk.Label(palette_row, bg=color, width=3, height=1, cursor="hand2", bd=0, highlightthickness=3, highlightbackground=colors["row"])
            swatch.pack(side="left", padx=3)
            swatch.bind("<Button-1>", lambda event, c=color: self.pick(c))
            self.swatches[color] = swatch
        ghost_button(palette_row, colors, "Другой…", command=self.pick_other, small=True, width=80).pack(side="left", padx=(10, 0))
        self.contrast_label = ctk.CTkLabel(palette_card, text="", text_color=colors["muted"], font=ctk.CTkFont(size=11))
        self.contrast_label.pack(anchor="w", padx=12, pady=(0, 8))

        preview = ctk.CTkFrame(right, fg_color="transparent")
        preview.grid(row=1, column=0, sticky="nsew")
        preview.grid_columnconfigure(0, weight=1)
        preview.grid_columnconfigure(1, weight=1)
        preview.grid_rowconfigure(1, weight=1)
        caption(preview, colors, "ПРЕДПРОСМОТР — СВЕТЛАЯ ТЕМА").grid(row=0, column=0, sticky="w")
        caption(preview, colors, "ПРЕДПРОСМОТР — ТЁМНАЯ ТЕМА").grid(row=0, column=1, sticky="w", padx=(10, 0))
        self.previews = {}
        for column, (name, palette) in enumerate((("light", LIGHT_PREVIEW), ("dark", DARK_PREVIEW))):
            frame = tk.Frame(preview, bg=palette["bg"], bd=0, highlightthickness=1, highlightbackground=palette["line"])
            frame.grid(row=1, column=column, sticky="nsew", padx=(0 if column == 0 else 10, 0), pady=(4, 0))
            table = CanvasTable(frame, palette, (
                {"key": "time", "label": "Время", "width": 60, "weight": 0, "anchor": "w", "muted": True},
                {"key": "type", "label": "Операция", "width": 130, "weight": 0, "anchor": "w", "kind": "badge"},
                {"key": "product", "label": "Товар", "width": 120, "weight": 1, "anchor": "w"},
                {"key": "qty", "label": "± шт", "width": 60, "weight": 0, "anchor": "e", "kind": "signed"},
            ))
            table.pack(fill="both", expand=True, padx=8, pady=(8, 6))
            phone = tk.Frame(frame, bg=palette["zebra"], bd=0)
            phone.pack(fill="x", padx=8, pady=(0, 8))
            cards = []
            for type_key in ("exchange", "income"):
                card = tk.Frame(phone, bg=palette["row"], bd=0, highlightthickness=0)
                card.pack(fill="x", padx=6, pady=4)
                stripe = tk.Frame(card, width=4, bg=palette["line"])
                stripe.pack(side="left", fill="y")
                inner = tk.Frame(card, bg=palette["row"])
                inner.pack(side="left", fill="x", expand=True, padx=8, pady=5)
                head = tk.Frame(inner, bg=palette["row"])
                head.pack(anchor="w")
                tag = tk.Label(head, text="", font=("Segoe UI", 9, "bold"), padx=6, pady=1)
                tag.pack(side="left")
                tk.Label(head, text="  18:43 · Володимир" if type_key == "exchange" else "  11:05 · Олег", bg=palette["row"], fg=palette["muted"], font=("Segoe UI", 8)).pack(side="left")
                line = tk.Label(inner, text="отдаём 60×150×6000  −12 шт · получаем 60×120×4000  +12 шт" if type_key == "exchange" else "30×50×3000  +643 шт · +1929 мп",
                                bg=palette["row"], fg=palette["fg"], font=("Segoe UI", 9), anchor="w")
                line.pack(anchor="w")
                cards.append((type_key, stripe, tag))
            self.previews[name] = {"palette": palette, "table": table, "cards": cards}

        bottom = ctk.CTkFrame(window, fg_color="transparent")
        bottom.pack(fill="x", padx=16, pady=(0, 12))
        self.save_button = accent_button(bottom, colors, "Сохранить", command=self.save, width=130)
        self.save_button.pack(side="left")
        ghost_button(bottom, colors, "Сбросить к стандартным", command=self.reset, width=190).pack(side="left", padx=(8, 0))
        self.status = ctk.CTkLabel(bottom, text="", text_color=colors["muted"], font=ctk.CTkFont(size=12))
        self.status.pack(side="left", padx=(12, 0))
        self.select(self.selected)

    # ---------------- стан ----------------
    def select(self, type_key):
        self.selected = type_key
        self.render()

    def pick(self, color):
        self.chosen[self.selected] = color.upper()
        self.render()

    def pick_other(self):
        chosen = colorchooser.askcolor(color=self.chosen[self.selected], parent=self.window, title="Цвет операции «%s»" % OPERATION_LABELS[self.selected])
        if chosen and chosen[1]:
            self.pick(chosen[1])

    def reset(self):
        self.chosen = dict(DEFAULT_OPERATION_COLORS)
        self.render()

    def dirty(self):
        return self.chosen != self.saved_snapshot

    def render(self):
        colors = self.colors
        light = palette_for(self.chosen, False)
        dark = palette_for(self.chosen, True)
        client_palette = dark if colors.get("dark") else light
        for type_key, button in self.op_buttons.items():
            bg, fg = client_palette[type_key]
            active = type_key == self.selected
            button.configure(fg_color=bg, hover_color=bg, text_color=fg, border_width=2 if active else 0,
                             border_color=colors["accent"] if active else bg)
        self.palette_caption.configure(text="%s — цвет" % OPERATION_LABELS[self.selected].upper())
        current = self.chosen[self.selected].upper()
        for color, swatch in self.swatches.items():
            swatch.configure(highlightbackground=colors["fg"] if color.upper() == current else colors["row"])
        ok, worst = readable(self.chosen)
        lb, lf = light[self.selected]
        db, df = dark[self.selected]
        self.contrast_label.configure(
            text="Выбрано %s · контраст текста к фону: светлая %.1f, тёмная %.1f (нужно ≥ %.1f) · %s" % (
                current, contrast(parse_hex(lb), parse_hex(lf)), contrast(parse_hex(db), parse_hex(df)), MIN_CONTRAST,
                "все операции читаемы" if ok else "ЕСТЬ НЕЧИТАЕМЫЕ",
            ),
            text_color=colors["muted"] if ok else colors["minus"],
        )
        for name, preview in self.previews.items():
            palette = light if name == "light" else dark
            rows = []
            for type_key, time_text, label, product, qty, sign in PREVIEW_ROWS:
                rows.append({"values": {"time": time_text, "type": label, "product": product, "qty": qty},
                             "badges": {"type": palette[type_key]}, "signs": {"qty": sign}})
            preview["table"].set_rows(rows)
            for type_key, stripe, tag in preview["cards"]:
                bg, fg = palette[type_key]
                stripe.configure(bg=fg)
                tag.configure(text="Обмен №3" if type_key == "exchange" else "Приход №7", bg=bg, fg=fg)
        self.save_button.configure(state="normal" if self.dirty() else "disabled")
        self.status.configure(text="" if not self.dirty() else "есть несохранённые изменения")

    def save(self):
        ok, worst = readable(self.chosen)
        if not ok and not messagebox.askyesno("Цвета операций", "Часть меток плохо читается (контраст %.1f). Сохранить всё равно?" % worst, parent=self.window):
            return
        try:
            self.on_save(dict(self.chosen))
        except Exception as exc:
            messagebox.showerror("Цвета операций", str(exc), parent=self.window)
            return
        self.saved_snapshot = dict(self.chosen)
        self.render()
        self.save_button.configure(text="✓ Сохранено", fg_color=self.colors["plus"], state="disabled")
        self.status.configure(text="Цвета применены: журнал программы, форма бота, домашняя программа")
        self.window.after(2000, self._unflash)

    def _unflash(self):
        if self.window.winfo_exists():
            self.save_button.configure(text="Сохранить", fg_color=self.colors["accent"], state="normal" if self.dirty() else "disabled")

    def close(self):
        if self.dirty() and self.window.winfo_exists():
            if not messagebox.askyesno("Цвета операций", "Есть несохранённые изменения. Закрыть без сохранения?", parent=self.window):
                return
        if self.window.winfo_exists():
            self.window.destroy()


def open_colors_window(owner, attr, parent, current, on_save, colors=None):
    existing = getattr(owner, attr, None)
    if existing is not None and existing.window.winfo_exists():
        existing.window.deiconify()
        existing.window.lift()
        existing.window.focus_force()
        return existing
    window = ColorsWindow(parent, current, on_save, colors=colors)
    setattr(owner, attr, window)
    return window
