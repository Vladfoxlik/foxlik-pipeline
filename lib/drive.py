# -*- coding: utf-8 -*-
"""Google Диск: забрать ролик, который креатор сдал через форму.

Форма кладет файл в папку «Ответы на форму» и пишет в таблицу **ссылку**, а не
идентификатор. Ссылки Google выдает в нескольких видах, и все они приезжают в одну
и ту же колонку - поэтому разбор ссылки здесь главный и самый проверяемый кусок.

🔴 Сервисный аккаунт видит только то, что ему расшарили. Ответ 404 на файл, который
в браузере открывается, означает не «файла нет», а «папка не расшарена на
...@...iam.gserviceaccount.com». Ошибка так и написана - иначе это ищется часами.

⚠️ Живьем не проверено - нужен сервисный аккаунт.
"""
import re

from . import google_auth, http

API = "https://www.googleapis.com/drive/v3/files/"
SCOPES = [google_auth.SCOPE_DRIVE]

# Виды ссылок, которые реально приезжают из формы и из «Поделиться»
_ID_PATTERNS = [
    re.compile(r"/file/d/([A-Za-z0-9_-]{10,})"),          # /file/d/<id>/view
    re.compile(r"[?&]id=([A-Za-z0-9_-]{10,})"),           # open?id=<id>, uc?id=<id>
    re.compile(r"/document/d/([A-Za-z0-9_-]{10,})"),
    re.compile(r"/folders/([A-Za-z0-9_-]{10,})"),
]


class DriveError(Exception):
    pass


def file_id(link):
    """Вытаскивает идентификатор из любой формы ссылки. Голый id принимает как есть."""
    link = (link or "").strip()
    if not link:
        raise DriveError("пустая ссылка на файл")
    for pattern in _ID_PATTERNS:
        found = pattern.search(link)
        if found:
            return found.group(1)
    if "/" not in link and re.fullmatch(r"[A-Za-z0-9_-]{10,}", link):
        return link
    raise DriveError("не удалось разобрать ссылку на Диск: %s" % link)


class Drive:
    def __init__(self, sa):
        self.sa = sa

    def _headers(self):
        return self.sa.headers(SCOPES)

    def info(self, link):
        """Имя, размер, тип. Размер нужен до скачивания - решает, потянем ли."""
        fid = file_id(link)
        try:
            r = http.request(API + fid, params={
                "fields": "id,name,size,mimeType",
                "supportsAllDrives": "true"}, headers=self._headers())
        except http.HttpError as e:
            raise DriveError(_explain(e, fid))
        if not isinstance(r, dict) or "id" not in r:
            raise DriveError("не тот ответ о файле %s: %s" % (fid, r))
        r["size"] = int(r.get("size") or 0)
        return r

    def fetch(self, link, max_mb=None):
        """Скачивает файл целиком. Возвращает (имя, байты).

        `max_mb` - предохранитель: без него один гигантский файл съест память
        раннера и такт упадет молча.
        """
        meta = self.info(link)
        if max_mb and meta["size"] > max_mb * 1024 * 1024:
            raise DriveError("файл %s весит %.1f МБ, предел %s МБ"
                             % (meta["name"], meta["size"] / 1048576.0, max_mb))
        try:
            content, _ = http.download(API + meta["id"], params={
                "alt": "media", "supportsAllDrives": "true"},
                headers=self._headers())
        except http.HttpError as e:
            raise DriveError(_explain(e, meta["id"]))
        if meta["size"] and len(content) != meta["size"]:
            raise DriveError("файл %s скачался обрезанным: %s из %s байт"
                             % (meta["name"], len(content), meta["size"]))
        return meta["name"], content


def _explain(err, fid):
    """Превращает сухой код Google в подсказку, куда идти чинить."""
    if err.status in (403, 404):
        return ("Диск не отдал файл %s (HTTP %s). Чаще всего это не пропажа файла, "
                "а нерасшаренная папка: откройте доступ на почту сервисного аккаунта "
                "вида ...@...iam.gserviceaccount.com. Ответ Google: %s"
                % (fid, err.status, err.body[:300]))
    return "Диск ответил HTTP %s на файл %s: %s" % (err.status, fid, err.body[:300])


def selftest():
    # --- разбор всех видов ссылок ---
    cases = {
        "https://drive.google.com/file/d/1AbC_dEfGhIjKlMn/view?usp=drivesdk": "1AbC_dEfGhIjKlMn",
        "https://drive.google.com/open?id=1AbC_dEfGhIjKlMn": "1AbC_dEfGhIjKlMn",
        "https://drive.google.com/uc?export=download&id=1AbC_dEfGhIjKlMn": "1AbC_dEfGhIjKlMn",
        "https://drive.google.com/drive/folders/1AbC_dEfGhIjKlMn": "1AbC_dEfGhIjKlMn",
        "  1AbC_dEfGhIjKlMn  ": "1AbC_dEfGhIjKlMn",
    }
    for link, expected in cases.items():
        assert file_id(link) == expected, (link, file_id(link))
    for bad in ("", "   ", "https://example.com/video.mp4", "коротко"):
        try:
            file_id(bad)
            raise AssertionError("должно было отвергнуться: %r" % bad)
        except DriveError:
            pass

    calls = []
    state = {"size": 12, "body": b"\x00" * 12}

    class FakeSA:
        def headers(self, scopes, now=None):
            assert scopes == SCOPES
            return {"Authorization": "Bearer AT"}

    def fake_request(url, method="GET", params=None, headers=None, **kw):
        calls.append(("meta", url, params, (headers or {}).get("Authorization")))
        return {"id": "1AbC_dEfGhIjKlMn", "name": "ролик.mp4",
                "size": str(state["size"]), "mimeType": "video/mp4"}

    def fake_download(url, params=None, headers=None, **kw):
        calls.append(("body", url, params, (headers or {}).get("Authorization")))
        return state["body"], "video/mp4"

    real_r, real_d = http.request, http.download
    http.request, http.download = fake_request, fake_download
    try:
        d = Drive(FakeSA())
        meta = d.info("https://drive.google.com/file/d/1AbC_dEfGhIjKlMn/view")
        assert meta["size"] == 12 and isinstance(meta["size"], int), "размер обязан быть числом"
        assert calls[0][3] == "Bearer AT"

        name, content = d.fetch("https://drive.google.com/open?id=1AbC_dEfGhIjKlMn")
        assert (name, content) == ("ролик.mp4", b"\x00" * 12)
        assert calls[-1][2]["alt"] == "media", "без alt=media Google отдаст JSON, а не файл"

        # 🔴 обрезанная закачка не должна пройти как успех
        state["size"] = 99
        try:
            d.fetch("1AbC_dEfGhIjKlMn")
            raise AssertionError("обрезанный файл обязан отвергаться")
        except DriveError as e:
            assert "обрезанным" in str(e), e

        # --- предохранитель по размеру срабатывает ДО скачивания ---
        state["size"] = 500 * 1024 * 1024
        before = len(calls)
        try:
            d.fetch("1AbC_dEfGhIjKlMn", max_mb=200)
            raise AssertionError("перебор размера должен ловиться")
        except DriveError as e:
            assert "предел 200" in str(e), e
        assert all(c[0] != "body" for c in calls[before:]), "тяжелый файл не должен качаться"

        # --- 404 обязан объяснять про расшаривание, а не просто ругаться ---
        def denied(url, method="GET", params=None, headers=None, **kw):
            raise http.HttpError(404, '{"error":{"message":"File not found"}}', url)
        http.request = denied
        try:
            d.info("1AbC_dEfGhIjKlMn")
            raise AssertionError("404 должен подниматься")
        except DriveError as e:
            assert "iam.gserviceaccount.com" in str(e), "ошибка обязана называть причину"
    finally:
        http.request, http.download = real_r, real_d
    print("drive selftest OK: все виды ссылок, alt=media, обрезанная закачка, "
          "предохранитель размера, 404 объясняет расшаривание")


if __name__ == "__main__":
    selftest()
