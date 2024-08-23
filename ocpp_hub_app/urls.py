from django.urls import path, include
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    # Landing and Dashboard
    path('', views.landing, name='landing'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('dashboard/data/', views.dashboard_data, name='dashboard_data'),

    # Authentication and User Management
    path('register/', views.register, name='register'),
    path('activate/<uidb64>/<token>/', views.activate, name='activate'),
    path('resend-verification-email/', views.resend_verification_email, name='resend_verification_email'),
    path('accounts/', include('allauth.urls')),
    path('profile/', views.profile_view, name='profile_view'),
    path('delete-account/', views.delete_account, name='delete_account'),
    path('password_reset/', auth_views.PasswordResetView.as_view(), name='password_reset'),
    path('password_reset/done/', auth_views.PasswordResetDoneView.as_view(), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('reset/done/', auth_views.PasswordResetCompleteView.as_view(), name='password_reset_complete'),

    # OAuth
    path('oauth/login/', views.oauth_login, name='oauth_login'),
    path('oauth/callback/', views.oauth_callback, name='oauth_callback'),
    path('api/tokens/', views.TokenEndpoint.as_view(), name='token_endpoint'),
    path('api/hello/', views.ApiEndpoint.as_view(), name='api-hello'),

    # Chargepoint Management
    path('chargepoints/', views.chargepoint_list, name='chargepoint_list'),
    path('chargepoints/new/', views.chargepoint_create, name='chargepoint_create'),
    path('chargepoints/<uuid:uuid>/edit/', views.chargepoint_edit, name='chargepoint_edit'),
    path('chargepoints/<uuid:uuid>/delete/', views.chargepoint_delete, name='chargepoint_delete'),
    path('chargepoints/<uuid:uuid>/log/', views.chargepoint_log, name='chargepoint_log'),
    path('chargepoints/<uuid:uuid>/log/data/', views.chargepoint_log_data, name='chargepoint_log_data'),
    path('chargepoints/logs/', views.chargepoint_log_list, name='chargepoint_log_list'),

    # Authorization Management
    path('chargepoints/<uuid:uuid>/authorizations/', views.authorization_list, name='authorization_list'),
    path('chargepoints/<uuid:uuid>/authorizations/new/', views.authorization_create, name='authorization_create'),
    path('chargepoints/<uuid:chargepoint_uuid>/authorizations/<uuid:auth_uuid>/edit/', views.authorization_edit, name='authorization_edit'),
    path('chargepoints/<uuid:chargepoint_uuid>/authorizations/<uuid:auth_uuid>/delete/', views.authorization_delete, name='authorization_delete'),
    path('authorizations/<uuid:uuid>/log/', views.authorization_log, name='authorization_log'),
    path('authorizations/<uuid:uuid>/log/data/', views.authorization_log_data, name='authorization_log_data'),
    path('authorizations/logs/', views.authorization_log_list, name='authorization_log_list'),
    path('chargepoints/<uuid:chargepoint_uuid>/authorizations/<uuid:authorization_uuid>/set_primary/', views.set_primary_authorization, name='set_primary_authorization'),

    # API Endpoints
    path('api/setup_ocpp_configuration/', views.setup_ocpp_configuration, name='setup_ocpp_configuration'),
    path('api/get_meter_values/', views.get_meter_values, name='get_meter_values'),
    path('api/v1/chargepoints/', views.list_chargepoints, name='list_chargepoints'),
    path('api/v1/chargepoints/<uuid:uuid>/details/', views.get_chargepoint_details, name='get_chargepoint_details'),
    path('api/v1/chargepoints/<uuid:uuid>/status/', views.update_chargepoint_status, name='update_chargepoint_status'),
    path('api/v1/chargepoints/<uuid:uuid>/connection_status/', views.update_chargepoint_connection_status, name='update_chargepoint_connection_status'),
    path('api/v1/authorizations/<uuid:uuid>/connection_status/', views.update_authorization_connection_status, name='update_authorization_connection_status'),
    path('api/restart_authorization/', views.restart_authorization, name='restart_authorization'),
    path('api/restart_chargepoint/', views.restart_chargepoint, name='restart_chargepoint'),
    path('api/reset_chargepoint_connections/', views.reset_chargepoint_connections, name='reset_chargepoint_connections'),

    # Miscellaneous
    path('generate-auth-key/', views.generate_auth_key_view, name='generate_auth_key'),
    path('status/', views.status_page, name='status_page'),
    path('logs/', views.logs, name='logs'),
    path('logs/data/', views.logs_data, name='logs_data'),
]