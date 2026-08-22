from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import UserProfile
from permissions.models import Role, RoleCode


DEFAULT_PASSWORD = "hesba-demo-only"

# One account per seeded role, so the permission matrix can be exercised from
# the interface instead of only from unit tests. Support is deliberately absent:
# real support access is granted through SupportAccessGrant and audited, not
# handed out as a standing login.
DEMO_USERS = (
    (RoleCode.OWNER, "owner", "المالك"),
    (RoleCode.MANAGER, "manager", "المدير"),
    (RoleCode.CASHIER, "cashier", "الكاشير"),
    (RoleCode.STOCK_KEEPER, "stock_keeper", "أمين المخزن"),
    (RoleCode.ACCOUNTANT, "accountant", "المحاسب"),
)


class Command(BaseCommand):
    help = (
        "Create one demo account per role so role-based screens can be checked "
        "by signing in. Refuses to run with DEBUG off unless --force is passed."
    )

    def add_arguments(self, parser):
        parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Shared password for every demo account.")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Allow seeding with DEBUG off. Never use this on a client's production database.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "Refusing to seed accounts with known passwords while DEBUG is off. "
                "These are demo logins, not real users. Pass --force only if this "
                "database is genuinely disposable."
            )

        password = options["password"]
        results = []

        for role_code, username, display_name in DEMO_USERS:
            role = Role.objects.filter(code=role_code).first()
            if role is None:
                raise CommandError(
                    f"The {role_code} role is missing. Run 'manage.py migrate' so the "
                    "role and permission seed data is applied, then try again."
                )
            results.append(self._seed(username, display_name, role, password))

        if not options["verbosity"]:
            return

        self.stdout.write(self.style.SUCCESS(f"{len(results)} demo accounts ready."))
        for username, role_code, created in results:
            self.stdout.write(f"  {username:14} {role_code:14} ({'created' if created else 'already present'})")
        self.stdout.write("")
        self.stdout.write(f"Password for all of them: {password}")

    def _seed(self, username, display_name, role, password):
        users = get_user_model().objects
        user = users.filter(username=username).first()
        created = user is None

        if created:
            user = users.create_user(username=username, password=password)
        else:
            # Keep an existing demo account usable rather than leaving whoever
            # runs this wondering why the printed password does not work.
            user.set_password(password)
            user.save(update_fields=["password"])

        UserProfile.objects.update_or_create(
            user=user,
            defaults={"role": role, "display_name": display_name, "active": True},
        )
        return username, role.code, created
