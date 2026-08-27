# -*- coding: utf-8 -*-
"""Проверка замера на Д7: гейт, продление токена, что чем считается.

Сеть не нужна. Проверяется то, чем замер портит петлю: посчитанный не тот гейт,
повторный замер того же ролика, молча умерший токен, подписки, которых API не дает.
"""
import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import metrics as M  # noqa: E402
from lib import http  # noqa: E402
from tests.test_tick import FakeSheet, FakeBot  # noqa: E402

TODAY = datetime.date(2026, 9, 15)


class FakeIG:
    token = "TOKEN"

    def _url(self, path):
        return "https://graph.instagram.com/v23.0/" + path


def pub(pid, date, media="M1", platform="instagram"):
    return {"ID": pid, "Дата": date, "Площадка": platform,
            "Ссылка": "https://instagram.com/reel/x", "Медиа ID": media}


def build(pubs, measured=(), answers=None, today=TODAY):
    m = M.Metrics(ig=FakeIG(), pubs=FakeSheet(list(pubs)),
                  metrics_sheet=FakeSheet(list(measured)),
                  settings=FakeSheet([]), bot=FakeBot(), today=today)
    m._answers = answers or {}
    return m


def selftest():
    # --- счет гейта ---
    assert M.per_1000(15, 10000) == 1.5
    assert M.per_1000(3, 10000) == 0.3
    assert M.per_1000(5, 0) is None, "ноль просмотров - нечего делить, а не ноль репостов"
    assert M.verdict(1.5) == "залет" and M.verdict(4.2) == "залет"
    assert M.verdict(0.49) == "провал" and M.verdict(0.9) == "середина"
    assert M.verdict(None) == "нет данных"
    assert M.GATE == 1.5, "порог задан в ТЗ §3, менять только вместе с ним"

    calls = []
    state = {"insights": {"views": 10000, "reach": 8000, "shares": 20,
                          "saved": 5, "comments": 3, "likes": 100},
             "extra": {"reels_skip_rate": 0.42, "ig_reels_avg_watch_time": 4200,
                       "total_interactions": 128},
             "refresh": {"access_token": "NEWTOKEN", "token_type": "bearer",
                         "expires_in": 60 * 86400}}

    def fake(url, method="GET", params=None, **kw):
        calls.append((url, dict(params or {})))
        if "refresh_access_token" in url:
            return state["refresh"]
        wanted = (params or {}).get("metric", "")
        src = state["extra"] if "skip_rate" in wanted else state["insights"]
        if isinstance(src, Exception):
            raise src
        # ответ Meta приходит именно в такой форме: список, число лежит во вложенном values
        return {"data": [{"name": k, "period": "lifetime", "values": [{"value": v}]}
                         for k, v in src.items()]}

    real, http.request = http.request, fake
    try:
        # --- продление токена ---
        m = build([])
        assert m.refresh_token() == 60
        url, params = calls[0]
        assert "refresh_access_token" in url
        assert params["grant_type"] == "ig_refresh_token", "иначе Meta откажет"
        saved = {r["Ключ"]: r["Значение"] for r in m.settings.rows}
        assert saved["IG_TOKEN"] == "NEWTOKEN", "продленный токен обязан лечь в таблицу"
        assert saved["IG_TOKEN_ДО"] == "2026-11-14", saved["IG_TOKEN_ДО"]
        assert not m.bot.notes, "нормальное продление владельца не беспокоит"

        # 🔴 короткий срок - тревога: не продлим за 60 дней, токен умрет насовсем
        state["refresh"] = dict(state["refresh"], expires_in=5 * 86400)
        m = build([])
        m.refresh_token()
        assert any("умрет" in n for n in m.bot.notes), m.bot.notes
        state["refresh"] = dict(state["refresh"], expires_in=60 * 86400)

        # --- кого берем в замер ---
        m = build([pub("P1-01", "2026-09-01"),          # 14 дней - пора
                   pub("P1-02", "2026-09-08"),          # ровно 7 - пора
                   pub("P1-03", "2026-09-12"),          # 3 дня - рано
                   pub("P1-04", "2026-09-01", platform="vk"),      # не Instagram
                   pub("P1-05", "2026-09-01", media="")])          # без медиа id
        due = [r["ID"] for r in m.due()]
        assert due == ["P1-01", "P1-02"], due

        # 🔴 уже замеренное второй раз не берем
        m = build([pub("P1-01", "2026-09-01")], measured=[{"ID": "P1-01"}])
        assert m.due() == [], "повторный замер испортил бы историю"

        # --- сам замер ---
        m = build([pub("P1-07", "2026-09-01")])
        m.run()
        row = m.metrics.rows[0]
        assert row["Репосты"] == 20 and row["Просмотры"] == 10000
        assert row["Репосты/1000"] == 2.0 and row["Вердикт"] == "залет"
        assert row["Дата замера"] == "2026-09-15"
        # 🔴 подписок с ролика API не отдает - поле обязано остаться пустым,
        # а не заполниться нулем, который потом прочитают как «подписок не было»
        assert row["Подписки"] == "", "нулем это заполнять нельзя"
        assert row["Пропуск первых 3 сек"] == 0.42
        assert any("Замер на 7-й день" in n for n in m.bot.notes), m.bot.notes
        assert any("Прошли гейт" in n for n in m.bot.notes)

        # --- метрики «на будущее» падают, основной замер продолжается ---
        state["extra"] = RuntimeError("metric not supported")
        m = build([pub("P1-08", "2026-09-01")])
        m.run()
        row = m.metrics.rows[0]
        assert row["Вердикт"] == "залет", "отказ по необязательным метрикам не отменяет замер"
        assert row["Пропуск первых 3 сек"] == ""
        state["extra"] = {"reels_skip_rate": 0.42, "ig_reels_avg_watch_time": 4200}

        # --- провал считается провалом ---
        state["insights"] = dict(state["insights"], shares=2)
        m = build([pub("P1-09", "2026-09-01")])
        m.run()
        assert m.metrics.rows[0]["Вердикт"] == "провал"
        assert any("Не прошли" in n for n in m.bot.notes)
        state["insights"] = dict(state["insights"], shares=20)

        # --- отказ продления не отменяет замер ---
        def refuse(url, method="GET", params=None, **kw):
            if "refresh_access_token" in url:
                raise http.HttpError(400, '{"error":{"message":"token expired"}}', url)
            return fake(url, method, params, **kw)
        http.request = refuse
        m = build([pub("P1-10", "2026-09-01")])
        m.run()
        assert m.metrics.rows, "замер обязан пройти, даже если токен не продлился"
        assert any("не продлился" in n for n in m.bot.notes)
        http.request = fake

        # --- 🔴 секрет не утекает в публичный лог ---
        state["insights"] = RuntimeError(
            "HTTP 400 на https://graph.instagram.com/x?access_token=EAAsecretVALUE")
        m = build([pub("P1-11", "2026-09-01")])
        log = " ".join(m.run())
        assert "EAAsecretVALUE" not in log, "секрет утек в лог"
        assert "EAAsecretVALUE" not in " ".join(m.bot.notes), "секрет утек владельцу"
        assert "P1-11" in log, "но факт отказа в логе быть обязан"
    finally:
        http.request = real
    # --- нет токена Instagram: это не поломка, а «замерять пока нечем» ---
    # 🔴 Замер 27.08: без IG_TOKEN main() выходил с ошибкой, то есть ежедневное
    # задание краснело бы каждый день до самого получения токена. Вечно красная
    # тревога перестает быть тревогой: настоящий отказ в ней уже не разглядеть.
    saved = {k: os.environ.pop(k, None) for k in ("IG_TOKEN", "IG_USER_ID")}
    try:
        code = M.main()
        assert code in (None, 0), "без токена замер обязан выходить спокойно, а не падать"
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v

    print("metrics selftest OK: счет гейта, продление токена и тревога, отбор на Д7, "
          "нет повторов, подписки пустые, необязательные метрики не роняют замер, секреты, "
          "отсутствие токена не красит задание")


if __name__ == "__main__":
    selftest()
