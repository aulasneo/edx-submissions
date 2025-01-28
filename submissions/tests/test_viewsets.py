"""
Tests for XQueue API views.
"""
import json
from unittest.mock import patch
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from django.test import override_settings
from django.utils import timezone
import uuid

from submissions.models import SubmissionQueueRecord
from submissions.tests.factories import SubmissionFactory, SubmissionQueueRecordFactory


@override_settings(ROOT_URLCONF='submissions.urls')
class TestXqueueViewSet(APITestCase):
    """
    Test cases for XqueueViewSet endpoints.
    """

    def setUp(self):
        """Set up test data."""
        super().setUp()
        self.submission = SubmissionFactory()
        self.queue_record = SubmissionQueueRecordFactory(
            submission=self.submission,
            pullkey='test_pull_key',
            status='pending',
            num_failures=0
        )
        self.url = reverse('xqueue-put_result')
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
            mock_set_score.return_value = True
            self.client.post(self.url, payload, format='json')

        mock_log.info.assert_called_with(
            "Successfully updated submission score for submission %s",
            self.submission.id
        )
