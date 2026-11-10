"""HG-084: who reads patient records.

Until now any active user linked to an active employee read and wrote every patient's clinical file
(``medical.services._is_doctor``), so a receptionist or a cashier on the payroll did too. From now on a
clinical permission held by role is needed:

1. a new permission ``medical.treat_patients`` (read the files, record visits), and a new seeded role
   «الدكتور أو الأخصائي» (``doctor``) that holds only it. ``view_records`` / ``write_records`` keep their
   practice-wide meaning (owner, manager) and get labels that say so;
2. an upgraded install keeps its clinicians working: a user who used the employee link and is shown by the
   records to be a clinician (the doctor on a visit or a dental step, the therapist on a physiotherapy
   session, or the one who entered lab results) moves to a copy of their own role plus
   ``medical.treat_patients`` («<role> + كشف المرضى», code ``custom_hg084_<role>``): everything they could do,
   they still can. A clinician with no role gets the doctor role. Every move is in the audit log.
   Everyone else linked to an employee (reception, cashiers, stock keepers) loses the clinical screens;
   the owner gives them back from the users or roles screen if that was meant.

On a fresh database there is nobody to move: only the permission, the role and the labels are written.
The reverse moves the users back to their old role, removes the copies, the role (unless still worn) and
the permission, and restores the old labels; it leaves the audit rows, which are append-only.
"""

from django.db import migrations

TREAT = "medical.treat_patients"
VIEW, WRITE = "medical.view_records", "medical.write_records"
#: code: (new Arabic, new English, old Arabic, old English)
LABELS = {
    TREAT: ("الكشف على المرضى: قراءة ملفاتهم وتسجيل الكشوفات", "Treat patients: read their files and record visits", "", ""),
    VIEW: ("كل الملفات الطبية وتقرير العيادة ومتابعات كل الدكاترة (للإدارة)", "All patient files, the clinic report and every doctor's follow-ups (management)",
           "عرض الملفات الطبية للمرضى", "View patient medical files"),
    WRITE: ("تعديل أي كشف أو ملف طبي، حتى اللي سجّله دكتور تاني (للإدارة)", "Change any visit or patient file, another doctor's too (management)",
            "تسجيل الكشوفات وتعديل الملف الطبي", "Record visits and edit medical files"),
}
DOCTOR = ("doctor", "الدكتور أو الأخصائي", "Doctor or clinician",
          "يكشف على المرضى: يقرأ ملفاتهم ويسجّل الكشوفات والتحاليل، ويشوف دوره في قائمة الانتظار؛ من غير الفواتير والخزنة والتقارير.")
CLONE_PREFIX = "custom_hg084_"
#: Never in a role the owner did not make himself (permissions.role_services.LOCKED).
LOCKED = ("permissions.manage_roles",)


def _medical_somewhere(apps):
    ClientProfile = apps.get_model("settings_core", "ClientProfile")
    Entity = apps.get_model("entities", "Entity")
    return ClientProfile.objects.filter(activity_slug="medical").exists() or Entity.objects.filter(activity_slug="medical").exists()


def clinician_user_ids(apps):
    """Users who used the employee link and whom the records show treating patients."""

    Employee = apps.get_model("staff", "Employee")
    employees = set(apps.get_model("medical", "ClinicalVisit").objects.exclude(doctor=None).values_list("doctor_id", flat=True))
    employees |= set(apps.get_model("medical", "TreatmentStep").objects.exclude(doctor=None).values_list("doctor_id", flat=True))
    if _medical_somewhere(apps):  # a package's session in a salon or a gym is not a patient's
        employees |= set(apps.get_model("agreements", "PackageUse").objects.exclude(employee=None).values_list("employee_id", flat=True))
    users = set(Employee.objects.filter(pk__in=employees, active=True).exclude(user=None).values_list("user_id", flat=True))
    lab = set(apps.get_model("medical", "LabResult").objects.values_list("entered_by_id", flat=True))  # a lab's results name no employee
    users |= set(Employee.objects.filter(active=True, user_id__in=lab).values_list("user_id", flat=True))
    return users


def _unique_name(Role, name):
    candidate, n = name[:255], 2
    while Role.objects.filter(name_ar__iexact=candidate).exists():
        candidate, n = f"{name} ({n})"[:255], n + 1
    return candidate


def _clone(apps, source, treat):
    Role = apps.get_model("permissions", "Role")
    RolePermission = apps.get_model("permissions", "RolePermission")
    code = f"{CLONE_PREFIX}{source.code}"
    clone = Role.objects.filter(code=code).first()
    if clone is not None:
        return clone
    clone = Role.objects.create(
        code=code, name_ar=_unique_name(Role, f"{source.name_ar or source.code} + كشف المرضى"),
        name_en=f"{source.name_en or source.code} + patient care",
        description=f"اتعمل تلقائيًا (HG-084) للدكاترة اللي كانوا على دور «{source.name_ar or source.code}»: نفس صلاحياته وزيادة الكشف على المرضى.",
        is_system_role=False, active=True)
    for grant in RolePermission.objects.filter(role=source, allow=True).exclude(permission__code__in=LOCKED):
        RolePermission.objects.create(role=clone, permission_id=grant.permission_id, allow=True)
    RolePermission.objects.update_or_create(role=clone, permission=treat, defaults={"allow": True})
    return clone


def _audit(apps, profile, before, after):
    apps.get_model("audit", "AuditLog").objects.create(
        event_type="permission_change", actor=None, module="permissions", action="hg084_clinician_role", object_type="UserProfile",
        object_id=str(profile.pk), before_data={"user": profile.user_id, "role": before}, after_data={"user": profile.user_id, "role": after},
        reason="HG-084: patient records need a clinical permission; this user treats patients, so they keep it.")


def forwards(apps, schema_editor):
    Permission = apps.get_model("permissions", "Permission")
    Role = apps.get_model("permissions", "Role")
    RolePermission = apps.get_model("permissions", "RolePermission")
    UserProfile = apps.get_model("accounts", "UserProfile")
    User = apps.get_model("auth", "User")

    treat, _ = Permission.objects.update_or_create(code=TREAT, defaults={
        "name_ar": LABELS[TREAT][0], "name_en": LABELS[TREAT][1], "module": "medical", "is_report_permission": False,
        "is_sensitive_finance": False, "active": True})
    for code in (VIEW, WRITE):
        Permission.objects.filter(code=code).update(name_ar=LABELS[code][0], name_en=LABELS[code][1])
    code, name_ar, name_en, description = DOCTOR
    doctor, _ = Role.objects.update_or_create(code=code, defaults={"name_ar": name_ar, "name_en": name_en, "description": description,
                                                                  "is_system_role": True, "active": True})
    RolePermission.objects.update_or_create(role=doctor, permission=treat, defaults={"allow": True})
    # The owner and the manager already read and write every file; they also treat patients.
    for role in Role.objects.filter(code__in=("owner", "manager")):
        RolePermission.objects.update_or_create(role=role, permission=treat, defaults={"allow": True})

    clinical = (VIEW, TREAT)
    for user in User.objects.filter(pk__in=clinician_user_ids(apps), is_active=True).order_by("pk"):
        profile = UserProfile.objects.filter(user=user).select_related("role").first()
        if profile is None:
            profile = UserProfile.objects.create(user=user, role=doctor, active=True)
            _audit(apps, profile, None, doctor.code)
            continue
        if not profile.active:
            continue  # switched off by the owner: the employee link wrongly ignored that; it stays off
        if profile.role is None:
            profile.role = doctor
            profile.save(update_fields=["role"])
            _audit(apps, profile, None, doctor.code)
            continue
        role = profile.role
        if not role.active or role.rolepermission_set.filter(permission__code__in=clinical, allow=True, permission__active=True).exists():
            continue  # already reads the files by role (owner, manager), or a role the owner switched off
        clone = _clone(apps, role, treat)
        profile.role = clone
        profile.save(update_fields=["role"])
        _audit(apps, profile, role.code, clone.code)


def backwards(apps, schema_editor):
    Permission = apps.get_model("permissions", "Permission")
    Role = apps.get_model("permissions", "Role")
    UserProfile = apps.get_model("accounts", "UserProfile")
    AuditLog = apps.get_model("audit", "AuditLog")

    for clone in Role.objects.filter(code__startswith=CLONE_PREFIX):
        source = Role.objects.filter(code=clone.code[len(CLONE_PREFIX):]).first()
        if source is None:
            continue
        for profile in UserProfile.objects.filter(role=clone):
            profile.role = source
            profile.save(update_fields=["role"])
            AuditLog.objects.create(event_type="permission_change", actor=None, module="permissions", action="hg084_clinician_role_reversed",
                                    object_type="UserProfile", object_id=str(profile.pk), before_data={"role": clone.code},
                                    after_data={"role": source.code}, reason="HG-084 migration reversed.")
        clone.delete()
    doctor = Role.objects.filter(code=DOCTOR[0]).first()
    if doctor is not None:
        moved = AuditLog.objects.filter(action="hg084_clinician_role", after_data__role=DOCTOR[0]).values_list("object_id", flat=True)
        UserProfile.objects.filter(role=doctor, pk__in=[int(pk) for pk in moved if pk.isdigit()]).update(role=None)
        if not UserProfile.objects.filter(role=doctor).exists():  # PROTECT: a role somebody wears stays
            doctor.delete()
    Permission.objects.filter(code=TREAT).delete()
    for code in (VIEW, WRITE):
        Permission.objects.filter(code=code).update(name_ar=LABELS[code][2], name_en=LABELS[code][3])


class Migration(migrations.Migration):
    dependencies = [
        ("medical", "0009_insured_bill_kinds"),
        ("permissions", "0012_seed_giftcard_permissions"),
        ("accounts", "0003_twofactor"),
        ("agreements", "0003_move_physio_packages"),
        ("staff", "0001_initial"),
        ("audit", "0002_rename_audit_audit_created_87df99_idx_audit_audit_created_2c1626_idx_and_more"),
        ("settings_core", "0006_clientprofile_activity_slug_and_more"),
        ("entities", "0001_initial"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
