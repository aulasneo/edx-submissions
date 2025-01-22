from django.urls import path
from .views import XQueueSubmissionView


from rest_framework.routers import DefaultRouter

router = DefaultRouter()
router.register(r'get_submissions', XQueueSubmissionView)

urlpatterns = [
   
]

urlpatterns += router.urls
