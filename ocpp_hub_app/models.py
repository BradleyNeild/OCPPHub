import uuid
from django.db import models
from django.contrib.auth.models import User
import json
from django.db import transaction
from django.core.exceptions import ValidationError  # Add this import
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
    is_primary = models.BooleanField(default=False, verbose_name="Primary Authorization")

    def __str__(self):
        return self.csms_name

    def log_event(self, event_type, message):
        LogEntry.objects.create(
            authorization=self,
            event_type=event_type,
            message=message
        )

    def set_as_primary(self):
        with transaction.atomic():
            # Set all other authorizations for this chargepoint to non-primary
            Authorization.objects.filter(chargepoint=self.chargepoint).update(is_primary=False)
            # Set this authorization as primary
            self.is_primary = True
            self.save()
        
        # Log the event
        LogEntry.objects.create(
            authorization=self,
            chargepoint=self.chargepoint,
            event_type='Authorization Change',
            message=f'Set as primary authorization for {self.chargepoint.name}',
            from_entity={'name': self.csms_name, 'type': 'CSMS', 'uuid': str(self.uuid)},
            to_entity={'name': self.chargepoint.name, 'type': 'Chargepoint', 'uuid': str(self.chargepoint.uuid)}
        )

from django.db import models

class LogEntry(models.Model):
    LEVEL_CHOICES = [
        ('INFO', 'Info'),
        ('WARNING', 'Warning'),
        ('ERROR', 'Error'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default='INFO', db_index=True)
    event_type = models.CharField(max_length=50, db_index=True)
    action = models.CharField(max_length=200, default='', blank=True)  # Make it optional
    from_entity = models.JSONField(default=dict, blank=True)  # Make it optional
    to_entity = models.JSONField(default=dict, blank=True)  # Make it optional
    message = models.TextField()
    raw_message = models.TextField(null=True, blank=True)
    
    chargepoint = models.ForeignKey('ChargePoint', on_delete=models.CASCADE, null=True, blank=True)
    authorization = models.ForeignKey('Authorization', on_delete=models.CASCADE, null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['timestamp', 'level', 'event_type']),
        ]

    def __str__(self):
        return f"[{self.timestamp}] {self.level} - {self.event_type}: {self.action}"

    def set_from_entity(self, name, entity_type, uuid=None):
        self.from_entity = {"name": name, "type": entity_type, "uuid": str(uuid) if uuid else None}

    def set_to_entity(self, name, entity_type, uuid=None):
        self.to_entity = {"name": name, "type": entity_type, "uuid": str(uuid) if uuid else None}

    def get_from_entity(self):
        return self.from_entity

    def get_to_entity(self):
        return self.to_entity

    def set_raw_message(self, data):
        if isinstance(data, str):
            self.raw_message = data
        else:
            self.raw_message = json.dumps(data, indent=2)

    def get_raw_message(self):
        return self.raw_message

    def clean(self):
        super().clean()
        if not isinstance(self.from_entity, dict):
            raise ValidationError("From entity must be a dictionary")
        if not isinstance(self.to_entity, dict):
            raise ValidationError("To entity must be a dictionary")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)