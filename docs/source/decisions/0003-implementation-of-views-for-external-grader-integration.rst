3. Implementation of XQueue Compatible Views for External Grader Integration
############################################################################

Status
******

**Provisional** *2025-02-21*

Implemented by https://github.com/openedx/edx-submissions/pull/284

Context
*******

Following the creation of ExternalGraderDetail (ADR 1) and SubmissionFile (ADR 2) models, we need to implement the API
endpoints that will allow external graders (XWatcher) to interact with the system. The current XQueue implementation
provides three critical endpoints that need to be replicated:

1. Authentication Service:
   - Secure login mechanism for external graders
   - Session management
   - CSRF handling for specific endpoints

2. Submission Retrieval (get_submission):
   - Queue-based submission distribution
   - Status tracking and locking mechanism
   - File information packaging for graders

3. Result Processing (put_result):
   - Score validation and processing
   - Status updates
   - Error handling and retry mechanisms

The current XQueue implementation has these services spread across multiple systems, requiring complex HTTP communication
and session management. The existing workflow:

1. Authentication Flow:
   - Basic username/password authentication
   - Session-based token management
   - Manual CSRF handling for specific endpoints

2. Submission Processing:
   - Manual queue status checks
   - Complex state transitions
   - Synchronous HTTP-based file retrieval

3. Result Handling:
   - Direct database updates
   - Limited error recovery
   - Complex retry logic

Decision
********

We will implement a ViewSet that consolidates these services while leveraging the new ExternalGraderDetail and
SubmissionFile models:

1. Authentication Layer:
   - Custom SessionAuthentication class for CSRF exemptions
   - Robust session management with explicit cookie handling
   - Secure login/logout endpoints

2. Submission Distribution:
   - Atomic queue-based submission retrieval
   - Integrated file handling through SubmissionFileManager
   - Status tracking with explicit state transitions
   - UUID-based submission keys for security

3. Result Processing:
   - Transactional score updates
   - Integrated retry mechanism
   - Comprehensive error handling
   - Atomic status updates

Key Implementation Details:

1. Session Management:
   ```python
   class XQueueSessionAuthentication(SessionAuthentication):
       def enforce_csrf(self, request):
           if 'put_result' in request.path:
               return None
           return super().enforce_csrf(request)
   ```

2. Submission Retrieval:
   ```python
   @transaction.atomic
   def get_submission(self, request):
       submission_record = ExternalGraderDetail.objects.filter(
           queue_name=queue_name,
           status__in=['pending']
       ).select_related('submission').first()
   ```

3. Score Processing:
   ```python
   set_score(str(submission_record.submission.uuid),
             points_earned,
             max_points)
   ```

Consequences
***********

Positive:
---------

1. Architecture:
   - Consolidated service endpoints
   - Clean separation of concerns
   - Improved error handling
   - Better session management

2. Security:
   - Robust authentication
   - Secure file handling
   - Protected state transitions

3. Operations:
   - Simplified deployment
   - Better monitoring capabilities
   - Improved error visibility
   - Automatic retry handling

Negative:
---------

1. Complexity:
   - More complex session management
   - Additional state validation required
   - Complex transaction handling

2. Performance:
   - Additional database operations
   - Session verification overhead

3. Migration:
   - Changes required in external graders
   - New deployment procedures needed

References
**********

Implementation References:
   * XQueue ViewSet Implementation: Link to PR
   * External Grader Integration Guide: Link to documentation

Related ADRs:
   * ADR 1: Creation of ExternalGraderDetail Model
   * ADR 2: File Handling Implementation

Documentation:
   * XQueue API Specification
   * External Grader Integration Guide
   * Session Management Documentation

Architecture Guidelines:
   * Django REST Framework Best Practices
   * Open edX API Guidelines