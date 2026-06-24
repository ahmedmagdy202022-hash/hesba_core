from django.contrib.auth.views import LoginView, LogoutView
from django.urls import reverse_lazy


class HesbaLoginView(LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        return reverse_lazy("dashboard_snapshot")


class HesbaLogoutView(LogoutView):
    next_page = reverse_lazy("login")
