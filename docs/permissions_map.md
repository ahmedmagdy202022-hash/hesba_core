# Permissions Map

## Roles

- Owner
- Manager
- Cashier
- Stock Keeper
- Accountant
- Doctor or clinician («الدكتور أو الأخصائي», medical activities only, HG-084)

## Default sensitive access

- Profit report: Owner only by default
- Cost fields: hidden from Cashier
- Supplier reports: hidden from Cashier
- Full cashbox reports: hidden from Cashier and Stock Keeper

## Patient records (medical, HG-084)

Clinical records (history, allergies, visits, lab results, the dental chart, the animal card) open only for a user whose role holds a clinical permission:

- `medical.treat_patients` («الكشف على المرضى»): the doctor's role. Reads every patient's file and records visits; edits only the visits they recorded; sees their own follow-ups and their own turn in the waiting list (through the employee linked to their login).
- `medical.view_records` / `medical.write_records` («للإدارة»): owner and manager. Also the clinic report, every doctor's follow-ups and correcting another doctor's visit.

Linking a login to an employee does not open the files any more: reception and cashiers are employees too. Reception keeps booking, the agenda, the waiting list, invoicing and insurance without clinical text.

For the owner (ملاحظة للمالك): to let a doctor in, give their login the role «الدكتور أو الأخصائي» on the users screen. If the same person also works the counter, copy the cashier role on the roles screen and tick «الكشف على المرضى» in the medical records section. On upgrade, doctors already shown in the records were moved automatically to «<their old role> + كشف المرضى».

## Rule

Menu hiding is not enough. Every route and every service must check permissions in the backend.
