# validators.py
from rest_framework.exceptions import ValidationError
import re

def validate_websocket_url(value):
    if not re.match(r'^wss?://', value):
        raise ValidationError("Enter a valid WebSocket URL.")
