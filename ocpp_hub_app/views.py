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
import os
import logging
from .models import OAuthToken, ChargePoint, Profile, Authorization
from .forms import UserRegistrationForm, ChargePointForm, AuthorizationForm
from .serializers import ChargePointSerializer, OCPPCredentialsSerializer
from .permissions import TokenHasScopeForMethod
from allauth.account.utils import send_email_confirmation
from .decorators import method_scopes

logger = logging.getLogger(__name__)

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
    return render(request, 'account/register.html', {'form': form})

from .utils import generate_auth_key

from django.views.decorators.csrf import ensure_csrf_cookie

@login_required
@ensure_csrf_cookie
def generate_auth_key_view(request):
    auth_key = generate_auth_key()
    return JsonResponse({'auth_key': auth_key})

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
        return render(request, 'account/activation_invalid.html')

@login_required
def profile(request):
    if request.method == 'POST':
        # handle profile update logic
        pass
    return render(request, 'account/profile.html')

@login_required
def profile_view(request):
    email_address = EmailAddress.objects.filter(user=request.user, primary=True).first()
    is_verified = email_address.verified if email_address else False

    return render(request, 'account/profile.html', {
        'user': request.user,
        'email': email_address.email if email_address else '',
        'is_verified': is_verified,
    })

def landing(request):
    return render(request, 'landing.html')

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from .models import ChargePoint, Authorization


@login_required
def dashboard(request):
    user = request.user
    chargepoints = ChargePoint.objects.filter(user=user)
    authorizations = Authorization.objects.filter(chargepoint__in=chargepoints)

    status_info = {
        'total_chargepoints': chargepoints.count(),
        'total_authorizations': authorizations.count(),
        'connected_authorizations': authorizations.filter(connection_status='Connected').count(),
        'disconnected_authorizations': authorizations.filter(connection_status='Disconnected').count(),
    }

    return render(request, 'dashboard.html', {'status_info': status_info})


import json
from uuid import UUID

# Function to notify the proxy server to close the authorization connection
def notify_proxy_server_close(authorization_uuid):
    proxy_url = os.getenv('PROXY_SERVER_URL')
    close_endpoint = f"{proxy_url}/api/close_authorization"
    try:
        response = requests.post(close_endpoint, json={'authorization_uuid': str(authorization_uuid)})
        if response.status_code == 200:
            logger.info(f"Successfully notified proxy server to close authorization {authorization_uuid}")
        else:
            logger.error(f"Failed to notify proxy server to close authorization {authorization_uuid}: {response.content.decode()}")
    except Exception as e:
        logger.error(f"Error notifying proxy server to close authorization {authorization_uuid}: {str(e)}")

# Authorization Management
@login_required
@permission_classes([IsProxyOrOwner])
def authorization_create(request, uuid):
    chargepoint = get_object_or_404(ChargePoint, uuid=uuid, user=request.user)
    if request.method == 'POST':
        form = AuthorizationForm(request.POST)
        if form.is_valid():
            authorization = form.save(commit=False)
            authorization.chargepoint = chargepoint
            if not Authorization.objects.filter(chargepoint=chargepoint).exists():
                authorization.is_primary = True
            authorization.save()
            reset_connections_for_chargepoint(uuid)
            return redirect('authorization_list', uuid=uuid)
    else:
        form = AuthorizationForm()
    return render(request, 'authorization/authorization_form.html', {'form': form, 'chargepoint': chargepoint})


@login_required
@permission_classes([IsProxyOrOwner])
def authorization_edit(request, chargepoint_uuid, auth_uuid):
    chargepoint = get_object_or_404(ChargePoint, uuid=chargepoint_uuid, user=request.user)
    authorization = get_object_or_404(Authorization, uuid=auth_uuid, chargepoint=chargepoint)
    if request.method == 'POST':
        form = AuthorizationForm(request.POST, instance=authorization)
        if form.is_valid():
            form.save()
            reset_connections_for_chargepoint(chargepoint_uuid)
            return redirect('authorization_list', uuid=chargepoint_uuid)
    else:
        form = AuthorizationForm(instance=authorization)
    return render(request, 'authorization/authorization_form.html', {'form': form, 'chargepoint': chargepoint})


@login_required
@permission_classes([IsProxyOrOwner])
def authorization_delete(request, chargepoint_uuid, auth_uuid):
    chargepoint = get_object_or_404(ChargePoint, uuid=chargepoint_uuid, user=request.user)
    authorization = get_object_or_404(Authorization, uuid=auth_uuid, chargepoint=chargepoint)
    if request.method == 'POST':
        authorization.delete()
        reset_connections_for_chargepoint(chargepoint_uuid)
        return redirect('authorization_list', uuid=chargepoint_uuid)
    return render(request, 'authorization/authorization_confirm_delete.html', {'authorization': authorization, 'chargepoint': chargepoint})
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

from .models import ChargePoint, Authorization, LogEntry

from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required

@login_required
def chargepoint_log_list(request):
    chargepoints = ChargePoint.objects.filter(user=request.user)
    return render(request, 'chargepoint/chargepoint_log_list.html', {'chargepoints': chargepoints})

@login_required
def authorization_log_list(request):
    authorizations = Authorization.objects.filter(chargepoint__user=request.user)
    return render(request, 'authorization/authorization_log_list.html', {'authorizations': authorizations})

@login_required
def chargepoint_log(request, uuid):
    chargepoint = get_object_or_404(ChargePoint, uuid=uuid, user=request.user)
    logs = LogEntry.objects.filter(chargepoint=chargepoint).order_by('-timestamp')
    return render(request, 'chargepoint/chargepoint_log.html', {'chargepoint': chargepoint, 'logs': logs})

@login_required
def authorization_log(request, uuid):
    authorization = get_object_or_404(Authorization, uuid=uuid)
    logs = LogEntry.objects.filter(authorization=authorization).order_by('-timestamp')
    return render(request, 'authorization/authorization_log.html', {'authorization': authorization, 'logs': logs})

@api_view(['PATCH'])
@permission_classes([IsProxyOrOwner])
def update_chargepoint_status(request, uuid):
    chargepoint = get_object_or_404(ChargePoint, uuid=uuid)
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
def update_chargepoint_connection_status(request, uuid):
    chargepoint = get_object_or_404(ChargePoint, uuid=uuid)
    new_connection_status = request.data.get('connection_status')

    valid_statuses = ['Connected', 'Disconnected']

    if new_connection_status not in valid_statuses:
        return Response({'error': 'Invalid connection_status'}, status=http_status.HTTP_400_BAD_REQUEST)

    chargepoint.connection_status = new_connection_status
    chargepoint.save()

    return Response({'status': 'success'}, status=http_status.HTTP_200_OK)

@api_view(['PATCH'])
@permission_classes([IsProxyOrOwner])
def update_authorization_connection_status(request, uuid):
    authorization = get_object_or_404(Authorization, uuid=uuid)
    new_connection_status = request.data.get('connection_status')

    valid_statuses = ['Connected', 'Disconnected']

    if new_connection_status not in valid_statuses:
        return Response({'error': 'Invalid connection_status'}, status=http_status.HTTP_400_BAD_REQUEST)

    authorization.connection_status = new_connection_status
    authorization.save()

    return Response({'status': 'success'}, status=http_status.HTTP_200_OK)

# Charge Point Management
@login_required
@method_scopes({'GET': ['chargepoints:read']})
def chargepoint_list(request):
    chargepoints = ChargePoint.objects.filter(user=request.user)
    return render(request, 'chargepoint/chargepoint_list.html', {'chargepoints': chargepoints})

@login_required
def chargepoint_create(request):
    if request.method == 'POST':
        form = ChargePointForm(request.POST)
        if form.is_valid():
            chargepoint = form.save(commit=False)
            chargepoint.user = request.user
            chargepoint.save()
            return redirect('dashboard')
    else:
        form = ChargePointForm()
    return render(request, 'chargepoint/chargepoint_form.html', {'form': form, 'is_edit': False})

@login_required
def chargepoint_edit(request, uuid):
    chargepoint = get_object_or_404(ChargePoint, uuid=uuid, user=request.user)
    if request.method == 'POST':
        form = ChargePointForm(request.POST, instance=chargepoint)
        if form.is_valid():
            form.save()
            reset_connections_for_chargepoint(uuid)
            return redirect('dashboard')
    else:
        form = ChargePointForm(instance=chargepoint)
    return render(request, 'chargepoint/chargepoint_form.html', {'form': form, 'is_edit': True})

@login_required
@method_scopes({'POST': ['chargepoints:write']})
def chargepoint_delete(request, uuid):
    chargepoint = get_object_or_404(ChargePoint, uuid=uuid, user=request.user)
    if request.method == 'POST':
        chargepoint.delete()
        return redirect('dashboard')
    return render(request, 'chargepoint/chargepoint_confirm_delete.html', {'chargepoint': chargepoint})

from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from .models import ChargePoint, Authorization, LogEntry
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils.timezone import make_aware
import datetime
import json

@login_required
def logs(request):
    return render(request, 'logs.html')
import logging
import json
import datetime
from django.utils.timezone import make_aware
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from .models import LogEntry
from django.core.paginator import Paginator
logger = logging.getLogger(__name__)

@require_http_methods(["POST"])
def logs_data(request):
    try:
        # Parse request data
        data = json.loads(request.body)
        logger.debug(f"Received request data: {data}")

        # Extract filter parameters
        event_types = data.get('eventTypes', [])
        start_date = data.get('startDate')
        end_date = data.get('endDate')
        from_entity = data.get('fromEntity')
        to_entity = data.get('toEntity')
        search_query = data.get('searchQuery', '').lower()
        logs_per_page = int(data.get('logsPerPage', 30))
        page = int(data.get('page', 1))

        # Start with all logs, ordered by timestamp
        logs = LogEntry.objects.all().order_by('-timestamp')

        # Apply filters
        if event_types:
            logs = logs.filter(event_type__in=event_types)

        if start_date:
            logs = logs.filter(timestamp__gte=make_aware(datetime.fromisoformat(start_date)))

        if end_date:
            logs = logs.filter(timestamp__lte=make_aware(datetime.fromisoformat(end_date)))

        if from_entity:
            logs = logs.filter(Q(from_entity__name__icontains=from_entity) | Q(from_entity__type__icontains=from_entity))

        if to_entity:
            logs = logs.filter(Q(to_entity__name__icontains=to_entity) | Q(to_entity__type__icontains=to_entity))

        if search_query:
            logs = logs.filter(
                Q(message__icontains=search_query) |
                Q(event_type__icontains=search_query) |
                Q(action__icontains=search_query) |
                Q(from_entity__name__icontains=search_query) |
                Q(to_entity__name__icontains=search_query)
            )

        # Log the count of filtered logs
        logger.debug(f"Filtered logs count: {logs.count()}")

        # Paginate the results
        paginator = Paginator(logs, logs_per_page)
        logger.debug(f"Paginator created with {logs_per_page} logs per page")
        page_obj = paginator.get_page(page)

        # Prepare the log data for JSON serialization
        log_data = []
        for log in page_obj:
            log_data.append({
                'timestamp': log.timestamp.isoformat(),
                'level': log.level,
                'event_type': log.event_type,
                'action': log.action,
                'from': log.get_from_entity(),
                'to': log.get_to_entity(),
                'message': log.message,
                'raw_message': log.get_raw_message()
            })

        # Prepare the response
        response_data = {
            'logs': log_data,
            'totalLogs': paginator.count,
            'totalPages': paginator.num_pages,
            'currentPage': page
        }

        logger.debug(f"Returning {len(log_data)} logs for page {page}")
        return JsonResponse(response_data)

    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in request body: {str(e)}")
        return JsonResponse({'error': 'Invalid JSON in request body'}, status=400)
    except ValueError as e:
        logger.error(f"Invalid value in request: {str(e)}")
        return JsonResponse({'error': str(e)}, status=400)
    except Exception as e:
        logger.error(f"Unexpected error in logs_data view: {str(e)}", exc_info=True)
        return JsonResponse({'error': 'An unexpected error occurred while fetching logs'}, status=500)


from django.http import JsonResponse

from django.urls import reverse
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from .models import ChargePoint, Authorization, LogEntry

@login_required
def dashboard_data(request):
    user = request.user
    chargepoints = ChargePoint.objects.filter(user=user)
    authorizations = Authorization.objects.filter(chargepoint__user=user)
    
    chargepoints_data = []
    for cp in chargepoints:
        chargepoints_data.append({
            'name': cp.name,
            'location': cp.location,
            'status': cp.status,
            'connection_status': cp.connection_status,
            'uuid': str(cp.uuid),
            'edit_url': reverse('chargepoint_edit', args=[cp.uuid]),
            'delete_url': reverse('chargepoint_delete', args=[cp.uuid]),
            'manage_authorizations_url': reverse('authorization_list', args=[cp.uuid]),
            'view_logs_url': reverse('chargepoint_log', args=[cp.uuid]),
            'log_count': cp.logentry_set.count(),
        })
    
    authorizations_data = []
    for auth in authorizations:
        authorizations_data.append({
            'uuid': str(auth.uuid),
            'csms_name': auth.csms_name,
            'connect_url': auth.connect_url,
            'chargepoint_name': auth.chargepoint.name,
            'chargepoint_uuid': str(auth.chargepoint.uuid),
            'cp_id': auth.cp_id,
            'connection_status': auth.connection_status,
            'is_primary': auth.is_primary,
            'edit_url': reverse('authorization_edit', args=[auth.chargepoint.uuid, auth.uuid]),
            'delete_url': reverse('authorization_delete', args=[auth.chargepoint.uuid, auth.uuid]),
            'view_logs_url': reverse('authorization_log', args=[auth.uuid]),
            'set_primary_url': reverse('set_primary_authorization', args=[auth.chargepoint.uuid, auth.uuid]),
            'log_count': auth.logentry_set.count(),
        })
    
    status_info = {
        'total_chargepoints': chargepoints.count(),
        'total_authorizations': authorizations.count(),
        'connected_authorizations': authorizations.filter(connection_status='Connected').count(),
        'disconnected_authorizations': authorizations.filter(connection_status='Disconnected').count(),
    }
    
    data = {
        'chargepoints': chargepoints_data,
        'authorizations': authorizations_data,
        'status_info': status_info,
    }
    return JsonResponse(data)

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
    charge_point_uuid = request.data.get('charge_point_uuid')
    config_data = request.data.get('config')

    if not charge_point_uuid or not config_data:
        logger.warning("Missing charge point UUID or configuration data")
        return Response({'error': 'Charge point UUID and configuration data are required'}, status=400)

    configuration_data = {
        'charge_point_uuid': charge_point_uuid,
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

    charge_point_uuid = request.query_params.get('charge_point_uuid')

    if not charge_point_uuid:
        logger.warning("Missing charge point UUID")
        return Response({'error': 'Charge point UUID is required'}, status=400)

    meter_values = fetch_meter_values_from_csms(charge_point_uuid)
    logger.warning(f"Meter values retrieved: {meter_values}")
    return Response({'meter_values': meter_values})

def fetch_meter_values_from_csms(charge_point_uuid):
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
            "uuid": str(cp.uuid),
            "name": cp.name,
            "status": cp.status,
            "location": cp.location
        }
        for cp in chargepoints
    ]
    return JsonResponse(response_data, safe=False)

# Authorization Management
@login_required
@method_scopes({'GET': ['chargepoints:read']})
def authorization_list(request, uuid):
    chargepoint = get_object_or_404(ChargePoint, uuid=uuid, user=request.user)
    authorizations = Authorization.objects.filter(chargepoint=chargepoint)
    return render(request, 'authorization/authorization_list.html', {'authorizations': authorizations, 'chargepoint': chargepoint})

@api_view(['GET'])
@permission_classes([IsProxyOrOwner])
def get_chargepoint_details(request, uuid):
    logger.info(f"Fetching details for ChargePoint UUID: {uuid}")

    chargepoint = get_object_or_404(ChargePoint, uuid=uuid)
    
    # Fetch the authorization details associated with this chargepoint
    authorizations = Authorization.objects.filter(chargepoint=chargepoint)
    if not authorizations:
        return Response({'error': 'Authorization details not found'}, status=404)

    # Access control is handled by the IsProxyOrOwner permission class
    data = {
        "chargepoint_name": chargepoint.name,
        "authorizations": [
            {
                "csms_name": auth.csms_name,
                "connect_url": auth.connect_url,
                "cp_id": auth.cp_id,
                "auth_key": auth.auth_key,
                "sec_prof": auth.sec_prof,
            }
            for auth in authorizations
        ],
    }
    logger.info(f"ChargePoint details: {data}")
    return JsonResponse(data, status=200)

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
        message = render_to_string('account/activate_email.html', {
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

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
import requests
import os
import json

@csrf_exempt
@require_POST
def restart_authorization(request):
    data = json.loads(request.body)
    authorization_uuid = data.get('authorization_uuid')
    
    if not authorization_uuid:
        return JsonResponse({'error': 'authorization_uuid is required'}, status=400)
    
    proxy_url = os.getenv('PROXY_SERVER_URL') + '/api/restart_authorization'
    response = requests.post(proxy_url, json={'authorization_uuid': str(authorization_uuid)})
    
    if response.status_code == 200:
        return JsonResponse({'status': 'success'})
    else:
        return JsonResponse({'error': 'Failed to restart authorization'}, status=500)

@csrf_exempt
@require_POST
def restart_chargepoint(request):
    """
    Restarts the connection for a chargepoint by its UUID.

    Parameters:
        request (HttpRequest): The HTTP request containing the chargepoint UUID.

    Returns:
        JsonResponse: The response indicating the success or failure of the operation.
    """
    try:
        data = json.loads(request.body)
        chargepoint_uuid = data.get('chargepoint_uuid')
        
        if not chargepoint_uuid:
            logger.error('chargepoint_uuid is required')
            return JsonResponse({'error': 'chargepoint_uuid is required'}, status=400)
        
        chargepoint = get_object_or_404(ChargePoint, uuid=chargepoint_uuid)
        notify_proxy_server_restart(chargepoint_uuid)
        logger.info(f'Successfully restarted chargepoint {chargepoint_uuid}')
        return JsonResponse({'status': 'success'})
    except Exception as e:
        logger.error(f'Error restarting chargepoint {chargepoint_uuid}: {str(e)}', exc_info=True)
        return JsonResponse({'error': 'Failed to restart chargepoint', 'details': str(e)}, status=500)
from django.db import transaction

@login_required
@require_POST
def set_primary_authorization(request, chargepoint_uuid, authorization_uuid):
    try:
        chargepoint = get_object_or_404(ChargePoint, uuid=chargepoint_uuid, user=request.user)
        authorization = get_object_or_404(Authorization, uuid=authorization_uuid, chargepoint=chargepoint)

        # Get all authorizations for this chargepoint
        all_authorizations = Authorization.objects.filter(chargepoint=chargepoint)
        authorizations_data = [
            {
                'uuid': str(auth.uuid),
                'csms_name': auth.csms_name,
                'is_primary': auth.uuid == authorization_uuid
            } for auth in all_authorizations
        ]

        # Notify proxy server about the change
        proxy_response = notify_proxy_server_set_primary(chargepoint_uuid, authorization_uuid, authorizations_data)
        
        if proxy_response.get('status') == 'success':
            # Update local database to reflect the change
            Authorization.objects.filter(chargepoint=chargepoint).update(is_primary=False)
            authorization.is_primary = True
            authorization.save()

            # Reset connections for the chargepoint
            reset_success = reset_connections_for_chargepoint(chargepoint_uuid)

            if reset_success:
                logger.info(f"Successfully reset connections for chargepoint {chargepoint_uuid}")
            else:
                logger.error(f"Failed to reset connections for chargepoint {chargepoint_uuid}")

            # Prepare response data
            primary_auth = {
                'uuid': str(authorization.uuid),
                'csms_name': authorization.csms_name,
                'is_primary': True
            }
            secondary_auths = [{
                'uuid': str(auth.uuid),
                'csms_name': auth.csms_name,
                'is_primary': False
            } for auth in Authorization.objects.filter(chargepoint=chargepoint).exclude(uuid=authorization.uuid)]

            logger.info(f"Successfully set primary authorization {authorization_uuid} for chargepoint {chargepoint_uuid}")
            return JsonResponse({
                'status': 'success',
                'primary': primary_auth,
                'secondary': secondary_auths
            })
        else:
            logger.error(f"Proxy server failed to set primary authorization {authorization_uuid} for chargepoint {chargepoint_uuid}")
            return JsonResponse({'status': 'error', 'message': 'Proxy server failed to set primary authorization'}, status=500)
    except Exception as e:
        logger.error(f"Error setting primary authorization: {str(e)}", exc_info=True)
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

def reset_connections_for_chargepoint(chargepoint_uuid):
    proxy_url = os.getenv('PROXY_SERVER_URL')
    reset_endpoint = f"{proxy_url}/api/reset_chargepoint_connections"
    
    try:
        response = requests.post(reset_endpoint, json={'chargepoint_uuid': str(chargepoint_uuid)})
        response.raise_for_status()
        response_data = response.json()
        if response_data.get('status') == 'success':
            logger.info(f"Successfully reset connections for chargepoint {chargepoint_uuid}")
            return True
        else:
            logger.error(f"Failed to reset connections for chargepoint {chargepoint_uuid}: {response_data.get('error', 'Unknown error')}")
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Error communicating with proxy server to reset connections for chargepoint {chargepoint_uuid}: {str(e)}")
        return False

@login_required
@require_POST
def reset_chargepoint_connections(request):
    try:
        data = json.loads(request.body)
        chargepoint_uuid = data.get('chargepoint_uuid')
        
        if not chargepoint_uuid:
            logger.error('chargepoint_uuid is required')
            return JsonResponse({'error': 'chargepoint_uuid is required'}, status=400)
        
        chargepoint = get_object_or_404(ChargePoint, uuid=chargepoint_uuid, user=request.user)
        
        proxy_url = os.getenv('PROXY_SERVER_URL')
        reset_endpoint = f"{proxy_url}/api/reset_chargepoint_connections"
        
        response = requests.post(reset_endpoint, json={'chargepoint_uuid': str(chargepoint_uuid)})
        
        response_data = response.json()
        if response.status_code == 200 and response_data.get('status') == 'success':
            logger.info(f'Successfully reset connections for chargepoint {chargepoint_uuid}')
            return JsonResponse({'status': 'success'})
        else:
            error_message = response_data.get('error', 'Unknown error occurred')
            logger.error(f'Failed to reset connections for chargepoint {chargepoint_uuid}: {error_message}')
            return JsonResponse({'error': error_message}, status=500)
    except requests.exceptions.RequestException as e:
        logger.error(f'Error communicating with proxy server: {str(e)}', exc_info=True)
        return JsonResponse({'error': f'Failed to communicate with proxy server: {str(e)}'}, status=500)
    except Exception as e:
        logger.error(f'Unexpected error resetting connections for chargepoint {chargepoint_uuid}: {str(e)}', exc_info=True)
        return JsonResponse({'error': f'An unexpected error occurred: {str(e)}'}, status=500)

def notify_proxy_server_set_primary(chargepoint_uuid, authorization_uuid, authorizations_data):
    proxy_url = os.getenv('PROXY_SERVER_URL')
    set_primary_endpoint = f"{proxy_url}/api/set_primary_authorization"
    try:
        response = requests.post(set_primary_endpoint, json={
            'chargepoint_uuid': str(chargepoint_uuid),
            'authorization_uuid': str(authorization_uuid),
            'authorizations': authorizations_data
        })
        response.raise_for_status()
        logger.info(f"Successfully notified proxy server to set primary authorization {authorization_uuid} for chargepoint {chargepoint_uuid}")
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error notifying proxy server to set primary authorization {authorization_uuid} for chargepoint {chargepoint_uuid}: {str(e)}", exc_info=True)
        return {'status': 'error', 'message': str(e)}


def notify_proxy_server_restart(chargepoint_uuid):
    """
    Notifies the proxy server to restart the connection for a chargepoint.

    Parameters:
        chargepoint_uuid (str): The UUID of the chargepoint.
    """
    proxy_url = os.getenv('PROXY_SERVER_URL')
    restart_endpoint = f"{proxy_url}api/restart_chargepoint"
    try:
        response = requests.post(restart_endpoint, json={'chargepoint_uuid': str(chargepoint_uuid)})
        response.raise_for_status()
        logger.info(f"Successfully notified proxy server to restart chargepoint {chargepoint_uuid}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error notifying proxy server to restart chargepoint {chargepoint_uuid}: {str(e)}", exc_info=True)
        raise



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