""" Submissions Views. """

import logging

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from submissions.api import SubmissionRequestError, get_submissions

log = logging.getLogger(__name__)

from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import BasicAuthentication

from submissions.api import get_submissions, SubmissionRequestError
from django.db import DatabaseError, IntegrityError, transaction

from submissions.models import (
    DELETED,
    Score,
    ScoreAnnotation,
    ScoreSummary,
    StudentItem,
    Submission,
    score_reset,
    score_set
)
from submissions.serializers import (
    ScoreSerializer,
    StudentItemSerializer,
    SubmissionSerializer,
    UnannotatedScoreSerializer
)


class XQueueSubmissionView(ModelViewSet):

    #permission_classes = (IsAppAuthenticated, IsAppStaff, IsAuthenticated, IsUserAdmin, IsCompanyPermission)
    queryset = StudentItem.objects.all().order_by('id')
    serializer_class = StudentItemSerializer
    filter_backends = (DjangoFilterBackend,)
    filter_fields = ('course_id', 'student_id', 'item_id', "item_type",)

    #authentication_classes = [BasicAuthentication]
    #permission_classes = [IsAuthenticated]

@login_required()
def get_submissions_for_student_item(request, course_id, student_id, item_id):
    """Retrieve all submissions associated with the given student item.

    Developer utility for accessing all the submissions associated with a
    student item. The student item is specified by the unique combination of
    course, student, and item.

    Args:
        request (dict): The request.
        course_id (str): The course id for this student item.
        student_id (str): The student id for this student item.
        item_id (str): The item id for this student item.

    Returns:
        HttpResponse: The response object for this request. Renders a simple
            development page with all the submissions related to the specified
            student item.

    """
    student_item_dict = {
        "course_id": course_id,
        "student_id": student_id,
        "item_id": item_id,
    }
    context = {**student_item_dict}
    try:
        submissions = get_submissions(student_item_dict)
        context["submissions"] = submissions
    except SubmissionRequestError:
        context["error"] = "The specified student item was not found."

    return render(request, 'submissions.html', context)



