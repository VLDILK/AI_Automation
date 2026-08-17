"""Карта діалогової машини станів Telegram-бота — розбита на 7 файлів за
доменом (2026, задача користувача: "виправ щоб на майбутнє полегшило пошук
також"), кожен `telegram_dialog_*.py`, легко знайти всі одним ґлобом. Цей
файл — тонкий агрегатор: збирає всі міксини в один `TelegramDialogMixin`
множинним успадкуванням (той самий патерн, що вже є в main.py:
`class TelegramBotWorker(TelegramDialogMixin)`), тож `main.py`/`gui.py` не
потребують ЖОДНОЇ зміни — і `TelegramApiError`, і `TelegramDialogMixin`
й далі імпортуються рівно так само, як і раніше.

Де що шукати:
  telegram_dialog_core.py                — вхід/дозволи/"Назад"/спільні хелпери
  telegram_dialog_botmode.py              — ШИ/без ШИ, Claude API, калькулятор
  telegram_dialog_menu.py                 — дерево кастомних кнопок меню
  telegram_dialog_antiseptic.py           — потік "Антисептирование"
  telegram_dialog_reports.py              — звіти продажів/залишків, фільтри складу
  telegram_dialog_income_sale_flow.py     — старт/диспетчер/редагування приходу-продажу
  telegram_dialog_income_sale_parsing.py  — розбір тексту/валідація/нормалізація
  telegram_dialog_writeoff.py             — потік "Списание" (4-й вид операції)

Це чисто структурний розподіл — жодна логіка не змінювалась при перенесенні
(перевірено: 0 дублікатів імен методів, клас не має власного __init__, кожна
модульна константа переїхала рівно в той файл, що її використовує). Виклики
self.якийсь_метод() між доменами працюють ІДЕНТИЧНО, як і до розбиття —
Python шукає метод по MRO екземпляра під час виконання, а не за файлом,
де метод фізично написаний.
"""

from telegram_dialog_core import TelegramApiError, CoreDialogMixin
from telegram_dialog_botmode import BotModeDialogMixin
from telegram_dialog_menu import MenuDialogMixin
from telegram_dialog_antiseptic import AntisepticDialogMixin
from telegram_dialog_reports import ReportsDialogMixin
from telegram_dialog_income_sale_flow import IncomeSaleFlowDialogMixin
from telegram_dialog_income_sale_parsing import IncomeSaleParsingDialogMixin
from telegram_dialog_writeoff import WriteoffDialogMixin

__all__ = ["TelegramApiError", "TelegramDialogMixin"]


class TelegramDialogMixin(
    CoreDialogMixin,
    BotModeDialogMixin,
    MenuDialogMixin,
    AntisepticDialogMixin,
    ReportsDialogMixin,
    IncomeSaleFlowDialogMixin,
    IncomeSaleParsingDialogMixin,
    WriteoffDialogMixin,
):
    """TelegramDialogMixin визначає методи, але сам НЕ є повноцінним ботом —
    його методи покладаються на self.token/self.settings_path/self._api/
    self._excel_sync_mode тощо, які реально існують лише на класі
    TelegramBotWorker (main.py), що успадковує цей мікс:
    `class TelegramBotWorker(TelegramDialogMixin):`."""
