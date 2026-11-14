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
    # M9 (HG-092): the analyzer's own short name for the test (GLU, HB), upper case; empty = none. Unique among the lab's tests when set.
    code = models.CharField(max_length=20, blank=True, default="")
    unit = models.CharField(max_length=30, blank=True)
    low = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    high = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    service = models.ForeignKey("master_data.Item", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    position = models.PositiveSmallIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["position", "name"]
        constraints = [models.UniqueConstraint(fields=["code"], condition=~models.Q(code=""), name="medical_lab_test_code_unique_when_set")]

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


class LabLinkCounter(models.Model):
    """HG-106: the wrong answers given to one patient result link in its whole life, counted in the database.

    The link itself is a stateless signed token (``medical.public``) and has no row of its own, so this is the only table the page owns. It holds
    nothing about the patient: ``link_hash`` is a keyed hash of the token (never the token, and not the plain hash the cache and the cookie use),
    and a link made again is a new token, so a new row that starts at zero. The row is made by the link's first counted answer
    (``public._life_take``) and the counter is raised only by one guarded atomic UPDATE, so every worker sees it, a deploy cannot reset it, and
    N answers at once never exceed ``public.TOKEN_LIFE``. Rows are small and are not purged (see HG-106).
    """

    link_hash = models.CharField(max_length=64, unique=True)
    wrong_answers = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"lab link {self.link_hash[:8]}: {self.wrong_answers}"


# ---- MED-005: physiotherapy session packages: since PKG-UNIFY they are agreements.Package (see medical.physio) ----


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
    """One insured visit (or dental steps, or a physio package: MED-009) billed as two ordinary draft invoices: the patient's share and the insurer's.

    The figures are what was agreed when it was billed. The patient's invoice is
    absent when the insurer pays it all; the appointment's own invoice is then
    the insurer's."""

    class Kind(models.TextChoices):
        VISIT = "visit", "Appointment"
        DENTAL = "dental", "Dental steps"
        PACKAGE = "package", "Session package"

    #: MED-009: an insured bill is an appointment's, a patient's done dental steps' or a physio package's.
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.VISIT)
    appointment = models.OneToOneField("appointments.Appointment", on_delete=models.PROTECT, null=True, blank=True, related_name="insured_bill")
    patient = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, null=True, related_name="insured_bills")
    description = models.CharField(max_length=255, blank=True)
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


# ---- M2: the medicines a doctor prescribes on a visit (HG-086) ----

class VisitMedicine(models.Model):
    """One medicine line on a visit's prescription: what to take, how much, how often and for how long.

    Clinical text only: no price, no stock, no invoice. A line chosen from the reference drug list
    (``pharmacy.DrugReference``, HG-082) keeps a copy of the row's names, ingredient, strength and form as they were
    when it was prescribed, so a later import that renames, switches off or deletes the row never rewrites an old
    prescription (``reference`` then only says where the copy came from, and goes empty if the row is deleted). A line
    written by hand has no reference and only the typed name. Written only by ``medical.services.save_visit``."""

    visit = models.ForeignKey(ClinicalVisit, on_delete=models.CASCADE, related_name="medicines")
    position = models.PositiveSmallIntegerField(default=0)
    reference = models.ForeignKey("pharmacy.DrugReference", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    name_ar = models.CharField(max_length=255, blank=True)
    name_en = models.CharField(max_length=255, blank=True)
    active_ingredient = models.CharField(max_length=255, blank=True)
    strength = models.CharField(max_length=60, blank=True)
    #: The dosage form's code as the reference list had it (``pharmacy.models.DosageForm``), shown in the reader's language.
    form = models.CharField(max_length=12, blank=True)
    dose = models.CharField(max_length=60, blank=True)
    frequency = models.CharField(max_length=80, blank=True)
    duration = models.CharField(max_length=60, blank=True)
    route = models.CharField(max_length=60, blank=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["position", "pk"]

    def __str__(self):
        return f"{self.visit_id}:{self.position}:{self.name_en or self.name_ar}"

    def name(self, lang="ar"):
        return (self.name_en or self.name_ar) if lang == "en" else (self.name_ar or self.name_en)


# ---- M1: visit templates per specialty (HG-089) ----

class VisitTemplate(models.Model):
    """A starting text for a visit: what the doctor usually writes for a common complaint, so the form opens half-filled.

    Clinical wording only. A template never holds a patient, an animal, vitals, a date or an appointment; it is never
    linked from a visit (a visit keeps only the text and lines the doctor chose to apply, as ordinary visit text). The
    four texts are the visit form's own fields and take the same limits. ``lines`` is a small JSON list of medicine lines
    (``reference`` of a ``pharmacy.DrugReference`` or empty, ``name``, ``dose``, ``frequency``, ``duration``, ``route``,
    ``notes``): applying the template puts them on the form as ordinary lines, and ``prescribing.resolve`` checks and
    snapshots them when the visit is saved, as for a line the doctor added by hand. Written only by
    ``medical.visit_templates``.

    ``owner`` empty means shared with every clinician; otherwise the template is that user's own (an employee link is not
    used: it grants nothing, HG-084). ``specialty`` is a code from ``visit_templates.SPECIALTIES`` (kept out of the column's
    choices so the list can grow without a migration); ``lang`` is the language its texts are written in.
    ``seed_key`` names the built-in starting template a row came from ("" for one a person wrote)."""

    name = models.CharField(max_length=120)
    lang = models.CharField(max_length=2, default="ar")
    specialty = models.CharField(max_length=20, default="general")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    complaint = models.TextField(blank=True)
    examination = models.TextField(blank=True)
    diagnosis = models.TextField(blank=True)
    treatment = models.TextField(blank=True)
    lines = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)
    position = models.PositiveIntegerField(default=0)
    seed_key = models.CharField(max_length=60, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "pk"]

    def __str__(self):
        return self.name


# ---- M8 (HG-088): inpatient: wards and beds, an admission and its charges ----

class Ward(models.Model):
    """A ward or a floor of rooms. It belongs to an entity like every scoped record (NULL: the main one, HG-034)."""

    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    entity = models.ForeignKey("entities.Entity", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    position = models.PositiveSmallIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["position", "pk"]

    def display(self, lang="ar"):
        return (self.name_en or self.name_ar) if lang == "en" else self.name_ar

    def __str__(self):
        return self.name_ar


class Bed(models.Model):
    """A bed (or a room) in a ward. ``daily_service`` is the service billed for each day in it, at its price on the day
    the bed-day is written. A bed that is not ``active`` is out of service: nobody is admitted to it."""

    ward = models.ForeignKey(Ward, on_delete=models.PROTECT, related_name="beds")
    code = models.CharField(max_length=20)
    daily_service = models.ForeignKey("master_data.Item", on_delete=models.PROTECT, related_name="+")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["ward__position", "ward_id", "code", "pk"]
        constraints = [models.UniqueConstraint(fields=["ward", "code"], name="medical_one_bed_code_per_ward")]

    def __str__(self):
        return self.code


class AdmissionState(models.TextChoices):
    ADMITTED = "admitted", "Admitted"
    DISCHARGED = "discharged", "Discharged"
    CANCELLED = "cancelled", "Cancelled"


class Admission(models.Model):
    """A patient's stay in a bed, from admission to discharge.

    It never moves money by itself. Its charges are billed at discharge by an ordinary draft sales invoice
    (``medical.inpatient.bill``); posting that invoice on the sales page is what touches the patient's account,
    the stock of the supplies and the cashbox. An advance is a deposit (S5, ``deposits.services.take``) whose
    target is this row.

    The field is ``state``, not ``status``: the deposit service reads a target's ``status`` as a booking's
    (it takes a deposit only on "booked" / "confirmed") and a target without one as always open. An admission
    is not a booking, so it carries no ``status`` and ``medical.inpatient`` decides when an advance may be taken."""

    number = models.CharField(max_length=30, unique=True)
    patient = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, related_name="admissions")
    bed = models.ForeignKey(Bed, on_delete=models.PROTECT, related_name="admissions")
    #: When the patient took the bed they are in now (the admission, or the last move): its bed-days start here.
    bed_since = models.DateTimeField()
    doctor = models.ForeignKey("staff.Employee", on_delete=models.SET_NULL, null=True, blank=True, related_name="admissions")
    admitted_at = models.DateTimeField(db_index=True)
    discharged_at = models.DateTimeField(null=True, blank=True)
    state = models.CharField(max_length=12, choices=AdmissionState.choices, default=AdmissionState.ADMITTED, db_index=True)
    #: Clinical content: shown only to those who may read patient records (HG-084).
    diagnosis = models.TextField(blank=True)
    entity = models.ForeignKey("entities.Entity", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-admitted_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["patient"], condition=models.Q(state="admitted"), name="medical_one_open_admission_per_patient"),
            models.UniqueConstraint(fields=["bed"], condition=models.Q(state="admitted"), name="medical_one_open_admission_per_bed"),
        ]

    def __str__(self):
        return self.number


class ChargeKind(models.TextChoices):
    BED_DAY = "bed_day", "Bed day"
    PROCEDURE = "procedure", "Procedure or service"
    OPERATION = "operation", "Operation"
    SUPPLY = "supply", "Supply"


class AdmissionCharge(models.Model):
    """Something the stay is billed for: a day in a bed, a procedure, an operation (with its surgeon) or a supply.

    A charge is billed once: ``invoice`` is the draft it went on, and a charge on a live invoice can no longer be
    removed. A bed-day is written by the service only (one per calendar day of the stay, never two)."""

    admission = models.ForeignKey(Admission, on_delete=models.PROTECT, related_name="charges")
    kind = models.CharField(max_length=12, choices=ChargeKind.choices)
    item = models.ForeignKey("master_data.Item", on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=1)
    price = models.DecimalField(max_digits=14, decimal_places=2)
    day = models.DateField(db_index=True)
    #: The bed a bed-day was spent in.
    bed = models.ForeignKey(Bed, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    surgeon = models.ForeignKey("staff.Employee", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    #: Clinical content, like the diagnosis: never copied onto the invoice.
    note = models.CharField(max_length=255, blank=True)
    invoice = models.ForeignKey("sales.SalesInvoice", on_delete=models.PROTECT, null=True, blank=True, related_name="admission_charges")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["day", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["admission", "day"], condition=models.Q(kind="bed_day"), name="medical_one_bed_day_per_day"),
        ]

    def __str__(self):
        return f"{self.admission_id}:{self.kind}:{self.day}"


# ---- M7 (HG-090): the vet's vaccines and the doses given ----

class Vaccine(models.Model):
    """A vaccine the clinic gives. A set-up record (owner and manager), like a ward: it belongs to an entity (NULL: the main one, HG-034).

    ``species`` is one of the animal file's own species values (``Species``) or empty for any species. ``interval_days`` is the
    days until the next dose or booster, empty for a one-off. ``item`` is the stock or service item a dose is billed on, empty
    when the clinic does not bill it from here (a campaign, a free dose). Never read by posting: it only says what to put on a draft."""

    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    species = models.CharField(max_length=20, choices=Species.choices, blank=True)
    interval_days = models.PositiveSmallIntegerField(null=True, blank=True)
    item = models.ForeignKey("master_data.Item", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    active = models.BooleanField(default=True)
    entity = models.ForeignKey("entities.Entity", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name_ar", "pk"]

    def display(self, lang="ar"):
        return (self.name_en or self.name_ar) if lang == "en" else (self.name_ar or self.name_en)

    def __str__(self):
        return self.name_ar


class Vaccination(models.Model):
    """One dose of a vaccine given to an animal.

    ``next_due`` is when the next dose is expected: worked out from the vaccine's interval when the dose is recorded, or typed by
    the vet; empty for a one-off. A dose is *superseded* when a later dose of the same vaccine was given to the same animal; that
    is worked out when read (``medical.vaccines.superseded``), never stored, so removing a mistaken later dose brings the earlier
    due date back. ``note`` is clinical text (HG-084): only those who may read patient records see it, and it is never copied onto
    an invoice or a message. ``invoice`` is the draft sales invoice the dose was billed on (``medical.vaccines.bill``); a dose on a
    live invoice cannot be removed. The row is written only by ``medical.vaccines``."""

    animal = models.ForeignKey(Animal, on_delete=models.PROTECT, related_name="vaccinations")
    vaccine = models.ForeignKey(Vaccine, on_delete=models.PROTECT, related_name="vaccinations")
    given_on = models.DateField(db_index=True)
    next_due = models.DateField(null=True, blank=True, db_index=True)
    batch = models.CharField(max_length=60, blank=True)
    given_by = models.ForeignKey("staff.Employee", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    visit = models.ForeignKey(ClinicalVisit, on_delete=models.SET_NULL, null=True, blank=True, related_name="vaccinations")
    note = models.TextField(blank=True)
    invoice = models.ForeignKey("sales.SalesInvoice", on_delete=models.PROTECT, null=True, blank=True, related_name="vaccinations")
    entity = models.ForeignKey("entities.Entity", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-given_on", "-pk"]

    def __str__(self):
        return f"{self.animal_id}:{self.vaccine_id}:{self.given_on}"
