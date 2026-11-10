from django.conf import settings
from django.contrib import admin as django_admin
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.views import LogoutView
from django.urls import include, path

from config import demo as demo_views
from config import demo_first_run as first_run
from django.views.generic import TemplateView

from config.health import healthz
from config.nightly import digest, nightly

from reports.dashboard_views import dashboard
from reports.status_views import status_counts_report
from reports.functional_views import report_hub
from reports.views import home
from accounts.login_guard import GuardedLoginView, login_verify
from printing import views as printing_views
from workorders import public as workorders_public
from payments import views as payments_views
from appointments import views_booking as booking_views
from settings_core.setup_views import after_login, root_redirect, setup_complete, setup_modules, setup_questions, setup_review, sub_activity_step


urlpatterns = [
    path("", root_redirect, name="root_redirect"),
    path("healthz/", healthz, name="healthz"),
    # BACKUP-003: token-guarded daily backup trigger for hosts without cron.
    path("ops/nightly/", nightly, name="ops_nightly"),
    path("ops/digest/", digest, name="ops_digest"),
    path("login/", GuardedLoginView.as_view(template_name="registration/login.html", next_page="/start/"), name="login"),
    # SEC-002: the authenticator-app code after the password, for users who switched it on.
    path("login/verify/", login_verify, name="login_verify"),
    # Logout must not itself require a login: a signed-out user posting from a
    # stale page was bounced to /login/?next=/logout/ and, after signing back
    # in, landed on a GET of this POST-only view (405).
    path("logout/", login_not_required(LogoutView.as_view(next_page="login")), name="logout"),
    # SHARE-001: signed, expiring, read-only document links for customers (no account).
    path("share/<str:token>/", login_not_required(printing_views.shared_document), name="share_document"),
    # S2: the customer approves or refuses a work order's quote from the same signed link (public, CSRF-protected).
    path("share/<str:token>/approve/", login_not_required(workorders_public.decide), {"action": "approve"}, name="share_work_order_approve"),
    path("share/<str:token>/refuse/", login_not_required(workorders_public.decide), {"action": "refuse"}, name="share_work_order_refuse"),
    # S4 (HG-070): the online payment link. Public, gated by a signed token; only the webhook is CSRF-exempt (HMAC-verified).
    path("pay/webhook/paymob/", login_not_required(payments_views.webhook), name="pay_webhook_paymob"),
    path("pay/return/paymob/", login_not_required(payments_views.pay_return), name="pay_return_paymob"),
    path("pay/mock/<str:reference>/", login_not_required(payments_views.mock_checkout), name="pay_mock_checkout"),
    path("pay/<str:token>/", login_not_required(payments_views.pay_page), name="pay_page"),
    path("pay/<str:token>/go/", login_not_required(payments_views.pay_go), name="pay_go"),
    path("pay/<str:token>/done/", login_not_required(payments_views.pay_done), name="pay_done"),
    # S3 (HG-077): the public online booking page of one entity (random key), and its signed confirmation. CSRF-protected,
    # throttled, 404 unless the «الحجز أونلاين» capability is on and the entity takes bookings.
    path("book/<str:key>/", login_not_required(booking_views.page), name="book_page"),
    path("book/<str:key>/done/<str:token>/", login_not_required(booking_views.done), name="book_done"),
    path("start/", after_login, name="after_login"),
    path("setup/", TemplateView.as_view(template_name="setup/setup_gate.html"), name="setup_gate"),
    path("setup/activity/", TemplateView.as_view(template_name="setup/activity_selection.html"), name="setup_activity"),
    path("setup/activity/commercial/", TemplateView.as_view(template_name="setup/activity_commercial_subactivity.html"), name="setup_activity_commercial"),
    path("setup/activity/restaurants/", TemplateView.as_view(template_name="setup/activity_restaurants_subactivity.html"), name="setup_activity_restaurants"),
    # ACT-002: these share one catalog-driven sub-activity step.
    path("setup/activity/medical/", sub_activity_step, {"activity": "medical"}, name="setup_activity_medical"),
    path("setup/activity/education/", sub_activity_step, {"activity": "education"}, name="setup_activity_education"),
    path("setup/activity/other/", sub_activity_step, {"activity": "other"}, name="setup_activity_other"),
    path("setup/activity/contracting/", sub_activity_step, {"activity": "contracting"}, name="setup_activity_contracting"),
    path("setup/activity/manufacturing/", sub_activity_step, {"activity": "manufacturing"}, name="setup_activity_manufacturing"),
    path("setup/activity/services/", TemplateView.as_view(template_name="setup/activity_services_subactivity.html"), name="setup_activity_services"),
    path("setup/activity/service/", TemplateView.as_view(template_name="setup/activity_subactivity_placeholder.html"), name="setup_activity_service"),
    path("setup/questions/", setup_questions, name="setup_questions"),
    path("setup/modules/", setup_modules, name="setup_modules"),
    path("setup/review/", setup_review, name="setup_review"),
    path("setup/complete/", setup_complete, name="setup_complete"),
    path("home/", home, name="home"),
    path("dashboard/", dashboard, name="dashboard_snapshot"),
    path("demo/activity/", demo_views.switch_activity, name="demo_activity"),
    path("demo/sample/", demo_views.sample, name="demo_sample"),
    path("demo/restart/", login_not_required(demo_views.restart), name="demo_restart"),
    # DEMO-FIRST-RUN: one tap on the kind of business opens a ready shop (config/demo_first_run.py).
    path("demo/", first_run.landing, name="demo_landing"),
    path("demo/start/", first_run.start, name="demo_start"),
    path("demo/preparing/", first_run.preparing, name="demo_preparing"),
    path("demo/begin/", first_run.begin, name="demo_begin"),
    path("demo/login-as/", first_run.login_as, name="demo_login_as"),
    path("demo/<slug:activity>/", first_run.sub_activities, name="demo_sub_activities"),
    path("demo/feedback/", include("feedback.urls")),
    path("master-data/", include("master_data.urls")),
    path("purchases/", include("purchases.urls")),
    path("inventory/", include("inventory.urls")),
    path("sales/", include("sales.urls")),
    path("cashboxes/", include("cashboxes.urls")),
    path("promotions/", include("promotions.urls")),
    path("loyalty/", include("loyalty.urls")),
    path("banks/", include("banking.urls")),
    path("cheques/", include("cheques.urls")),
    path("store/", include("onlinestore.urls")),
    path("shipping/", include("shipping.urls")),
    path("payments/", include("payments.urls")),  # S4 (HG-070): the staff side of payment links
    path("giftcards/", include("giftcards.urls")),  # S6 (HG-071): gift cards
    path("approvals/", include("approvals.urls")),  # FB-27
    path("ereceipt/", include("ereceipt.urls")),
    path("expenses/", include("expenses.urls")),
    path("print/", include("printing.urls")),
    path("barcode/", include("barcode.urls")),
    path("pricing/", include("pricing.urls")),
    path("taxes/", include("taxes.urls")),
    path("einvoice/", include("einvoice.urls")),
    path("units/", include("units.urls")),
    path("batches/", include("batches.urls")),
    path("variants/", include("variants.urls")),
    path("serials/", include("serials.urls")),
    path("instalments/", include("installments.urls")),
    path("assets/", include("fixed_assets.urls")),
    path("shifts/", include("shifts.urls")),
    path("parties/", include("parties.urls")),
    path("medical/", include("medical.urls")),
    path("education/", include("education.urls")),
    path("search/", include("search.urls")),
    path("imports/", include("imports.urls")),
    path("closing/", include("closing.urls")),
    path("entities/", include("entities.urls")),
    path("accounting/", include("ledger.urls")),
    # STAFF-001: employees & technicians.
    path("staff/", include("staff.urls")),
    # APPT-001: appointments & visits.
    path("appointments/", include("appointments.urls")),
    path("contracts/", include("agreements.urls")),
    # RESTO-001: tables, orders and the kitchen ticket.
    path("restaurant/", include("restaurant.urls")),
    path("work-orders/", include("workorders.urls")),
    path("attachments/", include("attachments.urls")),  # S1: photos, signature and files on a record
    path("orders/", include("orders.urls")),
    path("purchases/orders/", include("orders.purchase_urls")),  # T2
    path("pharmacy/", include("pharmacy.urls")),
    path("staff/attendance/", include("attendance.urls")),
    path("staff/payroll/", include("payroll.urls")),
    path("messages/", include("messaging.urls")),  # T9: the daily outbox of customer messages  # T11: under /staff/, so the employees module opens and closes it
    # CONTRACT-001: projects for contractors.
    path("projects/", include("projects.urls")),
    # MFG-001: recipes and production runs.
    path("manufacturing/", include("manufacturing.urls")),
    path("profile/", include("accounts.urls")),
    path("settings/", include("settings_core.urls")),
    path("reports/", report_hub, name="report_hub"),
    path("reports/", include("reports.urls")),
    path("status/", status_counts_report, name="status_counts_report"),
    path("ai/", include("ai.urls")),  # A1 (HG-079): the dashboard's voice summary
    path(settings.ADMIN_URL, django_admin.site.urls),
]


def _superuser_only(request):
    """ADMIN-001: shop owners and staff never reach Django Admin; only a superuser does.
    TRACK-START S04: and never on a showcase, where every tester's owner is a superuser of their own copy."""

    return request.user.is_active and request.user.is_superuser and not getattr(settings, "DEMO_MODE", False)


django_admin.site.has_permission = _superuser_only
