from django.urls import path

from . import views_links

app_name = "links"

urlpatterns = [
    path("", views_links.lista, name="lista"),
    path("novo/", views_links.novo, name="novo"),
    path("cartaz/", views_links.cartaz, name="cartaz"),
    path("<int:pk>/revogar/", views_links.revogar, name="revogar"),
]
