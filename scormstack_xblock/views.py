from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.utils.html import format_html
from django.views.decorators.http import require_GET

from . import oauth
from .config import ScormStackError


@login_required
@require_GET
def callback(request):
    try:
        if request.GET.get("error"):
            raise ScormStackError("Connection cancelled. You can close this window.")
        oauth.complete(request.user.pk, request.GET.get("state", ""),
                       request.GET.get("code", ""), request.GET.get("iss", ""))
        message = "ScormStack connected. Close this window and refresh the connection in Studio."
        status = 200
    except ScormStackError as exc:
        message, status = str(exc), 400
    response = HttpResponse(format_html("<!doctype html><html><head><meta name='referrer' content='no-referrer'><title>ScormStack</title></head><body><h1>ScormStack</h1><p>{}</p></body></html>", message), status=status)
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    response["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    return response
