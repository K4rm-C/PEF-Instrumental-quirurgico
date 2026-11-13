"""Share the portal's SQLAlchemy session and transaction boundaries.

Do NOT construct a second engine or create another database connection here.
The copied Admin mappings use their own metadata for table declarations, but
all queries run inside Flask-SQLAlchemy's normal request-scoped session.
"""
from extensions import db as web_db

db = web_db.session
