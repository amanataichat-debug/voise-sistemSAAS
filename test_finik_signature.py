"""
Юнит-тест канонизации и подписи Finik (backend/services/finik_service.py).

Фикстуры взяты 1-в-1 из официального Node-пакета @mancho.devs/authorizer
(__test__/index.test.js + testData.js): если наша каноническая строка и
подпись совпадают с эталонными — формат воспроизведён точно.

Запуск: python test_finik_signature.py
"""

import importlib.util
import os
import sys

# Finik-сервис не требует настроек при импорте, но config.py валидирует env —
# подставляем минимум для локального запуска теста
os.environ.setdefault("HOST_URL", "https://voicyfy.ru")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@example.com/test")

# Загружаем модуль напрямую по пути, чтобы не тянуть весь backend-пакет
# (backend/services/__init__.py импортирует БД и все сервисы приложения)
_spec = importlib.util.spec_from_file_location(
    "finik_service",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend", "services", "finik_service.py"),
)
_finik = importlib.util.module_from_spec(_spec)
sys.modules["finik_service"] = _finik
_spec.loader.exec_module(_finik)

build_canonical_string = _finik.build_canonical_string
sign_canonical_string = _finik.sign_canonical_string
verify_canonical_string = _finik.verify_canonical_string
is_success_status = _finik.is_success_status
_canonical_json_body = _finik._canonical_json_body

# ---------------------------------------------------------------------------
# Фикстуры из @mancho.devs/authorizer 2.12.8
# ---------------------------------------------------------------------------

PRIVATE_KEY = """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEAoq/3UxpzgfXOLrJEZd39TJ7Fc4iRLMcK1Hj563O+sNcP4BnA
eNnRXfCCJCLs+UZxPLxV7f1Zj7zHmgUOsB6rPS3TJwbIIbclDrhoySzPAnTNg+jU
9lpPS99vvT8tJpUb0zAA6sAwFxHUQk43mzgLou04eMlSHZmZ4MFVGzWGakIwDRlP
MQbQCOiU+N/0Kl7qBDwDkn6USwc16MTa2Xm6BAhcwaug0jlhN5NIu7hyK2gk8In1
S0OrxBEK3Z1zShRhixGEhA8Z7NREJCMuqHke09dVK1nVXz6CGTQMafjDKDwgmiBR
7cJo25Icb/OqFs4TzE7XPfU/3gK0+1YdqF4J/wIDAQAB
AoIBADY3DeTT28pjb+J/5etMnyz5fDOUP0z8x88mwaKFX/butIuqCUo8zFjR3YzN
Vst7fiEPYlN9KmuMLbhWFx76GXa53rQSvn792YueSM1B8XqQEizzWoT+c46FV2dp
tlI1PqCSTrvscBpABsxR5JYFh1s0Uq1t6b+TgIQ16Xa3z6N5AyLVw7MbXEx2iB/b
VdivmWNIjLWarr4yJDwAMgB/dOYtZp80ut8yOYYvaTmUVBUEp4ZHjBN+MgNlMD0z
DZR/y+5mr1r3TfuPUsw9eIDpD6tYwHvDgoneSGi1LtQpuEXTKhwEFezJkLZZnVCt
FCdRaKkS607fT37fJOMLDfNOgYECgYEAzpzgjzCz3+2qHJl+ejRtw2cUxCOIvVBA
zvbqVk8R/NqZVF2WteUhx/0lM+FK0/AHpcnpZu79ruvra3yaB/AZuaPOnrsjzLQ2
xsJfLpEu0Caoho3jWP7JFDGhX1CQeRXEQ9VM6l+PbjoKZ4bRO6ktVMuq/D7gBNvh
/mNglCziLLsCgYEAyZMwO91zk+4DJucflLuNKiS56J8Sq71v6j+iFwF6zimYJJAF
m7xdQ2vWXSP5jf5SeKVrQ/aA0ACSi0iYBEzhtQ19cCydjYQKSRvO0olMvRbcJfVg
ABCN+JtFIaSnWLS/sXSTzcH3vm4FgccRHtt27HQIVCFHgwwVapymAYfnRY0CgYAP
KTHNMAyy7NSjvpuqSfiX8xNyBQ1+nsnypemyJaEzRbMknq11cXfWHfxB31FHVgCp
qLRIylaxJDylKYJ//J1Wou+BdEf/OGYglZi4aQzfV0bcgMLi/+cvZSjrPpUrXW6G
b7tyI0r6EqY6zIjD8PkTlNJaKh70HFJsAUzP8q8yCwKBgQCFyFJy6P8UZxtgbnTf
WbrPBaD9atYRdaEZbzI84paGzcRUP+H5AoNDhAa5um6edvR1bhRK/wdvBXI9TujV
sdD7QQDHulS237OT4gjaYpWzycBC0R/t6w7OuP6g3Y7TqOKw/BY8sUej85FkGKKc
QDwlor0EWTIFH7f3EhB7Y59y4QKBgB5K2pRrw3wjQOHlssEO/2FqwE1NTYZalgpg
hPLRO+4Y8qM8hkKsBjgQAQhd66mWIbIvuvcGErUrz3qFJHtjTIZHqOSVlW3hiiuM
VKzZCoPa8nUJ0l6ylTpzNIb/T0HUwXgSRJBNhdHNo0yGFisaJpezF/kDUhOCzvTd
oNI/M0Zy
-----END RSA PRIVATE KEY-----
"""

PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAoq/3UxpzgfXOLrJEZd39
TJ7Fc4iRLMcK1Hj563O+sNcP4BnAeNnRXfCCJCLs+UZxPLxV7f1Zj7zHmgUOsB6r
PS3TJwbIIbclDrhoySzPAnTNg+jU9lpPS99vvT8tJpUb0zAA6sAwFxHUQk43mzgL
ou04eMlSHZmZ4MFVGzWGakIwDRlPMQbQCOiU+N/0Kl7qBDwDkn6USwc16MTa2Xm6
BAhcwaug0jlhN5NIu7hyK2gk8In1S0OrxBEK3Z1zShRhixGEhA8Z7NREJCMuqHke
09dVK1nVXz6CGTQMafjDKDwgmiBR7cJo25Icb/OqFs4TzE7XPfU/3gK0+1YdqF4J
/wIDAQAB
-----END PUBLIC KEY-----
"""

EXPECTED_SIGNATURE = (
    "TiQCuWaV1WE/VDsbYKn6O0B2diji6MyZI6zjC8Q9lEdnc6KkxURnot1i874fw8q5cyBpLXO6T7dH70V"
    "pC11pT1vlrZDZe+PzGnYe27pRqwxU6KcohG5iYp5eeUjQHNaJHL/7zkJdCRu6nIj0z84xbLYMYbPBfT"
    "HPPp+viwnGqEdR4wIcjVm18Op3WKgOj5zTv2HB4ATNi31nERYN2R3/ecn+CgK8tIf6Ox3azhNJat3oI"
    "QT6Gk10wvAROLsNFKm82Px3CeT/lXO1d8UeeTMNGe8mvo7POGUrH4UJhjsa1myvpNyKeW1vF1kuSv8b"
    "FcoJfkXbiZ51gHGxpoL8MmYhlA=="
)

EXPECTED_CANONICAL = (
    "post\n"
    "/services\n"
    "host:api.paymentsgateway.averspay.kg"
    "&x-api-header:header value"
    "&x-api-key:h8z1TDStxu5YY2YuN8jUa9hpzIVbfkLT7kPiPiYj"
    "&x-api-timestamp:1636026186643\n"
    "best=%D1%82%D0%B5%D1%81%D1%82%20%D0%B4%D0%B0%D1%82%D0%B0&from=0&size=10&test=\n"
    '{"amount":100,"payment":"payment"}'
)

HEADERS = {
    "Host": "api.paymentsgateway.averspay.kg",
    "x-api-timestamp": "1636026186643",
    "x-api-key": "h8z1TDStxu5YY2YuN8jUa9hpzIVbfkLT7kPiPiYj",
    "x-api-header": "header value",
    "X-Forwarded-For": "54.187.127.20, 15.158.4.100",
    "X-Forwarded-Port": "443",
    "X-Forwarded-Proto": "https",
}
QUERY = {"from": "0", "size": "10", "test": None, "best": "тест дата"}
BODY = {"payment": "payment", "amount": 100}


def test_canonical_string():
    canonical = build_canonical_string("POST", "/services", HEADERS, QUERY, BODY)
    assert canonical == EXPECTED_CANONICAL, (
        f"Canonical string mismatch:\n--- got ---\n{canonical}\n--- expected ---\n{EXPECTED_CANONICAL}"
    )
    print("✅ canonical string matches @mancho.devs/authorizer fixture")


def test_signature():
    canonical = build_canonical_string("POST", "/services", HEADERS, QUERY, BODY)
    signature = sign_canonical_string(canonical, PRIVATE_KEY)
    assert signature == EXPECTED_SIGNATURE, (
        f"Signature mismatch:\n got: {signature}\n exp: {EXPECTED_SIGNATURE}"
    )
    print("✅ RSA-SHA256 signature matches Node package fixture")


def test_verify():
    canonical = build_canonical_string("POST", "/services", HEADERS, QUERY, BODY)
    signature = sign_canonical_string(canonical, PRIVATE_KEY)
    assert verify_canonical_string(canonical, signature, PUBLIC_KEY)
    assert not verify_canonical_string(canonical + "x", signature, PUBLIC_KEY)
    print("✅ verify: valid signature accepted, tampered data rejected")


def test_no_query_and_no_body():
    # Без query — секция query полностью отсутствует (не пустая строка)
    canonical = build_canonical_string("POST", "/v1/payment", HEADERS, None, None)
    assert canonical == (
        "post\n/v1/payment\n"
        "host:api.paymentsgateway.averspay.kg"
        "&x-api-header:header value"
        "&x-api-key:h8z1TDStxu5YY2YuN8jUa9hpzIVbfkLT7kPiPiYj"
        "&x-api-timestamp:1636026186643\n"
    )
    print("✅ canonical string without query/body is correct")


def test_body_top_level_sort_only():
    # Сортируется только верхний уровень; вложенный Data сохраняет порядок
    body = {
        "PaymentId": "abc",
        "Amount": 100,
        "Data": {"accountId": "A1", "name_en": "Voicyfy", "webhookUrl": "https://x/y"},
        "CardType": "FINIK_QR",
    }
    assert _canonical_json_body(body) == (
        '{"Amount":100,"CardType":"FINIK_QR",'
        '"Data":{"accountId":"A1","name_en":"Voicyfy","webhookUrl":"https://x/y"},'
        '"PaymentId":"abc"}'
    )
    print("✅ body canonicalization: top-level sort only, nested order preserved")


def test_status_matching():
    assert is_success_status("success")
    assert is_success_status("SUCCEEDED")
    assert is_success_status("PaymentSucceeded")
    assert not is_success_status("failed")
    assert not is_success_status(None)
    print("✅ success status matching is case-insensitive")


if __name__ == "__main__":
    test_canonical_string()
    test_signature()
    test_verify()
    test_no_query_and_no_body()
    test_body_top_level_sort_only()
    test_status_matching()
    print("\n🎉 All Finik signature tests passed")
