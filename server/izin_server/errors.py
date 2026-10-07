class IzinError(Exception):
  status_code = 400
  code = "Bad Request"

  def __init__(self, message: str):
    super().__init__(message)
    self.message = message

class Unauthorized(IzinError):
  status_code = 401
  code = "unauthorized"

class InvalidInput(IzinError):
  status_code = 422
  code = "invalid_input"

class IdempotencyConflict(IzinError):
  status_code = 409
  code = "idempotency_conflict"

class Forbidden(IzinError):
  status_code = 403
  code = "forbidden"

class NotFound(IzinError):
  status_code = 404
  code = "not_found"

class AlreadyDecided(IzinError):
  status_code = 409
  code = "already_decided"

class NotDecided(IzinError):
  status_code = 409
  code = "not_decided"