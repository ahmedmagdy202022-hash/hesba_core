from django.contrib import admin

from .models import ClosingRun, Period, PeriodSummary, PostClosingAdjustment


@admin.register(Period)
class PeriodAdmin(admin.ModelAdmin):
    list_display = ("period_code", "name", "frequency", "start_date", "end_date", "status", "closed_at")
    search_fields = ("period_code", "name")
    list_filter = ("frequency", "status", "start_date", "end_date")
    autocomplete_fields = ("closed_by", "reopened_by")


@admin.register(ClosingRun)
class ClosingRunAdmin(admin.ModelAdmin):
    list_display = ("period", "run_number", "status", "started_at", "completed_at", "created_by")
    search_fields = ("period__period_code", "period__name", "reason")
    list_filter = ("status", "started_at", "completed_at")
    autocomplete_fields = ("period", "created_by")


@admin.register(PeriodSummary)
class PeriodSummaryAdmin(admin.ModelAdmin):
    """Saved summaries, one set per closing run.

    A reopened period keeps the summaries of every run it went through, so the
    run has to be on the row: without it the list shows one apparently
    duplicated set per re-closing, with nothing to tell the sets apart.
    """

    list_display = ("period", "run_number", "summary_code", "summary_name", "amount", "quantity", "created_at")
    search_fields = ("period__period_code", "summary_code", "summary_name")
    list_filter = ("summary_code", "created_at")
    autocomplete_fields = ("period", "closing_run")
    readonly_fields = ("created_at",)

    @admin.display(description="Run", ordering="closing_run__run_number")
    def run_number(self, obj):
        return obj.closing_run.run_number


@admin.register(PostClosingAdjustment)
class PostClosingAdjustmentAdmin(admin.ModelAdmin):
    list_display = ("adjustment_number", "related_closed_period", "adjustment_date", "status", "created_by")
    search_fields = ("adjustment_number", "related_closed_period__period_code", "reason")
    list_filter = ("status", "adjustment_date")
    autocomplete_fields = ("related_closed_period", "created_by")
