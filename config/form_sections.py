"""AN3 (Ahmed's notes, 17 Oct 2026): forms read in a few short, titled parts instead of one long grid.

A form lists its parts in ``sections_spec``: ``(key, title_ar, title_en, hint_ar,
hint_en, field names)``. ``form.sections`` gives the visible fields in that
order (a field a part names but the form does not have is skipped), and any
field no part names lands in a last "other details" part, so nothing is ever
lost. Tick boxes are kept apart in ``form.checks``: they read as settings, at
the end. The shared template is ``partials/form_sections.html``.
"""

OTHER = {"ar": "تفاصيل أخرى", "en": "Other details"}


def _is_check(field):
    return getattr(field.field.widget, "input_type", "") == "checkbox"


class Sectioned:
    sections_spec = ()

    @property
    def sections(self):
        lang = "en" if getattr(self, "lang", "ar") == "en" else "ar"
        visible = [field for field in self.visible_fields() if not _is_check(field)]
        by_name = {field.name: field for field in visible}
        used, parts = set(), []
        for key, title_ar, title_en, hint_ar, hint_en, names in self.sections_spec:
            fields = [by_name[name] for name in names if name in by_name and name not in used]
            if not fields:
                continue
            used.update(field.name for field in fields)
            parts.append({"key": key, "title": title_en if lang == "en" else title_ar, "hint": hint_en if lang == "en" else hint_ar, "fields": fields})
        rest = [field for field in visible if field.name not in used]
        if rest:
            parts.append({"key": "other", "title": OTHER[lang] if parts else "", "hint": "", "fields": rest})
        return parts

    @property
    def checks(self):
        return [field for field in self.visible_fields() if _is_check(field)]
