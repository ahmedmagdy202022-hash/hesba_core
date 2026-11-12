"""Contracting C2a / C2b (HG-095): who sees and who changes a project's documents and daily site reports.

One rule, the project's own: whoever may open the project screens (``projects.view_projects``, the site engineer's, or
``sales.view_sales_invoices``, the office's) sees the documents and the reports; whoever may record progress on it
(``projects.record_progress`` or ``sales.create_sales_invoice``, as the certificate screens' ``can_record``) adds and edits;
deleting a document needs ``sales.create_sales_invoice`` (the office). No permission code is new.

A project has no entity of its own (its first money document fixes one), and the project page is not narrowed to an entity
today, so neither are its documents and reports (``group_wide`` in the attachments registry). What belongs to the entity is
the money, and nothing here touches it.
"""

from django.core.exceptions import PermissionDenied

from permissions.services import user_has_permission

VIEW_PROJECTS, VIEW_SALES = "projects.view_projects", "sales.view_sales_invoices"
RECORD, MANAGE = "projects.record_progress", "sales.create_sales_invoice"


def can_view(user):
    return user_has_permission(user, VIEW_PROJECTS) or user_has_permission(user, VIEW_SALES)


def can_write(user):
    """Add documents, write and edit a daily report, add and delete its photos."""

    return user_has_permission(user, RECORD) or user_has_permission(user, MANAGE)


def can_delete_documents(user):
    """The office removes a project's documents; a site engineer adds but does not delete (a contract is the record)."""

    return user_has_permission(user, MANAGE)


def module_open():
    """The Projects module is on (the ``/attachments/`` URLs sit outside the module gate of ``/projects/``)."""

    from settings_core.module_gate import closed_module

    return closed_module("/projects/") is None


def visible_projects(user):
    from .models import Project

    if not can_view(user):
        raise PermissionDenied("Projects need projects.view_projects or sales.view_sales_invoices.")
    return Project.objects.all() if module_open() else Project.objects.none()

