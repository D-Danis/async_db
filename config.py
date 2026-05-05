import datetime as dt
import os

from dotenv import load_dotenv

load_dotenv()


DB_NAME = os.environ.get("DB_NAME")
DB_HOST = os.environ.get("DB_HOST")
DB_PORT = os.environ.get("DB_PORT")
DB_USER = os.environ.get("DB_USER")
DB_PASS = os.environ.get("DB_PASS")


START_URL = "https://spimex.com/markets/oil_products/trades/results/"
START_FROM = dt.datetime(2023, 1, 1).date()
DEST_FOLDER = "data"
MAX_DOWNLOAD_WORKERS = 3
MAX_PROCESS_WORKERS = 4
