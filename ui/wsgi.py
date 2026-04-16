import os

from dotenv import load_dotenv

from ui.app import create_app


load_dotenv()

app = create_app(
    bucket=os.getenv("S3_BUCKET"),
    prefix=os.getenv("S3_PREFIX", "politifact"),
    region=os.getenv("AWS_REGION"),
    backend=os.getenv("STORAGE_BACKEND", "auto"),
    local_dir=os.getenv("LOCAL_DATA_DIR", "data"),
)
