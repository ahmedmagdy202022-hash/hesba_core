from django.http import HttpResponse


def home(request):
    return HttpResponse("Hesba Core dashboard is ready. Open /admin/ for details.")
