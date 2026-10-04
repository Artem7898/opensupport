from django.urls import path

from . import views

urlpatterns = [
    path("session/", views.session),
    path("register/", views.register),
    path("login/", views.sign_in),
    path("logout/", views.sign_out),
    path("projects/", views.projects),
    path("submissions/", views.create_submission),
    path("submissions/mine/", views.mine),
    path("submissions/<int:pk>/verify/", views.verify),
    path("submissions/<int:pk>/challenge/", views.renew_challenge),
    path("moderation/", views.moderation),
    path("submissions/<int:pk>/review/", views.review),
    path("health/", views.health),
]
