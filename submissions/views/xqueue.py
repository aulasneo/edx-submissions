"""Xqueue View set"""

from datetime import timedelta
import json
import logging
import uuid

from django.contrib.auth import authenticate, login, logout
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from openedx_events.content_authoring.data import ExternalGraderScoreData
from openedx_events.content_authoring.signals import EXTERNAL_GRADER_SCORE_SUBMITTED
from rest_framework import status, viewsets
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from submissions.api import set_score
from submissions.models import ExternalGraderDetail, SubmissionFileManager

log = logging.getLogger(__name__)


class XQueueSessionAuthentication(SessionAuthentication):
    def enforce_csrf(self, request):
        if 'put_result' in request.path:
            return None
        return super().enforce_csrf(request)


class XqueueViewSet(viewsets.ViewSet):
    """
    A collection of services for xwatcher interactions and authentication.

    This ViewSet provides endpoints for managing external grader results
    and handling user authentication in the system.

    Key features:
    - Handles validation of external grader responses
    - Processes and updates submission scores
    - Provides a secure endpoint for result updates
    - Manages user authentication and session handling

    Endpoints:
    - put_result: Endpoint for graders to submit their assessment results
    - login: Endpoint for user authentication
    - logout: Endpoint for ending user sessions
    """

    authentication_classes = [XQueueSessionAuthentication]

    def get_permissions(self):
        """
        Override to implement custom permission logic per action.
        - Login endpoint is public
        - All other endpoints require authentication
        """
        if self.action == 'login':
            permission_classes = [AllowAny]
        else:
            permission_classes = [IsAuthenticated]
        return [permission() for permission in permission_classes]

    @action(detail=False, methods=['post'], url_name='login')
    def login(self, request):
        """
        Endpoint for authenticating users and creating sessions.
        """
        log.info(f"Login attempt with data: {request.data}")

        if 'username' not in request.data or 'password' not in request.data:
            return Response(
                {'return_code': 1, 'content': 'Insufficient login info'},
                status=status.HTTP_400_BAD_REQUEST
            )

        user = authenticate(
            request,
            username=request.data['username'],
            password=request.data['password']
        )

        if user is not None:
            login(request, user)
            response = Response(
                {'return_code': 0, 'content': 'Logged in'},
                status=status.HTTP_200_OK
            )

            return response

        return Response(
            {'return_code': 1, 'content': 'Incorrect login credentials'},
            status=status.HTTP_401_UNAUTHORIZED
        )

    @action(detail=False, methods=['post'], url_name='logout')
    def logout(self, request):
        """
        Endpoint for ending user sessions.
        """
        logout(request)
        return Response(
            self.compose_reply(True, 'Goodbye'),
            status=status.HTTP_200_OK
        )

    @action(detail=False, methods=['get'], url_name='get_submission')
    @transaction.atomic
    def get_submission(self, request):
        """
        Endpoint for retrieving a single unprocessed submission from a specified queue.
        Query Parameters:
        - queue_name (required): Name of the queue to pull from
        Returns:
        - Submission data with pull information if successful
        - Error message if queue is empty or invalid
        """

        queue_name = request.query_params.get('queue_name')

        if not queue_name:
            return Response(
                self.compose_reply(False, "'get_submission' must provide parameter 'queue_name'"),
                status=status.HTTP_400_BAD_REQUEST
            )

        timeout_threshold = timezone.now() - timedelta(minutes=5)

        # **Bloqueo manual para evitar doble procesamiento**
        with transaction.atomic(): #validar para quitar atomic
            submission_record = (
                ExternalGraderDetail.objects
                .select_for_update()  # Bloquea la fila para evitar que otro proceso la seleccione simultáneamente
                .filter(queue_name=queue_name, status='pending', pullkey__isnull=True) # quitar pullkey__isnull
                .select_related('submission')
                .order_by('status_time')
                .first()
            )

            # Si no hay submissions en 'pending', buscar atascadas en 'pulled'
            if not submission_record: #validar para quitar segunda condicion
                submission_record = (
                    ExternalGraderDetail.objects
                    .select_for_update()
                    .filter(queue_name=queue_name, status='pulled', status_time__lt=timeout_threshold)
                    .order_by('status_time')
                    .first()
                )

                # Si encontramos una submission atascada, la marcamos como 'failed' antes de regresarla a 'pending'
                if submission_record:
                    submission_record.update_status('failed')
                    submission_record.pullkey = None
                    submission_record.save(update_fields=['status', 'status_time', 'pullkey'])

                    # Ahora sí podemos devolverlo a 'pending'
                    submission_record.update_status('pending')
                    submission_record.save(update_fields=['status', 'status_time'])

            if submission_record:
                try:
                    pull_time = timezone.now()
                    pullkey = str(uuid.uuid4())

                    # **Verificar si ya ha sido tomada antes de asignar pullkey**
                    if submission_record.pullkey:
                        return Response(
                            self.compose_reply(False, "Submission already in process"),
                            status=status.HTTP_409_CONFLICT
                        )

                    submission_record.update_status('pulled')
                    submission_record.pullkey = pullkey
                    submission_record.status_time = pull_time
                    submission_record.save(update_fields=['pullkey', 'status_time'])

                    ext_header = {
                        'submission_id': submission_record.submission.id,
                        'submission_key': pullkey
                    }
                    answer = submission_record.submission.answer
                    submission_data = {
                        "grader_payload": json.dumps({"grader": submission_record.grader_file_name}),
                        "student_info": json.dumps({
                            "anonymous_student_id": str(submission_record.submission.student_item.student_id),
                            "submission_time": str(int(submission_record.created_at.timestamp())),
                            "random_seed": 1
                        }),
                        "student_response": answer
                    }

                    file_manager = SubmissionFileManager(submission_record)

                    payload = {
                        'xqueue_header': json.dumps(ext_header),
                        'xqueue_body': json.dumps(submission_data),
                        'xqueue_files': json.dumps(file_manager.get_files_for_grader())
                    }

                    return Response(
                        self.compose_reply(True, content=json.dumps(payload)),
                        status=status.HTTP_200_OK
                    )

                except ValueError as e:
                    return Response(
                        self.compose_reply(False, f"Error processing submission: {str(e)}"),
                        status=status.HTTP_400_BAD_REQUEST
                    )

        return Response(
            self.compose_reply(False, f"Queue '{queue_name}' is empty"),
            status=status.HTTP_404_NOT_FOUND
        )


    @action(detail=False, methods=['post'], url_name='put_result')
    @transaction.atomic
    def put_result(self, request):
        """
        Endpoint for graders to post their results and update submission scores.
        """
        (reply_valid, submission_id, submission_key, score_msg, points_earned) = (
            self.validate_grader_reply(request.data))

        if not reply_valid:
            log.error("Invalid reply from pull-grader: request.data: %s",
                      request.data)
            return Response(
                self.compose_reply(False, 'Incorrect reply format'),
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            submission_record = ExternalGraderDetail.objects.select_for_update(
                                                                        nowait=True
            ).get(submission__id=submission_id)
        except ExternalGraderDetail.DoesNotExist:
            log.error(
                "Grader submission_id refers to nonexistent entry in Submission DB: "
                "grader: %s, submission_key: %s, score_msg: %s",
                submission_id,
                submission_key,
                score_msg
            )
            return Response(
                self.compose_reply(False, 'Submission does not exist'),
                status=status.HTTP_404_NOT_FOUND
            )

        if not submission_record.pullkey or submission_key != submission_record.pullkey:
            log.error(f"Invalid pullkey: submission key from xwatcher {submission_key} "
                      f"and submission key stored {submission_record.pullkey} are different")
            return Response(
                self.compose_reply(False, 'Incorrect key for submission'),
                status=status.HTTP_403_FORBIDDEN
            )

        # pylint: disable=broad-exception-caught
        try:
            log.info("Attempting to set_score...")
            set_score(str(submission_record.submission.uuid),
                      points_earned,
                      1
                      )
            log.info(f"=====> Saving score {score_msg}")
            submission_record.grader_reply = score_msg
            submission_record.status_time = timezone.now()
            submission_record.update_status('retired')
            submission_record.save()
            log.info("=====> Sending score to event bus")

            # Modify the event emission in put_result
            EXTERNAL_GRADER_SCORE_SUBMITTED.send_event(
                score=ExternalGraderScoreData(
                    points_possible=submission_record.points_possible,
                    points_earned=points_earned,
                    course_id=submission_record.submission.student_item.course_id,
                    score_msg=score_msg,
                    submission_id=submission_id,
                    # Extract these from the submission
                    user_id=submission_record.submission.student_item.student_id,
                    module_id=submission_record.submission.student_item.item_id,
                    queue_key=submission_record.queue_key,
                    queue_name=submission_record.queue_name
                )
            )
            log.info("Score event sent to bus successfully <=====")
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

        return Response(self.compose_reply(success=True, content=''))

    def validate_grader_reply(self, external_reply):
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

    def compose_reply(self, success, content):
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

    @action(detail=False, methods=['post'], url_name='status')
    def status(self, _):
        return HttpResponse(self.compose_reply(success=True, content='OK'))
