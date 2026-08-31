# -*- coding: utf-8 -*-
u"""Упаковка одного ролика под каждую площадку.

🔴 Зачем (31.08, требование владельца). До этого в ВК и Instagram уходил один
и тот же текст. На живом прогоне это стало видно: **в ВК ссылка кликается**,
а мы слали туда голый номер артикула - человек не мог перейти к товару.
**В Instagram ссылка не кликается** - и слать туда ссылку значит занимать
строку подписи мусором, который нельзя нажать.

Один ролик - один смысл, но упаковка у каждой сети своя:

| Площадка | Что получает | Почему |
|---|---|---|
| ВК | текст + **прямая ссылка** на карточку WB | ссылки кликаются |
| Instagram | текст + **артикул номером**, дубль в ленту профиля | ссылки не кликаются |
| YouTube | текст + ссылка + **заголовок** | Shorts без заголовка не публикуется |
| TikTok | текст + артикул | ссылки без верификации не кликаются |

Правила живут в `data/площадки.tsv` с источником у каждой строки: добавить
площадку или поменять лимит можно, не трогая код.
"""

# Шаблон карточки товара на Wildberries. Проверен браузером 31.08: открывается
# «FOXLIK / Детский игровой развивающий световой стол песочница Алфавит».
# ⚠️ Проверять можно только глазами: на запрос из кода WB отвечает 498
# (защита от ботов), и «ссылка не работает» из такого ответа НЕ следует.
WB_URL = "https://www.wildberries.ru/catalog/%s/detail.aspx"

TITLE_LIMIT = 100          # заголовок YouTube Shorts


class НетПравил(Exception):
    u"""Аккаунт есть, а правил для его площадки нет. Молчать нельзя: пост уйдет
    с чужой упаковкой - со ссылкой туда, где ссылки не работают."""


def read_rules(stream):
    u"""Правила площадок из TSV. Ключ - chanel_id (число, как в API сервиса)."""
    rows = [line.rstrip("\n").split("\t") for line in stream if line.strip()]
    head = [c.strip() for c in rows[0]]
    out = {}
    for raw in rows[1:]:
        item = dict(zip(head, [c.strip() for c in raw]))
        try:
            канал = int(item.get("chanel_id") or 0)
        except ValueError:
            continue
        if not канал:
            continue
        out[канал] = {
            "площадка": item.get("Площадка", ""),
            "ссылка": (item.get("Ссылка кликабельна", "") or "").lower().startswith("да"),
            "лимит": _int(item.get("Лимит текста"), 100000),
            "добавлять": (item.get("Что добавлять к тексту") or "").strip(),
            "поля": _fields(item.get("Поля публикации")),
            "заголовок": (item.get("Заголовок нужен", "") or "").lower().startswith("да"),
            "источник": item.get("Источник", ""),
        }
    return out


def _int(value, default):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _fields(text):
    u"""«youtube_privacy_status=1,tiktok_comment=true» -> {поле: значение}."""
    out = {}
    for кусок in (text or "").split(","):
        if "=" not in кусок:
            continue
        имя, значение = кусок.split("=", 1)
        имя, значение = имя.strip(), значение.strip().lower()
        if not имя:
            continue
        if значение in ("true", "да"):
            out[имя] = True
        elif значение in ("false", "нет"):
            out[имя] = False
        else:
            out[имя] = _int(значение, значение)
    return out


def details(accounts, text, артикул, file_ids, rules, publication_type=4):
    u"""Детали публикации: по одной на аккаунт, каждая упакована по своим правилам."""
    out = []
    for acc in accounts:
        канал = acc.get("chanel_id")
        правило = rules.get(канал)
        if правило is None:
            raise НетПравил(
                u"аккаунт %s (канал %s) не описан в data/площадки.tsv - "
                u"непонятно, как упаковывать пост" % (acc.get("id"), канал))
        деталь = {"publication_type": publication_type,
                  "account_id": acc.get("id"),
                  "file_ids": list(file_ids),
                  "content": _text_for(text, артикул, правило)}
        деталь.update(правило["поля"])
        if правило["заголовок"]:
            деталь["title"] = _title(text)
        out.append(деталь)
    return out


def _text_for(text, артикул, правило):
    u"""Текст под площадку: ссылка там, где она кликается, номер - где нет."""
    хвост = ""
    if артикул:
        if правило["добавлять"] == "ссылка":
            хвост = WB_URL % артикул
        elif правило["добавлять"] == "артикул":
            хвост = u"🛒 Артикул на WB: #%s" % артикул
    целиком = (text.strip() + ("\n\n" + хвост if хвост else "")).strip()
    лимит = правило["лимит"]
    if len(целиком) <= лимит:
        return целиком
    # 🔴 Режем МЫ, а не площадка: она обрежет по своему усмотрению и может
    # оставить пост без хвоста со ссылкой. Хвост сохраняем, режем начало.
    склейка = u"...\n\n" if хвост else u"..."
    место = лимит - len(хвост) - len(склейка)
    итог = (text.strip()[:max(место, 0)].rstrip() + склейка + хвост).strip()
    return итог[:лимит]


def _title(text):
    u"""Заголовок Shorts: первая фраза текста, не длиннее предела площадки."""
    первая = text.strip().split(".")[0].strip() or text.strip()
    return первая[:TITLE_LIMIT].rstrip()


def selftest():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tests import test_platforms
    test_platforms.selftest()


if __name__ == "__main__":
    selftest()
