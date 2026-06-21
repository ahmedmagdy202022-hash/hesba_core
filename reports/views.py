from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import render

from reports.services import get_local_controlled_cycle_snapshot


def home(request):
    snapshot = None
    snapshot_ready = False
    error_message = ""

    try:
        snapshot = get_local_controlled_cycle_snapshot()
        snapshot_ready = True
    except ObjectDoesNotExist:
        error_message = "البيانات التجريبية لم تكتمل بعد. شغل seed_dev_master_data ثم controlled_cycle_smoke_test."

    return render(
        request,
        "reports/home.html",
        {
            "snapshot": snapshot,
            "snapshot_ready": snapshot_ready,
            "error_message": error_message,
        },
    )
