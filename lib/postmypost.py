# -*- coding: utf-8 -*-
"""Публикация через Postmypost - один сервис на все шесть площадок.

Почему через сервис, а не своими токенами: замер 29.08 показал, что в ВК не работает
ни один бесплатный путь (приложение заблокировано администрацией, у токена сообщества
нет права «видео» - проверено на 25 ключах, токены VK ID через API не пишут).
Решение владельца 29.08 - публикуем сервисом.

Механика по справочнику `управление/POSTMYPOST_API.md`, схемы сняты живьем с их
MCP-сервера нашим токеном. Четыре шага:

  1. POST /upload/init      - либо {url}, либо {name, size}. Ровно один вариант.
  2. POST в хранилище S3    - только для варианта с файлом, поля из ответа точь-в-точь,
     затем POST /upload/complete?id=<id загрузки>
  3. GET  /upload/status    - опрашивать до status=1, там появляется `file_id`
  4. POST /publications     - details с publication_type=4 (reels/shorts/клипы)

🔴 Две ловушки, обе стоят публикации и обе молчат:
  - в шаг 4 идет `file_id` из шага 3, а НЕ `id` из шага 1. Оба целые числа;
  - тип публикации нумеруется по-разному: история - 2 при создании и 3 в аналитике.

⚠️ Замер 31.08: после оплаты тарифа API остается закрытым, пока в биллинге не включен
отдельный модуль «API» - ответ 400 «Ваш тариф не поддерживает API». Поэтому такая стена
поднимается своим классом TariffError: это не сбой сети и не повод повторять запрос.
"""
import json
import time

from . import http

HOST = "https://api.postmypost.io/v4.1"

TYPE_POST = 1
TYPE_STORY = 2          # 🔴 в аналитике та же история приходит как 3
TYPE_REELS = 4          # reels · shorts · клипы ВК - наш рабочий тип

STATUS_DRAFT = 4
STATUS_SCHEDULED = 5    # ждет публикации - наш рабочий режим

UPLOAD_DONE = 1
UPLOAD_FAILED = 2

POLL_EVERY = 5          # опрос загрузки, секунды
POLL_LIMIT = 60         # не дольше пяти минут: 5 ГБ сервис обрабатывает не мгновенно


class PublishError(Exception):
    pass


class TariffError(PublishError):
    """Модуль API не включен. Повторять запрос бессмысленно - нужен человек в биллинге."""


class Postmypost:
    def __init__(self, token, project_id, sleep=time.sleep):
        self.token = token
        self.project = int(project_id)
        self._sleep = sleep

    # --- транспорт ---------------------------------------------------------

    def _headers(self):
        return {"Authorization": "Bearer " + self.token, "Accept": "application/json"}

    def _call(self, path, method="GET", params=None, body=None):
        headers = self._headers()
        raw = None
        if body is not None:
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            return http.request(HOST + path, method=method, params=params,
                                headers=headers, raw_body=raw)
        except http.HttpError as e:
            if "тариф не поддерживает API" in (e.body or ""):
                raise TariffError(
                    "Postmypost: модуль «API» не включен в тарифе проекта %s. "
                    "Включается в кабинете: app.postmypost.io/billing, раздел «Модули»."
                    % self.project)
            raise

    # --- справочники -------------------------------------------------------

    def accounts(self):
        """Подключенные страницы: id, имя, канал. Без них публиковать некуда."""
        r = self._call("/accounts", params={"project_id": self.project})
        data = (r or {}).get("data")
        if not isinstance(data, list):
            raise PublishError("список аккаунтов не прочитан: %s" % r)
        return data

    # --- загрузка файла ----------------------------------------------------

    def _wait_file(self, upload_id):
        """Опрос до готовности. Возвращает file_id - именно он идет в публикацию."""
        for _ in range(POLL_LIMIT):
            r = self._call("/upload/status", params={"id": upload_id}) or {}
            status = r.get("status")
            if status == UPLOAD_DONE:
                file_id = r.get("file_id")
                if file_id is None:
                    raise PublishError("загрузка готова, но file_id не пришел: %s" % r)
                return file_id
            if status == UPLOAD_FAILED:
                raise PublishError("загрузка %s: ошибка на стороне сервиса (%s)"
                                   % (upload_id, r))
            self._sleep(POLL_EVERY)
        raise PublishError("загрузка %s не дозрела за отведенное время" % upload_id)

    def upload_from_url(self, url):
        """Вариант B: сервис скачивает сам, до 1 ГБ. /upload/complete тут не нужен."""
        r = self._call("/upload/init", method="POST",
                       body={"project_id": self.project, "url": url}) or {}
        if "id" not in r:
            raise PublishError("загрузка по ссылке не начата: %s" % r)
        return self._wait_file(r["id"])

    def upload_bytes(self, content, filename):
        """Вариант A: файл у нас на руках, до 5 ГБ. Заливка в хранилище своими руками."""
        r = self._call("/upload/init", method="POST",
                       body={"project_id": self.project, "name": filename,
                             "size": len(content)}) or {}
        if "id" not in r or "action" not in r:
            raise PublishError("прямая загрузка не начата: %s" % r)
        # поля подписи уходят без единой правки, иначе хранилище отвергнет запрос
        extra = {f["key"]: f["value"] for f in (r.get("fields") or [])}
        http.post_file(r["action"], "file", filename, content, extra=extra)
        self._call("/upload/complete", method="POST", params={"id": r["id"]})
        return self._wait_file(r["id"])

    # --- публикация --------------------------------------------------------

    def create_publication(self, post_at, account_ids, file_ids=(), content="",
                           publication_type=TYPE_REELS,
                           publication_status=STATUS_SCHEDULED, details_extra=None):
        """Шаг 4. Возвращает id публикации в сервисе."""
        detail = {"publication_type": publication_type}
        if content:
            detail["content"] = content
        if file_ids:
            detail["file_ids"] = list(file_ids)
        detail.update(details_extra or {})
        if not (detail.get("content") or detail.get("file_ids") or detail.get("link")):
            raise PublishError(
                "деталь публикации пуста: нужен хотя бы один из content, file_ids, link")
        body = {"project_id": self.project, "post_at": post_at,
                "account_ids": list(account_ids),
                "publication_status": publication_status, "details": [detail]}
        r = self._call("/publications", method="POST", body=body) or {}
        pid = (r.get("data") or {}).get("id") if isinstance(r.get("data"), dict) \
            else r.get("id")
        if pid is None:
            raise PublishError("публикация не создана: %s" % r)
        return pid

    def get_publication_posts(self, pub_id):
        """Посты публикации по аккаунтам - `GET /publications/{id}` → `posts`.

        🔴 Единственный способ узнать, вышла ли публикация и где она живет:
        при создании сервис возвращает только свой id, ссылки еще нет
        (справочник §5, наличие posts ЗАМЕРЕНО 31.08). Состав полей поста
        снят ПО ДОКЕ, не живьем, - поэтому вызывающий ищет ссылку мягко,
        по нескольким именам, и молчит, если ее нет.
        """
        r = self._call("/publications/%s" % pub_id) or {}
        data = r.get("data") if isinstance(r.get("data"), dict) else r
        return list((data or {}).get("posts") or [])

    def post_video_url(self, video_url, content, account_ids, post_at):
        """Весь путь для ролика, доступного по ссылке. Возвращает id публикации."""
        file_id = self.upload_from_url(video_url)
        return self.create_publication(post_at=post_at, account_ids=account_ids,
                                       file_ids=[file_id], content=content)

    def post_publication(self, post_at, account_ids, details, черновик=False):
        """Публикация с готовыми деталями - по одной на аккаунт.

        🔴 Так у каждой сети своя упаковка: в ВК кликабельная ссылка, в Instagram
        артикул номером, у YouTube заголовок. Собирает их `lib/platforms.py`.
        """
        body = {"project_id": self.project, "post_at": post_at,
                "account_ids": list(account_ids),
                "publication_status": STATUS_DRAFT if черновик else STATUS_SCHEDULED,
                "details": list(details)}
        r = self._call("/publications", method="POST", body=body) or {}
        pid = (r.get("data") or {}).get("id") if isinstance(r.get("data"), dict) \
            else r.get("id")
        if pid is None:
            raise PublishError("публикация не создана: %s" % r)
        return pid

    def post_video_bytes(self, content_bytes, filename, content, account_ids, post_at,
                         черновик=False, details=None):
        """То же для файла на руках - так публикуется ролик, скачанный с Диска.

        `черновик=True` - холостой прогон: путь проходится целиком, но публикация
        остается в кабинете со статусом «черновик» и в ленту не выходит.
        """
        file_id = self.upload_bytes(content_bytes, filename)
        if details is not None:
            # детали приходят готовыми, но file_id известен только сейчас
            готовые = [dict(d, file_ids=[file_id]) for d in details]
            return self.post_publication(post_at, account_ids, готовые, черновик)
        return self.create_publication(
            post_at=post_at, account_ids=account_ids, file_ids=[file_id],
            content=content,
            publication_status=STATUS_DRAFT if черновик else STATUS_SCHEDULED)


def selftest():
    """Полный набор - в pipeline/tests/test_postmypost.py, здесь только вход."""
    from tests import test_postmypost
    test_postmypost.selftest()


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    selftest()
