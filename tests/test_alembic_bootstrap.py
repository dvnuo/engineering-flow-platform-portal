from app.services.alembic_bootstrap import should_bootstrap_empty_db


def test_should_bootstrap_empty_db_only_when_truly_empty():
    assert should_bootstrap_empty_db(set()) is True
    assert should_bootstrap_empty_db({"users"}) is False
    assert should_bootstrap_empty_db({"alembic_version"}) is False
    assert should_bootstrap_empty_db({"some_partial_table", "users"}) is False
