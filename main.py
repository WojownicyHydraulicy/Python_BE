import asyncio
import json
import logging
import os
import traceback
import gc
import psutil
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uuid
import pandas as pd
from lib.db_conn import *

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# .env loading
load_dotenv('.env')
SECURITY_KEY = os.getenv('SECURITY_KEY')



# FastAPI INIT
app = FastAPI()

# FastAPI models
class CustomerRequest(BaseModel):
    name: str
    telephone: str
    city: str
    street: str
    post_code: str
    house_nr: str
    defect_difficulty: str
    description: str
    security_key: str
    
class AssignOrderRequest(BaseModel):
    security_key: str

def get_data_from_db(table_name):
    """
    Fetches all data from the specified table in the database.

    :param table_name: The name of the table to fetch data from.
    :return: A list of dictionaries containing the data, or an empty list if an error occurs.
    """
    # Create an instance of the DatabaseManager class
    db = DatabaseManager()
    
    # Prepare the SQL query to select all data from the given table
    query = f"SELECT * FROM {table_name};"
    
    # Fetch all data from the table
    data = db.fetch_all(query)
    
    # If data is found, convert it into a list of dictionaries
    if data:
        # Get column names from the cursor description
        columns = [desc[0] for desc in db.cursor.description]
        # Create a list of dictionaries where keys are column names and values are the row data
        result = [dict(zip(columns, row)) for row in data]
        db.close()  # Close the database connection
        return result
    else:
        logging.warning(f"No data found in table '{table_name}'.")
        db.close()  # Close the database connection
        return []

     
def update_assigned_to(order_id: str, worker_id: str):
    """
    Updates the 'assigned_to' field in the 'orders' table for a given 'order_id'.

    :param order_id: The ID of the order to be updated.
    :param worker_id: The ID of the worker to be assigned to the order.
    :return: A dictionary with the status of the operation.
    """
    # Create an instance of DatabaseManager to handle the connection
    db = DatabaseManager()

    # Prepare the SQL query to update the assigned worker for the order
    update_query = """
        UPDATE orders
        SET assigned_to = %s
        WHERE order_id = %s;
    """
    
    # Execute the update query with parameters
    try:
        db.execute_query(update_query, (worker_id, order_id))
        
        # Check if the update was successful
        # We can check how many rows were affected to confirm the update
        if db.cursor.rowcount > 0:
            return {"status": "success", "message": f"Order {order_id} assigned to {worker_id}"}
        else:
            return {"status": "error", "message": "No matching order found."}
    
    except Exception as e:
        return {"status": "error", "message": str(e)}
    
    finally:
        # Close the database connection after the operation
        db.close()

def update_worker_availability(worker_id: str, new_availability: int):
    """
    Updates the 'availability' field in the 'employees' table for a given 'worker_id'.

    :param worker_id: The ID of the worker whose availability is being updated.
    :param new_availability: The new availability value for the worker.
    :return: A dictionary with the status of the operation.
    """
    db = DatabaseManager()

    # Ensure new_availability is a regular Python int
    new_availability = int(new_availability)  # Convert numpy.int64 to a native int
    
    update_query = """
        UPDATE employees
        SET availability = %s
        WHERE worker_id = %s;
    """
    
    try:
        db.execute_query(update_query, (new_availability, worker_id))
        
        if db.cursor.rowcount > 0:
            return {"status": "success", "message": f"Worker {worker_id} availability updated to {new_availability}"}
        else:
            return {"status": "error", "message": "No matching worker found."}
    
    except Exception as e:
        return {"status": "error", "message": str(e)}
    
    finally:
        db.close()



@app.post("/save_customer_request_db/")
def save_customer_request_db(request: CustomerRequest):
    params = (
        str(uuid.uuid4()),            # Order_id
        request.name,                 # name
        request.telephone,            # telephone
        request.city,                 # city
        request.street,               # street
        request.post_code,            # post_code
        request.house_nr,             # house_nr
        request.defect_difficulty,    # defect_difficulty
        request.description,          # description
        None                          # assigned_to is set to None
    )

    db = DatabaseManager()


    # SQL query to insert the new order
    insert_query = """
        INSERT INTO orders (
            order_id, name, telephone, city, street, post_code, house_nr,
            defect_difficulty, description, assigned_to
        ) 
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
    """
    try:
        db.execute_query(insert_query, params)
        logging.info(f"Order {params[0]} inserted successfully.")
    except Exception as e:
        logging.error(f"Failed to insert order {params[0]}: {e}")

    db.close()

@app.post("/assign_orders_to_employees/")
def save_customer_request_db(request: AssignOrderRequest):
    # Fetch all unassigned orders from the database
    orders_data_df = pd.DataFrame(get_data_from_db("orders"))
    unassigned_orders = orders_data_df[orders_data_df["assigned_to"].isna()]

    if not unassigned_orders.empty:
        employees_data_df = pd.DataFrame(get_data_from_db("employees"))

        # Step 1: Assign orders to the boss first
        boss = employees_data_df[employees_data_df["worker_role"] == "Boss"].iloc[0]
        boss_availability = boss["availability"]
        
        while boss_availability > 0 and not unassigned_orders.empty:
            unassigned_order = unassigned_orders.iloc[0]
            update_assigned_to(unassigned_order["order_id"], boss["worker_id"])

            boss_availability -= 1
            employees_data_df.loc[boss.name, "availability"] = boss_availability
            unassigned_orders = unassigned_orders.iloc[1:].reset_index(drop=True)

        # Step 2: Distribute remaining orders to employees
        employees_mask = employees_data_df["worker_role"] == "Employee"
        employees_indices = employees_data_df[employees_mask].index
        num_employees = len(employees_indices)

        if num_employees > 0 and not unassigned_orders.empty:
            for idx, order in unassigned_orders.iterrows():
                worker_idx = employees_indices[idx % num_employees]
                current_availability = employees_data_df.loc[worker_idx, "availability"]

                if current_availability > 0:
                    update_assigned_to(order["order_id"], employees_data_df.loc[worker_idx, "worker_id"])
                    employees_data_df.loc[worker_idx, "availability"] = current_availability - 1

        # Step 3: Update the availability of employees in the database
        for idx, employee in employees_data_df.iterrows():
            update_worker_availability(employee["worker_id"], employee["availability"])

    return {"status": 'Orders successfully assigned to employees.'}