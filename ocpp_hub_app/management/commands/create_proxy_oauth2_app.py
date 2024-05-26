# management/commands/create_proxy_oauth2_app.py
from django.core.management.base import BaseCommand
from oauth2_provider.models import Application
from django.conf import settings
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

OAUTH2_PROXY_CLIENT_ID = os.getenv('OAUTH2_PROXY_CLIENT_ID')
OAUTH2_PROXY_CLIENT_SECRET = os.getenv('OAUTH2_PROXY_CLIENT_SECRET')

class Command(BaseCommand):
    help = 'Create an OAuth2 application for the proxy'

    def handle(self, *args, **options):
        client_id = OAUTH2_PROXY_CLIENT_ID
        client_secret = OAUTH2_PROXY_CLIENT_SECRET

        if not Application.objects.filter(client_id=client_id).exists():
            Application.objects.create(
                name='Proxy Application',
                client_id=client_id,
                client_secret=client_secret,
                client_type=Application.CLIENT_CONFIDENTIAL,
                authorization_grant_type=Application.GRANT_CLIENT_CREDENTIALS,
                redirect_uris='',  # Client credentials flow doesn't use redirect URIs
            )
            self.stdout.write(self.style.SUCCESS(f'Successfully created proxy OAuth application with client_id: {client_id}'))
        else:
            self.stdout.write(self.style.WARNING(f'Proxy OAuth application with client_id: {client_id} already exists'))
