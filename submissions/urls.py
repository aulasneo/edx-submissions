from django.urls import path, include
from rest_framework.routers import DefaultRouter
from submissions.views.grader import GraderViewSet

router = DefaultRouter()
router.register(r'graders', GraderViewSet, basename='grader')

urlpatterns = [
    path('', include(router.urls)),
]