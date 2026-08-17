"""Звіт продажів (ПРОДАЖИ), залишки складу "Остаток", фільтри/перегляд складу, історія приходу. Частина розбиття telegram_dialog.py - див. telegram_dialog.py для повної карти."""

import re
import sqlite3
from datetime import date, datetime

import permissions as perm
import reports
from paths import REPORTS_DIR, SETTINGS_PATH
from settings import SettingsStore
from utils import (
    _display_bot_number,
    _display_value,
    _normalize_keyboard_code,
    _normalize_phrase,
    _number_value,
)
from warehouse_data import (
    BOT_MESSAGE_DEFAULTS,
    antiseptic_rows,
    item_measure_kind,
    ITEM_MEASURE_UNIT,
    row_value,
    sales_rows,
    warehouse_rows,
)

# Крок 3+ "Дії": структурні деталі друку (ширина колонки в мм, вирівнювання,
# одиниця для підсумку) — це НЕ те, що має редагувати непрограміст через GUI,
# тож лишаються тут, у коді. А ось ЧИ показується колонка взагалі і як вона
# називається (label) — тепер бере з живих bot_operation_fields (stock_report/
# sales_report), тому редагування в "Дії" реально змінює справжній звіт.
_STOCK_REPORT_COLUMN_META = {
    "product": {"width_mm": 52, "align": "left"},
    "breed": {"width_mm": 32, "align": "left"},
    "condition": {"width_mm": 28, "align": "left"},
    "size": {"width_mm": 42, "align": "left"},
    "quantity": {"width_mm": 28, "align": "right", "total": True, "unit": "шт"},
    "volume": {"width_mm": 30, "align": "right", "total": True, "unit": "м3"},
    "area": {"width_mm": 30, "align": "right", "total": True, "unit": "м2"},
    "linear": {"width_mm": 28, "align": "right", "total": True, "unit": "мп"},
    "note": {"width_mm": 60, "align": "left", "note": True},
}

# Спільний словник для sales_report, antiseptic_report І sales_by_client_report
# (усі ключі нижче - загальні поняття "дата"/"клієнт"/"сума" тощо, жодного
# sales-специфічного змісту не було й раніше - перейменовано з
# _SALES_REPORT_COLUMN_META, коли з'явився другий реальний споживач).
_REPORT_COLUMN_META = {
    "date": {"width_mm": 24, "align": "center"},
    "client": {"width_mm": 55, "align": "left"},
    "address": {"width_mm": 45, "align": "left", "in_message": False},
    "position": {"width_mm": 60, "align": "left"},
    # Окрема, не-відмінювана позначка (як і "шт"/"м3"/"мп") - навмисно НЕ "шт",
    # щоб не плутати "кількість продажів" із сусіднім "quantity" (теж "шт")
    # у компактному форматі повідомлення, де немає заголовків колонок.
    "count": {"width_mm": 26, "align": "right", "total": True, "unit": "прод."},
    "quantity": {"width_mm": 26, "align": "right", "total": True, "unit": "шт"},
    "volume": {"width_mm": 26, "align": "right", "total": True, "unit": "м3"},
    "area": {"width_mm": 28, "align": "right", "total": True, "unit": "м2"},
    "linear": {"width_mm": 26, "align": "right", "total": True, "unit": "мп"},
    "total_amount": {"width_mm": 28, "align": "right", "total": True, "unit": "MDL"},
    "payment_method": {"width_mm": 40, "align": "left", "in_message": False},
    "manager": {"width_mm": 35, "align": "left", "in_message": False},
}

# Скільки рядків залишку за фільтром під час продажу ще можна показати
# одним списком для вибору позиції в чаті — понад це число проще уточнити
# фільтр (продукт/породу/тип/розмір), ніж гортати довгий список. Не
# плутати з message_row_limit (_stock_report_spec) — той керує показом
# готового ЗВІТУ "Остаток" (з вибором формату PDF/Excel/повідомлення), тут
# же йдеться про проміжний крок вибору позиції всередині діалогу продажу,
# де іншого формату взагалі немає.
_STOCK_FILTER_INLINE_LIMIT = 20


class ReportsDialogMixin:

    def _stock_filter_keyboard(self):
        return {
            "keyboard": [
                [{"text": "Показать"}, {"text": "Очистить фильтр"}],
                [{"text": "Отмена"}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }

    def _stock_filter_replace_keyboard(self):
        return {
            "keyboard": [
                [{"text": "Заменить"}, {"text": "Оставить как есть"}],
                [{"text": "Отмена"}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }

    def _warehouse_placeholder_command(self, text):
        normalized = _normalize_phrase(text)
        if normalized in {"списание", "списание в разработке", "списать"}:
            return "Списание"
        if normalized in {"фильтры", "фильтры в разработке", "фильтр", "остатки по фильтрам"}:
            return "Фильтры"
        return None

    # --- Фільтри залишків та історія операцій приходу ---
    def _parse_stock_filters(self, text, store):
        filters = {}
        try:
            _, columns, rows = warehouse_rows(store)
        except sqlite3.Error:
            return filters

        size_match = re.search(
            r"(?P<thickness>\d+(?:[.,]\d+)?)\s*[xххХX*]\s*"
            r"(?P<width>\d+(?:[.,]\d+)?)\s*[xххХX*]\s*"
            r"(?P<length>\d+(?:[.,]\d+)?)",
            text,
            flags=re.IGNORECASE,
        )
        if size_match:
            filters["thickness"] = self._parse_number_with_thousands_separator(size_match.group("thickness"))
            filters["width"] = self._parse_number_with_thousands_separator(size_match.group("width"))
            filters["length"] = self._parse_number_with_thousands_separator(size_match.group("length"))
        elif re.fullmatch(r"[\d\s.,x×хХ*/\-]+", text.strip()):
            # Довіряємо голим числам як розмірам, тільки якщо в повідомленні
            # взагалі немає інших слів — інакше випадкові числа (кількість,
            # ціна тощо) в реченні можуть хибно потрапити в товщину/ширину.
            plain_numbers = [
                self._parse_number_with_thousands_separator(match.group(0))
                for match in re.finditer(r"(?<![\d.,])\d+(?:[.,]\d+)?(?![\d.,])", text)
            ]
            if len(plain_numbers) >= 2:
                filters["thickness"] = plain_numbers[0]
                filters["width"] = plain_numbers[1]
                if len(plain_numbers) >= 3:
                    filters["length"] = plain_numbers[2]

        dimension_patterns = {
            "thickness": r"(?:толщина|толщиной|толщину)\s*[:=\-]?\s*(\d+(?:[.,]\d+)?)",
            "width": r"(?:ширина|шириной|ширину)\s*[:=\-]?\s*(\d+(?:[.,]\d+)?)",
            "length": r"(?:длина|длинна|длиной|длинной|длину|длинну)\s*[:=\-]?\s*(\d+(?:[.,]\d+)?)",
        }
        for field, pattern in dimension_patterns.items():
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                filters[field] = self._parse_number_with_thousands_separator(match.group(1))

        for field in ("product", "breed", "condition"):
            column_index = columns.get(field)
            if column_index is None:
                continue
            for value in self._existing_values(rows, column_index, False):
                if self._stock_text_value_in_request(value, text):
                    filters[field] = value
                    break
        if "condition" not in filters:
            for value in self._existing_product_type_values(rows, columns.get("product")):
                if self._stock_text_value_in_request(value, text):
                    filters["condition"] = value
                    break

        return filters

    def _stock_text_value_in_request(self, value, text):
        normalized_value = _normalize_phrase(value)
        normalized_text = f" {_normalize_phrase(text)} "
        if normalized_value and f" {normalized_value} " in normalized_text:
            return True

        value_code = _normalize_keyboard_code(value).replace(" ", "")
        text_code = f" {_normalize_keyboard_code(text)} "
        return bool(value_code) and f" {value_code} " in text_code

    # Реальний ризик (аудит коду, 2026-08-14): "прошлая неделя"/"прошлый
    # месяц" (КАЛЕНДАРНІ межі, пн-нд минулого тижня) розпізнавались лише в
    # _sales_period_from_text (звіт продажів) — тут (звіт залишків/приходу,
    # природномовний парсер _parse_online_ai_request) фраза "прошлая
    # неделя" мовчки збігалась із загальною підстрокою "недел" і тихо
    # трактувалась як РУХОМЕ вікно "останні 7 днів" — та сама фраза давала
    # РІЗНИЙ діапазон дат залежно від того, який саме звіт питаєш. Сама
    # календарна математика тепер в 2 спільних статичних хелперах нижче —
    # ОБИДВІ функції рахують межі однаково, лишаючи собі лише розпізнавання
    # фрази (навмисно РІЗне: тут природномовне речення, де період — лише
    # ЧАСТИНА фрази ("сколько пришло ЗА ПРОШЛУЮ НЕДЕЛЮ доска"), тож
    # substring-перевірка; _sales_period_from_text чекає короткий термінальний
    # ответ на власне питання "за який період?", тож точний збіг усієї фрази).
    @staticmethod
    def _previous_calendar_week_bounds(today):
        this_week_monday = date.fromordinal(today.toordinal() - today.weekday())
        start = date.fromordinal(this_week_monday.toordinal() - 7)
        end = date.fromordinal(start.toordinal() + 6)
        return start, end

    @staticmethod
    def _previous_calendar_month_bounds(today):
        this_month_first = today.replace(day=1)
        end = date.fromordinal(this_month_first.toordinal() - 1)
        start = end.replace(day=1)
        return start, end

    def _parse_stock_period(self, text):
        normalized = _normalize_phrase(text)
        today = date.today()
        match = re.search(r"\b(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?\b", text)
        if match:
            day = int(match.group(1))
            month = int(match.group(2))
            year_raw = match.group(3)
            year = int(year_raw) if year_raw else today.year
            if year < 100:
                year += 2000
            try:
                selected = date(year, month, day)
                return {"from": selected, "to": selected, "label": selected.strftime("%d.%m.%Y")}
            except ValueError:
                pass

        if "сегодня" in normalized or "сьогодні" in normalized or "за день" in normalized:
            return {"from": today, "to": today, "label": "сегодня"}
        if "вчера" in normalized or "вчора" in normalized:
            selected = date.fromordinal(today.toordinal() - 1)
            return {"from": selected, "to": selected, "label": "вчера"}
        # "прошл"+"недел"/"тижд" (календарний тиждень) ПЕРЕД загальним
        # "недел"/"тижд" (рухоме вікно) нижче — той самий порядок пріоритету,
        # що й у _sales_period_from_text.
        if "прошл" in normalized and ("недел" in normalized or "тижд" in normalized):
            start, end = self._previous_calendar_week_bounds(today)
            return {
                "from": start,
                "to": end,
                "label": f"прошлая неделя ({start.strftime('%d.%m')}–{end.strftime('%d.%m.%Y')})",
            }
        if "недел" in normalized or "тижд" in normalized:
            start = date.fromordinal(today.toordinal() - 6)
            return {"from": start, "to": today, "label": "последние 7 дней"}
        if "прошл" in normalized and ("месяц" in normalized or "місяц" in normalized):
            start, end = self._previous_calendar_month_bounds(today)
            return {
                "from": start,
                "to": end,
                "label": f"прошлый месяц ({start.strftime('%m.%Y')})",
            }
        if "месяц" in normalized or "місяц" in normalized:
            start = date.fromordinal(today.toordinal() - 29)
            return {"from": start, "to": today, "label": "последние 30 дней"}
        return None

    def _stock_income_history_reply(self, store, request):
        period = request.get("period")
        if not period:
            return (
                "Уточните период прихода: сегодня, вчера, неделя, месяц или конкретная дата.\n"
                "Например: Сколько пришло сегодня KD 25x150x6000."
            )

        movements = store.list_stock_movements(
            movement_type="income",
            start_date=period.get("from"),
            end_date=period.get("to"),
            limit=1000,
        )
        filters = request.get("filters") or {}
        movements = [movement for movement in movements if self._stock_movement_matches_filters(movement, filters)]
        if not movements:
            return (
                f"За период {period['label']} приходов не найдено"
                f"{self._stock_filter_text(filters)}.\n"
                "История прихода учитывает только операции, записанные после добавления журнала движений."
            )

        total_quantity = sum(_number_value(movement.get("quantity")) for movement in movements)
        # Рухи можуть бути змішані за виміром (звичайна дошка м3 поруч із
        # мп-розміром) — раніше тут завжди сумувалось лише "volume", тож
        # Вагонка (площа) чи мп-рухи мовчки показувались як "0 м3"
        # (реальний, давній баг, знайдений при роботі над мп).
        totals_by_kind = {"volume": 0.0, "area": 0.0, "linear": 0.0}
        for movement in movements:
            kind = item_measure_kind(movement)
            if kind is not None:
                totals_by_kind[kind] += _number_value(movement.get(kind))
        total_parts = [f"{_display_bot_number(total_quantity)} шт"]
        for kind in ("volume", "area", "linear"):
            if totals_by_kind[kind]:
                total_parts.append(f"{_display_bot_number(totals_by_kind[kind])} {ITEM_MEASURE_UNIT[kind]}")
        lines = [
            f"Приход за период {period['label']}{self._stock_filter_text(filters)}:",
            f"Позиций: {len(movements)}",
            f"Итого: {' / '.join(total_parts)}",
            "",
        ]
        for index, movement in enumerate(movements[:12], start=1):
            size = (
                f"{_display_bot_number(movement.get('thickness'))}x"
                f"{_display_bot_number(movement.get('width'))}x"
                f"{_display_bot_number(movement.get('length'))}"
            )
            measure_kind = item_measure_kind(movement)
            if measure_kind is None:
                lines.append(
                    f"{index}. {movement.get('product') or ''} | {movement.get('breed') or ''} | "
                    f"{movement.get('condition') or ''} | {size}: "
                    f"{_display_bot_number(movement.get('quantity'))} шт"
                )
            else:
                lines.append(
                    f"{index}. {movement.get('product') or ''} | {movement.get('breed') or ''} | "
                    f"{movement.get('condition') or ''} | {size}: "
                    f"{_display_bot_number(movement.get('quantity'))} шт / "
                    f"{_display_bot_number(movement.get(measure_kind))} {ITEM_MEASURE_UNIT[measure_kind]}"
                )
        if len(movements) > 12:
            lines.append(f"...и еще {len(movements) - 12} позиций.")
        return "\n".join(lines)

    def _stock_movement_matches_filters(self, movement, filters):
        for field, expected in (filters or {}).items():
            if field in {"product", "breed", "condition"} and not self._text_equal(movement.get(field), expected):
                return False
            if field in {"thickness", "width", "length"} and not self._number_equal(movement.get(field), expected):
                return False
        return True

    # Автономний перегляд складу за фільтром (кнопка "Фильтры" у меню Склад,
    # без жодного продажу в процесі) — перевикористовує ті самі
    # _stock_filter_*/_parse_stock_filters методи, що й гілка "продаж не
    # знайдено -> перевірити залишок за фільтром" (payload["browse_mode"]
    # відрізняє два сценарії лише там, де фінальна дія й текст скасування
    # відрізняються: "Показать" тут веде у report-builder (reports.py),
    # а не назад у продовження продажу).
    def _start_stock_browse_filters(self, store, context):
        denied = self._require_permission(store, context, perm.WAREHOUSE_VIEW)
        if denied:
            return denied
        payload = {"stock_filter": {}, "browse_mode": True}
        store.save_pending_operation(
            context["chat_id"],
            context["user_id"],
            "stock_browse",
            "stock_filter_collect",
            payload,
        )
        return self._stock_filter_prompt(payload)

    def _continue_stock_browse(self, text, store, context, pending):
        payload = pending["payload"]
        status = pending["status"]
        answer = text.strip()
        if status == "choose_stock_category":
            return self._handle_stock_category_choice(answer, store, context, payload)
        if status == "stock_filter_collect":
            return self._handle_stock_filter_collect(answer, store, context, payload)
        if status == "stock_filter_confirm_replace":
            return self._handle_stock_filter_replace(answer, store, context, payload)
        store.delete_pending_operation(context["chat_id"], context["user_id"])
        return self._with_main_menu("Предыдущая операция сброшена. Отправьте запрос заново.", store)

    def _handle_stock_category_choice(self, answer, store, context, payload):
        normalized = _normalize_phrase(answer)
        if normalized in {"фильтры", "фильтр"}:
            return self._start_stock_browse_filters(store, context)
        if normalized in {"all products", "все товары", "все продукты", "весь склад"}:
            store.delete_pending_operation(context["chat_id"], context["user_id"])
            return self._stock_balance_reply(store, context, filters=None, source="command")
        category_product = self._stock_category_filter(answer)
        if category_product is None:
            return {
                "type": "message",
                "text": "Не понял категорию. Выберите одну из кнопок ниже.",
                "reply_markup": self._stock_data_keyboard(),
            }
        store.delete_pending_operation(context["chat_id"], context["user_id"])
        return self._stock_balance_reply(store, context, filters={"product": category_product}, source="command")

    # ПРОДАЖИ (Фаза B, п.3) — звіт за період, за зразком СКЛАД (п.2): період
    # -> категорія товару (ті самі кнопки, що й у СКЛАД) -> формат
    # (Сообщением/PDF/Excel через reports.py, той самий вибір, що й "Остаток").
    def _start_sales_report_reply(self, store, context):
        denied = self._require_permission(store, context, perm.SALE_VIEW)
        if denied:
            return denied
        return self._sales_period_prompt_reply(store, context, {})

    # Спільний прев'ю-крок "За какой период" — використовується і при
    # першому вході (ДАННЫЕ -> ПРОДАЖИ), і при поверненні "Назад" з кроку
    # категорії (_handle_back_request), щоб не дублювати збереження
    # pending-операції й текст запрошення у двох місцях.
    def _sales_period_prompt_reply(self, store, context, payload):
        store.save_pending_operation(
            context["chat_id"], context["user_id"], "sales_report", "choose_period", payload
        )
        return {
            "type": "message",
            "text": store.get_message_template("start_sales_report", BOT_MESSAGE_DEFAULTS["start_sales_report"]),
            "reply_markup": self._sales_period_keyboard(),
        }

    def _sales_period_keyboard(self):
        return {
            "keyboard": [
                [{"text": "Сегодня"}, {"text": "Вчера"}],
                [{"text": "Неделя"}, {"text": "Месяц"}],
                [{"text": "Прошлая неделя"}, {"text": "Прошлый месяц"}],
                [{"text": "Весь период"}],
                [{"text": "Назад"}, {"text": "Главное меню"}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }

    # from/to зберігаються як ISO-рядки ("YYYY-MM-DD"), а не date-об'єкти —
    # payload pending-операції серіалізується голим json.dumps (на відміну
    # від значень рядків листів, які проходять через _serialize_value і
    # вміють date/datetime). date.fromisoformat(...) розпаковує назад там,
    # де потрібне порівняння/форматування.
    def _sales_period_from_text(self, text):
        normalized = _normalize_phrase(text)
        today = date.today()

        # ТЗ gap-аналіз: довільний діапазон дат ("01.03.2026-15.03.2026" чи
        # "01.03.2026 по 15.03.2026") — перевіряється ПЕРШИМ, до одно-дато вого
        # регексу нижче, інакше той розпізнав би лише ПЕРШУ дату діапазону.
        # Рік за замовчуванням (коли не вказаний) — окремо для КОЖНОЇ половини
        # діапазону (та сама логіка, що й одно-дато вий регекс).
        range_match = re.search(
            r"\b(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?\b\s*(?:-|—|по|до)\s*"
            r"\b(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?\b",
            text,
        )
        if range_match:
            day1, month1, year1_raw, day2, month2, year2_raw = range_match.groups()

            def _resolve_year(year_raw):
                if not year_raw:
                    return today.year
                year = int(year_raw)
                return year + 2000 if year < 100 else year

            try:
                from_date = date(_resolve_year(year1_raw), int(month1), int(day1))
                to_date = date(_resolve_year(year2_raw), int(month2), int(day2))
            except ValueError:
                from_date = to_date = None
            if from_date and to_date:
                # Обидві половини — валідні календарні дати, це точно
                # СПРОБА задати діапазон: якщо він перевернутий (від > до),
                # явно повертаємо None ЗАРАЗ ЖЕ — не даємо провалитись до
                # одно-дато вого регексу нижче, який інакше тихо прийняв би
                # лише ПЕРШУ дату як єдиний день (хибний, не "Не понял").
                if from_date > to_date:
                    return None
                return {
                    "from": from_date.isoformat(),
                    "to": to_date.isoformat(),
                    "label": f"{from_date.strftime('%d.%m.%Y')} — {to_date.strftime('%d.%m.%Y')}",
                }

        # Аудит коду: на відміну від _parse_stock_period (звіт складу), тут
        # раніше не було жодного розпізнавання конкретної дати — лише
        # фіксовані відносні слова. Той самий регекс, адаптований під
        # власну "isoformat-рядок" конвенцію цієї функції (перевірено:
        # _sales_report_rows далі робить date.fromisoformat(period["from"])).
        match = re.search(r"\b(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?\b", text)
        if match:
            day = int(match.group(1))
            month = int(match.group(2))
            year_raw = match.group(3)
            year = int(year_raw) if year_raw else today.year
            if year < 100:
                year += 2000
            try:
                selected = date(year, month, day)
                return {
                    "from": selected.isoformat(),
                    "to": selected.isoformat(),
                    "label": selected.strftime("%d.%m.%Y"),
                }
            except ValueError:
                pass
        if normalized in {"сегодня", "сьогодні"}:
            return {"from": today.isoformat(), "to": today.isoformat(), "label": "сегодня"}
        if normalized in {"вчера", "вчора"}:
            selected = date.fromordinal(today.toordinal() - 1)
            return {"from": selected.isoformat(), "to": selected.isoformat(), "label": "вчера"}
        # ТЗ gap-аналіз: КАЛЕНДАРНИЙ тиждень (понеділок-неділя) ПЕРЕД поточним
        # — на відміну від "неделя" нижче, яка лишається рухомим вікном
        # "останні 7 днів від сьогодні" (не чіпаємо — інші сценарії/тести вже
        # покладаються саме на цю поведінку).
        if normalized in {"прошлая неделя", "прошлый тиждень", "минувшая неделя", "прошедшая неделя"}:
            start, end = self._previous_calendar_week_bounds(today)
            return {
                "from": start.isoformat(),
                "to": end.isoformat(),
                "label": f"прошлая неделя ({start.strftime('%d.%m')}–{end.strftime('%d.%m.%Y')})",
            }
        if normalized in {"неделя", "тиждень"}:
            start = date.fromordinal(today.toordinal() - 6)
            return {"from": start.isoformat(), "to": today.isoformat(), "label": "последние 7 дней"}
        # Той самий принцип — КАЛЕНДАРНИЙ місяць ПЕРЕД поточним, "месяц" нижче
        # лишається рухомим вікном "останні 30 днів".
        if normalized in {"прошлый месяц", "минувший месяц", "прошедший месяц"}:
            start, end = self._previous_calendar_month_bounds(today)
            return {
                "from": start.isoformat(),
                "to": end.isoformat(),
                "label": f"прошлый месяц ({start.strftime('%m.%Y')})",
            }
        if normalized in {"месяц", "місяць", "місяц"}:
            start = date.fromordinal(today.toordinal() - 29)
            return {"from": start.isoformat(), "to": today.isoformat(), "label": "последние 30 дней"}
        if normalized in {"весь период", "весь час", "все время", "всё время"}:
            return {"from": None, "to": None, "label": "весь период"}
        return None

    # Ті самі категорії, що й СКЛАД (п.2), без Фильтры — для продажів
    # достатньо категорії товару, розширений фільтр поки не потрібен.
    def _sales_category_keyboard(self):
        return {
            "keyboard": [
                [{"text": "ДОСКА AD"}, {"text": "ДОСКА KD"}],
                [{"text": "ОСБ"}, {"text": "ВАГОНКА"}],
                [{"text": "ВСЕ ТОВАРЫ"}],
                [{"text": "Назад"}, {"text": "Главное меню"}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }

    # Той самий принцип, що й _sales_period_prompt_reply вище — спільний
    # крок "Выберите категорию товара", який використовується і після
    # вибору періоду, і при поверненні "Назад" з формату, і на порожньому/
    # помилковому результаті звіту (замість викидання в головне меню).
    def _sales_category_prompt_reply(self, store, context, payload):
        store.save_pending_operation(
            context["chat_id"], context["user_id"], "sales_report", "choose_category", payload
        )
        period = payload.get("period") or {}
        return {
            "type": "message",
            "text": f"Продажи за {period.get('label', '')}. Выберите категорию товара:",
            "reply_markup": self._sales_category_keyboard(),
        }

    def _continue_sales_report(self, text, store, context, pending):
        payload = pending["payload"]
        status = pending["status"]
        answer = text.strip()

        if status == "choose_period":
            period = self._sales_period_from_text(answer)
            if period is None:
                return {
                    "type": "message",
                    "text": "Не понял период. Выберите один из вариантов ниже.",
                    "reply_markup": self._sales_period_keyboard(),
                }
            payload["period"] = period
            return self._sales_category_prompt_reply(store, context, payload)

        if status == "choose_category":
            normalized = _normalize_phrase(answer)
            product_filter = None
            if normalized not in {"все товары", "все продукты"}:
                product_filter = self._stock_category_filter(answer)
                if product_filter is None:
                    return {
                        "type": "message",
                        "text": "Не понял категорию. Выберите одну из кнопок ниже.",
                        "reply_markup": self._sales_category_keyboard(),
                    }
            payload["product_filter"] = product_filter
            return self._sales_report_reply(store, context, payload["period"], product_filter)

        if status == "choose_format":
            fmt = self._stock_report_format_choice(answer)
            if fmt is None:
                return {
                    "type": "message",
                    "text": "Не понял формат отчета. Выберите один из вариантов ниже.",
                    "reply_markup": self._stock_report_format_keyboard(),
                }
            # Так само, як у stock_report — pending НЕ видаляється, щоб можна
            # було одразу подивитись той самий звіт ще в іншому форматі.
            store.save_pending_operation(
                context["chat_id"], context["user_id"], "sales_report", "choose_format", payload
            )
            return self._sales_report_reply(
                store, context, payload["period"], payload.get("product_filter"), fmt=fmt
            )

        store.delete_pending_operation(context["chat_id"], context["user_id"])
        return self._with_main_menu("Предыдущая операция сброшена. Отправьте запрос заново.", store)

    def _sales_report_reply(self, store, context, period, product_filter=None, fmt=None):
        report_rows, error = self._sales_report_rows(store, period, product_filter)
        # Помилка/порожній результат більше НЕ викидають у головне меню —
        # повертають до вибору категорії того самого звіту (з тим самим
        # періодом), щоб одразу спробувати іншу категорію, а не починати
        # звіт заново з ДАННЫЕ.
        if error:
            return self._prepend_reply_text(
                error, self._sales_category_prompt_reply(store, context, {"period": period, "product_filter": product_filter})
            )
        if not report_rows:
            filter_text = f", товар: {product_filter}" if product_filter else ""
            return self._prepend_reply_text(
                f"Продажи за {period['label']}: записей нет{filter_text}.",
                self._sales_category_prompt_reply(store, context, {"period": period, "product_filter": product_filter}),
            )

        if fmt is None:
            store.save_pending_operation(
                context["chat_id"],
                context["user_id"],
                "sales_report",
                "choose_format",
                {"period": period, "product_filter": product_filter},
            )
            filter_text = f", товар: {product_filter}" if product_filter else ""
            return {
                "type": "message",
                "text": f"Продажи за {period['label']}: позиций {len(report_rows)}{filter_text}.\nВ каком виде показать?",
                "reply_markup": self._stock_report_format_keyboard(),
            }

        return self._render_sales_report(store, report_rows, period, product_filter, fmt)

    def _sale_row_date(self, value):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str) and value.strip():
            try:
                return datetime.fromisoformat(value.strip()).date()
            except ValueError:
                return None
        return None

    def _sales_report_rows(self, store, period, product_filter=None):
        headers, columns, rows = sales_rows(store)
        if not headers or not rows:
            return [], None
        required = {
            "date": "Дата",
            "client": "Клиент",
            "product": "Продукт",
            "quantity": "Количество, шт",
            "total_amount": "Сумма",
        }
        missing = [label for key, label in required.items() if columns.get(key) is None]
        if missing:
            return [], "Не могу сформировать отчет по продажам: не найдены колонки " + ", ".join(missing) + "."

        period_from = date.fromisoformat(period["from"]) if period.get("from") else None
        period_to = date.fromisoformat(period["to"]) if period.get("to") else None
        collected = []
        for _, row in rows:
            row_date = self._sale_row_date(row_value(row, columns["date"]))
            product = row_value(row, columns["product"])
            client_value = row_value(row, columns["client"])
            quantity_value = row_value(row, columns["quantity"])
            total_amount_value = row_value(row, columns["total_amount"])
            # Той самий реальний баг, що й в _antiseptic_report_rows: аркуш
            # може мати буквально порожні рядки, і для "весь период" (period_
            # from/to обидва None) жодна з перевірок нижче їх не відсіює.
            # Пропускаємо рядок лише коли УСІ 5 ідентифікуючих полів порожні
            # одночасно - справжня позиція з БУДЬ-яким реальним значенням хоч
            # в одному з них і далі показується.
            if (
                row_date is None
                and not _display_value(client_value)
                and not _display_value(product)
                and _number_value(quantity_value) == 0
                and _number_value(total_amount_value) == 0
            ):
                continue
            if period_from and (row_date is None or row_date < period_from):
                continue
            if period_to and (row_date is None or row_date > period_to):
                continue
            if product_filter and not self._text_equal(product, product_filter):
                continue

            breed = row_value(row, columns.get("breed"))
            thickness = row_value(row, columns.get("thickness"))
            width = row_value(row, columns.get("width"))
            length = row_value(row, columns.get("length"))
            size = "x".join(
                _display_bot_number(value)
                for value in (thickness, width, length)
                if value not in (None, "")
            )
            position = ", ".join(
                part for part in [_display_value(product), _display_value(breed), size] if part
            )

            is_area = self._is_area_based_product(product)
            is_quantity_only = self._is_quantity_only_product(product)
            is_linear = not is_area and not is_quantity_only and self._is_linear_meter_size(thickness, width)
            volume = row_value(row, columns.get("total_volume"))
            area = row_value(row, columns.get("total_area"))
            linear = row_value(row, columns.get("total_linear"))

            collected.append(
                {
                    "date_sort": row_date,
                    "date": row_date.strftime("%d.%m.%Y") if row_date else "",
                    "client": _display_value(client_value),
                    "address": _display_value(row_value(row, columns.get("address"))),
                    "position": position,
                    # Задача користувача (2026-08-14): "не має бути такого
                    # злиття - і тип, і ширина, і товщина, все в одному" -
                    # окремі product/breed/size (thickness/width/length теж,
                    # для фільтра-модала розміру) поруч зі старим "position"
                    # (той лишається лише для PDF/Excel-звіту, де компактний
                    # один стовпець "Товар" і далі доречний).
                    "product": _display_value(product),
                    "breed": _display_value(breed),
                    "size": size,
                    "thickness": thickness,
                    "width": width,
                    "length": length,
                    "quantity": quantity_value,
                    "volume": volume if not is_area and not is_linear and not is_quantity_only else None,
                    "area": area if is_area else None,
                    "linear": linear if is_linear else None,
                    "total_amount": row_value(row, columns["total_amount"]),
                    "payment_method": _display_value(row_value(row, columns.get("payment_method"))),
                    "manager": _display_value(row_value(row, columns.get("manager_final"))),
                }
            )

        collected.sort(key=lambda item: item["date_sort"] or date.min, reverse=True)
        for item in collected:
            item.pop("date_sort", None)
        return collected, None

    def _sales_report_spec(self, store, report_rows, period):
        columns = [{"key": "index", "label": "N", "width_mm": 10, "align": "center", "in_message": False}]
        operation = store.get_operation_by_code("sales_report")
        for field in store.list_operation_fields(operation[0]):
            field_key, label = field[2], field[3]
            meta = _REPORT_COLUMN_META.get(field_key)
            if meta is None:
                continue
            columns.append({"key": field_key, "label": label, **meta})
        rows = [dict(row, index=index) for index, row in enumerate(report_rows, start=1)]
        return {
            "title": f"Продажи за {period['label']}",
            "generated_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
            "columns": columns,
            "rows": rows,
            # Продажів за період може бути багато (десятки), і кожен рядок —
            # окрема операція (не дублікат тієї самої позиції складу), тож
            # групування як у "Остатке" тут не рятує — але повторювати
            # "Поле: значення" на кожному рядку так само нечитабельно
            # (реальна скарга користувача: "каша"). message_compact — рядок
            # без міток, значення підряд через " — ", кількість/обсяг/сума з
            # одиницею (шт/м3/MDL) через кому.
            "message_compact": True,
            # Продажів за період може бути багато (десятки) — на відміну від
            # СКЛАД (де рятує групування), тут просто обмежуємо повідомлення
            # першими N рядками (як і в _stock_income_history_reply), щоб не
            # виходити за ~2 екрани. PDF/Excel показують ВСІ рядки без обмеження.
            "message_row_limit": 12,
        }

    # Реальний ризик (аудит коду, 2026-08-14): раніше тут був окремий
    # sum()-по-report_rows, незалежний від reports.column_totals — того
    # самого, яким рахує підсумок В ФАЙЛІ (render_report_pdf/excel, і
    # футер самого повідомлення через _append_footer). spec["columns"]
    # (адміністративно налаштовувані через "Дії" в GUI) можуть НЕ включати
    # якусь із цих колонок — тоді підсумок у підписі до файлу показував би
    # число для колонки, якої в самому файлі/повідомленні взагалі немає, чи
    # розійшовся б із ним. column_totals(spec) — та сама функція, той самий
    # spec, тож підпис і файл ФІЗИЧНО не можуть розійтись.
    def _sales_report_totals(self, spec):
        raw_totals = reports.column_totals(spec)
        report_rows = spec["rows"]
        total_amount = raw_totals.get("total_amount", 0)
        return {
            "quantity": raw_totals.get("quantity", 0),
            "volume": raw_totals.get("volume", 0),
            "area": raw_totals.get("area", 0),
            "linear": raw_totals.get("linear", 0),
            "total_amount": total_amount,
            "average_amount": round(total_amount / len(report_rows), 2) if report_rows else 0,
            "distinct_clients": len({row.get("client") for row in report_rows if row.get("client")}),
        }

    def _render_sales_report(self, store, report_rows, period, product_filter, fmt):
        spec = self._sales_report_spec(store, report_rows, period)
        totals = self._sales_report_totals(spec)
        extra_lines = []
        if product_filter:
            extra_lines.append(f"Фильтр: товар={product_filter}")
        summary_line = (
            f"Средняя сумма продажи: {_display_bot_number(totals['average_amount'])} MDL, "
            f"клиентов: {totals['distinct_clients']}"
        )

        if fmt == reports.FORMAT_MESSAGE:
            text = reports.render_report_message(spec)
            if extra_lines:
                text += "\n" + "\n".join(extra_lines)
            text += "\n" + summary_line
            text += "\n\nПоказать в другом формате, Назад или Главное меню."
            return {
                "type": "message",
                "text": text,
                "reply_markup": self._stock_report_format_keyboard(),
            }

        try:
            REPORTS_DIR.mkdir(exist_ok=True)
            ext = "pdf" if fmt == reports.FORMAT_PDF else "xlsx"
            if period.get("from") and period.get("to"):
                date_from = date.fromisoformat(period["from"]).strftime("%d-%m-%Y")
                date_to = date.fromisoformat(period["to"]).strftime("%d-%m-%Y")
            else:
                date_from = datetime.now().strftime("%d-%m-%Y")
                date_to = None
            filename = reports.build_report_filename(
                "ПРОДАЖИ", product_filter, date_from, date_to, ext=ext
            )
            path = REPORTS_DIR / filename
            rendered = reports.render_report(spec, fmt, path=path)
        except PermissionError:
            # Аудит коду: раніше клієнт бачив сирий текст Python-винятку
            # (напр. англомовний OSError) прямо в чаті. Найімовірніша
            # практична причина — файл звіту вже відкритий іншою програмою.
            return (
                "Не удалось сформировать отчет: файл уже открыт в другой программе. "
                "Закройте его и попробуйте еще раз."
            )
        except Exception:
            return "Не удалось сформировать отчет. Попробуйте еще раз позже."

        totals_text = f"Итого: {_display_bot_number(totals['quantity'])} шт"
        if totals.get("volume"):
            totals_text += f" / {_display_bot_number(totals['volume'])} м3"
        if totals.get("area"):
            totals_text += f" / {_display_bot_number(totals['area'])} м2"
        if totals.get("linear"):
            totals_text += f" / {_display_bot_number(totals['linear'])} мп"
        totals_text += f" / Сумма: {_display_bot_number(totals['total_amount'])}"
        caption_lines = [
            spec["title"],
            f"Позиций: {len(report_rows)}",
            totals_text,
            summary_line,
        ]
        caption_lines.extend(extra_lines)
        caption_lines.append("Показать в другом формате, Назад или Главное меню.")
        rendered["caption"] = "\n".join(caption_lines)
        rendered["reply_markup"] = self._stock_report_format_keyboard()
        return rendered

    # ТЗ gap-аналіз, 4-й з 5 пунктів: "АНТИСЕПТИРОВАНИЕ" — окрема категорія
    # звіту, дзеркало sales_report (той самий 2-крокового формату вибір:
    # період -> у якому вигляді), але БЕЗ кроку категорії - антисептирование
    # не має товарного розрізу (порода/розмір), лише один вид послуги.
    def _start_antiseptic_report_reply(self, store, context):
        denied = self._require_permission(store, context, perm.SALE_VIEW)
        if denied:
            return denied
        return self._antiseptic_period_prompt_reply(store, context, {})

    # Двійник _sales_period_prompt_reply, НЕ параметризація останньої — та
    # жорстко зберігає pending як "sales_report". _sales_period_keyboard/
    # _sales_period_from_text нижче - повністю загальні (перевірено: жодної
    # sales-специфічної логіки всередині, лише розбір тексту дати/періоду),
    # тому реюзаються як є, без дублювання.
    def _antiseptic_period_prompt_reply(self, store, context, payload):
        store.save_pending_operation(
            context["chat_id"], context["user_id"], "antiseptic_report", "choose_period", payload
        )
        return {
            "type": "message",
            "text": store.get_message_template(
                "start_antiseptic_report", BOT_MESSAGE_DEFAULTS["start_antiseptic_report"]
            ),
            "reply_markup": self._sales_period_keyboard(),
        }

    def _continue_antiseptic_report(self, text, store, context, pending):
        payload = pending["payload"]
        status = pending["status"]
        answer = text.strip()

        if status == "choose_period":
            period = self._sales_period_from_text(answer)
            if period is None:
                return {
                    "type": "message",
                    "text": "Не понял период. Выберите один из вариантов ниже.",
                    "reply_markup": self._sales_period_keyboard(),
                }
            payload["period"] = period
            return self._antiseptic_report_reply(store, context, period)

        if status == "choose_format":
            fmt = self._stock_report_format_choice(answer)
            if fmt is None:
                return {
                    "type": "message",
                    "text": "Не понял формат отчета. Выберите один из вариантов ниже.",
                    "reply_markup": self._stock_report_format_keyboard(),
                }
            store.save_pending_operation(
                context["chat_id"], context["user_id"], "antiseptic_report", "choose_format", payload
            )
            return self._antiseptic_report_reply(store, context, payload["period"], fmt=fmt)

        store.delete_pending_operation(context["chat_id"], context["user_id"])
        return self._with_main_menu("Предыдущая операция сброшена. Отправьте запрос заново.", store)

    def _antiseptic_report_reply(self, store, context, period, fmt=None):
        report_rows, error = self._antiseptic_report_rows(store, period)
        # Нема категорії, тому "нема записів"/помилка повертають до вибору
        # ПЕРІОДУ (не категорії, як у sales_report — тут її просто нема).
        if error:
            return self._prepend_reply_text(
                error, self._antiseptic_period_prompt_reply(store, context, {"period": period})
            )
        if not report_rows:
            return self._prepend_reply_text(
                f"Антисептирование за {period['label']}: записей нет.",
                self._antiseptic_period_prompt_reply(store, context, {"period": period}),
            )

        if fmt is None:
            store.save_pending_operation(
                context["chat_id"],
                context["user_id"],
                "antiseptic_report",
                "choose_format",
                {"period": period},
            )
            return {
                "type": "message",
                "text": f"Антисептирование за {period['label']}: позиций {len(report_rows)}.\nВ каком виде показать?",
                "reply_markup": self._stock_report_format_keyboard(),
            }

        return self._render_antiseptic_report(store, report_rows, period, fmt)

    def _antiseptic_report_rows(self, store, period):
        headers, columns, rows = antiseptic_rows(store)
        if not headers or not rows:
            return [], None
        required = {
            "date": "Дата",
            "client": "Клиент",
            "total_amount": "Стоимость",
        }
        missing = [label for key, label in required.items() if columns.get(key) is None]
        if missing:
            return [], "Не могу сформировать отчет по антисептированию: не найдены колонки " + ", ".join(missing) + "."

        period_from = date.fromisoformat(period["from"]) if period.get("from") else None
        period_to = date.fromisoformat(period["to"]) if period.get("to") else None
        collected = []
        for _, row in rows:
            row_date = self._sale_row_date(row_value(row, columns["date"]))
            client_value = row_value(row, columns["client"])
            total_amount_value = row_value(row, columns["total_amount"])
            # Реальний баг з продакшн-бота: аркуш АНТИСЕПТИРОВАНИЕ має багато
            # буквально порожніх рядків (не пошкоджені операції - геть без
            # дати/клієнта/суми). Для конкретного періоду row_date is None і
            # так відсіювало б їх нижче, але для "весь период" (period_from/
            # to обидва None) жодна з двох перевірок нижче не спрацьовує -
            # порожні рядки проходили без фільтра. Пропускаємо рядок лише
            # коли УСІ 3 ідентифікуючі поля порожні одночасно - справжня
            # позиція з БУДЬ-яким реальним значенням хоч в одному з них і
            # далі показується.
            if row_date is None and not _display_value(client_value) and _number_value(total_amount_value) == 0:
                continue
            if period_from and (row_date is None or row_date < period_from):
                continue
            if period_to and (row_date is None or row_date > period_to):
                continue

            collected.append(
                {
                    "date_sort": row_date,
                    "date": row_date.strftime("%d.%m.%Y") if row_date else "",
                    "client": _display_value(client_value),
                    "address": _display_value(row_value(row, columns.get("address"))),
                    "volume": row_value(row, columns.get("volume")),
                    "total_amount": total_amount_value,
                    "payment_method": _display_value(row_value(row, columns.get("payment_method"))),
                    "manager": _display_value(row_value(row, columns.get("manager"))),
                }
            )

        collected.sort(key=lambda item: item["date_sort"] or date.min, reverse=True)
        for item in collected:
            item.pop("date_sort", None)
        return collected, None

    # Той самий принцип, що й _sales_report_totals вище — через
    # reports.column_totals(spec), не власний sum().
    def _antiseptic_report_totals(self, spec):
        raw_totals = reports.column_totals(spec)
        return {
            "volume": raw_totals.get("volume", 0),
            "total_amount": raw_totals.get("total_amount", 0),
        }

    def _antiseptic_report_spec(self, store, report_rows, period):
        columns = [{"key": "index", "label": "N", "width_mm": 10, "align": "center", "in_message": False}]
        operation = store.get_operation_by_code("antiseptic_report")
        for field in store.list_operation_fields(operation[0]):
            field_key, label = field[2], field[3]
            meta = _REPORT_COLUMN_META.get(field_key)
            if meta is None:
                continue
            columns.append({"key": field_key, "label": label, **meta})
        rows = [dict(row, index=index) for index, row in enumerate(report_rows, start=1)]
        return {
            "title": f"Антисептирование за {period['label']}",
            "generated_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
            "columns": columns,
            "rows": rows,
            "message_compact": True,
            "message_row_limit": 12,
        }

    def _render_antiseptic_report(self, store, report_rows, period, fmt):
        spec = self._antiseptic_report_spec(store, report_rows, period)
        totals = self._antiseptic_report_totals(spec)

        if fmt == reports.FORMAT_MESSAGE:
            text = reports.render_report_message(spec)
            text += "\n\nПоказать в другом формате, Назад или Главное меню."
            return {
                "type": "message",
                "text": text,
                "reply_markup": self._stock_report_format_keyboard(),
            }

        try:
            REPORTS_DIR.mkdir(exist_ok=True)
            ext = "pdf" if fmt == reports.FORMAT_PDF else "xlsx"
            if period.get("from") and period.get("to"):
                date_from = date.fromisoformat(period["from"]).strftime("%d-%m-%Y")
                date_to = date.fromisoformat(period["to"]).strftime("%d-%m-%Y")
            else:
                date_from = datetime.now().strftime("%d-%m-%Y")
                date_to = None
            filename = reports.build_report_filename(
                "АНТИСЕПТИРОВАНИЕ", None, date_from, date_to, ext=ext
            )
            path = REPORTS_DIR / filename
            rendered = reports.render_report(spec, fmt, path=path)
        except PermissionError:
            return (
                "Не удалось сформировать отчет: файл уже открыт в другой программе. "
                "Закройте его и попробуйте еще раз."
            )
        except Exception:
            return "Не удалось сформировать отчет. Попробуйте еще раз позже."

        totals_text = f"Итого: {_display_bot_number(totals['volume'])} м3"
        totals_text += f" / Сумма: {_display_bot_number(totals['total_amount'])}"
        caption_lines = [
            spec["title"],
            f"Позиций: {len(report_rows)}",
            totals_text,
        ]
        caption_lines.append("Показать в другом формате, Назад или Главное меню.")
        rendered["caption"] = "\n".join(caption_lines)
        rendered["reply_markup"] = self._stock_report_format_keyboard()
        return rendered

    # ТЗ gap-аналіз, 5-й (останній) з 5 пунктів: "Продажи по клиентам" —
    # ЗГРУПОВАНИЙ підсумок по всіх клієнтах за період (не дрилл-даун по
    # одному), той самий спрощений період->формат потік, що й
    # antiseptic_report (нема категорії - беремо всі товари одразу).
    def _start_sales_by_client_report_reply(self, store, context):
        denied = self._require_permission(store, context, perm.SALE_VIEW)
        if denied:
            return denied
        return self._sales_by_client_period_prompt_reply(store, context, {})

    def _sales_by_client_period_prompt_reply(self, store, context, payload):
        store.save_pending_operation(
            context["chat_id"], context["user_id"], "sales_by_client_report", "choose_period", payload
        )
        return {
            "type": "message",
            "text": store.get_message_template(
                "start_sales_by_client_report", BOT_MESSAGE_DEFAULTS["start_sales_by_client_report"]
            ),
            "reply_markup": self._sales_period_keyboard(),
        }

    def _continue_sales_by_client_report(self, text, store, context, pending):
        payload = pending["payload"]
        status = pending["status"]
        answer = text.strip()

        if status == "choose_period":
            period = self._sales_period_from_text(answer)
            if period is None:
                return {
                    "type": "message",
                    "text": "Не понял период. Выберите один из вариантов ниже.",
                    "reply_markup": self._sales_period_keyboard(),
                }
            payload["period"] = period
            return self._sales_by_client_report_reply(store, context, period)

        if status == "choose_format":
            fmt = self._stock_report_format_choice(answer)
            if fmt is None:
                return {
                    "type": "message",
                    "text": "Не понял формат отчета. Выберите один из вариантов ниже.",
                    "reply_markup": self._stock_report_format_keyboard(),
                }
            store.save_pending_operation(
                context["chat_id"], context["user_id"], "sales_by_client_report", "choose_format", payload
            )
            return self._sales_by_client_report_reply(store, context, payload["period"], fmt=fmt)

        store.delete_pending_operation(context["chat_id"], context["user_id"])
        return self._with_main_menu("Предыдущая операция сброшена. Отправьте запрос заново.", store)

    def _sales_by_client_report_reply(self, store, context, period, fmt=None):
        report_rows, error = self._sales_by_client_rows(store, period)
        if error:
            return self._prepend_reply_text(
                error, self._sales_by_client_period_prompt_reply(store, context, {"period": period})
            )
        if not report_rows:
            return self._prepend_reply_text(
                f"Продажи по клиентам за {period['label']}: записей нет.",
                self._sales_by_client_period_prompt_reply(store, context, {"period": period}),
            )

        if fmt is None:
            store.save_pending_operation(
                context["chat_id"],
                context["user_id"],
                "sales_by_client_report",
                "choose_format",
                {"period": period},
            )
            return {
                "type": "message",
                "text": f"Продажи по клиентам за {period['label']}: клиентов {len(report_rows)}.\nВ каком виде показать?",
                "reply_markup": self._stock_report_format_keyboard(),
            }

        return self._render_sales_by_client_report(store, report_rows, period, fmt)

    # Перевикористовує вже наявний _sales_report_rows (той самий рушій, що
    # вже читає/парсить ПРОДАЖА МАТЕРИАЛА) - нуль дублювання читання листа.
    # Групує вже готові рядки за клієнтом і сумує; рядки без клієнта (якщо
    # такі є) пропускаються - групувати по порожньому ключу нема сенсу.
    def _sales_by_client_rows(self, store, period):
        report_rows, error = self._sales_report_rows(store, period, None)
        if error or not report_rows:
            return [], error
        grouped = {}
        for row in report_rows:
            client = row.get("client")
            if not client:
                continue
            bucket = grouped.setdefault(
                client,
                {
                    "client": client,
                    "count": 0,
                    "quantity": 0,
                    "volume": None,
                    "area": None,
                    "linear": None,
                    "total_amount": 0,
                },
            )
            bucket["count"] += 1
            for key in ("quantity", "total_amount"):
                bucket[key] += _number_value(row.get(key))
            # Свіжий пере-аудит (New-Notable #1): None (вимір не застосовний
            # до цього рядка - напр. площа для товару, що рахується в м3)
            # раніше додавався як 0 - клієнт, чиї покупки ЖОДНОГО разу не
            # зустрічали цей вимір, отримував фіктивне "0 м3" замість
            # порожньої клітинки/пропуску (reports.py різнить None і 0
            # навмисно, рядки 184/343-347). Тепер лишається None, поки
            # ЖОДЕН рядок клієнта не мав реального значення.
            for key in ("volume", "area", "linear"):
                value = row.get(key)
                if value is None:
                    continue
                bucket[key] = (bucket[key] or 0) + _number_value(value)
        return sorted(grouped.values(), key=lambda item: item["total_amount"], reverse=True), None

    # НЕ перевикористовує _sales_report_totals напряму - її average_amount/
    # distinct_clients тут втрачають сенс (report_rows вже й так по одному
    # рядку на клієнта; distinct_clients лише дублював би len(report_rows)).
    # Той самий принцип, що й _sales_report_totals — через
    # reports.column_totals(spec), не власний sum().
    def _sales_by_client_report_totals(self, spec):
        raw_totals = reports.column_totals(spec)
        return {
            "count": raw_totals.get("count", 0),
            "quantity": raw_totals.get("quantity", 0),
            "volume": raw_totals.get("volume", 0),
            "area": raw_totals.get("area", 0),
            "linear": raw_totals.get("linear", 0),
            "total_amount": raw_totals.get("total_amount", 0),
        }

    def _sales_by_client_report_spec(self, store, report_rows, period):
        columns = [{"key": "index", "label": "N", "width_mm": 10, "align": "center", "in_message": False}]
        operation = store.get_operation_by_code("sales_by_client_report")
        for field in store.list_operation_fields(operation[0]):
            field_key, label = field[2], field[3]
            meta = _REPORT_COLUMN_META.get(field_key)
            if meta is None:
                continue
            columns.append({"key": field_key, "label": label, **meta})
        rows = [dict(row, index=index) for index, row in enumerate(report_rows, start=1)]
        return {
            "title": f"Продажи по клиентам за {period['label']}",
            "generated_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
            "columns": columns,
            "rows": rows,
            "message_compact": True,
            # Клієнтів природньо менше, ніж транзакцій - трохи вищий ліміт,
            # ніж у sales_report/antiseptic_report (12), бо саме "побачити
            # більше клієнтів" тут головна цінність звіту.
            "message_row_limit": 20,
        }

    def _render_sales_by_client_report(self, store, report_rows, period, fmt):
        spec = self._sales_by_client_report_spec(store, report_rows, period)
        totals = self._sales_by_client_report_totals(spec)

        if fmt == reports.FORMAT_MESSAGE:
            text = reports.render_report_message(spec)
            text += "\n\nПоказать в другом формате, Назад или Главное меню."
            return {
                "type": "message",
                "text": text,
                "reply_markup": self._stock_report_format_keyboard(),
            }

        try:
            REPORTS_DIR.mkdir(exist_ok=True)
            ext = "pdf" if fmt == reports.FORMAT_PDF else "xlsx"
            if period.get("from") and period.get("to"):
                date_from = date.fromisoformat(period["from"]).strftime("%d-%m-%Y")
                date_to = date.fromisoformat(period["to"]).strftime("%d-%m-%Y")
            else:
                date_from = datetime.now().strftime("%d-%m-%Y")
                date_to = None
            filename = reports.build_report_filename(
                "КЛИЕНТЫ", None, date_from, date_to, ext=ext
            )
            path = REPORTS_DIR / filename
            rendered = reports.render_report(spec, fmt, path=path)
        except PermissionError:
            return (
                "Не удалось сформировать отчет: файл уже открыт в другой программе. "
                "Закройте его и попробуйте еще раз."
            )
        except Exception:
            return "Не удалось сформировать отчет. Попробуйте еще раз позже."

        totals_text = f"Итого: {_display_bot_number(totals['count'])} продаж"
        totals_text += f" / Сумма: {_display_bot_number(totals['total_amount'])}"
        caption_lines = [
            spec["title"],
            f"Клиентов: {len(report_rows)}",
            totals_text,
        ]
        caption_lines.append("Показать в другом формате, Назад или Главное меню.")
        rendered["caption"] = "\n".join(caption_lines)
        rendered["reply_markup"] = self._stock_report_format_keyboard()
        return rendered

    # Перший пункт "Високий пріоритет" із загальної перевірки сильних місць
    # (2026-08-03) — нарешті реальний споживач warehouse_items/
    # find_warehouse_items-інфраструктури (5 незалежних агентів визначили її
    # як готову, але ніде не підключену). Живий знімок складу (не історичні
    # дані, як sales/antiseptic) — періоду немає взагалі, одразу від кнопки
    # до вибору формату. Мірне повторення _stock_report_spec/
    # _STOCK_REPORT_COLUMN_META (складський, а не транзакційний звіт), а не
    # _REPORT_COLUMN_META (sales/antiseptic/sales_by_client).
    def _start_low_stock_report_reply(self, store, context):
        denied = self._require_permission(store, context, perm.WAREHOUSE_VIEW)
        if denied:
            return denied
        return self._low_stock_report_reply(store, context)

    def _low_stock_report_reply(self, store, context, fmt=None):
        threshold = SettingsStore(SETTINGS_PATH).get("low_stock_threshold")
        items = store.low_stock_warehouse_items(threshold)
        intro = store.get_message_template(
            "start_low_stock_report", BOT_MESSAGE_DEFAULTS["start_low_stock_report"]
        )

        if not items:
            store.delete_pending_operation(context["chat_id"], context["user_id"])
            return self._prepend_reply_text(
                f"{intro}\nПозиций с остатком {_display_bot_number(threshold)} шт и меньше не найдено.",
                self._enter_data_menu_node(store, context, re_entering=True),
            )

        if fmt is None:
            store.save_pending_operation(
                context["chat_id"], context["user_id"], "low_stock_report", "choose_format", {}
            )
            return {
                "type": "message",
                "text": (
                    f"{intro}\n\nПозиций с низким остатком: {len(items)} "
                    f"(порог: {_display_bot_number(threshold)} шт). В каком виде показать?"
                ),
                "reply_markup": self._stock_report_format_keyboard(),
            }

        return self._render_low_stock_report(store, items, threshold, fmt)

    def _continue_low_stock_report(self, text, store, context, pending):
        fmt = self._stock_report_format_choice(text.strip())
        if fmt is None:
            return {
                "type": "message",
                "text": "Не понял формат отчета. Выберите один из вариантов ниже.",
                "reply_markup": self._stock_report_format_keyboard(),
            }
        store.save_pending_operation(
            context["chat_id"], context["user_id"], "low_stock_report", "choose_format", {}
        )
        return self._low_stock_report_reply(store, context, fmt=fmt)

    def _render_low_stock_report(self, store, items, threshold, fmt):
        report_rows = []
        for item in items:
            size = "x".join(
                _display_bot_number(value)
                for value in (item.get("thickness"), item.get("width"), item.get("length"))
                if value not in (None, "")
            )
            report_rows.append(
                {
                    "product": _display_value(item.get("product")),
                    "breed": _display_value(item.get("breed")),
                    "condition": _display_value(item.get("condition")),
                    "size": size,
                    "quantity": item.get("balance_qty"),
                    "volume": item.get("balance_volume"),
                    "area": item.get("balance_area"),
                }
            )

        columns = [{"key": "index", "label": "N", "width_mm": 10, "align": "center", "in_message": False}]
        operation = store.get_operation_by_code("low_stock_report")
        for field in store.list_operation_fields(operation[0]):
            field_key, label = field[2], field[3]
            meta = _STOCK_REPORT_COLUMN_META.get(field_key)
            if meta is None:
                continue
            columns.append({"key": field_key, "label": label, **meta})
        rows = [dict(row, index=index) for index, row in enumerate(report_rows, start=1)]
        spec = {
            "title": "Позиции с низким остатком",
            "generated_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
            "columns": columns,
            "rows": rows,
            "message_compact": True,
            "message_row_limit": 20,
        }

        if fmt == reports.FORMAT_MESSAGE:
            text = reports.render_report_message(spec)
            text += "\n\nПоказать в другом формате, Назад или Главное меню."
            return {
                "type": "message",
                "text": text,
                "reply_markup": self._stock_report_format_keyboard(),
            }

        try:
            REPORTS_DIR.mkdir(exist_ok=True)
            ext = "pdf" if fmt == reports.FORMAT_PDF else "xlsx"
            date_from = datetime.now().strftime("%d-%m-%Y")
            filename = reports.build_report_filename("НИЗКИЙ ОСТАТОК", None, date_from, None, ext=ext)
            path = REPORTS_DIR / filename
            rendered = reports.render_report(spec, fmt, path=path)
        except PermissionError:
            return (
                "Не удалось сформировать отчет: файл уже открыт в другой программе. "
                "Закройте его и попробуйте еще раз."
            )
        except Exception:
            return "Не удалось сформировать отчет. Попробуйте еще раз позже."

        caption_lines = [
            spec["title"],
            f"Позиций: {len(report_rows)}",
            f"Порог: {_display_bot_number(threshold)} шт",
        ]
        caption_lines.append("Показать в другом формате, Назад или Главное меню.")
        rendered["caption"] = "\n".join(caption_lines)
        rendered["reply_markup"] = self._stock_report_format_keyboard()
        return rendered

    def _stock_filter_prompt(self, payload, added=None):
        lines = ["Просмотр остатка."]
        if added:
            lines.extend(["", "Добавлены фильтры:"])
            lines.extend(f"- {line}" for line in added)
        lines.extend(["", self._stock_filter_state_text(payload.get("stock_filter") or {})])
        lines.extend(["", "Напишите фильтр или нажмите Показать."])
        return {
            "type": "message",
            "text": "\n".join(lines),
            "reply_markup": self._stock_filter_keyboard(),
        }

    def _handle_stock_filter_collect(self, answer, store, context, payload):
        normalized = _normalize_phrase(answer)
        browse_mode = bool(payload.get("browse_mode"))
        operation_type = "stock_browse" if browse_mode else "stock_sale"
        if normalized in {"отмена", "отменить", "стоп", "скасувати", "відміна"}:
            store.delete_pending_operation(context["chat_id"], context["user_id"])
            return self._cancelled_reply("Просмотр остатка отменен." if browse_mode else "Операция продажи отменена.", store)

        if normalized in {"очистить фильтр", "очистить фильтры", "сбросить фильтр", "сбросить", "очистить"}:
            payload["stock_filter"] = {}
            store.save_pending_operation(
                context["chat_id"],
                context["user_id"],
                operation_type,
                "stock_filter_collect",
                payload,
            )
            return self._stock_filter_prompt(payload)

        if normalized in {"показать", "покажи", "показать остаток", "готово", "принять", "да"}:
            if browse_mode:
                store.delete_pending_operation(context["chat_id"], context["user_id"])
                return self._stock_balance_reply(store, context, filters=payload.get("stock_filter") or {}, source="command")
            return self._stock_filter_result_reply(store, context, payload)

        parsed = self._parse_stock_filters(answer, store)
        if not parsed:
            return {
                "type": "message",
                "text": (
                    "Не понял фильтр.\n"
                    "Например: KD, Сосна, длина 6000, ширина 50 или 25x50x6000."
                ),
                "reply_markup": self._stock_filter_keyboard(),
            }

        filters = dict(payload.get("stock_filter") or {})
        conflicts = []
        for key, value in parsed.items():
            if key in filters and not self._filter_value_equal(key, filters[key], value):
                conflicts.append((key, filters[key], value))

        if conflicts:
            payload["pending_filter_replace"] = {
                "filters": parsed,
                "conflicts": [key for key, _, _ in conflicts],
            }
            store.save_pending_operation(
                context["chat_id"],
                context["user_id"],
                operation_type,
                "stock_filter_confirm_replace",
                payload,
            )
            lines = ["Уже заданы фильтры, которые конфликтуют с новыми значениями:"]
            for key, old, new in conflicts:
                lines.append(
                    f"- {self._stock_filter_label(key)}: {_display_bot_number(old)} -> {_display_bot_number(new)}?"
                )
            lines.extend(["", "Заменить все на новые значения?"])
            return {
                "type": "message",
                "text": "\n".join(lines),
                "reply_markup": self._stock_filter_replace_keyboard(),
            }

        added = []
        for key, value in parsed.items():
            filters[key] = value
            added.append(self._stock_filter_line(key, value))

        payload["stock_filter"] = filters
        store.save_pending_operation(
            context["chat_id"],
            context["user_id"],
            operation_type,
            "stock_filter_collect",
            payload,
        )
        return self._stock_filter_prompt(payload, added=added)

    def _handle_stock_filter_replace(self, answer, store, context, payload):
        normalized = _normalize_phrase(answer)
        browse_mode = bool(payload.get("browse_mode"))
        operation_type = "stock_browse" if browse_mode else "stock_sale"
        if normalized in {"отмена", "отменить", "стоп", "скасувати", "відміна"}:
            store.delete_pending_operation(context["chat_id"], context["user_id"])
            return self._cancelled_reply("Просмотр остатка отменен." if browse_mode else "Операция продажи отменена.", store)

        pending = payload.pop("pending_filter_replace", {}) or {}
        parsed = pending.get("filters") or {}
        conflict_keys = set(pending.get("conflicts") or [])
        filters = dict(payload.get("stock_filter") or {})
        if normalized in {"заменить", "да", "yes", "так", "1"}:
            added = []
            for key, value in parsed.items():
                filters[key] = value
                added.append(self._stock_filter_line(key, value))
            payload["stock_filter"] = filters
            store.save_pending_operation(
                context["chat_id"],
                context["user_id"],
                operation_type,
                "stock_filter_collect",
                payload,
            )
            return self._stock_filter_prompt(payload, added=added)

        if normalized in {"оставить как есть", "оставить", "нет", "no", "net", "2"}:
            # Лишаємо старі значення тільки для полів, що конфліктували;
            # інші поля з того самого повідомлення (які не конфліктували)
            # все одно застосовуємо, а не викидаємо разом з конфліктними.
            added = []
            for key, value in parsed.items():
                if key in conflict_keys:
                    continue
                filters[key] = value
                added.append(self._stock_filter_line(key, value))
            payload["stock_filter"] = filters
            store.save_pending_operation(
                context["chat_id"],
                context["user_id"],
                operation_type,
                "stock_filter_collect",
                payload,
            )
            return self._stock_filter_prompt(payload, added=added or None)

        payload["pending_filter_replace"] = pending
        store.save_pending_operation(
            context["chat_id"],
            context["user_id"],
            operation_type,
            "stock_filter_confirm_replace",
            payload,
        )
        return {
            "type": "message",
            "text": "Выберите: Заменить, Оставить как есть или Отмена.",
            "reply_markup": self._stock_filter_replace_keyboard(),
        }

    # Той самий принцип зваженого збігу, що й _similar_sale_rows (Задача
    # користувача: коли фільтр складу нічого не знайшов — запропонувати
    # схожі наявні позиції, як і при реалізації), лише тут вхід — плаский
    # словник filters (product/breed/condition/thickness/width/length),
    # а не payload+item з флоу продажу.
    def _similar_stock_rows(self, store, filters):
        headers, columns, rows = warehouse_rows(store)
        # Задача продуктивності (аудит коду, 2026-08-16): condition_values
        # не залежить від конкретного рядка - рахуємо один раз ДО цикла,
        # а не на кожній ітерації (було O(rows^2) на весь список складу).
        condition_values = self._existing_product_type_values(rows, columns["product"])
        scored = []
        for row_id, row in rows:
            if not self._warehouse_row_has_balance(row, columns):
                continue
            score = 0
            row_product, row_type = self._split_product_condition(
                row_value(row, columns["product"]),
                condition_values,
            )
            if filters.get("product") and self._text_equal(row_product, filters.get("product")):
                score += 3
            if filters.get("breed") and self._text_equal(row_value(row, columns["breed"]), filters.get("breed")):
                score += 2
            if filters.get("condition") and self._text_equal(row_type, filters.get("condition")):
                score += 2
            for field in ("thickness", "width", "length"):
                if filters.get(field) is not None and self._number_equal(row_value(row, columns[field]), filters.get(field)):
                    score += 1
            if score <= 0:
                continue
            scored.append((score, self._warehouse_row_summary(row, columns)))
        scored.sort(key=lambda entry: entry[0], reverse=True)
        return [summary for _, summary in scored[:5]]

    # Запис приходу/продажу в SQLite + синхронізація Excel (apply_sale_operation,
    # apply_income_operation) тепер у warehouse_data.py — викликаються з
    # диспетчера діалогу нижче по файлу (_handle_pending_operation).

    # --- Залишки складу, фільтри, PDF-звіт "Остаток" ---
    def _first_rows_reply(self, requested_sheet, store):
        sheet_name = self._resolve_sheet_name(requested_sheet, store)
        if not sheet_name:
            return "Не нашел такой лист. Напишите /sheets для списка."

        headers = [_display_value(value) for value in store.get_headers(sheet_name)]
        rows = store.fetch_rows(sheet_name, 5, 0)
        if not rows:
            return f"{sheet_name}: строк нет."

        lines = [f"{sheet_name}: первые {len(rows)} строк"]
        for index, (_, row_values) in enumerate(rows, start=1):
            cells = []
            for col_index, value in enumerate(row_values[:5]):
                header = headers[col_index] if col_index < len(headers) and headers[col_index] else f"Колонка {col_index + 1}"
                cells.append(f"{header}: {_display_value(value)}")
            lines.append(f"{index}. " + " | ".join(cells))
        return "\n".join(lines)

    def _stock_balance_reply(self, store, context, filters=None, source="command", fmt=None, message_row_limit=40):
        report_rows, error = self._stock_balance_rows(store, filters=filters)
        # Помилка/порожній результат повертають до вибору категорії/фільтра
        # складу (ДАННЫЕ -> СКЛАД), а не в головне меню — можна одразу
        # спробувати іншу категорію.
        if error:
            return self._prepend_reply_text(error, self._stock_data_menu_reply(store, context))
        if not report_rows:
            filter_text = self._stock_filter_text(filters)
            lines = [f"Остаток по складу: по заданным параметрам ничего не найдено{filter_text}."]
            similar = self._similar_stock_rows(store, filters) if filters else []
            if similar:
                lines.append("")
                lines.append("Похожие позиции на складе:")
                lines.extend(f"{index}. {summary}" for index, summary in enumerate(similar, start=1))
            return self._prepend_reply_text(
                "\n".join(lines),
                self._stock_data_menu_reply(store, context),
            )

        if fmt is None:
            store.save_pending_operation(
                context["chat_id"],
                context["user_id"],
                "stock_report",
                "choose_format",
                {"filters": filters, "source": source},
            )
            filter_text = self._stock_filter_text(filters)
            return {
                "type": "message",
                "text": f"Остаток по складу: позиций {len(report_rows)}{filter_text}.\nВ каком виде показать?",
                "reply_markup": self._stock_report_format_keyboard(),
            }

        return self._render_stock_balance_report(store, report_rows, filters, source, fmt, message_row_limit)

    def _continue_stock_report(self, text, store, context, pending):
        payload = pending["payload"]
        if pending["status"] == "choose_stock_message_limit":
            return self._handle_stock_message_limit_choice(text, store, context, payload)

        fmt = self._stock_report_format_choice(text)
        if fmt is None:
            return {
                "type": "message",
                "text": "Не понял формат отчета. Выберите один из вариантов ниже.",
                "reply_markup": self._stock_report_format_keyboard(),
            }
        # Реальний запит користувача: "Сообщением" раніше одразу рендерило
        # з жорстко зашитим лімітом 40 рядків — тепер спершу питаємо, скільки
        # позицій показати (сам користувач обирає), і лише тоді рендеримо.
        # PDF/Excel — без обмеження, як і раніше, тому цей крок їх не чіпає.
        if fmt == reports.FORMAT_MESSAGE:
            store.save_pending_operation(
                context["chat_id"],
                context["user_id"],
                "stock_report",
                "choose_stock_message_limit",
                payload,
            )
            return {
                "type": "message",
                "text": "Сколько позиций показать в сообщении?",
                "reply_markup": self._stock_message_limit_keyboard(),
            }

        # Навмисно НЕ видаляємо pending-операцію — той самий звіт (ті самі
        # filters) лишається доступним, щоб користувач міг одразу подивитись
        # його ще в іншому форматі (PDF/Excel/Сообщением по черзі), а не
        # починати заново з "Остаток". Завершується лише через "Отмена".
        store.save_pending_operation(
            context["chat_id"],
            context["user_id"],
            "stock_report",
            "choose_format",
            payload,
        )
        return self._stock_balance_reply(
            store,
            context,
            filters=payload.get("filters"),
            source=payload.get("source", "command"),
            fmt=fmt,
        )

    def _handle_stock_message_limit_choice(self, text, store, context, payload):
        choice = self._stock_message_limit_choice(text)
        if choice is None:
            return {
                "type": "message",
                "text": "Не понял вариант. Выберите один из вариантов ниже.",
                "reply_markup": self._stock_message_limit_keyboard(),
            }
        message_row_limit = None if choice == "all" else choice
        store.save_pending_operation(
            context["chat_id"],
            context["user_id"],
            "stock_report",
            "choose_format",
            payload,
        )
        return self._stock_balance_reply(
            store,
            context,
            filters=payload.get("filters"),
            source=payload.get("source", "command"),
            fmt=reports.FORMAT_MESSAGE,
            message_row_limit=message_row_limit,
        )

    def _stock_report_format_keyboard(self):
        return {
            "keyboard": [
                [{"text": "📄 PDF"}, {"text": "📊 Excel"}],
                [{"text": "💬 Сообщением"}],
                [{"text": "Назад"}, {"text": "Главное меню"}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }

    def _stock_report_format_choice(self, answer):
        normalized = _normalize_phrase(answer)
        if "excel" in normalized or "эксель" in normalized or "таблица" in normalized:
            return reports.FORMAT_EXCEL
        if "pdf" in normalized or "пдф" in normalized:
            return reports.FORMAT_PDF
        if "сообщение" in normalized or "сообщением" in normalized or "текст" in normalized:
            return reports.FORMAT_MESSAGE
        return None

    # Скільки позицій показати в текстовому повідомленні "Остаток" — реальне
    # побажання користувача: раніше жорстко зашитий ліміт 40 рядків, тепер
    # користувач сам обирає (20/40/60 чи взагалі без обмеження). PDF/Excel
    # завжди показують усі рядки незалежно від цього вибору.
    def _stock_message_limit_keyboard(self):
        return {
            "keyboard": [
                [{"text": "20"}, {"text": "40"}, {"text": "60"}],
                [{"text": "Показать все"}],
                [{"text": "Назад"}, {"text": "Главное меню"}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }

    def _stock_message_limit_choice(self, answer):
        normalized = _normalize_phrase(answer)
        if normalized in {"20"}:
            return 20
        if normalized in {"40"}:
            return 40
        if normalized in {"60"}:
            return 60
        if normalized in {
            "все", "всё", "показать все", "показать всё", "весь список",
            "полностью", "все позиции", "всё позиции", "all",
        }:
            return "all"
        return None

    def _stock_report_spec(self, store, report_rows, message_row_limit=40):
        columns = [{"key": "index", "label": "N", "width_mm": 10, "align": "center", "in_message": False}]
        operation = store.get_operation_by_code("stock_report")
        for field in store.list_operation_fields(operation[0]):
            field_key, label = field[2], field[3]
            meta = _STOCK_REPORT_COLUMN_META.get(field_key)
            if meta is None:
                continue
            columns.append({"key": field_key, "label": label, **meta})
        rows = [dict(row, index=index) for index, row in enumerate(report_rows, start=1)]
        return {
            "title": "Остаток по складу",
            "generated_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
            "columns": columns,
            "rows": rows,
            # У повідомленні групуємо по товару/породі/стану — інакше кожен
            # з десятків рядків повторює однакові "Продукт: X | Порода: Y",
            # і звіт стає нечитабельним (саме це і сталось у реальному боті).
            "message_group_by": ["product", "breed", "condition"],
            # Реальний ризик з аудиту: групування рятує лише коли МАЛО груп
            # з багатьма рядками кожна — широкий/непідфільтрований "Остаток"
            # може мати десятки РІЗНИХ товар/порода/розмір комбінацій (кожна
            # своя окрема група), тож без обмеження повідомлення могло вийти
            # дуже довгим (більше ризику впертись у ліміт Telegram/429). PDF/
            # Excel і надалі показують усі рядки без обмеження. Реальне
            # побажання користувача: сам ліміт (20/40/60/без обмеження)
            # тепер обирає користувач (_stock_message_limit_keyboard), а не
            # жорстко зашите число — None тут означає "без обмеження".
            "message_row_limit": message_row_limit,
            "warnings": self._stock_report_warnings(report_rows),
        }

    def _stock_report_warnings(self, report_rows):
        mismatched = [row for row in report_rows if row.get("note")]
        if not mismatched:
            return []
        lines = [f"⚠ Обнаружено {len(mismatched)} позици(й) с несоответствием кол-ва и объёма — проверьте данные:"]
        for row in mismatched:
            lines.append(f"- {row.get('product')} / {row.get('breed')} / {row.get('size')}: {row.get('note')}")
        return lines

    def _render_stock_balance_report(self, store, report_rows, filters, source, fmt, message_row_limit=40):
        spec = self._stock_report_spec(store, report_rows, message_row_limit)
        totals = self._stock_report_totals(spec)
        extra_lines = []
        filter_text = self._stock_filter_text(filters)
        if filter_text:
            extra_lines.append(f"Фильтр{filter_text}")
        if source == "online_ai":
            extra_lines.append("Режим: Онлайн ШИ, только чтение")

        if fmt == reports.FORMAT_MESSAGE:
            text = reports.render_report_message(spec)
            if extra_lines:
                text += "\n" + "\n".join(extra_lines)
            text += "\n\nПоказать в другом формате, Назад или Главное меню."
            # Pending-операція лишається активною (_continue_stock_report не
            # видаляє її) — тож клавіатура вибору формату теж лишається:
            # користувач може одразу подивитись той самий звіт ще в іншому
            # форматі, не починаючи заново з "Остаток".
            return {
                "type": "message",
                "text": text,
                "reply_markup": self._stock_report_format_keyboard(),
            }

        try:
            REPORTS_DIR.mkdir(exist_ok=True)
            ext = "pdf" if fmt == reports.FORMAT_PDF else "xlsx"
            scope = (filters or {}).get("product")
            filename = reports.build_report_filename(
                "ОСТАТОК", scope, datetime.now().strftime("%d-%m-%Y"), ext=ext
            )
            path = REPORTS_DIR / filename
            rendered = reports.render_report(spec, fmt, path=path)
        except PermissionError:
            return (
                "Не удалось сформировать отчет: файл уже открыт в другой программе. "
                "Закройте его и попробуйте еще раз."
            )
        except Exception:
            return "Не удалось сформировать отчет. Попробуйте еще раз позже."

        totals_text = f"Итого: {_display_bot_number(totals['quantity'])} шт / {_display_bot_number(totals['volume'])} м3"
        if totals.get("area"):
            totals_text += f" / {_display_bot_number(totals['area'])} м2"
        if totals.get("linear"):
            totals_text += f" / {_display_bot_number(totals['linear'])} мп"
        caption_lines = [
            spec["title"],
            f"Позиций: {len(report_rows)}",
            totals_text,
        ]
        caption_lines.extend(extra_lines)
        if spec.get("warnings"):
            caption_lines.append(f"⚠ Обнаружены несоответствия в {sum(1 for row in report_rows if row.get('note'))} позиции(ях), см. отчет.")
        caption_lines.append("Показать в другом формате, Назад или Главное меню.")
        rendered["caption"] = "\n".join(caption_lines)
        rendered["reply_markup"] = self._stock_report_format_keyboard()
        return rendered

    def _stock_balance_rows(self, store, filters=None):
        sheet_name = "СКЛАД"
        headers = store.get_headers(sheet_name)
        rows = store.fetch_all_rows(sheet_name)
        if not headers or not rows:
            return [], "Не могу сформировать остаток: лист СКЛАД пустой."

        columns = self._stock_columns(headers)
        optional_columns = {"Состояние", "Остаток, м2", "Остаток, мп", "Основная ед. учета"}
        missing_columns = [
            title
            for title, index in columns.items()
            if index is None and title not in optional_columns
        ]
        if missing_columns:
            return (
                [],
                "Не могу сформировать остаток: не найдены колонки "
                + ", ".join(missing_columns)
                + "."
            )

        report_rows = []
        for row in rows:
            product = row_value(row, columns["Продукт"])
            breed = row_value(row, columns["Порода"])
            _, product_type = self._split_product_condition(product, [])
            condition = row_value(row, columns["Состояние"]) or product_type
            thickness = row_value(row, columns["Толщина, мм"])
            width = row_value(row, columns["Ширина, мм"])
            length = row_value(row, columns["Длина, мм"])
            quantity = row_value(row, columns["Остаток, шт"])
            volume = row_value(row, columns["Остаток, м3"])
            area = row_value(row, columns["Остаток, м2"]) if columns.get("Остаток, м2") is not None else None
            linear = row_value(row, columns["Остаток, мп"]) if columns.get("Остаток, мп") is not None else None

            if (
                _number_value(quantity) == 0
                and _number_value(volume) == 0
                and _number_value(area) == 0
                and _number_value(linear) == 0
            ):
                continue

            size = "x".join(
                _display_bot_number(value)
                for value in (thickness, width, length)
                if value not in (None, "")
            )

            # Товар, який ведеться в м2 (напр. вагонка) чи мп (25x50/30x50/
            # 50x50), фізично не має "об'єму" в цьому обліку — показувати
            # йому "0 м3" виглядає як помилка даних, хоча це просто не той
            # показник. Показуємо площу/довжину замість об'єму саме для
            # таких рядків, а не всі три одразу.
            # Реальний баг з аудиту: раніше вимір визначався за ТЕКСТОМ у
            # колонці "Основная ед. учета" — той самий текст, що записується
            # ОДИН РАЗ, лише при створенні НОВОГО рядка складу
            # (apply_income_operation), і ніколи не оновлюється для вже
            # існуючого рядка. Позиція 25x50, створена ДО появи мп (unit
            # лишився "м3"), чи будь-який рядок, де людина вручну через GUI
            # (звичайний редактор таблиці, без перевірок) вписала не той
            # текст у цю колонку — показували б застарілий/невірний вимір
            # (чи взагалі "губили" реальний залишок мп зі звіту). Тепер
            # визначаємо вимір ТАК САМО, як і вся решта коду (_row_measure_
            # kind) — за товаром/розміром, а не за текстом, що міг застаріти
            # чи бути вписаний вручну без перевірки.
            is_area_based = self._is_area_based_product(product)
            is_quantity_only = self._is_quantity_only_product(product)
            is_linear_based = not is_area_based and not is_quantity_only and self._is_linear_meter_size(thickness, width)
            note = self._stock_row_mismatch_note(
                is_area_based, is_linear_based, thickness, width, length, quantity, volume, linear, is_quantity_only
            )

            report_row = {
                "product": _display_value(product),
                "breed": _display_value(breed),
                "condition": _display_value(condition),
                "thickness": thickness,
                "width": width,
                "length": length,
                "size": size,
                "quantity": quantity,
                "volume": None if (is_area_based or is_linear_based or is_quantity_only) else volume,
                "area": area if is_area_based else None,
                "linear": linear if is_linear_based else None,
                "note": note or "",
            }
            if filters and not self._stock_report_row_matches(report_row, filters):
                continue
            report_rows.append(report_row)
        return report_rows, None

    # Порівнює заявлений обʼєм із перерахунком за розмірами (товщина х ширина
    # х довжина х кількість, мм -> м3) — щоб зловити реальні розбіжності
    # (напр. хтось вписав не той обʼєм чи кількість), а не просто товар,
    # який ведеться в м2 (is_area_based) чи мп (is_linear_based, довжина х
    # кількість, без товщини/ширини) — для них цю перевірку не робимо.
    _STOCK_VOLUME_MISMATCH_TOLERANCE = 0.05

    def _stock_row_mismatch_note(
        self, is_area_based, is_linear_based, thickness, width, length, quantity, volume, linear=None,
        is_quantity_only=False,
    ):
        if is_area_based or is_linear_based or is_quantity_only:
            return None
        expected_volume = (
            _number_value(thickness) / 1000
            * (_number_value(width) / 1000)
            * (_number_value(length) / 1000)
            * _number_value(quantity)
        )
        if expected_volume <= 0.0005:
            return None
        relative_diff = abs(_number_value(volume) - expected_volume) / expected_volume
        if relative_diff <= self._STOCK_VOLUME_MISMATCH_TOLERANCE:
            return None
        return (
            f"⚠ заявлено {_display_bot_number(volume)} м3, "
            f"по размеру ожидается ~{_display_bot_number(round(expected_volume, 4))} м3"
        )

    def _stock_report_row_matches(self, row, filters):
        text_fields = {
            "product": row.get("product", ""),
            "breed": row.get("breed", ""),
            "condition": row.get("condition", ""),
        }
        for field, expected in (filters or {}).items():
            if field in text_fields and not self._text_equal(text_fields[field], expected):
                return False
            if field in {"thickness", "width", "length"} and not self._number_equal(row.get(field), expected):
                return False
        return True

    # Той самий принцип, що й _sales_report_totals — через
    # reports.column_totals(spec), не власний sum().
    def _stock_report_totals(self, spec):
        raw_totals = reports.column_totals(spec)
        return {
            "quantity": raw_totals.get("quantity", 0),
            "volume": raw_totals.get("volume", 0),
            "area": raw_totals.get("area", 0),
            "linear": raw_totals.get("linear", 0),
        }

    def _stock_filter_result_reply(self, store, context, payload):
        filters = payload.get("stock_filter") or {}
        report_rows, error = self._stock_balance_rows(store, filters=filters)
        if error:
            store.save_pending_operation(
                context["chat_id"],
                context["user_id"],
                "stock_sale",
                "collect_income_missing",
                payload,
            )
            return error
        if not report_rows:
            store.save_pending_operation(
                context["chat_id"],
                context["user_id"],
                "stock_sale",
                "collect_income_missing",
                payload,
            )
            return (
                f"Остаток по фильтрам: позиций не найдено{self._stock_filter_text(filters)}.\n"
                "Уточните данные продажи. Операция не отменена."
            )

        if len(report_rows) > _STOCK_FILTER_INLINE_LIMIT:
            store.save_pending_operation(
                context["chat_id"],
                context["user_id"],
                "stock_sale",
                "stock_filter_collect",
                payload,
            )
            return {
                "type": "message",
                "text": (
                    f"Найдено много позиций: {len(report_rows)}{self._stock_filter_text(filters)}.\n"
                    "Уточните фильтр: продукт, породу, тип продукта или размер."
                ),
                "reply_markup": self._stock_filter_keyboard(),
            }

        # Примітка: на відміну від _render_stock_balance_report вище, це
        # проста внутрішньочатова довідка під час продажу (нема PDF/Excel,
        # нема вибору формату) - немає "другого способу" рахувати те саме,
        # тож reports.column_totals(spec) тут не потрібен (спец без
        # реального PDF/Excel-споживача був би зайвою складністю заради
        # самої лише схожості коду).
        totals = {
            "quantity": sum(_number_value(row.get("quantity")) for row in report_rows),
            "volume": sum(_number_value(row.get("volume")) for row in report_rows),
            "area": sum(_number_value(row.get("area")) for row in report_rows),
            "linear": sum(_number_value(row.get("linear")) for row in report_rows),
        }
        totals_line = f"Итого: {_display_bot_number(totals['quantity'])} шт / {_display_bot_number(totals['volume'])} м3"
        if totals.get("area"):
            totals_line += f" / {_display_bot_number(totals['area'])} м2"
        if totals.get("linear"):
            totals_line += f" / {_display_bot_number(totals['linear'])} мп"
        lines = [
            f"Остаток по фильтрам{self._stock_filter_text(filters)}:",
            f"Позиций: {len(report_rows)}",
            totals_line,
            "",
        ]
        for index, row in enumerate(report_rows, start=1):
            if row.get("linear") is not None:
                measure_suffix = f" / {_display_bot_number(row.get('linear'))} мп"
            elif row.get("area") is not None:
                measure_suffix = f" / {_display_bot_number(row.get('area'))} м2"
            elif row.get("volume") is not None:
                measure_suffix = f" / {_display_bot_number(row.get('volume'))} м3"
            else:
                measure_suffix = ""
            lines.append(
                f"{index}. {row.get('product')} / {row.get('breed')} / "
                f"{row.get('size')} — "
                f"{_display_bot_number(row.get('quantity'))} шт{measure_suffix}"
            )
        lines.extend(["", "Уточните данные продажи. Операция не отменена."])
        store.save_pending_operation(
            context["chat_id"],
            context["user_id"],
            "stock_sale",
            "collect_income_missing",
            payload,
        )
        return "\n".join(lines)

    def _stock_filter_state_text(self, filters):
        if not filters:
            return "Фильтры пока не выбраны."
        lines = ["Текущие фильтры:"]
        lines.extend(f"- {self._stock_filter_line(key, filters[key])}" for key in self._ordered_filter_keys(filters))
        return "\n".join(lines)

    def _ordered_filter_keys(self, filters):
        order = ("product", "breed", "condition", "thickness", "width", "length")
        return [key for key in order if key in (filters or {})]

    def _stock_filter_label(self, key):
        labels = {
            "product": "Продукт",
            "breed": "Порода",
            "condition": "Тип продукта",
            "thickness": "Толщина",
            "width": "Ширина",
            "length": "Длина",
        }
        return labels.get(key, key)

    def _stock_filter_line(self, key, value):
        return f"{self._stock_filter_label(key)}: {_display_bot_number(value)}"

    def _filter_value_equal(self, key, left, right):
        if key in {"thickness", "width", "length"}:
            return self._number_equal(left, right)
        return self._text_equal(left, right)

    def _stock_filter_text(self, filters):
        if not filters:
            return ""
        labels = {
            "product": "продукт",
            "breed": "порода",
            "condition": "тип продукта",
            "thickness": "толщина",
            "width": "ширина",
            "length": "длина",
        }
        parts = []
        for key in ("product", "breed", "condition", "thickness", "width", "length"):
            if key not in filters:
                continue
            value = filters[key]
            parts.append(f"{labels[key]}={_display_bot_number(value)}")
        return ": " + ", ".join(parts) if parts else ""

    def _stock_columns(self, headers):
        names = {
            "Продукт": ["Продукт"],
            "Порода": ["Порода"],
            "Состояние": ["Состояние"],
            "Толщина, мм": ["Толщина, мм", "Толщина"],
            "Ширина, мм": ["Ширина, мм", "Ширина"],
            "Длина, мм": ["Длина, мм", "Длинна, мм", "Длина", "Длинна"],
            "Остаток, шт": ["Остаток, шт"],
            "Остаток, м3": ["Остаток, м3"],
            "Остаток, м2": ["Остаток, м2"],
            "Остаток, мп": ["Остаток, мп"],
            "Основная ед. учета": ["Основная ед. учета", "Ед. учета"],
        }
        normalized_headers = {
            _normalize_phrase(header): index
            for index, header in enumerate(headers)
            if header is not None
        }
        return {
            target: next(
                (
                    normalized_headers[_normalize_phrase(candidate)]
                    for candidate in candidates
                    if _normalize_phrase(candidate) in normalized_headers
                ),
                None,
            )
            for target, candidates in names.items()
        }

    def _resolve_sheet_name(self, requested_sheet, store):
        sheet_names = store.sheet_names()
        if not requested_sheet:
            return sheet_names[0] if sheet_names else None

        requested = requested_sheet.casefold()
        for sheet_name in sheet_names:
            if sheet_name.casefold() == requested:
                return sheet_name
        for sheet_name in sheet_names:
            if requested in sheet_name.casefold():
                return sheet_name
        return None
