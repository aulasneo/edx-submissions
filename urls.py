"""Norla URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/1.8/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  url(r'^$', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  url(r'^$', Home.as_view(), name='home')
Including another URLconf
    1. Add an import:  from blog import urls as blog_urls
    2. Add a URL to urlpatterns:  url(r'^blog/', include(blog_urls))
"""
from django.urls import include, re_path, path
from django.conf.urls.static import static
from django.views.static import serve
from rest_framework_jwt.views import obtain_jwt_token, refresh_jwt_token, verify_jwt_token

from django.conf import settings

urlpatterns = [
    re_path(r'^authenticate/login/', obtain_jwt_token),
    re_path(r'^authenticate/token-refresh/', refresh_jwt_token),
    re_path(r'^authenticate/token-verify/', verify_jwt_token),
    re_path(r'^api/', include('submissions.urls')),
]


urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
