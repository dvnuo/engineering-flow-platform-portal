from pathlib import Path


def test_dockerfile_runs_alembic_before_uvicorn_and_packages_migration_assets():
    source = Path("Dockerfile").read_text(encoding="utf-8")
    assert "COPY alembic.ini ./" in source
    assert "COPY alembic ./alembic" in source
    assert "alembic upgrade head" in source


def test_k8s_manifest_mounts_matching_alembic_assets_for_runtime_repo_clone():
    source = Path("k8s/efp-portal-deployment.yaml").read_text(encoding="utf-8")
    assert "mountPath: /app/alembic" in source
    assert "subPath: efp-portal-code/alembic" in source
    assert "mountPath: /app/alembic.ini" in source
    assert "subPath: efp-portal-code/alembic.ini" in source


def test_portal_git_clone_manifest_mounts_matching_alembic_assets_for_runtime_repo_clone():
    source = Path("k8s/portal-git-clone/efp-portal-deployment.yaml").read_text(encoding="utf-8")
    assert "mountPath: /app/alembic" in source
    assert "subPath: efp-portal-code/alembic" in source
    assert "mountPath: /app/alembic.ini" in source
    assert "subPath: efp-portal-code/alembic.ini" in source
