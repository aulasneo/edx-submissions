"""Xqueue View set"""

import json
import logging

from django.http import HttpResponse
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db import transaction
from django.utils import timezone

from submissions.api import set_score
from submissions.models import SubmissionQueueRecord
from submissions.serializers import SubmissionQueueRecordSerializer

log = logging.getLogger(__name__)


class XqueueViewSet(viewsets.ViewSet):
    """
    A collection of services for xwatcher interactions.

    This ViewSet provides endpoints for managing and processing 
    external grader results in the system.

    Key features:
    - Handles validation of external grader responses
    - Processes and updates submission scores
    - Provides a secure endpoint for result updates

    Endpoints:
    - put_result: Endpoint for graders to submit their assessment results
    """

    #permission_classes = [IsAuthenticated]
    
    @action(detail=False, methods=['get'], url_name='get_submission')
    @transaction.atomic
    def get_submission(self, request):
        """
        Endpoint for consult data to submission.
        """
        queue_name = request.query_params.get('queue_name', None)
        status_param = request.query_params.get('status', None)
        submission_id = request.query_params.get('submission_id', None)

        # Filter queryset
        queryset = SubmissionQueueRecord.objects.all().order_by('id')

        if queue_name:
            queryset = queryset.filter(queue_name=queue_name)
        if status_param:
            queryset = queryset.filter(status=status_param)
        if submission_id:
            queryset = queryset.filter(submission_id=submission_id)

        # Serializar and return a response
        serializer = SubmissionQueueRecordSerializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_name='put_result')
    @transaction.atomic
    def put_result(self, request):
        """
        Endpoint for graders to post their results and update submission scores.
        """
        (reply_valid, submission_id, submission_key, score_msg, points_earned)= (
            self._validate_grader_reply(request.data))

        if not reply_valid:
            log.error("Invalid reply from pull-grader: request.data: %s",
                      request.data)
            return Response(
                self._compose_reply(False, 'Incorrect reply format'),
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            submission_record = SubmissionQueueRecord.objects.select_for_update().get(submission__id=submission_id)
        except SubmissionQueueRecord.DoesNotExist:
            log.error(
                "Grader submission_id refers to nonexistent entry in Submission DB: "
                "grader: %s, submission_key: %s, score_msg: %s",
                submission_id,
                submission_key,
                score_msg
            )
            return Response(
                self._compose_reply(False, 'Submission does not exist'),
                status=status.HTTP_404_NOT_FOUND
            )

        if not submission_record.pullkey or submission_key != submission_record.pullkey:
            log.error(f"Invalid pullkey: submission key from xwatcher {submission_key} "
                      f"and submission key stored {submission_record.pullkey} are different")
            return Response(
                self._compose_reply(False, 'Incorrect key for submission'),
                status=status.HTTP_403_FORBIDDEN
            )

        # pylint: disable=broad-exception-caught
        try:
            log.info("Attempting to set_score...")
            set_score(str(submission_record.submission.uuid),
                      points_earned,
                      1
                      )

            submission_record.grader_reply = score_msg
            submission_record.status_time = timezone.now()
            submission_record.status = "returned"
            submission_record.save()
            log.info("Successfully updated submission score for submission %s", submission_id)

        except Exception as e:
            log.error(f"Error when execute set_score: {e}")
            # Keep track of how many times we've failed to set_score a grade for this submission
            submission_record.num_failures += 1
            if submission_record.num_failures > 30:
                submission_record.status = "failed"
            else:
                submission_record.status = "pending"
            submission_record.save()

        return HttpResponse(self._compose_reply(success=True, content=''))


    def _validate_grader_reply(self, external_reply):
        """
        Validate the format of external grader reply.

        Returns:
            tuple: (is_valid, submission_id, submission_key, score_msg)
        """
        fail = (False, -1, '', '', '')

        if not isinstance(external_reply, dict):
            return fail

        try:
            header = external_reply['xqueue_header']
            score_msg = external_reply['xqueue_body']
        except KeyError:
            return fail

        try:
            header_dict = json.loads(header)
        except (TypeError, ValueError):
            return fail

        try:
            score = json.loads(score_msg)
            points_earned = score.get("score")
        except (TypeError, ValueError):
            return fail

        if not isinstance(header_dict, dict):
            return fail

        for tag in ['submission_id', 'submission_key']:
            if tag not in header_dict:
                return fail

        submission_id = int(header_dict['submission_id'])
        submission_key = header_dict['submission_key']

        return (True, submission_id, submission_key, score_msg, points_earned)


    def _compose_reply(self, success, content):
        """
        Compose response in Xqueue format.

        Args:
            success (bool): Whether the operation was successful
            content (str): Response message

        Returns:
            dict: Formatted response
        """
        return {
            'return_code': 0 if success else 1,
            'content': content
        }
