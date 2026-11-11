"""Configuration owned by BackendWebFlask (not a separate AdminService)."""
import os
from config import Config as WebConfig


class Config:
    DISPLAY_TIMEZONE = WebConfig.ADMIN_DISPLAY_TIMEZONE
    MODEL_WEIGHTS_BUCKET = os.getenv('ADMIN_MODEL_WEIGHTS_BUCKET', 'models')
