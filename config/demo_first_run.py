"""DEMO-FIRST-RUN: one tap on the kind of business, and a ready shop opens.

The public demo used to start with a login wall, a "start over" confirmation,
the setup wizard's questions and a wait while sample data was seeded. Now an
anonymous visit to ``/`` shows the activity tiles (D1); an activity with more
than one demo variant opens its sub-activities (D2); the choice POSTs to
``/demo/start/``, which copies the pre-built template of that business into
the visitor's private sandbox, signs them in as the owner and lands on the
dashboard. «ابدأ فاضي» copies the emptied demo instead and lands on «ابدأ من
هنا» (D4). Everything here is demo only (404 otherwise); a real install keeps
its login page and wizard untouched.
"""

import functools
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_not_required
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import demo as showcase
from . import demo_templates as templates


def _demo_only():
    if not getattr(settings, "DEMO_MODE", False):
        raise Http404("Demo only.")


def demo_view(view):
    """404 off a showcase before anything else is checked (a GET to a POST-only demo URL is a 404, not a 405)."""

    @functools.wraps(view)
    def wrapped(request, *args, **kwargs):
        _demo_only()
        return view(request, *args, **kwargs)

    return wrapped


def _lang(request):
    data = request.POST if request.method == "POST" else request.GET
    return "en" if data.get("lang") == "en" else "ar"


def _dir(lang):
    return "ltr" if lang == "en" else "rtl"


def _label(activity, sub_activity, lang):
    from settings_core import setup_catalog as catalog

    return catalog.sub_activity_label(activity, sub_activity, lang) if sub_activity else catalog.activity_label(activity, lang)


def _noun(activity, sub_activity, lang):
    """The business inside a sentence: the Arabic label reads well; in English, "pharmacy", not "Pharmacy" or "General building"."""

    if lang == "en":
        return showcase.DEMO_NOUNS_EN.get((activity, sub_activity)) or _label(activity, sub_activity, lang).lower()
    return _label(activity, sub_activity, lang)


def tiles(lang):
    """D1: the landing tiles. An activity with one demo variant starts straight away (``start`` set)."""

    from settings_core import setup_catalog as catalog

    rows = []
    for activity, hint_ar, hint_en in showcase.DEMO_TILES:
        # The approved activity icons of the setup gate (static/hesba/setup_gate/icons), named a little differently.
        icon = f"hesba/setup_gate/icons/activity_{ {'services': 'service', 'restaurants': 'restaurant'}.get(activity, activity) }_icon.png"
        if activity == "other":
            default = showcase.DEMO_DEFAULT
            rows.append({"key": "other", "label": "نشاط تاني" if lang == "ar" else "Something else", "hint": hint_ar if lang == "ar" else hint_en,
                         "icon": icon, "start": default, "href": ""})
            continue
        subs = showcase.demo_subs(activity)
        title = catalog.activity_label(activity, lang)
        # Short tile titles: the landing says "تجاري", not "نشاط تجاري".
        short = {"commercial": ("تجاري", "Commercial"), "services": ("خدمي", "Services"), "medical": ("طبي", "Medical"),
                 "education": ("تعليمي", "Education"), "restaurants": ("مطاعم وكافيهات", "Restaurants & cafés"),
                 "contracting": ("مقاولات", "Contracting"), "manufacturing": ("تصنيعي", "Manufacturing")}.get(activity)
        if short:
            title = short[0 if lang == "ar" else 1]
        rows.append({"key": activity, "label": title, "hint": hint_ar if lang == "ar" else hint_en, "icon": icon,
                     "start": (activity, subs[0]) if len(subs) == 1 else None,
                     "href": f"{reverse('demo_sub_activities', args=[activity])}?lang={lang}" if len(subs) > 1 else ""})
    return rows


WORDS = {
    "ar": {
        "page_title": "جرّب حِسبة على نشاطك - حِسبة", "title": "جرّب حِسبة على نشاطك",
        "lead": "اختار نوع شغلك، وهتلاقي قدامك نشاط جاهز وشغال ببياناته. نسختك لوحدك، ومفيش تسجيل.",
        "foot": "كل الأرقام تجريبية. اللي تعمله هنا مش بيأثر على حد.", "have_account": "عندك حساب في حِسبة؟ ادخل من هنا",
        "language": "English", "sub_title": "{activity}، إيه نوعه؟", "back": "رجوع", "not_sure": "مش متأكد؟ افتح {default}",
        "change_later": "تقدر تغيّر النشاط في أي وقت من شريط الديمو.",
        "preparing_title": "بنجهّز نسختك…", "preparing": "أول مرة حد يفتح {label} على السيرفر ده؛ بنجهّز نسخته، وده بياخد لحظات. الصفحة هتتحدث لوحدها.",
        "ready": "نسختك جاهزة.", "open": "افتح {label}", "too_many": "عدد المحاولات كبير خلال الساعة دي. جرّب تاني بعد شوية، أو كمّل في تجربتك الحالية.",
        "failed_title": "معرفناش نجهّز النسخة دي", "failed": "حصلت مشكلة وإحنا بنجهّز {label}. جرّب النسخة الجاهزة، أو اختار نشاط تاني.",
        "default_shop": "افتح نسخة التجزئة الجاهزة", "choose_other": "اختار نشاط تاني", "opened": "النسخة دي اتفتحت قبل كده.",
        "to_dashboard": "افتح لوحة التحكم", "not_built": "الديمو الفاضي لسه بيتجهّز. جرّب كمان دقيقة.",
    },
    "en": {
        "page_title": "Try Hesba on your business - Hesba", "title": "Try Hesba on your business",
        "lead": "Pick your kind of business and a working shop opens, already filled in. Your own copy, no sign-up.",
        "foot": "All figures are samples. Nothing you do here affects anyone.", "have_account": "Have a Hesba account? Sign in here",
        "language": "العربية", "sub_title": "{activity}: which kind?", "back": "Back", "not_sure": "Not sure? Open a {default}",
        "change_later": "You can change the activity any time from the demo bar.",
        "preparing_title": "Preparing your copy…", "preparing": "Nobody has opened a {label} on this server yet; its copy is being prepared, which takes a moment. This page refreshes by itself.",
        "ready": "Your copy is ready.", "open": "Open the {label}", "too_many": "Too many fresh starts from here this hour. Please wait a little, or carry on in your current demo.",
        "failed_title": "We could not prepare this copy", "failed": "Something went wrong while preparing the {label}. Try the ready-made shop, or pick another business.",
        "default_shop": "Open the ready-made retail shop", "choose_other": "Pick another business", "opened": "This copy was opened already.",
        "to_dashboard": "Open the dashboard", "not_built": "The empty demo is still being prepared. Please try again in a minute.",
    },
}


def _base_context(request, lang):
    return {"lang": lang, "dir": _dir(lang), "other_lang": "ar" if lang == "en" else "en", "words": WORDS[lang],
            "start_url": reverse("demo_start"), "login_url": f"{reverse('login')}?lang={lang}", "landing_url": f"{reverse('demo_landing')}?lang={lang}"}


@login_not_required
def landing(request):
    """D1: the activity tiles. Anonymous or signed in (the demo bar's «غيّر النشاط» comes here too)."""

    _demo_only()
    lang = _lang(request)
    return render(request, "demo/landing.html", {**_base_context(request, lang), "tiles": tiles(lang)})


@login_not_required
def sub_activities(request, activity):
    """D2: the sub-activities of one activity, each a one-tap start."""

    _demo_only()
    subs = showcase.demo_subs(activity)
    if len(subs) < 2:
        raise Http404("No choice to make here.")
    lang = _lang(request)
    from settings_core import setup_catalog as catalog

    rows = [{"slug": sub, "label": catalog.sub_activity_label(activity, sub, lang),
             "hint": showcase.DEMO_HINTS.get((activity, sub), ("", ""))[1 if lang == "en" else 0]} for sub in subs]
    default = showcase.DEMO_DEFAULT if activity == showcase.DEMO_DEFAULT[0] else (activity, subs[0])
    words = WORDS[lang]
    return render(request, "demo/sub_activities.html", {
        **_base_context(request, lang), "activity": activity, "rows": rows,
        "title": words["sub_title"].format(activity=catalog.activity_label(activity, lang)),
        "default": default, "not_sure": words["not_sure"].format(default=_noun(*default, lang)),
    })


def _choice(request):
    activity = request.POST.get("activity", "")
    sub = request.POST.get("sub_activity", "")
    if not templates.is_known(activity, sub):
        return None
    return activity, sub


def _owner():
    return get_user_model().objects.get(username="owner")


def _sign_in(request, user):
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")


#: DEMO-FIRST-RUN: the preparing page opens the shop by POSTing to ``start`` on its own. A Back to that page
#: (or a replay of its POST) must not do it again and silently replace the shop the visitor has been working in,
#: so the auto-POST carries a one-shot token: set in this signed cookie when ``start`` sends the visitor to wait,
#: spent by the ``start`` that opens the shop. A cookie, not the session: the waiting visitor usually has no copy
#: yet (the session table they see is the read-only template), and ``start`` flushes the session anyway.
PREPARE_COOKIE = "hesba_demo_prepare"
PREPARE_SALT = "hesba.demo-prepare"
PREPARE_MAX_AGE = 3600


def _prepare_token(request, activity, sub):
    """This visitor's unspent token for opening (activity, sub) from the preparing page, or ""."""

    value = request.get_signed_cookie(PREPARE_COOKIE, default="", salt=PREPARE_SALT, max_age=PREPARE_MAX_AGE)
    cookie_activity, cookie_sub, nonce = (value.split(":") + ["", "", ""])[:3]
    return nonce if (cookie_activity, cookie_sub) == (activity, sub) else ""


def _where_they_were(request, lang):
    """Where a spent (or forged) preparing replay goes instead of replacing anything."""

    if request.user.is_authenticated:
        return redirect(f"{reverse('dashboard_snapshot')}?lang={lang}")
    return redirect(f"{reverse('demo_landing')}?lang={lang}")


def _replace_in_place(activity, sub, empty):
    """Empty this database and open the chosen shop in it: what a private copy of a ready shop gives. Returns
    whether the shop is left empty. Only ever run on a database with no demo in it (nothing to wipe)."""

    from .demo_restart import restart_demo

    restart_demo()
    templates.complete_demo_setup(activity, sub)
    if not empty:
        templates.seed_sample_business()
    return empty


def _start_shared(activity, sub, empty):
    """No private copies (DEMO_SANDBOXES off, or PostgreSQL): one demo everyone shares, so a tap never wipes it.

    The setup is switched in place, as the old «غيّر النشاط» did, and a shop with no trade gets its sample month;
    what other testers entered stays. Only a database with no demo in it yet is set up from scratch. Returns
    whether the shop is left empty (then the visitor lands on «ابدأ من هنا»)."""

    from sales.models import SalesInvoice
    from settings_core.models import ClientProfile

    if ClientProfile.get_active() is None or not get_user_model().objects.filter(username="owner").exists():
        return _replace_in_place(activity, sub, empty)
    templates.complete_demo_setup(activity, sub)
    if not empty:
        templates.seed_sample_business()
    return not SalesInvoice.objects.filter(status="posted").exists()


@login_not_required
@demo_view
@require_POST
def start(request):
    """POST activity, sub_activity, mode (sample|empty): open that ready shop for this visitor, signed in as the owner.

    With private copies (DEMO-SANDBOX) the visitor's copy is replaced by a copy of the ready template: a file copy,
    well under a second. A missing template is built in the background behind a refreshing page. Without private
    copies the one shared demo is switched in place and never wiped (``_start_shared``)."""

    lang = _lang(request)
    choice = _choice(request)
    if choice is None:
        return HttpResponse("Unknown demo activity.", status=400)
    activity, sub = choice
    if "prepared" in request.POST:
        # The preparing page's own POST: only with this visitor's unspent token for this shop (see PREPARE_COOKIE).
        token = _prepare_token(request, activity, sub)
        if not token or not constant_time_compare(token, request.POST.get("prepared", "")):
            return _where_they_were(request, lang)
    empty = request.POST.get("mode") == "empty"
    switch = getattr(request, "demo_switch", None)
    if switch is not None:
        from .demo_sandbox import TOO_MANY, TooManyFreshStarts

        source = templates.fresh_template() if empty else templates.populated_template(activity, sub)
        if not source.is_file():
            if empty:
                return HttpResponse(WORDS[lang]["not_built"], status=503, content_type="text/plain; charset=utf-8")
            templates.builder.request(activity, sub)
            response = redirect(f"{reverse('demo_preparing')}?lang={lang}&activity={activity}&sub_activity={sub}")
            response.set_signed_cookie(PREPARE_COOKIE, f"{activity}:{sub}:{secrets.token_urlsafe(16)}", salt=PREPARE_SALT,
                                       max_age=PREPARE_MAX_AGE, httponly=True, samesite="Lax", secure=not settings.DEBUG)
            return response
        try:
            switch(source)
        except TooManyFreshStarts:
            return HttpResponse(TOO_MANY, status=429)
        if empty:
            templates.complete_demo_setup(activity, sub)
    else:
        empty = _start_shared(activity, sub, empty)
    # The visitor's session row lived in the copy that was just replaced (or flushed). A signed-in owner switching
    # activity is the same user id, so login() would keep that key and the save at the end would find no row
    # (SessionInterrupted, a 400). Start a fresh session in the new copy instead.
    request.session.flush()
    _sign_in(request, _owner())
    if empty:
        response = redirect(f"{reverse('demo_begin')}?lang={lang}")
    else:
        response = redirect(f"{reverse('dashboard_snapshot')}?lang={lang}")
    if PREPARE_COOKIE in request.COOKIES:
        response.delete_cookie(PREPARE_COOKIE, samesite="Lax")  # spent: a Back to the preparing page opens nothing
    return response


def _private_copies():
    from .demo_sandbox import enabled

    return enabled()


@login_not_required
@never_cache
def preparing(request):
    """The short wait while a missing template is built: refreshes until the file exists, then opens it once.

    Stops refreshing, with a way out, when the build keeps failing or takes far too long. Only a demo with private
    copies ever sends anyone here; without them it is the landing."""

    _demo_only()
    import time

    lang = _lang(request)
    activity, sub = request.GET.get("activity", ""), request.GET.get("sub_activity", "")
    if not _private_copies() or not templates.is_known(activity, sub):
        return redirect(f"{reverse('demo_landing')}?lang={lang}")
    ready = templates.populated_template(activity, sub).is_file()
    failed, since = False, 0.0
    if not ready:
        try:
            since = float(request.GET.get("since", ""))
        except ValueError:
            since = 0.0
        failed = templates.builder.gave_up(activity, sub) or bool(since and time.time() - since > templates.MAX_WAIT_SECONDS)
        if not failed:
            templates.builder.request(activity, sub)
            failed = templates.builder.gave_up(activity, sub)
    words = WORDS[lang]
    label = _noun(activity, sub, lang)
    token = _prepare_token(request, activity, sub) if ready else ""
    if failed:
        message = words["failed"].format(label=label)
    elif ready:
        message = words["ready"] if token or not request.user.is_authenticated else words["opened"]
    else:
        message = words["preparing"].format(label=label)
    context = {**_base_context(request, lang), "activity": activity, "sub_activity": sub, "ready": ready, "failed": failed,
               "token": token, "signed_in": request.user.is_authenticated, "label": label, "message": message,
               "open": words["open"].format(label=label), "default": showcase.DEMO_DEFAULT,
               "dashboard_url": f"{reverse('dashboard_snapshot')}?lang={lang}",
               "refresh_url": f"{reverse('demo_preparing')}?lang={lang}&activity={activity}&sub_activity={sub}&since={int(since or time.time())}"
               if not ready and not failed else ""}
    response = render(request, "demo/preparing.html", context)
    if not ready and not failed:
        response["Refresh"] = f"3; url={context['refresh_url']}"
    return response


@demo_view
@require_POST
def login_as(request):
    """The demo bar's «ادخل كموظف (كاشير)» / «ارجع كمالك»: another demo login in the same copy."""

    lang = _lang(request)
    username = request.POST.get("username", "")
    if username not in ("owner", "cashier"):
        return HttpResponse("Unknown demo login.", status=400)
    user = get_user_model().objects.filter(username=username, is_active=True).first()
    if user is None:
        return HttpResponse("This demo has no such login.", status=404)
    _sign_in(request, user)
    return redirect(f"{reverse('after_login')}?lang={lang}")


BEGIN_WORDS = {
    "ar": {
        "page_title": "ابدأ من هنا - حِسبة", "title": "ابدأ من هنا", "lead": "أربع خطوات وتبقى بتبيع. كل خطوة دقيقة.",
        "empty_line": "لسه مفيش حركة. ابدأ من هنا.", "fill_title": "عايز تشوفه مليان الأول؟",
        "fill_lead": "نملاه لك بشهر شغل تجريبي لـ{label}، في أقل من ثانية.", "fill": "املاه ببيانات تجريبية",
        "details": "عايز تعدّل تفاصيل شغلك؟ من الإعدادات", "done": "تم",
    },
    "en": {
        "page_title": "Start here - Hesba", "title": "Start here", "lead": "Four steps and you are selling. A minute each.",
        "empty_line": "No activity yet. Start here.", "fill_title": "Want to see it full first?",
        "fill_lead": "We fill it with a sample month for a {label}, in under a second.", "fill": "Fill it with sample data",
        "details": "Want to adjust how your business works? Settings", "done": "Done",
    },
}

#: (key, title_ar, title_en, hint_ar, hint_en, url name, module it needs or "")
BEGIN_STEPS = (
    ("item", "ضيف أول صنف", "Add your first item", "اسم وسعر، والباقي اختياري", "A name and a price; the rest is optional", "master_data:item_create", "items_services"),
    ("cashbox", "سجّل رصيد الخزنة", "Set the cash in the drawer", "الكاش اللي معاك دلوقتي", "The cash you have right now", "cashboxes:list", "cashboxes"),
    ("purchase", "سجّل أول فاتورة شراء", "Record your first purchase", "علشان المخزن يبقى فيه بضاعة", "So there is stock to sell", "purchases:create", "purchases"),
    ("sale", "سجّل أول عملية بيع", "Make your first sale", "من الكاشير، والوردية هتتفتح لوحدها", "From the till; the shift opens by itself", "sales:pos", "sales_operations"),
)


def begin_steps(lang, modules):
    """D4's four steps, only those whose module this business has, with the ones already done marked."""

    from reports.dashboard_data import onboarding_progress
    from settings_core.capabilities import capability_enabled

    cashbox_done, parties_done, items_done, trade_done = onboarding_progress()
    done = {"item": items_done, "cashbox": cashbox_done, "purchase": trade_done, "sale": trade_done}
    rows = []
    for key, ar, en, hint_ar, hint_en, url_name, module in BEGIN_STEPS:
        if module and module not in modules:
            continue
        if key == "sale" and not capability_enabled("pos"):
            url_name = "sales:create"
        rows.append({"key": key, "title": ar if lang == "ar" else en, "hint": hint_ar if lang == "ar" else hint_en,
                     "url": f"{reverse(url_name)}?lang={lang}", "done": done[key]})
    return rows


@demo_view
def begin(request):
    """D4: «ابدأ من هنا» after «ابدأ فاضي»: four steps to the first sale, and the card that fills it instead."""

    lang = _lang(request)
    from settings_core.models import ClientProfile
    from settings_core.setup_services import usable_modules

    profile = ClientProfile.get_active()
    if profile is None or not profile.setup_is_complete:
        return redirect("after_login")
    words = BEGIN_WORDS[lang]
    label = _noun(profile.activity_slug, profile.sub_activity_slug, lang)
    return render(request, "demo/begin.html", {
        "lang": lang, "dir": _dir(lang), "words": words, "page_title": words["page_title"], "client_name": profile.display_name,
        "steps": begin_steps(lang, set(usable_modules())), "fill_lead": words["fill_lead"].format(label=label),
        "demo_bar": demo_bar(request, lang, profile, empty=True),
        "details_url": f"{reverse('setup_questions')}?lang={lang}&activity={profile.activity_slug}&sub_activity={profile.sub_activity_slug}",
    })


def demo_bar(request, lang, profile, empty=False):
    """D3's one-line demo bar: what this demo is, «غيّر النشاط», «ابدأ فاضي», «ملاحظاتك», and the other login.

    ``known`` (the empty / fill buttons, and «ابدأ من هنا»'s fill card) only for someone who may see the settings,
    the owner: a cashier trying permissions may switch back to the owner, not wipe or refill the shop."""

    from permissions.services import user_has_permission

    if not getattr(settings, "DEMO_MODE", False) or profile is None:
        return None
    activity, sub = profile.activity_slug or "", profile.sub_activity_slug or ""
    label = _label(activity, sub, lang) if activity else ""
    user = request.user
    other = "cashier" if user.get_username() == "owner" else "owner"
    other_exists = get_user_model().objects.filter(username=other, is_active=True).exists()
    return {
        "label": ("Demo · " if lang == "en" else "ديمو · ") + label + ((" · empty" if lang == "en" else " · فاضي") if empty else ""),
        "change_url": f"{reverse('demo_landing')}?lang={lang}", "start_url": reverse("demo_start"), "login_as_url": reverse("demo_login_as"),
        "activity": activity, "sub_activity": sub, "empty": empty,
        "known": templates.is_known(activity, sub) and user_has_permission(user, "settings.view_settings"),
        "other_user": other if other_exists else "",
        "words": {
            "change": "Change activity" if lang == "en" else "غيّر النشاط", "empty": "Start empty" if lang == "en" else "ابدأ فاضي",
            "fill": "Fill with sample data" if lang == "en" else "املاه ببيانات تجريبية",
            "feedback": "Your feedback" if lang == "en" else "ملاحظاتك",
            "other": ("Sign in as an employee (cashier)" if lang == "en" else "ادخل كموظف (كاشير)") if other == "cashier"
            else ("Back as the owner" if lang == "en" else "ارجع كمالك"),
            "more": "More" if lang == "en" else "المزيد",
        },
    }


def tour_card(request, lang, profile):
    """D3's «جرّب دلوقتي»: three steps in this trade's words, each a real screen; dismissed in the browser."""

    if not getattr(settings, "DEMO_MODE", False) or profile is None or not profile.activity_slug:
        return None
    # The tester's owner is a superuser of their own copy; a manager or cashier trying permissions sees the plain shop.
    if not request.user.is_superuser:
        return None
    from settings_core.capabilities import closed_capability
    from settings_core.module_gate import closed_module

    activity, sub = profile.activity_slug, profile.sub_activity_slug or ""
    steps = []
    for ar, en, hint_ar, hint_en, url_name in showcase.demo_tour(activity, sub):
        path = reverse(url_name)
        if closed_module(path) or closed_capability(path):  # a screen this business switched off is no step to take
            continue
        query = f"?lang={lang}"
        if url_name == "reports:daily":  # the summary opens on yesterday by default; «اقفل اليوم» means today's
            query += f"&date={timezone.localdate().isoformat()}"
        steps.append({"n": len(steps) + 1, "title": ar if lang == "ar" else en, "hint": hint_ar if lang == "ar" else hint_en, "url": f"{path}{query}"})
    if not steps:
        return None
    if lang == "en":
        lead = f"Your {_noun(activity, sub, lang)} has a full month of trade."
    else:
        lead = f"عندك شهر شغل كامل في {_label(activity, sub, lang)}."
    if _can_sell_now(request.user):
        lead += " The shift is open, so you can sell straight away." if lang == "en" else " الوردية مفتوحة، فتقدر تبيع على طول."
    return {
        "key": f"{activity}:{sub}", "steps": steps,
        "title": "Try it now" if lang == "en" else "جرّب دلوقتي",
        "lead": lead,
        "dismiss": "Hide" if lang == "en" else "إخفاء",
    }


def _can_sell_now(user):
    """Only say «الوردية مفتوحة، فتقدر تبيع على طول» where it is true: this business sells at a till and the shift is open."""

    from settings_core.capabilities import capability_enabled
    from settings_core.setup_services import usable_modules
    from shifts.services import open_shift_for

    if not capability_enabled("pos") or not {"cashboxes", "sales_operations"} <= set(usable_modules()):
        return False
    return open_shift_for(user) is not None
