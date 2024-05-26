# ocpp_hub_app/middleware.py

from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin
import traceback

class CustomExceptionMiddleware(MiddlewareMixin):
    def process_exception(self, request, exception):
        response_data = {
            'error': 'Internal Server Error',
            'message': str(exception),
            'traceback': traceback.format_exc()
        }
        return JsonResponse(response_data, status=500)

from django.shortcuts import redirect
from allauth.account.models import EmailAddress

class EmailVerificationRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if hasattr(request, 'user') and request.user.is_authenticated:
            email_address = EmailAddress.objects.filter(user=request.user, primary=True).first()
            if email_address and not email_address.verified:
                return redirect('account_email_verification_sent')
        return self.get_response(request)
    
from oauth2_provider.oauth2_backends import OAuthLibCore

class CustomOAuth2Middleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.oauth2_backend = OAuthLibCore()

    def __call__(self, request):
        # Validate token and attach it to the request
        valid, request = self.oauth2_backend.validate_request(request)
        if valid:
            token = request.auth
            request.is_proxy = token and 'proxy_access' in token.scope
        response = self.get_response(request)
        return response