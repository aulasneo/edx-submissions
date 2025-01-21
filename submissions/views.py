""" Submissions Views. """

import logging

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from submissions.api import SubmissionRequestError, get_submissions

log = logging.getLogger(__name__)

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import BasicAuthentication

from submissions.api import get_submissions, SubmissionRequestError

class XQueueSubmissionView(APIView):
    """
    API View to handle XQueue server endpoints for get_submissions, get_queuelen, etc.
    """
    #authentication_classes = [BasicAuthentication]
    #permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        """
        Handle GET requests to retrieve submissions or queue length.
        """
        action = request.query_params.get('action')
        course_id = request.query_params.get('course_id')
        student_id = request.query_params.get('student_id')
        item_id = request.query_params.get('item_id')

        if action == 'get_submissions':
            return self.get_submissions(course_id, student_id, item_id)
        #elif action == 'get_queuelen':
            #return self.get_queuelen(course_id, student_id, item_id)
        else:
            return Response({"error": "Invalid action"}, status=status.HTTP_400_BAD_REQUEST)

    def get_submissions(self, course_id, student_id, item_id):
        """
        Retrieve all submissions associated with the given student item.
        """
        student_item_dict = {
            "course_id": course_id,
            "student_id": student_id,
            "item_id": item_id,
        }
        try:
            submissions = get_submissions(student_item_dict)
            return Response(submissions, status=status.HTTP_200_OK)
        except SubmissionRequestError:
            return Response({"error": "The specified student item was not found."}, status=status.HTTP_404_NOT_FOUND)

    # def get_queuelen(self, course_id, student_id, item_id):
    #     """
    #     Retrieve the queue length for the given student item.
    #     """
    #     student_item_dict = {
    #         "course_id": course_id,
    #         "student_id": student_id,
    #         "item_id": item_id,
    #     }
    #     try:
    #         queue_length = get_queuelen(student_item_dict)
    #         return Response({"queue_length": queue_length}, status=status.HTTP_200_OK)
    #     except SubmissionRequestError:
    #         return Response({"error": "The specified student item was not found."}, status=status.HTTP_404_NOT_FOUND)


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



