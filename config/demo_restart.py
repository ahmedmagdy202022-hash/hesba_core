"""DEMO-RESTART: wipe a showcase install back to an empty business, so the
whole cycle can be tried again from choosing the activity.

Demo only. Business data, sessions and setup answers go; what migrations
seed (roles and permissions, tax rates, expense categories, the main entity)
is put back by replaying the migrations' own data steps, and the demo logins
are recreated with the demo password.
"""

from django.conf import settings
from django.core.management import call_command
from django.db import connection, transaction


def replay_seed_migrations():
    """Run every RunPython step of every migration, in order, against the
    historical state it was written for. Seeds use get_or_create, so this is
    safe on an empty or a partly filled database."""

    from django.db.migrations.executor import MigrationExecutor
    from django.db.migrations.operations.special import RunPython

    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    plan = executor.migration_plan(targets, clean_start=True)
    state = executor._create_project_state(with_applied_migrations=False)
    # Seeds only read `schema_editor.connection`; a real schema editor cannot
    # open inside the surrounding transaction on SQLite.
    editor = type("SeedEditor", (), {"connection": connection})()
    for migration, _ in plan:
        for operation in migration.operations:
            if isinstance(operation, RunPython):
                operation.code(state.apps, editor)
            operation.state_forwards(migration.app_label, state)


def restart_demo():
    """Empty the demo and return the owner, ready to choose an activity."""

    if not getattr(settings, "DEMO_MODE", False):
        raise RuntimeError("restart_demo only runs on a demo install.")
    from django.contrib.auth import get_user_model

    if connection.vendor == "postgresql" and connection.in_atomic_block:
        # PostgreSQL refuses TRUNCATE while deferred foreign-key checks are
        # pending in the open transaction; settle them first.
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    call_command("flush", interactive=False, verbosity=0)
    with transaction.atomic():
        replay_seed_migrations()
        password = settings.DEMO_PASSWORD
        call_command("bootstrap_client", display_name="محل حِسبة التجريبي", client_code="DEMO", password=password, verbosity=0)
        call_command("seed_demo_users", password=password, force=True, verbosity=0)
    return get_user_model().objects.get(username="owner")
