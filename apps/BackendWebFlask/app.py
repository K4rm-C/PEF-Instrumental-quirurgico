from flask import Flask

from controllers import web_bp
from config import Config
from extensions import db


def create_app():
	app = Flask(__name__)
	app.config.from_object(Config)
	db.init_app(app)
	app.register_blueprint(web_bp)
	return app


app = create_app()


if __name__ == '__main__':
	app.run(debug=True)
