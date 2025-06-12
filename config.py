from dotenv import load_dotenv
import os

load_dotenv('.env')
SECRET_KEY = os.getenv('SECURITY_KEY')
EMAIL = os.getenv('EMAIL')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')
ALGORITHM = "HS256"
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.getenv(
    "GOOGLE_APPLICATION_CREDENTIALS", "service_account_key.json"
)

BUCKET_NAME = "arch_oprog_photos"
IMAGE_BASE_URL = f"https://storage.googleapis.com/{BUCKET_NAME}/"