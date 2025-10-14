import psycopg2
from psycopg2 import OperationalError
from dotenv import load_dotenv
import os

# Load environment variables from the .env file
load_dotenv()

# Get database connection parameters from the environment variables
DATABASE_USER = os.getenv("DATABASE_USER")
DATABASE_PASSWORD = os.getenv("DATABASE_PASSWORD")
DATABASE_HOST = os.getenv("DATABASE_HOST")
DATABASE_PORT = os.getenv("DATABASE_PORT")
DATABASE_NAME = os.getenv("DATABASE_NAME")


# Function to check the connection

def check_database_connection():
    try:
        # Establish a connection to the database
        conn = psycopg2.connect(
            dbname='postgres',
            user=DATABASE_USER,
            password=DATABASE_PASSWORD,
            host=DATABASE_HOST,
            port=DATABASE_PORT
        )

        # If connection is successful
        print("Connection to the database was successful!")
        conn.close()  # Close the connection

    except OperationalError as e:
        # If connection fails
        print(f"Failed to connect to the database: {e}")


# Run the function
if __name__ == "__main__":
    check_database_connection()
