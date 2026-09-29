from flask import Flask, g, request

import localization
from controllers import routes, web_bp
from config import Config
from extensions import db


def create_app():
	app = Flask(__name__)
	app.config.from_object(Config)
	db.init_app(app)
	# looked up at call time so tests can replace routes._current_user
	localization.init_app(app, user_loader=lambda: routes._current_user())
	app.register_blueprint(web_bp)

	@app.before_request
	def log_request_start():
		app.logger.info('Request started: %s %s', request.method, request.path)

	@app.after_request
	def log_request_end(response):
		for set_cookie in getattr(g, 'auth_set_cookies', ()):
			response.headers.add('Set-Cookie', set_cookie)
		app.logger.info('Request finished: %s %s -> %s', request.method, request.path, response.status_code)
		return response

	return app


app = create_app()


if __name__ == '__main__':
	app.run(debug=True)
