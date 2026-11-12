import ipaddress
from django.conf import settings
from django.utils.cache import patch_cache_control


class ProxyHeaders:
    """Only explicitly trusted peers may supply our nginx-overwritten headers."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.META.get("REMOTE_ADDR", "") in settings.TRUSTED_PROXIES:
            if request.META.get("HTTP_X_PREPPERDB_PROTO") == "https":
                request.META["wsgi.url_scheme"] = "https"
            client = request.META.get("HTTP_X_PREPPERDB_CLIENT_IP", "")
            try:
                request.META["REMOTE_ADDR"] = str(ipaddress.ip_address(client))
            except ValueError:
                pass
        return self.get_response(request)


class PrivateResponses:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        patch_cache_control(response, private=True, no_store=True)
        response["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob:; media-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        )
        return response
