"""I18N-001: service error messages, in Arabic on Arabic screens.

The business services raise English ValidationErrors (tests and logs rely on
that wording), and the screens used to show them as-is to Arabic users. This
translates them at display time only: exact sentences first, then the few that
carry numbers or names. Unknown text passes through unchanged, so a message
nobody listed is still shown, just untranslated.
"""

import re


EXACT = {
    "A cancellation reason is required.": "اكتب سبب الإلغاء.",
    "A cash operation with this reference already exists.": "فيه حركة نقدية بنفس رقم المرجع ده.",
    "A customer collection with this number already exists.": "فيه تحصيل بنفس الرقم ده.",
    "A destination cashbox is required.": "اختار الخزنة المستلمة.",
    "A purchase invoice with posted return documents cannot be cancelled.": "فاتورة الشراء دي عليها مرتجعات مرحّلة، فمينفعش تتلغي.",
    "A purchase line discount cannot exceed its gross amount.": "خصم السطر أكبر من قيمته.",
    "A purchase return must contain at least one line.": "مرتجع الشراء لازم يكون فيه سطر واحد على الأقل.",
    "A purchase return with this number already exists.": "فيه مرتجع شراء بنفس الرقم.",
    "A sales invoice with posted return documents cannot be cancelled.": "فاتورة البيع دي عليها مرتجعات مرحّلة، فمينفعش تتلغي.",
    "A sales invoice with this number already exists.": "فيه فاتورة بيع بنفس الرقم ده. سيبه فاضي وهيتعمل رقم جديد لوحده.",
    "A sales line discount cannot exceed its gross amount.": "خصم السطر أكبر من قيمته.",
    "A sales return must contain at least one line.": "مرتجع البيع لازم يكون فيه سطر واحد على الأقل.",
    "A sales return with this number already exists.": "فيه مرتجع بيع بنفس الرقم.",
    "A source line may appear only once in a return document.": "كل سطر من الفاتورة يتكتب مرة واحدة بس في المرتجع.",
    "A stock operation with this reference already exists.": "فيه حركة مخزون بنفس رقم المرجع.",
    "A supplier payment with this number already exists.": "فيه سداد مورد بنفس الرقم.",
    "A transfer cannot have an adjustment direction.": "التحويل مالوش اتجاه تسوية.",
    "A transfer requires source and destination cashboxes.": "التحويل محتاج خزنة مصدر وخزنة مستلمة.",
    "A transfer requires source and destination locations.": "التحويل محتاج مخزن مصدر ومخزن مستلم.",
    "Add at least one purchase line.": "ضيف سطر شراء واحد على الأقل.",
    "Add at least one purchase return line.": "ضيف سطر مرتجع واحد على الأقل.",
    "Add at least one sales line.": "ضيف سطر بيع واحد على الأقل.",
    "Add at least one sales return line.": "ضيف سطر مرتجع واحد على الأقل.",
    "Adjustment amount cannot be zero.": "قيمة التسوية مينفعش تكون صفر.",
    "Adjustment direction must be in or out.": "اتجاه التسوية لازم يكون زيادة أو نقص.",
    "Adjustment quantity must be greater than zero.": "كمية التسوية لازم تكون أكبر من صفر.",
    "Adjustment reason is required.": "اكتب سبب التسوية.",
    "An adjustment direction is required.": "اختار اتجاه التسوية.",
    "An adjustment requires exactly one location.": "التسوية محتاجة مخزن واحد بس.",
    "An expense needs a description.": "اكتب بيان المصروف.",
    "Cancel reason is required.": "اكتب سبب الإلغاء.",
    "Cash operation amount must be greater than zero.": "المبلغ لازم يكون أكبر من صفر.",
    "Cash operation reason is required.": "اكتب سبب الحركة.",
    "Cash operation reversal reason is required.": "اكتب سبب العكس.",
    "Cash operations require active cashboxes.": "الحركة محتاجة خزنة نشطة.",
    "Cashbox transfers require the same currency.": "التحويل بين الخزن لازم يكون بنفس العملة.",
    "Choose an active expense category.": "اختار بند مصروف نشط.",
    "Customer payment cancellation reason is required.": "اكتب سبب إلغاء التحصيل.",
    "Direct cash in requires only a destination cashbox.": "الإيداع محتاج خزنة مستلمة بس.",
    "Direct cash out requires only a source cashbox.": "الصرف محتاج خزنة مصدر بس.",
    "Every return line must belong to the source invoice.": "كل سطر في المرتجع لازم يكون من نفس الفاتورة.",
    "Expense amount must be greater than zero.": "مبلغ المصروف لازم يكون أكبر من صفر.",
    "Invoice discount cannot make the total negative.": "خصم الفاتورة أكبر من إجماليها.",
    "No period found for this date, and future months are not opened automatically.": "مفيش فترة محاسبية للتاريخ ده، والشهور الجاية مش بتتفتح لوحدها. افتحها من شاشة الفترات.",
    "No period found for this date.": "مفيش فترة محاسبية للتاريخ ده.",
    "Only closed periods can be reopened.": "مينفعش تعيد فتح غير فترة مقفولة.",
    "Only draft purchase invoices can be posted.": "الفاتورة دي مترحّلة قبل كده.",
    "Only draft sales invoices can be posted.": "الفاتورة دي مترحّلة قبل كده.",
    "Only posted cashbox operations can be reversed.": "الحركة دي اتعكست قبل كده.",
    "Only posted customer payments can be cancelled.": "التحصيل ده اتلغى قبل كده.",
    "Only posted opening-balance adjustments can be reversed.": "التسوية دي اتعكست قبل كده.",
    "Only posted purchase invoices can be cancelled.": "الفاتورة دي مش مرحّلة أو اتلغت قبل كده.",
    "Only posted purchase returns can be reversed.": "المرتجع ده اتعكس قبل كده.",
    "Only posted sales invoices can be cancelled.": "الفاتورة دي مش مرحّلة أو اتلغت قبل كده.",
    "Only posted sales returns can be reversed.": "المرتجع ده اتعكس قبل كده.",
    "Only posted stock operations can be reversed.": "حركة المخزون دي اتعكست قبل كده.",
    "Only posted supplier payments can be cancelled.": "السداد ده اتلغى قبل كده.",
    "Opening-balance correction cannot make the cashbox negative.": "التسوية دي هتخلّي الخزنة بالسالب.",
    "Period is already closed.": "الفترة دي مقفولة بالفعل.",
    "Period must be open for posting.": "تاريخ الحركة جوّه فترة مقفولة. افتح الفترة الأول أو غيّر التاريخ.",
    "Posted purchase invoice has no lines to reverse.": "الفاتورة مفيهاش سطور.",
    "Posted sales invoice has no lines to reverse.": "الفاتورة مفيهاش سطور.",
    "Purchase invoice must have at least one line before posting.": "الفاتورة لازم يكون فيها سطر واحد على الأقل قبل الترحيل.",
    "Purchase invoice must have at least one line.": "الفاتورة لازم يكون فيها سطر واحد على الأقل.",
    "Purchase return reason is required.": "اكتب سبب المرتجع.",
    "Purchase return reversal reason is required.": "اكتب سبب عكس المرتجع.",
    "Purchase returns cannot exceed the source invoice total.": "المرتجع أكبر من إجمالي الفاتورة.",
    "Purchase returns require a posted source invoice.": "المرتجع محتاج فاتورة مرحّلة.",
    "Reopen reason is required.": "اكتب سبب إعادة الفتح.",
    "Return line must belong to the source invoice.": "السطر ده مش من نفس الفاتورة.",
    "Return quantity must be greater than zero.": "كمية المرتجع لازم تكون أكبر من صفر.",
    "Reversal reason is required.": "اكتب سبب العكس.",
    "Sales invoice must have at least one line before posting.": "الفاتورة لازم يكون فيها سطر واحد على الأقل قبل الترحيل.",
    "Sales invoice must have at least one line.": "الفاتورة لازم يكون فيها سطر واحد على الأقل.",
    "Sales return reason is required.": "اكتب سبب المرتجع.",
    "Sales return reversal reason is required.": "اكتب سبب عكس المرتجع.",
    "Sales returns cannot exceed the source invoice total.": "المرتجع أكبر من إجمالي الفاتورة.",
    "Sales returns require a posted source invoice.": "المرتجع محتاج فاتورة مرحّلة.",
    "Stock adjustment reason is required.": "اكتب سبب تسوية المخزون.",
    "Stock operation reversal reason is required.": "اكتب سبب العكس.",
    "Stock transfer reason is required.": "اكتب سبب التحويل.",
    "Supplier payment cancellation reason is required.": "اكتب سبب إلغاء السداد.",
    "The cashbox cannot become negative for this sales return refund.": "الخزنة مفيهاش رصيد كفاية لرد مبلغ المرتجع.",
    "The cashbox cannot become negative when reversing this cash in.": "عكس الإيداع ده هيخلّي الخزنة بالسالب.",
    "The cashbox cannot become negative when reversing this return.": "عكس المرتجع ده هيخلّي الخزنة بالسالب.",
    "The destination cashbox cannot become negative when reversing this transfer.": "عكس التحويل هيخلّي الخزنة المستلمة بالسالب.",
    "The destination no longer has enough stock to reverse this transfer.": "المخزن المستلم مابقاش فيه كمية كفاية لعكس التحويل.",
    "The location no longer has enough stock to reverse this adjustment.": "المخزن مابقاش فيه كمية كفاية لعكس التسوية.",
    "The selected opening-balance target is required.": "اختار الحساب اللي هتتعمله التسوية.",
    "The source cashbox cannot become negative.": "الخزنة مفيهاش رصيد كفاية للمبلغ ده.",
    "This category already exists.": "البند ده موجود بالفعل.",
    "This invoice is registered with the e-invoice portal. Cancel it on the e-invoice page first, then cancel it here.": "الفاتورة دي متسجلة في منظومة الفاتورة الإلكترونية؛ الغيها الأول من صفحة الفاتورة الإلكترونية، وبعدين الغيها هنا.",
    "This date falls inside closed books; no new period can be opened there.": "التاريخ ده جوّه فترة اتقفلت؛ مينفعش تتسجل فيه حركة.",
    "This expense is already cancelled.": "المصروف ده اتلغى قبل كده.",
    "This record has no operational use yet; edit its opening balance directly.": "الحساب ده لسه ما اتستخدمش؛ عدّل رصيده الافتتاحي مباشرة.",
    "Transfer cashboxes must be different.": "الخزنتين لازم يكونوا مختلفين.",
    "Transfer locations must be different.": "المخزنين لازم يكونوا مختلفين.",
    "Transfer quantity must be greater than zero.": "كمية التحويل لازم تكون أكبر من صفر.",
    "Unknown cash operation type.": "نوع حركة غير معروف.",
    "Paid now cannot exceed invoice total.": "المدفوع أكبر من إجمالي الفاتورة.",
    "Value cannot be negative.": "القيمة مينفعش تكون بالسالب.",
    "This field is required.": "الخانة دي مطلوبة.",
    "This field is required for a sales line.": "الخانة دي مطلوبة في سطر البيع.",
    "This field is required for a purchase line.": "الخانة دي مطلوبة في سطر الشراء.",
    "Customer ledger entry cannot increase and decrease due at the same time.": "قيد العميل مينفعش يزوّد وينقّص في نفس الوقت.",
    "Supplier ledger entry cannot increase and decrease due at the same time.": "قيد المورد مينفعش يزوّد وينقّص في نفس الوقت.",
    "Related period must be closed.": "الفترة المرتبطة لازم تكون مقفولة.",
    "Post-closing adjustment must reference a closed period.": "تسوية ما بعد الإقفال لازم تكون على فترة مقفولة.",
    "Only draft post-closing adjustments can be posted.": "التسوية دي مترحّلة قبل كده.",
    "Only posted post-closing adjustments can be cancelled.": "التسوية دي اتلغت قبل كده.",
    "Return total must equal cash plus due reversal.": "إجمالي المرتجع لازم يساوي المردود نقدًا + المخصوم من الآجل.",
    "An opening-balance adjustment must reference exactly one target.": "التسوية لازم تكون على حساب واحد بس.",
    "Unknown opening-balance target type.": "نوع الحساب غير معروف.",
}

PATTERNS = (
    (r"^Not enough stock for item (?P<item>.+)\. Available: (?P<available>[^,]+), required: (?P<required>.+)\.$", "الكمية مش كفاية للصنف {item}: المتاح {available} والمطلوب {required}."),
    (r"^Not enough stock to (?:adjust out|transfer)\. Available: (?P<available>[^,]+), required: (?P<required>.+)\.$", "الكمية مش كفاية: المتاح {available} والمطلوب {required}."),
    (r"^Not enough stock to return item (?P<item>.+?)\. ?(?:Available: (?P<available>.+)\.)?$", "مفيش كمية كفاية لإرجاع الصنف {item}."),
    (r"^Not enough returned stock to reverse item (?P<item>.+?)\. ?(?:Available: (?P<available>.+)\.)?$", "مفيش كمية كفاية لعكس مرتجع الصنف {item}."),
    (r"^Return quantity exceeds the remaining quantity for line (?P<line>\d+)\.$", "كمية المرتجع أكبر من الباقي في السطر {line}."),
    (r"^Payment status must be (?P<status>\w+)\.$", "حالة السداد مش متطابقة مع المبالغ."),
    (r"^This screen needs the (?P<perm>[\w.]+) permission\.$", "الشاشة دي محتاجة صلاحية مش عندك."),
)
_COMPILED = tuple((re.compile(pattern), template) for pattern, template in PATTERNS)


def translate(text, lang="ar"):
    """One message in the screen's language (Arabic by default)."""

    text = str(text or "")
    if lang == "en" or not text:
        return text
    parts = [part.strip() for part in text.split("; ")]
    return "؛ ".join(_one(part) for part in parts)


def _one(text):
    if text in EXACT:
        return EXACT[text]
    for pattern, template in _COMPILED:
        match = pattern.match(text)
        if match:
            return template.format(**{key: value or "" for key, value in match.groupdict().items()})
    return text
