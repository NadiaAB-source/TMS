"""Request-level protections shared by authentication and audit code."""

from django.conf import settings
from django.shortcuts import redirect


def get_client_ip(request):
    """Use forwarding headers only when the direct peer is trusted."""

    remote_addr = (request.META.get("REMOTE_ADDR") or "").strip()
    trusted_proxies = set(getattr(settings, "TRUSTED_PROXY_IPS", ()))
    if remote_addr in trusted_proxies:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        candidate = forwarded.split(",", 1)[0].strip()
        if candidate:
            return candidate
    return remote_addr or None


class SecurityHeadersMiddleware:
    """Add defensive browser headers without relying on a third-party proxy."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault(
            "Content-Security-Policy",
            settings.CONTENT_SECURITY_POLICY,
        )
        response.setdefault("Referrer-Policy", "same-origin")
        response.setdefault(
            "Permissions-Policy",
            "camera=(), geolocation=(), microphone=(), payment=(), usb=()",
        )
        response.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.setdefault("X-Content-Type-Options", "nosniff")
        return response


class ForcePasswordChangeMiddleware:
    """Prevent a temporary-password account from using the app first."""

    ALLOWED_PATH_PREFIXES = (
        "/accounts/password-change/",
        "/accounts/password-reset/",
        "/accounts/logout/",
        "/static/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            getattr(user, "is_authenticated", False)
            and not user.is_superuser
            and not request.path_info.startswith(self.ALLOWED_PATH_PREFIXES)
        ):
            from .models import UserSecurityProfile

            if UserSecurityProfile.objects.filter(
                user=user,
                must_change_password=True,
            ).exists():
                return redirect("password_change")
        return self.get_response(request)
