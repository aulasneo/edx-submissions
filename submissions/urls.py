"""
Submission URLs.
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from submissions.views.xqueue import XqueueViewSet

router = DefaultRouter()
router.register(r'', XqueueViewSet, basename='xqueue')

urlpatterns = [
    path('', include(router.urls)),
]
