import psycopg2
from psycopg2 import sql
import logging
import os
import dotenv

dotenv.load_dotenv('.env')
INSTANCE_CONNECTION_NAME = os.getenv("INSTANCE_CONNECTION_NAME")
HOST= f"/cloudsql/{INSTANCE_CONNECTION_NAME}"
# HOST = os.getenv('DB_HOST')
DBNAME = os.getenv('DB_NAME')
USER = os.getenv('DB_USER')
PASSWORD = os.getenv('DB_PASSWORD')
PORT = os.getenv('DB_PORT')

class DatabaseManager:
    def __init__(self):
        """
        Initializes the database connection directly in the module. 
        You should modify the connection details (host, dbname, user, password) before running the script.
        """
        # Database connection parameters
        self.host = HOST          # host
        self.dbname = DBNAME      # database name
        self.user = USER          # username
        self.password = PASSWORD  # password
        self.port = PORT          # port

        # Initialize connection and cursor as None
        self.connection = None
        self.cursor = None

        # Establish connection
        self.connect()

    def connect(self):
        """Establishes the connection to the PostgreSQL database."""
        try:
            # Connecting to the database using psycopg2
            self.connection = psycopg2.connect(
                host=self.host,
                dbname=self.dbname,
                user=self.user,
                password=self.password,
                port=self.port
            )
            # Creating a cursor object to execute queries
            self.cursor = self.connection.cursor()
            logging.info("Database connection established successfully.")
        except Exception as e:
            logging.error(f"Failed to connect to the database: {e}")
            raise

    def execute_query(self, query, params=None):
        """Executes a SQL query on the database."""
        try:
            if params:
                self.cursor.execute(query, params)
            else:
                self.cursor.execute(query)
            # Commit the transaction to the database
            self.connection.commit()
            logging.info("Query executed successfully.")
        except Exception as e:
            logging.error(f"Error executing query: {e}")
            # Rollback in case of an error
            self.connection.rollback()

    def fetch_one(self, query, params=None):
        """Fetches a single result from a query."""
        try:
            if params:
                self.cursor.execute(query, params)
            else:
                self.cursor.execute(query)
            # Returns the first row of the result
            return self.cursor.fetchone()
        except Exception as e:
            logging.error(f"Error fetching data: {e}")
            return None

    def fetch_all(self, query, params=None):
        """Fetches all results from a query."""
        try:
            if params:
                self.cursor.execute(query, params)
            else:
                self.cursor.execute(query)
            # Returns all rows from the result
            return self.cursor.fetchall()
        except Exception as e:
            logging.error(f"Error fetching data: {e}")
            return []

    def close(self):
        """Closes the database connection."""
        if self.cursor:
            self.cursor.close()
        if self.connection:
            self.connection.close()
        logging.info("Database connection closed.")

# Example usage
if __name__ == "__main__":
    # Creating an instance of DatabaseManager, which will connect to the database
    db = DatabaseManager()

    # # Create a table if it doesn't exist
    # db.execute_query("CREATE TABLE IF NOT EXISTS employees (id SERIAL PRIMARY KEY, first_name VARCHAR(50), last_name VARCHAR(50));")

    # # Insert some data into the table
    # db.execute_query("INSERT INTO employees (first_name, last_name) VALUES (%s, %s);", ("John", "Doe"))

    # Fetch all data from the table
    result = db.fetch_all("SELECT * FROM employees;")
    print(result)

    # Close the database connection after operations
    db.close()
