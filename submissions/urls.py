from django.urls import path
from .views import XQueueSubmissionView

urlpatterns = [
    # ...existing code...
    path('get_submissions/', XQueueSubmissionView.as_view(), name='get_submissions'),
]
