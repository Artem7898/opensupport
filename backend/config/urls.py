from django.contrib import admin
from django.urls import include, path

urlpatterns = [path("admin/", admin.site.urls), path("api/", include("directory.urls"))]
admin.site.site_header = "OpenSupport · Moderation"
