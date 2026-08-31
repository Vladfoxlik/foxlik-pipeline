# -*- coding: utf-8 -*-
"""Разведка чужих залетов - вход снаружи (Ш4 в МОДЕЛЬ_КОНТЕНТА).

**Зачем.** Аудит 29.08: система крутится в семи собственных приемах, новых осей
взяться неоткуда. Внешний сбор дает гипотезы на проверку - что залетает
у аудитории, похожей на нашу.

🔴 **Рамка, которую надо держать в голове.** Apify **не отдает репосты** у чужих
аккаунтов (замер 20.07): в выдаче просмотры, лайки, комментарии. А репосты -
наша метрика победы. Значит:

> **внешний вход - генератор ГИПОТЕЗ, а не источник выводов.**

Оттуда берется идея - сюжет, прием, поворот, - которая дальше проверяется
на нашей партии нашей метрикой. Ждать от него ответа «вот что работает» нельзя:
он ответит «вот что много смотрят», а это другой вопрос.

**Деньги.** План FREE, $5 кредитов в месяц, живые деньги не списываются.
Ставка по замеру 20.07: 199 роликов с 10 аккаунтов = $0.52, то есть ~$0.0026
за ролик. Скромный режим (12 аккаунтов по 20 роликов дважды в месяц) - $1.25/мес.

🔴 **Расход проверяется ДО запуска.** Скрипт спрашивает остаток и отказывается
работать, если после прогона квота ушла бы в минус. Молча потратить чужую
квоту нельзя - правило от 25.08.

    python scout.py --check                 # только остаток, ничего не тратит
    python scout.py --probe @detsky_stul    # пробный прогон на одном аккаунте
    python scout.py --run                   # полный сбор по списку источников
"""
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.apify.com/v2"
ACTOR = "apify~instagram-scraper"

# 🔴 Ставка снята замером 20.07 на живом прогоне, не из прайса: 199 роликов
# с 10 аккаунтов стоили $0.52. Пересчитывать после каждого прогона - тарифы
# меняются, а вывод «дешево» сделан один раз и устаревает молча.
# 🔴 Ставка уточнена сверкой 29.08. Замер СРАЗУ после прогона давал 0.0019-0.0026
# за запись, а квота за сессию упала на $0.32 при 74 записях - втрое больше.
# Причина: Apify начисляет с задержкой (хранение датасетов, платформенные
# единицы), и мгновенная разница после прогона занижает расход. Мерить надо
# разницей за сутки, а не за минуту.
USD_PER_ITEM = 0.0045
SAFETY = 0.50          # столько кредитов оставляем нетронутыми на всякий случай

SOURCES = "data/источники.tsv"
OUT = "data/разведка"


def _call(path, method="GET", body=None, token=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=120)
        return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, e.read(800).decode("utf-8", "replace")
    except Exception as e:
        return "ERR", str(e)


def balance(token):
    """Сколько кредитов осталось. Только чтение, денег не тратит."""
    код, u = _call("/users/me", token=token)
    if код != 200:
        raise RuntimeError("Apify не ответил на /users/me: %s" % код)
    план = (u.get("data", {}).get("plan") or {})
    лимит = float(план.get("maxMonthlyUsageUsd") or 0)
    код, l = _call("/users/me/limits", token=token)
    потрачено = 0.0
    if код == 200:
        потрачено = float((l.get("data", {}).get("current") or {})
                          .get("monthlyUsageUsd") or 0)
    return {"план": план.get("id"), "лимит": лимит, "потрачено": потрачено,
            "остаток": лимит - потрачено}


def affordable(остаток, items):
    """Влезает ли прогон в остаток квоты. Считаем ДО запуска, а не после."""
    нужно = items * USD_PER_ITEM
    return (нужно <= остаток - SAFETY), нужно


def read_sources(path):
    rows = [l.rstrip("\n").split("\t")
            for l in io.open(path, encoding="utf-8-sig") if l.strip()]
    head = [c.strip() for c in rows[0]]
    out = []
    for r in rows[1:]:
        item = dict(zip(head, [c.strip() for c in r]))
        if item.get("Аккаунт") and (item.get("Берем") or "").lower() != "нет":
            out.append(item)
    return out


def scrape(token, accounts, limit):
    """Запуск актора и ожидание результата. Возвращает список постов."""
    body = {"directUrls": ["https://www.instagram.com/%s/" % a.lstrip("@")
                           for a in accounts],
            "resultsType": "posts",
            "resultsLimit": limit}
    код, run = _call("/acts/%s/runs" % ACTOR, "POST", body, token)
    if код not in (200, 201):
        raise RuntimeError("запуск не удался: %s %s" % (код, str(run)[:300]))
    run_id = run["data"]["id"]
    dataset = run["data"]["defaultDatasetId"]
    print("запуск %s, ждем..." % run_id)

    for _ in range(120):                      # до 20 минут, шаг 10 секунд
        time.sleep(10)
        код, st = _call("/actor-runs/%s" % run_id, token=token)
        if код != 200:
            continue
        status = st["data"]["status"]
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            print("статус: %s" % status)
            if status != "SUCCEEDED":
                raise RuntimeError("прогон завершился как %s" % status)
            break
    else:
        raise RuntimeError("прогон не завершился за 20 минут")

    код, items = _call("/datasets/%s/items" % dataset, token=token)
    if код != 200:
        raise RuntimeError("датасет не прочитался: %s" % код)
    return items


def digest(items, only_video=False):
    """Что взять из выдачи. Репостов тут нет и не будет - см. шапку модуля.

    🔴 `only_video` появился после первого прогона 29.08: у @detsky_stul
    **18 записей из 20 имели ноль просмотров** - это были фото и карусели,
    у которых `videoPlayCount` не заполняется в принципе. Считать по ним
    медиану значило бы получить ноль и сделать вывод «у конкурента не смотрят».
    Тип поста лежит в `type`/`productType`, фильтруем по нему.
    """
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        тип = it.get("type") or ""
        подтип = it.get("productType") or ""
        if only_video and тип != "Video":
            continue
        музыка = it.get("musicInfo") or {}
        out.append({
            "аккаунт": it.get("ownerUsername") or "",
            "url": it.get("url") or "",
            "дата": (it.get("timestamp") or "")[:10],
            "тип": ("клип" if подтип == "clips" else тип.lower()) or "",
            # 🔴 Верить videoPlayCount, а не videoViewCount - записано в CLAUDE.md
            # и подтверждено 29.08: 43 561 против 16 593 на одном ролике.
            "просмотры": it.get("videoPlayCount") or 0,
            "лайки": it.get("likesCount") or 0,
            "комментарии": it.get("commentsCount") or 0,
            "длина": it.get("videoDuration") or "",
            # Трендовый звук - отдельная гипотеза: через API его не поставить
            # (АВТОПУБЛИКАЦИЯ §3), но знать, на чем залетают у других, полезно.
            "звук": (музыка.get("song_name") or _artist(музыка) or "")[:60],
            "текст": (it.get("caption") or "").replace("\t", " ").replace("\n", " ")[:400],
        })
    return out


def _artist(музыка):
    """Имя исполнителя, если названия трека нет."""
    return (музыка or {}).get("artist_name") or ""


def selftest():
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests"))
    import test_scout
    test_scout.selftest()


def main():
    if "--selftest" in sys.argv:
        selftest()
        return
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        print("APIFY_TOKEN не задан - разведка не запускается")
        return
    b = balance(token)
    print("план %s · лимит $%.2f · потрачено $%.2f · остаток $%.2f"
          % (b["план"], b["лимит"], b["потрачено"], b["остаток"]))
    if "--check" in sys.argv:
        return

    корень = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if "--probe" in sys.argv:
        i = sys.argv.index("--probe")
        accounts = [sys.argv[i + 1]]
        limit = 20
    else:
        src = read_sources(os.path.join(корень, SOURCES))
        accounts = [s["Аккаунт"] for s in src]
        limit = 20

    ок, нужно = affordable(b["остаток"], len(accounts) * limit)
    print("к сбору: %d аккаунтов по %d роликов = %d, ожидаемая цена $%.2f"
          % (len(accounts), limit, len(accounts) * limit, нужно))
    if not ок:
        print("⛔ не запускаю: остаток $%.2f, нужно $%.2f плюс запас $%.2f"
              % (b["остаток"], нужно, SAFETY))
        return

    items = scrape(token, accounts, limit)
    if "--raw" in sys.argv:
        # Сырье нужно ровно один раз: чтобы увидеть поля выдачи глазами,
        # а не гадать, почему у 18 записей из 20 ноль просмотров.
        сырое = os.path.join(корень, OUT, "сырье.json")
        with io.open(сырое, "w", encoding="utf-8") as f:
            f.write(json.dumps(items[:3], ensure_ascii=False, indent=1))
        print("сырье первых трех записей -> %s" % сырое)
    rows = digest(items, only_video=True)
    всего = len(digest(items))
    if всего != len(rows):
        print("отброшено не-видео: %d из %d (у фото просмотры не считаются)"
              % (всего - len(rows), всего))
    папка = os.path.join(корень, OUT)
    if not os.path.isdir(папка):
        os.makedirs(папка)
    имя = os.path.join(папка, "разведка_%s.tsv" % time.strftime("%Y-%m-%d"))
    шапка = ["аккаунт", "url", "дата", "тип", "просмотры", "лайки",
             "комментарии", "длина", "звук", "текст"]
    with io.open(имя, "w", encoding="utf-8", newline="\n") as f:
        f.write("\t".join(шапка) + "\n")
        for r in rows:
            f.write("\t".join(str(r[k]) for k in шапка) + "\n")
    print("собрано %d записей -> %s" % (len(rows), имя))

    после = balance(token)
    факт = после["потрачено"] - b["потрачено"]
    print("ФАКТ: потрачено $%.4f на %d записей = $%.5f за запись "
          "(в расчете было $%.5f)"
          % (факт, len(rows), факт / max(len(rows), 1), USD_PER_ITEM))


if __name__ == "__main__":
    main()
