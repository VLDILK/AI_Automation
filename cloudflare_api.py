"""Тонкий клієнт REST API Cloudflare - Задача користувача (2026-08-20):
"хочу інформацію про тунель бачити в домашці... додай змогу приєднувати чи
від'єднувати куплені посилання".

Чому саме API, а не cloudflared.exe (перший, відкинутий варіант):
  - `cloudflared tunnel info` вміє ЛИШЕ показати конектори, а на приєднання
    адреси має `tunnel route dns`; команди ВИДАЛИТИ маршрут у нього немає
    взагалі (перевірено по його ж --help: підкоманди тільки dns/lb/ip).
    Тобто "від'єднати" без DNS API неможливе в принципі.
  - сам cloudflared.exe важить 54 МБ і НАВМИСНО не входить у збірку
    домашньої програми (build_exe.py: "БЕЗ webapp/icons/cloudflared.exe"),
    а його cert.pem є лише на тій машині, де колись робили `tunnel login`.

Той самий "дурний" стиль, що й в інших мережевих модулях проєкту
(github_releases.py, remote_control_client.py) - лише urllib зі стандартної
бібліотеки, жодних нових залежностей.

Контракт усіх функцій однаковий: повертають (результат, помилка), де
помилка - готовий до показу рядок або None. Виняток назовні не летить
ніколи: викликач (gui.py) малює текст помилки в тому ж місці, де мали б
бути дані, і не має обкладати кожен виклик своїм try.

Токен у текст помилки НЕ потрапляє ніколи - навіть коли Cloudflare
повертає його шматок у своїй відповіді.
"""

import json
import re
import urllib.error
import urllib.request

API_ROOT = "https://api.cloudflare.com/client/v4"
_TIMEOUT = 15

# Cloudflare позначає тунельні CNAME саме цим суфіксом: <tunnel-id>.cfargotunnel.com.
# За ним відрізняємо "адреси, прив'язані до тунелів" від решти DNS-записів
# зони (пошта, перевірки власності тощо), яких чіпати не можна.
TUNNEL_CNAME_SUFFIX = ".cfargotunnel.com"


def _request(path, token, method="GET", payload=None, timeout=_TIMEOUT, with_status=False):
    """Повертає (розібраний JSON, помилка), а з with_status=True - ще й код
    HTTP-відповіді. Код потрібен рівно в одному місці (verify_token), щоб
    відрізнити 401 "токен недійсний" від 403 "токен живий, але без прав" -
    за текстом повідомлення Cloudflare це не розрізняється."""
    url = path if path.startswith("http") else f"{API_ROOT}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Cloudflare і на помилку віддає свій JSON з errors[].message -
        # він конкретніший за код статусу ("Invalid API Token" замість
        # просто 403), тож пробуємо прочитати саме його.
        try:
            body = json.loads(exc.read().decode("utf-8"))
            message = "; ".join(
                str(error.get("message")) for error in body.get("errors", []) if error.get("message")
            )
        except (ValueError, OSError):
            message = ""
        if exc.code == 401:
            text = message or "Токен недійсний або відкликаний."
        elif exc.code == 403:
            text = message or "Токену бракує прав на цю дію."
        else:
            text = message or f"Cloudflare відповів помилкою {exc.code}."
        return (None, text, exc.code) if with_status else (None, text)
    except (urllib.error.URLError, OSError) as exc:
        text = f"Немає зв'язку з Cloudflare: {exc.reason if hasattr(exc, 'reason') else exc}"
        return (None, text, 0) if with_status else (None, text)
    except ValueError:
        text = "Cloudflare відповів не-JSON."
        return (None, text, 0) if with_status else (None, text)
    if not isinstance(body, dict) or not body.get("success"):
        message = "; ".join(
            str(error.get("message")) for error in (body or {}).get("errors", []) if error.get("message")
        )
        text = message or "Cloudflare відхилив запит."
        return (None, text, 200) if with_status else (None, text)
    return (body.get("result"), None, 200) if with_status else (body.get("result"), None)


# Токен Cloudflare - рівно 40 символів з [A-Za-z0-9_-]. Це не здогадка
# про формат "на око": усе, що не таке, Cloudflare відкидає з тим самим
# невиразним "Invalid API Token", що й справді відкликаний токен. Ловимо
# локально й кажемо, ЩО саме не так.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{40}$")


def describe_token_shape(token):
    """None = форма правильна. Інакше - готове пояснення для людини."""
    raw = token or ""
    token = raw.strip()
    if not token:
        return "Токен не вказано."
    if _TOKEN_RE.match(token):
        return None
    if len(token) != 40:
        return (
            f"Схоже, скопійовано не весь токен: {len(token)} символів, а має бути рівно 40. "
            "Скопіюйте рядок цілком із зеленої рамки після «Create Token»."
        )
    bad = sorted({character for character in token if not re.match(r"[A-Za-z0-9_-]", character)})
    if bad:
        shown = " ".join("пробіл" if character == " " else character for character in bad[:5])
        return f"У токені є зайві символи ({shown}) - скопійовано разом із чимось стороннім."
    return None


def verify_token(token):
    """(активний?, помилка). Окремий ендпоінт саме для перевірки токена -
    відповідає навіть тоді, коли токен не має жодних інших прав."""
    token = (token or "").strip()
    shape_problem = describe_token_shape(token)
    if shape_problem:
        return False, shape_problem

    result, error, code = _request("/user/tokens/verify", token, with_status=True)
    if not error:
        status = (result or {}).get("status")
        if status != "active":
            return False, f"Токен неактивний (статус: {status or 'невідомий'})."
        return True, None

    # /user/tokens/verify - user-рівневий ендпоінт, і токен БЕЗ user-прав
    # отримує від нього відмову, хоч сам живий і Active. Тому падіння тут
    # ще нічого не доводить: питаємо те, що токен має вміти для цього вікна.
    _zones, zones_error, zones_code = _request("/zones?per_page=1", token, with_status=True)
    if not zones_error:
        return True, None
    _accounts, accounts_error, accounts_code = _request("/accounts?per_page=1", token, with_status=True)
    if not accounts_error:
        return True, None

    # 401 бодай десь - токен справді не той. 403 усюди - токен живий, просто
    # порожній для наших потреб, і сказати треба РІВНО це.
    if 401 in (code, zones_code, accounts_code):
        return False, (
            "Cloudflare не впізнав цей токен. Перевірте, що натиснули «Create Token» "
            "(а не лише «Review token»), і що в токені не ввімкнено обмеження за IP."
        )
    return False, (
        "Токен живий, але не має жодного потрібного читання. У Cloudflare відкрийте цей токен "
        "і додайте: Zone → Zone → Read і Account → Cloudflare Tunnel → Read. "
        "Саме значення токена від редагування не зміниться."
    )


def list_accounts(token):
    result, error = _request("/accounts", token)
    if error:
        return [], error
    return [
        {"id": item.get("id"), "name": item.get("name")}
        for item in (result or [])
        if item.get("id")
    ], None


def list_zones(token):
    """Зони = домени, ВЖЕ додані в цей акаунт Cloudflare. Створити DNS-запис
    можна лише в них - свіжокуплений домен спершу треба завести в Cloudflare
    і перевести на його сервери імен, кнопкою це не робиться."""
    result, error = _request("/zones?per_page=50", token)
    if error:
        return [], error
    return [
        {"id": item.get("id"), "name": item.get("name")}
        for item in (result or [])
        if item.get("id") and item.get("name")
    ], None


def list_tunnels(token, account_id):
    result, error = _request(f"/accounts/{account_id}/cfd_tunnel?is_deleted=false", token)
    if error:
        return [], error
    return [
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "created_at": item.get("created_at"),
            "connections": len(item.get("connections") or []),
        }
        for item in (result or [])
        if item.get("id")
    ], None


def list_connectors(token, account_id, tunnel_id):
    """Активні конектори тунелю. ДЕКІЛЬКА конекторів з РІЗНИМИ origin_ip -
    це і є "дві машини на одній адресі": Cloudflare роздає запити між ними
    по колу, тож та сама адреса відповідає то одним ПК, то іншим."""
    result, error = _request(f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/connections", token)
    if error:
        return [], error
    connectors = []
    for item in result or []:
        edges = [
            conn.get("colo_name")
            for conn in (item.get("conns") or [])
            if conn.get("colo_name")
        ]
        connectors.append({
            "id": item.get("id") or "",
            "origin_ip": item.get("origin_ip") or "",
            "opened_at": item.get("run_at") or "",
            "edges": edges,
        })
    return connectors, None


def list_tunnel_addresses(token, zone_id):
    """Лише ті записи зони, що вказують на тунель (CNAME на
    <id>.cfargotunnel.com). Решту DNS-записів навмисно не показуємо й не
    даємо чіпати - вони не стосуються цієї програми, а видалити чужий
    MX-запис через "Від'єднати" було б катастрофою."""
    result, error = _request(f"/zones/{zone_id}/dns_records?type=CNAME&per_page=100", token)
    if error:
        return [], error
    addresses = []
    for item in result or []:
        content = item.get("content") or ""
        if not content.endswith(TUNNEL_CNAME_SUFFIX):
            continue
        addresses.append({
            "record_id": item.get("id"),
            "hostname": item.get("name"),
            "tunnel_id": content[: -len(TUNNEL_CNAME_SUFFIX)],
            "proxied": bool(item.get("proxied")),
        })
    return addresses, None


def attach_address(token, zone_id, hostname, tunnel_id):
    """Створює CNAME hostname -> <tunnel_id>.cfargotunnel.com. Те саме, що
    робить `cloudflared tunnel route dns`, лише без самого cloudflared.
    proxied=True обов'язково: без "помаранчевої хмарки" тунельний CNAME не
    працює взагалі."""
    payload = {
        "type": "CNAME",
        "name": hostname,
        "content": f"{tunnel_id}{TUNNEL_CNAME_SUFFIX}",
        "proxied": True,
        "comment": "AI_Automation: приєднано з домашньої програми",
    }
    result, error = _request(f"/zones/{zone_id}/dns_records", token, method="POST", payload=payload)
    if error:
        return None, error
    return result, None


def detach_address(token, zone_id, record_id):
    """Видаляє DNS-запис. Незворотно в тому сенсі, що бот і форма за цією
    адресою перестають відповідати ОДРАЗУ - тому в gui.py воно закрите
    підтвердженням із введенням повного імені хоста, а не простим "Так"."""
    result, error = _request(f"/zones/{zone_id}/dns_records/{record_id}", token, method="DELETE")
    if error:
        return False, error
    return True, None


def list_registrar_domains(token, account_id):
    """Домени, куплені саме через Cloudflare Registrar: тут лежить
    авторитетний auto_renew (увімкнене автопродовження чи ні) - RDAP такого
    не знає взагалі. Домен, зареєстрований в іншого реєстратора, сюди просто
    не потрапить, і це не помилка."""
    result, error = _request(f"/accounts/{account_id}/registrar/domains", token)
    if error:
        return {}, error
    domains = {}
    for item in result or []:
        name = (item.get("name") or "").lower()
        if not name:
            continue
        domains[name] = {
            "auto_renew": bool(item.get("auto_renew")),
            "expires_at": item.get("expires_at") or "",
            "locked": bool(item.get("locked")),
        }
    return domains, None


def probe_permissions(token):
    """Токен майже ніколи не має права читати ВЛАСНИЙ список прав (це
    окремий дозвіл, який зазвичай не видають), тож замість обіцянок робимо
    два нешкідливі читання й кажемо, що реально працює. Право ЗАПИСУ в DNS
    так перевірити не можна - воно підтвердиться на першому справжньому
    приєднанні, і саме там помилка буде показана прямим текстом."""
    zones, zones_error = list_zones(token)
    accounts, accounts_error = list_accounts(token)
    tunnels_ok = False
    if accounts:
        _tunnels, tunnels_error = list_tunnels(token, accounts[0]["id"])
        tunnels_ok = tunnels_error is None
    return {
        "zones_ok": zones_error is None,
        "zones_count": len(zones),
        "accounts_ok": accounts_error is None,
        "tunnels_ok": tunnels_ok,
    }
