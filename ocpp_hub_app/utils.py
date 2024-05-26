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

# Register clients (example usage)
register_client(name='Tesla', client_id='tesla-client-id', client_secret='tesla-client-secret')
register_client(name='PG&E', client_id='pge-client-id', client_secret='pge-client-secret')
