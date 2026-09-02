# -*- coding: utf-8 -*-
"""Единый разбор дат из таблиц и выгрузок.

🔴 Аудит 02.09: в проекте жили ПЯТЬ копий разбора даты с ТРЕМЯ разными
поведениями. tick/metrics/handout знали серийные числа Google («46269»),
generator возвращал None (и отказывался предлагать партию по живому листу),
dictionary давал date.max (и молча ломал хронологию выгорания). Одна функция -
одно поведение; расхождение копий уже стоило двух красных находок аудита.
"""
import datetime

# Google хранит даты числом дней от этой точки и отдает его строкой,
# когда формат ячейки не задан («46269» вместо «2026-09-04»).
GOOGLE_EPOCH = datetime.date(1899, 12, 30)

_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y")
# Выгрузка Business Suite пишет «Время публикации» по-американски:
# MM/DD/YYYY. Явный флаг, а не угадывание: «01/02/2026» неоднозначна.
_FORMATS_US = ("%Y-%m-%d", "%m/%d/%Y")


def as_date(value, us=False):
    """Дата или None. Понимает ISO, русские форматы и серийные числа Google.

    us=True - американский порядок (месяц первым), для колонок выгрузки
    Business Suite. Ничего не распознали - None: решение «что значит
    нечитаемая дата» принимает вызывающий, у такта и словаря оно разное.
    """
    text = str(value or "").strip()[:10]
    if not text:
        return None
    for fmt in (_FORMATS_US if us else _FORMATS):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    if text.isdigit() and len(text) >= 5:
        try:
            return GOOGLE_EPOCH + datetime.timedelta(days=int(text))
        except (ValueError, OverflowError):
            return None
    return None


def selftest():
    д = datetime.date(2026, 9, 4)
    assert as_date("2026-09-04") == д
    assert as_date("04.09.2026") == д
    assert as_date("04/09/2026") == д
    assert as_date("46269") == д, "серийное число Google обязано читаться"
    assert as_date(46269) == д, "и числом, не только строкой"
    assert as_date("09/04/2026", us=True) == д, "выгрузка BS: месяц первым"
    assert as_date("") is None and as_date(None) is None
    assert as_date("За всё время") is None
    assert as_date("05.09") is None, "дата без года - не дата"
    assert as_date("123") is None, "короткое число - не серийная дата"
    print("dates selftest OK: ISO, русские, серийные Google, американский BS")


if __name__ == "__main__":
    selftest()
