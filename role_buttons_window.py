"""Вікно «Кнопки ролей» (Задача користувача, 2026-09-06) - спільне для
client_app.py (локальне сховище) і gui.py (через тунель, remote_control_client).

Обраний макет (поправка 02 до двоспискової побудови): рядок вкладок-ролей
зверху («Склад», «Бухгалтерия», «Гость», свої ролі, «Добавить +»),
ліворуч «Доступные кнопки», праворуч «Кнопки роли», між ними стрілки;
внизу «Сохранить», «Редактировать вкладку» (назва, колір, «Удалить роль…»)
і рядок збереження. Адміністратора у вікні нема - у нього все. «Гость» -
лише набір кнопок: без перейменування, перефарбування й видалення.
Кнопка «‹ Назад» угорі, Esc - назад, вікно з ручками зміни розміру.

UI-тексти - російською (мова користувачів програми), як і решта вікон."""

import json
import threading
import tkinter as tk
import urllib.error
from datetime import datetime
from tkinter import colorchooser, messagebox, ttk

# Вісім готових кольорів бейджа; текст підбирається сам за яскравістю.
PALETTE = ["#185FA5", "#534AB7", "#854F0B", "#0F6E56", "#B42318", "#0E7490", "#6B7280", "#9D174D"]
SAVED_FLASH_MS = 2000

DEFAULT_COLORS = {
    "bg": "#EDEFF2", "fg": "#20242A", "muted": "#5B6470", "row": "#FFFFFF", "line": "#D5D9DF",
    "accent": "#3B6EA5", "accent_fg": "#FFFFFF", "ok": "#0F6E56", "ok_fg": "#FFFFFF", "danger": "#B42318",
}


def contrast_text_color(color_bg):
    value = str(color_bg or "").lstrip("#")
    try:
        r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return "#FFFFFF"
    return "#1A1D21" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#F4F6F8"


class RoleSource:
    """Що вікно хоче від програми. Кожна дія повертає повний стан
    ({"roles", "buttons", "allowed", "saved_at"}) або кидає ValueError з
    текстом для людини. run() виконує дію: локально - одразу, через тунель -
    у фоні, then(result) завжди на головному потоці."""

    def load(self):
        raise NotImplementedError

    def set_buttons(self, role_key, button_ids):
        raise NotImplementedError

    def add_role(self, label, color_bg):
        raise NotImplementedError

    def update_role(self, role_key, label, color_bg):
        raise NotImplementedError

    def delete_role(self, role_key, move_users_to):
        raise NotImplementedError

    def run(self, action, then, on_error):
        try:
            result = action()
        except ValueError as exc:
            on_error(str(exc))
            return
        then(result)


class LocalRoleSource(RoleSource):
    def __init__(self, store, on_changed=None):
        self.store = store
        self.on_changed = on_changed

    def _changed(self):
        if self.on_changed is not None:
            self.on_changed()
        return self.store.roles_payload()

    def load(self):
        return self.store.roles_payload()

    def set_buttons(self, role_key, button_ids):
        self.store.set_role_buttons(role_key, button_ids)
        return self._changed()

    def add_role(self, label, color_bg):
        key = self.store.add_role(label, color_bg)
        payload = self._changed()
        payload["key"] = key
        return payload

    def update_role(self, role_key, label, color_bg):
        self.store.update_role(role_key, label=label, color_bg=color_bg)
        return self._changed()

    def delete_role(self, role_key, move_users_to):
        self.store.delete_role(role_key, move_users_to)
        return self._changed()


class RemoteRoleSource(RoleSource):
    """gui.py: кожна дія - HTTP-запит до живого client_app.py через тунель
    (той самий принцип, що й редактор кнопок)."""

    def __init__(self, remote, run_on_main_thread):
        self.remote = remote
        self.run_on_main_thread = run_on_main_thread

    def load(self):
        payload = self.remote.fetch_remote_roles()
        if payload is None:
            raise ValueError("Не удалось получить роли с client_app.py. Проверьте соединение.")
        return payload

    def _post(self, payload):
        try:
            return self.remote.post_remote_roles_action(payload)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(detail).get("error") or detail
            except ValueError:
                pass
            raise ValueError(detail)
        except Exception as exc:
            raise ValueError(str(exc))

    def set_buttons(self, role_key, button_ids):
        return self._post({"op": "set_buttons", "role_key": role_key, "button_ids": list(button_ids)})

    def add_role(self, label, color_bg):
        return self._post({"op": "add", "label": label, "color_bg": color_bg})

    def update_role(self, role_key, label, color_bg):
        return self._post({"op": "update", "role_key": role_key, "label": label, "color_bg": color_bg})

    def delete_role(self, role_key, move_users_to):
        return self._post({"op": "delete", "role_key": role_key, "move_users_to": move_users_to})

    def run(self, action, then, on_error):
        def worker():
            try:
                result = action()
            except Exception as exc:  # ValueError з текстом для людини або будь-який збій тунелю
                text = str(exc)
                self.run_on_main_thread(lambda: on_error(text))
                return
            self.run_on_main_thread(lambda: then(result))

        threading.Thread(target=worker, daemon=True).start()


class RoleDialog:
    """«Новая роль» / «Роль «X»»: назва, палітра, «Другой…», приклад бейджа."""

    def __init__(self, parent, colors, title, label="", color_bg=None, primary="Создать",
                 on_ok=None, on_delete=None):
        self.colors = colors
        self.on_ok = on_ok
        self.color_bg = color_bg or PALETTE[0]
        window = tk.Toplevel(parent)
        self.window = window
        window.title(title)
        window.configure(bg=colors["bg"])
        window.transient(parent)
        window.resizable(False, False)
        top = tk.Frame(window, bg=colors["bg"])
        top.pack(fill="x", padx=12, pady=(10, 4))
        tk.Button(top, text="‹ Назад", command=self.close).pack(side="left")
        tk.Label(top, text=title, font=("Segoe UI", 11, "bold"), bg=colors["bg"], fg=colors["fg"]).pack(side="left", padx=(10, 0))
        body = tk.Frame(window, bg=colors["bg"])
        body.pack(fill="both", expand=True, padx=14, pady=(4, 12))
        tk.Label(body, text="Название:", bg=colors["bg"], fg=colors["fg"]).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.name_var = tk.StringVar(value=label)
        entry = tk.Entry(body, textvariable=self.name_var, width=30)
        entry.grid(row=0, column=1, sticky="w", pady=(0, 6))
        entry.focus_set()
        entry.select_range(0, "end")
        self.name_var.trace_add("write", lambda *_: self._refresh_example())
        tk.Label(body, text="Цвет:", bg=colors["bg"], fg=colors["fg"]).grid(row=1, column=0, sticky="w")
        palette = tk.Frame(body, bg=colors["bg"])
        palette.grid(row=1, column=1, sticky="w")
        self.swatches = []
        for color in PALETTE:
            swatch = tk.Label(palette, bg=color, width=3, height=1, cursor="hand2", bd=0, highlightthickness=2,
                              highlightbackground=colors["bg"])
            swatch.pack(side="left", padx=2)
            swatch.bind("<Button-1>", lambda event, c=color: self._pick(c))
            self.swatches.append((color, swatch))
        tk.Button(palette, text="Другой…", command=self._other).pack(side="left", padx=(8, 0))
        tk.Label(body, text="Пример:", bg=colors["bg"], fg=colors["muted"]).grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.example = tk.Label(body, text=label or "Роль", font=("Segoe UI", 9, "bold"), padx=8, pady=3)
        self.example.grid(row=2, column=1, sticky="w", pady=(10, 0))
        buttons = tk.Frame(body, bg=colors["bg"])
        buttons.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        tk.Button(buttons, text=primary, width=14, command=self._ok).pack(side="left")
        if on_delete is not None:
            tk.Button(buttons, text="Удалить роль…", fg=colors["danger"], command=lambda: on_delete(self)).pack(side="right")
        window.bind("<Escape>", lambda event: self.close())
        window.bind("<Return>", lambda event: self._ok())
        window.protocol("WM_DELETE_WINDOW", self.close)
        self._pick(self.color_bg)
        window.grab_set()

    def _pick(self, color):
        self.color_bg = color
        for swatch_color, swatch in self.swatches:
            swatch.configure(highlightbackground=self.colors["fg"] if swatch_color.lower() == color.lower() else self.colors["bg"])
        self._refresh_example()

    def _other(self):
        chosen = colorchooser.askcolor(color=self.color_bg, parent=self.window, title="Цвет роли")
        if chosen and chosen[1]:
            self._pick(chosen[1].upper())

    def _refresh_example(self):
        self.example.configure(text=self.name_var.get().strip() or "Роль", bg=self.color_bg, fg=contrast_text_color(self.color_bg))

    def _ok(self):
        name = " ".join(self.name_var.get().split())
        if not name:
            messagebox.showerror("Роль", "Введите название роли.", parent=self.window)
            return
        if self.on_ok is not None:
            self.on_ok(name, self.color_bg)

    def close(self):
        if self.window.winfo_exists():
            self.window.grab_release()
            self.window.destroy()


class RoleButtonsWindow:
    def __init__(self, parent, source, colors=None, on_change=None):
        self.source = source
        self.colors = dict(DEFAULT_COLORS, **(colors or {}))
        self.on_change = on_change
        self.payload = None
        self.current_key = None
        self.working = {}
        self.dirty = set()
        self._flash_job = None
        self.dialog = None
        colors = self.colors

        window = tk.Toplevel(parent)
        self.window = window
        window.title("Кнопки ролей")
        window.geometry("880x580")
        window.minsize(660, 440)
        window.configure(bg=colors["bg"])
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.bind("<Escape>", lambda event: self.close())

        top = tk.Frame(window, bg=colors["bg"])
        top.pack(fill="x", padx=14, pady=(12, 4))
        tk.Button(top, text="‹ Назад", command=self.close).pack(side="left")
        tk.Label(top, text="Кнопки ролей", font=("Segoe UI", 13, "bold"), bg=colors["bg"], fg=colors["fg"]).pack(side="left", padx=(10, 0))
        tk.Label(top, text="Esc — назад", bg=colors["bg"], fg=colors["muted"], font=("Segoe UI", 8)).pack(side="left", padx=(10, 0))

        self.tabs = tk.Frame(window, bg=colors["bg"])
        self.tabs.pack(fill="x", padx=14, pady=(6, 4))

        lists = tk.Frame(window, bg=colors["bg"])
        lists.pack(fill="both", expand=True, padx=14, pady=(2, 6))
        lists.grid_columnconfigure(0, weight=1)
        lists.grid_columnconfigure(2, weight=1)
        lists.grid_rowconfigure(1, weight=1)
        tk.Label(lists, text="Доступные кнопки", bg=colors["bg"], fg=colors["muted"], anchor="w").grid(row=0, column=0, sticky="w")
        self.right_title = tk.Label(lists, text="Кнопки роли", bg=colors["bg"], fg=colors["muted"], anchor="w")
        self.right_title.grid(row=0, column=2, sticky="w")
        self.left = self._listbox(lists)
        self.left.frame.grid(row=1, column=0, sticky="nsew")
        arrows = tk.Frame(lists, bg=colors["bg"])
        arrows.grid(row=1, column=1, padx=8)
        tk.Button(arrows, text="→", width=4, command=self.move_right).pack(pady=(0, 6))
        tk.Button(arrows, text="←", width=4, command=self.move_left).pack()
        self.right = self._listbox(lists)
        self.right.frame.grid(row=1, column=2, sticky="nsew")
        self.left.bind("<Double-Button-1>", lambda event: self.move_right())
        self.right.bind("<Double-Button-1>", lambda event: self.move_left())

        bottom = tk.Frame(window, bg=colors["bg"])
        bottom.pack(fill="x", padx=14, pady=(0, 12))
        self.save_button = tk.Button(bottom, text="Сохранить", width=14, command=self.save, state="disabled")
        self.save_button.pack(side="left")
        self.edit_button = tk.Button(bottom, text="Редактировать вкладку", command=self.edit_tab)
        self.status = tk.Label(bottom, text="", bg=colors["bg"], fg=colors["muted"], anchor="w")
        self.status.pack(side="left", padx=(12, 0), fill="x", expand=True)
        self._save_default = {"bg": self.save_button.cget("bg"), "fg": self.save_button.cget("fg"), "activebackground": self.save_button.cget("activebackground")}
        self.reload()

    # ---------------- дані ----------------
    def _listbox(self, parent):
        frame = tk.Frame(parent, bg=self.colors["bg"])
        box = tk.Listbox(frame, activestyle="none", selectmode="extended", bg=self.colors["row"], fg=self.colors["fg"],
                         highlightthickness=1, highlightbackground=self.colors["line"], bd=0, font=("Segoe UI", 10),
                         exportselection=False)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=box.yview)
        box.configure(yscrollcommand=scroll.set)
        box.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        box.frame = frame
        box.ids = []
        return box

    def reload(self, select_key=None):
        self.status.configure(text="Загрузка…")
        self.source.run(self.source.load, lambda payload: self.apply_payload(payload, select_key), self.show_error)

    def apply_payload(self, payload, select_key=None):
        if not self.window.winfo_exists():
            return
        self.payload = payload
        roles = payload.get("roles") or []
        keys = [role["key"] for role in roles]
        allowed = {key: set(ids) for key, ids in (payload.get("allowed") or {}).items()}
        fresh = {}
        for key in keys:
            if key in self.dirty and key in self.working:
                fresh[key] = self.working[key]
            else:
                fresh[key] = set(allowed.get(key, set()))
        self.working = fresh
        self.dirty = {key for key in self.dirty if key in keys}
        if select_key in keys:
            self.current_key = select_key
        elif self.current_key not in keys:
            self.current_key = keys[0] if keys else None
        self.status.configure(text="")
        self.render_tabs()
        self.fill_lists()

    def role(self, key=None):
        key = key or self.current_key
        for role in (self.payload or {}).get("roles") or []:
            if role["key"] == key:
                return role
        return None

    # ---------------- вкладки ----------------
    def render_tabs(self):
        for child in self.tabs.winfo_children():
            child.destroy()
        for role in (self.payload or {}).get("roles") or []:
            active = role["key"] == self.current_key
            text = role["label"] + (" •" if role["key"] in self.dirty else "")
            chip = tk.Label(
                self.tabs, text=text, font=("Segoe UI", 9, "bold"), bg=role["color_bg"], fg=role["color_fg"],
                padx=10, pady=4, cursor="hand2", highlightthickness=2,
                highlightbackground=self.colors["fg"] if active else self.colors["bg"],
            )
            chip.pack(side="left", padx=(0, 6))
            chip.bind("<Button-1>", lambda event, key=role["key"]: self.select(key))
        tk.Button(self.tabs, text="Добавить +", command=self.add_role).pack(side="left", padx=(4, 0))

    def select(self, key):
        self.current_key = key
        self.render_tabs()
        self.fill_lists()

    def fill_lists(self):
        buttons = (self.payload or {}).get("buttons") or []
        role = self.role()
        chosen = self.working.get(self.current_key, set()) if role else set()
        saved_at = (self.payload or {}).get("saved_at") or ""
        in_any_role = set()
        for ids in ((self.payload or {}).get("allowed") or {}).values():
            in_any_role.update(ids)
        for box in (self.left, self.right):
            box.delete(0, "end")
            box.ids = []
        for button in buttons:
            if button["id"] in chosen:
                self.right.insert("end", button["display"])
                self.right.ids.append(button["id"])
            else:
                text = button["display"]
                if saved_at and button["created_at"] > saved_at and button["id"] not in in_any_role:
                    text += " · новая"
                self.left.insert("end", text)
                self.left.ids.append(button["id"])
        if role:
            self.right_title.configure(text="Кнопки роли «%s»" % role["label"])
        self.save_button.configure(state="normal" if self.current_key in self.dirty else "disabled")
        if role and not role.get("builtin"):
            self.edit_button.pack(side="left", padx=(8, 0), after=self.save_button)
        else:
            self.edit_button.pack_forget()
        if role and role.get("builtin") == "guest":
            self.status.configure(text="Гость — роль новичков бота: имя и цвет не меняются, удалить нельзя")
        elif not self.current_key in self.dirty:
            self.status.configure(text="")

    # ---------------- перенесення ----------------
    def _selected_ids(self, box):
        return [box.ids[index] for index in box.curselection()]

    def move_right(self):
        ids = self._selected_ids(self.left)
        if not ids or self.current_key is None:
            return
        self.working[self.current_key].update(ids)
        self._mark_dirty()

    def move_left(self):
        ids = self._selected_ids(self.right)
        if not ids or self.current_key is None:
            return
        self.working[self.current_key].difference_update(ids)
        self._mark_dirty()

    def _mark_dirty(self):
        original = set(((self.payload or {}).get("allowed") or {}).get(self.current_key, []))
        if self.working[self.current_key] == original:
            self.dirty.discard(self.current_key)
        else:
            self.dirty.add(self.current_key)
        self.render_tabs()
        self.fill_lists()

    # ---------------- збереження ----------------
    def save(self):
        key = self.current_key
        if key is None or key not in self.dirty:
            return
        ids = sorted(self.working[key])
        self.save_button.configure(state="disabled")
        self.status.configure(text="Сохраняю…")

        def saved(payload):
            if not self.window.winfo_exists():
                return
            self.dirty.discard(key)
            self.apply_payload(payload, key)
            role = self.role(key)
            label = role["label"] if role else key
            self.status.configure(text="%s · %s: %d кнопок" % (datetime.now().strftime("%H:%M"), label, len(ids)))
            self._flash_saved()
            if self.on_change is not None:
                self.on_change()

        self.source.run(lambda: self.source.set_buttons(key, ids), saved, self.show_error)

    def _flash_saved(self):
        colors = self.colors
        self.save_button.configure(text="✓ Сохранено", bg=colors["ok"], fg=colors["ok_fg"], activebackground=colors["ok"], state="disabled")
        self.right.configure(highlightthickness=2, highlightbackground=colors["ok"])
        if self._flash_job is not None:
            self.window.after_cancel(self._flash_job)
        self._flash_job = self.window.after(SAVED_FLASH_MS, self._unflash)

    def _unflash(self):
        self._flash_job = None
        if not self.window.winfo_exists():
            return
        self.save_button.configure(text="Сохранить", **self._save_default)
        self.save_button.configure(state="normal" if self.current_key in self.dirty else "disabled")
        self.right.configure(highlightthickness=1, highlightbackground=self.colors["line"])

    def show_error(self, text):
        if not self.window.winfo_exists():
            return
        self.status.configure(text="")
        self.save_button.configure(state="normal" if self.current_key in self.dirty else "disabled")
        messagebox.showerror("Кнопки ролей", text, parent=self.window)

    # ---------------- ролі ----------------
    def add_role(self):
        dialog = RoleDialog(self.window, self.colors, "Новая роль", primary="Создать", on_ok=None)
        self.dialog = dialog

        def create(label, color_bg):
            def done(payload):
                dialog.close()
                self.apply_payload(payload, payload.get("key"))
                if self.on_change is not None:
                    self.on_change()
            self.source.run(lambda: self.source.add_role(label, color_bg), done,
                            lambda text: messagebox.showerror("Новая роль", text, parent=dialog.window))

        dialog.on_ok = create

    def edit_tab(self):
        role = self.role()
        if role is None or role.get("builtin"):
            return
        key = role["key"]
        dialog = RoleDialog(self.window, self.colors, "Роль «%s»" % role["label"], label=role["label"],
                            color_bg=role["color_bg"], primary="Сохранить", on_delete=lambda d: self.delete_role(key, d))
        self.dialog = dialog

        def update(label, color_bg):
            def done(payload):
                dialog.close()
                self.apply_payload(payload, key)
                if self.on_change is not None:
                    self.on_change()
            self.source.run(lambda: self.source.update_role(key, label, color_bg), done,
                            lambda text: messagebox.showerror("Роль", text, parent=dialog.window))

        dialog.on_ok = update

    def delete_role(self, key, parent_dialog=None):
        role = self.role(key)
        if role is None:
            return
        colors = self.colors
        others = [r for r in self.payload["roles"] if r["key"] != key]
        window = tk.Toplevel(parent_dialog.window if parent_dialog else self.window)
        window.title("Удалить роль «%s»?" % role["label"])
        window.configure(bg=colors["bg"])
        window.transient(parent_dialog.window if parent_dialog else self.window)
        window.resizable(False, False)
        body = tk.Frame(window, bg=colors["bg"])
        body.pack(padx=16, pady=14)
        count = int(role.get("users") or 0)
        if count:
            word = "сотрудник" if count % 10 == 1 and count % 100 != 11 else ("сотрудника" if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14 else "сотрудников")
            tk.Label(body, text="В этой роли %d %s." % (count, word), bg=colors["bg"], fg=colors["fg"]).pack(anchor="w")
            row = tk.Frame(body, bg=colors["bg"])
            row.pack(anchor="w", pady=(8, 0))
            tk.Label(row, text="Перевести в:", bg=colors["bg"], fg=colors["fg"]).pack(side="left")
            labels = [r["label"] for r in others]
            default = next((r["label"] for r in others if r.get("builtin") == "guest"), labels[0] if labels else "")
            target_var = tk.StringVar(value=default)
            ttk.Combobox(row, textvariable=target_var, values=labels, state="readonly", width=22).pack(side="left", padx=(8, 0))
        else:
            tk.Label(body, text="В этой роли никого нет. Удалить?", bg=colors["bg"], fg=colors["fg"]).pack(anchor="w")
            target_var = None
        buttons = tk.Frame(body, bg=colors["bg"])
        buttons.pack(anchor="w", pady=(14, 0))

        def confirm():
            move_to = None
            if target_var is not None:
                move_to = next((r["key"] for r in others if r["label"] == target_var.get()), None)
                if move_to is None:
                    return

            def done(payload):
                window.destroy()
                if parent_dialog is not None:
                    parent_dialog.close()
                self.dirty.discard(key)
                self.working.pop(key, None)
                self.apply_payload(payload)
                if self.on_change is not None:
                    self.on_change()

            self.source.run(lambda: self.source.delete_role(key, move_to), done,
                            lambda text: messagebox.showerror("Удалить роль", text, parent=window))

        tk.Button(buttons, text="Удалить", width=12, fg=colors["danger"], command=confirm).pack(side="left")
        tk.Button(buttons, text="Отмена", width=12, command=window.destroy).pack(side="left", padx=(8, 0))
        window.bind("<Escape>", lambda event: window.destroy())
        window.grab_set()

    # ---------------- закриття ----------------
    def close(self):
        if self.dirty and self.window.winfo_exists():
            labels = ", ".join((self.role(key) or {"label": key})["label"] for key in sorted(self.dirty))
            if not messagebox.askyesno("Кнопки ролей", "Есть несохранённые изменения (%s). Закрыть без сохранения?" % labels, parent=self.window):
                return
        if self._flash_job is not None:
            try:
                self.window.after_cancel(self._flash_job)
            except tk.TclError:
                pass
        if self.window.winfo_exists():
            self.window.destroy()


def open_role_buttons_window(owner, attr, parent, source, colors=None, on_change=None):
    """Одне вікно на програму: повторний виклик піднімає вже відкрите."""
    existing = getattr(owner, attr, None)
    if existing is not None and existing.window.winfo_exists():
        existing.window.deiconify()
        existing.window.lift()
        existing.window.focus_force()
        existing.reload()
        return existing
    window = RoleButtonsWindow(parent, source, colors=colors, on_change=on_change)
    setattr(owner, attr, window)
    return window
