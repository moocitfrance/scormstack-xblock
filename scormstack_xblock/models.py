from django.conf import settings
from django.db import models


class Connection(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    origin = models.CharField(max_length=255)
    client_id = models.CharField(max_length=255)
    access_token = models.TextField()
    refresh_token = models.TextField()
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "origin"], name="scormstack_connection_user_origin")]


class Authorization(models.Model):
    state_hash = models.CharField(max_length=64, primary_key=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    origin = models.CharField(max_length=255)
    client_id = models.CharField(max_length=255)
    verifier = models.TextField()
    redirect_uri = models.TextField()
    expires_at = models.DateTimeField()


class Attempt(models.Model):
    """Each published package keeps its own learner state; updates never delete it."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    usage_id = models.CharField(max_length=255)
    revision = models.CharField(max_length=64)
    data = models.JSONField(default=dict)
    session = models.CharField(max_length=64, blank=True)
    sequence = models.PositiveIntegerField(default=0)
    total_seconds = models.FloatField(default=0)
    session_seconds = models.FloatField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "usage_id", "revision"], name="scormstack_attempt_identity")]
