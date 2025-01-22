from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db import transaction
from django.utils import timezone
import json

import logging

from submissions.api import set_score
from submissions.errors import SubmissionInternalError, SubmissionRequestError
from submissions.models import Submission

log = logging.getLogger(__name__)


class GraderViewSet(viewsets.ViewSet):
    # TODO: permission_classes = [IsAuthenticated]

    def set_score(self, submission, grader_reply, submission_id):
        try:
            score_data = json.loads(grader_reply)
            points_earned = score_data.get('points_earned')
            points_possible = score_data.get('points_possible')

            if points_earned is None or points_possible is None:
                raise ValueError("Missing required score information")

            set_score(
                submission_uuid=submission.uuid,
                points_earned=points_earned,
                points_possible=points_possible,
                annotation_type='grader_response',
            )

            submission.retired = True
            submission.save()

            return Response(
                self._compose_reply(success=True, content=''),
                status=status.HTTP_200_OK
            )

        except (ValueError, json.JSONDecodeError) as e:
            log.error(
                "Invalid grader reply format: %s, submission_id: %s, error: %s",
                grader_reply,
                submission_id,
                str(e)
            )
            return Response(
                self._compose_reply(False, 'Invalid grader reply format'),
                status=status.HTTP_400_BAD_REQUEST
            )
        except (SubmissionInternalError, SubmissionRequestError) as e:
            log.error(
                "Error setting submission score: %s, submission_id: %s, error: %s",
                grader_reply,
                submission_id,
                str(e)
            )
            return Response(
                self._compose_reply(False, 'Error setting submission score'),
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['post'])
    @transaction.atomic
    def put_result(self, request):
        """
        Endpoint for graders to post their results and update submission scores.
        """
        reply_valid, submission_id, submission_key, grader_reply = self._validate_grader_reply(request.data)

        if not reply_valid:
            log.error("Invalid reply from pull-grader: request.data: %s",
                      request.data)
            return Response(
                self._compose_reply(False, 'Incorrect reply format'),
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            submission = Submission.objects.select_for_update().get(id=submission_id)
        except Submission.DoesNotExist:
            log.error(
                "Grader submission_id refers to nonexistent entry in Submission DB: "
                "grader: %s, submission_key: %s, grader_reply: %s",
                submission_id,
                submission_key,
                grader_reply
            )
            return Response(
                self._compose_reply(False, 'Submission does not exist'),
                status=status.HTTP_404_NOT_FOUND
            )

        if not submission.pullkey or submission_key != submission.pullkey:
            return Response(
                self._compose_reply(False, 'Incorrect key for submission'),
                status=status.HTTP_403_FORBIDDEN
            )

        # Update submission with grader results
        submission.return_time = timezone.now()
        submission.grader_reply = grader_reply


        return self.set_score(submission, submission_id, grader_reply)


    def _validate_grader_reply(self, external_reply):
        """
        Validate the format of external grader reply.

        Returns:
            tuple: (is_valid, submission_id, submission_key, score_msg)
        """
        fail = (False, -1, '', '')

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

        if not isinstance(header_dict, dict):
            return fail

        for tag in ['submission_id', 'submission_key']:
            if tag not in header_dict:
                return fail

        submission_id = int(header_dict['submission_id'])
        submission_key = header_dict['submission_key']

        return (True, submission_id, submission_key, score_msg)


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