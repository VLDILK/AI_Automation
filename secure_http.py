"""Одне місце для всіх захищених з'єднань програми (2026-09-06).

Живий випадок на робочому ПК: клієнт не міг ані перевірити оновлення на
GitHub («unable to get local issuer certificate»), ані підключити бота до
Telegram («self-signed certificate in certificate chain»), хоча браузер на
тому самому ПК відкривав усе. Python перевіряє сертифікати за сховищем
Windows, а браузер носить власний набір коренів — коли у сховищі Windows
бракує публічних коренів, падає лише Python.

Тому контекст TLS тут довіряє ОДРАЗУ двом джерелам:
1. сховищу Windows (антивірус чи проксі підприємства кладуть свій корінь саме
   туди);
2. вбудованому набору публічних коренів certifi (він уже входить до збірки).

Усі виходи в інтернет (Telegram, GitHub, тунель, Cloudflare) ідуть через
urlopen/build_opener звідси, а не через urllib.request напряму.
"""
import ssl
import urllib.request

_CONTEXT = None


def ssl_context():
    context = ssl.create_default_context()
    try:
        import certifi

        context.load_verify_locations(cafile=certifi.where())
    except Exception:
        # Без certifi лишається сховище Windows - як і було раніше.
        pass
    return context


def context():
    global _CONTEXT
    if _CONTEXT is None:
        _CONTEXT = ssl_context()
    return _CONTEXT


def urlopen(request, data=None, timeout=None):
    if timeout is None:
        return urllib.request.urlopen(request, data, context=context())
    return urllib.request.urlopen(request, data, timeout=timeout, context=context())


def build_opener(*handlers):
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=context()), *handlers)
