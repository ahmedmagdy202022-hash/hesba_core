from django.contrib.auth.views import LoginView, LogoutView
from django.urls import reverse, reverse_lazy


class HesbaLoginView(LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def form_valid(self, form):
        lang = self.request.POST.get("hesba_lang") or self.request.GET.get("lang") or "ar"
        self.request.session["hesba_lang"] = "en" if lang == "en" else "ar"
        return super().form_valid(form)

    def get_success_url(self):
        lang = self.request.session.get("hesba_lang") or self.request.POST.get("hesba_lang") or self.request.GET.get("lang") or "ar"
        lang = "en" if lang == "en" else "ar"
        return f"{reverse('dashboard_snapshot')}?lang={lang}"


class HesbaLogoutView(LogoutView):
    next_page = reverse_lazy("login")