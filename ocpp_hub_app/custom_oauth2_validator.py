# custom_oauth2_validator.py
from oauth2_provider.oauth2_validators import OAuth2Validator
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

OAUTH2_PROXY_CLIENT_ID = os.getenv('OAUTH2_PROXY_CLIENT_ID')

class CustomOAuth2Validator(OAuth2Validator):
    def validate_scopes(self, request, scopes, client, *args, **kwargs):
        if 'proxy_access' in scopes and client.client_id != OAUTH2_PROXY_CLIENT_ID:
            raise ValueError("Invalid scope: Only the proxy application can request 'proxy_access' scope")
        return super().validate_scopes(request, scopes, client, *args, **kwargs)
