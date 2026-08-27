# -*- coding: utf-8 -*-
"""Публикация Reels в Instagram через официальный API.

Механика по документации Meta (прочитана 27.08.2026):
  1. POST /<IG_ID>/media  с media_type=REELS и video_url -> контейнер
  2. GET  /<CONTAINER>?fields=status_code -> опрашивать раз в минуту, не дольше 5 минут
     ответы: IN_PROGRESS · FINISHED · ERROR · EXPIRED (контейнер живет 24 часа)
  3. POST /<IG_ID>/media_publish с creation_id -> публикация

Ограничения, которые нас касаются:
  - 100 публикаций за 24 часа. У нас одна в день.
  - video_url должен быть публично доступен: Meta делает cURL по ссылке.
    Google Диск и Яндекс.Диск отвергаются (замер 26.08), поэтому файл кладется
    на Cloudinary и стирается после публикации.

⚠️ Живьем не проверено - нужен токен. Это этап 0 сборки.
"""
import time

from . import http

HOST = "https://graph.instagram.com"
VERSION = "v23.0"
POLL_EVERY = 60          # раз в минуту, как рекомендует Meta
POLL_LIMIT = 5           # не дольше пяти минут


class PublishError(Exception):
    pass


class Instagram:
    def __init__(self, token, ig_user_id, version=VERSION, sleep=time.sleep):
        self.token = token
        self.ig = str(ig_user_id)
        self.version = version
        self._sleep = sleep      # подменяется в тестах, чтобы не ждать минуты

    def _url(self, path):
        return "%s/%s/%s" % (HOST, self.version, path)

    def create_container(self, video_url, caption=""):
        """Шаг 1. Возвращает id контейнера."""
        r = http.request(self._url(self.ig + "/media"), method="POST", data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "access_token": self.token,
        })
        if not isinstance(r, dict) or "id" not in r:
            raise PublishError("контейнер не создан: %s" % r)
        return r["id"]

    def wait_ready(self, container_id):
        """Шаг 2. Ждет FINISHED. Возвращает статус, решение принимает вызывающий."""
        for _ in range(POLL_LIMIT):
            r = http.request(self._url(container_id), params={
                "fields": "status_code,status", "access_token": self.token})
            code = (r or {}).get("status_code")
            if code == "FINISHED":
                return code
            if code in ("ERROR", "EXPIRED"):
                # текст ошибки лежит в status, а не в коде - без него чинить нечего
                raise PublishError("контейнер %s: %s" % (code, (r or {}).get("status")))
            self._sleep(POLL_EVERY)
        return "IN_PROGRESS"     # не дозрел - вызывающий вернет строку в очередь

    def publish(self, container_id):
        """Шаг 3. Возвращает id опубликованного медиа."""
        r = http.request(self._url(self.ig + "/media_publish"), method="POST", data={
            "creation_id": container_id, "access_token": self.token})
        if not isinstance(r, dict) or "id" not in r:
            raise PublishError("публикация не прошла: %s" % r)
        return r["id"]

    def permalink(self, media_id):
        r = http.request(self._url(media_id), params={
            "fields": "permalink", "access_token": self.token})
        return (r or {}).get("permalink")

    def quota_left(self):
        """Сколько публикаций осталось в суточном лимите 100."""
        r = http.request(self._url(self.ig + "/content_publishing_limit"),
                         params={"fields": "quota_usage", "access_token": self.token})
        used = ((r or {}).get("data") or [{}])[0].get("quota_usage")
        return None if used is None else 100 - used

    def post_reel(self, video_url, caption=""):
        """Весь путь целиком. Возвращает (media_id, permalink) либо None, если не дозрел."""
        cid = self.create_container(video_url, caption)
        if self.wait_ready(cid) != "FINISHED":
            return None
        mid = self.publish(cid)
        return mid, self.permalink(mid)


def selftest():
    """Проверяет последовательность вызовов на подставном HTTP, без сети."""
    calls = []

    def fake(url, method="GET", params=None, data=None, **kw):
        calls.append((method, url.rsplit("/", 1)[-1], data or params))
        if url.endswith("/media"):
            return {"id": "CONT1"}
        if url.endswith("/media_publish"):
            return {"id": "MEDIA1"}
        if "status_code" in (params or {}).get("fields", ""):
            # первый опрос - не готов, второй - готов: проверяем, что цикл ждет
            return {"status_code": "FINISHED"} if len(calls) > 2 else {"status_code": "IN_PROGRESS"}
        if "permalink" in (params or {}).get("fields", ""):
            return {"permalink": "https://instagram.com/reel/ABC"}
        raise AssertionError("неожиданный вызов " + url)

    real, http.request = http.request, fake
    try:
        ig = Instagram("TOKEN", "123", sleep=lambda s: None)
        mid, link = ig.post_reel("https://cdn/x.mp4", "подпись")
        assert (mid, link) == ("MEDIA1", "https://instagram.com/reel/ABC"), (mid, link)
        assert calls[0][0] == "POST" and calls[0][2]["media_type"] == "REELS"
        assert calls[0][2]["video_url"] == "https://cdn/x.mp4"
        assert any(c[0] == "POST" and c[1] == "media_publish" for c in calls)
        assert sum(1 for c in calls if c[0] == "GET") >= 2, "опрос должен повториться"

        # ошибка контейнера обязана всплыть с текстом, а не проглотиться
        def failing(url, method="GET", params=None, data=None, **kw):
            if url.endswith("/media"):
                return {"id": "C2"}
            return {"status_code": "ERROR", "status": "media download failed"}
        http.request = failing
        try:
            Instagram("T", "1", sleep=lambda s: None).post_reel("u")
            raise AssertionError("ошибка должна была подняться")
        except PublishError as e:
            assert "media download failed" in str(e), e
    finally:
        http.request = real
    print("instagram selftest OK: контейнер -> опрос -> публикация, ошибка не глотается")


if __name__ == "__main__":
    selftest()
