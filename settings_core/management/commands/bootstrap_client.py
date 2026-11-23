from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import UserProfile
from permissions.models import Role, RoleCode
from settings_core.models import ClientProfile


class Command(BaseCommand):
    help = (
        "Prepare a fresh Hesba database for first use: create the client profile, "
        "the owner account, and the profile row that links that account to the owner role."
    )

    def add_arguments(self, parser):
        parser.add_argument("--client-code", default="HESBA-001", help="Short code for this installation.")
        parser.add_argument("--legal-name", default=None, help="Registered business name. Defaults to the display name.")
        parser.add_argument("--display-name", default="Hesba Client", help="Business name shown in the interface.")
        parser.add_argument("--username", default="owner", help="Username for the owner account.")
        parser.add_argument("--email", default="", help="Email for the owner account.")
        parser.add_argument(
            "--password",
            default=None,
            help="Owner password. Falls back to DJANGO_SUPERUSER_PASSWORD, then to a prompt.",
        )

    def _resolve_password(self, supplied):
        import getpass
        import os

        password = supplied or os.environ.get("DJANGO_SUPERUSER_PASSWORD")
        if password:
            return password

        password = getpass.getpass("Owner password: ")
        if not password:
            raise CommandError("A password is required to create the owner account.")
        if password != getpass.getpass("Owner password (again): "):
            raise CommandError("The two passwords did not match.")
        return password

    @transaction.atomic
    def handle(self, *args, **options):
        profile, profile_created = self._ensure_client_profile(options)
        user, user_created = self._ensure_owner_user(options)
        role = self._owner_role()
        user_profile, link_created = self._ensure_user_profile(user, role, profile)

        if not options["verbosity"]:
            return

        def state(created):
            return "created" if created else "already present"

        self.stdout.write(self.style.SUCCESS("Hesba is ready to sign in to."))
        self.stdout.write(f"Client profile: {profile}  ({state(profile_created)})")
        self.stdout.write(f"Owner account:  {user.username}  ({state(user_created)})")
        self.stdout.write(f"Role link:      {user_profile.role.code}  ({state(link_created)})")
        self.stdout.write("")
        self.stdout.write("Next: start the server and sign in at /login/.")

    def _ensure_client_profile(self, options):
        existing = ClientProfile.get_active()
        if existing is not None:
            return existing, False

        display_name = options["display_name"]
        profile = ClientProfile(
            client_code=options["client_code"],
            legal_name=options["legal_name"] or display_name,
            display_name=display_name,
        )
        profile.save()
        return profile, True

    def _ensure_owner_user(self, options):
        users = get_user_model().objects
        username = options["username"]
        existing = users.filter(username=username).first()
        if existing is not None:
            return existing, False

        return (
            users.create_superuser(
                username=username,
                email=options["email"],
                password=self._resolve_password(options["password"]),
            ),
            True,
        )

    def _owner_role(self):
        role = Role.objects.filter(code=RoleCode.OWNER).first()
        if role is None:
            raise CommandError(
                "The owner role is missing. Run 'manage.py migrate' so the role and "
                "permission seed data is applied, then run this command again."
            )
        return role

    def _ensure_user_profile(self, user, role, client_profile):
        existing = UserProfile.objects.filter(user=user).first()
        if existing is not None:
            return existing, False

        return (
            UserProfile.objects.create(
                user=user,
                role=role,
                display_name=client_profile.display_name,
                active=True,
            ),
            True,
        )
