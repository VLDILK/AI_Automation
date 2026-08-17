"""Онлайн-джерело таблиці Excel: OneDrive/SharePoint через Microsoft Graph.

Задача користувача: "додай змогу додавати таблицю ексель до роботи. можна
як локальний так і онлайн" + "мені потрібно щоб це було просто для
користувача у программі" — вхід через device-code flow (MSAL): браузер
відкривається один раз, код вводиться на сторінці Microsoft, після цього
токен кешується (paths.MSAL_TOKEN_CACHE_PATH) і наступні запуски входу НЕ
потребують — get_access_token_silent() тихо оновлює токен.

CLIENT_ID/TENANT_ID — з ВЛАСНОЇ реєстрації застосунку користувача в Azure AD
(Microsoft Entra): публічний клієнт (без client secret), "Allow public
client flows" = Yes, delegated-дозвіл Files.ReadWrite. Плейсхолдери нижче
заповнюються реальними значеннями, щойно користувач їх надішле.
"""

import base64

import msal
import requests

from paths import MSAL_TOKEN_CACHE_PATH

# TODO: замінити на реальні значення з Azure-реєстрації користувача.
CLIENT_ID = ""
TENANT_ID = "common"

SCOPES = ["Files.ReadWrite"]
GRAPH_ROOT = "https://graph.microsoft.com/v1.0"


def _load_cache():
    cache = msal.SerializableTokenCache()
    if MSAL_TOKEN_CACHE_PATH.exists():
        try:
            cache.deserialize(MSAL_TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return cache


def _save_cache(cache):
    if not cache.has_state_changed:
        return
    # Свіжий пере-аудит (2026-08-02, Notable #8): запис кешу токена -
    # best-effort персистентність (той самий принцип, що й у main.py's
    # offset-файлі) - невдалий запис лише означає повторний вхід наступного
    # разу, не критичну помилку самого входу.
    try:
        MSAL_TOKEN_CACHE_PATH.parent.mkdir(exist_ok=True)
        MSAL_TOKEN_CACHE_PATH.write_text(cache.serialize(), encoding="utf-8")
    except OSError:
        pass


def _msal_app(cache):
    return msal.PublicClientApplication(
        CLIENT_ID, authority=f"https://login.microsoftonline.com/{TENANT_ID}", token_cache=cache,
    )


# Свіжий пере-аудит (2026-08-02, Notable #8): жоден із 3 викликів MSAL
# нижче не був захищений від сирого винятку бібліотеки (мережева помилка,
# зіпсований кеш тощо) - на відміну від _graph_request нижче, що вже
# конвертує мережеві помилки HTTP-викликів у той самий RuntimeError-
# контракт. Обгортаємо однаково всі 3.
def get_access_token_silent():
    """Тихо повертає токен з уже збереженого входу, або None, якщо
    користувач ще не входив (чи кеш прострочений) — саме це дає "просто
    для користувача": повторний логін не потрібен на кожному запуску."""
    cache = _load_cache()
    app = _msal_app(cache)
    try:
        accounts = app.get_accounts()
        if not accounts:
            return None, None
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
    except Exception as exc:
        raise RuntimeError(f"Помилка входу через Microsoft: {exc}") from exc
    _save_cache(cache)
    if not result or "access_token" not in result:
        return None, None
    return result["access_token"], accounts[0].get("username", "")


def start_device_flow():
    """Повертає (flow, cache) — flow несе user_code/verification_uri для
    показу в GUI; cache передається в complete_device_flow, щоб не губити
    стан між двома викликами."""
    cache = _load_cache()
    app = _msal_app(cache)
    try:
        flow = app.initiate_device_flow(scopes=SCOPES)
    except Exception as exc:
        raise RuntimeError(f"Помилка входу через Microsoft: {exc}") from exc
    if "user_code" not in flow:
        raise RuntimeError(flow.get("error_description", "Не вдалося почати вхід через Microsoft."))
    return flow, cache


def complete_device_flow(flow, cache):
    """Блокує до входу користувача в браузері чи таймауту — викликати в
    окремому потоці (не в головному потоці Tkinter)."""
    app = _msal_app(cache)
    try:
        result = app.acquire_token_by_device_flow(flow)
    except Exception as exc:
        raise RuntimeError(f"Помилка входу через Microsoft: {exc}") from exc
    _save_cache(cache)
    if not result or "access_token" not in result:
        raise RuntimeError((result or {}).get("error_description", "Вхід не завершено."))
    accounts = app.get_accounts()
    username = accounts[0].get("username", "") if accounts else ""
    return result["access_token"], username


def sign_out():
    cache = _load_cache()
    app = _msal_app(cache)
    for account in app.get_accounts():
        app.remove_account(account)
    _save_cache(cache)
    if MSAL_TOKEN_CACHE_PATH.exists():
        MSAL_TOKEN_CACHE_PATH.unlink()


def _encoded_share_url(share_url):
    # Формат, задокументований Microsoft Graph для "shares"-API:
    # "u!" + base64url(вихідний URL) без завершальних "=".
    raw = base64.urlsafe_b64encode(share_url.encode("utf-8")).decode("ascii").rstrip("=")
    return f"u!{raw}"


# Важлива знахідка нового аудиту (28.07.2026, #10): жоден з 3 HTTP-викликів
# нижче не ловив requests.exceptions.RequestException (мережева помилка,
# таймаут, DNS тощо) — сирий виняток requests пройшов би аж до GUI-межі,
# де ловиться лише RuntimeError (той самий контракт, що вже встановлений
# для start_device_flow вище і для excel_source.py по всьому проєкту).
def _graph_request(method, url, **kwargs):
    try:
        response = method(url, **kwargs)
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Помилка з'єднання з Microsoft OneDrive/SharePoint: {exc}") from exc
    return response


def resolve_share_link(access_token, share_url):
    """Повертає (drive_id, item_id, file_name) для посилання, яке
    користувач скопіював у OneDrive/SharePoint ("Копіювати посилання")."""
    encoded = _encoded_share_url(share_url)
    response = _graph_request(
        requests.get,
        f"{GRAPH_ROOT}/shares/{encoded}/driveItem",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    # Свіжий пере-аудит (2026-08-02, Notable #8): _graph_request захищає
    # лише сам HTTP-виклик - "200 OK" з неочікуваною формою тіла (не dict,
    # відсутній ключ) досі давав би сирий KeyError/TypeError тут.
    try:
        data = response.json()
        return data["parentReference"]["driveId"], data["id"], data["name"]
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"Не вдалося розпізнати відповідь Microsoft Graph: {exc}") from exc


def download_workbook_bytes(access_token, drive_id, item_id):
    response = _graph_request(
        requests.get,
        f"{GRAPH_ROOT}/drives/{drive_id}/items/{item_id}/content",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=60,
    )
    return response.content


def upload_workbook_bytes(access_token, drive_id, item_id, data):
    # Проста заміна вмісту — прийнятно для файлів цього розміру; сесія
    # завантаження (>4MB) свідомо поза межами цього кроку.
    _graph_request(
        requests.put,
        f"{GRAPH_ROOT}/drives/{drive_id}/items/{item_id}/content",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/octet-stream",
        },
        data=data,
        timeout=60,
    )
