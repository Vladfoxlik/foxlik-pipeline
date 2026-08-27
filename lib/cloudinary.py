# -*- coding: utf-8 -*-
"""Cloudinary - перевалочный пункт для Instagram.

Зачем: Meta забирает видео по публичной ссылке (делает cURL), а Google Диск
и Яндекс.Диск она отвергает - замерено 26.08. Поэтому файл кладется сюда,
отдается Instagram ссылкой и стирается сразу после публикации.

Постоянно здесь ничего не хранится: бесплатный тариф - 25 кредитов в месяц,
и тратить их на архив незачем, архив живет на Google Диске.

Ключи берутся из CLOUDINARY_URL вида cloudinary://<api_key>:<api_secret>@<cloud_name>.

✅ **Прогнано живьем 27.08.2026** на односекундном ролике, облако владельца:
загрузка прошла, **файл забрался по публичной ссылке без единого ключа** (именно так
по ней придет Meta), байты совпали с исходными, удаление вернуло `ok`.

⚠️ **Замечено там же: после удаления ссылка продолжает отдавать файл.** Это кеш CDN,
и он живет своей жизнью. Значит фраза «файл стирается сразу после публикации» верна
про хранилище и неверна про раздачу: какое-то время ролик еще доступен по прямой
ссылке. Нам это не вредит - к тому моменту он уже опубликован в Instagram открыто, -
но обещать «файл исчез» нельзя.
"""
import hashlib
import time
import urllib.parse

from . import http


class CloudinaryError(Exception):
    pass


class Cloudinary:
    def __init__(self, url=None, cloud_name=None, api_key=None, api_secret=None):
        if url:
            cloud_name, api_key, api_secret = parse_url(url)
        if not all([cloud_name, api_key, api_secret]):
            raise CloudinaryError("нет ключей Cloudinary")
        self.cloud, self.key, self.secret = cloud_name, api_key, api_secret

    def _endpoint(self, action):
        return "https://api.cloudinary.com/v1_1/%s/video/%s" % (self.cloud, action)

    def _sign(self, params):
        """Подпись Cloudinary: sha1 от отсортированных параметров плюс секрет."""
        canon = "&".join("%s=%s" % (k, params[k]) for k in sorted(params))
        return hashlib.sha1((canon + self.secret).encode("utf-8")).hexdigest()

    def upload(self, filename, content, public_id=None, now=None):
        """Кладет видео. Возвращает (public_id, прямая ссылка)."""
        params = {"timestamp": int(now or time.time())}
        if public_id:
            params["public_id"] = public_id
        signed = dict(params, signature=self._sign(params), api_key=self.key)
        r = http.post_file(self._endpoint("upload"), "file", filename, content,
                           extra={k: str(v) for k, v in signed.items()})
        if not isinstance(r, dict) or "secure_url" not in r:
            raise CloudinaryError("загрузка не удалась: %s" % r)
        return r["public_id"], r["secure_url"]

    def destroy(self, public_id, now=None):
        """Стирает после публикации. Молча терпит, если файла уже нет."""
        params = {"public_id": public_id, "timestamp": int(now or time.time())}
        r = http.request(self._endpoint("destroy"), method="POST",
                         data=dict(params, signature=self._sign(params),
                                   api_key=self.key))
        return (r or {}).get("result")


def parse_url(url):
    """cloudinary://key:secret@cloud -> (cloud, key, secret)."""
    p = urllib.parse.urlparse(url)
    if p.scheme != "cloudinary" or not p.hostname:
        raise CloudinaryError("не похоже на CLOUDINARY_URL: %s" % url)
    return p.hostname, p.username, p.password


def selftest():
    assert parse_url("cloudinary://123:abc@foxlik") == ("foxlik", "123", "abc")
    try:
        parse_url("https://x")
        raise AssertionError("чужая схема должна отвергаться")
    except CloudinaryError:
        pass

    c = Cloudinary(url="cloudinary://KEY:SECRET@foxlik")
    # подпись считается по отсортированным параметрам, секрет в хвосте и не в теле
    expected = hashlib.sha1(b"public_id=p1&timestamp=100SECRET").hexdigest()
    assert c._sign({"timestamp": 100, "public_id": "p1"}) == expected
    assert c._sign({"public_id": "p1", "timestamp": 100}) == expected, "порядок не должен влиять"

    sent = {}

    def fake_post_file(url, field, filename, content, extra=None, **kw):
        sent.update(url=url, extra=extra, field=field)
        return {"public_id": "p1", "secure_url": "https://res.cloudinary.com/foxlik/p1.mp4"}

    def fake_request(url, method="GET", data=None, **kw):
        sent.update(destroy_url=url, destroy=data)
        return {"result": "ok"}

    real_f, real_r = http.post_file, http.request
    http.post_file, http.request = fake_post_file, fake_request
    try:
        pid, link = c.upload("a.mp4", b"\x00", now=100)
        assert (pid, link) == ("p1", "https://res.cloudinary.com/foxlik/p1.mp4")
        assert "/foxlik/video/upload" in sent["url"], sent["url"]
        assert sent["extra"]["api_key"] == "KEY"
        assert "signature" in sent["extra"]
        assert "SECRET" not in str(sent["extra"]), "секрет не должен уходить в теле"
        assert c.destroy("p1", now=100) == "ok"
        assert "/video/destroy" in sent["destroy_url"]
    finally:
        http.post_file, http.request = real_f, real_r
    print("cloudinary selftest OK: разбор URL, подпись, загрузка, удаление, секрет не утекает")


if __name__ == "__main__":
    selftest()
