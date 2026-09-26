from django.urls import path
from .staff_access_views import staff_access

urlpatterns = [path("staff/", staff_access, name="staff")]
