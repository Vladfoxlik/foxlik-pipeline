# -*- coding: utf-8 -*-
"""Google-таблица - единственный носитель состояния конвейера.

Почему таблица, а не база: владелец должен видеть и править состояние руками,
без нас и без консоли. Все статусы роликов живут в листе СДАЧИ, и любой отказ
автоматики чинится тем, что человек меняет слово в ячейке.

Работаем через Sheets API v4 напрямую, поверх `google_auth`:
  values.get           прочитать диапазон
  values.update        записать диапазон
  values.append        дописать строку в конец

🔴 Правило адресации. Строки в таблице нумеруются с 1, первая строка - заголовки.
Поэтому строка данных с индексом i (с нуля) лежит в строке листа i + 2. Ошибка
на единицу здесь означает статус, записанный не тому ролику, - проверка на это есть.

⚠️ Живьем не проверено - нужен сервисный аккаунт и расшаренная таблица.
"""
from . import google_auth, http

API = "https://sheets.googleapis.com/v4/spreadsheets/"
SCOPES = [google_auth.SCOPE_SHEETS]
HEADER_ROW = 1                     # заголовки в первой строке листа


class SheetsError(Exception):
    pass


def a1_column(index):
    """0 -> A, 25 -> Z, 26 -> AA. Нужно, чтобы собрать адрес ячейки статуса."""
    if index < 0:
        raise SheetsError("отрицательный номер колонки")
    name = ""
    while True:
        index, rest = divmod(index, 26)
        name = chr(ord("A") + rest) + name
        if index == 0:
            break
        index -= 1
    return name


class Sheet:
    """Один лист таблицы: заголовки плюс строки данных."""

    def __init__(self, sa, spreadsheet_id, title):
        self.sa = sa
        self.sid = spreadsheet_id
        self.title = title
        self.header = []

    # ---------- низкий уровень ----------

    def _url(self, tail):
        return API + self.sid + tail

    def _get(self, rng):
        r = http.request(self._url("/values/" + _quote(rng)),
                         params={"majorDimension": "ROWS"},
                         headers=self.sa.headers(SCOPES))
        if not isinstance(r, dict):
            raise SheetsError("не тот ответ на чтение %s: %s" % (rng, r))
        return r.get("values", [])

    def _put(self, rng, values):
        return http.request(
            self._url("/values/" + _quote(rng)),
            method="PUT",
            params={"valueInputOption": "USER_ENTERED"},
            headers=dict(self.sa.headers(SCOPES),
                         **{"Content-Type": "application/json"}),
            raw_body=_json_body({"range": rng, "majorDimension": "ROWS",
                                 "values": values}))

    # ---------- рабочий уровень ----------

    def read(self):
        """Все строки листа словарями по заголовку. Добавляет служебное поле _row."""
        rows = self._get(self.title)
        if not rows:
            self.header = []
            return []
        self.header = [str(c).strip() for c in rows[0]]
        out = []
        for i, row in enumerate(rows[1:]):
            item = {name: (row[j] if j < len(row) else "")
                    for j, name in enumerate(self.header)}
            item["_row"] = i + HEADER_ROW + 1        # адрес этой строки в листе
            out.append(item)
        return out

    def column_letter(self, name):
        if name not in self.header:
            raise SheetsError("в листе %s нет колонки «%s», есть: %s"
                              % (self.title, name, ", ".join(self.header) or "ничего"))
        return a1_column(self.header.index(name))

    def set(self, row, column_name, value):
        """Пишет одну ячейку. row - это _row из read(), уже с поправкой на заголовок."""
        cell = "%s!%s%s" % (self.title, self.column_letter(column_name), row)
        return self._put(cell, [[value]])

    def set_many(self, row, pairs):
        """Несколько ячеек одной строки. Каждая - отдельный запрос, их единицы за такт."""
        return [self.set(row, name, value) for name, value in pairs.items()]

    def clear(self, keep_header=True):
        """Стирает содержимое листа. Нужно, когда залили не то и надо переналить."""
        first = (HEADER_ROW + 1) if keep_header else 1
        rng = "%s!A%s:ZZ" % (self.title, first)
        return http.request(self._url("/values/" + _quote(rng) + ":clear"),
                            method="POST",
                            headers=dict(self.sa.headers(SCOPES),
                                         **{"Content-Type": "application/json"}),
                            raw_body=b"{}")

    def append(self, values_by_name):
        """Дописывает строку в конец. Порядок берется из заголовка, а не из словаря."""
        if not self.header:
            self.read()
        row = [values_by_name.get(name, "") for name in self.header]
        return http.request(
            self._url("/values/" + _quote(self.title) + ":append"),
            method="POST",
            params={"valueInputOption": "USER_ENTERED",
                    "insertDataOption": "INSERT_ROWS"},
            headers=dict(self.sa.headers(SCOPES),
                         **{"Content-Type": "application/json"}),
            raw_body=_json_body({"values": [row]}))


class Book:
    """Вся таблица целиком: список листов и заведение недостающих."""

    def __init__(self, sa, spreadsheet_id):
        self.sa = sa
        self.sid = spreadsheet_id

    def titles(self):
        r = http.request(API + self.sid, params={"fields": "sheets.properties.title"},
                         headers=self.sa.headers(SCOPES))
        return [s["properties"]["title"] for s in (r or {}).get("sheets", [])]

    def add(self, title):
        """Заводит лист. Уже существующий не трогает - повтор безопасен."""
        if title in self.titles():
            return False
        http.request(API + self.sid + ":batchUpdate", method="POST",
                     headers=dict(self.sa.headers(SCOPES),
                                  **{"Content-Type": "application/json"}),
                     raw_body=_json_body({"requests": [
                         {"addSheet": {"properties": {"title": title}}}]}))
        return True

    def sheet(self, title):
        return Sheet(self.sa, self.sid, title)


def _quote(rng):
    import urllib.parse
    return urllib.parse.quote(rng, safe="")


def _json_body(obj):
    import json
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def selftest():
    import json as _json

    # --- адрес колонки ---
    assert [a1_column(i) for i in (0, 1, 25, 26, 27, 51, 52)] == \
        ["A", "B", "Z", "AA", "AB", "AZ", "BA"]

    calls = []
    table = [["ID", "Статус", "Дата публикации", "Причина отказа"],
             ["P26-01", "ОПУБЛИКОВАН", "2026-09-03", ""],
             ["P26-02", "ОДОБРЕН", "2026-09-04"],          # хвост короче заголовка
             ["P26-03", "ПРИНЯТ"]]

    def fake(url, method="GET", params=None, data=None, headers=None, raw_body=None, **kw):
        calls.append({"method": method, "url": url, "params": params,
                      "body": _json.loads(raw_body.decode("utf-8")) if raw_body else None,
                      "auth": (headers or {}).get("Authorization")})
        if method == "GET":
            return {"values": table}
        return {"updatedCells": 1}

    class FakeSA:
        def headers(self, scopes, now=None):
            assert scopes == SCOPES, scopes
            return {"Authorization": "Bearer AT"}

    real, http.request = http.request, fake
    try:
        sheet = Sheet(FakeSA(), "SID", "СДАЧИ")
        rows = sheet.read()
        assert calls[0]["auth"] == "Bearer AT", "чтение обязано ходить с токеном"

        # --- разбор строк ---
        assert len(rows) == 3
        assert rows[0]["ID"] == "P26-01" and rows[0]["Статус"] == "ОПУБЛИКОВАН"
        assert rows[1]["Причина отказа"] == "", "короткая строка добивается пустыми"
        assert rows[2]["Дата публикации"] == ""

        # 🔴 главное: адрес строки. Данные с индексом 0 лежат во ВТОРОЙ строке листа
        assert [r["_row"] for r in rows] == [2, 3, 4], [r["_row"] for r in rows]

        # --- запись статуса попадает в ту самую строку и колонку ---
        sheet.set(rows[2]["_row"], "Статус", "НА_ПРИЕМКЕ")
        put = calls[-1]
        assert put["method"] == "PUT"
        assert put["url"].endswith("%D0%A1%D0%94%D0%90%D0%A7%D0%98%21B4"), put["url"]
        assert put["body"]["values"] == [["НА_ПРИЕМКЕ"]]
        assert put["params"]["valueInputOption"] == "USER_ENTERED"

        # --- несуществующая колонка обязана назваться, а не молча промахнуться ---
        try:
            sheet.set(2, "Статуc", "X")          # латинская c в конце - опечатка
            raise AssertionError("опечатка в имени колонки должна ловиться")
        except SheetsError as e:
            assert "Статуc" in str(e) and "Статус" in str(e), e

        # --- дописывание идет в порядке заголовка, а не словаря ---
        sheet.append({"Причина отказа": "первые 3 сек пустые", "ID": "P26-04"})
        add = calls[-1]
        assert add["method"] == "POST" and add["url"].endswith(":append")
        assert add["body"]["values"] == [["P26-04", "", "", "первые 3 сек пустые"]]

        # --- заведение листов ---
        existing = {"sheets": [{"properties": {"title": "СДАЧИ"}},
                               {"properties": {"title": "ПЛАН"}}]}

        def fake_book(url, method="GET", params=None, headers=None, raw_body=None, **kw):
            calls.append({"method": method, "url": url,
                          "body": _json.loads(raw_body.decode("utf-8")) if raw_body else None})
            return existing if method == "GET" else {"replies": [{}]}

        http.request = fake_book
        book = Book(FakeSA(), "SID")
        assert book.titles() == ["СДАЧИ", "ПЛАН"]
        assert book.add("СДАЧИ") is False, "существующий лист трогать нельзя"
        assert calls[-1]["method"] == "GET", "лишнего запроса на существующий не должно быть"
        assert book.add("МЕТРИКИ") is True
        add = calls[-1]
        assert add["url"].endswith(":batchUpdate")
        assert add["body"]["requests"][0]["addSheet"]["properties"]["title"] == "МЕТРИКИ"

        # --- пустой лист не должен падать ---
        http.request = fake
        table = []
        assert Sheet(FakeSA(), "SID", "ПУСТО").read() == []
    finally:
        http.request = real
    print("sheets selftest OK: адрес колонки, поправка на заголовок, короткие строки, "
          "опечатка в колонке названа, порядок при дописывании, заведение листов")


if __name__ == "__main__":
    selftest()
