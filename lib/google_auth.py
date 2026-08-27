# -*- coding: utf-8 -*-
"""Вход в Google по сервисному аккаунту - без единой внешней библиотеки.

Зачем свой код. Google пускает сервисный аккаунт так: собрать JWT, подписать его
алгоритмом **RS256** и обменять на access_token. RS256 - это RSA, а в стандартной
библиотеке Python RSA нет: `hashlib` умеет только хеш (замерено 27.08 - в venv
нет ни `cryptography`, ни `rsa`, ни `jwt`, ни `google-auth`).

Ставить зависимость не стали: правило проекта - код запускается и локально без
`pip install`, и в GitHub Actions без шага установки. Вся математика RSA - это
одно возведение в степень по модулю, `pow(m, d, n)`, и оно в Python встроено.

✅ **Сверено с openssl 3.5.5 (27.08.2026):** на выброшенном ключе ниже подпись
этого модуля совпала с `openssl dgst -sha256 -sign` побайтно. Проверка живет
в selftest и падает, если формат разъедется.

Секрет берется из переменной `GOOGLE_SA_JSON` - целиком JSON сервисного аккаунта.
"""
import base64
import hashlib
import json
import os
import time

from . import http

KEY_FILE = ".google_sa.json"     # как называется ключ локально, закрыт от git
TOKEN_URI = "https://oauth2.googleapis.com/token"
GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
LIFETIME = 3600          # предел Google на JWT сервисного аккаунта
EARLY = 60               # обновляем за минуту до конца, чтобы не поймать край

# DigestInfo для SHA-256 из RFC 8017. Идет перед хешем внутри подписи.
SHA256_DER = bytes.fromhex("3031300d060960864801650304020105000420")

SCOPE_SHEETS = "https://www.googleapis.com/auth/spreadsheets"
SCOPE_DRIVE = "https://www.googleapis.com/auth/drive"


class GoogleAuthError(Exception):
    pass


# ---------- разбор ключа ----------

def _tlv(buf, i):
    """Один элемент ASN.1: возвращает (тег, тело, индекс следующего).

    Границы проверяются на каждом шаге: обрезанный ключ должен давать понятную
    ошибку, а не IndexError из глубины разбора.
    """
    if i + 2 > len(buf):
        raise GoogleAuthError("ключ обрывается на середине элемента ASN.1")
    tag = buf[i]
    length = buf[i + 1]
    i += 2
    if length & 0x80:                      # длинная форма: младшие биты - число байт длины
        n = length & 0x7F
        if i + n > len(buf):
            raise GoogleAuthError("ключ обрывается на длине элемента ASN.1")
        length = int.from_bytes(buf[i:i + n], "big")
        i += n
    if i + length > len(buf):
        raise GoogleAuthError("элемент ASN.1 длиннее самого ключа")
    return tag, buf[i:i + length], i + length


def _seq(body):
    """Разбирает тело SEQUENCE в список элементов."""
    out, i = [], 0
    while i < len(body):
        tag, value, i = _tlv(body, i)
        out.append((tag, value))
    return out


def parse_private_key(pem):
    """PEM сервисного аккаунта -> (n, e, d). Понимает PKCS#8 и PKCS#1."""
    pem = pem.replace("\\n", "\n")         # секреты часто приезжают с экранированным переводом строки
    lines = [l.strip() for l in pem.strip().splitlines()]
    body = "".join(l for l in lines if l and not l.startswith("-----"))
    if not body:
        raise GoogleAuthError("в ключе нет тела base64")
    try:
        der = base64.b64decode(body)
    except Exception as e:
        raise GoogleAuthError("тело ключа не base64: %s" % e)

    tag, outer, _ = _tlv(der, 0)
    if tag != 0x30:
        raise GoogleAuthError("ключ не начинается с SEQUENCE")
    items = _seq(outer)
    if len(items) >= 3 and items[2][0] == 0x04:
        # PKCS#8: третьим элементом OCTET STRING, внутри которого настоящий RSAPrivateKey
        tag, inner, _ = _tlv(items[2][1], 0)
        if tag != 0x30:
            raise GoogleAuthError("внутри PKCS#8 не RSAPrivateKey")
        items = _seq(inner)
    if len(items) < 4:
        raise GoogleAuthError("в RSAPrivateKey меньше четырех полей")
    #  версия, модуль, открытая экспонента, закрытая экспонента
    nums = [int.from_bytes(v, "big") for _, v in items[:4]]
    return nums[1], nums[2], nums[3]


# ---------- подпись ----------

def sign_rs256(message, n, d):
    """PKCS#1 v1.5 поверх SHA-256. Ровно то, что делает openssl dgst -sha256 -sign."""
    k = (n.bit_length() + 7) // 8
    tail = SHA256_DER + hashlib.sha256(message).digest()
    if k < len(tail) + 11:
        raise GoogleAuthError("ключ слишком короткий для SHA-256")
    # 0x00 0x01 <заполнение 0xFF> 0x00 <DigestInfo+хеш>
    em = b"\x00\x01" + b"\xff" * (k - len(tail) - 3) + b"\x00" + tail
    return pow(int.from_bytes(em, "big"), d, n).to_bytes(k, "big")


def _key_folders():
    """Где ищем файл ключа: откуда запустили, папка кода и корень проекта над ней."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # pipeline/
    return [os.getcwd(), here, os.path.dirname(here)]


def find_key_file(folders, limit=40):
    """Ищет ключ по содержимому, а не по имени.

    Google скачивает ключ под именем вроде `foxlik-pipeline-a1b2c3d4e5f6.json`,
    и переименовать его в файл с точкой впереди через проводник Windows неудобно.
    Поэтому просто кладем скачанный файл в папку проекта, а узнаем его по полю
    `"type": "service_account"` внутри - подделать его случайно нечем.

    Смотрим только верхний уровень папок и не больше `limit` файлов: рыться
    по всему диску в поисках чужого ключа - не наша работа.
    """
    for folder in folders:
        try:
            names = sorted(n for n in os.listdir(folder) if n.lower().endswith(".json"))
        except OSError:
            continue
        for name in names[:limit]:
            path = os.path.join(folder, name)
            try:
                if os.path.getsize(path) > 64 * 1024:
                    continue            # ключ весит килобайты, большой файл - не он
                with open(path, encoding="utf-8-sig") as f:
                    # читаем файл целиком: `type` у Google стоит первым полем, но
                    # порядок ключей в JSON ничем не гарантирован, а закрытый ключ
                    # длинный - на первых сотнях байт его легко не увидеть
                    head = f.read()
            except OSError:
                continue
            if '"service_account"' in head and '"private_key"' in head:
                return path
    return None


def _b64u(raw):
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


# ---------- сервисный аккаунт ----------

class ServiceAccount:
    def __init__(self, info):
        for field in ("client_email", "private_key"):
            if not info.get(field):
                raise GoogleAuthError("в JSON сервисного аккаунта нет поля %s" % field)
        self.info = info
        self.email = info["client_email"]
        self.token_uri = info.get("token_uri") or TOKEN_URI
        self.n, self.e, self.d = parse_private_key(info["private_key"])
        self._cache = {}                   # ключ - набор прав, значение - (токен, до какого времени)

    @classmethod
    def from_env(cls, var="GOOGLE_SA_JSON"):
        raw = os.environ.get(var)
        if not raw:
            raise GoogleAuthError("нет переменной %s" % var)
        return cls(json.loads(raw))

    @classmethod
    def from_file(cls, path):
        with open(path, encoding="utf-8-sig") as f:
            return cls(json.load(f))

    @classmethod
    def load(cls, var="GOOGLE_SA_JSON", filename=KEY_FILE, folders=None):
        """Два способа входа, и оба нужны.

        **Локально** ключ - это скачанный из Cloud Console файл: в `.env` многострочный
        JSON не положишь, а превращать его в одну строку руками - лишний шаг, на котором
        ломаются переводы строк внутри ключа.

        **В GitHub Actions** файла нет, там секрет приезжает переменной окружения.

        Порядок: сначала файл рядом, потом переменная. Не нашли ни того ни другого -
        говорим, где искали, а не «нет доступа».
        """
        # folders задается только в проверках: иначе они зависели бы от того,
        # лежит ли рядом настоящий ключ, и ломались бы от появления живого
        where = _key_folders() if folders is None else list(folders)
        for folder in where:
            candidate = os.path.join(folder, filename)
            if os.path.exists(candidate):
                return cls.from_file(candidate)
        found = find_key_file(where)
        if found:
            return cls.from_file(found)
        if os.environ.get(var):
            return cls.from_env(var)
        raise GoogleAuthError(
            "нет ключа сервисного аккаунта. Искал файл %s и любой json с "
            "\"type\": \"service_account\" в папках: %s - и переменную %s"
            % (filename, ", ".join(where), var))

    def make_jwt(self, scopes, now=None):
        now = int(now or time.time())
        header = {"alg": "RS256", "typ": "JWT"}
        if self.info.get("private_key_id"):
            header["kid"] = self.info["private_key_id"]
        claims = {"iss": self.email, "scope": " ".join(scopes),
                  "aud": self.token_uri, "iat": now, "exp": now + LIFETIME}
        head = ".".join(_b64u(json.dumps(x, separators=(",", ":"), sort_keys=True))
                        for x in (header, claims))
        sig = sign_rs256(head.encode("ascii"), self.n, self.d)
        return head + "." + _b64u(sig)

    def token(self, scopes, now=None):
        """Access token с запасом по времени. Повторный вызов не ходит в сеть зря."""
        now = int(now or time.time())
        key = " ".join(sorted(scopes))
        cached = self._cache.get(key)
        if cached and cached[1] - EARLY > now:
            return cached[0]
        r = http.request(self.token_uri, method="POST", data={
            "grant_type": GRANT, "assertion": self.make_jwt(scopes, now)})
        if not isinstance(r, dict) or "access_token" not in r:
            # текст ошибки Google - единственное, по чему чинится доступ
            raise GoogleAuthError("Google не выдал токен: %s" % r)
        self._cache[key] = (r["access_token"], now + int(r.get("expires_in", LIFETIME)))
        return r["access_token"]

    def headers(self, scopes, now=None):
        return {"Authorization": "Bearer " + self.token(scopes, now)}


# --- выброшенный ключ ТОЛЬКО для проверки. Никуда не подходит, нигде не заведен. ---
_TEST_PEM = """-----BEGIN PRIVATE KEY-----
MIICdgIBADANBgkqhkiG9w0BAQEFAASCAmAwggJcAgEAAoGBAKrrjlYTCiU1YQ3Y
PunSUPH8tiQoifaXDPYy32rm2BAdUR2s/0B3F/vl6FQ9kTxknRBQxpljCBi+SrqM
DwLuP7+pAWUlSI5u0ZXFCCDKQR1omGjI+utWX4Y6JSModg74c6XADwtCGX0f0Lks
SHl/NO+0AoPQJo4o4qUZIu36/GgXAgMBAAECgYEAgyqeYuPdp0xdnPhU36/tOqfL
R9hsd/bXlaDY3/sj2MoG1BVNFbXPjBqVvwA4kvZLqpeysUfUkNiIFL0jUAZymGtU
96YGoY4+GfbhIBWaoX1SRQvOugyX5LWRuWqARw7VSgjpPwRIcCoFr4BWFYOAoZnJ
6IlB+MZCuVTwuP0PcxECQQDgAdwIwTa7+SeeqhpCYvAiTHCXXda0J+UBsFiVWVRo
5d4/NKi5dkppa933BxxvCoQz788QZN05RkCSCegFRbYtAkEAw1S6a0Kt/cCtz70q
CQzbf23OcdTBq0HvhMt9lMNRnkMqJdtAJcqFjl3Nyl2veTa+EbunbW/5mcOrdFh8
lHXl0wJAKlzvqdAwc7gY4A63TJq9Yx8lo9qhQgzRaFJbTlNIfVYLg9SHnBtc0zcN
ESyNGgrZGaFefXE1zSEWEQhCksyuOQJAN3aqjGRdpUz9zZwIAJPfC7rxQM9Jwsgx
K8LgQMqJNWga4q7z8wcjjz5BffHGLqQFqmFfdCq0dB3kZF/v1/P2MwJAKTa8ubmN
05eDZXFIL2ppaGzgGKlmKvCFGT5I6b2ycXhRmV55sMqnB2Nl+QPwTT3MBMTFKZPS
PrkzCGMR9PGbiw==
-----END PRIVATE KEY-----"""

# openssl dgst -sha256 -sign k.pem на сообщении ниже, 27.08.2026
_TEST_MSG = b"eyJhbGciOiJSUzI1NiJ9.eyJpc3MiOiJmb3hsaWsifQ"
_TEST_SIG = ("16ee8a499729ee120106a4b83d17b94dc2998c58a0316f722cc879304b93c31e"
             "686e6305c32bc6a057c99586f2c342ca8b425c8dc90e0f88901ec911ff29bb4a"
             "e59515abf9bca2677de6c9ce080dd78fb7fe137b580f57cce1fa59a16b4ad8a9"
             "b0de7b08af372e23d3b666d243015b2e3adfd7f002e2954345152b85dc03e47e")


def selftest():
    # --- разбор ключа ---
    n, e, d = parse_private_key(_TEST_PEM)
    assert e == 65537, e
    assert n.bit_length() == 1024, n.bit_length()
    assert pow(pow(42, e, n), d, n) == 42, "n, e, d не образуют рабочую пару"
    # тот же ключ с экранированным переводом строки - так он приезжает из секрета
    assert parse_private_key(_TEST_PEM.replace("\n", "\\n")) == (n, e, d)
    for bad in ("", "-----BEGIN PRIVATE KEY-----\n@@@\n-----END PRIVATE KEY-----"):
        try:
            parse_private_key(bad)
            raise AssertionError("битый ключ должен отвергаться")
        except GoogleAuthError:
            pass

    # --- 🔴 главное: подпись обязана совпасть с openssl побайтно ---
    mine = sign_rs256(_TEST_MSG, n, d).hex()
    assert mine == _TEST_SIG, "подпись разошлась с openssl:\n мы  %s\n он  %s" % (mine, _TEST_SIG)

    # --- два входа для ключа: файл локально, переменная в Actions ---
    import tempfile
    info = {"client_email": "bot@foxlik.iam.gserviceaccount.com",
            "private_key": _TEST_PEM, "private_key_id": "KID1"}
    folder = tempfile.mkdtemp()
    with open(os.path.join(folder, KEY_FILE), "w", encoding="utf-8") as f:
        json.dump(info, f)
    was = os.getcwd()
    # 🔴 проверка смотрит ТОЛЬКО во временную папку. Иначе она зависела бы от того,
    # лежит ли рядом настоящий ключ, и сломалась бы, как только он появился -
    # так и случилось 27.08, когда ключ реально скачали.
    only = [folder]
    try:
        os.chdir(folder)
        assert ServiceAccount.load(folders=only).email == info["client_email"], "файл рядом не найден"
        os.remove(KEY_FILE)

        # 🔴 ключ, скачанный Google под своим именем, должен опознаваться по содержимому:
        # переименовывать файл в точку впереди через проводник Windows неудобно
        downloaded = "foxlik-pipeline-a1b2c3d4e5f6.json"
        with open(downloaded, "w", encoding="utf-8") as f:
            json.dump(dict(info, type="service_account"), f)
        assert ServiceAccount.load(folders=only).email == info["client_email"], "ключ по содержимому не найден"
        # чужой json рядом не должен приниматься за ключ
        with open("package.json", "w", encoding="utf-8") as f:
            json.dump({"name": "нечто", "private_key": "не тот"}, f)
        assert ServiceAccount.load(folders=only).email == info["client_email"]
        os.remove(downloaded)
        try:
            ServiceAccount.load(var="НЕТ_ТАКОЙ", folders=only)
            raise AssertionError("посторонний json не должен сходить за ключ")
        except GoogleAuthError:
            pass
        os.remove("package.json")

        os.environ["GOOGLE_SA_JSON_TEST"] = json.dumps(info)
        assert ServiceAccount.load(var="GOOGLE_SA_JSON_TEST", folders=only).email == info["client_email"]
        del os.environ["GOOGLE_SA_JSON_TEST"]
        try:
            ServiceAccount.load(var="GOOGLE_SA_JSON_TEST", folders=only)
            raise AssertionError("без ключа обязана быть ошибка")
        except GoogleAuthError as e:
            # 🔴 сказать, ГДЕ искали: «нет доступа» без этого ищется часами
            assert KEY_FILE in str(e) and "GOOGLE_SA_JSON_TEST" in str(e), e
    finally:
        os.chdir(was)

    sa = ServiceAccount(info)

    # --- сборка JWT ---
    jwt = sa.make_jwt([SCOPE_SHEETS], now=1000)
    head, claims, sig = jwt.split(".")
    assert "=" not in jwt, "base64url в JWT идет без выравнивания"
    def unb64(s):
        return json.loads(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)))
    assert unb64(head) == {"alg": "RS256", "kid": "KID1", "typ": "JWT"}
    c = unb64(claims)
    assert c["iss"] == "bot@foxlik.iam.gserviceaccount.com"
    assert c["aud"] == TOKEN_URI and c["scope"] == SCOPE_SHEETS
    assert c["exp"] - c["iat"] == LIFETIME == 3600
    # подпись стоит на паре заголовок.данные, а не на чем-то еще
    assert base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)) == \
        sign_rs256((head + "." + claims).encode("ascii"), n, d)

    # --- обмен на токен и кеш ---
    calls = []

    def fake(url, method="GET", data=None, **kw):
        calls.append((url, dict(data or {})))
        return {"access_token": "AT%s" % len(calls), "expires_in": 3600}

    real, http.request = http.request, fake
    try:
        assert sa.token([SCOPE_SHEETS], now=1000) == "AT1"
        assert calls[0][0] == TOKEN_URI and calls[0][1]["grant_type"] == GRANT
        assert sa.token([SCOPE_SHEETS], now=2000) == "AT1", "живой токен не перезапрашиваем"
        assert len(calls) == 1
        assert sa.token([SCOPE_SHEETS], now=1000 + 3600) == "AT2", "истекший обязан обновиться"
        assert sa.token([SCOPE_DRIVE], now=2000) == "AT3", "у каждого набора прав свой токен"
        assert sa.headers([SCOPE_DRIVE], now=2000)["Authorization"] == "Bearer AT3"

        http.request = lambda *a, **k: {"error": "invalid_grant",
                                        "error_description": "Invalid JWT Signature"}
        try:
            ServiceAccount({"client_email": "x@y", "private_key": _TEST_PEM}) \
                .token([SCOPE_SHEETS], now=1)
            raise AssertionError("отказ Google должен подниматься")
        except GoogleAuthError as err:
            assert "Invalid JWT Signature" in str(err), err
    finally:
        http.request = real
    print("google_auth selftest OK: разбор PKCS#8, подпись СОВПАЛА С OPENSSL, "
          "JWT, кеш токена, отказ не глотается")


if __name__ == "__main__":
    selftest()
