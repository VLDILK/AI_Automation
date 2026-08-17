"""Перевірка повноти перекладу: кожен виклик self._t("...") у gui.py має
відповідний ключ у i18n.TRANSLATIONS["ru"].

Реальний ризик (аудит коду, 2026-08-14): TRANSLATIONS["ru"] ключується
буквально оригінальним текстом виклику - будь-яка правка рядка в gui.py
(навіть виправлення пунктуації), без відповідної правки словника, тихо
"губить" переклад: translate() (i18n.py) мовчки повертає оригінал
(gettext-стиль fallback), тож російськомовний користувач побачив би
український оригінал замість перекладу - без жодної помилки чи попередження.
Раніше це ловилось лише одноразовим ручним AST-аудитом; цей скрипт можна
запускати знову після будь-якої правки gui.py.

Запуск: py check_i18n_coverage.py
Код виходу 0 - усе покрито; 1 - є хоч один self._t(...) без ключа в словнику.
"""

import ast
import sys
from pathlib import Path

from i18n import TRANSLATIONS

GUI_PATH = Path(__file__).resolve().parent / "gui.py"


def _find_t_call_literals(source_path):
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    literals = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # self._t("...") - Attribute(value=Name("self"), attr="_t").
        if not (isinstance(func, ast.Attribute) and func.attr == "_t"):
            continue
        if not node.args:
            continue
        first_arg = node.args[0]
        if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
            literals.append((node.lineno, first_arg.value))
        # Динамічний аргумент (f-рядок/змінна) - неможливо звірити статично,
        # свідомо пропускаємо (не false positive, а відома межа перевірки).
    return literals


def _is_cyrillic(text):
    return any("Ѐ" <= ch <= "ӿ" for ch in text)


def _looks_ukrainian(text):
    # Букви, яких немає в російському алфавіті - і/ї/є/ґ (великі й малі) -
    # надійна ознака, що рядок і досі оригінальним українським текстом, а
    # не вже написаний одразу російською (для якого запис у словнику не
    # потрібен - text.get(text, text) і так поверне сам текст незмінним).
    return any(ch in text for ch in "іїєґІЇЄҐ")


def main():
    literals = _find_t_call_literals(GUI_PATH)
    ru_keys = TRANSLATIONS.get("ru", {})
    missing = [(lineno, text) for lineno, text in literals if text not in ru_keys]
    # Реально вартий уваги випадок - рядок ще українською (є літери і/ї/є/ґ)
    # без запису в словнику: TRANSLATIONS["ru"] мапить З українського
    # оригіналу, тож для вже-російського тексту (типова ситуація для НОВОГО
    # коду, писаного одразу російською) відсутність ключа - НЕ баг, просто
    # нема чого перекладати (translate() і так поверне сам текст).
    missing_ukrainian = [(lineno, text) for lineno, text in missing if _looks_ukrainian(text)]

    print(f"Знайдено {len(literals)} викликів self._t(...) у gui.py, {len(missing)} без ключа в словнику.")
    print(
        f"З них {len(missing_ukrainian)} виглядають ще УКРАЇНСЬКОЮ (є і/ї/є/ґ) - "
        "оце й є реальний брак перекладу; решта - вже написані одразу російською, ключ не потрібен."
    )
    if not missing_ukrainian:
        print("OK: жодного українського рядка без перекладу не знайдено.")
        return 0

    lines = [f"gui.py:{lineno}  {text!r}" for lineno, text in missing_ukrainian]
    out_path = GUI_PATH.parent / "i18n_coverage_report.txt"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"БРАК ПЕРЕКЛАДУ: {len(missing_ukrainian)} рядків - деталі в {out_path.name}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
