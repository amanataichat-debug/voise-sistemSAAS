"""Google Таблица агента (services/sheets_leads.py): разбор ссылки и прайса."""
import os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sys
sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false", OPENAI_API_KEY="k")

from backend.services import sheets_leads as sl

failures = 0
def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1

ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-xy"
check("Ссылка → id", sl.parse_sheet_id(f"https://docs.google.com/spreadsheets/d/{ID}/edit#gid=0") == ID)
check("Голый id", sl.parse_sheet_id(ID) == ID)
check("Мусор → None", sl.parse_sheet_id("https://example.com/hello") is None)

cols = sl.map_columns(["Наименование", "Цена, сом", "Описание", "В наличии", "Ключевые слова"])
check("Колонки по русским названиям", cols == {"title": 0, "price": 1, "body": 2, "in_stock": 3, "keywords": 4}, cols)
check("Колонки по-английски и в другом порядке",
      sl.map_columns(["Price", "Name"]) == {"price": 0, "title": 1})

check("Цена с пробелами и валютой", sl.parse_price("1 500 сом") == 1500)
check("Цена с неразрывным пробелом", sl.parse_price("12 990") == 12990)
check("Цена с копейками", sl.parse_price("99,50") == 99.5)
check("Пустая цена", sl.parse_price("") is None)
check("Наличие «нет»", sl.parse_in_stock("нет") is False)
check("Наличие 0 штук", sl.parse_in_stock("0") is False)
check("Наличие 5 штук", sl.parse_in_stock("5") is True)
check("Пустое наличие = есть", sl.parse_in_stock("") is True)

rows = [["Название", "Цена", "Наличие"], ["Стрижка", "800 сом", "да"], ["", "100"], ["стрижка", "900"], ["Окрашивание", "2 500", "нет"]]
prods = sl.rows_to_products(rows)
check("Пустые и повторы пропущены", [p["title"] for p in prods] == ["Стрижка", "Окрашивание"], prods)
check("Цены и наличие разобраны", prods[0]["price"] == 800 and prods[1]["in_stock"] is False)
try:
    sl.rows_to_products([["Цена"], ["100"]])
    check("Без колонки «Название» — понятная ошибка", False)
except sl.SheetError as e:
    check("Без колонки «Название» — понятная ошибка", "Название" in str(e))

print("ПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
