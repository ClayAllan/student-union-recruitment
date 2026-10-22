class APIError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)


def require(condition, message='无权执行此操作。', status=403):
    if not condition:
        raise APIError(status, message)
