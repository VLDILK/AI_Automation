"""Дерево кастомних кнопок меню: навігація, диспетчеризація вузлів, ДАННЫЕ/СКЛАД/ПРОДАЖИ підменю. Частина розбиття telegram_dialog.py - див. telegram_dialog.py для повної карти."""

import json

import permissions as perm
from utils import (
    _normalize_phrase,
)
from warehouse_data import (
    BOT_MESSAGE_DEFAULTS,
)

class MenuDialogMixin:

    # Усі 5 колишніх пунктів головного меню (ПРИХОД/РЕАЛИЗАЦИЯ/ДАННЫЕ/
    # Калькулятор/Помощь) більше НЕ хардкоджені тут — Задача користувача
    # (переносити пункти меню в редактор по одному, "ті що є всередині —
    # вимкни і додай у редактор"): усі тепер звичайні кнопки в
    # custom_menu_buttons, сіються автоматично (BUILTIN_MIGRATED_CUSTOM_
    # BUTTONS/_seed_builtin_migrated_custom_buttons, warehouse_data.py) —
    # тож store=None (гіпотетичний виклик без доступу до БД) лишає меню
    # ПОРОЖНІМ (жодного пункту).
    def _main_command_keyboard(self, store=None):
        rows = []
        if store is not None:
            rows.extend(self._pack_custom_button_rows(store.list_custom_buttons(None), store))
        return {
            "keyboard": rows,
            "resize_keyboard": True,
            "one_time_keyboard": False,
        }

    def _custom_button_row_to_node(self, row):
        node_id, label, message_text, action_code, section, enabled, layout, operation_id = row
        return {
            "id": node_id,
            "label": label,
            "message_text": message_text,
            "action_code": action_code,
            "section": section,
            "enabled": enabled,
            "layout": layout,
            "operation_id": operation_id,
        }

    # get_custom_button (warehouse_data.py) повертає parent_id ДРУГИМ полем
    # (на відміну від list_custom_buttons, де parent_id взагалі не в рядку —
    # він уже відомий з аргументу виклику) — окремий конвертер, щоб не
    # плутати форму двох різних SELECT-ів.
    def _custom_button_full_row_to_node(self, row):
        node_id, parent_id, label, message_text, action_code, section, enabled, layout, operation_id = row
        return {
            "id": node_id,
            "parent_id": parent_id,
            "label": label,
            "message_text": message_text,
            "action_code": action_code,
            "section": section,
            "enabled": enabled,
            "layout": layout,
            "operation_id": operation_id,
        }

    # Розмір кнопки в клавіатурі (Задача користувача): "full" — суцільна, на
    # весь рядок; "half" — вдвічі менша, паруються ДВІ СУСІДНІ (за порядком
    # position) половинні кнопки в ОДИН рядок клавіатури. Немає окремого
    # "боку" — ліва/права визначається виключно порядком position: перша
    # (менший position) завжди йде зліва, друга — справа (Задача користувача:
    # "все буде визначатись індексом" — раніше був окремий вибір half_left/
    # half_right, тепер положення повністю задає позиція, яку й так можна
    # обрати випадаючим списком у формі). Непарна половинна кнопка (без пари
    # поруч — наприклад три половинні підряд, третя без сусіда) показується
    # сама на весь рядок, а не ламає клавіатуру.
    # Задача користувача: тап по кнопці головного меню має ОДРАЗУ відкривати
    # Mini App, без проміжного повідомлення-посередника з окремою кнопкою.
    # Telegram підтримує це для звичайної reply-кнопки клавіатури через
    # web_app - тому "РЕАЛИЗАЦИЯ (форма)" сама несе посилання, якщо тунель
    # уже піднятий (self.webapp_public_url). Якщо ні - звичайна текстова
    # кнопка, тап якою веде в _start_sale_all_in_one_reply (fallback,
    # показує той самий проміжний крок, як і раніше).
    # ТИМЧАСОВО вимкнено (2026-08-08, реальний баг живого тестування):
    # мега-форма (усі 4 категорії + антисептик разом) дає base64-JSON-
    # контекст на кілька тисяч символів - вбудований НАПРЯМУ в саму
    # кнопку головного меню (web_app.url), він потрапляє в АБСОЛЮТНО
    # кожне повідомлення з головним меню, і настільки довгий URL, вочевидь,
    # відхиляється Telegram при відправці (sendMessage падає ПІСЛЯ того, як
    # _build_reply_pipeline уже успішно полічив і залогував відповідь, тож
    # у "Журналі дій" усе виглядає "Виконано", а в чаті людина щоразу
    # бачить лише загальну "Произошла внутренняя ошибка" - на КОЖНЕ
    # наступне повідомлення, бо головне меню з цією ж кнопкою показується
    # знову і знову). Доки контекст мега-форми не стиснутий (напр. без
    # дублювання метаданих полів на кожну категорію), кнопка лишається
    # ЗВИЧАЙНОЮ текстовою - тап веде через старий, безпечний двокроковий
    # шлях (окреме повідомлення з формою, надсилається лише за запитом,
    # а не на кожен показ головного меню).
    def _button_dict_for_node(self, node, store):
        return {"text": node["label"]}

    def _pack_custom_button_rows(self, rows_data, store=None):
        keyboard_rows = []
        pending_half = None
        for row in rows_data:
            node = self._custom_button_row_to_node(row)
            if node["layout"] == "half":
                if pending_half is not None:
                    keyboard_rows.append(
                        [self._button_dict_for_node(pending_half, store), self._button_dict_for_node(node, store)]
                    )
                    pending_half = None
                else:
                    pending_half = node
            else:
                if pending_half is not None:
                    keyboard_rows.append([self._button_dict_for_node(pending_half, store)])
                    pending_half = None
                keyboard_rows.append([self._button_dict_for_node(node, store)])
        if pending_half is not None:
            keyboard_rows.append([self._button_dict_for_node(pending_half, store)])
        return keyboard_rows

    # Пошук кореневої кастомної кнопки за натиснутим текстом — лише коли
    # немає активної pending-операції (щоб не заважати вже наявним
    # хардкодженим кнопкам/діалогам).
    def _custom_root_button_by_label(self, text, store):
        normalized = _normalize_phrase(text)
        if not normalized:
            return None
        for row in store.list_custom_buttons(None):
            node = self._custom_button_row_to_node(row)
            if _normalize_phrase(node["label"]) == normalized:
                return node
        return None

    # Каталог CUSTOM_BUTTON_ACTIONS (warehouse_data.py) — лише дані; сам
    # виклик потрібної функції TelegramDialogMixin живе тут, за тим самим
    # стилем if-ланцюжка, що й command_code вище (_build_reply).
    def _custom_button_action_reply(self, action_code, store, context):
        if action_code == "start_income":
            return self._start_income_category_menu(store, context)
        if action_code == "start_income_form":
            return self._start_income_all_in_one_reply(store, context)
        if action_code == "start_sale":
            return self._start_sale_payment_method_menu(store, context)
        if action_code == "start_sale_form":
            return self._start_sale_all_in_one_reply(store, context)
        if action_code == "start_antiseptic_form":
            return self._start_antiseptic_all_in_one_reply(store, context)
        if action_code == "start_stock_report":
            return self._stock_data_menu_reply(store, context)
        if action_code == "start_sales_report":
            return self._start_sales_report_reply(store, context)
        if action_code == "start_antiseptic_report":
            return self._start_antiseptic_report_reply(store, context)
        if action_code == "start_sales_by_client_report":
            return self._start_sales_by_client_report_reply(store, context)
        if action_code == "start_low_stock_report":
            return self._start_low_stock_report_reply(store, context)
        if action_code == "start_data_browser_form":
            return self._start_data_browser_reply(store, context)
        if action_code == "start_writeoff":
            return self._start_writeoff_operation(store, context)
        if action_code == "start_writeoff_form":
            return self._start_writeoff_all_in_one_reply(store, context)
        if action_code == "start_calculator":
            return self._start_calculator_operation("калькулятор", store, context)
        if action_code == "show_help":
            return self._show_help_reply(store)
        return None

    # Клавіатура дочірніх кнопок конкретного вузла + рядок Назад/Главное
    # меню (той самий трейлер, що й _back_and_main_menu_keyboard, тут одразу
    # об'єднаний з рядками дітей в одну клавіатуру).
    def _custom_menu_keyboard(self, children_rows):
        rows = self._pack_custom_button_rows(children_rows)
        rows.append([{"text": "Назад"}, {"text": "Главное меню"}])
        return {"keyboard": rows, "resize_keyboard": True, "one_time_keyboard": True}

    # Вхід у кастомну кнопку (кореневу чи дочірню). "Дія" і "діти" —
    # взаємовиключні на рівні відображення (Задача користувача, ескіз
    # "або/або"): якщо в кнопки з'явилась хоч одна дочірня, її власний
    # action_code просто ігнорується — перевірка дітей йде ПЕРШОЮ.
    # re_entering=True — повторний показ клавіатури БАТЬКА після "Назад",
    # без повторного показу message_text (він уже був показаний один раз).
    def _enter_custom_button_node(self, node, store, context, re_entering=False):
        # Крок 4.2: РІВНО для 7 уже мігрованих кореневих вузлів (migration_key
        # — ПРИХОД/РЕАЛИЗАЦИЯ/ДАННЫЕ/СКЛАД/ПРОДАЖИ/Калькулятор/Помощь)
        # message_text ІГНОРУЄМО — для НИХ це не окрема кастомна кнопка, а
        # сама точка входу в стандартну дію, і її текст завжди йде з
        # bot_message_templates (той самий механізм, доступний однаково і
        # з дерева, і з ~15 інших шляхів виклику цих функцій — легасі-команди,
        # "Назад" тощо). Для БУДЬ-ЯКОГО ІНШОГО (справжнього кастомного) вузла
        # — навіть якщо в нього є action_code з одного з цих 6 — message_text
        # лишається власним, окремим і показується як і раніше: такий вузол
        # це навмисний ярлик/сценарій адміна ("Запускаем продажу." перед
        # запуском start_sale), а не дублювання того самого тексту.
        own_text = node["message_text"] if not store.is_custom_button_migrated(node["id"]) else None
        children_rows = store.list_custom_buttons(node["id"])
        if children_rows:
            store.save_pending_operation(
                context["chat_id"], context["user_id"], "custom_menu", "at_node", {"node_id": node["id"]},
            )
            reply = {
                "type": "message",
                "text": "Выберите пункт:",
                "reply_markup": self._custom_menu_keyboard(children_rows),
            }
            if own_text and not re_entering:
                return self._prepend_reply_text(own_text, reply)
            return reply

        # Крок 4.3: пряме посилання на КОНКРЕТНУ дію з "Дії" (ДОСКА AD і
        # т.д.) — взаємовиключне з action_code на рівні GUI-форми (радіо-
        # вибір), тож перевіряти можна в будь-якому порядку; тут — одразу
        # після дітей, як і action_code.
        if node.get("operation_id") is not None:
            operation_reply = self._start_operation_leaf(node["operation_id"], store, context)
            if operation_reply is not None:
                if own_text:
                    return self._prepend_reply_text(own_text, operation_reply)
                return operation_reply

        if node["action_code"]:
            action_reply = self._custom_button_action_reply(node["action_code"], store, context)
            if action_reply is not None:
                if own_text:
                    return self._prepend_reply_text(own_text, action_reply)
                return action_reply

        # Лист без дітей і без дії: якщо є батько — повертаємось до ЙОГО
        # клавіатури (не до головного меню) через рекурсивний виклик, щоб
        # не дублювати відображення клавіатури дітей в двох місцях.
        parent_id = node.get("parent_id")
        if parent_id:
            parent_row = store.get_custom_button(parent_id)
            if parent_row:
                parent_node = self._custom_button_full_row_to_node(parent_row)
                reply = self._enter_custom_button_node(parent_node, store, context, re_entering=True)
                return self._prepend_reply_text(own_text, reply) if own_text else reply
        return self._with_main_menu(own_text or "Ок.", store)

    # Крок 4.3: запуск КОНКРЕТНОЇ дії (bot_operations), на яку кастомна
    # кнопка посилається напряму (operation_id), в обхід зношеного
    # action_code+_category_from_text механізму. Будує стартовий payload
    # так само, як і _apply_category_change для СВІЖОГО вибору категорії
    # (product/condition з prefill_json), і одразу передає в
    # continue_income_operation/continue_sale_operation — той самий рушій
    # збору/валідації/підтвердження, що й натискання хардкодженої кнопки
    # категорії. kind="service" (антисептирование) не має власного product/
    # condition — веде напряму в _start_antiseptic_operation (той сам
    # перевіряє дозвіл).
    def _start_operation_leaf(self, operation_id, store, context):
        operation = store.get_operation(operation_id)
        if operation is None:
            return None
        (
            _operation_id, _code, kind, _requires_identity, label, _parent_action_code,
            prefill_json, _position, enabled, _builtin_key,
        ) = operation
        if not enabled:
            return None
        if kind == "service":
            return self._start_antiseptic_operation(store, context)
        if kind == "writeoff":
            denied = self._require_permission(store, context, perm.WRITEOFF)
            if denied:
                return denied
            new_payload = self._new_income_payload(label, context)
            new_payload["operation_kind"] = "writeoff"
            if prefill_json:
                new_payload.update(json.loads(prefill_json))
            return self._continue_writeoff_operation_impl(store, context, new_payload)
        if kind == "income":
            denied = self._require_permission(store, context, perm.INCOME)
        elif kind == "sale":
            denied = self._require_permission(store, context, perm.SALE_CREATE)
        else:
            return None
        if denied:
            return denied
        new_payload = (
            self._new_sale_payload(label, context) if kind == "sale" else self._new_income_payload(label, context)
        )
        if prefill_json:
            new_payload.update(json.loads(prefill_json))
        continue_operation = self._continue_sale_operation if kind == "sale" else self._continue_income_operation
        return continue_operation(store, context, new_payload)

    # Продовження діалогу всередині дерева кастомних кнопок (pending
    # operation_type "custom_menu", статус "at_node") — шукає серед дітей
    # ПОТОЧНОГО вузла збіг натиснутої мітки, той самий підхід, що й
    # _category_from_text для категорій складу.
    def _continue_custom_menu(self, text, store, context, pending):
        node_id = pending["payload"]["node_id"]
        children_rows = store.list_custom_buttons(node_id)
        normalized = _normalize_phrase(text)
        for row in children_rows:
            child_node = self._custom_button_row_to_node(row)
            if _normalize_phrase(child_node["label"]) == normalized:
                child_node["parent_id"] = node_id
                return self._enter_custom_button_node(child_node, store, context)
        return {
            "type": "message",
            "text": "Не понял, выберите один из пунктов ниже.",
            "reply_markup": self._custom_menu_keyboard(children_rows),
        }

    # ДАННЫЕ — мігрована кнопка-БАТЬКО (BUILTIN_MIGRATED_CUSTOM_BUTTONS,
    # migration_key="data_menu"), її діти СКЛАД/ПРОДАЖИ — теж мігровані
    # (migration_key="stock_report_section"/"sales_report_section", Задача
    # користувача: "ті що є всередині — вимкни і додай у редактор"). Раніше
    # ДАННЫЕ мала власну хардкоджену клавіатуру й pending-операцію
    # ("data_menu"/"choose_section") — тепер вхід у неї відбувається через
    # ЗАГАЛЬНИЙ механізм дерева кастомних кнопок (_enter_custom_button_node
    # / pending "custom_menu"), як і для будь-якої іншої гілки: показує
    # ПОТОЧНИЙ стан дітей (з урахуванням перейменувань/розміру/порядку/
    # вимкнення через Редактор кнопок), а не застарілий хардкод.
    # _is_data_menu_request (нижче) лишається як хардкоджений alias
    # ("данные"/"data") для випадків, коли текст набрано вручну, а не
    # натиснуто кнопку, — і для "Назад" з глибини СКЛАД/ПРОДАЖИ (де досі
    # немає загального механізму "крок до батька" для звітних потоків,
    # лише прямий перехід на верхній рівень ДАННЫЕ).
    def _enter_data_menu_node(self, store, context, re_entering=False):
        row = store.get_custom_button_by_migration_key("data_menu")
        if row is None:
            return self._main_menu_reply(store)
        return self._enter_custom_button_node(
            self._custom_button_full_row_to_node(row), store, context, re_entering=re_entering,
        )

    # СКЛАД (під ДАННЫЕ) — категорії + ВСЕ ТОВАРЫ + Фильтры. Той самий
    # operation_type "stock_browse", що й автономний перегляд за фільтром
    # (Фаза A, п.5) — лише новий статус "choose_stock_category" перед ним,
    # щоб відрізнити ці самі кнопки категорій від ПРИХОД/РЕАЛИЗАЦИЯ (п.1).
    # Кнопки лише російською (жодних латинських слів) — "ОСБ" замість "OSB".
    def _stock_data_keyboard(self):
        return {
            "keyboard": [
                [{"text": "ДОСКА AD"}, {"text": "ДОСКА KD"}],
                [{"text": "ОСБ"}, {"text": "ВАГОНКА"}],
                [{"text": "ВСЕ ТОВАРЫ"}],
                [{"text": "Фильтры"}],
                [{"text": "Назад"}, {"text": "Главное меню"}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": False,
        }

    def _stock_data_menu_reply(self, store, context):
        denied = self._require_permission(store, context, perm.WAREHOUSE_VIEW)
        if denied:
            return denied
        store.save_pending_operation(
            context["chat_id"],
            context["user_id"],
            "stock_browse",
            "choose_stock_category",
            {},
        )
        return {
            "type": "message",
            "text": store.get_message_template("start_stock_report", BOT_MESSAGE_DEFAULTS["start_stock_report"]),
            "reply_markup": self._stock_data_keyboard(),
        }

    # Категорія -> готовий фільтр для _stock_balance_reply. OSB розпізнаю
    # через уже наявну _is_osb_product (враховує кирилицю/латиницю) — словник
    # PRODUCT_CATEGORIES у warehouse_data.py має ключ "осб" кирилицею, тоді
    # як кнопка тут — латиницею "OSB", тож пряме звернення туди хибило б.
    def _stock_category_filter(self, text):
        if self._is_osb_product(text):
            return "ОСБ"
        normalized = _normalize_phrase(text)
        mapping = {
            "доска ad": "Доска AD",
            "доска kd": "Доска KD",
            "вагонка": "Вагонка",
        }
        return mapping.get(normalized)

    def _is_data_menu_request(self, text):
        return _normalize_phrase(text) in {"данные", "data"}

    # "Склад" — легасі-псевдонім. У п.1 Фази B вів на ДАННЫЕ (тоді СКЛАД як
    # окремий розділ ще не існував); тепер, коли є справжній розділ СКЛАД,
    # семантично точніше вести напряму туди.
    def _is_stock_data_menu_request(self, text):
        return _normalize_phrase(text) in {"склад", "warehouse"}
