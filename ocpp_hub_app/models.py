import uuid
from django.db import models
from django.contrib.auth.models import User

class OAuthToken(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    access_token = models.CharField(max_length=255)
    refresh_token = models.CharField(max_length=255, null=True, blank=True)
    token_type = models.CharField(max_length=50)
    expires_in = models.IntegerField()
    scope = models.CharField(max_length=255)

    def __str__(self):
        return self.access_token
    
class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    email_verified = models.BooleanField(default=False)

    def __str__(self):
        return self.user.username

class ChargePoint(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, verbose_name="Charge Point Name")
    status = models.CharField(max_length=100, default="N/A", verbose_name="Status")
    connection_status = models.CharField(max_length=100, default="Disconnected", verbose_name="Connection")
    location = models.CharField(max_length=100, verbose_name="Location")
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    def __str__(self):
        return self.name

    def log_event(self, event_type, message):
        LogEntry.objects.create(
            chargepoint=self,
            event_type=event_type,
            message=message
        )

class Authorization(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chargepoint = models.ForeignKey(ChargePoint, on_delete=models.CASCADE)
    csms_name = models.CharField(max_length=100, verbose_name="CSMS Name")
    connect_url = models.CharField(max_length=100, verbose_name="Connection URL")
    cp_id = models.CharField(max_length=100, verbose_name="Charge Point ID")
    auth_key = models.CharField(max_length=100, verbose_name="Auth Key")
    sec_prof = models.CharField(max_length=100, verbose_name="Security Profile")
    connection_status = models.CharField(max_length=100, default="Disconnected", verbose_name="Connection")

    def __str__(self):
        return self.csms_name

    def log_event(self, event_type, message):
        LogEntry.objects.create(
            authorization=self,
            event_type=event_type,
            message=message
        )

class LogEntry(models.Model):
    chargepoint = models.ForeignKey(ChargePoint, on_delete=models.CASCADE, null=True, blank=True)
    authorization = models.ForeignKey(Authorization, on_delete=models.CASCADE, null=True, blank=True)
    event_type = models.CharField(max_length=50)
    message = models.TextField()
    raw_message = models.TextField(blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        if self.chargepoint:
            return f"[{self.timestamp}] ChargePoint {self.chargepoint.name}: {self.message}"
        if self.authorization:
            return f"[{self.timestamp}] Authorization {self.authorization.csms_name}: {self.message}"
        return f"[{self.timestamp}] Log: {self.message}"
