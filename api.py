import logging
import os
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uuid
import pandas as pd
from lib.db_conn import *
from datetime import datetime
import redis
from fastapi.middleware.cors import CORSMiddleware

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
# Logging for Uvicorn
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("uvicorn")

# .env loading
load_dotenv('.env')
SECURITY_KEY = os.getenv('SECURITY_KEY')

# FastAPI INIT
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_credentials=True,
    allow_methods=["*"],  
    allow_headers=["*"],  
)


# Redis client
redis_client = redis.Redis()

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
    
class FinishOrder(BaseModel):
    security_key: str
    order_status: str
    order_id: str

class FetchOrders(BaseModel):
    security_key: str
    worker_id: str
    

@app.post("/save_customer_request_db/")
def save_customer_request_db(request: CustomerRequest):
    if request.security_key == SECURITY_KEY:
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
            None,                         # assigned_to is set to None
            created_date := datetime.now().strftime("%Y-%m-%d %H:%M:%S") # created_date
        )

        db = DatabaseManager()


        # SQL query to insert the new order
        insert_query = """
            INSERT INTO orders (
                order_id, name, telephone, city, street, post_code, house_nr,
                defect_difficulty, description, assigned_to, created_date
            ) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
        """
        try:
            db.execute_query(insert_query, params)
            redis_client.publish("new_order_arrived", params[0])
            logging.info(f"Order {params[0]} inserted successfully.")
            db.close()
            return {"status": "success", "order_id": params[0]}
        except Exception as e:
            logging.error(f"Failed to insert order {params[0]}: {e}")
            db.close()
            return {"status": "error", "message": str(e)}
    else:
        raise HTTPException(status_code=401, detail="Unauthorized")


"""
Fetching orders for a worker endpoint
"""
@app.post("/fetch_orders/")
def fetch_orders(request: FetchOrders):
    if request.security_key == SECURITY_KEY:
        db = DatabaseManager()
        try:
            rows = db.fetch_all(
                """SELECT order_id, name, telephone, city, street, post_code, house_nr, defect_difficulty, description
                   FROM orders
                   WHERE assigned_to = %s
                     AND order_status = 'In progress'""",
                (request.worker_id,)
            )
            db.close()

            # Convert tuples to a list of dicts
            orders = []
            for row in rows:
                orders.append({
                    "order_id": row[0],
                    "name": row[1],
                    "telephone": row[2],
                    "city": row[3],
                    "street": row[4],
                    "post_code": row[5],
                    "house_nr": row[6],
                    "defect_difficulty": row[7],
                    "description": row[8]
                })

            return {"status": "success", "orders": orders}
        except Exception as e:
            logging.error(f"Failed to fetch orders for worker {request.worker_id}: {e}")
            db.close()
            return {"status": "error", "message": str(e)}
    else:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    
        
"""
Finish order endpoint
"""
@app.post("/finish_order/")
def finish_order(request: FinishOrder):
    if request.security_key == SECURITY_KEY:
        db = DatabaseManager()
        try:
            if request.order_status not in ["Completed", "Deleted"]:
                return {"status": "error", "message": "Invalid order status."}
            # SQL query to update the order status
            db.execute_query(
                "UPDATE orders SET order_status = %s WHERE order_id = %s;",
                (request.order_status, request.order_id)
            )

            # SQL query to get the worker assigned to the order
            worker_id = db.fetch_one(
                "SELECT assigned_to FROM orders WHERE order_id = %s;",
                (request.order_id,)
            )

            if worker_id:
                # SQL query to increment the worker's availability
                db.execute_query(
                    "UPDATE employees SET availability = availability + 1 WHERE worker_id = %s;",
                    (worker_id[0],)
                )
                redis_client.publish("worker_available", worker_id[0])
                logging.info(f"Order {request.order_id} finished successfully.")
                db.close()
                return {"status": "success"}
            else:
                db.close()
                return {"status": "error", "message": "Order not found."}
        except Exception as e:
            logging.error(f"Failed to finish order {request.order_id}: {e}")
            db.close()
            return {"status": "error", "message": str(e)}
        
    else:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    
def assign_order_to_worker():
    # Assigns the first unassigned order to a worker.
    db = DatabaseManager()

    try:
        # Fetch the first unassigned order
        order = db.fetch_one("SELECT order_id FROM orders WHERE assigned_to IS NULL ORDER BY created_date asc, order_status desc LIMIT 1;")

        if not order:
            logging.info("Brak dostępnych zamówień do przypisania.")
            return

        order_id = order[0]

        # Fetch the boss availability
        boss = db.fetch_one("""
            SELECT worker_id FROM employees 
            WHERE worker_role = 'Boss' AND availability > 0 
            LIMIT 1;
        """)

        if boss:
            worker_id = boss[0]
        else:
            # If there is no boss available, assign the order to an employee
            employee = db.fetch_one("""
                SELECT worker_id FROM employees 
                WHERE availability > 0 
                ORDER BY availability DESC, worker_role = 'Employee' ASC
                LIMIT 1;
            """)

            if not employee:
                db.execute_query(f"UPDATE orders SET order_status = 'Ready to Assign' WHERE assigned_to is null")
                logging.info("No available employees.")
                return  # No available employees

            worker_id = employee[0]

        # Assign the order to the worker and update the order status
        db.execute_query("""
            UPDATE orders SET assigned_to = %s, order_status = 'In progress' WHERE order_id = %s;
            UPDATE employees SET availability = availability - 1 WHERE worker_id = %s;
        """, (worker_id, order_id, worker_id))
        db.execute_query(f"UPDATE orders SET order_status = 'Ready to Assign' WHERE assigned_to is null")

        logging.info(f"Order {order_id} assigned to worker {worker_id}.")

    except Exception as e:
        logging.error(f"Błąd bazy danych podczas przypisywania zamówienia: {e}")

    finally:
        db.close()  # Close the database connection


