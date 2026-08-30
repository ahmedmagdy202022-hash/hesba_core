class ViewOnlyAdminMixin:
    """Allow admin inspection without permitting service-owned writes."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ProtectedStatusAdminMixin:
    """Make posted/cancelled objects immutable through Django Admin."""

    protected_statuses = frozenset({"posted", "cancelled"})

    def is_status_protected(self, obj):
        return obj is not None and getattr(obj, "status", None) in self.protected_statuses

    def has_change_permission(self, request, obj=None):
        if self.is_status_protected(obj):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        # Disabling model-level deletion also removes Django's bulk-delete
        # action, which otherwise has no object available for the status check.
        return False


class ParentStatusProtectedAdminMixin:
    """Protect a child row whenever its owning transaction is immutable."""

    parent_status_field = "invoice"
    protected_statuses = frozenset({"posted", "cancelled"})

    def is_parent_status_protected(self, obj):
        if obj is None:
            return False
        parent = getattr(obj, self.parent_status_field)
        return getattr(parent, "status", None) in self.protected_statuses

    def has_add_permission(self, request):
        # Lines must be created through the parent draft form/service, never by
        # selecting an arbitrary (possibly posted) invoice in a standalone form.
        return False

    def has_change_permission(self, request, obj=None):
        if self.is_parent_status_protected(obj):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        # Standalone bulk deletion cannot safely inspect every parent status.
        # Draft line removal remains available through the guarded inline.
        return False
