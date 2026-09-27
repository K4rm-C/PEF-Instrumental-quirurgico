from flask import Flask, g, request

from controllers import web_bp
from config import Config
from extensions import db


def create_app():
	app = Flask(__name__)
	app.config.from_object(Config)
	db.init_app(app)
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
