from django.urls import path

from . import j35_grid_views


urlpatterns = [
    path("j35-grid/save/", j35_grid_views.j35_grid_save, name="j35_grid_save"),
]
