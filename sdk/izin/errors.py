class IzinError(Exception):
  """Base for everything the SDK raises."""

class NonCanonicalValue(IzinError, TypeError):
  """args contains a value that can't be hashed deterministically."""

class SecretInArgs(NonCanonicalValue):
  """args contains something that looks like a credential."""

class InvalidRequest(IzinError):
  """The server rejected the request (unknown action, args fail the schema...)."""

class IdempotencyConflict(IzinError):
  """This idempotency key already belongs to a different request."""

class AuthError(IzinError):
  """Missing or invalid token."""

class IzinUnavailable(IzinError):
  """The server couldn't be reached after retrying."""

class ConcurrentDuplicate(IzinError):
  """An identical request is already in flight in this run."""

class ApprovalTimeout(IzinError):
  """max_wait elapsed. The request is still pending; calling request() again resumes it."""

  def __init__(self, request_id: str):
    super().__init__(f"no decision yet for request {request_id}")
    self.request_id = request_id

class Denied(IzinError):
  """Raised by @requires when a human denies the action."""

  def __init__(self, decision):
    super().__init__(f"denied by {decision.decidec_by}: {decision.comment}")
    self.decision = decision