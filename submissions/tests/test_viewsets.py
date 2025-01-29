"""
Tests for XQueue API views.
"""
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.test import APITestCase
from django.test import override_settings
from django.utils import timezone
import uuid

from submissions.models import SubmissionQueueRecord
from submissions.tests.factories import SubmissionFactory, SubmissionQueueRecordFactory
from submissions.views.xqueue import XqueueViewSet

User = get_user_model()

@override_settings(ROOT_URLCONF='submissions.urls')
class TestXqueueViewSet(APITestCase):
    """
    Test cases for XqueueViewSet endpoints.
    """

    def setUp(self):
        """Set up test data."""
        super().setUp()
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass'
        )
        self.submission = SubmissionFactory()
        self.queue_record = SubmissionQueueRecordFactory(
            submission=self.submission,
            pullkey='test_pull_key',
            status='pending',
            num_failures=0
        )
        self.url = reverse('xqueue-put_result')
        self.url_login = reverse('xqueue-login')
        self.url_logout = reverse('xqueue-logout')
        self.url_status = reverse('xqueue-status')
        self.get_submission_url = reverse('xqueue-get_submission')
        
    def test_get_submission_missing_queue_name(self):
        """Test error when queue_name parameter is missing."""
        response = self.client.get(self.get_submission_url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data,
            {'success': False, 'message': "'get_submission' must provide parameter 'queue_name'"}
        )

    def test_get_submission_queue_empty(self):
        """Test error when the specified queue is empty."""
        queue_name = 'empty_queue'
        # Asegurar que la cola esté vacía
        SubmissionQueueRecord.objects.filter(queue_name=queue_name).delete()
        response = self.client.get(self.get_submission_url, {'queue_name': queue_name})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            response.data,
            {'success': False, 'message': f"Queue '{queue_name}' is empty"}
        )

    @patch('submissions.views.xqueue.timezone.now', return_value=timezone.now())
    @patch('submissions.views.xqueue.uuid.uuid4', return_value=str(uuid.uuid4()))
    def test_get_submission_success(self, mock_uuid, mock_now):
        """Test successfully retrieving a submission from the queue."""
        queue_name = 'prueba'
        new_submission = SubmissionFactory()
        submission_queue_record = SubmissionQueueRecordFactory(
            queue_name=queue_name,
            status='pending',
            submission=new_submission
        )

        response = self.client.get(self.get_submission_url, {'queue_name': queue_name})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        content = response.data['content']

        # Validar contenido de la respuesta
        xqueue_header = json.loads(content['xqueue_header'])
        self.assertEqual(xqueue_header['submission_id'], new_submission.id)
        self.assertEqual(xqueue_header['submission_key'], str(mock_uuid.return_value))
        self.assertEqual(content['xqueue_body'], new_submission.answer)
        self.assertEqual(content['xqueue_files'], '{}')

        # Verificar cambios en el registro
        submission_queue_record.refresh_from_db()
        self.assertEqual(submission_queue_record.status, 'pulled')
        self.assertEqual(submission_queue_record.pullkey, str(mock_uuid.return_value))
        self.assertIsNotNone(submission_queue_record.status_time)

    @patch('submissions.views.xqueue.SubmissionQueueRecord.update_status', side_effect=ValueError('Invalid transition'))
    def test_get_submission_invalid_transition(self, mock_update_status):
        """
        Test get_submission when there is an invalid state transition (ValueError).
        """
        queue_name = 'prueba'
        new_submission = SubmissionFactory()
        SubmissionQueueRecordFactory(
            submission=new_submission,
            queue_name=queue_name,
            status='failed'  # Estado que no permite transición a 'pulled'
        )

        response = self.client.get(self.get_submission_url, {'queue_name': queue_name})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data,
            {'success': False, 'message': "Error processing submission: Invalid transition"}
        )

    def test_put_result_success(self):
        """
        Test successful grade submission through put_result endpoint.
        """
        self.client.login(username='testuser', password='testpass')
        response = self.client.post(self.url_status)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        score_data = {
            'score': 1
        }
        payload = {
            'xqueue_header': json.dumps({
                'submission_id': self.submission.id,
                'submission_key': 'test_pull_key'
            }),
            'xqueue_body': json.dumps(score_data)
        }

        response = self.client.post(self.url, payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_content = response.content.decode('utf-8')
        self.assertEqual(response_content, 'return_codecontent')

        updated_queue_record = SubmissionQueueRecord.objects.get(id=self.queue_record.id)
        self.assertEqual(updated_queue_record.status, 'returned')
        self.assertEqual(updated_queue_record.grader_reply, json.dumps(score_data))

    def test_put_result_invalid_submission_id(self):
        """
        Test put_result with non-existent submission ID.
        """
        self.client.login(username='testuser', password='testpass')
        response = self.client.post(self.url_status)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = {
            'xqueue_header': json.dumps({
                'submission_id': 99999,
                'submission_key': 'test_pull_key'
            }),
            'xqueue_body': json.dumps({'score': 0.8})
        }

        response = self.client.post(self.url, payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        response_data = json.loads(response.content)
        self.assertEqual(response_data['return_code'], 1)
        self.assertEqual(response_data['content'], 'Submission does not exist')

    def test_put_result_invalid_key(self):
        """
        Test put_result with incorrect submission key.
        """
        self.client.login(username='testuser', password='testpass')
        response = self.client.post(self.url_status)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = {
            'xqueue_header': json.dumps({
                'submission_id': self.submission.id,
                'submission_key': 'wrong_key'
            }),
            'xqueue_body': json.dumps({'score': 0.8})
        }

        response = self.client.post(self.url, payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        response_data = json.loads(response.content)
        self.assertEqual(response_data['return_code'], 1)
        self.assertEqual(response_data['content'], 'Incorrect key for submission')

    def test_put_result_invalid_format(self):
        """
        Test put_result with malformed request data.
        """
        self.client.login(username='testuser', password='testpass')
        response = self.client.post(self.url_status)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        invalid_payloads = [
            {},
            {'xqueue_header': 'not_json'},
            {'xqueue_header': '{}', 'xqueue_body': 'not_json'},
            {'xqueue_body': '{}'},
            {'xqueue_header': '{}'},
        ]

        for payload in invalid_payloads:
            response = self.client.post(self.url, payload, format='json')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            response_data = json.loads(response.content)
            self.assertEqual(response_data['return_code'], 1)
            self.assertEqual(response_data['content'], 'Incorrect reply format')

    def test_put_result_set_score_failure(self):
        """
        Test put_result handling when set_score fails.
        """
        self.client.login(username='testuser', password='testpass')
        response = self.client.post(self.url_status)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = {
            'xqueue_header': json.dumps({
                'submission_id': self.submission.id,
                'submission_key': 'test_pull_key'
            }),
            'xqueue_body': json.dumps({'score': 0.8})
        }

        with patch('submissions.api.set_score') as mock_set_score:
            mock_set_score.side_effect = Exception('Test error')
            response = self.client.post(self.url, payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.queue_record.refresh_from_db()
        self.assertEqual(self.queue_record.num_failures, 1)
        self.assertEqual(self.queue_record.status, 'pending')

    def test_put_result_auto_retire(self):
        """
        Test submission auto-retirement after too many failures.
        """
        self.client.login(username='testuser', password='testpass')
        response = self.client.post(self.url_status)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        initial_failures = 29
        self.queue_record.num_failures = initial_failures
        self.queue_record.save()

        payload = {
            'xqueue_header': json.dumps({
                'submission_id': self.submission.id,
                'submission_key': 'test_pull_key'
            }),
            'xqueue_body': json.dumps({'score': 8})
        }

        for _ in range(2):
            with patch('submissions.api.set_score') as mock_set_score:
                mock_set_score.side_effect = Exception('Test error')
                _ = self.client.post(self.url, payload, format='json')
                self.queue_record.refresh_from_db()

        self.queue_record.refresh_from_db()

    @patch('submissions.views.xqueue.log')
    def test_logging(self, mock_log):
        """
        Test that appropriate logging occurs in various scenarios.
        """
        payload = {
            'xqueue_header': json.dumps({
                'submission_id': self.submission.id,
                'submission_key': 'test_pull_key'
            }),
            'xqueue_body': json.dumps({'score': 8})
        }

        with patch('submissions.api.set_score') as mock_set_score:
            self.client.login(username='testuser', password='testpass')
            response = self.client.post(self.url_status)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            mock_set_score.return_value = True
            self.client.post(self.url, payload, format='json')

        mock_log.info.assert_called_with(
            "Successfully updated submission score for submission %s",
            self.submission.id
        )

    def test_get_permissions_login(self):
        """Test permissions for login endpoint"""
        viewset = XqueueViewSet()
        viewset.action = 'login'
        permissions = viewset.get_permissions()
        self.assertTrue(any(isinstance(p, AllowAny) for p in permissions))

    def test_get_permissions_other_actions(self):
        """Test permissions for non-login endpoints"""
        viewset = XqueueViewSet()
        viewset.action = 'logout'
        permissions = viewset.get_permissions()
        self.assertTrue(all(isinstance(p, IsAuthenticated) for p in permissions))

    def test_dispatch_valid_session(self):
        """Test dispatch with valid session"""
        self.client.login(username='testuser', password='testpass')
        response = self.client.post(self.url_status)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_dispatch_invalid_session(self):
        """Test dispatch with invalid session"""
        # Create invalid session cookie
        self.client.cookies['sessionid'] = 'invalid_session_id'
        response = self.client.get(self.url_status)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_login_success(self):
        """Test successful login"""
        data = {
            'username': 'testuser',
            'password': 'testpass'
        }
        response = self.client.post(self.url_login, data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['return_code'], 0)
        self.assertTrue('sessionid' in response.cookies)

    def test_login_missing_credentials(self):
        """Test login with missing credentials"""
        response = self.client.post(self.url_login, {})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['return_code'], 1)

    def test_login_invalid_credentials(self):
        """Test login with invalid credentials"""
        data = {
            'username': 'testuser',
            'password': 'wrongpass'
        }
        response = self.client.post(self.url_login, data)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data['return_code'], 1)

    def test_logout(self):
        """Test logout functionality"""
        self.client.login(username='testuser', password='testpass')
        response = self.client.post(self.url_logout)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['return_code'], 0)

    def test_validate_grader_reply_valid(self):
        """Test _validate_grader_reply with valid data"""
        viewset = XqueueViewSet()
        external_reply = {
            'xqueue_header': json.dumps({
                'submission_id': 123,
                'submission_key': 'test_key'
            }),
            'xqueue_body': json.dumps({
                'score': 0.8
            })
        }
        valid, sub_id, sub_key, score_msg, points = viewset._validate_grader_reply(external_reply)
        self.assertTrue(valid)
        self.assertEqual(sub_id, 123)
        self.assertEqual(sub_key, 'test_key')
        self.assertEqual(points, 0.8)

    def test_validate_grader_reply_invalid(self):
        """Test _validate_grader_reply with invalid data"""
        viewset = XqueueViewSet()
        invalid_replies = [
            None,
            {},
            {'xqueue_header': 'invalid_json'},
            {'xqueue_header': '{}', 'xqueue_body': 'invalid_json'},
            {'xqueue_header': json.dumps({}), 'xqueue_body': '{}'}
        ]
        for reply in invalid_replies:
            valid, *_ = viewset._validate_grader_reply(reply)
            self.assertFalse(valid)

    def test_compose_reply(self):
        """Test _compose_reply method"""
        viewset = XqueueViewSet()
        success_reply = viewset._compose_reply(True, "Success message")
        self.assertEqual(success_reply['return_code'], 0)
        self.assertEqual(success_reply['content'], "Success message")

        error_reply = viewset._compose_reply(False, "Error message")
        self.assertEqual(error_reply['return_code'], 1)
        self.assertEqual(error_reply['content'], "Error message")

    def test_status_endpoint(self):
        """Test status endpoint"""
        self.client.login(username='testuser', password='testpass')
        response = self.client.post(self.url_status)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        content = response.content.decode('utf-8')
        self.assertIn('return_code', content)
        self.assertIn('content', content)