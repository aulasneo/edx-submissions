"""Xqueue View set"""

import json
import logging

from django.conf import settings
from django.contrib.sessions.backends.cache import SessionStore
from django.http import HttpResponse
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.db import transaction
from django.utils import timezone
from django.contrib.auth import authenticate, login, logout

from submissions.api import set_score
from submissions.models import SubmissionQueueRecord
from openedx.core.djangolib.default_auth_classes import DefaultSessionAuthentication
from openedx.core.lib.session_serializers import PickleSerializer
from django.contrib.auth import get_user_model

log = logging.getLogger(__name__)


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

    authentication_classes = [DefaultSessionAuthentication]

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
        log.info(f"Headers: {request.headers}")

        if hasattr(request, 'session'):
            cookie_header = request.headers.get('Cookie', '')
            log.info(f"Cookie header: {cookie_header}")

            cookies = {}
            if cookie_header:
                for cookie in cookie_header.split(';'):
                    if '=' in cookie:
                        name, value = cookie.strip().split('=', 1)
                        cookies[name] = value

            log.info(f"Cookies parsed: {cookies}")
            session_key = cookies.get('sessionid') or cookies.get('lms_sessionid')

            if session_key:
                try:
                    new_session = SessionStore(session_key=session_key)
                    new_session.serializer = PickleSerializer

                    if new_session.load():
                        request.session = new_session
                        if '_auth_user_id' in request.session:
                            User = get_user_model()
                            request.user = User.objects.get(pk=request.session['_auth_user_id'])

                        log.info(f"Request user: {request.user}")
                except Exception as e:
                    log.error(f"Error loading session: {str(e)}")

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

            log.info(f"Session after login: {request.session.session_key}")
            log.info(f"Session cookie name: {settings.SESSION_COOKIE_NAME}")
            log.info(f"Session cookie domain: {settings.SESSION_COOKIE_DOMAIN}")

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
