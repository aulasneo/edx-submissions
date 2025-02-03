"""Xqueue View set"""

import json
import logging
import re
import uuid

from django.conf import settings
from django.contrib.sessions.backends.cache import SessionStore
from django.contrib.sessions.serializers import JSONSerializer
from django.http import HttpResponse
from rest_framework import viewsets, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.db import transaction
from django.utils import timezone
from django.contrib.auth import authenticate, login, logout, get_user_model

from submissions.api import set_score
from submissions.models import SubmissionQueueRecord

log = logging.getLogger(__name__)


class XQueueSessionAuthentication(SessionAuthentication):
    def enforce_csrf(self, request):
        if 'put_result' in request.path:
            return None  # No aplicar CSRF para put_result
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


    def dispatch(self, request, *args, **kwargs):
        """
        Ensures the request has a valid session before processing it by handling session validation,
        cookie parsing, and user authentication.

        This method extends the base dispatch functionality to provide session management and
        user authentication by:
            1. Extracting and parsing cookies from the request headers
            2. Retrieving the session ID from either 'sessionid' or 'lms_sessionid' cookies
            3. Loading the session using Django's SessionStore
            4. Authenticating and loading the user if session contains auth data

        Args:
            request (HttpRequest): The incoming HTTP request object
            *args: Variable length argument list passed to parent dispatch
            **kwargs: Arbitrary keyword arguments passed to parent dispatch

        Returns:
            HttpResponse: The response from the parent dispatch method

        Side Effects:
            - Sets request.session if a valid session is found
            - Sets request.user if authentication data is present in session

        Raises:
            No explicit exceptions are raised, but logs any errors during session loading

        Example:
            This method is typically not called directly but is part of the request
            processing pipeline:

            ```
            class MyView(View):
                def dispatch(self, request, *args, **kwargs):
                    # This will ensure session validation before processing
                    return super().dispatch(request, *args, **kwargs)
            ```

        Note:
            - Uses PickleSerializer for session deserialization
            - Logs various debug information about headers, cookies, and session loading
            - Falls back to parent dispatch even if session loading fails
        """

        log.info("Dispatching request")

        if hasattr(request, 'session'):
            cookie_header = request.headers.get('Cookie', '')
            cookies = {}
            if cookie_header:
                for cookie in cookie_header.split(';'):
                    if '=' in cookie:
                        name, value = cookie.strip().split('=', 1)
                        cookies[name] = value

            session_key = cookies.get('sessionid') or cookies.get('lms_sessionid')

            if session_key:
                try:
                    if not re.match(r'^[a-zA-Z0-9]{32,}$', session_key):
                        log.warning("Invalid session key format")
                        return super().dispatch(request, *args, **kwargs)

                    new_session = SessionStore(session_key=session_key)
                    new_session.serializer = JSONSerializer

                    if not new_session.load():
                        log.warning("Invalid session")
                        return super().dispatch(request, *args, **kwargs)

                    if new_session.get_expiry_age() <= 0:
                        log.warning("Expired session")
                        return super().dispatch(request, *args, **kwargs)

                    request.session = new_session

                    if '_auth_user_id' in request.session:
                        try:
                            User = get_user_model()
                            request.user = User.objects.get(
                                pk=request.session['_auth_user_id']
                            )
                        except User.DoesNotExist:
                            log.warning("User not found for session")
                            return super().dispatch(request, *args, **kwargs)

                except Exception as e:
                    log.error(f"Session validation error: {type(e).__name__}")
                    if settings.DEBUG:
                        log.error(str(e))

        return super().dispatch(request, *args, **kwargs)


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
            log.info(f"Response headers: {response.headers}")
            response["Set-Cookie"] = f"sessionid={request.session.session_key}; HttpOnly; Path=/"

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
            self._compose_reply(True, 'Goodbye'),
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
                self._compose_reply(False, "'get_submission' must provide parameter 'queue_name'"),
                status=status.HTTP_400_BAD_REQUEST
            )

        # if queue_name not in settings.XQUEUES:
        # TODO: Define how to set this variable, maybe as a tutor config or hardcoded en edx platform
        #if queue_name not in {'my_course_queue': 'http://172.16.0.220:8125', 'test-pull': None}:
        #    return Response(
        #        {'success': False, 'message': f"Queue '{queue_name}' not found"},
        #        status=status.HTTP_404_NOT_FOUND
        #    )

        submission_record = SubmissionQueueRecord.objects.filter(
            queue_name=queue_name,
            status__in=['pending']
        ).select_related('submission').order_by('status_time').first()

        if not submission_record:
            return Response(
                self._compose_reply(False, f"Queue '{queue_name}' is empty"),
                status=status.HTTP_404_NOT_FOUND
            )

        pull_time = timezone.now()
        pullkey = str(uuid.uuid4())
        # grader_id = request.META.get('REMOTE_ADDR', '')

        try:
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
                "grader_payload": json.dumps({
                    "grader": submission_record.grader_file_name,

                }),
                "student_info": json.dumps({
                    "anonymous_student_id": str(submission_record.submission.uuid),
                    "submission_time": str(int(submission_record.created_at.timestamp())),
                    "random_seed": 1
                }),
                "student_response": answer
            }

            payload = {
                'xqueue_header': json.dumps(ext_header),
                'xqueue_body': json.dumps(submission_data),
                'xqueue_files': json.dumps(submission_record.submission.get_files())
                if hasattr(submission_record.submission, 'get_files') else '{}'
            }

            return Response(
                self._compose_reply(True, content=json.dumps(payload)),
                status=status.HTTP_200_OK
            )

        except ValueError as e:
            return Response(
                self._compose_reply(False, f"Error processing submission: {str(e)}"),
                status=status.HTTP_400_BAD_REQUEST
            )

        except Exception as e:
            submission_record.update_status('failed')
            log.error(f"Error processing submission: {str(e)}")
            return Response(
                self._compose_reply(False, "Error processing submission"),
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


    def get_files(self):
        """
        Helper method to get submission files if they exist.
        Can be implemented based on your specific file handling needs.
        """
        # Implement your file handling logic here
        return {}


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
            log.info(f"Attempting to set_score...")
            set_score(str(submission_record.submission.uuid),
                      points_earned,
                      submission_record.points_possible,
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

        return Response(self._compose_reply(success=True, content=''))


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

    @action(detail=False, methods=['post'], url_name='status')
    def status(self, request):
        return HttpResponse(self._compose_reply(success=True, content='OK'))

