# utils.py
from oauth2_provider.models import Application
from django.db import IntegrityError

def register_client(name, client_id, client_secret):
    try:
        app, created = Application.objects.get_or_create(
            client_id=client_id,
            defaults={
                'name': name,
                'client_secret': client_secret,
                'client_type': Application.CLIENT_CONFIDENTIAL,
                'authorization_grant_type': Application.GRANT_CLIENT_CREDENTIALS,
                'redirect_uris': "",
                'skip_authorization': True,
            }
        )
        if created:
            print(f"Client '{name}' registered successfully.")
        else:
            print(f"Client '{name}' already exists.")
    except IntegrityError as e:
        print(f"Error registering client '{name}': {e}")

import string
import random

def generate_auth_key(length=32):
    characters = string.ascii_letters + string.digits + string.punctuation
    auth_key = ''.join(random.choice(characters) for _ in range(length))
    return auth_key