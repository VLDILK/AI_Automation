"""Автозапуск client_app.py разом із Windows (Задача користувача,
2026-08-17: "додай в налаштуваннях - що можна галочку зняти").

HKCU\\...\\Run - той самий підхід, яким користується більшість звичайних
Windows-програм для автозапуску одного користувача: не потребує прав
адміністратора (на відміну від HKLM), не залежить від зовнішніх бібліотек
(winreg - стандартна бібліотека Python на Windows, .lnk-ярлики в Startup
вимагали б pywin32/winshell, яких у requirements.txt немає), і легко
перевіряється/знімається - весь стан це один ключ реєстру.

Модуль навмисно "тупий" (лише читання/запис реєстру, без жодної бізнес-
логіки на кшталт "чи має сенс вмикати в dev-режимі") - рішення "коли це
взагалі доречно" (getattr(sys, "frozen", False)) лишається на боці
викликача (client_app.py), як і в усіх подібних frozen-guard'ах цього
проєкту (code_backup.py, reports.py).
"""

import winreg

_RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "AI_Automation_Client"


def is_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, _VALUE_NAME)
        return True
    except FileNotFoundError:
        return False


def enable(command):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, command)


def disable():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, _VALUE_NAME)
    except FileNotFoundError:
        pass
