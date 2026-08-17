"""Прихід/продаж: розбір вільного тексту в структуровані дані (NLU), об'єднання з уже введеним, чек-лист відсутніх полів, нормалізація до відомих значень складу, рахунковий рушій кількості/об'єму, валідація/типо-підказки/фаззі-пошук клієнта. Керування СТАНОМ операції живе окремо, у telegram_dialog_income_sale_flow.py. Частина розбиття telegram_dialog.py - див. telegram_dialog.py для повної карти."""

import difflib
import re
import sqlite3
from difflib import get_close_matches

from utils import (
    _display_bot_number,
    _normalize_keyboard_code,
    _normalize_phrase,
    _number_value,
    _priced_amount,
    is_area_based_product,
    is_linear_meter_size,
    is_quantity_only_product,
    piece_measure as _shared_piece_measure,
)
from warehouse_data import (
    BUILTIN_BOT_COMMANDS,
    INCOME_QUANTITY_TOLERANCE,
    INCOME_VOLUME_TOLERANCE,
    SALES_SHEET_NAME,
    display_product_name,
    income_item_known_size,
    income_item_size,
    product_requires_type,
    required_sale_warehouse_columns,
    required_warehouse_columns,
    resolve_operation_for_payload,
    sales_columns,
    warehouse_rows,
)

MAX_INCOME_TOTAL_QUANTITY = 3000

# Аудит коду: _parse_number_with_thousands_separator уже вміє розібрати
# роздільник тисяч (крапка/пробіл), АЛЕ лише якщо ЗОВНІШНІЙ regex спершу
# захопив увесь текст числа в capture-групу — а ці зовнішні регекси досі
# ловили лише голий "\d+(?:[.,]\d+)?", без пробілу. Тому "3 500 шт" читалось
# як "500" (перша цифра "3 " губилась незрозуміло куди) — той самий клас
# багу, що вже виправлено для ціни, просто не поширено на кількість/об'єм/
# площу/мп/розміри. Групи РІВНО по 3 цифри після пробілу/крапки — "25 50"
# (2+2 цифри, два окремих розміри через пробіл) під це НЕ підпадає.
# Обгорнуто в (?:...) - без цього top-level "|" "витікає" за межі константи
# при вставці через f-string у ширший патерн (напр. rf"{_THOUSANDS_AWARE_NUMBER}
# \s*шт\b"), і другий варіант ("\d+...") комбінується лише з "шт", а перший
# ("3 200") зіставляється сам по собі без вимоги "шт" після - реальний баг,
# знайдений при фіксі _income_line_without_amounts (лишав "шт" як окреме
# слово замість повного вирізання "3 200 шт"). Місця, де константу й так
# уже обгортали в (?P<value>...), цим не зачіпаються (той самий результат).
_THOUSANDS_AWARE_NUMBER = r"(?:\d{1,3}(?:[.\s]\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)"


class IncomeSaleParsingDialogMixin:

    # Схожа на клієнта назва ("Joseph" на введене "jospeh") — на відміну від
    # звичайного Да/Нет, тут ДВІ окремі дії на "так": прийняти лише цей раз,
    # чи прийняти й запам'ятати цей конкретний одрук назавжди (client_name_
    # aliases). Плюс видима Отмена/Редактировать — раніше цей крок мав лише
    # Да/Нет без жодної видимої кнопки виходу (реальний баг з аудиту).
    def _client_suggestion_keyboard(self, suggestion):
        return {
            "keyboard": [
                [{"text": f"Принять и запомнить: {suggestion}"}],
                [{"text": f"Просто принять: {suggestion}"}],
                [{"text": "Нет"}, {"text": "Отмена"}, {"text": "Редактировать"}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }

    # Клієнт продажу/антисептирования має бути або назва компанії, або
    # "Физ лицо" (реальні дані складу: колонка "Клиент" ніде не містить
    # нічого третього) — кнопка тут заощаджує продавцю набір тексту, коли
    # покупець не компанія, а не вимагає слово в слово вводити "Физ лицо".
    # Голий текст на цьому кроці й так трактується буквально як значення
    # клієнта (ask_sale_client/ask_antiseptic_client) — кнопка просто
    # надсилає "Физ лицо" тим самим шляхом.
    def _client_entry_keyboard(self):
        return {
            "keyboard": [
                [{"text": "Физ лицо"}],
                [{"text": "Отмена"}, {"text": "Редактировать"}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }

    # Розбирає рядок, який або вже має роздільник тисяч (крапка/пробіл між
    # групами по 3 цифри, напр. "6.200", "6 200", "6.200,50"), або звичайне
    # число з комою як десятковою (напр. "6200,50", "6200"). У першому
    # випадку прибираємо роздільники тисяч і лишаємо кому (якщо є) як
    # десяткову крапку; у другому — просто міняємо кому на крапку, як
    # раніше через голий _number_value.
    #
    # Аудит коду: цей самий розбір потрібен НЕ ЛИШЕ ціні (де вже був
    # захист від "6.200 мдл" → 6200, а не 6,2) — товщина/ширина/довжина/
    # кількість/об'єм/площа/мп мали ту саму діру: "9x1.250x2.500" (лист
    # ОСБ 1250×2500мм) читалось як 9/1.25/2.5. Тепер усі ці поля йдуть
    # через цю саму функцію — назва більше не прив'язана лише до ціни.
    def _parse_number_with_thousands_separator(self, value_text):
        text = str(value_text or "").strip()
        thousands_match = re.match(r"^(\d{1,3}(?:[.\s]\d{3})+)(,\d+)?$", text)
        if thousands_match:
            integer_part = re.sub(r"[.\s]", "", thousands_match.group(1))
            decimal_part = (thousands_match.group(2) or "").replace(",", ".")
            return _number_value(integer_part + decimal_part)
        return _number_value(text.replace(",", "."))

    # loose_size_pattern/loose_partial_size_pattern (на відміну від "x"- чи
    # кома-роздільних варіантів) не мають однозначного роздільника — це
    # просто 2-3 ГОЛІ числа підряд. Реальний баг з аудиту: regex сам собою
    # "жадібно" ловив БУДЬ-ЯКІ 3 (чи 2) підряд числа як розмір, навіть коли
    # одне з них насправді кількість чи ціна — "47 100 20 6200" читалось як
    # розмір 47x100x20 (кількість 20 ставала ДОВЖИНОЮ!), а "25 50 100 шт" —
    # як розмір 25x50x100 (кількість 100 ставала фантомною довжиною ПОРЯД зі
    # справжньою кількістю 100, знайденою окремо). Спроби виправити це прямо
    # в regex через negative lookahead провалились — re.search просто шукає
    # ІНШЕ (так само не те) вікно з потрібною кількістю чисел у тому ж рядку,
    # і навіть при точному влучанні в потрібну позицію backtracking міг би
    # "відкусити" частину одного числа замість повного (лишаючи рештку
    # зліпленою без роздільника з наступним числом), обходячи lookahead.
    # Єдиний надійний спосіб — перевірити на рівні Python ще ДО спроби
    # збігу: рахуємо лише "непояснені" числа — ті, що НЕ мають одразу за
    # собою відомої одиниці виміру (шт/м3/мп/...). "5 шт" тут не рахується
    # (вже пояснене число — кількість), а "20"/"6200" без жодної одиниці —
    # рахуються (можуть бути чим завгодно). Довіряємо "вільному" розміру
    # ЛИШЕ коли непоясненних чисел РІВНО стільки, скільки очікує сам патерн
    # (3 для повного розміру, 2 для часткового) — легітимний "розмір +
    # кількість шт" (напр. "35 100 5 шт") лишається робочим, а справді
    # неоднозначні рядки ("47 100 20 6200", де жодне число нічим не
    # пояснене) — ні.
    def _loose_size_relevant_count(self, text):
        total = len(re.findall(r"\d+(?:[.,]\d+)?", text))
        explained = len(re.findall(
            rf"\d+(?:[.,]\d+)?\s*(?:{self._MEASURE_UNIT_TOKEN_ALTERNATION})\b",
            text,
            re.IGNORECASE,
        ))
        return total - explained

    def _loose_size_count_matches(self, text, expected_count):
        return self._loose_size_relevant_count(text) == expected_count

    # Навіть коли числових токенів рівно стільки, скільки треба — якщо одразу
    # за знайденим розміром йде відома одиниця виміру (шт/м3/мп/...), останнє
    # "число" насправді кількість/об'єм, а не довжина/ширина.
    def _loose_size_match_is_reliable(self, text, match):
        remainder = text[match.end():].lstrip()
        return not re.match(rf"(?:{self._MEASURE_UNIT_TOKEN_ALTERNATION})\b", remainder, re.IGNORECASE)

    # --- Розбір вільного тексту приходу/продажу в структуровані дані ---
    def _parse_income_message(self, text):
        rows = []
        parsed_fields = {}
        free_values = []
        unknown_fields = []
        info_notes = []
        single_dimension_candidate = None
        comma_size_pattern = re.compile(
            r"(?<![\d.,])"
            r"(?P<thickness>\d+)\s*,\s*(?P<width>\d+)"
            r"(?:\s*,\s*(?P<length>\d+)(?:\s*(?P<length_unit>мм|mm|м|m|к|k)\b)?)?"
            r"(?![\d.,])"
            rf"(?!\s*(?:{self._MEASURE_UNIT_TOKEN_ALTERNATION})\b)",
            re.IGNORECASE,
        )
        size_pattern = re.compile(
            r"(?P<thickness>\d+(?:[.,]\d+)?)\s*[xххХX*]\s*"
            r"(?P<width>\d+(?:[.,]\d+)?)\s*[xххХX*]\s*"
            r"(?P<length>\d+(?:[.,]\d+)?)(?:\s*(?P<length_unit>мм|mm|м|m|к|k)\b)?",
            re.IGNORECASE,
        )
        loose_size_pattern = re.compile(
            r"(?P<thickness>\d+(?:[.,]\d+)?)\s*(?:[-—–]|\s+)\s*"
            r"(?P<width>\d+(?:[.,]\d+)?)\s*(?:[-—–]|\s+)\s*"
            r"(?P<length>\d+(?:[.,]\d+)?)(?:\s*(?P<length_unit>мм|mm|м|m|к|k)\b)?",
            re.IGNORECASE,
        )
        partial_size_pattern = re.compile(
            r"(?P<thickness>\d+(?:[.,]\d+)?)\s*[xххХX*]\s*"
            r"(?P<width>\d+(?:[.,]\d+)?)(?!\s*[xххХX*]\s*\d)",
            re.IGNORECASE,
        )
        # (?!\d) одразу після ширини — без цього regex-backtracking міг би
        # "відкусити" коротший префікс ширини (напр. "10" замість "100", коли
        # решта "0" зліплена без роздільника з наступним числом) і все одно
        # пройти повз перевірку кількості чисел, лишивши ширину НЕ цілим
        # числом (той самий клас багу, що й у loose_size_pattern вище —
        # знайдено при тому ж аудиті). Немає окремого "не йде ще одне число"
        # lookahead — цю неоднозначність тепер вирішує _loose_size_count_
        # matches (рахує НЕПОЯСНЕНІ числа в усьому рядку) ще ДО виклику
        # .search(), а не сам regex: "35 100 5 шт" (де "5 шт" — вже пояснена
        # кількість) має коректно матчитись як 35x100, а не відхилятись лише
        # тому, що після ширини йде ще одне число.
        loose_partial_size_pattern = re.compile(
            r"(?P<thickness>\d+(?:[.,]\d+)?)\s*(?:[-—–]|\s+)\s*"
            r"(?P<width>\d+(?:[.,]\d+)?)(?!\d)",
            re.IGNORECASE,
        )
        single_dimension_pattern = re.compile(
            r"(?<![\d.,])(?P<value>\d+(?:[.,]\d+)?)(?:\s*(?P<unit>мм|mm|м|m|к|k)\b(?!\s*[A-Za-zА-Яа-яІЇЄҐіїєґ]))?"
            rf"(?!\s*(?:{self._MEASURE_UNIT_TOKEN_ALTERNATION})\b)",
            re.IGNORECASE,
        )
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            # "ширина 15, длина 5000" — кілька підписаних розмірів через кому
            # НА ОДНОМУ рядку (реальний баг: раніше жодна з двох міток
            # взагалі не розпізнавалась, а спроба вгадати за позицією плутала
            # значення між рядками). Обробляємо ЛИШЕ якщо рядок складається
            # ЦІЛКОМ з таких міток (нічого іншого не лишається після їх
            # вилучення) — змішаний рядок (мітка розміру + щось ще) лишається
            # для звичайної логіки нижче без змін.
            dimension_updates, dimension_remainder = self._extract_dimension_labels(stripped)
            if dimension_updates and not dimension_remainder:
                if not rows:
                    rows.append(self._empty_income_row())
                rows[-1].update(dimension_updates)
                continue

            loose_match = None
            if self._loose_size_count_matches(stripped, 3):
                loose_match = loose_size_pattern.search(stripped)
                if loose_match and not self._loose_size_match_is_reliable(stripped, loose_match):
                    loose_match = None
            match = size_pattern.search(stripped) or loose_match or comma_size_pattern.search(stripped)
            partial_match = None
            if not match:
                loose_partial_match = None
                if self._loose_size_count_matches(stripped, 2):
                    loose_partial_match = loose_partial_size_pattern.search(stripped)
                    if loose_partial_match and not self._loose_size_match_is_reliable(stripped, loose_partial_match):
                        loose_partial_match = None
                partial_match = partial_size_pattern.search(stripped) or loose_partial_match
            if not match and not partial_match:
                quantity = self._parse_income_quantity(stripped)
                volume = self._parse_income_volume(stripped)
                area = self._parse_income_area(stripped)
                linear = self._parse_income_linear(stripped)
                amount_text = self._income_line_without_amounts(stripped)
                if quantity is not None or volume is not None or area is not None or linear is not None:
                    row = self._empty_income_row()
                    row.update(
                        {
                            "quantity": quantity,
                            "volume": volume,
                            "area": area,
                            "linear": linear,
                            "quantity_provided": quantity is not None,
                            "volume_provided": volume is not None,
                            "area_provided": area is not None,
                            "linear_provided": linear is not None,
                        }
                    )
                    single_match = single_dimension_pattern.search(amount_text)
                    free_text = amount_text
                    if single_match:
                        value_text = "".join(
                            part
                            for part in [
                                single_match.group("value"),
                                single_match.group("unit") or "",
                            ]
                            if part
                        )
                        row["thickness"] = self._parse_income_length_value(value_text)
                        free_text = self._income_line_free_text(amount_text, single_match)
                    rows.append(row)
                    parsed_line = self._parse_income_field_line(free_text) if free_text else None
                    if parsed_line:
                        if parsed_line.get("field"):
                            parsed_fields[parsed_line["field"]] = parsed_line["value"]
                        else:
                            unknown_fields.append(parsed_line)
                    else:
                        free_values.extend(self._split_income_free_text(free_text))
                    continue

                single_match = single_dimension_pattern.search(stripped)
                if single_match:
                    value_text = "".join(
                        part
                        for part in [
                            single_match.group("value"),
                            single_match.group("unit") or "",
                        ]
                        if part
                    )
                    single_dimension_candidate = {
                        "value": self._parse_income_length_value(value_text),
                        "source_text": single_match.group(0).strip(),
                    }
                    free_text = self._income_line_free_text(stripped, single_match)
                    parsed_line = self._parse_income_field_line(free_text) if free_text else None
                    if parsed_line:
                        if parsed_line.get("field"):
                            parsed_fields[parsed_line["field"]] = parsed_line["value"]
                        else:
                            unknown_fields.append(parsed_line)
                    else:
                        free_values.extend(self._split_income_free_text(free_text))
                    continue

                parsed_line = self._parse_income_field_line(stripped)
                if parsed_line:
                    if parsed_line.get("field"):
                        parsed_fields[parsed_line["field"]] = parsed_line["value"]
                    else:
                        unknown_fields.append(parsed_line)
                    continue
                if self._is_income_command_line(stripped):
                    continue
                free_values.extend(self._split_income_free_text(stripped))
                continue

            quantity = self._parse_income_quantity(stripped)
            volume = self._parse_income_volume(stripped)
            area = self._parse_income_area(stripped)
            linear = self._parse_income_linear(stripped)
            quantity_typo_candidate = None
            quantity_typo_raw_text = None
            if quantity is None and volume is None and area is None and linear is None:
                typo = self._parse_income_quantity_typo(stripped)
                if typo:
                    quantity_typo_raw_text = typo["raw_text"]
                    if typo.get("auto_fix"):
                        quantity = typo["guessed_quantity"]
                        info_notes.append(
                            f"«{typo['raw_text']}» распознано как {_display_bot_number(quantity)} шт."
                        )
                    else:
                        quantity_typo_candidate = typo
            active_match = match or partial_match
            length = None
            if match:
                length = self._parse_income_length_value(
                    "".join(
                        part
                        for part in [
                            match.group("length") or "",
                            match.group("length_unit") or "",
                        ]
                        if part
                    )
                )
            rows.append(
                {
                    "thickness": self._parse_number_with_thousands_separator(active_match.group("thickness")),
                    "width": self._parse_number_with_thousands_separator(active_match.group("width")),
                    "length": length,
                    "quantity": quantity,
                    "volume": volume,
                    "area": area,
                    "linear": linear,
                    "quantity_provided": quantity is not None,
                    "volume_provided": volume is not None,
                    "area_provided": area is not None,
                    "linear_provided": linear is not None,
                    "quantity_typo_candidate": quantity_typo_candidate,
                    "row_id": None,
                    "create_new": False,
                }
            )
            free_text = self._income_line_free_text(stripped, active_match)
            if quantity_typo_raw_text:
                # "500штукыв"/"100 ш" тощо НЕ збігаються з точним "шт|штук"
                # з _income_line_free_text (саме тому вони й опинились тут
                # взагалі) — без цього прибирання вони просочувались би далі
                # як "вільний текст" і мовчки ставали здогадкою породи
                # (реальний баг: порода ставала буквально "500штукыв").
                free_text = free_text.replace(quantity_typo_raw_text, " ")
            trailing_condition = self._leading_condition_token(free_text)
            if trailing_condition:
                free_values.append(trailing_condition)
                free_text = re.sub(r"^\s*\S+\s*", "", free_text, count=1).strip()
            parsed_line = self._parse_income_field_line(free_text) if free_text else None
            if parsed_line:
                if parsed_line.get("field"):
                    parsed_fields[parsed_line["field"]] = parsed_line["value"]
                else:
                    unknown_fields.append(parsed_line)
            else:
                free_values.extend(self._split_income_free_text(free_text))

        # Явно підписані розміри ("ширина 15", "длина: 5000") застосовуються
        # до ОСТАННЬОГО розпізнаного рядка цього ж повідомлення (створюємо
        # порожній, якщо рядків ще нема) — так само надійно, як порода/тип/
        # товар, а не через крихке вгадування за позицією серед голих чисел.
        dimension_updates = {
            field: parsed_fields[field] for field in self._DIMENSION_FIELD_LABELS if field in parsed_fields
        }
        if dimension_updates:
            if not rows:
                rows.append(self._empty_income_row())
            target_row = rows[-1]
            for field, value in dimension_updates.items():
                target_row[field] = self._parse_income_length_value(value)

        payload = {
            "breed": parsed_fields.get("breed"),
            "condition": parsed_fields.get("condition"),
            "product": parsed_fields.get("product"),
            "free_values": free_values,
            "unknown_fields": unknown_fields,
            "info_notes": info_notes,
            "rows": rows,
        }
        if single_dimension_candidate:
            payload["single_dimension_candidate"] = single_dimension_candidate
        return payload, None

    # Аудит коду: значення тут раніше йшло через голий _number_value —
    # "3.500 шт" (3500 штук) читалось як 3.5. Регулярні вирази нижче й
    # так ловлять лише ОДНУ необов'язкову десяткову групу (\d+(?:[.,]\d+)?),
    # тож "3.500" повністю потрапляє в захоплену групу як текст — саме її
    # й треба розбирати тим самим "розумним" парсером, що й ціну.
    def _parse_income_quantity(self, text):
        match = re.search(rf"(?P<value>{_THOUSANDS_AWARE_NUMBER})\s*(?:шт|штук)\b", text, flags=re.IGNORECASE)
        return self._parse_number_with_thousands_separator(match.group("value")) if match else None

    def _parse_income_volume(self, text):
        match = re.search(rf"(?P<value>{_THOUSANDS_AWARE_NUMBER})\s*(?:м3|м³|куб)\b", text, flags=re.IGNORECASE)
        return self._parse_number_with_thousands_separator(match.group("value")) if match else None

    def _parse_income_area(self, text):
        match = re.search(
            rf"(?P<value>{_THOUSANDS_AWARE_NUMBER})\s*(?:м2|м²|кв\.?\s*м|квадрат\w*)\b",
            text,
            flags=re.IGNORECASE,
        )
        return self._parse_number_with_thousands_separator(match.group("value")) if match else None

    def _parse_income_linear(self, text):
        match = re.search(
            rf"(?P<value>{_THOUSANDS_AWARE_NUMBER})\s*(?:мп|м\.?\s*п\.?|пог\.?\s*м\w*|погон\w*)\b",
            text,
            flags=re.IGNORECASE,
        )
        return self._parse_number_with_thousands_separator(match.group("value")) if match else None

    # Явний список одруків "шт" сусідніми клавішами (по проханню користувача:
    # "ш, шт, шщ, шг, шш, ши, шь, шр, шо, і навпаки") — це ОДНОЗНАЧНІ одруки
    # (одна невірна/переставлена літера поруч із "ш"/"т" на клавіатурі), тому
    # приймаються одразу як "шт" БЕЗ запитання (лише інформаційна нотатка).
    # Довші, менш очевидні розбіжності (напр. "500штукыв") і надалі йдуть
    # через підтвердження Да/Нет/Отмена — див. _income_quantity_typo_issue.
    _QUANTITY_UNIT_TYPOS = frozenset(
        {
            "ш", "шт", "тш", "шщ", "щш", "шг", "гш", "шш",
            "ши", "иш", "шь", "ьш", "шр", "рш", "шо", "ош",
        }
    )

    # Коли рядок має число + щось СХОЖЕ на "шт" (одрук: "штукыв", "ш", просто
    # зіпсована розкладка), але не збігається з точним "шт|штук" з _parse_-
    # income_quantity — раніше кількість просто мовчки губилась (рядок
    # лишався "без кількості" без жодного пояснення користувачу). Викликається
    # лише коли _parse_income_quantity/_parse_income_volume/_parse_income_area
    # вже не спрацювали для цього рядка. "auto_fix": True — приймати одразу,
    # без питання (див. _QUANTITY_UNIT_TYPOS); False — усе ще неоднозначно,
    # питати Да/Нет/Отмена.
    def _parse_income_quantity_typo(self, text):
        for match in re.finditer(r"(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>[a-zа-яіїєґ]+)\b", text, flags=re.IGNORECASE):
            unit = _normalize_phrase(match.group("unit"))
            if unit in {"шт", "штук"}:
                continue
            if unit in self._QUANTITY_UNIT_TYPOS:
                return {
                    "guessed_quantity": self._parse_number_with_thousands_separator(match.group("value")),
                    "raw_text": match.group(0).strip(),
                    "auto_fix": True,
                }
            if unit.startswith("шт"):
                return {
                    "guessed_quantity": self._parse_number_with_thousands_separator(match.group("value")),
                    "raw_text": match.group(0).strip(),
                    "auto_fix": False,
                }
        return None

    def _parse_income_length_value(self, text):
        match = re.search(
            rf"^\s*(?P<value>{_THOUSANDS_AWARE_NUMBER})\s*(?P<unit>мм|mm|м|m|к|k)?\b\s*$",
            str(text or "").strip(),
            flags=re.IGNORECASE,
        )
        if not match:
            return self._parse_number_with_thousands_separator(text)
        value = self._parse_number_with_thousands_separator(match.group("value"))
        unit = (match.group("unit") or "").casefold()
        if unit in {"м", "m", "к", "k"}:
            return value * 1000
        return value

    def _parse_income_dimension_answer(self, answer, validation):
        if validation.get("field") == "length":
            return self._parse_income_length_value(answer)
        return self._parse_number_with_thousands_separator(answer)

    def _income_line_free_text(self, line, size_match):
        text = line[:size_match.start()] + " " + line[size_match.end():]
        # Той самий _THOUSANDS_AWARE_NUMBER, що й у _parse_income_quantity/
        # _volume/_area/_linear — інакше вирізаний тут фрагмент НЕ збігається
        # з тим, що реально розпізналось, і "хвіст" цифр лишається сміттям
        # у вільному тексті (напр. "500" від "3 500 шт", коли "3 " вже пішло
        # окремо в число).
        text = re.sub(rf"{_THOUSANDS_AWARE_NUMBER}\s*(?:шт|штук)\b", " ", text, flags=re.IGNORECASE)
        text = re.sub(rf"{_THOUSANDS_AWARE_NUMBER}\s*(?:м3|м³|куб)\b", " ", text, flags=re.IGNORECASE)
        text = re.sub(rf"{_THOUSANDS_AWARE_NUMBER}\s*(?:м2|м²|кв\.?\s*м|квадрат\w*)\b", " ", text, flags=re.IGNORECASE)
        text = re.sub(rf"{_THOUSANDS_AWARE_NUMBER}\s*(?:мп|м\.?\s*п\.?|пог\.?\s*м\w*|погон\w*)\b", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"[-—–,:;|()]+", " ", text)
        return " ".join(text.split())

    def _income_line_without_amounts(self, line):
        # Той самий _THOUSANDS_AWARE_NUMBER, що й у _income_line_free_text
        # (див. коментар там) - без нього тисячний роздільник ("3 200 шт")
        # лишає хвіст цифр ("3"), який single_dimension_pattern далі мовчки
        # трактує як товщину.
        text = re.sub(rf"{_THOUSANDS_AWARE_NUMBER}\s*(?:шт|штук)\b", " ", line, flags=re.IGNORECASE)
        text = re.sub(rf"{_THOUSANDS_AWARE_NUMBER}\s*(?:м3|м³|куб)\b", " ", text, flags=re.IGNORECASE)
        text = re.sub(rf"{_THOUSANDS_AWARE_NUMBER}\s*(?:м2|м²|кв\.?\s*м|квадрат\w*)\b", " ", text, flags=re.IGNORECASE)
        text = re.sub(rf"{_THOUSANDS_AWARE_NUMBER}\s*(?:мп|м\.?\s*п\.?|пог\.?\s*м\w*|погон\w*)\b", " ", text, flags=re.IGNORECASE)
        return " ".join(text.split())

    def _split_income_free_text(self, text):
        values = []
        for token in re.split(r"\s+", text or ""):
            token = token.strip(" -—–,:;|()")
            if not token:
                continue
            normalized = _normalize_phrase(token)
            if not normalized or self._is_income_command_line(token):
                continue
            if normalized in {
                "шт", "штук", "м3", "м³", "куб", "м2", "м²", "квм", "кв.м", "квадрат",
                # _normalize_phrase прибирає пунктуацію, тож "м.п."/"пог.м"
                # (одним словом, разом з крапками) нормалізуються у "м п"/
                # "пог м" (з пробілом) — а не "мп"/"погм".
                "мп", "м п", "погм", "пог м", "погонных", "погонный", "погонные",
            }:
                continue
            values.append(token)
        return values

    def _leading_condition_token(self, text):
        parts = str(text or "").strip().split()
        if not parts:
            return None
        first = parts[0].strip(" -—–,:;|()")
        if self._looks_like_condition_code(first):
            return first
        return None

    # Реальний баг: "ширина 15, длинна 5000" не розпізнавалось ЗОВСІМ (ці
    # мітки тут не були перелічені), тож "15"/"5000" вгадувались по позиції
    # (крихко — легко плутались з іншими рядками) або взагалі губились чи
    # потрапляли не в те поле. "длинна" (подвійне н) — поширений варіант
    # написання, який користувачі реально вживають.
    _DIMENSION_FIELD_LABELS = {
        "thickness": ["толщина", "толщиной", "толщину"],
        "width": ["ширина", "шириной", "ширину"],
        "length": ["длина", "длинна", "длиной", "длинной", "длину", "длинну"],
    }

    _DIMENSION_LABEL_TO_FIELD = {
        label: field for field, labels in _DIMENSION_FIELD_LABELS.items() for label in labels
    }

    _DIMENSION_LABEL_PATTERN = re.compile(
        r"\b(?P<label>" + "|".join(_DIMENSION_LABEL_TO_FIELD) + r")"
        rf"(?:\s*[:=\-—]\s*|\s+)(?P<value>{_THOUSANDS_AWARE_NUMBER})\s*(?P<unit>мм|mm|м|m|к|k)?\b",
        re.IGNORECASE,
    )

    # "5500 длинна" (значення ПЕРЕД міткою) — реальний баг зі скріна:
    # користувач природно пише число, а тоді уточнює, що це за розмір.
    # Мітка-ПІСЛЯ-значення так само однозначна, як і мітка-перед — просто
    # інший порядок слів у реченні.
    _DIMENSION_VALUE_LABEL_PATTERN = re.compile(
        rf"\b(?P<value>{_THOUSANDS_AWARE_NUMBER})\s*(?P<unit>мм|mm|м|m|к|k)?"
        r"(?:\s*[:=\-—]\s*|\s+)(?P<label>" + "|".join(_DIMENSION_LABEL_TO_FIELD) + r")\b",
        re.IGNORECASE,
    )

    # Знаходить УСІ підписані розміри ("ширина 15", "длина: 5000", а тепер і
    # "5500 длинна" — значення перед міткою) БУДЬ-ДЕ в тексті (не лише на
    # початку рядка, як _parse_income_field_line) і вирізає їх — для
    # "ширина 15, длина 5000" (кілька міток на одному рядку через кому),
    # яких звичайний однопрохідний парсер не бачив.
    # Повертає (dict знайдених полів, залишок тексту після вирізання).
    def _extract_dimension_labels(self, text):
        dimension_values = {}

        def _replace(match):
            field = self._DIMENSION_LABEL_TO_FIELD.get(match.group("label").lower())
            if field:
                value_text = match.group("value") + (match.group("unit") or "")
                dimension_values[field] = self._parse_income_length_value(value_text)
            return " "

        remaining = self._DIMENSION_LABEL_PATTERN.sub(_replace, text)
        remaining = self._DIMENSION_VALUE_LABEL_PATTERN.sub(_replace, remaining)
        remaining = remaining.strip(" ,;")
        return dimension_values, remaining

    def _parse_income_field_line(self, line):
        aliases = {
            "breed": ["порода"],
            "condition": ["состояние", "стан", "сорт"],
            "product": ["продукт", "товар"],
            **self._DIMENSION_FIELD_LABELS,
        }
        for field, labels in aliases.items():
            for label in sorted(labels, key=len, reverse=True):
                match = re.match(
                    rf"^\s*{re.escape(label)}(?:\s*[:=\-—]\s*|\s+)(?P<value>.+?)\s*$",
                    line,
                    flags=re.IGNORECASE,
                )
                if match:
                    value = match.group("value").strip()
                    return {"field": field, "label": label, "value": value} if value else None

        match = re.match(r"^\s*(?P<label>[A-Za-zА-Яа-яІЇЄҐієїґ]{2,30})\s*[:=]\s*(?P<value>.+?)\s*$", line)
        if match:
            return {
                "label": match.group("label").strip(),
                "value": match.group("value").strip(),
            }
        return None

    def _is_income_command_line(self, line):
        normalized = _normalize_phrase(line)
        for command in BUILTIN_BOT_COMMANDS:
            if command["code"] != "add_income":
                continue
            command_aliases = command["aliases"] + [command["title"], command["code"]]
            return normalized in {_normalize_phrase(alias) for alias in command_aliases}
        return False

    # Прикріплює накопичені одрук-нотатки ("«шь» распознано как 500 шт.") до
    # відповіді і одразу очищує їх з payload — показуються рівно один раз,
    # разом із тим, що бот усе одно вже мав сказати (наступне запитання,
    # "не хватает данных" чи підтвердження), а не окремим повідомленням.
    # ВАЖЛИВО: notes виймаються з payload ДО виклику _impl, а не після. Impl
    # сам зберігає payload в pending-операцію (через _save_income_question) в
    # багатьох проміжних точках — якщо забрати notes лише з результату (вже
    # ПІСЛЯ того, як impl відпрацював і встиг зберегти payload з notes ще
    # всередині), у базі лишається стара версія з notes, і при НАСТУПНІЙ
    # відповіді користувача (напр. "Да" на непов'язане запитання) вона
    # підвантажується знову і нотатка показується вдруге. Тому notes мають
    # бути прибрані з payload ще ДО першого можливого save.
    def _apply_info_notes(self, payload, reply):
        notes = payload.pop("info_notes", None)
        if not notes:
            return reply
        prefix = "\n".join(notes) + "\n\n"
        if isinstance(reply, dict) and isinstance(reply.get("text"), str):
            reply = dict(reply)
            reply["text"] = prefix + reply["text"]
            return reply
        if isinstance(reply, str):
            return prefix + reply
        return reply

    def _parse_plain_positive_number(self, text):
        value = str(text or "").strip()
        # Важлива знахідка нового аудиту (28.07.2026, #2): вужчий регекс тут
        # відхиляв "3 500" (пробіл-роздільник тисяч) ще ДО виклику
        # _parse_number_with_thousands_separator нижче — той сам вміє його
        # розібрати, просто fullmatch-гейт не пускав такий текст далі.
        if not re.fullmatch(_THOUSANDS_AWARE_NUMBER, value):
            return None
        number = self._parse_number_with_thousands_separator(value)
        return number if number > 0 else None

    # Наскільки схоже "candidate" на "target" (толерантність до одруків
    # "цену"/"цене" замість "цена") — ratio() з difflib, поріг 0.7
    # підібраний емпірично: реальні одруки з прикладів користувача
    # проходять (цену/цене=0.75), випадкові слова ("наличка","клиент",
    # "привет") — ні (<=0.40).
    def _looks_like_word(self, candidate, target, threshold=0.7):
        candidate = _normalize_phrase(candidate)
        if not candidate:
            return False
        if candidate == target:
            return True
        return difflib.SequenceMatcher(None, candidate, target).ratio() >= threshold

    # Толерантний до одруків/вільного формулювання парсер відповіді на
    # "Укажите цену за м3" — реальна скарга користувача: бот не розумів
    # "4000 за м3", "4000 за 1м3" (голе число з поясненням одиниці, не
    # просто число) чи одруки на кшталт "цену"/"цене" замість "цена".
    # Сума більше НЕ приймається як вхід (нове правило користувача) — бот
    # завжди рахує total_amount сам як price_per_unit * обсяг, тож ця
    # функція повертає лише {"price_per_unit": ...} або None, якщо
    # взагалі нічого розпізнати не вдалось.
    def _parse_sale_price_answer(self, text):
        stripped = str(text or "").strip()
        if not stripped:
            return None

        # Мітка (можливо з одруком) + число, роздільник необов'язковий —
        # напр. "цена 6000", "цена: 6000". Число тут теж може мати
        # роздільник тисяч ("цена 6.000") — той самий парсер значення,
        # що й у _extract_unlabeled_sale_markers.
        match = re.match(r"^([a-zа-яіїєґ]+)\b\s*[:=\-—]?\s*(\d[\d.,\s]*\d|\d)", stripped, flags=re.IGNORECASE)
        if match:
            label_word, number_text = match.group(1), match.group(2)
            value = self._parse_number_with_thousands_separator(number_text)
            if value > 0 and self._looks_like_word(label_word, "цена"):
                return {"price_per_unit": value}

        # Голе число, можливо з поясненням одиниці ("4000 за м3", "4000 за
        # 1м3", "4000 за 1") — прибираємо цю "одиницю" (вона й так відома з
        # контексту запитання), а не вимагаємо, щоб УВЕСЬ рядок був самим
        # числом.
        # Аудит коду: цей список одиниць був окремим, неповним дублікатом —
        # не мав "мп"/варіантів, тож "4000 за 1мп" не розпізнавалось, хоча
        # ідентичне "4000 за 1м3" працювало. Той самий спільний
        # _MEASURE_UNIT_TOKEN_ALTERNATION, що вже покриває мп в усіх інших
        # місцях — той самий клас багу, інший список, вже траплялось раніше.
        cleaned = re.sub(rf"\bза\s*\d*\s*(?:{self._MEASURE_UNIT_TOKEN_ALTERNATION})\.?", " ", stripped, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bза\s+\d+\b", " ", cleaned, flags=re.IGNORECASE)
        numbers = re.findall(r"\d{1,3}(?:[.\s]\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?", cleaned)
        if len(numbers) == 1:
            value = self._parse_number_with_thousands_separator(numbers[0])
            if value > 0:
                return {"price_per_unit": value}
        return None

    def _is_whole_number(self, value):
        number = _number_value(value)
        return abs(number - round(number)) <= 0.0000001

    # --- Об'єднання введених даних приходу, пошук незаповнених полів ---
    def _merge_income_payload(self, payload, incoming):
        incoming = incoming or {}
        for field in ("product", "breed", "condition"):
            if incoming.get(field) and not payload.get(field):
                payload[field] = incoming[field]

        payload.setdefault("free_values", [])
        payload.setdefault("unknown_fields", [])
        payload.setdefault("info_notes", [])
        payload["free_values"].extend(incoming.get("free_values") or [])
        payload["unknown_fields"].extend(incoming.get("unknown_fields") or [])
        payload["info_notes"].extend(incoming.get("info_notes") or [])

        single_candidate = incoming.get("single_dimension_candidate")
        if single_candidate:
            if not self._apply_single_dimension_to_missing(payload, single_candidate.get("value")):
                payload["single_dimension_candidate"] = single_candidate

        incoming_rows = list(incoming.get("rows") or [])

        # Позицію щойно відхилив склад ("не знайдена") і користувач після
        # "Похожие позиции"/"Нет" вводить виправлений розмір — цей розмір
        # має ЗАМІНИТИ саме відхилений рядок (correcting_row_index),
        # а не додатись новим: інакше _resolve_sale_rows і надалі перевіряє
        # рядки по черзі й застрягає на старому невірному, до якого йому
        # ніколи не дійти. Кількість зі старого рядка НЕ переноситься (вона
        # належала невірному розміру) — якщо в новому тексті кількості
        # немає, її знову спитають, так само як і раніше.
        correcting_index = payload.pop("correcting_row_index", None)
        if correcting_index is not None and incoming_rows:
            rows = payload.setdefault("rows", [])
            if 0 <= correcting_index < len(rows):
                self._overwrite_income_row(rows[correcting_index], incoming_rows.pop(0))
            payload.pop("sale_not_found_item", None)

        for incoming_row in incoming_rows:
            target = self._income_row_for_merge(payload, incoming_row)
            if target is None:
                # Голе "число одиниця" ("6200 м3") без жодного виміру, коли
                # нема куди його змерджити і продажу ще бракує ціни — реальний
                # баг зі скріна: замість того щоб стати ціною за м3, воно
                # мовчки ставало ОКРЕМИМ порожнім рядком-фантомом, через що
                # чекліст "не вистачає" помилково знову показував уже введені
                # товщину/ширину/довжину (бо агрегує по ВСІХ рядках). Замість
                # вгадування — питаємо (той самий принцип, що й
                # client_candidate): confirm_price_candidate.
                if (
                    payload.get("operation_kind") == "sale"
                    and payload.get("price_candidate") is None
                    and not (
                        _number_value(payload.get("price_per_unit")) > 0
                        or _number_value(payload.get("total_amount")) > 0
                    )
                    and self._income_row_is_amount_only(incoming_row)
                ):
                    payload["price_candidate"] = dict(incoming_row)
                    continue
                # Немає куди змерджити — або дійсно нова позиція, або (реальний
                # баг, який заплутував користувача) голі числа ("150 5000")
                # конфліктують із розміром, що вже є в НЕЗАВЕРШЕНОМУ рядку
                # (_income_row_dimensions_conflict), тому мовчки додались
                # окремим рядком-фантомом замість допов­нення поточного. Повну
                # поведінкову дизамбігуацію визнано занадто ризикованою —
                # натомість лише попереджаємо і підказуємо надійний спосіб
                # (мітки "ширина"/"длина") замість голих чисел.
                if self._income_row_conflicts_with_existing(payload, incoming_row):
                    note = (
                        "Заметил незавершённую позицию с другим размером — добавил "
                        "новую строку, а не дополнил старую. Если хотели уточнить "
                        "размер той же позиции, укажите его с подписями, например: "
                        "ширина 150, длина 5000."
                    )
                    info_notes = payload.setdefault("info_notes", [])
                    if note not in info_notes:
                        info_notes.append(note)
                payload.setdefault("rows", []).append(dict(incoming_row))
                continue
            self._merge_income_row(target, incoming_row)

    def _overwrite_income_row(self, target, incoming):
        for field in ("thickness", "width", "length"):
            if _number_value(incoming.get(field)) > 0:
                target[field] = _number_value(incoming.get(field))
        if self._income_row_has_amount(incoming):
            target["quantity"] = incoming.get("quantity")
            target["quantity_provided"] = incoming.get("quantity_provided", False)
            target["volume"] = incoming.get("volume")
            target["volume_provided"] = incoming.get("volume_provided", False)
            target["area"] = incoming.get("area")
            target["area_provided"] = incoming.get("area_provided", False)
            target["linear"] = incoming.get("linear")
            target["linear_provided"] = incoming.get("linear_provided", False)
        else:
            target["quantity"] = None
            target["quantity_provided"] = False
            target["volume"] = None
            target["volume_provided"] = False
            target["area"] = None
            target["area_provided"] = False
            target["linear"] = None
            target["linear_provided"] = False
        target["row_id"] = None
        target["create_new"] = False

    # incoming_row — щоб не змерджити в "неповний" рядок, який насправді
    # належить ІНШІЙ позиції (реальний баг: у пачці з кількох рядків, де
    # два різних рядки мають різні розміри й обидва без кількості, другий
    # рядок мовчки поглинав дані третього, бо в обох були "відсутні" поля).
    # Мерджити можна лише туди, де розміри або справді відсутні, або
    # збігаються з тим, що прийшло — інакше це нова окрема позиція.
    def _income_row_for_merge(self, payload, incoming_row=None):
        rows = payload.setdefault("rows", [])
        for row in rows:
            if not self._income_row_has_missing(row):
                continue
            if incoming_row is not None and self._income_row_dimensions_conflict(row, incoming_row):
                continue
            return row
        return None

    # Той самий обхід рядків, що й _income_row_for_merge, але щоб ПОЯСНИТИ
    # користувачу ЧОМУ злиття не відбулось (див. коментар біля виклику) —
    # відрізняє "справді нова позиція" (рядків взагалі нема) від "є
    # незавершений рядок, але з конфліктним розміром". Навмисно перевіряє
    # саме НЕЗАВЕРШЕНІСТЬ РОЗМІРУ (_income_row_missing_dimensions), а не
    # загальне "є що донести" (_income_row_has_missing, яке рахує й
    # кількість) — рядок із ПОВНІСТЮ заданим розміром, якому просто ще не
    # вказали кількість, і новий рядок з іншим розміром — це НЕ плутанина,
    # а звичайна друга позиція в тій самій продажі; нотатка тут була б
    # зайвою.
    def _income_row_conflicts_with_existing(self, payload, incoming_row):
        rows = payload.get("rows") or []
        return any(
            self._income_row_missing_dimensions(row) and self._income_row_dimensions_conflict(row, incoming_row)
            for row in rows
        )

    def _income_row_missing_dimensions(self, row):
        return any(_number_value(row.get(field)) <= 0 for field in ("thickness", "width", "length"))

    def _income_row_dimensions_conflict(self, target, incoming):
        target_missing = [
            field for field in ("thickness", "width", "length") if _number_value(target.get(field)) <= 0
        ]
        incoming_populated = [
            field for field in ("thickness", "width", "length") if _number_value(incoming.get(field)) > 0
        ]
        # Голі числа без міток ("50 5000") завжди підписуються в incoming як
        # thickness/width за ПОЗИЦІЄЮ в тексті, а не за реальним значенням
        # поля. Коли кількість заповнених в incoming ЗБІГАЄТЬСЯ з кількістю
        # відсутніх у target — це заповнення прогалин (той самий порядок,
        # яким скористається _merge_income_row нижче), а не другий,
        # суперечливий розмір: пряме порівняння за назвою поля тут дало б
        # хибний "конфлікт" (26 проти "50", хоча 50 насправді призначалось
        # ширині) і мовчки плодило рядки-фантоми замість того, щоб
        # доповнити той самий рядок (реальний баг зі скріна користувача).
        if target_missing and len(incoming_populated) == len(target_missing):
            return False
        for field in ("thickness", "width", "length"):
            target_value = _number_value(target.get(field))
            incoming_value = _number_value(incoming.get(field))
            if target_value > 0 and incoming_value > 0 and abs(target_value - incoming_value) > 0.001:
                return True
        return False

    def _income_row_has_missing(self, row):
        if any(_number_value(row.get(field)) <= 0 for field in ("thickness", "width", "length")):
            return True
        return not self._income_row_has_amount(row)

    def _income_row_has_amount(self, row):
        return (
            _number_value(row.get("quantity")) > 0
            or _number_value(row.get("volume")) > 0
            or _number_value(row.get("area")) > 0
            or _number_value(row.get("linear")) > 0
        )

    # Рядок ЛИШЕ з кількістю/об'ємом/площею, без жодного виміру — типовий
    # наслідок голого "число одиниця" ("6200 м3"), яке в контексті продажі
    # часто насправді означає ЦІНУ за м3 (кількість/розмір вже задані
    # окремим рядком раніше), а не окрему нову позицію.
    def _income_row_is_amount_only(self, row):
        has_dimension = any(_number_value(row.get(field)) > 0 for field in ("thickness", "width", "length"))
        return not has_dimension and self._income_row_has_amount(row)

    def _income_row_amount_value(self, row):
        for field in ("quantity", "volume", "area", "linear"):
            value = _number_value(row.get(field))
            if value > 0:
                return value
        return None

    def _merge_income_row(self, target, incoming):
        target_missing_dimensions = [
            field
            for field in ("thickness", "width", "length")
            if _number_value(target.get(field)) <= 0
        ]
        incoming_dimensions = [
            _number_value(incoming.get(field))
            for field in ("thickness", "width", "length")
            if _number_value(incoming.get(field)) > 0
        ]
        if target_missing_dimensions and any(_number_value(target.get(field)) > 0 for field in ("thickness", "width", "length")):
            for field, value in zip(target_missing_dimensions, incoming_dimensions):
                target[field] = value
        else:
            for field in ("thickness", "width", "length"):
                if _number_value(target.get(field)) <= 0 and _number_value(incoming.get(field)) > 0:
                    target[field] = _number_value(incoming.get(field))

        if not self._income_row_has_amount(target):
            if _number_value(incoming.get("quantity")) > 0:
                target["quantity"] = incoming.get("quantity")
                target["quantity_provided"] = incoming.get("quantity_provided", True)
                target["volume"] = incoming.get("volume")
                target["volume_provided"] = incoming.get("volume_provided", False)
                target["area"] = incoming.get("area")
                target["area_provided"] = incoming.get("area_provided", False)
            elif _number_value(incoming.get("volume")) > 0:
                target["volume"] = incoming.get("volume")
                target["volume_provided"] = incoming.get("volume_provided", True)
                target["quantity"] = incoming.get("quantity")
                target["quantity_provided"] = incoming.get("quantity_provided", False)
            # Реальний баг з аудиту: гілка для "area" тут була відсутня
            # взагалі — вхідний рядок ЛИШЕ з площею (без кількості/об'єму,
            # напр. окремий "50 м2" для Вагонки) мовчки губився при
            # об'єднанні, хоча _overwrite_income_row (заміна рядка цілком)
            # цю площу вже коректно враховує.
            elif _number_value(incoming.get("area")) > 0:
                target["area"] = incoming.get("area")
                target["area_provided"] = incoming.get("area_provided", True)
                target["quantity"] = incoming.get("quantity")
                target["quantity_provided"] = incoming.get("quantity_provided", False)
            elif _number_value(incoming.get("linear")) > 0:
                target["linear"] = incoming.get("linear")
                target["linear_provided"] = incoming.get("linear_provided", True)
                target["quantity"] = incoming.get("quantity")
                target["quantity_provided"] = incoming.get("quantity_provided", False)

    def _apply_single_dimension_to_missing(self, payload, value):
        if _number_value(value) <= 0:
            return False
        rows = payload.get("rows") or []
        for row in rows:
            missing = [
                field
                for field in ("thickness", "width", "length")
                if _number_value(row.get(field)) <= 0
            ]
            if missing:
                row[missing[0]] = _number_value(value)
                return True
        return False

    def _first_or_new_income_row(self, payload):
        rows = payload.setdefault("rows", [])
        if rows:
            return rows[0]
        row = self._empty_income_row()
        rows.append(row)
        return row

    def _empty_income_row(self):
        return {
            "thickness": None,
            "width": None,
            "length": None,
            "quantity": None,
            "volume": None,
            "area": None,
            "linear": None,
            "quantity_provided": False,
            "volume_provided": False,
            "area_provided": False,
            "linear_provided": False,
            "quantity_typo_candidate": None,
            "row_id": None,
            "create_new": False,
        }

    # Крок 3+ "Дії": чек-лист розмірів/кількості тепер читає РЕДАГОВАНІ
    # мітки та наявність полів із bot_operation_fields (Задача користувача:
    # "щоб редагування полів у GUI реально міняло, що бот питає в чаті").
    # kind="income" (за замовч.) чи "sale" — та сама функція обслуговує
    # ОБИДВА виклики (приход і продаж використовують один парсер розмірів),
    # тож викликач має сказати, яку саме дію (parent_action_code) шукати.
    # Товар ПОЗА 4 заведеними категоріями (вільний текст) — operation_id не
    # резолвиться, падаємо на стару жорстко закодовану поведінку
    # (_income_missing_fields_legacy), без жодної зміни для цього випадку.
    def _income_missing_fields(self, store, payload, kind="income"):
        # Було бінарним "income чи sale" - для 3-го виду (kind="writeoff")
        # це помилково резолвило б проти "start_sale". Явний словник замість
        # ternary, щоб додавання майбутнього 4-го виду теж не забулось тут.
        parent_action_code = {
            "income": "start_income",
            "writeoff": "start_writeoff",
        }.get(kind, "start_sale")
        operation_id = resolve_operation_for_payload(store, parent_action_code, kind, payload)
        if operation_id is None:
            return self._income_missing_fields_legacy(payload)

        fields = {field[2]: field for field in store.list_operation_fields(operation_id)}
        missing = []
        # Задача користувача: "поле-запит добавив клієнта на приході — запит
        # не змінився... код не має бути з різних блоків складений, код має
        # бути цілісним". Приход (на відміну від продажу й антисептирования)
        # НІКОЛИ не мав окремої перевірки для client/volume/price_per_unit/
        # payment_method — ці 4 перевірялись лише в _sale_mandatory_fields_
        # missing/_antiseptic_mandatory_fields_missing, кожна своїм трохи
        # іншим кодом. Тепер ОДНА спільна _flat_checklist_missing_fields
        # (нижче) використовується всіма трьома — новий доданий "Клиент" на
        # приході тепер реально потрапляє в чек-лист. Для kind="sale" тут
        # НЕ додаємо — _sale_mandatory_fields_missing і так викликається
        # ОКРЕМО й конкатенується в _sale_missing_prompt; додавши тут теж,
        # вийшло б задвоєння "- Клиент" двічі в одному повідомленні. Додаємо
        # ПІСЛЯ (не перед) розмірів/кількості — щоб такі "плоскі" поля не
        # випереджали основні ідентифікаційні питання в списку.
        flat_missing = self._flat_checklist_missing_fields(fields, payload, kind) if kind == "income" else []
        if "product" in fields and not payload.get("product"):
            missing.append(fields["product"][3])
        if "breed" in fields and not payload.get("breed"):
            missing.append(fields["breed"][3])
        if (
            "condition" in fields
            and payload.get("product")
            and product_requires_type(payload.get("product"))
            and not payload.get("condition")
        ):
            missing.append(f"{fields['condition'][3]}: AD / KD / другое")

        has_quantity = "quantity" in fields
        has_measure = "measure" in fields
        rows = payload.get("rows") or []
        if not rows:
            # Без жодного рядка розмір (а отже й мп-детекція за товщиною/
            # шириною) ще невідомий — лишається лише товарна (Вагонка) ознака.
            for key in ("thickness", "width", "length"):
                if key in fields:
                    missing.append(fields[key][3])
            if has_quantity and has_measure:
                unit_word = "м2" if self._is_area_based_product(payload.get("product")) else "м3"
                missing.append(f"Количество шт или {unit_word}")
            elif has_quantity:
                # ОСБ (і будь-який інший товар без поля-запиту "measure"):
                # лише кількість, без "або м3" — той самий принцип, що вже
                # застосовує по-рядкова гілка нижче.
                missing.append(fields["quantity"][3])
            elif has_measure:
                unit_word = "м2" if self._is_area_based_product(payload.get("product")) else "м3"
                missing.append(f"{fields['measure'][3]} ({unit_word})")
            missing.extend(flat_missing)
            return self._unique_missing_fields(missing)

        for row in rows:
            for key in ("thickness", "width", "length"):
                if key in fields and _number_value(row.get(key)) <= 0:
                    missing.append(fields[key][3])
            if has_quantity and has_measure:
                if not self._income_row_has_amount(row):
                    unit_label = self._MEASURE_KIND_UNIT.get(self._row_measure_kind(payload, row), "шт")
                    missing.append(f"Количество шт или {unit_label}")
            elif has_quantity:
                if _number_value(row.get("quantity")) <= 0:
                    missing.append(fields["quantity"][3])
            elif has_measure:
                measure_present = any(_number_value(row.get(k)) > 0 for k in ("volume", "area", "linear"))
                if not measure_present:
                    unit_label = self._MEASURE_KIND_UNIT.get(self._row_measure_kind(payload, row), "шт")
                    missing.append(f"{fields['measure'][3]} ({unit_label})")
        missing.extend(flat_missing)
        return self._unique_missing_fields(missing)

    # Стара жорстко закодована поведінка (до Кроку 3+) — фолбек для товару
    # ПОЗА 4 заведеними категоріями (там немає жодної bot_operations-дії,
    # тож немає що конфігурувати) і "запобіжна сітка", якщо конфігурація
    # раптом зіпсована/відсутня.
    def _income_missing_fields_legacy(self, payload):
        missing = []
        if not payload.get("product"):
            missing.append("Продукт")
        if not payload.get("breed"):
            missing.append("Порода")
        if payload.get("product") and product_requires_type(payload.get("product")) and not payload.get("condition"):
            missing.append("Тип продукта: AD / KD / другое")

        rows = payload.get("rows") or []
        if not rows:
            amount_label = (
                "Количество шт или м2"
                if self._is_area_based_product(payload.get("product"))
                else "Количество шт или м3"
            )
            missing.extend(["Толщина", "Ширина", "Длина", amount_label])
            return self._unique_missing_fields(missing)

        dimension_labels = {
            "thickness": "Толщина",
            "width": "Ширина",
            "length": "Длина",
        }
        for row in rows:
            for field, label in dimension_labels.items():
                if _number_value(row.get(field)) <= 0:
                    missing.append(label)
            if not self._income_row_has_amount(row):
                unit_label = self._MEASURE_KIND_UNIT.get(self._row_measure_kind(payload, row), "шт")
                missing.append(f"Количество шт или {unit_label}")
        return self._unique_missing_fields(missing)

    def _unique_missing_fields(self, fields):
        unique = []
        seen = set()
        for field in fields:
            if field in seen:
                continue
            seen.add(field)
            unique.append(field)
        return unique

    # Показує зверху вже розпізнані дані (продукт/порода/тип/розмір/клієнт/
    # ціна/оплата) — щоб не доводилось тримати в голові, що вже прийняте,
    # поки список "не вистачає" ще коротшає. Загальне для приходу, продажу
    # й антисептирования (ТЗ: "щоб це було для всіх меню загально").
    # Реальний баг, знайдений користувачем (скріншот "Технічні поля" — там
    # "Товар", а в прев'ю чек-листа "Продукт"): ці підписи були жорстко
    # закодовані тут, НЕ читаючи bot_operation_fields.label — перейменування
    # поля "Товар" через GUI не міняло НІЧОГО у цьому конкретному рядку
    # (та сама "бутафорія", що й раніше зі звітами). resolved_labels — живі
    # мітки за kind (income/sale/service); якщо store/kind не передано чи
    # резолв не вдався (товар поза 4 категоріями) — старі підписи-фолбек
    # (product/breed/condition тепер "Товар" замість "Продукт", щоб
    # збігатися з тим самим полем у "Технічні поля" — усвідомлена зміна
    # тексту бота, а не просто фолбек).
    def _recognized_data_lines(self, payload, store=None, kind=None):
        resolved_labels = {}
        if store is not None and kind in ("income", "sale"):
            parent_action_code = "start_income" if kind == "income" else "start_sale"
            operation_id = resolve_operation_for_payload(store, parent_action_code, kind, payload)
            if operation_id is not None:
                resolved_labels = {field[2]: field[3] for field in store.list_operation_fields(operation_id)}
        elif store is not None and kind == "service":
            operation = store.get_operation_by_code("sale_antiseptic")
            if operation is not None:
                resolved_labels = {field[2]: field[3] for field in store.list_operation_fields(operation[0])}

        def label(field_key, default):
            return resolved_labels.get(field_key, default)

        # Задача користувача: "тепер товар і тип продукта з редагування
        # напису теж можна прибрати, бо буде одне вікно для цього загалом,
        # яке не буде жорстко до чогось прив'язане" — коли операція
        # резолвиться (resolved_labels непорожній, тобто це одна з 8
        # налаштованих категорій), товар/тип продукта ЗАВЖДИ відомі наперед
        # (prefill_json, не збираються від користувача) — вільний заголовок
        # (_operation_header_text) тепер покриває цю роль замість жорсткого
        # автоматичного "Товар: X\nТип продукта: Y". Для товару ПОЗА
        # 4 налаштованими категоріями (resolved_labels порожній, легасі-
        # шлях) — обидва рядки лишаються, там це РЕАЛЬНО зібрана інформація,
        # а не наперед відома.
        resolved = bool(resolved_labels)
        lines = []
        if payload.get("product") and not resolved:
            lines.append(f"{label('product', 'Товар')}: {display_product_name(payload)}")
        if payload.get("breed"):
            lines.append(f"{label('breed', 'Порода')}: {payload['breed']}")
        if payload.get("condition") and not resolved:
            lines.append(f"{label('condition', 'Тип продукта')}: {payload['condition']}")
        known_sizes = [
            income_item_known_size(row)
            for row in payload.get("rows") or []
            if income_item_known_size(row) != "размер"
        ]
        if known_sizes:
            lines.append(f"Размер: {', '.join(known_sizes)}")
        if payload.get("client"):
            lines.append(f"{label('client', 'Клиент')}: {payload['client']}")
        if _number_value(payload.get("volume")) > 0 and not (payload.get("rows")):
            # "volume" (антисептирование) навмисно НЕ резолвиться через
            # label(): жива мітка вже містить одиницю ("Объем услуги, м3"),
            # а цей рядок ще й сам дописує " м3" після значення — разом
            # вийшло б задвоєння одиниці. Мітка тут не бере участі в
            # редагуванні через Технічні поля, тому конфлікту з нею немає.
            lines.append(f"Объем услуги: {_display_bot_number(payload['volume'])} м3")
        if _number_value(payload.get("total_amount")) > 0:
            lines.append(f"{label('total_amount', 'Сумма')}: {_display_bot_number(payload['total_amount'])}")
        elif _number_value(payload.get("price_per_unit")) > 0:
            lines.append(f"{label('price_per_unit', 'Цена')}: {_display_bot_number(payload['price_per_unit'])}")
        if payload.get("payment_method"):
            lines.append(f"{label('payment_method', 'Способ оплаты')}: {payload['payment_method']}")
        return lines

    # Задача користувача: "запит-питання чат-бота має складатись із 2х
    # частин. 1 частина - заголовок, редагувати можна на свій смак, як
    # душа забажає. а все сам запрос формується автоматично" — вільний,
    # повністю редагований заголовок над автоматичним чек-листом
    # (recognized-data + "Не хватает данных..."). Той самий резолв
    # operation_id, що й _recognized_data_lines (income/sale/service), щоб
    # не дублювати логіку в 4 місцях. Використовує ЗАГАЛЬНИЙ
    # bot_message_templates (той самий механізм, що й фіксовані
    # повідомлення на кшталт "Приход. Выберите категорию товара:"), лише з
    # НОВИМ префіксом ключа "operation_header_{code}" і порожнім дефолтом —
    # поки адмін не задав власний текст, заголовка просто нема, поведінка
    # чат-бота НЕ змінюється.
    def _operation_header_text(self, store, kind, payload):
        if store is None:
            return ""
        if kind in ("income", "sale"):
            parent_action_code = "start_income" if kind == "income" else "start_sale"
            operation_id = resolve_operation_for_payload(store, parent_action_code, kind, payload)
            operation = store.get_operation(operation_id) if operation_id is not None else None
        elif kind == "service":
            operation = store.get_operation_by_code("sale_antiseptic")
        else:
            operation = None
        if operation is None:
            return ""
        return store.get_message_template(f"operation_header_{operation[1]}", "")

    # Підказка AD/KD тепер вбудована в саму мітку "_income_missing_fields"
    # (там, де вона й вирішується — за КЛЮЧЕМ поля, не за текстом мітки, щоб
    # адміністратор міг перейменувати "Тип продукта" на будь-що, і підказка
    # й далі коректно з'являлась) — тут просто виводимо готовий рядок.
    def _income_missing_prompt(self, missing_fields, payload=None, store=None):
        lines = []
        if payload is not None:
            header = self._operation_header_text(store, "income", payload)
            if header:
                lines.append(header)
                lines.append("")
            recognized = self._recognized_data_lines(payload, store=store, kind="income")
            if recognized:
                lines.extend(recognized)
                lines.append("")
        lines.append("Не хватает данных для прихода:")
        lines.extend(f"- {field}" for field in missing_fields)
        return "\n".join(
            lines
        )

    # Аудит коду: раніше булевий прапорець "quantity_limit_confirmed" означав
    # "більше НІКОЛИ не перевіряти цю операцію" — навіть якщо кількість потім
    # зростала через "Редактировать" на щось значно більше вже підтвердженого.
    # Тепер запам'ятовуємо ЧИСЛО (підсумок, який реально підтвердили) —
    # перевірка мовчить лише для ТОЧНО того самого чи меншого підсумку, будь-
    # яке збільшення знову перевищує поріг і перепитує.
    def _income_quantity_limit_issue(self, payload):
        total_quantity = sum(_number_value(row.get("quantity")) for row in payload.get("rows") or [])
        confirmed_total = payload.get("quantity_limit_confirmed_total")
        if confirmed_total is not None and total_quantity <= _number_value(confirmed_total):
            return None
        if total_quantity <= MAX_INCOME_TOTAL_QUANTITY:
            return None
        rows = payload.get("rows") or []
        row_index, item = max(
            enumerate(rows),
            key=lambda row: _number_value(row[1].get("quantity")),
        )
        return {
            "row_index": row_index,
            "quantity": _number_value(item.get("quantity")),
            "volume": _number_value(item.get("volume")),
            "total_quantity": total_quantity,
            "total_volume": sum(_number_value(row.get("volume")) for row in rows),
        }

    # payload (опційно) — якщо задано, у той самий список дописуються ще й
    # обов'язкові поля продажу (Клиент/Цена/Способ оплаты), яких ще не
    # вистачає. Реальна вимога користувача: бот має показувати ВЕСЬ список
    # того, що потрібно для оформлення продажу, ОДНИМ повідомленням від
    # самого початку (а не спершу розміри/кількість, і лише ПОТІМ, окремим
    # кроком, клієнта/ціну/оплату) — менше кроків для людини. Парсер вже й
    # так приймає Клиент:/Цена:/Оплата: у ЦЬОМУ САМОМУ повідомленні, навіть
    # поки розміри ще не завершені (_extract_sale_fields викликається в
    # "collect_income_missing" незалежно від стану рядків) — бракувало лише
    # видимості в самому запрошенні.
    def _sale_missing_prompt(self, missing_fields, payload=None, store=None):
        lines = []
        if payload is not None:
            header = self._operation_header_text(store, "sale", payload)
            if header:
                lines.append(header)
                lines.append("")
            recognized = self._recognized_data_lines(payload, store=store, kind="sale")
            if recognized:
                lines.extend(recognized)
                lines.append("")
        lines.append("Не хватает данных для продажи:")
        lines.extend(f"- {field}" for field in missing_fields)
        if payload is not None:
            lines.extend(f"- {field}" for field in self._sale_mandatory_fields_missing(store, payload))
        return "\n".join(
            lines
        )

    # Клиент/цена/способ оплаты (ТЗ п.4/п.8) — обов'язкові для продажу.
    # Раніше кожне з них питалось окремим коротким повідомленням одне за
    # одним ("Укажите клиента." -> "Укажите цену..." -> "Выберите способ
    # оплаты.") — користувач скаржився, що це забагато окремих кроків.
    # Тепер, як і для розмірів/кількості (_sale_missing_prompt вище),
    # показуємо ОДИН список усього, чого ще не вистачає — і він
    # коротшає з кожною відповіддю. Сам стейт-машина (ask_sale_client/
    # ask_sale_price/ask_sale_payment_method) НЕ змінилась: гола відповідь
    # без мітки все ще трактується як значення саме поточного поля — просто
    # текст запрошення тепер показує весь список, а не одне поле.
    # Крок 3+ "Дії": редаговані мітки з bot_operation_fields, як і для
    # розмірів вище. price_per_unit зберігається як БАЗОВЕ слово ("Цена") —
    # " за {одиниця}" дописується тут динамічно (одиниця залежить від
    # товару). Якщо товар ще невідомий (product не встановлено) — операцію
    # неможливо однозначно резолвити, падаємо на стару поведінку.
    def _sale_mandatory_fields_missing(self, store, payload):
        operation_id = resolve_operation_for_payload(store, "start_sale", "sale", payload) if store else None
        if operation_id is None:
            return self._sale_mandatory_fields_missing_legacy(payload)
        fields = {field[2]: field for field in store.list_operation_fields(operation_id)}
        return self._flat_checklist_missing_fields(fields, payload, "sale")

    # Крок 3+ "Дії": ОДНА спільна перевірка "плоских" (не по-рядкових) полів
    # — client/volume/price_per_unit/payment_method — замість того, що
    # продаж і антисептирование мали КОЖЕН свій окремий, трохи інакший
    # шматок коду, а приход не мав НІЯКОГО (звідси й реальний баг: "Клиент",
    # доданий на приход, бот не бачив — _income_missing_fields просто
    # ніколи не викликав жодної такої перевірки). Тепер усі три дії
    # використовують РІВНО цю саму функцію. "volume" — семантично
    # антисептирование-специфічне значення (payload["volume"] як єдине
    # ціле число на всю дію, а не по рядках, як у приході/продажу) — якщо
    # колись з'явиться на приході/продажі з іншим сенсом, знадобиться
    # окремий розгляд, але сьогодні такого поля там немає в жодній
    # засіяній дії.
    def _flat_checklist_missing_fields(self, fields, payload, kind):
        missing = []
        if "client" in fields and not payload.get("client"):
            missing.append(fields["client"][3])
        if "address" in fields and not payload.get("address"):
            missing.append(fields["address"][3])
        if "volume" in fields and not (_number_value(payload.get("volume")) > 0):
            missing.append(fields["volume"][3])
        has_price = _number_value(payload.get("price_per_unit")) > 0 or _number_value(payload.get("total_amount")) > 0
        if "price_per_unit" in fields and not has_price:
            label = fields["price_per_unit"][3]
            if kind == "service":
                # Антисептирование: одиниця завжди м3, тож мітка вже
                # містить повну фразу ("Цена за м3") — дописувати нічого
                # не треба.
                missing.append(label)
            else:
                unit_label = self._MEASURE_KIND_UNIT.get(self._payload_measure_kind(payload), "шт")
                missing.append(f"{label} за {unit_label}")
        if "payment_method" in fields and not payload.get("payment_method"):
            missing.append(fields["payment_method"][3])
        return missing

    def _sale_mandatory_fields_missing_legacy(self, payload):
        missing = []
        if not payload.get("client"):
            missing.append("Клиент")
        if not payload.get("address"):
            missing.append("Адрес выгрузки")
        has_price = _number_value(payload.get("price_per_unit")) > 0 or _number_value(payload.get("total_amount")) > 0
        if not has_price:
            unit_label = self._MEASURE_KIND_UNIT.get(self._payload_measure_kind(payload), "шт")
            missing.append(f"Цена за {unit_label}")
        if not payload.get("payment_method"):
            missing.append("Способ оплаты")
        return missing

    def _sale_mandatory_fields_prompt(self, store, payload):
        lines = []
        header = self._operation_header_text(store, "sale", payload)
        if header:
            lines.append(header)
            lines.append("")
        recognized = self._recognized_data_lines(payload, store=store, kind="sale")
        if recognized:
            lines.extend(recognized)
            lines.append("")
        lines.append("Не хватает данных для продажи:")
        lines.extend(f"- {field}" for field in self._sale_mandatory_fields_missing(store, payload))
        return "\n".join(lines)

    # Показує підсумкову суму продажу в картці підтвердження (ТЗ, розділ 11).
    # Не пише нічого в payload — фактичний total_amount, який піде в
    # SALES_SHEET, і так порахує sale_sheet_values (warehouse_data.py) для
    # кожного рядка окремо; тут лише узгоджений з тим самим розрахунком
    # попередній перегляд одним числом на всю продажу.
    # Товар без фізичного виміру (ОСБ) рахується напряму по кількості —
    # item.get(None) дав би просто None/0, тож для такого рядка беремо
    # item["quantity"] замість фізичного виміру.
    def _row_amount_for_pricing(self, payload, item):
        measure_key = self._row_measure_kind(payload, item)
        if measure_key is None:
            return _number_value(item.get("quantity"))
        return _number_value(item.get(measure_key))

    def _sale_total_amount(self, payload):
        total_amount = _number_value(payload.get("total_amount"))
        if total_amount > 0:
            return total_amount
        price_per_unit = _number_value(payload.get("price_per_unit"))
        if price_per_unit <= 0:
            return None
        # Кожен рядок підсумовується за СВОЇМ власним виміром (не одним
        # спільним для всього payload) — інакше продаж, що змішує звичайну
        # дошку (м3) і мп-розмір в ОДНОМУ повідомленні, тихо загубив би
        # вимір рядків іншого типу з підсумку (їхнє поле для "чужого"
        # measure_key завжди None).
        total_measure = sum(
            self._row_amount_for_pricing(payload, item)
            for item in payload.get("rows") or []
        )
        # _priced_amount (utils.py) - та сама формула, що вже й
        # sale_sheet_values/income_sheet_values/antiseptic_sheet_values
        # (warehouse_data.py) - аудит коду, 2026-08-14.
        return _priced_amount(price_per_unit, total_measure)

    # Знаходить ПЕРШИЙ рядок з одруком у кількості ("500штукыв", "100 ш"),
    # який ще без нормальної кількості — щоб спитати про нього окремо замість
    # загального "Не хватает данных: Количество шт или м3". Якщо рядків з
    # такою проблемою декілька — питає по одному, і після кожного Да/Нет
    # continue_operation проходить весь ланцюжок знову й знаходить наступний.
    def _income_quantity_typo_issue(self, payload):
        for index, row in enumerate(payload.get("rows") or []):
            candidate = row.get("quantity_typo_candidate")
            if candidate and not self._income_row_has_amount(row):
                return {"row_index": index, **candidate}
        return None

    def _quantity_typo_prompt(self, payload, typo_issue):
        rows = payload.get("rows") or []
        row_index = typo_issue.get("row_index", 0)
        row = rows[row_index] if 0 <= row_index < len(rows) else {}
        return {
            "type": "message",
            "text": (
                f"Позиция: {income_item_known_size(row)}\n"
                f"Не разобрал количество: \"{typo_issue.get('raw_text', '')}\".\n"
                f"Возможно, имели в виду {_display_bot_number(typo_issue.get('guessed_quantity'))} шт?\n"
                "Да / Нет / Отмена"
            ),
            "reply_markup": self._quantity_typo_keyboard(),
        }

    def _answer_value(self, answer, expected_field):
        parsed = self._parse_income_field_line(answer)
        if parsed and parsed.get("field") == expected_field:
            return parsed.get("value", "").strip()
        return answer.strip()

    def _apply_income_free_values(self, store, payload):
        free_values = payload.get("free_values") or []
        if not free_values:
            return

        try:
            _, columns, rows = warehouse_rows(store)
        except sqlite3.Error:
            return

        condition_values = self._existing_product_type_values(rows, columns.get("product"))
        existing = {
            "product": self._existing_product_values(rows, columns.get("product"), condition_values),
            "breed": self._existing_values(rows, columns.get("breed"), False),
            "condition": condition_values,
        }
        remaining = []
        for value in free_values:
            if self._is_income_command_line(value):
                continue
            if self._is_osb_product(value):
                payload["product"] = "ОСБ"
                payload["breed"] = "Другое"
                continue
            if self._is_osb_product(payload.get("product")) and self._value_exists(value, existing["breed"], False):
                continue
            if self._assign_income_free_value(payload, value, existing):
                continue
            remaining.append(value)
        payload["free_values"] = remaining

    def _existing_product_type_values(self, rows, product_column_index):
        values = []
        for value in self._existing_values(rows, product_column_index, False):
            _, product_type = self._split_product_condition(value, [])
            if product_type:
                values.append(product_type)
        unique = []
        seen = set()
        for value in values:
            key = _normalize_keyboard_code(value).replace(" ", "") or _normalize_phrase(value)
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(value)
        return unique

    def _existing_product_values(self, rows, column_index, condition_values=None):
        values = []
        for value in self._existing_values(rows, column_index, False):
            product, _ = self._split_product_condition(value, condition_values or [])
            if product:
                values.append(product)
        unique = []
        seen = set()
        for value in values:
            key = _normalize_phrase(value)
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(value)
        return unique

    def _split_product_condition(self, value, condition_values=None):
        text = " ".join(str(value or "").strip().split())
        if not text:
            return "", ""
        parts = text.split()
        if len(parts) < 2:
            return text, ""
        last = parts[-1]
        condition_values = condition_values or []
        if (
            self._looks_like_condition_code(last)
            or self._value_exists(last, condition_values, False)
            or self._suggest_value(last, condition_values, False) is not None
        ):
            return " ".join(parts[:-1]).strip(), last
        return text, ""

    def _assign_income_free_value(self, payload, value, existing):
        product, condition = self._split_product_condition(value, existing.get("condition"))
        if condition and not payload.get("condition"):
            payload["condition"] = condition
            if not product:
                return True
        product_candidate = product or value
        if not payload.get("product") and self._value_exists(value, existing["product"], False):
            payload["product"] = value
            return True
        if not payload.get("product") and self._value_exists(product_candidate, existing["product"], False):
            payload["product"] = product_candidate
            return True
        # Аудит коду: "орех"/"тіс" (звичайні породи деревини) обидва
        # транслітеруються в ≤4 латинські літери за _looks_like_condition_code
        # — раніше цей "здогад" перевірявся НЕЗАЛЕЖНО від того, чи value вже
        # відома/схожа порода, тож слово потрапляло в "стан" навіть коли воно
        # й так збігається з реальною породою складу. Голий здогад-за-формою
        # тепер довіряється лише тоді, коли value НЕ схожа на жодну відому
        # породу — якщо схожа, порода має пріоритет.
        looks_like_code_not_a_known_breed = self._looks_like_condition_code(value) and not (
            self._value_exists(value, existing["breed"], False)
            or self._suggest_value(value, existing["breed"], False) is not None
        )
        if not payload.get("condition") and (
            self._value_exists(value, existing["condition"], False)
            or self._suggest_value(value, existing["condition"], False) is not None
            or looks_like_code_not_a_known_breed
        ):
            payload["condition"] = value
            return True
        if not payload.get("breed") and (
            self._value_exists(value, existing["breed"], False)
            or self._suggest_value(value, existing["breed"], False) is not None
        ):
            payload["breed"] = value
            return True
        # Значення не збіглось ЖОДНИМ відомим товаром/типом/породою. Раніше
        # тут було "сліпе" призначення в породу (звідси баг: ім'я клієнта
        # "джон" мовчки ставало породою) — тепер для продажу без клієнта
        # стейджимо значення на підтвердження "це клієнт?" замість вгадування.
        # Багатослівне ім'я ("SCANDINAVIAN SMART HOUSE") розбивається на
        # ОКРЕМІ слова ще на етапі парсингу (_split_income_free_text) — тому
        # ДОПИСУЄМО кожне наступне нерозпізнане слово до вже стейдженого
        # кандидата, а не перезаписуємо його (реальний баг: останнє слово
        # мовчки витісняло всі попередні, і замість "SCANDINAVIAN SMART
        # HOUSE" кандидатом лишалось випадкове останнє слово з усього
        # повідомлення). Прихід клієнта не має, тому там лишається стара
        # поведінка нижче.
        if payload.get("operation_kind") == "sale" and not payload.get("client"):
            # Реальний баг з аудиту: те саме, що й ЕФАКТУРА (Задача 50), але
            # для ціни — гола ціна БЕЗ валютного слова ("6200" без "мдл")
            # мовчки дописувалась до кандидата в клієнти ("ACME SRL 6200"),
            # хоча насправді це ціна, яка просто прийшла без одиниці. Гола
            # число (без жодної літери) сюди й так потрапляє лише тоді, коли
            # ціни ще нема — вирізаємо ЙОГО ОКРЕМО як кандидата на ціну,
            # замість домішування до імені клієнта.
            if (
                not payload.get("price_per_unit")
                and payload.get("bare_price_candidate") is None
                and re.fullmatch(r"\d+(?:[.,]\d+)?", value.strip())
            ):
                payload["bare_price_candidate"] = value.strip()
                return True
            existing_candidate = payload.get("client_candidate")
            payload["client_candidate"] = f"{existing_candidate} {value}".strip() if existing_candidate else value
            return True
        # Прихід клієнта не має, але та сама проблема тут стосується
        # породи/типу/товару: "Дуб европейский" (нова, ще не відома складу
        # двослівна порода) розбивається на "Дуб"/"европейский" ще на етапі
        # парсингу — раніше перше слово мовчки йшло в породу, а ДРУГЕ (та
        # частина тієї ж назви) — уже в ТИП ПРОДУКТА, вигадуючи неіснуюче
        # значення (реальний баг з аудиту). Тепер, як і для клієнта в
        # продажу, накопичуємо нерозпізнані слова в ОДИН кандидат і питаємо,
        # а не розкидаємо їх по різних полях наосліп.
        if payload.get("operation_kind") != "sale":
            existing_candidate = payload.get("income_free_candidate")
            payload["income_free_candidate"] = (
                f"{existing_candidate} {value}".strip() if existing_candidate else value
            )
            return True
        if not payload.get("breed"):
            payload["breed"] = value
            return True
        if not payload.get("condition"):
            payload["condition"] = value
            return True
        if not payload.get("product"):
            payload["product"] = value
            return True
        return False

    def _looks_like_condition_code(self, value):
        normalized = _normalize_keyboard_code(value).replace(" ", "")
        return bool(normalized) and normalized.isascii() and normalized.isalpha() and len(normalized) <= 4

    # --- Нормалізація продукту/породи/стану до значень, що вже є на складі ---
    def _canonicalize_income_values(self, store, payload):
        try:
            _, columns, rows = warehouse_rows(store)
        except sqlite3.Error:
            return

        condition_values = self._existing_product_type_values(rows, columns.get("product"))
        if payload.get("product"):
            product, condition = self._split_product_condition(payload["product"], condition_values)
            if product:
                payload["product"] = product
            if condition and not payload.get("condition"):
                payload["condition"] = condition

        field_columns = {
            "product": columns.get("product"),
            "breed": columns.get("breed"),
            "condition": columns.get("product"),
        }
        for field, column in field_columns.items():
            if not payload.get(field):
                continue
            if field == "product":
                existing = self._existing_product_values(rows, column, condition_values)
            elif field == "condition":
                existing = condition_values
            else:
                existing = self._existing_values(rows, column, False)
            payload[field] = self._canonical_income_text(
                payload[field],
                existing,
                code_match=(field == "condition"),
            )

        self._apply_osb_income_defaults(payload)

    def _is_osb_product(self, value):
        normalized = _normalize_phrase(value)
        keyboard = _normalize_keyboard_code(value).replace(" ", "")
        return normalized in {"осб", "osb"} or keyboard == "ocb"

    def _apply_osb_income_defaults(self, payload):
        if payload.get("product") and self._is_osb_product(payload["product"]):
            payload["product"] = "ОСБ"
            payload["breed"] = "Другое"
            payload["condition"] = None

    # Вагонка веде облік у площі (м2), а не в об'ємі (м3) — так само в
    # реальних даних складу ("Основная ед. учета" = м2) і в оригінальній
    # специфікації (розділ ПРИХОД -> ВАГОНКА просить саме "Площадь, м²", без
    # "Объем"). Товщина все одно потрібна для ідентифікації позиції, просто
    # не бере участі в самому розрахунку кількості.
    def _is_area_based_product(self, value):
        return is_area_based_product(value)

    # ОСБ рахується ЛИСТАМИ за фіксованою ціною за лист, без жодного
    # фізичного виміру (об'єм/площа/мп) — на відміну від решти товарів, де
    # РІВНО один вимір завжди застосовний. _row_measure_kind повертає None
    # для такого товару, і кожне місце, що з ним працює, лікує None як
    # "ціна/показ рахуються напряму по кількості (шт), фізичного виміру
    # немає взагалі" (не просто "ще не визначено").
    def _is_quantity_only_product(self, value):
        return is_quantity_only_product(value)

    # Спільний фрагмент regex для ВСІХ токенів одиниць виміру кількості
    # (шт/м3/м2/мп) — раніше список "шт|штук|м3|...|квадрат\w*" був
    # продубльований у 5+ місцях (виключення для розпізнавання розмірів,
    # парсери кількості/об'єму/площі, "прибрати суму з рядка" тощо); тепер
    # додавання мп в ОДНЕ місце автоматично покриває всі ці місця.
    _MEASURE_UNIT_TOKEN_ALTERNATION = (
        r"шт|штук|м3|м³|куб|м2|м²|кв\.?\s*м|квадрат\w*|"
        r"мп|м\.?\s*п\.?|пог\.?\s*м\w*|погон\w*"
    )

    def _is_linear_meter_size(self, thickness, width):
        return is_linear_meter_size(thickness, width)

    # Єдина точка, де визначається "чим міряти" КОНКРЕТНИЙ рядок (розмір) —
    # площею (Вагонка, за назвою товару), погонними метрами (за розміром
    # товщина x ширина) чи об'ємом (усе інше за замовчуванням). Товарна
    # властивість (Вагонка) перевіряється ПЕРШОЮ — на практиці розміри
    # Вагонки (тонка, широка дошка) ніколи не збігаються з 25x50/30x50/
    # 50x50, але порядок перевірки все одно важливий для однозначності.
    def _row_measure_kind(self, payload, item):
        if self._is_quantity_only_product(payload.get("product")):
            return None
        if self._is_area_based_product(payload.get("product")):
            return "area"
        if self._is_linear_meter_size(item.get("thickness"), item.get("width")):
            return "linear"
        return "volume"

    # Для payload-рівневих підказок/міток (напр. "Цена за м3" у запиті
    # ціни), де ціна за одиницю — ОДНЕ значення на весь продаж/прихід, а не
    # на рядок: беремо вимір ПЕРШОГО рядка як показовий. На практиці змішані
    # продажі (і мп, і м3 в ОДНІЙ операції) — рідкість; коли вона таки
    # трапляється, підпис може не відповідати іншим рядкам, але сам
    # розрахунок (sale_sheet_values/apply_sale_operation) все одно рахує
    # кожен рядок за ЙОГО власним виміром, незалежно від цієї підказки.
    def _payload_measure_kind(self, payload):
        rows = payload.get("rows") or []
        if rows:
            return self._row_measure_kind(payload, rows[0])
        if self._is_quantity_only_product(payload.get("product")):
            return None
        return "area" if self._is_area_based_product(payload.get("product")) else "volume"

    def _canonical_income_text(self, value, existing_values, code_match=False):
        if value in (None, ""):
            return value
        text = str(value).strip()
        normalized = _normalize_phrase(text)
        for existing in existing_values:
            if _normalize_phrase(existing) == normalized:
                return existing

        if code_match:
            keyboard_code = _normalize_keyboard_code(text).replace(" ", "")
            for existing in existing_values:
                if _normalize_keyboard_code(existing).replace(" ", "") == keyboard_code:
                    return existing
            if keyboard_code and keyboard_code.isascii() and keyboard_code.isalpha() and len(keyboard_code) <= 4:
                return keyboard_code.upper()

        return self._format_new_income_text(text)

    def _format_new_income_text(self, text):
        text = " ".join(str(text).strip().split())
        if not text:
            return text
        if text.isascii() and text.replace(" ", "").isalpha() and len(text.replace(" ", "")) <= 4:
            return text.upper()
        return " ".join(part[:1].upper() + part[1:].lower() for part in text.split())

    def _reject_new_income_value(self, store, context, payload, validation):
        payload.pop("validation", None)
        field = validation.get("field")
        row_index = validation.get("row_index")
        if row_index is None:
            payload[field] = None
            status_by_field = {
                "breed": "ask_breed",
                "condition": "ask_condition",
                "product": "ask_product",
            }
            return self._save_income_question(
                store,
                context,
                payload,
                status_by_field.get(field, "ask_breed"),
                self._income_field_prompt(field),
            )

        payload["dimension_request"] = validation
        return self._save_income_question(
            store,
            context,
            payload,
            "ask_dimension",
            self._dimension_prompt(payload, validation),
        )

    def _income_field_prompt(self, field):
        prompts = {
            "breed": "Напишите породу для этой операции.",
            "condition": "Укажите тип продукта. Например: AD, KD или другое.",
            "product": "Напишите продукт для этой операции.",
        }
        return prompts.get(field, "Напишите корректное значение.")

    def _dimension_prompt(self, payload, validation):
        row_index = validation.get("row_index", 0)
        label = validation.get("label", "Значение")
        item = payload.get("rows", [{}])[row_index]
        if validation.get("missing"):
            known_size = income_item_known_size(item)
            if validation.get("field") == "length":
                return (
                    f"Вы указали {known_size}, но не указали длину.\n"
                    "Напишите только длину для этой позиции. Например: 6000, 6м или 6к."
                )
        return f'Напишите корректное значение для "{label}" в позиции {income_item_size(item)}.'

    def _validation_position_text(self, payload, validation):
        if not payload:
            return ""
        row_index = validation.get("row_index")
        rows = payload.get("rows") or []
        if row_index is None or row_index < 0 or row_index >= len(rows):
            return ""
        item = rows[row_index]
        try:
            size = income_item_size(item)
        except (KeyError, TypeError):
            size = income_item_known_size(item)
        return f"Позиция: {row_index + 1}. {size}\n"

    def _validation_suggestion_prompt(self, validation, payload=None):
        label = validation.get("label", "Значение")
        value = _display_bot_number(validation.get("value"))
        suggestion = _display_bot_number(validation.get("suggestion"))
        position_text = self._validation_position_text(payload, validation)
        if validation.get("field") in {"thickness", "width", "length"}:
            dimension_labels = {
                "thickness": "толщину",
                "width": "ширину",
                "length": "длину",
            }
            return self._yes_no_reply(
                f"{position_text}"
                f"Вы ввели {dimension_labels.get(validation.get('field'), label.lower())} {value}.\n"
                f"Возможно, имели в виду {suggestion}?\n"
                "Да / Нет"
            )
        if validation.get("field") == "client":
            return {
                "type": "message",
                "text": (
                    f'Клиент "{value}" не найден.\n'
                    f'Возможно, вы имели в виду "{suggestion}"?\n\n'
                    f'"Принять и запомнить" — в следующий раз "{value}" сразу '
                    f'будет распознан как "{suggestion}", без этого вопроса.\n'
                    '"Просто принять" — только в этот раз.'
                ),
                "reply_markup": self._client_suggestion_keyboard(suggestion),
            }
        return self._yes_no_reply(
            f'{label} "{value}" не найден.\n'
            f'Возможно, вы имели в виду "{suggestion}"?\n'
            "Да / Нет"
        )

    def _field_mapping_question(self, store, field_mapping):
        label = field_mapping.get("label", "значение")
        value = field_mapping.get("value", "")
        return "\n".join(
            [
                f'Не понимаю, к какой колонке относится "{label}" со значением "{value}".',
                "Выберите вариант:",
                f"1. Порода{self._field_examples(store, 'breed')}",
                f"2. Тип продукта{self._field_examples(store, 'condition')}",
                f"3. Продукт{self._field_examples(store, 'product')}",
            ]
        )

    def _field_examples(self, store, field):
        try:
            _, columns, rows = warehouse_rows(store)
        except sqlite3.Error:
            return ""
        column = columns.get(field)
        if column is None:
            return ""
        if field == "product":
            condition_values = self._existing_product_type_values(rows, columns.get("product"))
            values = self._existing_product_values(rows, column, condition_values)
        elif field == "condition":
            values = self._existing_product_type_values(rows, columns.get("product"))
        else:
            values = self._existing_values(rows, column, False)
        examples = [str(value) for value in values[:5]]
        return f" ({', '.join(examples)})" if examples else ""

    def _field_from_mapping_answer(self, answer):
        normalized = _normalize_phrase(answer)
        if normalized in {"1", "порода"}:
            return "breed"
        if normalized in {"2", "состояние", "стан", "сорт"}:
            return "condition"
        if normalized in {"3", "продукт", "товар"}:
            return "product"
        return None

    # --- Розрахунок кількості/об'єму, конфлікти введених значень ---
    # Площинні товари (вагонка) рахуються по-іншому (ширина x довжина x
    # кількість, товщина не бере участі), тож усе розгалужено на measure_key
    # ("area" чи "volume") — та сама логіка розпізнавання/конфліктів, лише
    # інша формула й одиниця. Вихід (item["volume"]/item["area"]) лишається
    # None для непридатного поля — так само, як у звіті "Остаток".
    # measure_kind ("volume"/"area"/"linear") — те саме, що повертає
    # _row_measure_kind, але тепер визначається ПО КОЖНОМУ РЯДКУ окремо
    # (не один раз на весь payload): одна продажа/прихід можуть містити і
    # звичайну дошку (м3), і мп-розмір (25x50/30x50/50x50) одночасно.
    def _prepare_income_amounts(self, payload):
        for row_index, item in enumerate(payload["rows"]):
            measure_key = self._row_measure_kind(payload, item)
            if measure_key is None:
                # ОСБ (і будь-який інший товар без фізичного виміру): нема
                # об'єму/площі/мп, з якими можна звіряти чи розраховувати
                # кількість — просто приймаємо кількість як є, округливши
                # до цілого числа штук (_quantity_options_issue тут не
                # підходить: вона будує варіанти РАЗОМ із перерахованим
                # виміром, якого для цього товару не існує).
                quantity = item.get("quantity")
                if quantity is None or _number_value(quantity) <= 0:
                    request = {"row_index": row_index}
                    return {
                        "status": "ask_item_amount",
                        "payload_key": "amount_request",
                        "payload": request,
                        "message": self._amount_prompt(payload, request),
                    }
                item["quantity"] = int(round(_number_value(quantity)))
                continue
            piece_amount = self._piece_measure(item, measure_key)
            if piece_amount <= 0:
                continue

            quantity = item.get("quantity")
            measure_value = item.get(measure_key)
            has_quantity = quantity is not None and _number_value(quantity) > 0
            has_measure = measure_value is not None and _number_value(measure_value) > 0

            if has_quantity:
                rounded_quantity = round(_number_value(quantity))
                if abs(_number_value(quantity) - rounded_quantity) <= INCOME_QUANTITY_TOLERANCE:
                    item["quantity"] = int(rounded_quantity)
                else:
                    return self._quantity_options_issue(payload, row_index, _number_value(quantity), None)

            if has_quantity and not has_measure:
                item[measure_key] = self._calculated_measure(item, item["quantity"], measure_key)
                item[f"{measure_key}_provided"] = False
                continue

            if has_measure and not has_quantity:
                raw_quantity = _number_value(measure_value) / piece_amount
                rounded_quantity = round(raw_quantity)
                if rounded_quantity > 0 and abs(raw_quantity - rounded_quantity) <= INCOME_QUANTITY_TOLERANCE:
                    item["quantity"] = int(rounded_quantity)
                    item["quantity_provided"] = False
                    continue
                return self._quantity_options_issue(payload, row_index, raw_quantity, _number_value(measure_value))

            if has_quantity and has_measure:
                calculated_measure = self._calculated_measure(item, item["quantity"], measure_key)
                if abs(calculated_measure - _number_value(measure_value)) > INCOME_VOLUME_TOLERANCE + 0.0000001:
                    conflict = {
                        "row_index": row_index,
                        "entered_volume": _number_value(measure_value),
                        "calculated_volume": calculated_measure,
                        "quantity": item["quantity"],
                        "measure_kind": measure_key,
                    }
                    return {
                        "status": "confirm_volume_conflict",
                        "payload_key": "volume_conflict",
                        "payload": conflict,
                        "message": self._volume_conflict_prompt(payload, conflict),
                    }
                continue

            request = {"row_index": row_index}
            return {
                "status": "ask_item_amount",
                "payload_key": "amount_request",
                "payload": request,
                "message": self._amount_prompt(payload, request),
            }
        return None

    # Реальний ризик (аудит коду, 2026-08-14): _shared_piece_measure вже
    # імпортована (з utils.py) саме для цього — але тут раніше жила окрема,
    # вручну переписана копія тієї самої формули (3 методи, по одному на
    # м3/м2/мп), яку той імпорт мовчки обходив. Дві копії формули "скільки
    # м3/м2/мп в одній штуці" — точно той самий клас ризику, що вже реально
    # ламався (float-шум) для формули суми (див. _priced_amount, utils.py) і
    # тепер зведений до одного джерела. item.get(...) тут — той самий набір
    # полів, що utils.piece_measure очікує позиційно.
    def _piece_measure(self, item, measure_kind):
        return _shared_piece_measure(
            item.get("thickness"), item.get("width"), item.get("length"), measure_kind
        )

    def _calculated_measure(self, item, quantity, measure_kind):
        return round(self._piece_measure(item, measure_kind) * _number_value(quantity), 6)

    def _quantity_options_issue(self, payload, row_index, raw_quantity, entered_volume):
        lower = max(1, int(raw_quantity))
        upper = lower + 1
        quantities = sorted({lower, upper})
        item = payload["rows"][row_index]
        measure_key = self._row_measure_kind(payload, item)
        options = [
            {
                "quantity": quantity,
                measure_key: self._calculated_measure(item, quantity, measure_key),
            }
            for quantity in quantities
        ]
        option_payload = {
            "row_index": row_index,
            "raw_quantity": raw_quantity,
            "entered_volume": entered_volume,
            "options": options,
            "measure_kind": measure_key,
        }
        return {
            "status": "choose_quantity_option",
            "payload_key": "quantity_options",
            "payload": option_payload,
            "message": self._quantity_options_prompt(payload, option_payload),
        }

    def _volume_conflict_prompt(self, payload, conflict):
        item = payload["rows"][conflict["row_index"]]
        unit = self._MEASURE_KIND_UNIT[conflict.get("measure_kind", "volume")]
        return self._yes_no_reply(
            f"Для {income_item_size(item)} и {_display_bot_number(conflict['quantity'])} шт "
            f"получается {_display_bot_number(conflict['calculated_volume'])} {unit}, "
            f"а вы указали {_display_bot_number(conflict['entered_volume'])} {unit}.\n"
            f"Использовать расчетное значение {_display_bot_number(conflict['calculated_volume'])} {unit}?\n"
            "Да / Нет"
        )

    _MEASURE_KIND_WORD = {"volume": "объему", "area": "площади", "linear": "погонным метрам"}

    def _quantity_options_prompt(self, payload, options):
        row_index = options.get("row_index", 0)
        item = payload["rows"][row_index]
        raw_quantity = options.get("raw_quantity", 0)
        entered_volume = options.get("entered_volume")
        measure_key = options.get("measure_kind", "volume")
        unit = self._MEASURE_KIND_UNIT[measure_key]
        measure_word = self._MEASURE_KIND_WORD[measure_key]
        header = (
            f"По {measure_word} {_display_bot_number(entered_volume)} {unit} "
            if entered_volume is not None
            else ""
        )
        lines = [
            f"{header}для {income_item_size(item)} получается {_display_bot_number(round(raw_quantity, 3))} шт.",
            "Штуки должны быть целым числом.",
            "Ближайшие варианты:",
        ]
        for index, option in enumerate(options.get("options", []), start=1):
            lines.append(
                f"{index}. {_display_bot_number(option['quantity'])} шт = "
                f"{_display_bot_number(option[measure_key])} {unit}"
            )
        lines.append("Напишите номер варианта или количество шт вручную.")
        return "\n".join(lines)

    def _amount_prompt(self, payload, request):
        row_index = request.get("row_index", 0)
        item = payload["rows"][row_index]
        measure_key = self._row_measure_kind(payload, item)
        if measure_key is None:
            return (
                f"Напишите количество шт для позиции {income_item_size(item)}.\n"
                "Например: 100 шт."
            )
        unit = self._MEASURE_KIND_UNIT[measure_key]
        example = {"area": "5 м2", "linear": "5 мп"}.get(measure_key, "3 м3")
        return (
            f"Напишите количество шт или {unit} для позиции {income_item_size(item)}.\n"
            f"Например: 100 шт или {example}."
        )

    # Розбір відповіді на "Клиент X не найден, возможно Y?" — на відміну від
    # звичайного Да/Нет тут ТРИ різні дії: прийняти й запам'ятати цей одрук
    # назавжди (client_name_aliases), прийняти лише цей раз, чи відхилити.
    # Природна мова теж працює ("так"/"ок"/"добре"/"хорошо" -> accept;
    # "запомни Joseph"/"запам'ятай" -> remember) — не лише готові кнопки.
    def _parse_client_suggestion_decision(self, answer):
        if self._is_edit_request(answer):
            return "edit"
        normalized = _normalize_phrase(answer)
        if "запомни" in normalized or "запам ят" in normalized or "запамят" in normalized:
            return "remember"
        if normalized.startswith("прост") and "прин" in normalized:
            return "accept"
        decision = self._yes_no(answer)
        if decision is True:
            return "accept"
        if decision is False:
            return "reject"
        return None

    # --- Валідація нових значень (мапінг колонок складу — тепер у warehouse_data.py) ---
    def _existing_values(self, rows, column_index, numeric=False):
        values = []
        for _, row in rows:
            if column_index is None or column_index >= len(row):
                continue
            value = row[column_index]
            if value in (None, ""):
                continue
            values.append(_number_value(value) if numeric else str(value).strip())
        unique = []
        seen = set()
        for value in values:
            key = value if numeric else _normalize_phrase(value)
            if key in seen:
                continue
            seen.add(key)
            unique.append(value)
        return unique

    def _value_exists(self, value, existing_values, numeric=False):
        if numeric:
            number = _number_value(value)
            return any(abs(number - _number_value(existing)) < 0.000001 for existing in existing_values)
        normalized = _normalize_phrase(value)
        return any(_normalize_phrase(existing) == normalized for existing in existing_values)

    def _suggest_value(self, value, existing_values, numeric=False):
        if numeric:
            number = _number_value(value)
            candidates = [_number_value(existing) for existing in existing_values]
            for candidate in candidates:
                if abs(number / 10 - candidate) < 0.000001 or abs(number * 10 - candidate) < 0.000001:
                    return candidate
            if candidates:
                nearest = min(candidates, key=lambda candidate: abs(candidate - number))
                if number and abs(nearest - number) / abs(number) <= 0.2:
                    return nearest
            return None

        normalized_values = {_normalize_phrase(existing): existing for existing in existing_values}
        normalized = _normalize_phrase(value)
        keyboard_code = _normalize_keyboard_code(value)
        if keyboard_code:
            for existing in existing_values:
                if (
                    _normalize_keyboard_code(existing) == keyboard_code
                    and _normalize_phrase(existing) != normalized
                ):
                    return existing
        matches = get_close_matches(normalized, list(normalized_values), n=1, cutoff=0.72)
        if not matches:
            return None
        # Аудит коду: cutoff=0.72 сам по собі занадто грубий для коротких
        # слів — "сосна"/"осина" (дві РІЗНІ реальні породи) дають ratio=0.8,
        # вище порогу, і фактично невідрізнимі від справжніх одруків такої ж
        # довжини ("клен"/"клён"=0.75, "береза"/"бреза"=0.909). Перша версія
        # цього фіксу вимагала Левенштейн-відстань ≤ 1 — це виправило короткі
        # породи, але зламало РЕАЛЬНИЙ, уже існуючий тест: "IMPEX TRAID SRL"
        # (одрук клієнта) проти "IMPEX TRADE SRL" — відстань 2 на 15-символьному
        # рядку, це один цілком нормальний людський одрук, довший рядок просто
        # природньо допускає більше посимвольних відмінностей на той самий
        # "один одрук". Тому поріг — ВІДНОСНА відстань (до довжини слова), а
        # не абсолютна: короткі слова толерують 1 правку, довгі — пропорційно
        # більше, з розумною стелею (3), щоб дуже довгі рядки не почали
        # збігатися з чимось геть іншим лише через довжину.
        if not self._looks_like_typo(normalized, matches[0]):
            return None
        return normalized_values[matches[0]]

    # Поріг — ВІДНОСНА відстань (без абсолютної стелі: перша чернетка мала
    # ще й distance <= 3, але це зламало реальний наявний тест — "IMPEX
    # TRAID SRL 2" проти "IMPEX TRADE SRL", distance=4 на 17-символьному
    # рядку, відносно лише 0.235, цілком розумний одрук/варіація довшого
    # імені клієнта). Перевірено на коротких породах деревини
    # (сосна/осина/дуб/клен/береза/ясень/орех/бук/липа — жодна пара РІЗНИХ
    # порід не проходить, усі справжні одруки проходять) і на довших
    # назвах клієнтів (impex traid/trade srl, impex traid srl 2/impex trade
    # srl — реальні одруки — проходять; finam plus srl/impex trade srl —
    # геть різні компанії — не проходить).
    def _looks_like_typo(self, left, right):
        if not left or not right:
            return False
        distance = self._levenshtein_distance(left, right)
        if distance == 0:
            return True
        max_len = max(len(left), len(right))
        return distance / max_len <= 0.35

    def _levenshtein_distance(self, left, right):
        if left == right:
            return 0
        previous_row = list(range(len(right) + 1))
        for row_index, left_char in enumerate(left, start=1):
            current_row = [row_index]
            for col_index, right_char in enumerate(right, start=1):
                cost = 0 if left_char == right_char else 1
                current_row.append(
                    min(
                        previous_row[col_index] + 1,
                        current_row[col_index - 1] + 1,
                        previous_row[col_index - 1] + cost,
                    )
                )
            previous_row = current_row
        return previous_row[-1]

    def _suggest_dimension_value(self, field, value, existing_values, numeric=False):
        if field == "length":
            number = _number_value(value)
            if 0 < number < 1000:
                candidate = number
                while candidate < 1000:
                    candidate *= 10
                existing_numbers = [_number_value(existing) for existing in existing_values]
                for existing in existing_numbers:
                    if abs(existing - candidate) < 0.000001:
                        return existing
                if existing_numbers:
                    nearest = min(existing_numbers, key=lambda existing: abs(existing - candidate))
                    if nearest >= 1000 and candidate and abs(nearest - candidate) / candidate <= 0.25:
                        return nearest
                return candidate
        return self._suggest_value(value, existing_values, numeric)

    def _confirmed_new_key(self, validation):
        return f"{validation.get('field')}:{validation.get('row_index', '')}:{_normalize_phrase(validation.get('value'))}"

    def _is_new_value_confirmed(self, payload, validation):
        return self._confirmed_new_key(validation) in payload.get("confirmed_new", [])

    def _mark_new_value_confirmed(self, payload, validation):
        payload.setdefault("confirmed_new", [])
        key = self._confirmed_new_key(validation)
        if key not in payload["confirmed_new"]:
            payload["confirmed_new"].append(key)

    def _apply_validated_value(self, payload, validation, value):
        field = validation.get("field")
        row_index = validation.get("row_index")
        if row_index is None:
            payload[field] = value
        else:
            payload["rows"][row_index][field] = _number_value(value)

    def _next_income_validation_issue(self, store, payload):
        headers, columns, rows = warehouse_rows(store)
        missing_columns = required_warehouse_columns(columns)
        if missing_columns:
            return {
                "field": "schema",
                "label": "Колонки склада",
                "value": ", ".join(missing_columns),
                "suggestion": None,
            }

        condition_values = self._existing_product_type_values(rows, columns["product"])
        checks = [
            {"field": "product", "label": "Продукт", "column": columns["product"], "numeric": False},
            {"field": "breed", "label": "Порода", "column": columns["breed"], "numeric": False},
            {"field": "condition", "label": "Тип продукта", "column": columns["product"], "numeric": False},
        ]

        for check in checks:
            value = payload.get(check["field"])
            if check["field"] == "product":
                existing = self._existing_product_values(rows, check["column"], condition_values)
            elif check["field"] == "condition":
                existing = condition_values
            else:
                existing = self._existing_values(rows, check["column"], check["numeric"])
            validation = {
                "field": check["field"],
                "label": check["label"],
                "value": value,
                "row_index": None,
            }
            if check["field"] == "condition" and not value:
                continue
            if value and not self._value_exists(value, existing, check["numeric"]):
                if self._is_new_value_confirmed(payload, validation):
                    continue
                validation["suggestion"] = self._suggest_value(value, existing, check["numeric"])
                return validation

        row_checks = [
            {"field": "thickness", "label": "Толщина", "column": columns["thickness"], "numeric": True},
            {"field": "width", "label": "Ширина", "column": columns["width"], "numeric": True},
            {"field": "length", "label": "Длина", "column": columns["length"], "numeric": True},
        ]
        for row_index, item in enumerate(payload["rows"]):
            for check in row_checks:
                value = item[check["field"]]
                existing = self._existing_values(rows, check["column"], check["numeric"])
                validation = {
                    "field": check["field"],
                    "label": check["label"],
                    "value": value,
                    "row_index": row_index,
                }
                if not self._value_exists(value, existing, check["numeric"]):
                    if self._is_new_value_confirmed(payload, validation):
                        continue
                    validation["suggestion"] = self._suggest_dimension_value(
                        check["field"],
                        value,
                        existing,
                        check["numeric"],
                    )
                    return validation
        return None

    def _next_sale_validation_issue(self, store, payload):
        headers, columns, rows = warehouse_rows(store)
        missing_columns = required_sale_warehouse_columns(columns)
        if missing_columns:
            return {
                "field": "schema",
                "label": "Колонки склада",
                "value": ", ".join(missing_columns),
                "suggestion": None,
            }

        condition_values = self._existing_product_type_values(rows, columns["product"])
        checks = [
            {"field": "product", "label": "Продукт", "column": columns["product"], "numeric": False},
            {"field": "breed", "label": "Порода", "column": columns["breed"], "numeric": False},
            {"field": "condition", "label": "Тип продукта", "column": columns["product"], "numeric": False},
        ]

        for check in checks:
            value = payload.get(check["field"])
            if check["field"] == "product":
                existing = self._existing_product_values(rows, check["column"], condition_values)
            elif check["field"] == "condition":
                existing = condition_values
            else:
                existing = self._existing_values(rows, check["column"], check["numeric"])
            if check["field"] == "condition" and not value:
                continue
            if value and not self._value_exists(value, existing, check["numeric"]):
                suggestion = self._suggest_value(value, existing, check["numeric"])
                if suggestion is not None:
                    return {
                        "field": check["field"],
                        "label": check["label"],
                        "value": value,
                        "row_index": None,
                        "suggestion": suggestion,
                    }
                return None

        row_checks = [
            {"field": "thickness", "label": "Толщина", "column": columns["thickness"], "numeric": True},
            {"field": "width", "label": "Ширина", "column": columns["width"], "numeric": True},
            {"field": "length", "label": "Длина", "column": columns["length"], "numeric": True},
        ]
        for row_index, item in enumerate(payload["rows"]):
            for check in row_checks:
                value = item[check["field"]]
                existing = self._existing_values(rows, check["column"], check["numeric"])
                if not self._value_exists(value, existing, check["numeric"]):
                    suggestion = self._suggest_dimension_value(
                        check["field"],
                        value,
                        existing,
                        check["numeric"],
                    )
                    if suggestion is not None:
                        return {
                            "field": check["field"],
                            "label": check["label"],
                            "value": value,
                            "row_index": row_index,
                            "suggestion": suggestion,
                        }
                    return None

        return self._sale_client_validation_issue(store, payload)

    # Клієнт — не колонка складу (СКЛАД), а історія самого листа продажів,
    # тому не влазить у checks/row_checks вище (ті завжди читають columns/rows
    # зі СКЛАД). Той самий родовий механізм (_existing_values/_suggest_value/
    # _value_exists), лише джерело — "ПРОДАЖА МАТЕРИАЛА". На відміну від
    # product/breed/condition, новий клієнт без схожого збігу — це НЕ помилка
    # (клієнтська база природно росте), тому suggestion=None тут не означає
    # "позиція не знайдена на складі" — обробляється в _continue_sale_operation.
    def _sale_client_validation_issue(self, store, payload):
        client = payload.get("client")
        if not client:
            return None
        # Раніше запам'ятана виправлена назва ("jospeh" -> "Joseph",
        # обрано через "Принять и запомнить" на попередньому одруку) —
        # застосовуємо мовчки, без повторного питання про той самий одрук.
        remembered = store.get_client_alias(client)
        if remembered:
            payload["client"] = remembered
            client = remembered
        sales_headers = store.get_headers(SALES_SHEET_NAME)
        client_column = sales_columns(sales_headers).get("client")
        if client_column is None:
            return None
        sales_rows = store.fetch_rows(SALES_SHEET_NAME, 100000, 0)
        existing = self._existing_values(sales_rows, client_column, False)
        if self._value_exists(client, existing, False):
            return None
        validation = {"field": "client", "label": "Клиент", "value": client, "row_index": None}
        if self._is_new_value_confirmed(payload, validation):
            return None
        validation["suggestion"] = self._suggest_value(client, existing, False)
        return validation
