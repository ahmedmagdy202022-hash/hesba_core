from django.core.checks import Tags, register


@register(Tags.models)
def sector_module_checks(app_configs, **kwargs):
    return []
