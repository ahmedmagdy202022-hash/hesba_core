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
