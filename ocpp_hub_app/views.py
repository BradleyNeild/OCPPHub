from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse
from django.contrib.sites.shortcuts import get_current_site
from django.template.loader import render_to_string
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth import login, authenticate
from .permissions import IsProxyOrOwner
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.contrib.auth.models import User
from django.views.decorators.csrf import csrf_protect
from django.db import IntegrityError
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from oauth2_provider.decorators import protected_resource
from oauth2_provider.views.generic import ProtectedResourceView
from requests_oauthlib import OAuth2Session
from allauth.account.models import EmailAddress
import requests
import logging
from .models import OAuthToken, ChargePoint, Profile, Authorization
from .forms import UserRegistrationForm, ChargePointForm, AuthorizationForm
from .serializers import ChargePointSerializer, OCPPCredentialsSerializer
from .permissions import TokenHasScopeForMethod
from .decorators import method_scopes

logger = logging.getLogger(__name__)
from django.shortcuts import render, redirect
from allauth.account.forms import SignupForm
from allauth.account.models import EmailAddress
from allauth.account.utils import send_email_confirmation
import logging


@csrf_protect
def register(request):
    logger.warning("Accessing the register view")
    if request.method == 'POST':
        logger.warning("POST request received for registration")
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            logger.warning("Registration form is valid")
            new_user = form.save(commit=False)
            new_user.set_password(form.cleaned_data['password'])
            new_user.save()

            if not Profile.objects.filter(user=new_user).exists():
                try:
                    Profile.objects.create(user=new_user)
                    logger.warning(f"Profile created for user: {new_user.username}")
                except IntegrityError as e:
                    logger.error(f"Failed to create profile for user {new_user.username}: {str(e)}")

            logger.warning(f"User registered successfully: {new_user.username}")
            send_verification_email(request, new_user)

            backend = 'django.contrib.auth.backends.ModelBackend'
            new_user.backend = backend

            login(request, new_user)
            return render(request, 'account/verification_sent.html', {'email': new_user.email})
        else:
            logger.warning(f"Registration form is invalid: {form.errors}")
    else:
        logger.warning("GET request received for registration")
        form = UserRegistrationForm()
    return render(request, 'register.html', {'form': form})





def activate(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        logger.warning(f"Decoded UID: {uid}")
        user = User.objects.get(pk=uid)
        logger.warning(f"User found: {user.username}")
    except (TypeError, ValueError, OverflowError, User.DoesNotExist) as e:
        logger.error(f"Failed to decode UID or user does not exist: {str(e)}")
        user = None

    if user is not None and default_token_generator.check_token(user, token):
        logger.warning(f"Token is valid for user {user.username}")
        user.profile.email_verified = True
        user.profile.save()
        backend = 'django.contrib.auth.backends.ModelBackend'
        user.backend = backend
        login(request, user)
        return redirect('dashboard')
    else:
        logger.warning(f"Invalid activation link for UID: {uid} and token: {token}")
        return render(request, 'activation_invalid.html')

@login_required
def profile(request):
    if request.method == 'POST':
        # handle profile update logic
        pass
    return render(request, 'profile.html')

@login_required
def profile_view(request):
    email_address = EmailAddress.objects.filter(user=request.user, primary=True).first()
    is_verified = email_address.verified if email_address else False

    return render(request, 'profile.html', {
        'user': request.user,
        'email': email_address.email if email_address else '',
        'is_verified': is_verified,
    })

def landing(request):
    return render(request, 'landing.html')

@login_required
def dashboard(request):
    user = request.user
    chargepoints = ChargePoint.objects.filter(user=user)
    authorizations = Authorization.objects.filter(chargepoint__user=user)
    
    # Collect status information
    status_info = {
        'total_chargepoints': chargepoints.count(),
        'active_chargepoints': chargepoints.filter(status='Active').count(),
        'inactive_chargepoints': chargepoints.filter(status='Inactive').count(),
        'total_authorizations': authorizations.count(),
    }
    
    context = {
        'chargepoints': chargepoints,
        'authorizations': authorizations,
        'status_info': status_info,
    }
    
    return render(request, 'dashboard.html', context)
# OAuth Views
@login_required
def oauth_login(request):
    logger.warning("Starting OAuth login process")
    client_id = settings.OAUTH2_CLIENT_ID
    redirect_uri = request.build_absolute_uri('/oauth/callback/')
    oauth = OAuth2Session(client_id, redirect_uri=redirect_uri)
    authorization_url, state = oauth.authorization_url(settings.OAUTH2_AUTHORIZATION_URL)
    request.session['oauth_state'] = state
    logger.warning(f"Authorization URL: {authorization_url}")
    return redirect(authorization_url)

def oauth_callback(request):
    logger.warning("OAuth callback received")
    client_id = settings.OAUTH2_CLIENT_ID
    client_secret = settings.OAUTH2_CLIENT_SECRET
    redirect_uri = request.build_absolute_uri('/oauth/callback/')
    oauth = OAuth2Session(client_id, state=request.session['oauth_state'], redirect_uri=redirect_uri)
    token = oauth.fetch_token(settings.OAUTH2_TOKEN_URL, client_secret=client_secret, authorization_response=request.build_absolute_uri())
    logger.warning(f"Token received: {token}")
    user = request.user
    OAuthToken.objects.update_or_create(
        user=user,
        defaults={
            'access_token': token.get('access_token'),
            'refresh_token': token.get('refresh_token'),
            'token_type': token.get('token_type'),
            'expires_in': token.get('expires_in', 3600),
            'scope': token.get('scope', ''),
        },
    )
    return JsonResponse(token)

class TokenEndpoint(ProtectedResourceView):
    def get(self, request, *args, **kwargs):
        logger.warning(f"TokenEndpoint accessed by user: {request.user}")
        token = OAuthToken.objects.filter(user=request.user).first()
        if token:
            response_data = {
                'access_token': token.access_token,
                'refresh_token': token.refresh_token,
                'token_type': token.token_type,
                'expires_in': token.expires_in,
                'scope': token.scope,
            }
            logger.warning(f"Token data: {response_data}")
        else:
            response_data = {'error': 'No token found'}
            logger.warning("No token found for user")
        return JsonResponse(response_data)

class ApiEndpoint(ProtectedResourceView):
    def get(self, request, *args, **kwargs):
        logger.warning(f"ApiEndpoint accessed by user: {request.user}")
        return HttpResponse('Hello, OAuth2!')

from rest_framework import status as http_status

@api_view(['PATCH'])
@permission_classes([IsProxyOrOwner])
def update_chargepoint_status(request, id):
    chargepoint = get_object_or_404(ChargePoint, pk=id)
    new_status = request.data.get('status')

    valid_statuses = [
        'Available', 'Preparing', 'Charging', 'SuspendedEVSE', 'SuspendedEV',
        'Finishing', 'Reserved', 'Unavailable', 'Faulted', 'Occupied'
    ]

    if new_status not in valid_statuses:
        return Response({'error': 'Invalid status'}, status=http_status.HTTP_400_BAD_REQUEST)

    chargepoint.status = new_status
    chargepoint.save()

    return Response({'status': 'success'}, status=http_status.HTTP_200_OK)

@api_view(['PATCH'])
@permission_classes([IsProxyOrOwner])
def update_authorization_status(request, id):
    chargepoint = get_object_or_404(ChargePoint, pk=id)
    new_status = request.data.get('status')

    valid_statuses = ['Connected', 'Disconnected']

    if new_status not in valid_statuses:
        return Response({'error': 'Invalid status'}, status=http_status.HTTP_400_BAD_REQUEST)

    authorization = Authorization.objects.filter(chargepoint=chargepoint).first()
    if authorization:
        authorization.status = new_status
        authorization.save()
    else:
        return Response({'error': 'Authorization not found'}, status=http_status.HTTP_404_NOT_FOUND)

    return Response({'status': 'success'}, status=http_status.HTTP_200_OK)


# Charge Point Management
@login_required
@method_scopes({'GET': ['chargepoints:read']})
def chargepoint_list(request):
    chargepoints = ChargePoint.objects.filter(user=request.user)
    return render(request, 'chargepoint_list.html', {'chargepoints': chargepoints})

@login_required
@method_scopes({'POST': ['chargepoints:write']})
def chargepoint_create(request):
    if request.method == 'POST':
        form = ChargePointForm(request.POST)
        if form.is_valid():
            chargepoint = form.save(commit=False)
            chargepoint.user = request.user
            chargepoint.save()
            return redirect('chargepoint_list')
    else:
        form = ChargePointForm()
    return render(request, 'chargepoint_form.html', {'form': form})

@login_required
@method_scopes({'POST': ['chargepoints:write']})
def chargepoint_edit(request, pk):
    chargepoint = get_object_or_404(ChargePoint, pk=pk, user=request.user)
    if request.method == 'POST':
        form = ChargePointForm(request.POST, instance=chargepoint)
        if form.is_valid():
            form.save()
            return redirect('chargepoint_list')
    else:
        form = ChargePointForm(instance=chargepoint)
    return render(request, 'chargepoint_form.html', {'form': form})

@login_required
@method_scopes({'POST': ['chargepoints:write']})
def chargepoint_delete(request, pk):
    chargepoint = get_object_or_404(ChargePoint, pk=pk, user=request.user)
    if request.method == 'POST':
        chargepoint.delete()
        return redirect('chargepoint_list')
    return render(request, 'chargepoint_confirm_delete.html', {'chargepoint': chargepoint})

# Authorization Management
@login_required
@method_scopes({'GET': ['chargepoints:read']})
def authorization_list(request, pk):
    chargepoint = get_object_or_404(ChargePoint, pk=pk, user=request.user)
    authorizations = Authorization.objects.filter(chargepoint=chargepoint)
    return render(request, 'authorization_list.html', {'authorizations': authorizations, 'chargepoint': chargepoint})

@login_required
@method_scopes({'POST': ['chargepoints:write']})
def authorization_create(request, pk):
    chargepoint = get_object_or_404(ChargePoint, pk=pk, user=request.user)
    if request.method == 'POST':
        form = AuthorizationForm(request.POST)
        if form.is_valid():
            authorization = form.save(commit=False)
            authorization.chargepoint = chargepoint
            authorization.save()
            return redirect('authorization_list', pk=pk)
    else:
        form = AuthorizationForm()
    return render(request, 'authorization_form.html', {'form': form, 'chargepoint': chargepoint})

@login_required
@method_scopes({'POST': ['chargepoints:write']})
def authorization_edit(request, chargepoint_pk, auth_pk):
    chargepoint = get_object_or_404(ChargePoint, pk=chargepoint_pk, user=request.user)
    authorization = get_object_or_404(Authorization, pk=auth_pk, chargepoint=chargepoint)
    if request.method == 'POST':
        form = AuthorizationForm(request.POST, instance=authorization)
        if form.is_valid():
            form.save()
            return redirect('authorization_list', pk=chargepoint_pk)
    else:
        form = AuthorizationForm(instance=authorization)
    return render(request, 'authorization_form.html', {'form': form, 'chargepoint': chargepoint})

@login_required
@method_scopes({'POST': ['chargepoints:write']})
def authorization_delete(request, chargepoint_pk, auth_pk):
    chargepoint = get_object_or_404(ChargePoint, pk=chargepoint_pk, user=request.user)
    authorization = get_object_or_404(Authorization, pk=auth_pk, chargepoint=chargepoint)
    if request.method == 'POST':
        authorization.delete()
        return redirect('authorization_list', pk=chargepoint_pk)
    return render(request, 'authorization_confirm_delete.html', {'authorization': authorization, 'chargepoint': chargepoint})

# OCPP Configuration
@login_required
@method_scopes({'POST': ['ocpp:config']})
def setup_ocpp_configuration(request):
    logger.warning(f"setup_ocpp_configuration called with data: {request.data}")

    if not request.auth:
        logger.warning("No authentication found in the request")
        return Response({'error': 'No authentication found'}, status=403)

    token_scopes = request.auth.scope.split()
    logger.warning(f"Token scopes: {token_scopes}")

    if 'ocpp:config' not in token_scopes:
        logger.warning(f"Insufficient scope: ocpp:config. Token scopes: {token_scopes}")
        return Response({'error': 'Insufficient scope'}, status=403)

    user = request.user
    logger.warning(f"User: {user}")

    if not user.is_authenticated and 'client_credentials' in request.auth.token_type:
        oauth_token = request.auth
        user = None  # Proceed without a user context for client credentials
    else:
        oauth_token = get_object_or_404(OAuthToken, user=user)

    logger.warning(f"OAuthToken: {oauth_token}")
    charge_point_id = request.data.get('charge_point_id')
    config_data = request.data.get('config')

    if not charge_point_id or not config_data:
        logger.warning("Missing charge point ID or configuration data")
        return Response({'error': 'Charge point ID and configuration data are required'}, status=400)

    configuration_data = {
        'charge_point_id': charge_point_id,
        'config': config_data,
    }

    response = set_ocpp_configuration(oauth_token.access_token, configuration_data)
    logger.warning(f"Set OCPP configuration response: {response.content.decode()}")

    if response.status_code == 200:
        logger.warning("Configuration successful")
        return Response({'message': 'Configuration successful'})
    else:
        logger.warning(f"Configuration failed: {response.content.decode()}")
        return Response({'error': 'Configuration failed', 'details': response.content.decode()}, status=response.status_code)

def set_ocpp_configuration(access_token, configuration_data):
    csms_url = 'https://example-csms.com/api/set_ocpp_configuration'  # Replace with the actual CSMS URL
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
    }
    logger.warning(f"Setting OCPP configuration with data: {configuration_data}")
    try:
        response = requests.post(csms_url, json=configuration_data, headers=headers)
        response.raise_for_status()
        logger.warning("OCPP configuration set successfully")
        return response
    except requests.exceptions.RequestException as e:
        logger.error(f"Error setting OCPP configuration: {str(e)}")
        response = requests.Response()
        response.status_code = 500
        response._content = str(e).encode('utf-8')
        return response

# Meter Values
@api_view(['GET'])
@permission_classes([TokenHasScopeForMethod])
@method_scopes({'GET': ['ocpp:meter']})
def get_meter_values(request):
    logger.warning(f"get_meter_values called with query params: {request.query_params}")

    if not request.auth:
        logger.warning("No authentication found in the request")
        return Response({'error': 'No authentication found'}, status=403)

    token_scopes = request.auth.scope.split()
    if 'ocpp:meter' not in token_scopes:
        logger.warning(f"Insufficient scope: ocpp:meter. Token scopes: {request.auth.scope}")
        return Response({'error': 'Insufficient scope'}, status=403)

    user = request.user
    logger.warning(f"User: {user}")

    if not user.is_authenticated and 'client_credentials' in request.auth.token_type:
        pass  # Proceed without a user context for client credentials

    charge_point_id = request.query_params.get('charge_point_id')

    if not charge_point_id:
        logger.warning("Missing charge point ID")
        return Response({'error': 'Charge point ID is required'}, status=400)

    meter_values = fetch_meter_values_from_csms(charge_point_id)
    logger.warning(f"Meter values retrieved: {meter_values}")
    return Response({'meter_values': meter_values})

def fetch_meter_values_from_csms(charge_point_id):
    return [{"timestamp": "2023-05-16T08:00:00Z", "value": 10.5}, {"timestamp": "2023-05-16T09:00:00Z", "value": 12.3}]

# API Views for Charge Points
@api_view(['GET'])
@permission_classes([TokenHasScopeForMethod])
@method_scopes({'GET': ['chargepoints:read']})
def list_chargepoints(request):
    user = request.user
    chargepoints = ChargePoint.objects.filter(user=user)
    response_data = [
        {
            "id": cp.id,
            "name": cp.name,
            "status": cp.status,
            "location": cp.location
        }
        for cp in chargepoints
    ]
    return JsonResponse(response_data, safe=False)

@api_view(['GET'])
@permission_classes([IsProxyOrOwner])
def get_chargepoint_details(request, id):
    logger.info(f"Fetching details for ChargePoint ID: {id}")

    chargepoint = get_object_or_404(ChargePoint, pk=id)
    
    # Fetch the authorization details associated with this chargepoint
    authorization = Authorization.objects.filter(chargepoint=chargepoint).first()
    if not authorization:
        return Response({'error': 'Authorization details not found'}, status=404)

    # Access control is handled by the IsProxyOrOwner permission class
    data = {
        "connect_url": authorization.connect_url,
        "cp_id": authorization.cp_id,
        "auth_key": authorization.auth_key,
        "sec_prof": authorization.sec_prof,
    }
    logger.info(f"ChargePoint details: {data}")
    return JsonResponse(data, status=200)

@api_view(['POST'])
@permission_classes([TokenHasScopeForMethod])
@method_scopes({'POST': ['chargepoints:write']})
def set_ocpp_credentials(request, id):
    user = request.user
    chargepoint = get_object_or_404(ChargePoint, id=id, user=user)

    connect_url = request.data.get('connect_url')
    cp_id = request.data.get('cp_id')
    auth_key = request.data.get('auth_key')
    sec_prof = request.data.get('sec_prof')

    if not connect_url or not cp_id or not auth_key or not sec_prof:
        logger.warning("Missing required fields in the request")
        return JsonResponse({'error': 'All fields are required'}, status=400)

    logger.warning(f"Setting OCPP credentials for ChargePoint ID: {id} with data: {request.data}")

    chargepoint.connect_url = connect_url
    chargepoint.cp_id = cp_id
    chargepoint.auth_key = auth_key
    chargepoint.sec_prof = sec_prof
    chargepoint.save()

    return JsonResponse({'message': 'Credentials set successfully'}, status=200)

# Status Page
@login_required
@method_scopes({'GET': ['chargepoints:read']})
def status_page(request):
    chargepoints = ChargePoint.objects.filter(user=request.user)
    return render(request, 'status_page.html', {'chargepoints': chargepoints})

# Function to send verification email
def send_verification_email(request, user):
    try:
        token = default_token_generator.make_token(user)
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        current_site = get_current_site(request)
        mail_subject = 'Activate your account.'
        activation_link = f"http://{current_site.domain}{reverse('activate', kwargs={'uidb64': uid, 'token': token})}"
        logger.warning(f"Generated activation link for user {user.username}: {activation_link}")
        message = render_to_string('activate_email.html', {
            'user': user,
            'activation_link': activation_link,
        })
        send_mail(
            mail_subject,
            message,
            'webmaster@localhost',
            [user.email],
            fail_silently=False,
            html_message=message,
        )
        logger.warning(f"Activation email sent to {user.email}")
    except Exception as e:
        logger.error(f"Failed to send activation email to {user.email}: {str(e)}")
from .forms import ResendVerificationEmailForm
from django.contrib import messages
def resend_verification_email(request):
    if request.method == 'POST':
        form = ResendVerificationEmailForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            email_address = EmailAddress.objects.filter(email=email).first()
            if email_address:
                if email_address.verified:
                    messages.info(request, 'This email address is already verified.')
                    logger.warning(f"Email address {email} is already verified.")
                else:
                    user = email_address.user
                    send_email_confirmation(request, user)
                    messages.success(request, 'A new verification email has been sent.')
                    logger.warning(f"Verification email resent to: {email}")
                    return render(request, 'account/verification_sent.html', {'email': user.email})
            else:
                logger.warning(f"No email address found for: {email}")
                form.add_error('email', 'No email address found for this email.')
    else:
        form = ResendVerificationEmailForm()
    return render(request, 'account/resend_verification_email.html', {'form': form})

@login_required
def delete_account(request):
    if request.method == 'POST':
        user = request.user
        user.delete()
        messages.success(request, 'Your account has been deleted successfully.')
        return redirect('account_login')
    return render(request, 'account/delete_account.html')