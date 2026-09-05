# -*- coding: utf-8 -*-
"""Редактор кнопок бота - один вигляд для клієнта й домашки.

Задача користувача (2026-09-05): "потрібно переробити налаштовування
кнопок. а то там все є що потрібно, але виглядає досить криво та нелогічно
як для користувача середнього класу знань ПК". Обраний варіант 01 із
пʼяти: ліворуч "телефон" - меню так, як його бачить людина в Telegram;
тапнув кнопку - праворуч її властивості: назва, що робить (один список
людською мовою), розмір, показувати чи ні, текст після натискання,
"вище/нижче" замість номера позиції, вкладені кнопки, видалити. Жодних
модальних вікон, зміни зберігаються одразу.

Чому окремий модуль: у клієнта кнопки лежать у власній базі, домашка
редагує живе дерево клієнта через тунель - але ВИГЛЯД має бути той самий.
Тому панель знає лише про "джерело" (ButtonSource нижче), а звідки беруться
рядки й куди йдуть зміни - справа кожної програми.

Мова написів - російська: так само говорить сам бот і решта екранів, де
адміністратор бачить те саме, що й співробітник у Telegram.
"""
import tkinter as tk
from tkinter import messagebox, ttk

# Кольори "телефона" - навмисно як у Telegram, щоб людина впізнала меню.
# Вони ж внесені в семантичні кольори домашки (gui.py), щоб перемикання
# теми їх не перефарбовувало.
PHONE_BG = "#17212B"
PHONE_BUBBLE = "#182533"
KEY_BG = "#2B5278"
KEY_SELECTED_BG = "#3D6E9E"
KEY_HIDDEN_BG = "#3A4A5A"
KEY_FG = "white"
KEY_HIDDEN_FG = "#B0B8C0"

SUBMENU_LABEL = "Открывает подменю (вложенные кнопки)"
MESSAGE_ONLY_LABEL = "Только отвечает текстом"
NEW_BUTTON_LABEL = "Новая кнопка"


class ButtonSource:
    """Що панель хоче від програми. Усі методи синхронні з погляду панелі:
    для віддаленого джерела apply() сам виконує дію у фоні й після успіху
    викликає then() на головному потоці (інакше панель перемалюється зі
    старими даними)."""

    def rows(self, parent_id):
        """[(id, label, message_text, action_code, section, enabled, layout, operation_id)]
        у порядку меню, разом із прихованими."""
        raise NotImplementedError

    def get(self, node_id):
        """(id, parent_id, label, message_text, action_code, section, enabled, layout, operation_id) або None."""
        raise NotImplementedError

    def actions(self):
        """[(code, label)] - стандартні дії."""
        raise NotImplementedError

    def operations(self):
        """[(operation_id, label)] - прямі посилання на "Дії"."""
        raise NotImplementedError

    def label_collides(self, label, exclude_id=None):
        return False

    def add(self, parent_id, label, layout):
        """Повертає id нової кнопки (для віддаленого джерела - через result)."""
        raise NotImplementedError

    def update(self, node_id, label, message_text, action_code, layout, operation_id):
        raise NotImplementedError

    def move(self, node_id, new_index):
        raise NotImplementedError

    def delete(self, node_id):
        raise NotImplementedError

    def set_enabled(self, node_id, enabled):
        raise NotImplementedError

    def apply(self, action, then):
        """Локально: action(); then(). Через тунель: у фоні, then() після успіху."""
        action()
        then()


class ButtonEditorPanel:
    """Телефон ліворуч, властивості праворуч. parent - будь-який tk-контейнер."""

    def __init__(self, parent, source, colors, on_changed=None):
        self.source = source
        self.colors = colors
        self.on_changed = on_changed
        self.parent_id = None
        self.selected_id = None
        self.show_hidden = tk.BooleanVar(value=False)
        self._suspend_commits = False

        self.frame = tk.Frame(parent, bg=colors["bg"])
        self.frame.pack(fill="both", expand=True)

        left = tk.Frame(self.frame, bg=colors["bg"], width=300)
        left.pack(side="left", fill="y", padx=(0, 16))
        left.pack_propagate(False)
        tk.Label(
            left, text="Так видит меню сотрудник в Telegram", font=("Segoe UI", 9, "bold"),
            fg=colors["fg"], bg=colors["bg"], anchor="w",
        ).pack(fill="x")
        self.breadcrumb = tk.Label(
            left, text="", font=("Segoe UI", 9), fg=colors["muted"], bg=colors["bg"], anchor="w",
        )
        self.breadcrumb.pack(fill="x", pady=(0, 6))

        self.phone = tk.Frame(left, bg=PHONE_BG, padx=10, pady=10)
        self.phone.pack(fill="x")
        self.bubble = tk.Label(
            self.phone, text="Главное меню. Выберите действие:", font=("Segoe UI", 9),
            fg="#DFE6EE", bg=PHONE_BUBBLE, padx=8, pady=4, anchor="w",
        )
        self.bubble.pack(anchor="w", pady=(0, 8))
        self.keyboard = tk.Frame(self.phone, bg=PHONE_BG)
        self.keyboard.pack(fill="x")
        self.keyboard.grid_columnconfigure(0, weight=1, uniform="key")
        self.keyboard.grid_columnconfigure(1, weight=1, uniform="key")

        controls = tk.Frame(left, bg=colors["bg"])
        controls.pack(fill="x", pady=(8, 0))
        tk.Button(
            controls, text="+ Новая кнопка", font=("Segoe UI", 9, "bold"),
            bg=colors["accent"], fg="white", activebackground=colors["accent"], activeforeground="white",
            relief="flat", padx=10, pady=5, cursor="hand2", command=self.add_button,
        ).pack(side="left")
        tk.Checkbutton(
            controls, text="Показывать скрытые", variable=self.show_hidden, command=self.refresh,
            font=("Segoe UI", 9), fg=colors["fg"], bg=colors["bg"], selectcolor=colors["card"],
            activebackground=colors["bg"], activeforeground=colors["fg"],
        ).pack(side="left", padx=(10, 0))

        self.props = tk.Frame(self.frame, bg=colors["card"], padx=14, pady=12)
        self.props.pack(side="left", fill="both", expand=True)
        self._build_props()
        self.refresh()

    # ---------- телефон ----------
    def refresh(self):
        for child in self.keyboard.winfo_children():
            child.destroy()
        rows = self.source.rows(self.parent_id)
        if not self.show_hidden.get():
            rows = [row for row in rows if row[5]]
        if self.parent_id is None:
            self.breadcrumb.configure(text="Главное меню")
            self.bubble.configure(text="Главное меню. Выберите действие:")
        else:
            parent = self.source.get(self.parent_id)
            parent_label = parent[2] if parent else "?"
            self.breadcrumb.configure(text=f"Главное меню  ›  {parent_label}")
            self.bubble.configure(text=f"{parent_label}. Выберите действие:")

        grid_row = 0
        if self.parent_id is not None:
            back = tk.Button(
                self.keyboard, text="← Назад", font=("Segoe UI", 9), fg=KEY_FG, bg=KEY_HIDDEN_BG,
                activebackground=KEY_HIDDEN_BG, activeforeground=KEY_FG, relief="flat", bd=0,
                padx=6, pady=6, cursor="hand2", command=self.go_up,
            )
            back.grid(row=grid_row, column=0, columnspan=2, sticky="ew", pady=(0, 3))
            grid_row += 1

        pending_half = None
        for row in rows:
            node_id, label, _message, _action, _section, enabled, layout, _operation = row
            if layout == "half":
                if pending_half is None:
                    pending_half = row
                    continue
                self._make_key(pending_half, grid_row, 0, 1)
                self._make_key(row, grid_row, 1, 1)
                pending_half = None
                grid_row += 1
                continue
            if pending_half is not None:
                self._make_key(pending_half, grid_row, 0, 2)
                pending_half = None
                grid_row += 1
            self._make_key(row, grid_row, 0, 2)
            grid_row += 1
        if pending_half is not None:
            self._make_key(pending_half, grid_row, 0, 2)
            grid_row += 1
        if grid_row == 0:
            tk.Label(
                self.keyboard, text="Кнопок пока нет — нажмите «+ Новая кнопка».",
                font=("Segoe UI", 9), fg=KEY_HIDDEN_FG, bg=PHONE_BG, wraplength=250, justify="left",
            ).grid(row=0, column=0, columnspan=2, sticky="w")

        if self.selected_id is not None and self.source.get(self.selected_id) is None:
            self.selected_id = None
        self._fill_props()

    def _make_key(self, row, grid_row, column, span):
        node_id, label, _message, _action, _section, enabled, _layout, _operation = row
        selected = node_id == self.selected_id
        text = label if enabled else f"{label}  \U0001F441"
        key = tk.Button(
            self.keyboard, text=text, font=("Segoe UI", 9, "bold" if selected else "normal"),
            fg=KEY_FG if enabled else KEY_HIDDEN_FG,
            bg=KEY_SELECTED_BG if selected else (KEY_BG if enabled else KEY_HIDDEN_BG),
            activebackground=KEY_SELECTED_BG, activeforeground=KEY_FG,
            relief="flat", bd=0, padx=6, pady=6, cursor="hand2", wraplength=120 if span == 1 else 250,
            command=lambda nid=node_id: self.select(nid),
        )
        key.grid(row=grid_row, column=column, columnspan=span, sticky="ew", padx=(0, 3) if (span == 1 and column == 0) else 0, pady=(0, 3))

    def select(self, node_id):
        self._flush_text()
        self.selected_id = node_id
        self.refresh()

    def go_up(self):
        self._flush_text()
        parent = self.source.get(self.parent_id) if self.parent_id is not None else None
        self.selected_id = self.parent_id
        self.parent_id = parent[1] if parent else None
        self.refresh()

    def enter_children(self):
        if self.selected_id is None:
            return
        self._flush_text()
        self.parent_id = self.selected_id
        self.selected_id = None
        self.refresh()

    # ---------- властивості ----------
    def _build_props(self):
        c = self.colors
        self.props_title = tk.Label(
            self.props, text="", font=("Segoe UI", 11, "bold"), fg=c["fg"], bg=c["card"], anchor="w",
        )
        self.props_title.pack(fill="x", pady=(0, 8))
        self.props_hint = tk.Label(
            self.props, text="Нажмите на кнопку в телефоне слева, чтобы её изменить.",
            font=("Segoe UI", 9), fg=c["muted"], bg=c["card"], anchor="w", justify="left", wraplength=360,
        )
        self.props_hint.pack(fill="x")

        self.props_body = tk.Frame(self.props, bg=c["card"])

        def caption(text):
            tk.Label(
                self.props_body, text=text.upper(), font=("Segoe UI", 8), fg=c["muted"], bg=c["card"], anchor="w",
            ).pack(fill="x", pady=(8, 2))

        caption("Название")
        self.name_var = tk.StringVar()
        self.name_entry = tk.Entry(
            self.props_body, textvariable=self.name_var, font=("Segoe UI", 10),
            bg=c["entry"], fg=c["fg"], insertbackground=c["fg"], relief="flat", highlightthickness=1,
            highlightbackground=c["border"], highlightcolor=c["accent"],
        )
        self.name_entry.pack(fill="x", ipady=4)
        self.name_entry.bind("<FocusOut>", lambda _e: self._commit_text())
        self.name_entry.bind("<Return>", lambda _e: self._commit_text())

        caption("Что делает")
        self.action_var = tk.StringVar()
        self.action_combo = ttk.Combobox(self.props_body, textvariable=self.action_var, state="readonly", font=("Segoe UI", 10))
        self.action_combo.pack(fill="x")
        self.action_combo.bind("<<ComboboxSelected>>", lambda _e: self._commit())

        caption("Размер")
        size_row = tk.Frame(self.props_body, bg=c["card"])
        size_row.pack(fill="x")
        self.layout_var = tk.StringVar(value="full")
        self.size_buttons = {}
        for value, text in (("full", "На всю ширину"), ("half", "Половина")):
            button = tk.Button(
                size_row, text=text, font=("Segoe UI", 9), relief="flat", bd=0, padx=10, pady=4, cursor="hand2",
                command=lambda v=value: self._set_layout(v),
            )
            button.pack(side="left", padx=(0, 4))
            self.size_buttons[value] = button

        self.enabled_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            self.props_body, text="Показывать в меню", variable=self.enabled_var, command=self._commit_enabled,
            font=("Segoe UI", 10), fg=c["fg"], bg=c["card"], selectcolor=c["entry"],
            activebackground=c["card"], activeforeground=c["fg"],
        ).pack(anchor="w", pady=(10, 0))

        caption("Текст после нажатия (необязательно)")
        self.message_text = tk.Text(
            self.props_body, height=3, font=("Segoe UI", 10), bg=c["entry"], fg=c["fg"],
            insertbackground=c["fg"], relief="flat", highlightthickness=1,
            highlightbackground=c["border"], highlightcolor=c["accent"], wrap="word",
        )
        self.message_text.pack(fill="x")
        self.message_text.bind("<FocusOut>", lambda _e: self._commit_text())

        actions = tk.Frame(self.props_body, bg=c["card"])
        actions.pack(fill="x", pady=(14, 0))

        def small(text, command, danger=False, side="left"):
            button = tk.Button(
                actions, text=text, font=("Segoe UI", 9), relief="flat", bd=0, padx=8, pady=4, cursor="hand2",
                bg=c["entry"], fg=("#d1242f" if danger else c["fg"]),
                activebackground=c["border"], activeforeground=("#d1242f" if danger else c["fg"]),
                command=command,
            )
            button.pack(side=side, padx=(0, 6) if side == "left" else 0)
            return button

        small("↑ Выше", lambda: self.move(-1))
        small("↓ Ниже", lambda: self.move(1))
        self.children_button = small("Вложенные кнопки ▸", self.enter_children)
        small("Удалить", self.delete, danger=True, side="right")

        tk.Label(
            self.props_body, text="Изменения сохраняются сразу — бот покажет их после следующего «Главное меню».",
            font=("Segoe UI", 8), fg=c["muted"], bg=c["card"], anchor="w", justify="left", wraplength=360,
        ).pack(fill="x", pady=(12, 0))

    def _action_options(self):
        options = [SUBMENU_LABEL, MESSAGE_ONLY_LABEL]
        options += [label for _code, label in self.source.actions()]
        options += [f"Действие: {label}" for _op_id, label in self.source.operations()]
        return options

    def _action_label_for(self, row):
        _id, _parent, _label, message_text, action_code, _section, _enabled, _layout, operation_id = row
        if operation_id is not None:
            for op_id, label in self.source.operations():
                if op_id == operation_id:
                    return f"Действие: {label}"
        if action_code:
            for code, label in self.source.actions():
                if code == action_code:
                    return label
        has_children = bool(self.source.rows(_id))
        if has_children or not (message_text or "").strip():
            return SUBMENU_LABEL
        return MESSAGE_ONLY_LABEL

    def _action_from_label(self, label):
        """-> (action_code, operation_id)."""
        if label.startswith("Действие: "):
            wanted = label[len("Действие: "):]
            for op_id, op_label in self.source.operations():
                if op_label == wanted:
                    return None, op_id
        for code, action_label in self.source.actions():
            if action_label == label:
                return code, None
        return None, None

    def _fill_props(self):
        row = self.source.get(self.selected_id) if self.selected_id is not None else None
        if not row:
            self.props_title.configure(text="Кнопка не выбрана")
            self.props_hint.pack(fill="x")
            self.props_body.pack_forget()
            return
        self._suspend_commits = True
        try:
            _id, _parent, label, message_text, _action, _section, enabled, layout, _operation = row
            self.props_title.configure(text=label)
            self.props_hint.pack_forget()
            self.props_body.pack(fill="both", expand=True)
            self.name_var.set(label)
            self.action_combo["values"] = self._action_options()
            self.action_var.set(self._action_label_for(row))
            self.layout_var.set(layout or "full")
            self._paint_size_buttons()
            self.enabled_var.set(bool(enabled))
            self.message_text.delete("1.0", "end")
            self.message_text.insert("1.0", message_text or "")
            children = len(self.source.rows(_id))
            self.children_button.configure(text=f"Вложенные кнопки ({children}) ▸")
        finally:
            self._suspend_commits = False

    def _paint_size_buttons(self):
        c = self.colors
        for value, button in self.size_buttons.items():
            active = value == self.layout_var.get()
            button.configure(
                bg=c["accent"] if active else c["entry"], fg="white" if active else c["fg"],
                activebackground=c["accent"] if active else c["border"],
            )

    def _set_layout(self, value):
        self.layout_var.set(value)
        self._paint_size_buttons()
        self._commit()

    # ---------- збереження ----------
    def _current_values(self):
        label = self.name_var.get().strip()
        action_code, operation_id = self._action_from_label(self.action_var.get())
        message = self.message_text.get("1.0", "end").strip()
        return label, message, action_code, self.layout_var.get(), operation_id

    def _commit_text(self):
        if self._suspend_commits or self.selected_id is None:
            return
        self._commit()

    def _flush_text(self):
        """Перед зміною вибору - зберегти те, що ще не збережено в полях."""
        if self.selected_id is None or self._suspend_commits:
            return
        row = self.source.get(self.selected_id)
        if not row:
            return
        label, message, action_code, layout, operation_id = self._current_values()
        if (label, message) != ((row[2] or "").strip(), (row[3] or "").strip()):
            self._commit(refresh=False)

    def _commit(self, refresh=True):
        if self._suspend_commits or self.selected_id is None:
            return
        row = self.source.get(self.selected_id)
        if not row:
            return
        label, message, action_code, layout, operation_id = self._current_values()
        if not label:
            messagebox.showerror("Редактор кнопок", "Название кнопки не может быть пустым.", parent=self.frame)
            self.name_var.set(row[2])
            return
        if label.lower() != (row[2] or "").lower() and self.source.label_collides(label, exclude_id=self.selected_id):
            messagebox.showerror(
                "Редактор кнопок",
                f'Название «{label}» совпадает с уже существующей командой бота. Выберите другое.',
                parent=self.frame,
            )
            self.name_var.set(row[2])
            return
        node_id = self.selected_id
        self.source.apply(
            lambda: self.source.update(node_id, label, message, action_code, layout, operation_id),
            self._after_change if refresh else (lambda: None),
        )

    def _commit_enabled(self):
        if self._suspend_commits or self.selected_id is None:
            return
        node_id, enabled = self.selected_id, bool(self.enabled_var.get())
        self.source.apply(lambda: self.source.set_enabled(node_id, enabled), self._after_change)

    def _after_change(self):
        self.refresh()
        if self.on_changed:
            self.on_changed()

    # ---------- дії ----------
    def add_button(self):
        self._flush_text()
        siblings = [row[1].lower() for row in self.source.rows(self.parent_id)]
        label = NEW_BUTTON_LABEL
        counter = 2
        while label.lower() in siblings or self.source.label_collides(label):
            label = f"{NEW_BUTTON_LABEL} {counter}"
            counter += 1
        parent_id = self.parent_id
        holder = {}

        def action():
            holder["id"] = self.source.add(parent_id, label, "full")

        def then():
            new_id = holder.get("id")
            if new_id is not None:
                self.selected_id = new_id
            self._after_change()
            self.name_entry.focus_set()
            self.name_entry.selection_range(0, "end")

        self.source.apply(action, then)

    def move(self, delta):
        if self.selected_id is None:
            return
        self._flush_text()
        siblings = [row[0] for row in self.source.rows(self.parent_id)]
        if self.selected_id not in siblings:
            return
        index = siblings.index(self.selected_id)
        new_index = max(0, min(index + delta, len(siblings) - 1))
        if new_index == index:
            return
        node_id = self.selected_id
        self.source.apply(lambda: self.source.move(node_id, new_index), self._after_change)

    def delete(self):
        if self.selected_id is None:
            return
        row = self.source.get(self.selected_id)
        if not row:
            return
        children = len(self.source.rows(self.selected_id))
        text = f"Удалить кнопку «{row[2]}»?"
        if children:
            text += f"\n\nВместе с ней исчезнут {children} вложенных."
        if not messagebox.askyesno("Редактор кнопок", text, parent=self.frame):
            return
        node_id = self.selected_id
        self.selected_id = None
        self.source.apply(lambda: self.source.delete(node_id), self._after_change)
