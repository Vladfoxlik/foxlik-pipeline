# -*- coding: utf-8 -*-
u"""Упаковка поста под каждую площадку. Сеть не нужна.

🔴 Зачем (31.08, по требованию владельца). До этого в обе сети уходил ОДИН
и тот же текст, и это было видно на живом прогоне: в ВК ссылка кликается,
а мы слали туда голый номер артикула; в Instagram ссылка не кликается,
и слать туда ссылку - значит занимать строку мусором.

Правила лежат в `data/площадки.tsv`, а не в коде: у каждой строки источник,
и добавление площадки не требует правки программы.
"""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import platforms as P  # noqa: E402

ПРАВИЛА = (
    "Площадка\tchanel_id\tСсылка кликабельна\tЛимит текста\tХештегов максимум\t"
    "Что добавлять к тексту\tПоля публикации\tЗаголовок нужен\tИсточник\n"
    "vk\t2\tда\t16000\tнет предела\tссылка\tvkontakte_carousel=false\tнет\tдока ВК\n"
    "instagram\t1\tнет\t2200\t30\tартикул\tinstagram_share_to_feed=true\tнет\tдока Meta\n"
    "youtube\t16\tда\t5000\t15\tссылка\tyoutube_privacy_status=1\tда\tдока YouTube\n"
)

АККАУНТЫ = [{"id": 2248535, "chanel_id": 2, "name": "FOXLIK"},
            {"id": 2248551, "chanel_id": 1, "name": "myplayroom_shop"},
            {"id": 999, "chanel_id": 16, "name": "FOXLIK YouTube"}]

ТЕКСТ = u"Принес пульт - ушел рисовать песком. Световой стол не запрещает мультики."


def selftest():
    правила = P.read_rules(io.StringIO(ПРАВИЛА))
    assert set(правила) == {2, 1, 16}, правила

    детали = P.details(АККАУНТЫ, ТЕКСТ, артикул="43287163", file_ids=[7], rules=правила)
    по_аккаунту = {d["account_id"]: d for d in детали}
    assert set(по_аккаунту) == {2248535, 2248551, 999}, по_аккаунту

    # --- ВК: ссылка кликается, значит идет ссылка, а не номер ---------------
    вк = по_аккаунту[2248535]
    assert "wildberries.ru/catalog/43287163/detail.aspx" in вк["content"], вк
    assert "Артикул" not in вк["content"], u"в ВК номер не нужен - там ссылка"
    assert вк["publication_type"] == 4 and вк["file_ids"] == [7]

    # --- Instagram: ссылка не кликается, значит номер ------------------------
    иг = по_аккаунту[2248551]
    assert "#43287163" in иг["content"], иг
    assert "wildberries.ru" not in иг["content"], \
        u"некликабельная ссылка в Instagram - мусор в подписи"
    assert иг.get("instagram_share_to_feed") is True, иг

    # --- YouTube: нужен заголовок ------------------------------------------
    ют = по_аккаунту[999]
    assert ют.get("title"), u"Shorts без заголовка не публикуется"
    assert len(ют["title"]) <= 100, u"заголовок YouTube не длиннее 100 знаков"
    assert ют.get("youtube_privacy_status") == 1, ют

    # --- 🔴 лимит текста режется по площадке, а не по самой длинной ---------
    длинный = u"а" * 3000
    детали2 = P.details(АККАУНТЫ, длинный, артикул="43287163", file_ids=[7],
                        rules=правила)
    по2 = {d["account_id"]: d for d in детали2}
    assert len(по2[2248551]["content"]) <= 2200, u"Instagram обрежет сам и криво"
    assert len(по2[2248535]["content"]) > 2200, u"в ВК резать незачем"

    # --- площадка, которой нет в правилах, не пропадает молча ---------------
    чужой = АККАУНТЫ + [{"id": 555, "chanel_id": 77, "name": "Неизвестная"}]
    try:
        P.details(чужой, ТЕКСТ, артикул="43287163", file_ids=[7], rules=правила)
        raise AssertionError(u"аккаунт без правил обязан быть слышен")
    except P.НетПравил as e:
        assert "555" in str(e) or "77" in str(e), e

    # --- 🔴 без артикула ссылку не выдумываем -------------------------------
    без = P.details(АККАУНТЫ[:1], ТЕКСТ, артикул="", file_ids=[7], rules=правила)
    assert "wildberries" not in без[0]["content"], \
        u"ссылка на пустой артикул ведет в никуда"
    assert без[0]["content"].strip() == ТЕКСТ.strip()

    print("platforms selftest OK: ВК получает ссылку, Instagram артикул, "
          "YouTube заголовок, лимиты по площадке, чужой канал слышен, "
          "пустой артикул не выдумывается")


if __name__ == "__main__":
    selftest()
