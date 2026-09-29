from sqlalchemy import text
from main import engine
from werkzeug.security import check_password_hash

with engine.connect() as connection:
    database_info = connection.execute(
        text("SELECT current_database(), current_user")
    ).fetchone()

    password_hash = connection.execute(
        text('SELECT password_hash FROM "user" WHERE email = :email'),
        {"email": "operator.e2e@pef.local"},
    ).scalar_one()

print("DATABASE:", database_info)
print("PASSWORD VALID:", check_password_hash(password_hash, "E2eTest#2026"))