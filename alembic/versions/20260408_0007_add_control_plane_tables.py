"""add control plane tables missing from migrations

Revision ID: 20260408_0007
Revises: 20260408_0006
Create Date: 2026-04-08
"""

from alembic import op
import sqlalchemy as sa


revision = "20260408_0007"
down_revision = "20260408_0006"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    if not _has_table("agent_groups"):
        op.create_table(
            "agent_groups",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("leader_agent_id", sa.String(length=36), nullable=False),
            sa.Column("shared_context_policy_json", sa.Text(), nullable=True),
            sa.Column("task_routing_policy_json", sa.Text(), nullable=True),
            sa.Column("ephemeral_agent_policy_json", sa.Text(), nullable=True),
            sa.Column("specialist_agent_pool_json", sa.Text(), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["leader_agent_id"], ["agents.id"]),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    if _has_table("agent_groups") and not _has_index("agent_groups", "ix_agent_groups_leader_agent_id"):
        op.create_index("ix_agent_groups_leader_agent_id", "agent_groups", ["leader_agent_id"])
    if _has_table("agent_groups") and not _has_index("agent_groups", "ix_agent_groups_created_by_user_id"):
        op.create_index("ix_agent_groups_created_by_user_id", "agent_groups", ["created_by_user_id"])

    if not _has_table("agent_group_members"):
        op.create_table(
            "agent_group_members",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("group_id", sa.String(length=36), nullable=False),
            sa.Column("member_type", sa.String(length=16), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("agent_id", sa.String(length=36), nullable=True),
            sa.Column("role", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["group_id"], ["agent_groups.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    for index_name, columns in [
        ("ix_agent_group_members_group_id", ["group_id"]),
        ("ix_agent_group_members_user_id", ["user_id"]),
        ("ix_agent_group_members_agent_id", ["agent_id"]),
        ("ix_group_member_group_role", ["group_id", "role"]),
        ("ix_group_member_group_agent", ["group_id", "agent_id"]),
        ("ix_group_member_group_user", ["group_id", "user_id"]),
    ]:
        if _has_table("agent_group_members") and not _has_index("agent_group_members", index_name):
            op.create_index(index_name, "agent_group_members", columns)

    if not _has_table("agent_tasks"):
        op.create_table(
            "agent_tasks",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("group_id", sa.String(length=36), nullable=True),
            sa.Column("parent_agent_id", sa.String(length=36), nullable=True),
            sa.Column("assignee_agent_id", sa.String(length=36), nullable=False),
            sa.Column("source", sa.String(length=64), nullable=False),
            sa.Column("task_type", sa.String(length=128), nullable=False),
            sa.Column("input_payload_json", sa.Text(), nullable=True),
            sa.Column("shared_context_ref", sa.String(length=255), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("result_payload_json", sa.Text(), nullable=True),
            sa.Column("retry_count", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["parent_agent_id"], ["agents.id"]),
            sa.ForeignKeyConstraint(["assignee_agent_id"], ["agents.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    for index_name, columns in [
        ("ix_agent_tasks_group_id", ["group_id"]),
        ("ix_agent_tasks_parent_agent_id", ["parent_agent_id"]),
        ("ix_agent_tasks_assignee_agent_id", ["assignee_agent_id"]),
    ]:
        if _has_table("agent_tasks") and not _has_index("agent_tasks", index_name):
            op.create_index(index_name, "agent_tasks", columns)

    if not _has_table("capability_profiles"):
        op.create_table(
            "capability_profiles",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("tool_set_json", sa.Text(), nullable=True),
            sa.Column("channel_set_json", sa.Text(), nullable=True),
            sa.Column("skill_set_json", sa.Text(), nullable=True),
            sa.Column("allowed_external_systems_json", sa.Text(), nullable=True),
            sa.Column("allowed_webhook_triggers_json", sa.Text(), nullable=True),
            sa.Column("allowed_actions_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    if _has_table("capability_profiles") and not _has_index("capability_profiles", "ix_capability_profiles_name"):
        op.create_index("ix_capability_profiles_name", "capability_profiles", ["name"], unique=True)

    if not _has_table("policy_profiles"):
        op.create_table(
            "policy_profiles",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("auto_run_rules_json", sa.Text(), nullable=True),
            sa.Column("permission_rules_json", sa.Text(), nullable=True),
            sa.Column("audit_rules_json", sa.Text(), nullable=True),
            sa.Column("transition_rules_json", sa.Text(), nullable=True),
            sa.Column("max_parallel_tasks", sa.Integer(), nullable=True),
            sa.Column("escalation_rules_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    if _has_table("policy_profiles") and not _has_index("policy_profiles", "ix_policy_profiles_name"):
        op.create_index("ix_policy_profiles_name", "policy_profiles", ["name"], unique=True)

    if not _has_table("external_event_subscriptions"):
        op.create_table(
            "external_event_subscriptions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("agent_id", sa.String(length=36), nullable=False),
            sa.Column("source_type", sa.String(length=64), nullable=False),
            sa.Column("event_type", sa.String(length=128), nullable=False),
            sa.Column("target_ref", sa.String(length=255), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("config_json", sa.Text(), nullable=True),
            sa.Column("dedupe_key_template", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    for index_name, columns in [
        ("ix_external_event_subscriptions_agent_id", ["agent_id"]),
        ("ix_external_sub_source_event", ["source_type", "event_type"]),
        ("ix_external_sub_agent_enabled", ["agent_id", "enabled"]),
    ]:
        if _has_table("external_event_subscriptions") and not _has_index("external_event_subscriptions", index_name):
            op.create_index(index_name, "external_event_subscriptions", columns)

    if not _has_table("workflow_transition_rules"):
        op.create_table(
            "workflow_transition_rules",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("system_type", sa.String(length=64), nullable=False),
            sa.Column("project_key", sa.String(length=64), nullable=False),
            sa.Column("issue_type", sa.String(length=128), nullable=False),
            sa.Column("trigger_status", sa.String(length=128), nullable=False),
            sa.Column("assignee_binding", sa.String(length=255), nullable=True),
            sa.Column("target_agent_id", sa.String(length=36), nullable=False),
            sa.Column("skill_name", sa.String(length=128), nullable=True),
            sa.Column("success_transition", sa.String(length=128), nullable=True),
            sa.Column("failure_transition", sa.String(length=128), nullable=True),
            sa.Column("success_reassign_to", sa.String(length=32), nullable=True),
            sa.Column("failure_reassign_to", sa.String(length=32), nullable=True),
            sa.Column("explicit_success_assignee", sa.String(length=255), nullable=True),
            sa.Column("explicit_failure_assignee", sa.String(length=255), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("config_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["target_agent_id"], ["agents.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    for index_name, columns in [
        ("ix_workflow_transition_rules_system_type", ["system_type"]),
        ("ix_workflow_transition_rules_target_agent_id", ["target_agent_id"]),
        ("ix_wtr_system_project_issue_status", ["system_type", "project_key", "issue_type", "trigger_status"]),
        ("ix_wtr_target_agent_enabled", ["target_agent_id", "enabled"]),
    ]:
        if _has_table("workflow_transition_rules") and not _has_index("workflow_transition_rules", index_name):
            op.create_index(index_name, "workflow_transition_rules", columns)

    if not _has_table("runtime_capability_catalog_snapshots"):
        op.create_table(
            "runtime_capability_catalog_snapshots",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("source_agent_id", sa.String(length=36), nullable=True),
            sa.Column("catalog_version", sa.String(length=128), nullable=True),
            sa.Column("catalog_source", sa.String(length=64), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("fetched_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    for index_name, columns in [
        ("ix_runtime_capability_catalog_snapshots_source_agent_id", ["source_agent_id"]),
        ("ix_runtime_capability_catalog_snapshots_fetched_at", ["fetched_at"]),
    ]:
        if _has_table("runtime_capability_catalog_snapshots") and not _has_index("runtime_capability_catalog_snapshots", index_name):
            op.create_index(index_name, "runtime_capability_catalog_snapshots", columns)

    if not _has_table("agent_identity_bindings"):
        op.create_table(
            "agent_identity_bindings",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("agent_id", sa.String(length=36), nullable=False),
            sa.Column("system_type", sa.String(length=64), nullable=False),
            sa.Column("external_account_id", sa.String(length=255), nullable=False),
            sa.Column("username", sa.String(length=255), nullable=True),
            sa.Column("scope_json", sa.Text(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    for index_name, columns, unique in [
        ("ix_agent_identity_bindings_agent_id", ["agent_id"], False),
        ("ix_binding_agent_system_external", ["agent_id", "system_type", "external_account_id"], True),
        ("ix_binding_system_external", ["system_type", "external_account_id"], False),
    ]:
        if _has_table("agent_identity_bindings") and not _has_index("agent_identity_bindings", index_name):
            op.create_index(index_name, "agent_identity_bindings", columns, unique=unique)

    if not _has_table("group_shared_context_snapshots"):
        op.create_table(
            "group_shared_context_snapshots",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("group_id", sa.String(length=36), nullable=False),
            sa.Column("context_ref", sa.String(length=255), nullable=False),
            sa.Column("scope_kind", sa.String(length=64), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("created_by_user_id", sa.Integer(), nullable=True),
            sa.Column("source_delegation_id", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("group_id", "context_ref", name="uq_group_shared_context_group_ref"),
        )
    for index_name, columns in [
        ("ix_group_shared_context_snapshots_group_id", ["group_id"]),
        ("ix_group_shared_context_snapshots_context_ref", ["context_ref"]),
        ("ix_group_shared_context_snapshots_created_by_user_id", ["created_by_user_id"]),
        ("ix_group_shared_context_snapshots_source_delegation_id", ["source_delegation_id"]),
        ("ix_group_shared_context_group_ref", ["group_id", "context_ref"]),
    ]:
        if _has_table("group_shared_context_snapshots") and not _has_index("group_shared_context_snapshots", index_name):
            op.create_index(index_name, "group_shared_context_snapshots", columns)


def downgrade() -> None:
    if _has_table("group_shared_context_snapshots"):
        for index_name in [
            "ix_group_shared_context_group_ref",
            "ix_group_shared_context_snapshots_source_delegation_id",
            "ix_group_shared_context_snapshots_created_by_user_id",
            "ix_group_shared_context_snapshots_context_ref",
            "ix_group_shared_context_snapshots_group_id",
        ]:
            if _has_index("group_shared_context_snapshots", index_name):
                op.drop_index(index_name, table_name="group_shared_context_snapshots")
        op.drop_table("group_shared_context_snapshots")

    if _has_table("agent_identity_bindings"):
        for index_name in [
            "ix_binding_system_external",
            "ix_binding_agent_system_external",
            "ix_agent_identity_bindings_agent_id",
        ]:
            if _has_index("agent_identity_bindings", index_name):
                op.drop_index(index_name, table_name="agent_identity_bindings")
        op.drop_table("agent_identity_bindings")

    if _has_table("runtime_capability_catalog_snapshots"):
        for index_name in [
            "ix_runtime_capability_catalog_snapshots_fetched_at",
            "ix_runtime_capability_catalog_snapshots_source_agent_id",
        ]:
            if _has_index("runtime_capability_catalog_snapshots", index_name):
                op.drop_index(index_name, table_name="runtime_capability_catalog_snapshots")
        op.drop_table("runtime_capability_catalog_snapshots")

    if _has_table("workflow_transition_rules"):
        for index_name in [
            "ix_wtr_target_agent_enabled",
            "ix_wtr_system_project_issue_status",
            "ix_workflow_transition_rules_target_agent_id",
            "ix_workflow_transition_rules_system_type",
        ]:
            if _has_index("workflow_transition_rules", index_name):
                op.drop_index(index_name, table_name="workflow_transition_rules")
        op.drop_table("workflow_transition_rules")

    if _has_table("external_event_subscriptions"):
        for index_name in [
            "ix_external_sub_agent_enabled",
            "ix_external_sub_source_event",
            "ix_external_event_subscriptions_agent_id",
        ]:
            if _has_index("external_event_subscriptions", index_name):
                op.drop_index(index_name, table_name="external_event_subscriptions")
        op.drop_table("external_event_subscriptions")

    if _has_table("policy_profiles"):
        if _has_index("policy_profiles", "ix_policy_profiles_name"):
            op.drop_index("ix_policy_profiles_name", table_name="policy_profiles")
        op.drop_table("policy_profiles")

    if _has_table("capability_profiles"):
        if _has_index("capability_profiles", "ix_capability_profiles_name"):
            op.drop_index("ix_capability_profiles_name", table_name="capability_profiles")
        op.drop_table("capability_profiles")

    if _has_table("agent_tasks"):
        for index_name in [
            "ix_agent_tasks_assignee_agent_id",
            "ix_agent_tasks_parent_agent_id",
            "ix_agent_tasks_group_id",
        ]:
            if _has_index("agent_tasks", index_name):
                op.drop_index(index_name, table_name="agent_tasks")
        op.drop_table("agent_tasks")

    if _has_table("agent_group_members"):
        for index_name in [
            "ix_group_member_group_user",
            "ix_group_member_group_agent",
            "ix_group_member_group_role",
            "ix_agent_group_members_agent_id",
            "ix_agent_group_members_user_id",
            "ix_agent_group_members_group_id",
        ]:
            if _has_index("agent_group_members", index_name):
                op.drop_index(index_name, table_name="agent_group_members")
        op.drop_table("agent_group_members")

    if _has_table("agent_groups"):
        for index_name in [
            "ix_agent_groups_created_by_user_id",
            "ix_agent_groups_leader_agent_id",
        ]:
            if _has_index("agent_groups", index_name):
                op.drop_index(index_name, table_name="agent_groups")
        op.drop_table("agent_groups")
