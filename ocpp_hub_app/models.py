from django.db import models
from django.contrib.auth.models import User
import uuid
class OAuthToken(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    access_token = models.CharField(max_length=255)
    refresh_token = models.CharField(max_length=255, null=True, blank=True)  # Allow null and blank
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
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    status = models.CharField(max_length=100, default="N/A")
    location = models.CharField(max_length=100)
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    def __str__(self):
        return self.name

class Authorization(models.Model):
    chargepoint = models.ForeignKey(ChargePoint, on_delete=models.CASCADE)
    csms_name = models.CharField(max_length=100)
    status = models.CharField(max_length=100, default='Disconnected')
    connect_url = models.CharField(max_length=255)
    cp_id = models.CharField(max_length=100)
    auth_key = models.CharField(max_length=255)
    sec_prof = models.IntegerField()

    def __str__(self):
        return self.csms_name