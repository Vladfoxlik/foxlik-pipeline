# -*- coding: utf-8 -*-
"""Тонкая обертка над urllib: одна точка входа для всех запросов конвейера.

Зависимостей нет намеренно - код должен запускаться и локально в venv без pip,
и в GitHub Actions без шага установки.
"""
import http.client
import json
import mimetypes
import re
import socket
import time
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

# 🔴 Повтор при сетевом обрыве (замер 11.09): такт упал на чтении таблицы Google -
# «Connection reset by peer» прямо в установке защищенного канала, а следующий
# прогон через 50 минут прошел чисто. Одиночный обрыв в облаке - обычное дело,
# и без повтора каждый из них становится падением такта или тревогой владельцу.
# Повторяется ТОЛЬКО чтение (GET, HEAD): запись при обрыве могла дойти до сервиса,
# и повтор публикации дал бы двойной эфир - то, от чего конвейер защищен первым делом.
RETRY_WAIT = (2, 5)                       # пауза перед 2-й и 3-й попыткой, секунды
TRANSIENT_HTTP = (429, 500, 502, 503, 504)
_SAFE_METHODS = ("GET", "HEAD")
# точки подмены для проверок: сеть и сон без настоящих вызовов
_urlopen = urllib.request.urlopen
_sleep = time.sleep


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


def _with_retry(req, timeout, read):
    """Открывает запрос и читает ответ; чтение при обрыве повторяет.

    Повтор накрывает и открытие, и чтение тела: обрыв бывает в обоих местах.
    Ошибка по сути (4xx) не повторяется никогда - повтор ее не лечит.
    """
    повторять = req.get_method() in _SAFE_METHODS
    паузы = (0,) + RETRY_WAIT
    for i, пауза in enumerate(паузы):
        последняя = i == len(паузы) - 1
        if пауза:
            _sleep(пауза)
        try:
            with _urlopen(req, timeout=timeout) as r:
                return read(r)
        except urllib.error.HTTPError as e:
            if повторять and e.code in TRANSIENT_HTTP and not последняя:
                continue
            raise HttpError(e.code, e.read().decode("utf-8", "replace"), req.full_url)
        except (urllib.error.URLError, ConnectionError, TimeoutError, socket.timeout,
                http.client.HTTPException):
            if повторять and not последняя:
                continue
            raise


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
    text = _with_retry(req, timeout, lambda r: r.read().decode("utf-8", "replace"))
    return _maybe_json(text)


def download(url, params=None, headers=None, timeout=600):
    """Скачивает тело как есть, байтами.

    Отдельно от `request` намеренно: тот декодирует ответ в utf-8 и разбирает JSON,
    а видео от такого обращения превращается в мусор. Возвращает (байты, тип).
    """
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=dict(headers or {}), method="GET")
    return _with_retry(req, timeout,
                       lambda r: (r.read(), r.headers.get("Content-Type", "")))


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
    # Короткий он тоже намеренно: длинная подделка формой совпадает с настоящим
    # токеном, и поиск секретов перед публикацией спотыкался бы о нее каждый раз.
    tg = "https://api.telegram.org/bot1234567890:FAKE/sendMessage"
    assert mask(tg) == "https://api.telegram.org/bot<ТОКЕН>/sendMessage", mask(tg)
    assert mask("https://graph.instagram.com/x?fields=id&access_token=EAAG123abc") \
        == "https://graph.instagram.com/x?fields=id&access_token=<СКРЫТО>"
    assert mask("api.vk.com/method/video.save?access_token=vk1.a.SECRET&v=5.199") \
        == "api.vk.com/method/video.save?access_token=<СКРЫТО>&v=5.199"
    err = HttpError(400, '{"error":"bad"}', tg)
    assert "FAKE" not in str(err), "токен обязан исчезнуть из текста ошибки"
    # разбор ответа: JSON разбирается, произвольный текст остается текстом
    assert _maybe_json('{"ok": true}') == {"ok": True}
    assert _maybe_json("не json") == "не json"
    assert _maybe_json('') == ''
    # multipart: тело содержит и поле, и файл, и границы
    body = _multipart_for_test()
    assert b'name="video_file"' in body and b"filename=\"a.mp4\"" in body
    assert body.rstrip().endswith(b"--")
    # 🔴 Обрыв соединения при ЧТЕНИИ повторяется, а не валит такт (замер 11.09:
    # такт упал на чтении таблицы Google - «Connection reset by peer» прямо
    # в установке защищенного канала, следующий прогон прошел чисто).
    global _urlopen, _sleep
    настоящие = (_urlopen, _sleep)
    try:
        вызовы = []

        def обрыв_дважды(req, timeout=None):
            вызовы.append(req.get_method())
            if len(вызовы) < 3:
                raise urllib.error.URLError(
                    ConnectionResetError(104, "Connection reset by peer"))
            return _FakeResponse(b'{"ok": true}')

        _urlopen, _sleep = обрыв_дважды, (lambda s: None)
        assert request("https://x/y") == {"ok": True}, u"чтение не пережило обрыв"
        assert len(вызовы) == 3, вызовы

        # 🔴 ЗАПИСЬ при обрыве НЕ повторяется: сервис мог успеть создать публикацию,
        # и повтор дал бы двойной эфир - то, от чего конвейер защищен в первую очередь
        del вызовы[:]
        try:
            request("https://x/y", method="POST", raw_body=b"{}")
            assert False, u"обрыв записи обязан подниматься наверх, а не повторяться"
        except urllib.error.URLError:
            pass
        assert вызовы == ["POST"], u"запись повторилась: %s" % вызовы

        # ошибка по сути (4xx) не повторяется даже у чтения: повтор ее не лечит
        del вызовы[:]

        def отказ(req, timeout=None):
            вызовы.append(req.get_method())
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

        _urlopen = отказ
        try:
            request("https://x/y")
            assert False, u"403 обязан подниматься как HttpError"
        except HttpError as e:
            assert e.status == 403, e
        assert len(вызовы) == 1, u"403 повторился: %s" % вызовы
    finally:
        _urlopen, _sleep = настоящие
    print("http selftest OK: строка запроса, разбор ответа, multipart, повтор чтения "
          "при обрыве, запись и 4xx не повторяются")


class _FakeResponse(object):
    """Ответ для проверок: то же, что отдает urlopen, без сети."""

    def __init__(self, body, ctype="application/json"):
        self._body = body
        self.headers = {"Content-Type": ctype}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


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
