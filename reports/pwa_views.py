from django.shortcuts import render


def home(request):
    return render(request, "reports/mobile_app_shell.html", {"checkpoint_code": "093_FOUNDATION_FIRST_UI_NAVIGATION_MAP", "page_title": "خريطة تشغيل أول شاشة UI"})


def dashboard_snapshot(request):
    return render(request, "reports/mobile_app_shell.html", {"checkpoint_code": "094_FOUNDATION_DASHBOARD_SNAPSHOT", "page_title": "Dashboard Snapshot قراءة فقط"})


def report_hub(request):
    return render(request, "reports/mobile_app_shell.html", {"checkpoint_code": "096_FOUNDATION_READ_ONLY_REPORT_HUB", "page_title": "مركز التقارير قراءة فقط"})
