from flask import Flask, g, jsonify, request
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

import audit
import security
from api import api
from config import Config
from database import db
from errors import ApiError


def create_app():
    app = Flask(__name__)
    app.json.sort_keys = False
    app.register_blueprint(api)

    @app.before_request
    def assign_correlation_id():
        audit.correlation_id()

    @app.after_request
    def add_correlation_header(response):
        if getattr(g, 'correlation_id', None):
            response.headers['X-Request-ID'] = g.correlation_id
        return response

    @app.teardown_appcontext
    def remove_session(exception=None):
        if exception is not None:
            db.rollback()
        db.remove()

    @app.errorhandler(ApiError)
    def api_error(error):
        db.rollback()
        return jsonify(error.to_dict()), error.status

    @app.errorhandler(SQLAlchemyError)
    def database_error(error):
        db.rollback()
        app.logger.exception('Database error', exc_info=error)
        return jsonify(ApiError(503, 'database_unavailable', 'Database unavailable.').to_dict()), 503

    @app.errorhandler(404)
    def route_not_found(error):
        return jsonify(ApiError(404, 'route_not_found', f'No route for {request.path}.').to_dict()), 404

    @app.errorhandler(405)
    def method_not_allowed(error):
        return jsonify(ApiError(405, 'method_not_allowed', 'Method not allowed.').to_dict()), 405

    @app.get('/health')
    def health():
        checks = {'postgres': False, 'redis': False}
        try:
            db.execute(text('SELECT 1'))
            checks['postgres'] = True
        except SQLAlchemyError:
            db.rollback()
        try:
            checks['redis'] = bool(security._redis().ping())
        except RedisError:
            pass
        healthy = all(checks.values())
        return jsonify({'ok': healthy, 'service': 'admin', 'checks': checks}), 200 if healthy else 503

    return app


app = create_app()


if __name__ == '__main__':
    if Config.JWT_SECRET_KEY == 'change-this-secret':
        raise RuntimeError('JWT_SECRET_KEY must be configured (same value as BackendAuthService).')
    app.run(host=Config.HOST, port=Config.PORT, debug=False)
