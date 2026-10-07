from flask import Flask, g, request

from controllers import web_bp
from config import Config
from extensions import babel, db
from i18n import (
	LOCALE_COOKIE,
	LOCALE_COOKIE_MAX_AGE,
	install_jinja_gettext,
	resolve_locale,
	select_locale,
)


def create_app():
	app = Flask(__name__)
	app.config.from_object(Config)
	db.init_app(app)
	babel.init_app(app, locale_selector=select_locale)
	install_jinja_gettext(app)
	app.register_blueprint(web_bp)

	@app.before_request
	def log_request_start():
		app.logger.info('Request started: %s %s', request.method, request.path)

	@app.after_request
	def log_request_end(response):
		for set_cookie in getattr(g, 'auth_set_cookies', ()):
			response.headers.add('Set-Cookie', set_cookie)
		# DS01: after login user.ui_preferences wins, so keep the guest cookie in sync with it.
		user = getattr(g, 'verified_user', None)
		if user and not getattr(g, 'locale_cookie_set', False):
			locale = resolve_locale(user)
			if request.cookies.get(LOCALE_COOKIE) != locale:
				response.set_cookie(
					LOCALE_COOKIE, locale, max_age=LOCALE_COOKIE_MAX_AGE, path='/', samesite='Lax',
				)
		app.logger.info('Request finished: %s %s -> %s', request.method, request.path, response.status_code)
		return response

	return app


app = create_app()


if __name__ == '__main__':
	app.run(debug=True)
