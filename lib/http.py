# -*- coding: utf-8 -*-
"""Тонкая обертка над urllib: одна точка входа для всех запросов конвейера.

Зависимостей нет намеренно - код должен запускаться и локально в venv без pip,
и в GitHub Actions без шага установки.
"""
import json
import mimetypes
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid

# 🔴 Секреты живут прямо в адресе: Telegram кладет токен бота в путь, Meta и ВК -
# в строку запроса. Замерено 27.08: неотловленная ошибка Telegram напечатала токен
# целиком в трассировку. В GitHub Actions такая трассировка уходит в лог, который
# виден всем с доступом к репозиторию. Поэтому текст ошибки чистится всегда.
_SECRETS = [
    (re.compile(r"/bot\d+:[A-Za-z0-9_-]+"), "/bot<ТОКЕН>"),
    (re.compile(r"((?:access_token|api_key|assertion|key)=)[^&\s\"']+"), r"\1<СКРЫТО>"),
]


def mask(text):
    """Убирает секреты из строки перед тем, как она попадет в лог или в Telegram."""
    text = str(text)
    for pattern, replace in _SECRETS:
        text = pattern.sub(replace, text)
    return text


class HttpError(Exception):
    """Ошибка запроса с телом ответа - без тела разбирать API площадок невозможно."""

    def __init__(self, status, body, url):
        self.status, self.body, self.url = status, mask(body), mask(url)
        super().__init__("HTTP %s на %s: %s" % (status, self.url, self.body[:400]))


def request(url, method="GET", params=None, data=None, headers=None,
            timeout=120, raw_body=None):
    """Один запрос. params идут в строку, data - form-urlencoded телом.

    Возвращает разобранный JSON, если ответ похож на JSON, иначе - строку.
    """
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    body = raw_body
    hdrs = dict(headers or {})
    if data is not None and raw_body is None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, e.read().decode("utf-8", "replace"), url)
    return _maybe_json(text)


def download(url, params=None, headers=None, timeout=600):
    """Скачивает тело как есть, байтами.

    Отдельно от `request` намеренно: тот декодирует ответ в utf-8 и разбирает JSON,
    а видео от такого обращения превращается в мусор. Возвращает (байты, тип).
    """
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=dict(headers or {}), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, e.read().decode("utf-8", "replace"), url)


def post_file(url, field, filename, content, extra=None, headers=None, timeout=600):
    """multipart/form-data с одним файлом - так грузят видео ВК и Cloudinary."""
    boundary = uuid.uuid4().hex
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    parts = []
    for k, v in (extra or {}).items():
        parts.append(
            ("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
             % (boundary, k, v)).encode("utf-8"))
    parts.append(
        ("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
         "Content-Type: %s\r\n\r\n" % (boundary, field, filename, ctype)).encode("utf-8"))
    parts.append(content)
    parts.append(("\r\n--%s--\r\n" % boundary).encode("utf-8"))
    hdrs = dict(headers or {})
    hdrs["Content-Type"] = "multipart/form-data; boundary=%s" % boundary
    return request(url, method="POST", headers=hdrs,
                   raw_body=b"".join(parts), timeout=timeout)


def _maybe_json(text):
    stripped = text.lstrip()
    if stripped[:1] in ("{", "["):
        try:
            return json.loads(text)
        except ValueError:
            pass
    return text


def selftest():
    # сборка строки запроса
    assert "?a=1&b=2" in _url_for_test("https://x/y", {"a": 1, "b": 2})
    assert "&a=1" in _url_for_test("https://x/y?z=0", {"a": 1})
    # 🔴 секреты не должны доживать до лога
    # 🔴 Токен здесь ВЫДУМАННЫЙ. До 27.08 в этой строке стоял настоящий токен бота:
    # взяли живой пример, чтобы проверка была «как в бою», и чуть не отправили его
    # в публичный репозиторий вместе с историей. Проверке все равно, какие цифры,
    # а цена ошибки - чужой доступ к боту приемки.
    tg = "https://api.telegram.org/bot1234567890:AAFakeFakeFakeFakeFakeFakeFakeFakeFak/sendMessage"
    assert mask(tg) == "https://api.telegram.org/bot<ТОКЕН>/sendMessage", mask(tg)
    assert mask("https://graph.instagram.com/x?fields=id&access_token=EAAG123abc") \
        == "https://graph.instagram.com/x?fields=id&access_token=<СКРЫТО>"
    assert mask("api.vk.com/method/video.save?access_token=vk1.a.SECRET&v=5.199") \
        == "api.vk.com/method/video.save?access_token=<СКРЫТО>&v=5.199"
    err = HttpError(400, '{"error":"bad"}', tg)
    assert "AAFakeFake" not in str(err), "токен обязан исчезнуть из текста ошибки"
    # разбор ответа: JSON разбирается, произвольный текст остается текстом
    assert _maybe_json('{"ok": true}') == {"ok": True}
    assert _maybe_json("не json") == "не json"
    assert _maybe_json('') == ''
    # multipart: тело содержит и поле, и файл, и границы
    body = _multipart_for_test()
    assert b'name="video_file"' in body and b"filename=\"a.mp4\"" in body
    assert body.rstrip().endswith(b"--")
    print("http selftest OK: строка запроса, разбор ответа, multipart")


def _url_for_test(url, params):
    return url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)


def _multipart_for_test():
    boundary = "TESTBOUND"
    parts = [("--%s\r\nContent-Disposition: form-data; name=\"video_file\"; "
              "filename=\"a.mp4\"\r\nContent-Type: video/mp4\r\n\r\n" % boundary).encode(),
             b"\x00\x01", ("\r\n--%s--\r\n" % boundary).encode()]
    return b"".join(parts)


if __name__ == "__main__":
    selftest()
