"""Проверка раскатки сценариев провайдера на подставном Voximplant."""
import asyncio, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.CRITICAL)

import backend.api.telephony as t


class FakeChild:
    def __init__(self, acc, app_id, scenario_ids=None, rule_ids=None):
        self.vox_account_id = acc
        self.vox_api_key = "k"
        self.vox_application_id = app_id
        self.user_id = "u-" + acc
        self.vox_scenario_ids = scenario_ids
        self.vox_rule_ids = rule_ids


class FakeService:
    def __init__(self):
        self.parent_account_id = "parent"
        self.parent_api_key = "pk"
        self.added, self.updated, self.rules = [], [], []

    async def get_parent_scenarios(self, with_script=False):
        return {"success": True, "scenarios": [
            {"scenario_name": "inbound_fish", "scenario_id": 1},
            {"scenario_name": "outbound_fish", "scenario_id": 2},
            {"scenario_name": "inbound_cascade", "scenario_id": 3},   # лишний — не должен попасть
        ]}

    async def get_scenarios(self, account_id=None, api_key=None, with_script=False, scenario_id=None):
        if with_script:
            return {"success": True, "scenarios": [{"scenario_script": "// code " + str(scenario_id)}]}
        return {"success": True, "scenarios": []}

    async def add_scenario(self, child_account_id, child_api_key, scenario_name, scenario_script):
        self.added.append((child_account_id, scenario_name))
        return {"success": True, "scenario_id": 100 + len(self.added)}

    async def update_scenario(self, child_account_id, child_api_key, scenario_id, scenario_script):
        self.updated.append((child_account_id, scenario_id))
        return {"success": True}

    async def add_rule(self, child_account_id, child_api_key, application_id, rule_name, rule_pattern, scenario_id):
        self.rules.append((child_account_id, rule_name, rule_pattern))
        return {"success": True, "rule_id": 900}

    async def get_rules(self, **kw):
        return {"success": True, "rules": []}


class FakeQuery:
    def __init__(self, rows): self._rows = rows
    def all(self): return self._rows


class FakeDB:
    def __init__(self, rows): self._rows = rows; self.commits = 0
    def query(self, model): return FakeQuery(self._rows)
    def commit(self): self.commits += 1


async def main():
    svc = FakeService()
    t.get_voximplant_partner_service = lambda: svc
    t.flag_modified = lambda obj, field: None

    children = [
        FakeChild("acc1", "app1"),                                   # чистый — add + rule
        FakeChild("acc2", None),                                     # без приложения — skip
        FakeChild("acc3", "app3", {"inbound_fish": "55"}, {}),       # частично есть — update + add
    ]
    db = FakeDB(children)

    events = []
    async for ev in t._deploy_provider_scenarios_iter(
        db, ["inbound_fish", "outbound_fish"], "outbound_fish"
    ):
        events.append(ev)

    kinds = [e["type"] for e in events]
    assert kinds[0] == "start", kinds
    assert kinds[-1] == "done", kinds
    assert kinds.count("account") == 3, kinds
    print("✅ события: start → 3×account → done")

    start = events[0]
    assert start["total_accounts"] == 3, start
    assert start["scenarios"] == ["inbound_fish", "outbound_fish"], start
    print("✅ с родителя взяты только сценарии Fish, посторонние отброшены")

    accounts = [e for e in events if e["type"] == "account"]
    a1, a2, a3 = accounts

    assert a1["status"] == "ok" and sorted(a1["scripts_added"]) == ["inbound_fish", "outbound_fish"], a1
    assert a1["rules_created"] == ["outbound_fish"], a1
    assert a2["status"] == "skipped_no_app", a2
    assert a3["scripts_updated"] == ["inbound_fish"], a3
    assert a3["scripts_added"] == ["outbound_fish"], a3
    print("✅ чистый аккаунт, аккаунт без приложения и частичный обработаны по-разному")

    # правило заводится с ожидаемым паттерном и только там, где есть приложение
    assert svc.rules == [("acc1", "outbound_fish", "outbound_fish_.*"),
                         ("acc3", "outbound_fish", "outbound_fish_.*")], svc.rules
    print("✅ правило outbound_fish создано на обоих рабочих аккаунтах")

    done = events[-1]
    assert done["scripts_added"] == 3 and done["scripts_updated"] == 1, done
    assert done["rules_created"] == 2 and done["skipped"] == 1 and done["failed"] == 0, done
    print("✅ итоги сходятся:", {k: v for k, v in done.items() if k != "type"})

    # прогресс коммитится по каждому изменённому аккаунту, а не одним махом в конце
    assert db.commits == 2, db.commits
    print("✅ прогресс закоммичен по аккаунтам (commits=%d)" % db.commits)

    # обёртка одним ответом даёт ту же картину
    svc.added.clear(); svc.updated.clear(); svc.rules.clear()
    for c in children:
        c.vox_scenario_ids = None if c.vox_account_id != "acc3" else {"inbound_fish": "55"}
        c.vox_rule_ids = None
    res = await t._deploy_provider_scenarios(FakeDB(children), ["inbound_fish", "outbound_fish"], "outbound_fish")
    assert len(res["details"]) == 3 and res["rules_created"] == 2, res
    print("✅ обёртка одним ответом возвращает те же итоги")

    print("\nвсе проверки раскатки пройдены")

asyncio.run(main())
