"""Authentication views with bounded sessions and abuse protection."""

import hashlib
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import (
    LoginView,
    PasswordChangeView,
    PasswordResetConfirmView,
    PasswordResetView,
)
from django.db import transaction
from django.utils import timezone

from .models import ActivityLog, LoginAttempt, UserSecurityProfile
from .normalization import normalize_email
from .security import get_client_ip


class RememberMeAuthenticationForm(AuthenticationForm):
    remember_me = forms.BooleanField(
        required=False,
        label="Remember me on this device",
    )


def _attempt_key(request, username):
    raw_value = f"{get_client_ip(request) or 'unknown'}\0{normalize_email(username)}"
    return hashlib.sha256(raw_value.encode("utf-8")).hexdigest()


def _locked(key_hash):
    return LoginAttempt.objects.filter(
        key_hash=key_hash,
        locked_until__gt=timezone.now(),
    ).exists()


def _record_failure(key_hash):
    now = timezone.now()
    window = timedelta(seconds=settings.LOGIN_ATTEMPT_WINDOW_SECONDS)
    lock_period = timedelta(seconds=settings.LOGIN_LOCK_SECONDS)
    with transaction.atomic():
        attempt, _ = LoginAttempt.objects.select_for_update().get_or_create(
            key_hash=key_hash,
            defaults={
                "failure_count": 0,
                "window_started_at": now,
                "last_attempt_at": now,
            },
        )
        if now - attempt.window_started_at > window:
            attempt.failure_count = 0
            attempt.window_started_at = now
            attempt.locked_until = None
        attempt.failure_count += 1
        attempt.last_attempt_at = now
        if attempt.failure_count >= settings.LOGIN_MAX_FAILURES:
            attempt.locked_until = now + lock_period
        attempt.save(
            update_fields=[
                "failure_count",
                "window_started_at",
                "last_attempt_at",
                "locked_until",
            ]
        )


class SecureLoginView(LoginView):
    authentication_form = RememberMeAuthenticationForm
    template_name = "registration/login.html"

    def post(self, request, *args, **kwargs):
        self.attempt_key = _attempt_key(request, request.POST.get("username", ""))
        if _locked(self.attempt_key):
            form = self.get_form()
            form.add_error(
                None,
                "Sign-in is temporarily unavailable. Please try again later.",
            )
            self.rate_limited = True
            return self.form_invalid(form)
        self.rate_limited = False
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        if not getattr(self, "rate_limited", False):
            _record_failure(getattr(self, "attempt_key", _attempt_key(self.request, "")))
        return super().form_invalid(form)

    def form_valid(self, form):
        response = super().form_valid(form)
        if form.cleaned_data.get("remember_me"):
            self.request.session.set_expiry(settings.REMEMBER_ME_SESSION_AGE)
        else:
            self.request.session.set_expiry(0)
        LoginAttempt.objects.filter(key_hash=self.attempt_key).delete()
        ActivityLog.objects.create(
            actor=self.request.user,
            action=ActivityLog.Action.LOGIN,
            object_type="User",
            object_id=str(self.request.user.pk),
            description="User signed in.",
            ip_address=get_client_ip(self.request),
        )
        return response


class SecurePasswordChangeView(PasswordChangeView):
    template_name = "registration/password_change_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        UserSecurityProfile.objects.filter(user=self.request.user).update(
            must_change_password=False,
            password_changed_at=timezone.now(),
        )
        return response


class SecurePasswordResetView(PasswordResetView):
    template_name = "registration/password_reset_form.html"
    email_template_name = "registration/password_reset_email.html"
    subject_template_name = "registration/password_reset_subject.txt"


class SecurePasswordResetConfirmView(PasswordResetConfirmView):
    template_name = "registration/password_reset_confirm.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        UserSecurityProfile.objects.filter(user=self.user).update(
            must_change_password=False,
            password_changed_at=timezone.now(),
        )
        return response
