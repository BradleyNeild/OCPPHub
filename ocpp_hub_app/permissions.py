from oauth2_provider.contrib.rest_framework import TokenHasScope
import logging
from rest_framework.permissions import BasePermission
logger = logging.getLogger(__name__)

class TokenHasScopeForMethod(TokenHasScope):
    def has_permission(self, request, view):
        token = request.auth

        if not token:
            logger.warning("No token found in the request")
            return False

        if hasattr(token, "scope"):
            # Get the scopes required for the current method from the view
            required_scopes = getattr(view, 'required_scopes_per_method', {}).get(request.method, [])
            if not required_scopes:
                logger.warning(f"No scopes required for method {request.method}")
                return True

            token_scopes = token.scope.split()
            if all(scope in token_scopes for scope in required_scopes):
                logger.info(f"Token has required scopes: {required_scopes}")
                return True
            else:
                logger.warning(f"Token scopes {token_scopes} do not include required scopes {required_scopes}")
                return False

        logger.warning("Token does not have 'scope' attribute")
        return False

class IsProxyOrOwner(BasePermission):
    """
    Custom permission to allow access to proxy and the owner of the chargepoint.
    """

    def has_permission(self, request, view):
        token = request.auth
        if token and 'proxy_access' in token.scope:
            logger.info("Access granted for proxy")
            return True
        elif request.user and request.user.is_authenticated:
            logger.info(f"Access granted for user: {request.user}")
            return True
        logger.warning("Access denied: No valid token or user")
        return False

    def has_object_permission(self, request, view, obj):
        token = request.auth
        if token and 'proxy_access' in token.scope:
            return True
        return obj.user == request.user