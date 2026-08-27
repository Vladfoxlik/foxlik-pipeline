# -*- coding: utf-8 -*-
"""Публикация видео в сообщество ВКонтакте.

Механика по справочнику dev.vk.com (прочитан 27.08.2026):
  1. video.save(group_id, name, description) -> upload_url, owner_id, video_id
  2. POST файла на upload_url полем video_file (multipart)
  3. wall.post с attachment video<owner_id>_<video_id> - если нужна запись на стене

Публичная ссылка не нужна: ВК принимает файл напрямую, в отличие от Instagram.

⚠️ Право доступа `video` справочник называет выдаваемым «в исключительных случаях
через запрос в поддержку». На практике токен со scope video для своего сообщества
берется через Standalone-приложение. Проверяется получением токена - это этап 3 сборки.
Если ВК откажет - письмо на devsupport@corp.vk.com.
"""
from . import http

API = "https://api.vk.com/method/"
VERSION = "5.199"


class VkError(Exception):
    pass


class Vk:
    def __init__(self, token, group_id, version=VERSION):
        self.token = token
        # ВК ждет положительный group_id в video.save и отрицательный owner_id на стене
        self.group_id = abs(int(group_id))
        self.version = version

    def call(self, method, **params):
        params.update(access_token=self.token, v=self.version)
        r = http.request(API + method, method="POST", data=params)
        if isinstance(r, dict) and "error" in r:
            e = r["error"]
            raise VkError("%s: [%s] %s" % (method, e.get("error_code"),
                                           e.get("error_msg")))
        return (r or {}).get("response", r)

    def upload_video(self, filename, content, name="", description=""):
        """Шаги 1-2. Возвращает (owner_id, video_id) - из них строится attachment."""
        saved = self.call("video.save", group_id=self.group_id,
                          name=name[:128], description=description[:5000],
                          wallpost=0)
        url = saved.get("upload_url")
        if not url:
            raise VkError("video.save не вернул upload_url: %s" % saved)
        http.post_file(url, "video_file", filename, content)
        return saved["owner_id"], saved["video_id"]

    def post_to_wall(self, owner_id, video_id, message=""):
        """Шаг 3. Запись на стене сообщества от имени сообщества."""
        r = self.call("wall.post",
                      owner_id=-self.group_id,
                      from_group=1,
                      message=message,
                      attachments="video%s_%s" % (owner_id, video_id))
        post_id = r.get("post_id")
        return "https://vk.com/wall-%s_%s" % (self.group_id, post_id)

    def publish(self, filename, content, name="", message=""):
        """Весь путь. Возвращает ссылку на запись."""
        owner_id, video_id = self.upload_video(filename, content, name, message)
        return self.post_to_wall(owner_id, video_id, message)


def selftest():
    calls = []

    def fake_request(url, method="GET", data=None, **kw):
        calls.append((url.rsplit("/", 1)[-1], dict(data or {})))
        if url.endswith("video.save"):
            return {"response": {"upload_url": "https://up.vk/x",
                                 "owner_id": -777, "video_id": 42}}
        if url.endswith("wall.post"):
            return {"response": {"post_id": 99}}
        raise AssertionError("неожиданный метод " + url)

    def fake_post_file(url, field, filename, content, **kw):
        calls.append(("UPLOAD", {"field": field, "file": filename,
                                 "bytes": len(content)}))
        return {"size": len(content), "video_id": 42}

    real_r, real_f = http.request, http.post_file
    http.request, http.post_file = fake_request, fake_post_file
    try:
        vk = Vk("TOKEN", -777)
        link = vk.publish("roll.mp4", b"\x00" * 10, name="P26-03", message="текст")
        assert link == "https://vk.com/wall-777_99", link

        save = dict(calls[0][1])
        assert calls[0][0] == "video.save"
        assert save["group_id"] == 777, "group_id обязан быть положительным"
        assert calls[1][0] == "UPLOAD" and calls[1][1]["field"] == "video_file"
        wall = calls[2][1]
        assert wall["owner_id"] == -777, "на стене сообщества owner_id отрицательный"
        assert wall["from_group"] == 1
        assert wall["attachments"] == "video-777_42", wall["attachments"]

        # ошибка ВК обязана всплыть с кодом, а не притвориться успехом
        http.request = lambda *a, **k: {"error": {"error_code": 15,
                                                  "error_msg": "Access denied"}}
        try:
            Vk("T", 1).call("video.save")
            raise AssertionError("ошибка должна была подняться")
        except VkError as e:
            assert "15" in str(e) and "Access denied" in str(e), e
    finally:
        http.request, http.post_file = real_r, real_f
    print("vk selftest OK: знаки id, поле video_file, attachment, ошибка не глотается")


if __name__ == "__main__":
    selftest()
