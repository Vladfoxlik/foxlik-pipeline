# -*- coding: utf-8 -*-
"""Проверка публикации через Postmypost. Сеть не нужна.

Проверяется то, чем этот сервис портит публикацию молча:

  - в публикацию уходит `file_id` из `/upload/status`, а НЕ `id` из `/upload/init`.
    Оба целых, перепутать легко, симптом - «Вы не можете разместить это фото»;
  - загрузка по ссылке (вариант B) НЕ вызывает `/upload/complete`, а прямая (вариант A)
    вызывает обязательно;
  - в `/upload/init` идет ровно один из вариантов: либо `url`, либо `name`+`size`.
    Оба сразу - 422 «matched none» (замерено 29.08);
  - тип публикации для клипов и Reels - 4, статус «ждет публикации» - 5;
  - `status: 2` в загрузке значит ошибку, а не «еще думает»;
  - 400 «Ваш тариф не поддерживает API» обязан читаться человеком: замер 31.08 показал,
    что после оплаты тарифа API остается закрытым, пока не включен отдельный модуль.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import http, postmypost as P  # noqa: E402

PROJECT = 358244


class FakeApi:
    """Подставной Postmypost: помнит вызовы и отвечает по справочнику."""

    def __init__(self, statuses=None, file_id=778899):
        self.calls = []                     # (метод, путь, тело или параметры)
        self.statuses = list(statuses or [1])   # что отдает /upload/status по очереди
        self.file_id = file_id
        self.s3 = []                        # что ушло в хранилище

    def request(self, url, method="GET", params=None, data=None, headers=None,
                timeout=120, raw_body=None):
        import json as _json
        path = url.split("v4.1")[-1].split("?")[0]
        body = _json.loads(raw_body.decode("utf-8")) if raw_body else (data or params)
        self.calls.append((method, path, body))
        if path == "/accounts":
            return {"data": [{"id": 11, "name": "foxlik_official", "channel_id": 2},
                             {"id": 12, "name": "myplayroom_shop", "channel_id": 1}]}
        if path == "/upload/init":
            if "url" in (body or {}):
                return {"id": 32, "status": 5}
            return {"id": 32, "status": 5, "name": body["name"], "size": body["size"],
                    "action": "https://uploads.s3.amazonaws.com/",
                    "fields": [{"key": "acl", "value": "public-read"},
                               {"key": "key", "value": "1/79811084-x"},
                               {"key": "Policy", "value": "POLICY"},
                               {"key": "X-Amz-Signature", "value": "SIG"}]}
        if path == "/upload/complete":
            return {"id": 32, "status": 1}
        if path == "/upload/status":
            st = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            out = {"id": 32, "status": st}
            if st == 1:
                out["file_id"] = self.file_id
            return out
        if path == "/publications":
            return {"data": {"id": 90001}}
        raise AssertionError("неожиданный вызов " + url)

    def post_file(self, url, field, filename, content, extra=None, headers=None,
                  timeout=600):
        self.s3.append((url, field, filename, content, dict(extra or {})))
        return ""


def with_api(api, fn):
    real_req, real_file = http.request, http.post_file
    http.request, http.post_file = api.request, api.post_file
    try:
        return fn()
    finally:
        http.request, http.post_file = real_req, real_file


def client(sleep=lambda s: None):
    return P.Postmypost("TOKEN", PROJECT, sleep=sleep)


def selftest():
    # --- аккаунты: без них не с чем сопоставлять площадки -------------------
    api = FakeApi()
    accs = with_api(api, lambda: client().accounts())
    assert [a["id"] for a in accs] == [11, 12], accs
    assert api.calls[0][2]["project_id"] == PROJECT, "проект обязан уйти параметром"

    # --- вариант B: загрузка по ссылке -------------------------------------
    api = FakeApi(statuses=[5, 3, 1])
    fid = with_api(api, lambda: client().upload_from_url("https://cdn/x.mp4"))
    assert fid == 778899, "вернуть надо file_id из /upload/status, а не id загрузки"
    assert fid != 32, "🔴 id загрузки в публикацию не годится"
    paths = [c[1] for c in api.calls]
    assert "/upload/complete" not in paths, \
        "🔴 при загрузке по ссылке /upload/complete не вызывается"
    assert paths.count("/upload/status") == 3, "опрос обязан дождаться готовности"

    # --- вариант A: файл с диска -------------------------------------------
    api = FakeApi(statuses=[3, 1])
    fid = with_api(api, lambda: client().upload_bytes(b"\x00\x01\x02", "ролик.mp4"))
    assert fid == 778899, fid
    paths = [c[1] for c in api.calls]
    assert paths[0] == "/upload/init" and "/upload/complete" in paths, paths
    assert paths.index("/upload/complete") < paths.index("/upload/status"), \
        "complete идет до опроса статуса"
    init_body = api.calls[0][2]
    assert init_body["name"] == "ролик.mp4" and init_body["size"] == 3, init_body
    assert "url" not in init_body, "🔴 url и name+size вместе дают 422"
    # поля хранилища уходят точь-в-точь, иначе S3 отказывает подписи
    url, field, name, content, extra = api.s3[0]
    assert url == "https://uploads.s3.amazonaws.com/", url
    assert field == "file" and content == b"\x00\x01\x02"
    assert extra["Policy"] == "POLICY" and extra["X-Amz-Signature"] == "SIG", extra
    assert extra["key"] == "1/79811084-x", "ключ хранилища обязан дойти без правок"

    # --- ошибка загрузки не притворяется ожиданием -------------------------
    api = FakeApi(statuses=[2])
    try:
        with_api(api, lambda: client().upload_from_url("https://cdn/bad.mp4"))
        raise AssertionError("status=2 обязан подняться ошибкой")
    except P.PublishError as e:
        assert "ошибка" in str(e).lower(), e

    # --- создание публикации: тип 4 и статус 5 -----------------------------
    api = FakeApi()
    pid = with_api(api, lambda: client().create_publication(
        post_at="2026-09-04T10:00:00+03:00", account_ids=[11, 12],
        file_ids=[778899], content="Текст поста"))
    assert pid == 90001, pid
    body = api.calls[-1][2]
    assert body["project_id"] == PROJECT and body["account_ids"] == [11, 12]
    assert body["publication_status"] == 5, "5 = ждет публикации, наш рабочий режим"
    assert body["post_at"] == "2026-09-04T10:00:00+03:00", "время уходит с зоной"
    det = body["details"][0]
    assert det["publication_type"] == 4, "🔴 4 = reels/shorts/клипы; 1 - это обычный пост"
    assert det["file_ids"] == [778899] and det["content"] == "Текст поста", det

    # --- деталь без content, link и file_ids сервис не примет ---------------
    try:
        with_api(FakeApi(), lambda: client().create_publication(
            post_at="2026-09-04T10:00:00+03:00", account_ids=[11],
            file_ids=[], content=""))
        raise AssertionError("пустая деталь должна отлавливаться у нас, а не у них")
    except P.PublishError as e:
        assert "content" in str(e) or "file_ids" in str(e), e

    # --- весь путь целиком --------------------------------------------------
    api = FakeApi(statuses=[3, 1])
    pid = with_api(api, lambda: client().post_video_url(
        "https://cdn/x.mp4", "Подпись", [11, 12], "2026-09-04T10:00:00+03:00"))
    assert pid == 90001, pid
    assert [c[1] for c in api.calls][-1] == "/publications", "публикация идет последней"

    # --- 🔴 закрытый модуль API читается человеком --------------------------
    def tariff_wall(url, method="GET", params=None, data=None, headers=None,
                    timeout=120, raw_body=None):
        raise http.HttpError(400, '{"message":"Ваш тариф не поддерживает API"}', url)

    real = http.request
    http.request = tariff_wall
    try:
        client().accounts()
        raise AssertionError("стена тарифа обязана подняться понятной ошибкой")
    except P.TariffError as e:
        assert "модул" in str(e).lower(), \
            "текст обязан называть причину: включить модуль API в биллинге"
    finally:
        http.request = real

    # --- токен не доживает до лога -----------------------------------------
    err = http.HttpError(401, "unauthorized", "https://api.postmypost.io/v4.1/accounts")
    assert "TOKEN" not in str(err)

    print("postmypost selftest OK: file_id против id загрузки, вариант B без complete, "
          "поля хранилища точь-в-точь, тип 4 и статус 5, пустая деталь, стена тарифа")


if __name__ == "__main__":
    selftest()
