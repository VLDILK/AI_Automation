"""Термін дії домену через RDAP - Задача користувача (2026-08-20): "а ще
можливо термін закінчення дії там вивести?".

RDAP - це наступник WHOIS, звичайний HTTPS+JSON, ПУБЛІЧНИЙ: жодного токена,
жодного акаунта. Тому цей блок у вікні "Тунель" працює навіть тоді, коли
токен Cloudflare не вказаний, і взагалі для будь-якого домену, який колись
докуплять.

rdap.org - офіційний перенаправляч IANA: сам знаходить RDAP-сервер потрібної
зони й віддає 302 на нього (для .trade це rdap.nic.trade). urllib іде за
перенаправленням сам, окремого коду не треба.
"""

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

_RDAP_ROOT = "https://rdap.org/domain/"
_TIMEOUT = 20
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

# Відповідь RDAP не змінюється роками, а запит помітно повільніший за все
# інше у вікні - тримаємо в пам'яті на час роботи програми. Ключ - домен.
_CACHE = {}


def registrable_domain(hostname, known_domains=()):
    """bot.botaiautomationeu.trade -> botaiautomationeu.trade.

    known_domains (імена зон із Cloudflare) - ТОЧНЕ джерело: зона і є
    зареєстрований домен. Коли їх нема, лишається груба евристика "останні
    дві частини", яка помиляється на кшталт .co.uk - тому й перевіряємо
    спершу список зон, а не навпаки."""
    hostname = (hostname or "").strip().rstrip(".").lower()
    if not hostname:
        return ""
    for domain in known_domains:
        domain = (domain or "").strip().lower()
        if domain and (hostname == domain or hostname.endswith("." + domain)):
            return domain
    parts = hostname.split(".")
    if len(parts) <= 2:
        return hostname
    return ".".join(parts[-2:])


def _parse_rdap_date(value):
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def fetch_domain_info(domain, timeout=_TIMEOUT):
    """({...}, помилка). Той самий контракт, що й у cloudflare_api - назовні
    виняток не летить, викликач малює текст помилки в рядку домену."""
    domain = (domain or "").strip().lower()
    if not domain:
        return None, "Домен не вказано."
    if domain in _CACHE:
        return _CACHE[domain], None

    request = urllib.request.Request(_RDAP_ROOT + domain, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, "Домен не знайдено в RDAP."
        return None, f"RDAP відповів помилкою {exc.code}."
    except (urllib.error.URLError, OSError) as exc:
        return None, f"Немає зв'язку з RDAP: {getattr(exc, 'reason', exc)}"
    except ValueError:
        return None, "RDAP відповів не-JSON."

    events = {}
    for event in body.get("events") or []:
        action = event.get("eventAction")
        if action:
            events[action] = _parse_rdap_date(event.get("eventDate"))

    registrar = ""
    for entity in body.get("entities") or []:
        if "registrar" not in (entity.get("roles") or []):
            continue
        for item in (entity.get("vcardArray") or [None, []])[1]:
            if isinstance(item, list) and item and item[0] == "fn":
                registrar = str(item[3])
                break

    expires = events.get("expiration")
    days_left = None
    if expires is not None:
        days_left = (expires - datetime.now(timezone.utc)).days

    info = {
        "domain": domain,
        "registrar": registrar,
        "registered": events.get("registration"),
        "expires": expires,
        "days_left": days_left,
    }
    _CACHE[domain] = info
    return info, None


def clear_cache():
    """Для кнопки "Обновить" - інакше термін дії лишався б із першого
    відкриття вікна до перезапуску програми."""
    _CACHE.clear()
