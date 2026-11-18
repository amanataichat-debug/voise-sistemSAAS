"""
Справочник ниш бизнеса (ветка 1811-niche-admin-speed).

Здесь только СПИСОК: ключ, название, группа, значок — его показывают экран «Чем занимается ваш
бизнес?», админка «Клиенты» и настройки. Что каждая ниша включает и настраивает (модули, воронка,
каталог, аналитика, бот) — отдельно, после согласования с владельцем (предложение 18.11.2026).

Ключи — латиница, хранятся в users.business_niche и agent_configs.niche_key: не переименовывать,
только добавлять. Название поменять можно.
"""

from typing import Dict, List, Optional

# Группы — по модели работы бизнеса (что у него главное: запись, продажа товара, сделка…)
NICHE_GROUPS: List[Dict[str, str]] = [
    {"key": "booking", "title": "Запись к мастеру или врачу", "ky": "Устага же дарыгерге жазылуу"},
    {"key": "shop", "title": "Магазин и интернет-магазин", "ky": "Дүкөн жана интернет-дүкөн"},
    {"key": "deals", "title": "Сделки: недвижимость, стройка, авто, туризм", "ky": "Келишимдер: кыймылсыз мүлк, курулуш, авто, туризм"},
    {"key": "auto", "title": "СТО и автосервис", "ky": "СТО жана автосервис"},
    {"key": "food", "title": "Еда и доставка", "ky": "Тамак-аш жана жеткирүү"},
    {"key": "education", "title": "Обучение", "ky": "Окутуу"},
    {"key": "other", "title": "Другое", "ky": "Башка"},
]

NICHES: List[Dict[str, str]] = [
    # Запись к мастеру или врачу
    {"key": "beauty_salon", "group": "booking", "icon": "💇‍♀️", "title": "Салон красоты", "ky": "Сулуулук салону"},
    {"key": "barbershop", "group": "booking", "icon": "💈", "title": "Барбершоп", "ky": "Барбершоп"},
    {"key": "nails", "group": "booking", "icon": "💅", "title": "Маникюр и педикюр", "ky": "Маникюр жана педикюр"},
    {"key": "massage_spa", "group": "booking", "icon": "💆", "title": "Массаж и СПА", "ky": "Массаж жана СПА"},
    {"key": "cosmetology", "group": "booking", "icon": "🧴", "title": "Косметология", "ky": "Косметология"},
    {"key": "dentistry", "group": "booking", "icon": "🦷", "title": "Стоматология", "ky": "Стоматология"},
    {"key": "clinic", "group": "booking", "icon": "🏥", "title": "Клиника и медцентр", "ky": "Клиника жана медборбор"},
    {"key": "vet", "group": "booking", "icon": "🐾", "title": "Ветклиника", "ky": "Ветклиника"},
    {"key": "fitness", "group": "booking", "icon": "🧘", "title": "Фитнес, йога, танцы", "ky": "Фитнес, йога, бий"},
    {"key": "psychology", "group": "booking", "icon": "🧠", "title": "Психолог и коуч", "ky": "Психолог жана коуч"},
    {"key": "photo_studio", "group": "booking", "icon": "📸", "title": "Фотостудия", "ky": "Фотостудия"},
    {"key": "car_wash", "group": "booking", "icon": "🚿", "title": "Автомойка и детейлинг", "ky": "Автожуучу жана детейлинг"},
    # Магазин и интернет-магазин
    {"key": "clothing", "group": "shop", "icon": "👗", "title": "Одежда и обувь", "ky": "Кийим жана бут кийим"},
    {"key": "kids_goods", "group": "shop", "icon": "🧸", "title": "Детские товары", "ky": "Балдар товарлары"},
    {"key": "flowers", "group": "shop", "icon": "💐", "title": "Цветы и подарки", "ky": "Гүлдөр жана белектер"},
    {"key": "cosmetics_shop", "group": "shop", "icon": "💄", "title": "Косметика и парфюмерия", "ky": "Косметика жана парфюмерия"},
    {"key": "electronics", "group": "shop", "icon": "📱", "title": "Электроника и техника", "ky": "Электроника жана техника"},
    {"key": "furniture", "group": "shop", "icon": "🛋️", "title": "Мебель", "ky": "Эмерек"},
    {"key": "building_materials", "group": "shop", "icon": "🧱", "title": "Стройматериалы", "ky": "Курулуш материалдары"},
    {"key": "auto_parts", "group": "shop", "icon": "🔩", "title": "Автозапчасти", "ky": "Автобөлүктөр"},
    {"key": "grocery", "group": "shop", "icon": "🛒", "title": "Продукты", "ky": "Азык-түлүк"},
    {"key": "ecommerce", "group": "shop", "icon": "🛍️", "title": "Интернет-магазин", "ky": "Интернет-дүкөн"},
    # Сделки
    {"key": "real_estate", "group": "deals", "icon": "🏠", "title": "Агентство недвижимости", "ky": "Кыймылсыз мүлк агенттиги"},
    {"key": "developer", "group": "deals", "icon": "🏗️", "title": "Застройщик", "ky": "Куруучу компания"},
    {"key": "construction", "group": "deals", "icon": "🔨", "title": "Строительство и ремонт", "ky": "Курулуш жана оңдоо"},
    {"key": "auto_sales", "group": "deals", "icon": "🚗", "title": "Продажа и импорт авто", "ky": "Авто сатуу жана импорт"},
    {"key": "tourism", "group": "deals", "icon": "✈️", "title": "Туризм", "ky": "Туризм"},
    {"key": "legal", "group": "deals", "icon": "⚖️", "title": "Юристы и бухгалтерия", "ky": "Юристтер жана бухгалтерия"},
    {"key": "b2b_services", "group": "deals", "icon": "💼", "title": "Услуги для бизнеса", "ky": "Бизнес үчүн кызматтар"},
    # СТО
    {"key": "auto_service", "group": "auto", "icon": "🔧", "title": "СТО и автосервис", "ky": "СТО жана автосервис"},
    # Еда
    {"key": "cafe", "group": "food", "icon": "🍽️", "title": "Кафе и ресторан", "ky": "Кафе жана ресторан"},
    {"key": "food_delivery", "group": "food", "icon": "🛵", "title": "Доставка еды", "ky": "Тамак жеткирүү"},
    {"key": "bakery", "group": "food", "icon": "🎂", "title": "Кондитерская, торты на заказ", "ky": "Кондитердик, буюртма торттор"},
    # Обучение
    {"key": "courses", "group": "education", "icon": "🎓", "title": "Курсы и тренинги", "ky": "Курстар жана тренингдер"},
    {"key": "language_school", "group": "education", "icon": "🗣️", "title": "Языковая школа", "ky": "Тил мектеби"},
    {"key": "driving_school", "group": "education", "icon": "🚦", "title": "Автошкола", "ky": "Автомектеп"},
    {"key": "kids_center", "group": "education", "icon": "🧒", "title": "Детский центр", "ky": "Балдар борбору"},
    # Другое
    {"key": "other", "group": "other", "icon": "✨", "title": "Другое", "ky": "Башка"},
]

_BY_KEY: Dict[str, Dict[str, str]] = {n["key"]: n for n in NICHES}


def is_known_niche(key: Optional[str]) -> bool:
    return bool(key) and key in _BY_KEY


def niche(key: Optional[str]) -> Optional[Dict[str, str]]:
    return _BY_KEY.get(key or "")


def niche_title(key: Optional[str], other: Optional[str] = None, lang: str = "ru") -> Optional[str]:
    """Название ниши для людей; у «Другое» — то, что владелец написал сам."""
    if not key:
        return None
    item = _BY_KEY.get(key)
    if not item:
        return key
    if key == "other" and other:
        return f"Другое: {other}" if lang != "ky" else f"Башка: {other}"
    return item["ky"] if lang == "ky" else item["title"]


def catalog_payload() -> Dict[str, list]:
    """Список для экранов выбора (группы по порядку, ниши внутри групп)."""
    return {"groups": NICHE_GROUPS, "niches": NICHES}
