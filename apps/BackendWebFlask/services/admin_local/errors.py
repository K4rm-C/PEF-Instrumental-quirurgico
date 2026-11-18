class ApiError(Exception):
    def __init__(self, status, code, message, field=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.field = field

    def to_dict(self):
        body = {'code': self.code, 'message': self.message}
        if self.field:
            body['field'] = self.field
        return {'error': body}


def bad_request(code, message, field=None):
    return ApiError(400, code, message, field)


def not_found(code='not_found', message='Record not found.'):
    return ApiError(404, code, message)


def conflict(code, message, field=None):
    return ApiError(409, code, message, field)
