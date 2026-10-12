from django.urls import path
from . import views

urlpatterns = [path("oauth/callback/", views.callback, name="callback")]
