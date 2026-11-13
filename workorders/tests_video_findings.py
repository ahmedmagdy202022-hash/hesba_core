"""Services video findings: the intake receipt's heading is the document's name, the button keeps the verb (f04)."""

from django.urls import reverse

from .tests import WorkshopSetup


class IntakeReceiptTitleTests(WorkshopSetup):
    def page(self, lang):
        self.client.force_login(self.owner)
        order = self.order()
        return self.client.get(reverse("workorders:receipt", args=[order.pk]), {"lang": lang}).content.decode()

    def test_arabic_heading_is_the_documents_name_and_the_button_keeps_the_verb(self):
        body = self.page("ar")
        self.assertIn('<h2 class="pr-doc__title">إيصال استلام جهاز</h2>', body)
        self.assertIn('data-print>اطبع إيصال الاستلام</button>', body)
        self.assertNotIn('<h2 class="pr-doc__title">اطبع', body)

    def test_english_heading_is_a_noun_too(self):
        body = self.page("en")
        self.assertIn('<h2 class="pr-doc__title">Device intake receipt</h2>', body)
        self.assertIn('data-print>Print the intake receipt</button>', body)
