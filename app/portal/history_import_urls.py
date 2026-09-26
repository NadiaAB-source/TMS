from django.urls import path
from .history_import_views import historical_import

urlpatterns = [path('reports/import-history/', historical_import, name='historical_import')]
