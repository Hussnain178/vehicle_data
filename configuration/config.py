from dotenv import load_dotenv
import os

load_dotenv()


class Config:
    WEBSHARE_PROXY_USER = os.getenv('WEBSHARE_PROXY_USER')
    WEBSHARE_PROXY_PASSWORD = os.getenv("WEBSHARE_PROXY_PASSWORD")
    WEBSHARE_PROXY_HOST = os.getenv('WEBSHARE_PROXY_HOST')
    WEBSHARE_PROXY_PORT = os.getenv("WEBSHARE_PROXY_PORT")

    DATABASE_USER = os.getenv('DATABASE_USER')
    DATABASE_PASSWORD = os.getenv('DATABASE_PASSWORD')
    DATABASE_HOST = os.getenv('DATABASE_HOST')
    DATABASE_PORT = os.getenv('DATABASE_PORT')
    DATABASE_NAME = os.getenv('DATABASE_NAME')

    SCRAPE_DO_TOKEN = os.getenv('SCRAPE_DO_TOKEN')
