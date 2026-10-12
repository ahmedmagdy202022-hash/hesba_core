"""MED-001: the patient's file for clinics, medical centres and hospitals.

A patient is a customer (billing, statement and appointments stay where they
are); the file adds what a doctor needs to follow them: who they are, what
they are allergic to, what they live with, and every visit with its findings,
diagnosis and treatment. Visits never touch money; billing stays with the
appointment and the sales invoice.
"""

from django.conf import settings
from django.db import models


class Gender(models.TextChoices):
    MALE = "male", "Male"
    FEMALE = "female", "Female"


class BloodType(models.TextChoices):
    A_POS = "A+", "A+"
    A_NEG = "A-", "A-"
    B_POS = "B+", "B+"
    B_NEG = "B-", "B-"
    AB_POS = "AB+", "AB+"
    AB_NEG = "AB-", "AB-"
    O_POS = "O+", "O+"
    O_NEG = "O-", "O-"


class PatientFile(models.Model):
    customer = models.OneToOneField("master_data.Customer", on_delete=models.PROTECT, related_name="patient_file")
    file_number = models.CharField(max_length=20, unique=True)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=10, choices=Gender.choices, blank=True)
    blood_type = models.CharField(max_length=4, choices=BloodType.choices, blank=True)
    allergies = models.TextField(blank=True)
    chronic_conditions = models.TextField(blank=True)
    current_medications = models.TextField(blank=True)
    emergency_contact = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["file_number"]

    def __str__(self):
        return f"{self.file_number} {self.customer}"


class ClinicalVisit(models.Model):
    patient = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, related_name="clinical_visits")
    appointment = models.OneToOneField("appointments.Appointment", on_delete=models.SET_NULL, null=True, blank=True, related_name="clinical_visit")
    doctor = models.ForeignKey("staff.Employee", on_delete=models.SET_NULL, null=True, blank=True, related_name="clinical_visits")
    #: MED-006: for a vet, the owner's animal this visit was for (the owner stays the patient who pays).
    animal = models.ForeignKey("medical.Animal", on_delete=models.PROTECT, null=True, blank=True, related_name="visits")
    visit_date = models.DateField(db_index=True)
    complaint = models.TextField(blank=True)
    examination = models.TextField(blank=True)
    blood_pressure = models.CharField(max_length=15, blank=True)
    pulse = models.PositiveSmallIntegerField(null=True, blank=True)
    temperature = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    weight = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    height = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    diagnosis = models.TextField(blank=True)
    treatment = models.TextField(blank=True)
    follow_up_date = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="clinical_visits")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-visit_date", "-pk"]


# ---- MED-003: the dental chart and the treatment plan (HG-043, design A) ----

class ToothCondition(models.TextChoices):
    HEALTHY = "healthy", "Healthy"
    CARIES = "caries", "Caries"
    FILLED = "filled", "Filled"
    ROOT_CANAL = "root_canal", "Root canal"
    CROWN = "crown", "Crown"
    BRIDGE = "bridge", "Bridge"
    IMPLANT = "implant", "Implant"
    FRACTURED = "fractured", "Fractured"
    MISSING = "missing", "Missing"


class ToothRecord(models.Model):
    """The state of one tooth (FDI number) of one patient: the dental chart is the set of them."""

    patient = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, related_name="teeth")
    tooth = models.PositiveSmallIntegerField()
    condition = models.CharField(max_length=20, choices=ToothCondition.choices, default=ToothCondition.HEALTHY)
    notes = models.CharField(max_length=255, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tooth"]
        constraints = [models.UniqueConstraint(fields=["patient", "tooth"], name="medical_one_record_per_tooth")]

    def __str__(self):
        return f"{self.patient_id}:{self.tooth}"


class StepStatus(models.TextChoices):
    PLANNED = "planned", "Planned"
    DONE = "done", "Done"
    CANCELLED = "cancelled", "Cancelled"


class TreatmentStep(models.Model):
    """One step of a patient's treatment plan: a service on a tooth (or the whole mouth), in a numbered stage.

    A done step is billed by an ordinary draft sales invoice (``medical.dental.bill_done``); posting that
    invoice is what touches the customer's account and the cashbox. A step never moves money by itself."""

    patient = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, related_name="treatment_steps")
    tooth = models.PositiveSmallIntegerField(null=True, blank=True)
    service = models.ForeignKey("master_data.Item", on_delete=models.PROTECT, related_name="+")
    price = models.DecimalField(max_digits=14, decimal_places=2)
    stage = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=20, choices=StepStatus.choices, default=StepStatus.PLANNED, db_index=True)
    notes = models.CharField(max_length=255, blank=True)
    doctor = models.ForeignKey("staff.Employee", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    done_on = models.DateField(null=True, blank=True)
    visit = models.ForeignKey(ClinicalVisit, on_delete=models.SET_NULL, null=True, blank=True, related_name="treatment_steps")
    invoice = models.ForeignKey("sales.SalesInvoice", on_delete=models.PROTECT, null=True, blank=True, related_name="treatment_steps")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["stage", "pk"]

    def __str__(self):
        return f"{self.patient_id}:{self.stage}:{self.service_id}"


# ---- MED-004: lab tests and their results (HG-043, design A) ----

class LabTest(models.Model):
    """A test the lab runs: its unit and normal range. A result is checked against the range it had when it was entered."""

    name = models.CharField(max_length=120, unique=True)
    unit = models.CharField(max_length=30, blank=True)
    low = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    high = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    service = models.ForeignKey("master_data.Item", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    position = models.PositiveSmallIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["position", "name"]

    def __str__(self):
        return self.name


class LabFlag(models.TextChoices):
    LOW = "low", "Low"
    NORMAL = "normal", "Normal"
    HIGH = "high", "High"
    NONE = "", "No range"


class LabResult(models.Model):
    """One test's result for one patient on one day. The unit and range are copied from the test when entered."""

    patient = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, related_name="lab_results")
    test = models.ForeignKey(LabTest, on_delete=models.PROTECT, related_name="results")
    taken_on = models.DateField(db_index=True)
    value = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    text_value = models.CharField(max_length=120, blank=True)
    unit = models.CharField(max_length=30, blank=True)
    low = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    high = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    flag = models.CharField(max_length=10, choices=LabFlag.choices, blank=True)
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-taken_on", "test__position", "test__name"]
        constraints = [models.UniqueConstraint(fields=["patient", "test", "taken_on"], name="medical_one_result_per_test_and_day")]

    def __str__(self):
        return f"{self.patient_id}:{self.test_id}:{self.taken_on}"


# ---- MED-005: physiotherapy session packages (HG-043, design A) ----

class SessionPackage(models.Model):
    """A package of sessions sold on one ordinary sales invoice (revenue at sale, like any service); sessions are used one by one."""

    patient = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, related_name="session_packages")
    service = models.ForeignKey("master_data.Item", on_delete=models.PROTECT, related_name="+")
    sessions = models.PositiveSmallIntegerField()
    price = models.DecimalField(max_digits=14, decimal_places=2)
    invoice = models.OneToOneField("sales.SalesInvoice", on_delete=models.PROTECT, related_name="session_package")
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{self.patient_id}:{self.sessions}"


class PackageSession(models.Model):
    """One session used from a package. A mistaken one is cancelled (kept), which gives the session back."""

    package = models.ForeignKey(SessionPackage, on_delete=models.PROTECT, related_name="uses")
    used_on = models.DateField()
    therapist = models.ForeignKey("staff.Employee", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    visit = models.ForeignKey(ClinicalVisit, on_delete=models.SET_NULL, null=True, blank=True, related_name="package_sessions")
    notes = models.CharField(max_length=255, blank=True)
    cancelled = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-used_on", "-pk"]


# ---- MED-006: the vet's animal file (HG-043, design A, with one owner able to bring several animals) ----

class Species(models.TextChoices):
    DOG = "dog", "Dog"
    CAT = "cat", "Cat"
    BIRD = "bird", "Bird"
    RABBIT = "rabbit", "Rabbit"
    HORSE = "horse", "Horse"
    CATTLE = "cattle", "Cattle"
    SHEEP_GOAT = "sheep_goat", "Sheep / goat"
    OTHER = "other", "Other"


class Animal(models.Model):
    """An animal brought to a vet. Its owner is an ordinary customer: the one who books, is billed and pays."""

    owner = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, related_name="animals")
    name = models.CharField(max_length=80)
    species = models.CharField(max_length=20, choices=Species.choices)
    breed = models.CharField(max_length=80, blank=True)
    sex = models.CharField(max_length=10, choices=Gender.choices, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    colour = models.CharField(max_length=60, blank=True)
    microchip = models.CharField(max_length=40, blank=True)
    neutered = models.BooleanField(default=False)
    allergies = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "pk"]

    def __str__(self):
        return self.name


# ---- MED-007: the reception queue (HG-043, design A) ----

class TicketStatus(models.TextChoices):
    WAITING = "waiting", "Waiting"
    CALLED = "called", "Called in"
    DONE = "done", "Done"
    LEFT = "left", "Left without being seen"


class QueueTicket(models.Model):
    """A patient's turn on a day: numbered in arrival order, called in, then done (or left).

    Arriving for an appointment marks that appointment "arrived" through the appointments service."""

    day = models.DateField(db_index=True)
    number = models.PositiveIntegerField()
    patient = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, related_name="queue_tickets")
    appointment = models.OneToOneField("appointments.Appointment", on_delete=models.SET_NULL, null=True, blank=True, related_name="queue_ticket")
    doctor = models.ForeignKey("staff.Employee", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    status = models.CharField(max_length=10, choices=TicketStatus.choices, default=TicketStatus.WAITING, db_index=True)
    arrived_at = models.DateTimeField()
    called_at = models.DateTimeField(null=True, blank=True)
    done_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["day", "number"]
        constraints = [models.UniqueConstraint(fields=["day", "number"], name="medical_one_ticket_number_per_day")]

    def __str__(self):
        return f"{self.day}#{self.number}"


# ---- MED-008: medical insurance (HG-042, design A: two ordinary invoices per insured visit) ----

class InsuranceContract(models.Model):
    """An insurance company the clinic bills and what it covers.

    The company is an ordinary customer: its invoices, balance, aging and
    collections are that customer's, through the unchanged sales engine."""

    insurer = models.OneToOneField("master_data.Customer", on_delete=models.PROTECT, related_name="insurance_contract")
    #: The share of a visit's price the insurer pays, unless the service has its own (``ServiceCoverage``).
    coverage_percent = models.DecimalField(max_digits=5, decimal_places=2)
    #: The day of the month the previous month's claim is sent.
    claim_day = models.PositiveSmallIntegerField(default=1)
    notes = models.CharField(max_length=255, blank=True)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["insurer__name", "pk"]

    def __str__(self):
        return str(self.insurer)


class ServiceCoverage(models.Model):
    """A service an insurer covers at its own share (0 = not covered)."""

    contract = models.ForeignKey(InsuranceContract, on_delete=models.CASCADE, related_name="services")
    service = models.ForeignKey("master_data.Item", on_delete=models.PROTECT, related_name="+")
    percent = models.DecimalField(max_digits=5, decimal_places=2)

    class Meta:
        ordering = ["service__item_name", "pk"]
        constraints = [models.UniqueConstraint(fields=["contract", "service"], name="medical_one_coverage_per_service")]


class PatientCover(models.Model):
    """The insurer a patient is covered by, with their member number."""

    patient = models.OneToOneField("master_data.Customer", on_delete=models.PROTECT, related_name="insurance_cover")
    contract = models.ForeignKey(InsuranceContract, on_delete=models.PROTECT, related_name="members")
    member_number = models.CharField(max_length=60, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)


class InsuredBill(models.Model):
    """One insured visit billed as two ordinary draft invoices: the patient's share and the insurer's.

    The figures are what was agreed when it was billed. The patient's invoice is
    absent when the insurer pays it all; the appointment's own invoice is then
    the insurer's."""

    appointment = models.OneToOneField("appointments.Appointment", on_delete=models.PROTECT, related_name="insured_bill")
    contract = models.ForeignKey(InsuranceContract, on_delete=models.PROTECT, related_name="bills")
    member_number = models.CharField(max_length=60, blank=True)
    price = models.DecimalField(max_digits=14, decimal_places=2)
    percent = models.DecimalField(max_digits=5, decimal_places=2)
    insurer_share = models.DecimalField(max_digits=14, decimal_places=2)
    patient_share = models.DecimalField(max_digits=14, decimal_places=2)
    insurer_invoice = models.OneToOneField("sales.SalesInvoice", on_delete=models.PROTECT, related_name="insured_bill")
    patient_invoice = models.OneToOneField("sales.SalesInvoice", on_delete=models.PROTECT, null=True, blank=True, related_name="insured_patient_bill")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
