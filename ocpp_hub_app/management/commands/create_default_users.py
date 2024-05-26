from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.conf import settings

class Command(BaseCommand):
    help = 'Create default users if they do not exist'

    def handle(self, *args, **options):
        users = getattr(settings, 'DEFAULT_USERS', [])
        for user_data in users:
            username = user_data.get('username')
            email = user_data.get('email')
            password = user_data.get('password')
            is_superuser = user_data.get('is_superuser', False)

            if not User.objects.filter(username=username).exists():
                if is_superuser:
                    User.objects.create_superuser(username=username, email=email, password=password)
                    self.stdout.write(self.style.SUCCESS(f'Successfully created superuser {username}'))
                else:
                    User.objects.create_user(username=username, email=email, password=password)
                    self.stdout.write(self.style.SUCCESS(f'Successfully created user {username}'))
            else:
                self.stdout.write(self.style.WARNING(f'User {username} already exists'))
