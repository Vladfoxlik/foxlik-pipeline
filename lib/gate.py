# -*- coding: utf-8 -*-
"""Гейт репостов и вердикты - единственный экземпляр.

🔴 Аудит 02.09: в колонке «Вердикт» листа МЕТРИКИ жили ДВА словаря слов -
замер API писал «залет/середина/провал», импорт CSV - «прошел/не решено/
провал», а пороги 1,5 и 0,5 были захардкожены в двух местах. Любой будущий
фильтр по вердикту молча терял бы половину строк.
"""

GATE = 1.5      # репостов на 1000 просмотров - порог допуска (ТЗ §3)
LOW = 0.5       # ниже - провал


def per_1000(shares, views):
    """Репостов на 1000 просмотров. Ноль просмотров - «нечего делить», не ноль."""
    if not views or shares is None:
        return None
    return round(shares * 1000.0 / views, 2)


def verdict(rate):
    if rate is None:
        return "нет данных"
    return "залет" if rate >= GATE else ("провал" if rate < LOW else "середина")


def selftest():
    assert per_1000(3, 1000) == 3.0
    assert per_1000(0, 1000) == 0.0
    assert per_1000(5, 0) is None and per_1000(None, 100) is None
    assert verdict(2.0) == "залет" and verdict(1.5) == "залет"
    assert verdict(1.0) == "середина" and verdict(0.4) == "провал"
    assert verdict(None) == "нет данных"
    print("gate selftest OK: пороги и слова вердикта в одном месте")


if __name__ == "__main__":
    selftest()
