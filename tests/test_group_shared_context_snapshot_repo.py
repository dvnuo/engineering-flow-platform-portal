from sqlalchemy.exc import IntegrityError

from app.models.group_shared_context_snapshot import GroupSharedContextSnapshot
from app.repositories.group_shared_context_snapshot_repo import GroupSharedContextSnapshotRepository


class _FakeDB:
    def __init__(self):
        self.rollback_calls = 0

    def rollback(self):
        self.rollback_calls += 1


def test_group_shared_context_upsert_recovers_from_insert_race(monkeypatch):
    db = _FakeDB()
    repo = GroupSharedContextSnapshotRepository(db)  # type: ignore[arg-type]

    existing = GroupSharedContextSnapshot(
        group_id="group-1",
        context_ref="ctx-1",
        scope_kind="issue",
        payload_json='{"state":"old"}',
        created_by_user_id=1,
        source_delegation_id="d-old",
    )

    calls = {"get": 0}

    def _fake_get(group_id: str, context_ref: str):
        _ = (group_id, context_ref)
        calls["get"] += 1
        if calls["get"] == 1:
            return None
        return existing

    def _fake_create(**_kwargs):
        raise IntegrityError("insert", {}, Exception("duplicate"))

    monkeypatch.setattr(repo, "get_by_group_and_ref", _fake_get)
    monkeypatch.setattr(repo, "create", _fake_create)

    saved = {"called": False}

    def _fake_save(snapshot: GroupSharedContextSnapshot):
        saved["called"] = True
        return snapshot

    monkeypatch.setattr(repo, "save", _fake_save)

    result = repo.upsert_by_group_and_ref(
        group_id="group-1",
        context_ref="ctx-1",
        scope_kind="task",
        payload_json='{"state":"new"}',
        created_by_user_id=99,
        source_delegation_id="d-new",
    )

    assert result is existing
    assert result.payload_json == '{"state":"new"}'
    assert result.scope_kind == "task"
    assert result.created_by_user_id == 99
    assert result.source_delegation_id == "d-new"
    assert db.rollback_calls == 1
    assert saved["called"] is True
