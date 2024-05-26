import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ocpp_hub_project.settings')

application = get_wsgi_application()

# Import and run the custom management command
from django.core.management import call_command
call_command('create_default_users')
call_command('create_proxy_oauth2_app')