"""Разбор переписки падал в конце (13.11.2026): «Can't flag attribute 'memory' modified;
it's not present in the object state» → в инбоксе «Бот не смог ответить … сбой модели бота».

Инструмент бота (смена этапа, память клиента, отправка) делает db.commit(), поля карточки истекают,
и голый flag_modified(agent_contact, 'memory') в конце разбора бросал исключение, если дальше
никто карточку не читал (переписка, этап сменил сам бот). Теперь — agent_orchestrator._flag_memory.

Часть 1 — без БД (SQLite, своя модель): сама механика SQLAlchemy.
Часть 2 — PostgreSQL при POSTCALL_TEST_DATABASE_URL (или CATALOG_TEST_DATABASE_URL): настоящая AgentContact.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.setdefault("HOST_URL", "https://t.voksyai.online")
os.environ.setdefault("OPENAI_API_KEY", "k")
os.environ.setdefault("JWT_SECRET_KEY", "postcall-test-secret-0123456789")
PG = os.environ.get("POSTCALL_TEST_DATABASE_URL") or os.environ.get("CATALOG_TEST_DATABASE_URL")
os.environ.setdefault("DATABASE_URL", PG or "postgresql://u:p@localhost:1/none")
import logging  # noqa: E402
logging.disable(logging.CRITICAL)

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


import backend.core.config  # noqa: E402,F401
from sqlalchemy import create_engine, Column, Integer, String, JSON, text  # noqa: E402
from sqlalchemy.orm import declarative_base, sessionmaker  # noqa: E402
from sqlalchemy.orm.attributes import flag_modified  # noqa: E402
from backend.services.agent_orchestrator import _flag_memory  # noqa: E402

# ── Часть 1: механика ─────────────────────────────────────────────────────
B = declarative_base()


class C(B):
    __tablename__ = "c"
    id = Column(Integer, primary_key=True)
    status = Column(String)
    memory = Column(JSON)


e = create_engine("sqlite://")
B.metadata.create_all(e)
s = sessionmaker(bind=e)()
c = C(status="new", memory={"a": 1})
s.add(c); s.commit()
_ = c.status
s.commit()   # «инструмент бота» сделал commit → поля истекли
try:
    flag_modified(c, "memory")
    raised = False
except Exception:
    raised = True
check("голый flag_modified после commit падает (так было)", raised)
try:
    _flag_memory(c); c.status = "work"; s.commit()
    ok = True
except Exception as ex:
    ok = False; print(ex)
check("_flag_memory после commit не падает, этап сохраняется", ok and s.query(C).get(c.id).status == "work")
c.memory["b"] = 2          # изменение «на месте» — пометка нужна и работает
_flag_memory(c); s.commit()
s.expire_all()
check("изменение memory на месте сохраняется", s.query(C).get(c.id).memory == {"a": 1, "b": 2})

# ── Часть 2: настоящая карточка клиента ───────────────────────────────────
if not PG:
    print("SKIP часть 2: POSTCALL_TEST_DATABASE_URL не задан")
else:
    from backend.db.session import engine, SessionLocal
    from backend.models.base import Base
    import backend.models  # noqa: F401
    from backend.models.user import User
    from backend.models.agent_config import AgentConfig
    from backend.models.agent_contact import AgentContact
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    Base.metadata.create_all(engine)
    db = SessionLocal()
    u = User(email="pc@x.kg", password_hash="x"); db.add(u); db.flush()
    ag = AgentConfig(user_id=u.id, name="Бот"); db.add(ag); db.flush()
    ct = AgentContact(agent_config_id=ag.id, user_id=u.id, name="Айжан", phone="+996700111222", status="new", memory={"x": 1})
    db.add(ct); db.commit()
    # как в разборе: инструмент move_contact_stage меняет этап в своей сессии и коммитит
    db.query(AgentContact).filter(AgentContact.id == ct.id).update({"status": "active"})
    db.commit()
    try:
        _flag_memory(ct)
        db.commit()
        ok = True
    except Exception as ex:
        ok = False; print(ex)
    check("AgentContact: конец разбора после commit инструмента не падает", ok)
    db.expire_all()
    row = db.query(AgentContact).get(ct.id)
    check("этап, выставленный инструментом, не затёрт", row.status == "active", row.status)
    check("память клиента цела", row.memory == {"x": 1}, row.memory)
    db.close()

print("ПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
