"""Потік "Антисептирование" (окрема послуга, не прихід/продаж складу) - вже й раніше найізольованіший блок файлу. Частина розбиття telegram_dialog.py - див. telegram_dialog.py для повної карти."""

import json

import permissions as perm
import webapp_server
from utils import (
    _display_bot_number,
    _normalize_phrase,
    _number_value,
)
from warehouse_data import (
    BOT_MESSAGE_DEFAULTS,
    apply_antiseptic_operation,
    product_requires_type,
)

class AntisepticDialogMixin:

    # "Антисептирование" — назва СЕРВІСНОЇ дії (kind='service'), яку теж
    # можна перейменувати через "Дії" (той самий CRUD, що й для звичайних
    # категорій) — тут і в _apply_category_change читаємо ПОТОЧНУ мітку
    # замість хардкодженого рядка, щоб підтвердження не показувало стару
    # назву після перейменування.
    def _antiseptic_operation_label(self, store):
        for operation in store.list_operations("start_sale"):
            if operation[2] == "service":
                return operation[4]
        return "Антисептирование"

    # --- Антисептирование: окремий, значно простіший флоу, ніж продаж
    # (немає рядків/розмірів/складу, лише клієнт+обсяг+ціна+оплата), тому
    # НЕ переиспользує величезний _continue_sale_operation_impl — власний,
    # ізольований ланцюжок статусів (той самий принцип, що й у
    # stock_report/sales_report/stock_browse — див. _handle_pending_operation).
    # Клієнтську валідацію (_sale_client_validation_issue, історія листа
    # ПРОДАЖА МАТЕРИАЛА) переиспользує напряму — та сама клієнтська база.
    # carry_over — payload продажу, з якого прийшли (choose_category/
    # confirm_category_change, коли "Способ оплаты" вже обрано на самому
    # старті "Реализации"). Реальний баг: перехід у сервіс раніше завжди
    # будував payload "з нуля" і губив уже обраний спосіб оплати, змушуючи
    # бота перепитувати його вдруге, попри "спитати один раз на старті".
    # Без carry_over (виклик напряму з кастомної кнопки-листа) — це
    # ГЕНУЇННО свіжий старт, переносити нічого.
    #
    # Реальний баг з аудиту (знайдено при додаванні поля "Адрес выгрузки"):
    # раніше тут ЗАВЖДИ жорстко зберігався статус "ask_antiseptic_client",
    # навіть коли client вже прийшов через carry_over. Це було непомітно,
    # поки одразу за клієнтом ішов volume — _apply_antiseptic_free_text
    # сама вміла підхопити голе число як обсяг незалежно від статусу. Але
    # тепер між клієнтом і обсягом з'явився адреса-крок, і "ask_antiseptic_
    # client"-гілка (де client уже відомий) просто МОВЧКИ відкидала
    # відповідь користувача, нічого з нею не роблячи, перш ніж наступний
    # виклик _continue_antiseptic_operation_step вже сам перепитував
    # правильне поле. Фікс: делегувати обчислення РЕАЛЬНОГО першого кроку
    # (client/адреса/обсяг/ціна/оплата — залежно від того, що вже відомо
    # з carry_over) тій самій функції, що веде решту діалогу.
    def _start_antiseptic_operation(self, store, context, carry_over=None):
        denied = self._require_permission(store, context, perm.SALE_CREATE)
        if denied:
            return denied
        payload = {
            "operation_kind": "antiseptic",
            "original_text": "",
            "user": {
                "id": context["user_id"],
                "username": context["username"],
                "full_name": context["full_name"],
            },
            "confirmed_new": [],
        }
        if carry_over:
            self._carry_over_sale_accumulation(carry_over, payload)
        return self._prepend_reply_text(
            "Антисептирование.", self._continue_antiseptic_operation_step(store, context, payload)
        )

    # Задача користувача: антисептирование сьогодні — 5 ОКРЕМИХ послідовних
    # питань (client/address/volume/price/payment_method) підряд, саме той
    # клас UX, який форма (Telegram Mini App) має замінити — на відміну від
    # income/sale/writeoff (один комбінований чек-лист), тут ОДНА форма з
    # усіма 5 полями одразу пропонується на КОЖНОМУ з цих кроків (той самий
    # принцип уніфікації, що й для решти 3 потоків): подання форми з будь-
    # якого кроку однаково зливає всі надані поля й веде до
    # _continue_antiseptic_operation_step, який сам розбереться, що ще
    # бракує. Старий послідовний шлях лишається як є — форма лише додається.
    _ANTISEPTIC_COLLECT_STATUSES = {
        "ask_antiseptic_client",
        "ask_antiseptic_address",
        "ask_antiseptic_volume",
        "ask_antiseptic_price",
        "ask_antiseptic_payment_method",
        "edit_antiseptic_data",
    }

    def _save_antiseptic_question(self, store, context, payload, status, text):
        store.save_pending_operation(context["chat_id"], context["user_id"], "antiseptic_service", status, payload)
        if isinstance(text, str):
            reply = {"type": "message", "text": text, "reply_markup": self._cancel_and_edit_keyboard()}
        else:
            reply = text
        if status in self._ANTISEPTIC_COLLECT_STATUSES and isinstance(reply, dict):
            operation = store.get_operation_by_code("sale_antiseptic")
            operation_id = operation[0] if operation else None
            reply = dict(reply)
            reply["reply_markup"] = self._webapp_keyboard(
                store,
                operation_id,
                self._WEBAPP_ANTISEPTIC_KEYS,
                payload,
                "Антисептирование",
                reply.get("reply_markup") or self._cancel_and_edit_keyboard(),
            )
        return reply

    # "Редактировать" на ask_antiseptic_* — як і _reopen_sale_collection,
    # веде до вже існуючого "edit_antiseptic_data" (там _apply_antiseptic_free_text
    # приймає виправлення будь-якого поля), а не залишає голий текст
    # буквальним значенням щойно запитаного поля.
    def _reopen_antiseptic_collection(self, store, context, payload):
        prompt = (
            "Напишите новое значение — я заменю только то, что вы укажете.\n\n"
            + self._antiseptic_mandatory_fields_prompt(store, payload)
        )
        return self._save_antiseptic_question(store, context, payload, "edit_antiseptic_data", prompt)

    # Мітки (Клиент:/Адрес выгрузки:/Цена:/Сумма:/Оплата:/Комментарий:/Дата:)
    # розпізнаються на КОЖНОМУ кроці (той самий принцип, що й у продажу) —
    # досвідчений користувач може дати кілька полів одним повідомленням.
    # Голе число трактується як обсяг (м3) ЛИШЕ якщо клієнт й адреса вже
    # відомі, а обсяг ще не заданий — інакше на кроці "Укажите клиента"
    # ім'я клієнта на кшталт "12345" помилково забрало б собі це значення,
    # так само як і на кроці "Укажите адрес" номер будинку сам по собі.
    def _apply_antiseptic_free_text(self, payload, text, store):
        extracted_text, extra_fields = self._extract_sale_fields(text, store)
        payload.update(extra_fields)
        remaining = extracted_text.strip()
        if (
            remaining
            and payload.get("client")
            and payload.get("address")
            and not (_number_value(payload.get("volume")) > 0)
        ):
            plain_number = self._parse_plain_positive_number(remaining)
            if plain_number is not None:
                payload["volume"] = plain_number
                remaining = ""
        return remaining

    def _antiseptic_preview(self, payload):
        volume = _number_value(payload.get("volume"))
        price_per_unit = _number_value(payload.get("price_per_unit"))
        # Аудит коду (перевірка охоплення Fix #4): без round(..., 2) тут
        # користувач бачив "Сумма: 30.299999999999997 MDL" у самому вікні
        # підтвердження (float-шум від price_per_unit * volume) - той самий
        # клас багу, що вже виправлений у sale_sheet_values/antiseptic_
        # sheet_values, лише не поширений на цей прев'ю-розрахунок.
        total_amount = _number_value(payload.get("total_amount")) or round(price_per_unit * volume, 2)
        # Задача користувача: "сумму в кінці і через пробіл, і одного
        # Антисептирование на початку достатньо" - заголовок "Антисептирование:"
        # прибрано (виклик уже додає "Антисептирование." окремим рядком,
        # _prepend_reply_text), Сумма переїхала в самий кінець.
        # Задача користувача: "додай змогу ще додавати доски до продажі
        # послуги" - кілька позицій несуть СВОЮ розбивку (_position_lines,
        # _continue_antiseptic_all_in_one_multi_position) - тоді замість
        # одного "Объем"/"Цена" друкуємо кожну дошку окремо + загальний об'єм.
        position_lines = payload.get("_position_lines")
        lines = []
        if position_lines:
            lines.extend(position_lines)
            lines.append("")
        lines.append(f"Клиент: {payload.get('client')}")
        lines.append("")
        lines.append(f"Адрес выгрузки: {payload.get('address')}")
        if position_lines:
            lines.append(f"Объём всего: {_display_bot_number(volume)} м3")
        else:
            lines.append(f"Объем: {_display_bot_number(volume)} м3")
            if price_per_unit:
                lines.append(f"Цена: {_display_bot_number(price_per_unit)} MDL/м3")
        lines.append(f"Оплата: {payload.get('payment_method')}")
        if payload.get("comment"):
            lines.append(f"Комментарий: {payload['comment']}")
        lines.append("")
        lines.append(f"Сумма: {_display_bot_number(total_amount)} MDL")
        lines.append("")
        lines.append("Склад не списывается.")
        lines.append("Подтвердить запись?")
        return "\n".join(lines)

    # Далі йде та сама послідовність, що й у продажі (Клиент -> Цена/Сумма ->
    # Способ оплаты), лише без розмірів/перевірки складу — обсяг послуги
    # питається одразу після клієнта.
    def _continue_antiseptic_operation_step(self, store, context, payload):
        if not payload.get("client"):
            return self._save_antiseptic_question(
                store,
                context,
                payload,
                "ask_antiseptic_client",
                {
                    "type": "message",
                    "text": self._antiseptic_mandatory_fields_prompt(store, payload),
                    "reply_markup": self._client_entry_keyboard(),
                },
            )

        validation = self._sale_client_validation_issue(store, payload)
        if validation:
            payload["validation"] = validation
            store.save_pending_operation(
                context["chat_id"], context["user_id"], "antiseptic_service", "confirm_new_antiseptic_client", payload
            )
            # Реальний баг з аудиту: на відміну від продажу (_next_sale_
            # validation_issue гілкується на suggestion), тут БУДЬ-ЯКЕ
            # нерозпізнане ім'я клієнта одразу йшло в "додати нового?" —
            # навіть якщо _sale_client_validation_issue вже знайшов явний
            # фаззі-збіг з існуючим клієнтом (одрук на кшталт "IMPEX TRAID
            # SRL" замість "IMPEX TRADE SRL"). Тепер показуємо ту саму
            # пропозицію-з-запам'ятовуванням, що й у продажу.
            if validation.get("suggestion") is not None:
                return self._validation_suggestion_prompt(validation, payload)
            value = validation.get("value", "")
            return self._yes_no_reply(
                f'Клиент "{value}" не найден в истории продаж.\n'
                f'Добавить нового клиента "{value}"?\n'
                "Да / Нет"
            )

        # ТЗ: "Адрес выгрузки" — вводиться один раз, одразу після клієнта
        # (той самий крок у списку полів, що й у РЕАЛИЗАЦИЯ). На відміну
        # від клієнта — без фаззі-звірки з історією, просто вільний текст.
        if not payload.get("address"):
            return self._save_antiseptic_question(
                store,
                context,
                payload,
                "ask_antiseptic_address",
                self._antiseptic_mandatory_fields_prompt(store, payload),
            )

        if not (_number_value(payload.get("volume")) > 0):
            return self._save_antiseptic_question(
                store, context, payload, "ask_antiseptic_volume", self._antiseptic_mandatory_fields_prompt(store, payload)
            )

        has_price = _number_value(payload.get("price_per_unit")) > 0 or _number_value(payload.get("total_amount")) > 0
        if not has_price:
            return self._save_antiseptic_question(
                store,
                context,
                payload,
                "ask_antiseptic_price",
                self._antiseptic_mandatory_fields_prompt(store, payload),
            )

        if not payload.get("payment_method"):
            return self._save_antiseptic_question(
                store,
                context,
                payload,
                "ask_antiseptic_payment_method",
                {
                    "type": "message",
                    "text": self._antiseptic_mandatory_fields_prompt(store, payload),
                    "reply_markup": self._payment_method_keyboard(store),
                },
            )

        store.save_pending_operation(
            context["chat_id"], context["user_id"], "antiseptic_service", "confirm_antiseptic_write", payload
        )
        return self._confirmation_reply(
            self._antiseptic_preview(payload),
            allow_edit=not payload.get("_from_webapp_form"),
            show_form_return=bool(payload.get("_from_webapp_form")),
            yes_cancel_only=True,
        )

    def _continue_antiseptic_operation(self, text, store, context, pending):
        payload = pending["payload"]
        status = pending["status"]
        answer = text.strip()

        if status == "confirm_antiseptic_write":
            from_webapp_form = bool(payload.get("_from_webapp_form"))
            # Задача користувача: "додай ще повернення в форму... знизу
            # третя кнопка" - знову відкриває ТУ САМУ форму замість старого
            # текстового "Редактировать".
            if from_webapp_form and _normalize_phrase(answer) == _normalize_phrase(self._WEBAPP_FORM_RETURN_LABEL):
                return self._reopen_webapp_form_reply(store, context, payload)
            # Той самий принцип, що й у sale/income/writeoff: форма-режим
            # (антисептирование через мега-форму, без carry_over із продажі)
            # не має виходу в старий текстовий "Редактировать".
            if not from_webapp_form and self._is_edit_request(answer):
                # Свіжий пере-аудит (2026-08-02): усі ІНШІ "Редактировать"-гілки
                # антисептирования вже викликають _reopen_antiseptic_collection
                # (антисептик-специфічний чек-лист, без дощок/штук) - ця була
                # єдиним забутим винятком, що показувала загальний
                # income/sale-приклад "Доска AD, 6000 или 120 шт.".
                return self._reopen_antiseptic_collection(store, context, payload)
            decision = self._yes_no(answer)
            if decision is None:
                text = (
                    f"Ответьте, пожалуйста: Да, Нет или {self._WEBAPP_FORM_RETURN_LABEL}."
                    if from_webapp_form
                    else "Ответьте, пожалуйста: Да, Нет или Редактировать."
                )
                return self._confirmation_reply(
                    text, allow_edit=not from_webapp_form, show_form_return=from_webapp_form
                )
            if not decision:
                store.delete_pending_operation(context["chat_id"], context["user_id"])
                # Реальний баг зі скріна: форма → "Нет" мовчки тягнув би у
                # СТАРИЙ вибір категорії РЕАЛИЗАЦИЯ (carry_over нижче) - для
                # антисептирования, розпочатого напряму з мега-форми, ніякої
                # продажі "в процесі" й нема, тож це чиста "пастка", не
                # відновлення контексту. Форма-режим завершується ЛИШЕ
                # інформуванням.
                if from_webapp_form:
                    return self._webapp_form_terminal_reply(store, context, "Антисептирование отменено.")
                # Реальний баг з аудиту: скасування тут завжди йшло в
                # головне меню, тихо гублячи client/payment_method/
                # completed_positions, якщо в антисептирование зайшли
                # посеред накопиченої продажі (carry_over уже приніс ці
                # поля в payload — _start_antiseptic_operation). Тепер
                # повертаємось до вибору категорії ІЗ ЦИМИ Ж даними, той
                # самий carry_over, що й на успішному завершенні нижче.
                return self._prepend_reply_text(
                    "Антисептирование отменено.",
                    self._start_sale_category_menu(store, context, carry_over=payload),
                )
            sync_mode = self._excel_sync_mode()
            result = apply_antiseptic_operation(store, payload, sync_mode, self)
            if not result.get("ok"):
                return f"⚠️ {result['message']}"
            # Задача користувача (2026-08-17): дубль звіту в окрему групу.
            self._notify_report_broadcast(context, result["message"])
            # Реальний баг зі скріна користувача: форма → успіх → бот
            # ЗАВЖДИ повертав у СТАРИЙ покроковий вибір категорії РЕАЛИЗАЦИЯ
            # - людина, що ввела антисептирование через форму, опинялась у
            # чужому UI-режимі без кнопки форми. Форма-режим завершується
            # ЛИШЕ інформуванням про результат.
            if from_webapp_form:
                return self._webapp_form_terminal_reply(store, context, result["message"], parse_mode="HTML")
            # Не викидаємо в головне меню — повертаємо до вибору категорії
            # РЕАЛИЗАЦИЯ (звідки й потрапляють в антисептирование), щоб можна
            # було одразу оформити ще одну операцію. Головне меню — лише за
            # явним натисканням "Главное меню". Реальний баг з аудиту: раніше
            # цей виклик не передавав payload узагалі — уже введені позиції/
            # клієнт/спосіб оплати з продажі, з якої прийшли в антисептирование,
            # тихо губились. carry_over=payload переносить їх назад.
            return self._prepend_reply_text(
                result["message"],
                self._start_sale_category_menu(store, context, carry_over=payload),
                parse_mode="HTML",
            )

        if status == "edit_antiseptic_data":
            # Важлива знахідка нового аудиту (28.07.2026, #3): раніше
            # повернене remaining ігнорувалось - голе число, яким
            # виправляють ЦІНУ (volume вже встановлено на цьому кроці, тож
            # єдина "голе число" гілка всередині _apply_antiseptic_free_text
            # - no-op), просто зникало без сліду. Той самий виклик, що вже
            # робить ask_antiseptic_price - БЕЗ гейту "лише якщо ціна ще не
            # встановлена" (тут навпаки, ціна майже завжди вже є, і саме її
            # виправляють).
            remaining = self._apply_antiseptic_free_text(payload, answer, store)
            if remaining:
                price_fields = self._parse_sale_price_answer(remaining)
                if price_fields is not None:
                    payload.update(price_fields)
            return self._continue_antiseptic_operation_step(store, context, payload)

        if status == "confirm_new_antiseptic_client":
            validation = payload.get("validation", {})
            if validation.get("suggestion") is not None:
                # Той самий розбір, що й у продажу (Принять и запомнить/
                # Просто принять/Нет/Редактировать + природна мова) —
                # див. _parse_client_suggestion_decision.
                decision_kind = self._parse_client_suggestion_decision(answer)
                if decision_kind is None:
                    return self._validation_suggestion_prompt(validation, payload)
                if decision_kind == "edit":
                    payload.pop("validation", None)
                    return self._reopen_antiseptic_collection(store, context, payload)
                if decision_kind == "remember":
                    store.remember_client_alias(validation.get("value"), validation.get("suggestion"))
                if decision_kind in ("remember", "accept"):
                    self._apply_validated_value(payload, validation, validation.get("suggestion"))
                    payload.pop("validation", None)
                    return self._continue_antiseptic_operation_step(store, context, payload)
                # decision_kind == "reject" — не той клієнт, пропонуємо
                # додати введене ім'я як нового (той самий крок, що й раніше
                # для "нема suggestion взагалі"). Реальний баг з аудиту: тут
                # раніше НЕ зберігався payload і НЕ прибиралась suggestion —
                # той самий статус на наступному кроці знову йшов у гілку
                # "є пропозиція" вище, тож "Так" помилково приймало щойно
                # відхилену пропозицію, а "Ні" зациклювалось. Прибираємо
                # suggestion і зберігаємо — наступна відповідь тепер коректно
                # потрапляє в гілку "нема пропозиції" нижче (звичайне
                # Так/Ні "додати як нового").
                validation = dict(validation)
                validation["suggestion"] = None
                payload["validation"] = validation
                store.save_pending_operation(
                    context["chat_id"], context["user_id"], "antiseptic_service", "confirm_new_antiseptic_client", payload
                )
                value = validation.get("value", "")
                return self._yes_no_reply(
                    f'Клиент "{value}" не найден в истории продаж.\n'
                    f'Добавить нового клиента "{value}"?\n'
                    "Да / Нет"
                )

            decision = self._yes_no(answer)
            if decision is None:
                return self._yes_no_reply("Ответьте, пожалуйста: Да или Нет.")
            if not decision:
                payload.pop("validation", None)
                payload["client"] = None
                return self._continue_antiseptic_operation_step(store, context, payload)
            self._mark_new_value_confirmed(payload, validation)
            payload.pop("validation", None)
            return self._continue_antiseptic_operation_step(store, context, payload)

        if status == "ask_antiseptic_client":
            if self._is_edit_request(answer):
                return self._reopen_antiseptic_collection(store, context, payload)
            remaining = self._apply_antiseptic_free_text(payload, answer, store)
            if not payload.get("client"):
                client = remaining.strip()
                if not client:
                    return self._save_antiseptic_question(
                        store,
                        context,
                        payload,
                        "ask_antiseptic_client",
                        {
                            "type": "message",
                            "text": self._antiseptic_mandatory_fields_prompt(store, payload),
                            "reply_markup": self._client_entry_keyboard(),
                        },
                    )
                payload["client"] = client
            return self._continue_antiseptic_operation_step(store, context, payload)

        if status == "ask_antiseptic_address":
            if self._is_edit_request(answer):
                return self._reopen_antiseptic_collection(store, context, payload)
            remaining = self._apply_antiseptic_free_text(payload, answer, store)
            if not payload.get("address"):
                address = remaining.strip()
                if not address:
                    return self._save_antiseptic_question(
                        store,
                        context,
                        payload,
                        "ask_antiseptic_address",
                        self._antiseptic_mandatory_fields_prompt(store, payload),
                    )
                payload["address"] = address
            return self._continue_antiseptic_operation_step(store, context, payload)

        if status == "ask_antiseptic_volume":
            if self._is_edit_request(answer):
                return self._reopen_antiseptic_collection(store, context, payload)
            self._apply_antiseptic_free_text(payload, answer, store)
            if not (_number_value(payload.get("volume")) > 0):
                return self._save_antiseptic_question(
                    store,
                    context,
                    payload,
                    "ask_antiseptic_volume",
                    "Не понял объем. Напишите число в м3 (например: 6.3).",
                )
            return self._continue_antiseptic_operation_step(store, context, payload)

        if status == "ask_antiseptic_price":
            if self._is_edit_request(answer):
                return self._reopen_antiseptic_collection(store, context, payload)
            remaining = self._apply_antiseptic_free_text(payload, answer, store)
            has_price = _number_value(payload.get("price_per_unit")) > 0 or _number_value(payload.get("total_amount")) > 0
            if not has_price:
                price_fields = self._parse_sale_price_answer(remaining)
                if price_fields is not None:
                    payload.update(price_fields)
                else:
                    return self._save_antiseptic_question(
                        store,
                        context,
                        payload,
                        "ask_antiseptic_price",
                        "Не понял цену. Напишите число (например: 350 — цена за м3).",
                    )
            return self._continue_antiseptic_operation_step(store, context, payload)

        if status == "ask_antiseptic_payment_method":
            if self._is_edit_request(answer):
                return self._reopen_antiseptic_collection(store, context, payload)
            remaining = self._apply_antiseptic_free_text(payload, answer, store)
            if not payload.get("payment_method"):
                payment_method = remaining.strip()
                if not payment_method:
                    return self._save_antiseptic_question(
                        store,
                        context,
                        payload,
                        "ask_antiseptic_payment_method",
                        {
                            "type": "message",
                            "text": self._antiseptic_mandatory_fields_prompt(store, payload),
                            "reply_markup": self._payment_method_keyboard(store),
                        },
                    )
                payload["payment_method"] = payment_method
            return self._continue_antiseptic_operation_step(store, context, payload)

        store.delete_pending_operation(context["chat_id"], context["user_id"])
        return self._with_main_menu("Не удалось продолжить операцию. Отправьте антисептирование заново.", store)

    # Той самий принцип для антисептирования (див. коментар вище) — плюс
    # "Объем услуги, м3", якого нема у звичайній продажі. Тут РІВНО ОДНА
    # дія (sale_antiseptic) — жодного product/condition-резолву не треба,
    # береться напряму за кодом.
    def _antiseptic_mandatory_fields_missing(self, store, payload):
        operation = store.get_operation_by_code("sale_antiseptic") if store else None
        if operation is None:
            return self._antiseptic_mandatory_fields_missing_legacy(payload)
        fields = {field[2]: field for field in store.list_operation_fields(operation[0])}
        return self._flat_checklist_missing_fields(fields, payload, "service")

    def _antiseptic_mandatory_fields_missing_legacy(self, payload):
        missing = []
        if not payload.get("client"):
            missing.append("Клиент")
        if not payload.get("address"):
            missing.append("Адрес выгрузки")
        if not (_number_value(payload.get("volume")) > 0):
            missing.append("Объем услуги, м3")
        has_price = _number_value(payload.get("price_per_unit")) > 0 or _number_value(payload.get("total_amount")) > 0
        if not has_price:
            missing.append("Цена за м3")
        if not payload.get("payment_method"):
            missing.append("Способ оплаты")
        return missing

    def _antiseptic_mandatory_fields_prompt(self, store, payload):
        lines = []
        header = self._operation_header_text(store, "service", payload)
        if header:
            lines.append(header)
            lines.append("")
        recognized = self._recognized_data_lines(payload, store=store, kind="service")
        if recognized:
            lines.extend(recognized)
            lines.append("")
        # Реальний баг: заголовок раніше друкувався БЕЗУМОВНО, тож коли
        # _antiseptic_mandatory_fields_missing повертає [] (наприклад через
        # прогалину в bot_operation_fields, як з "Способ оплаты" на
        # sale_antiseptic — сама перевірка в _continue_antiseptic_operation_
        # step жорстко закодована й від конфігурації не залежить, тож бот
        # все одно перепитує поле, яке чек-лист "не бачить"), користувач
        # отримував порожній, незрозумілий "Не хватает данных..." без жодного
        # пункту. Тепер заголовок друкується лише разом із реальним списком.
        missing = self._antiseptic_mandatory_fields_missing(store, payload)
        if missing:
            lines.append("Не хватает данных для оформления услуги:")
            lines.extend(f"- {field}" for field in missing)
        return "\n".join(lines)

    # --- "Антисептирование (форма)" - окрема Mini App форма (не доповнення
    # до продажу) - той самий мега-формат, що вже мають ПРИХОД/РЕАЛИЗАЦИЯ/
    # СПИСАНИЕ (форма): товар/порода/розмір обираються ПРЯМО в формі, з тим
    # самим select+вручну (allow_custom), що й усюди. Перевикористовує РЕАЛЬНІ
    # sale-категорії (Доска AD/KD/ОСБ/Вагонка) лише як джерело товару/породи/
    # каскаду розмірів - об'єм (кубатура) рахується у webapp/app.js з тих
    # самих товщини/ширини/довжини/штук (m3 = т*ш*д/1e9*штук, той самий
    # принцип, що вже давно рахує currentAntisepticVolume() для антисептик-
    # доповнення до продажу), і саме він, а не порода/розмір, іде у запис -
    # antiseptic_sheet_values (warehouse_data.py) не має колонок товару/
    # породи/розміру взагалі, лише volume/price_per_unit/client/address/
    # payment_method.
    # Задача користувача: "додай змогу ще додавати для одного клієнта доски
    # до продажі послуги, так як це реалізовано в продажі пиломатеріалу" -
    # price_per_unit тепер НА КОЖНІЙ позиції (як у sale), не спільне поле -
    # інакше суму окремої дошки нізвідки взяти в момент додавання в кошик
    # (задача користувача, скріншот "суму окремо після кубатури").
    _WEBAPP_ANTISEPTIC_CATEGORY_KEYS = ("breed", "thickness", "width", "length", "quantity", "price_per_unit")
    _WEBAPP_ANTISEPTIC_COMMON_KEYS = ("client", "address", "payment_method")

    def _webapp_antiseptic_form_context(self, store, resume_payload=None):
        antiseptic_operation = store.get_operation_by_code("sale_antiseptic")
        antiseptic_operation_id = antiseptic_operation[0] if antiseptic_operation else None
        # Задача користувача: мітка "Цена" раніше бралась з sale_antiseptic
        # (єдине спільне поле) - тепер price_per_unit переїхало НА КОЖНУ
        # категорію (Доска AD/KD), у яких свого налаштування мітки цього
        # поля нема, тож без цього override воно тихо втратило б адмінське
        # перейменування (напр. "Цена за м3") і показувало типове "Цена".
        antiseptic_price_label = None
        if antiseptic_operation_id is not None:
            for field in store.list_operation_fields(antiseptic_operation_id):
                if field[2] == "price_per_unit":
                    antiseptic_price_label = field[3]
                    break
        categories = []
        for operation in store.list_operations("start_sale"):
            op_id, _code, kind, _requires_identity, label, _parent, prefill_json, *_rest = operation
            if kind == "service":
                continue
            prefill = json.loads(prefill_json) if prefill_json else {}
            # Задача користувача: "антисептируеться може лише доска. Вагонка
            # і ОСБ - не антисептируються" - товар обирається лише серед
            # категорій ДОСКА (AD/KD), той самий product_requires_type, що
            # вже визначає "чи цей товар взагалі має Тип (AD/KD)" деінде.
            if not product_requires_type(prefill.get("product")):
                continue
            sub_payload = {"product": prefill.get("product"), "condition": prefill.get("condition")}
            # Задача користувача (скріншот): "не прив'язуй вибір випадаючим
            # списком... розміри мають показувати всі що існують чи
            # існували в таблиці" - антисептирование НЕ споживає залишок
            # складу (те саме "Склад не списан", що вже друкує apply_
            # antiseptic_operation), тож на відміну від продажу/списання тут
            # НЕМАЄ причини звужувати дропдауни під поточний залишок чи
            # прив'язувати товщину/ширину/довжину одне до одного каскадом -
            # той самий restrict_to_existing_combos=False, що вже має Приход
            # (нова комбінація - нормальний сценарій, не помилка).
            sub_ctx = self._webapp_form_context(
                store, op_id, self._WEBAPP_ANTISEPTIC_CATEGORY_KEYS, sub_payload, label,
                restrict_to_existing_combos=False,
            )
            if antiseptic_price_label:
                for field in sub_ctx["fields"]:
                    if field["key"] == "price_per_unit":
                        field["label"] = antiseptic_price_label
                        break
            categories.append({
                "key": op_id,
                "label": label,
                # kind="antiseptic" (не "sale") - webapp/app.js розрізняє за
                # ним одноразове (без кошика) подання з обчисленням об'єму,
                # той самий принцип, що вже діє для kind="income"/"writeoff".
                "kind": "antiseptic",
                "fields": sub_ctx["fields"],
                "dimension_combos": sub_ctx["dimension_combos"],
                "product": prefill.get("product"),
            })
        # operation_id антисептика (не None) - щоб мітка "Цена" підхопила
        # адмінське перейменування поля sale_antiseptic (той самий пошук
        # мітки, що вже робить _save_antiseptic_question вище для чатового
        # шляху), а не завжди показувала типове "Цена".
        common_ctx = self._webapp_form_context(
            store, antiseptic_operation_id, self._WEBAPP_ANTISEPTIC_COMMON_KEYS, {}, "Антисептирование",
        )
        ctx = {
            "mode": "all_in_one",
            "kind": "antiseptic",
            "title": "Антисептирование одной формой",
            "categories": categories,
            "common_fields": common_ctx["fields"],
            **self._webapp_style_ctx(),
            **self._webapp_templates_ctx(store, "antiseptic"),
        }
        # Задача користувача (скріншот "нащо ти кнопку прибрав"): та сама
        # логіка відновлення, що вже має продаж - "поточна" (ще не
        # підтверджена) позиція антисептирования несе СВОЇ category_
        # operation_id/breed/rows у payload (нижче, _continue_antiseptic_
        # all_in_one_submission) саме для ЦЬОГО - antiseptic_sheet_values
        # їх не читає взагалі, лише volume/price/client/address/payment.
        resume_positions = resume_payload.get("_resume_positions") if resume_payload else None
        if resume_positions:
            # Кілька дощок (мультипозиційне антисептирование, нижче) -
            # відновлюємо як КОШИК, той самий cart.push(...)/renderCart(),
            # що вже відновлює продаж (webapp/app.js - код там уже
            # спільний, не потребує окремої гілки під antiseptic).
            cart = []
            for position in resume_positions:
                key = str(position.get("category_operation_id"))
                if any(str(cat["key"]) == key for cat in categories):
                    cart.append({
                        "category_operation_id": position.get("category_operation_id"),
                        "breed": position.get("breed"),
                        "rows": position.get("rows"),
                        "price_per_unit": position.get("price_per_unit"),
                    })
            if cart:
                ctx["resume"] = {
                    "cart": cart,
                    "common": {
                        "client": resume_payload.get("client"),
                        "address": resume_payload.get("address"),
                        "payment_method": resume_payload.get("payment_method"),
                    },
                }
        elif resume_payload and resume_payload.get("category_operation_id"):
            key = str(resume_payload["category_operation_id"])
            if any(str(cat["key"]) == key for cat in categories):
                ctx["resume"] = {
                    "category_operation_id": resume_payload["category_operation_id"],
                    "breed": resume_payload.get("breed"),
                    "rows": resume_payload.get("rows"),
                    "common": {
                        "client": resume_payload.get("client"),
                        "address": resume_payload.get("address"),
                        "payment_method": resume_payload.get("payment_method"),
                        "price_per_unit": resume_payload.get("price_per_unit"),
                    },
                }
        return ctx

    def _antiseptic_all_in_one_webapp_button(self, store, resume_payload=None):
        base_url = getattr(self, "webapp_public_url", None)
        if not base_url:
            return None
        ctx = self._webapp_antiseptic_form_context(store, resume_payload=resume_payload)
        if not ctx["categories"]:
            return None
        token = webapp_server.register_context(ctx)
        url = f"{base_url.rstrip('/')}/index.html?t={token}"
        return {"web_app": {"url": url}}

    def _start_antiseptic_all_in_one_reply(self, store, context, resume_payload=None):
        denied = self._require_permission(store, context, perm.SALE_CREATE)
        if denied:
            return denied
        web_app = self._antiseptic_all_in_one_webapp_button(store, resume_payload=resume_payload)
        if web_app is None:
            return self._with_main_menu(
                "Антисептирование одной формой сейчас недоступно (форма не подключена "
                "или нет категорий товара). Используйте обычное «Антисептирование».",
                store,
            )
        # antiseptic_all_in_one - той самий статус-маркер, що sale_all_in_one/
        # writeoff_all_in_one/income_all_in_one вже мають (_continue_operation_
        # with_webapp_payload, telegram_dialog_core.py) - страховка на випадок,
        # якщо форма подається ПІСЛЯ живого бот-кроку (тунель піднявся вже
        # після відкриття меню), а не прямим тапом по кнопці меню.
        store.save_pending_operation(
            context["chat_id"], context["user_id"], "antiseptic_service", "antiseptic_all_in_one", {}
        )
        keyboard = {
            "keyboard": [
                [{"text": "Заполнить форму антисептирования", **web_app}],
                [{"text": "Главное меню"}],
            ],
            "resize_keyboard": True,
        }
        return {
            "type": "message",
            "text": store.get_message_template("start_antiseptic_form", BOT_MESSAGE_DEFAULTS["start_antiseptic_form"]),
            "reply_markup": keyboard,
        }

    # Пряме відкриття (submitted["antiseptic_form"]=true,
    # _continue_direct_open_webapp_submission) чи фолбек через pending
    # (status="antiseptic_all_in_one") - обидва ведуть сюди. Товар/порода/
    # розмір із submitted НЕ читаються - лише вже ОБЧИСЛЕНИЙ на клієнті
    # "volume" (той самий підхід, що вже мав sale-доповнення антисептика:
    # kind=="service"-гілка в _continue_sale_all_in_one_submission), решта
    # полів - буквально ті самі, що приймає _continue_antiseptic_operation_step.
    def _continue_antiseptic_all_in_one_submission(self, store, context, submitted):
        denied = self._require_permission(store, context, perm.SALE_CREATE)
        if denied:
            return denied
        store.delete_pending_operation(context["chat_id"], context["user_id"])
        # Задача користувача: "додай змогу ще додавати для одного клієнта
        # доски до продажі послуги, так як це реалізовано в продажі
        # пиломатеріалу" - webapp/app.js накопичує кілька дощок у себе (той
        # самий кошик-принцип, submit() там же) і надсилає їх РАЗОМ як
        # positions[], лише коли реально було ЩОСЬ додано кнопкою
        # "Продолжить" - інакше (одна дошка) лишається старий, уже
        # перевірений шлях нижче без positions[] узагалі.
        positions_data = submitted.get("positions")
        if isinstance(positions_data, list) and positions_data:
            return self._continue_antiseptic_all_in_one_multi_position(store, context, submitted, positions_data)
        payload = {
            "operation_kind": "antiseptic",
            "original_text": "",
            "user": {
                "id": context["user_id"],
                "username": context["username"],
                "full_name": context["full_name"],
            },
            "confirmed_new": [],
            # Задача користувача (скріншот "нащо ти кнопку прибрав"): без
            # цього прапорця _continue_antiseptic_operation_step показує
            # "Редактировать" (чатовий шлях) замість "Вернуться в форму" -
            # той самий прапорець, що вже має sale/income/writeoff.
            "_from_webapp_form": True,
        }
        for key in ("client", "address", "volume", "price_per_unit", "payment_method"):
            value = submitted.get(key)
            if isinstance(value, str):
                value = value.strip()
            if value not in (None, ""):
                payload[key] = value
        # category_operation_id/breed/rows - antiseptic_sheet_values їх
        # НІКОЛИ не читає (лише volume/price/client/address/payment), несемо
        # їх у payload ЩОБ "Вернуться в форму" міг відновити ту саму
        # категорію/розмір при поверненні (_webapp_antiseptic_form_context
        # resume_payload нижче) - І (аудит коду, 2026-08-16) щоб самим
        # перерахувати volume нижче, а не сліпо довіряти клієнту.
        if submitted.get("category_operation_id") is not None:
            payload["category_operation_id"] = submitted.get("category_operation_id")
        if submitted.get("breed") not in (None, ""):
            payload["breed"] = submitted.get("breed")
        if submitted.get("rows"):
            payload["rows"] = submitted.get("rows")
        # Реальна знахідка (аудит коду, 2026-08-16): раніше payload["volume"]
        # ЦІЛКОМ був числом, надісланим браузером (лінія вище, ключ "volume")
        # - жодного перерахунку з розмірів, на відміну від _continue_
        # antiseptic_all_in_one_multi_position нижче, яка ЗАВЖДИ рахує
        # об'єм сама з thickness×width×length×quantity. Сума платежу прямо
        # залежить від volume × price_per_unit - зламаний/змінений клієнт
        # (не анонімна атака - вимагає вже авторизованого SALE_CREATE
        # користувача, що свідомо втручається в код сторінки) міг надіслати
        # довільний volume. Той самий перерахунок тут, коли rows реально є
        # (одиночна позиція з picker'ом розміру, той самий формат рядка, що
        # й у multi-position) - для рідкісного випадку "rows немає взагалі"
        # (напр. дуже старий кеш форми) лишається клієнтське значення, а не
        # падіння операції.
        rows = payload.get("rows")
        if rows:
            row = rows[0] if isinstance(rows, list) else rows
            thickness = _number_value(row.get("thickness"))
            width = _number_value(row.get("width"))
            length = _number_value(row.get("length"))
            quantity = _number_value(row.get("quantity"))
            if thickness and width and length and quantity:
                recomputed_volume = round(thickness * width * length / 1e9 * quantity, 6)
                if recomputed_volume > 0:
                    payload["volume"] = recomputed_volume
        return self._prepend_reply_text(
            "Антисептирование.", self._continue_antiseptic_operation_step(store, context, payload)
        )

    # Кілька дощок в ОДНІЙ послузі (Задача користувача, "як у продажі
    # пиломатеріалу"): antiseptic_sheet_values не має колонок товару/
    # розміру взагалі (лише volume/price/client/address/payment), тож усі
    # позиції зводяться в ОДИН підсумковий volume/total_amount (справжня
    # ціна за м3 у листі виходить середньозваженою - той самий price_per_
    # unit/total_amount "або/або" механізм, що вже має apply_antiseptic_
    # operation). Розбивка по кожній дошці не губиться - лишається текстом
    # у _position_lines, який _antiseptic_preview друкує окремими блоками.
    def _continue_antiseptic_all_in_one_multi_position(self, store, context, submitted, positions_data):
        resolved = []
        for position in positions_data:
            if not isinstance(position, dict):
                continue
            operation_id = position.get("category_operation_id")
            operation = store.get_operation(operation_id) if operation_id is not None else None
            if operation is None:
                return self._with_main_menu(
                    "Не удалось определить категорию одной из позиций антисептирования. Начните заново.", store
                )
            _op_id, _code, _kind, _requires_identity, label, _parent, _prefill_json, *_rest = operation
            row = (position.get("rows") or [{}])[0]
            volume = round(
                (_number_value(row.get("thickness")) * _number_value(row.get("width")) * _number_value(row.get("length")))
                / 1e9 * _number_value(row.get("quantity")),
                6,
            )
            if volume <= 0:
                return self._with_main_menu(
                    "Не удалось рассчитать объём одной из позиций антисептирования. "
                    "Проверьте размер и количество и начните заново.",
                    store,
                )
            price = _number_value(position.get("price_per_unit"))
            resolved.append({
                "operation_id": operation_id,
                "label": label,
                "breed": position.get("breed"),
                "row": row,
                "volume": volume,
                "price_per_unit": price,
                "total_amount": round(volume * price, 2),
            })
        if not resolved:
            return self._with_main_menu(
                "Не удалось определить ни одной позиции антисептирования. Начните заново.", store
            )

        total_volume = round(sum(entry["volume"] for entry in resolved), 6)
        total_amount = round(sum(entry["total_amount"] for entry in resolved), 2)

        position_lines = []
        for entry in resolved:
            if position_lines:
                position_lines.append("")
            header = entry["label"] + (f" / {entry['breed']}" if entry.get("breed") else "")
            position_lines.append(header)
            row = entry["row"]
            position_lines.append(
                f"{_display_bot_number(row.get('thickness'))}x{_display_bot_number(row.get('width'))}x"
                f"{_display_bot_number(row.get('length'))} — {_display_bot_number(row.get('quantity'))} шт — "
                f"{_display_bot_number(entry['volume'])} м3"
            )
            if entry["price_per_unit"]:
                position_lines.append(
                    f"Цена: {_display_bot_number(entry['price_per_unit'])} MDL/м3 — "
                    f"Сумма: {_display_bot_number(entry['total_amount'])} MDL"
                )

        payload = {
            "operation_kind": "antiseptic",
            "original_text": "",
            "user": {
                "id": context["user_id"],
                "username": context["username"],
                "full_name": context["full_name"],
            },
            "confirmed_new": [],
            "_from_webapp_form": True,
            "volume": total_volume,
            "total_amount": total_amount,
            "_position_lines": position_lines,
            # "Вернуться в форму" (resume) - весь кошик, той самий принцип,
            # що вже реалізований для продажу (_build_sale_resume_cart).
            "_resume_positions": [
                {
                    "category_operation_id": entry["operation_id"],
                    "breed": entry.get("breed"),
                    "rows": [entry["row"]],
                    "price_per_unit": entry["price_per_unit"],
                }
                for entry in resolved
            ],
        }
        for key in ("client", "address", "payment_method"):
            value = submitted.get(key)
            if isinstance(value, str):
                value = value.strip()
            if value not in (None, ""):
                payload[key] = value
        return self._prepend_reply_text(
            "Антисептирование.", self._continue_antiseptic_operation_step(store, context, payload)
        )
