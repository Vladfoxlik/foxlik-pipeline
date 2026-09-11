# -*- coding: utf-8 -*-
"""Проверка доктора: отвалившаяся площадка обязана быть слышной.

🔴 Чем оплачено. 05.09 Instagram отключился от Postmypost, доктор напечатал
«аккаунтов подключено: 1» и следом «🎉 все узлы отвечают» - и отвал прошел мимо
глаз. Владелец узнал о нем из письма сервиса про непрошедшую публикацию.
Доктор обязан отличать «все на месте» от «половина площадок отпала».
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import live_check as L  # noqa: E402


def selftest():
    оба = [{"name": "myplayroom_shop", "connection_status": 1},
           {"name": "FOXLIK ВК", "connection_status": 1}]
    строка = L.описать_аккаунты(оба)
    assert "2" in строка and "myplayroom_shop" in строка, строка

    # 🔴 отвал - это отказ проверки, а не примечание в строке успеха
    один_отпал = [{"name": "myplayroom_shop", "connection_status": 2},
                  {"name": "FOXLIK ВК", "connection_status": 1}]
    try:
        L.описать_аккаунты(один_отпал)
        assert False, "отключенная площадка обязана валить проверку, а не молчать"
    except RuntimeError as e:
        assert "myplayroom_shop" in str(e), e
        assert "FOXLIK ВК" not in str(e).split("подключено")[0], (
            "в тексте отказа названы именно отпавшие: %s" % e)

    # площадок нет вовсе - тоже отказ: публиковать некуда
    try:
        L.описать_аккаунты([])
        assert False, "пустой список площадок - не повод рапортовать об успехе"
    except RuntimeError:
        pass

    print("live_check selftest OK: отвал площадки слышен, пустой список тоже")


if __name__ == "__main__":
    selftest()
