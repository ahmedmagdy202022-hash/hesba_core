"""PKG-UNIFY: physiotherapy session packages move to the shared package tables.

Each ``medical.SessionPackage`` becomes an ``agreements.Package`` (a new PK-
number, the same patient, service, sessions, price, invoice, notes, author and
date), and each of its ``PackageSession`` rows a ``PackageUse`` (the therapist
as the employee, the same day, notes, cancelled flag, author and date). The
link a session could keep to a clinical visit has no place in the shared table;
it is kept in the session's notes. Nothing else changes: the invoices, the
customer's account and the sessions used are exactly as before.
"""

import re

from django.db import migrations


def move(apps, schema_editor):
    SessionPackage = apps.get_model("medical", "SessionPackage")
    PackageSession = apps.get_model("medical", "PackageSession")
    Package = apps.get_model("agreements", "Package")
    PackageUse = apps.get_model("agreements", "PackageUse")

    pattern = re.compile(r"^PK-(\d+)$")
    top = max([int(m.group(1)) for m in (pattern.match(n or "") for n in Package.objects.values_list("number", flat=True)) if m] or [0])
    for old in SessionPackage.objects.order_by("pk"):
        top += 1
        new = Package.objects.create(number=f"PK-{top:04d}", customer_id=old.patient_id, service_id=old.service_id, sessions=old.sessions,
                                     price=old.price, invoice_id=old.invoice_id, notes=old.notes, created_by_id=old.created_by_id)
        Package.objects.filter(pk=new.pk).update(created_at=old.created_at)
        for session in PackageSession.objects.filter(package=old).order_by("pk"):
            notes = session.notes
            if session.visit_id:
                notes = f"{notes} (visit {session.visit_id})".strip()[:255]
            use = PackageUse.objects.create(package=new, used_on=session.used_on, employee_id=session.therapist_id, notes=notes,
                                            cancelled=session.cancelled, created_by_id=session.created_by_id)
            PackageUse.objects.filter(pk=use.pk).update(created_at=session.created_at)


class Migration(migrations.Migration):
    dependencies = [
        ("agreements", "0002_package_packageuse_timeentry"),
        ("medical", "0007_insurance"),
    ]

    operations = [migrations.RunPython(move, migrations.RunPython.noop)]
