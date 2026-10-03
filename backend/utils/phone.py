"""
Нормализация телефонов в E.164 с приоритетом Кыргызстана.

Пользователи вводят номера как привыкли: «0700 123 456», «700123456»,
«996 555 12 34 56», «+996 (700) 12-34-56», а также российские «8 999 …» и
«+7 …». Telegram (Telethon) понимает только международный формат, а старая
логика импорта контактов считала любой номер на 7 российским — «700123456»
превращался в несуществующий «+700123456».

Правила (после удаления пробелов, скобок, дефисов, точек):
  +…                   → как есть (любая страна);
  00…                  → международный префикс, + вместо 00;
  996XXXXXXXXX (12)    → +996…;
  0XXXXXXXXX (10)      → КР с ведущим нулём: +996 + 9 цифр;
  XXXXXXXXX (9)        → КР без кода: +996 + 9 цифр;
  8XXXXXXXXXX (11)     → РФ/КЗ: +7 + 10 цифр;
  7XXXXXXXXXX (11)     → РФ/КЗ: +7…;
  XXXXXXXXXX (10, не с 0) → РФ без 8: +7…;
  иначе                → разбор phonenumbers с регионом KG.
Итог проверяется phonenumbers.is_valid_number.
"""

import re
from typing import Optional

import phonenumbers

_STRIP_RE = re.compile(r"[\s\(\)\-\.]")


def normalize_phone_e164(raw, default_region: str = "KG") -> Optional[str]:
    """Телефон → E.164 ('+996700123456') или None, если номер невалиден."""
    if raw is None:
        return None
    if isinstance(raw, float):
        raw = str(int(raw)) if raw == int(raw) else str(raw)
    s = _STRIP_RE.sub("", str(raw).strip())
    if not s:
        return None

    if s.startswith("+"):
        candidate = s
    elif s.startswith("00"):
        candidate = "+" + s[2:]
    elif not s.isdigit():
        candidate = s
    elif s.startswith("996") and len(s) == 12:
        candidate = "+" + s
    elif s.startswith("0") and len(s) == 10:
        candidate = "+996" + s[1:]
    elif len(s) == 9:
        candidate = "+996" + s
    elif s.startswith("8") and len(s) == 11:
        candidate = "+7" + s[1:]
    elif s.startswith("7") and len(s) == 11:
        candidate = "+" + s
    elif len(s) == 10:
        candidate = "+7" + s  # российский номер без 8/+7 (9991234567)
    else:
        candidate = s

    # Кыргызский номер нужной длины принимаем, даже если в метаданных
    # phonenumbers ещё нет нового префикса оператора (их добавляют с опозданием).
    if re.fullmatch(r"\+996\d{9}", candidate):
        return candidate
    try:
        num = phonenumbers.parse(candidate, default_region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(num):
        return None
    return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
