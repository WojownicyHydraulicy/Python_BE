import logging
import os
import re
import uuid
from datetime import datetime, date, timedelta
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from jose import JWTError, jwt
from google.cloud import storage
from pydantic import BaseModel
from collections import defaultdict

from lib.db_conn import DatabaseManager
from lib.order_classifier import OrderClassifier
from lib.email_sender import GmailSender
from lib.validation_rules import (
    validate_nip,
    validate_phone,
    validate_name_surname,
    validate_email,
    validate_address,
    validate_postal_code,
    validate_house_number
)

# logging.basicConfig(
#     level=logging.INFO,  # lub DEBUG, jeśli chcesz więcej szczegółów
#     format="%(asctime)s - %(levelname)s - %(message)s"
# )

# 1. Konfiguracja środowiska
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

# 2. Modele
class AuthUser(BaseModel):
    user_id: str
    user_role: str

class FinishOrder(BaseModel):
    order_id: str
    order_status: str

# 3. Aplikacja i CORS
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 4. Token verification (unikamy f-stringów dla zapytań SQL)
def verify_token(authorization: Optional[str] = Header(None)) -> AuthUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token missing or invalid")
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_email = payload.get("email")
        if not user_email:
            raise HTTPException(status_code=401, detail="Invalid token payload")

        db = DatabaseManager()
        query = """
            SELECT e.user_id, e.worker_role
              FROM users u
              JOIN employees e ON e.user_id = u.id
             WHERE u.email = %s
        """
        result = db.fetch_all(query, (user_email,))
        db.close()

        if not result:
            raise HTTPException(status_code=401, detail="Invalid token payload")
        uid, role = result[0]
        return AuthUser(user_id=str(uid), user_role=role)

    except JWTError:
        raise HTTPException(status_code=401, detail="Token verification failed")

"""
Creating order endpoint -
"""
@app.post("/create_order/")
async def create_order(
    name: str = Form(...),
    telephone: str = Form(...),
    city: str = Form(...),
    street: str = Form(...),
    post_code: str = Form(...),
    house_nr: str = Form(...),
    description: str = Form(...),
    urgency: str = Form(...),
    email: str = Form(...),
    payment_method: str = Form(...),
    sales_document: str = Form(...),
    billing_name: str = Form(None),
    billing_address: str = Form(None),
    billing_city: str = Form(None),
    billing_postcode: str = Form(None),
    billing_country: str = Form(None),
    billing_phone: str = Form(None),
    billing_tax_id: str = Form(None),
    photo: UploadFile = File(None)
):
    """
    Tworzy nowe zamówienie serwisowe.

    Parametry formularza:
      * name            – imię i nazwisko klienta,
      * telephone       – 9-cyfrowy numer telefonu,
      * city, street    – adres wykonania usługi,
      * post_code       – kod pocztowy w formacie XX-XXX,
      * house_nr        – numer budynku/lokalu,
      * description     – opis usterki,
      * urgency         – priorytet (np. "Pilne"/"Normalne"),
      * email           – adres e-mail klienta,
      * payment_method  – sposób płatności,
      * sales_document  – dokument sprzedaży ("Faktura"/"Paragon"),
      * billing_*       – dane do faktury (opcjonalnie),
      * photo           – załączone zdjęcie usterki (opcjonalnie).

    Zwraca JSON z:
      - status: "success" lub "error",
      - order_id: wygenerowane UUID,
      - photo_url: URL zdjęcia (jeśli wysłano),
      - price, client_response, appointment_date (jeśli powiodło się),
      - message w przypadku błędu.
    """

    # 1) Generowanie ID zamówienia i znacznika czasu
    order_id = str(uuid.uuid4())
    created_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    photo_url = None

    if photo and photo.filename:
        try:
            # Stwórz klienta GCS
            storage_client = storage.Client()
            bucket = storage_client.bucket(BUCKET_NAME)

            # Stwórz unikalną nazwę pliku
            extension = os.path.splitext(photo.filename)[1]
            blob_name = f"{order_id}{extension}"
            blob = bucket.blob(blob_name)

            # Wrzuć do bucketa
            blob.upload_from_file(photo.file, content_type=photo.content_type)

            # Zapisz URL do zdjęcia
            photo_url = f"{IMAGE_BASE_URL}{blob_name}"
        except Exception as e:
            logging.error(f"Failed to upload photo: {e}")
    else:
        photo_url = None
    
    # 3) Walidacja danych 
    if not validate_nip(billing_tax_id) and sales_document == 'Faktura':
        return {"status": "error", "message": "Nieprawidłowy NIP. Proszę podać poprawny numer NIP."}
    if sales_document == 'Faktura' and not validate_name_surname(billing_name):
        return {"status": "error", "message": "Nieprawidłowe imię i nazwisko na fakturze. Proszę podać poprawne imię i nazwisko."}
    if sales_document == 'Faktura' and not validate_address(billing_address):
        return {"status": "error", "message": "Nieprawidłowy adres na fakturze. Proszę podać poprawny adres."}
    if sales_document == 'Faktura' and not validate_address(billing_address+" "+billing_city):
        return {"status": "error", "message": "Nieprawidłowy adre na fakturze. Proszę podać poprawny adres."}
    if sales_document == 'Faktura' and not validate_postal_code(billing_postcode):
        return {"status": "error", "message": "Nieprawidłowy kod pocztowy na fakturze. Proszę podać poprawny kod pocztowy."}
    if sales_document == 'Faktura' and not validate_address(billing_country):
        return {"status": "error", "message": "Nieprawidłowy kraj na fakturze. Proszę podać poprawny kraj."}
    if sales_document == 'Faktura' and not validate_phone(billing_phone):
        return {"status": "error", "message": "Nieprawidłowy numer telefonu na fakturze. Proszę podać poprawny numer telefonu."}
    if not validate_phone(telephone):
        return {"status": "error", "message": "Nieprawidłowy numer telefonu. Proszę podać poprawny numer telefonu."}
    if not validate_name_surname(name):
        return {"status": "error", "message": "Nieprawidłowe imię i nazwisko. Proszę podać poprawne imię i nazwisko."}
    if not validate_email(email):
        return {"status": "error", "message": "Nieprawidłowy adres e-mail. Proszę podać poprawny adres e-mail."}
    if not validate_address(city+" "+street):
        return {"status": "error", "message": "Nieprawidłowy adres. Proszę podać poprawny adres."}
    if not validate_postal_code(post_code):
        return {"status": "error", "message": "Nieprawidłowy kod pocztowy. Proszę podać poprawny kod pocztowy."}
    if not validate_house_number(house_nr):
        return {"status": "error", "message": "Nieprawidłowy numer budynku/lokalu. Proszę podać poprawny numer budynku/lokalu."}
    
    
    # 4) Klasyfikacja zlecenia i kalkulacja ceny
    classifier = OrderClassifier(
        project_id=os.getenv("PROJECT_ID"),
        location=os.getenv("GCLOUD_REGION"),
        model_name=os.getenv("GEMINI_MODEL")
    )
    classifier.initialize()
    result = classifier.evaluate_difficulty(description, photo_url)
    defect_difficulty = result['flaw_category']
    price = result['price']
    client_response = result['client_response']
    is_valid_request = result['is_valid_request']
    order_status = 'Ready to Assign'
    
    if not is_valid_request:
        return {"status": "error", "message": f"\n\n{client_response}"}
    
    if sales_document != 'Faktura':
        billing_name = None
        billing_address = None
        billing_city = None
        billing_postcode = None
        billing_country = None
        billing_phone = None
        billing_tax_id = None

    # 5) Przygotowanie parametrów i zapis do bazy
    params = (
        order_id,
        order_status,
        name,
        telephone,
        city,
        street,
        post_code,
        house_nr,
        defect_difficulty,
        description,
        None,            # assigned_to
        created_date,
        photo_url,
        price,
        client_response,
        email,
        payment_method,
        sales_document,
        urgency,
        billing_name,
        billing_address,
        billing_city,
        billing_postcode,
        billing_country,
        billing_phone,
        billing_tax_id,
        None             # appointment_date (NULL na start)
    )

    db = DatabaseManager()

    insert_query = """
        INSERT INTO orders (
            order_id, order_status, name, telephone, city, street, post_code, house_nr,
            defect_difficulty, description, assigned_to, created_date, photo_url,
            price, client_response, email, payment_method, sales_document,
            urgency, billing_name, billing_address, billing_city, billing_postcode,
            billing_country, billing_phone, billing_tax_id, appointment_date
        ) 
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
    """

    try:
        db.execute_query(insert_query, params)

        # 6) Przydzielenie terminu i wysłanie maila potwierdzającego
        appointment_date = assign_order_to_worker(order_id, city, urgency)
        
        logging.info(f"Sending confirmation email to {email} for order {order_id}")
        
        try:
            email_sender = GmailSender(EMAIL, EMAIL_PASSWORD)
        except Exception as e:
            logging.error(f"Failed to initialize email sender: {e}")
        try:
            email_sender.send_order_confirmation(email, order_id)
        except Exception as e:
            logging.error(f"Failed to send confirmation email: {e}")
            
        db.close()
        return {
            "status": "success",
            "order_id": order_id,
            "photo_url": photo_url,
            "price": price,
            "client_response": client_response,
            "appointment_date": str(appointment_date) if appointment_date else None,
        }
    except Exception as e:
        db.close()
        return {"status": "error", "message": str(e)}
"""
Fetching orders for a worker endpoint
"""
@app.post("/fetch_orders/")
async def fetch_orders(auth_user: AuthUser = Depends(verify_token)):
    # Tylko OWNER i WORKER mają dostęp
    if auth_user.user_role not in ["OWNER", "WORKER"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = DatabaseManager()
    try:
        rows = db.fetch_all(
            """SELECT 
                order_id, name, telephone, 
                city, street, post_code, house_nr, 
                defect_difficulty, description, photo_url,
                appointment_date, payment_method,
                billing_name, billing_address, billing_city,
                billing_postcode, billing_country, billing_phone, billing_tax_id,
                email, price, client_response, sales_document
               FROM orders
               WHERE assigned_to = %s
                 AND order_status = 'In progress' order by appointment_date ASC;""",
            (auth_user.user_id,)
        )
        db.close()

        orders = [
            {
                "order_id": row[0],
                "name": row[1],
                "telephone": row[2],
                "city": row[3],
                "street": row[4],
                "post_code": row[5],
                "house_nr": row[6],
                "defect_difficulty": row[7],
                "description": row[8],
                "photo_url":  row[9],
                "appointment_date": row[10].strftime("%Y-%m-%d") if row[10] else None,
                "payment_method": row[11],
                "billing_name": row[12] if row[12] else None,
                "billing_address": row[13] if row[13] else None,
                "billing_city": row[14] if row[14] else None,
                "billing_postcode": row[15] if row[15] else None,
                "billing_country": row[16] if row[16] else None,
                "billing_phone": row[17] if row[17] else None,
                "billing_tax_id": row[18] if row[18] else None,
                "email": row[19],
                "price": row[20],
                "client_response": row[21],
                "sales_document": row[22]
            }
            for row in rows
        ]

        return {
            "status": "success",
            "message": f"Fetched {len(orders)} orders.",
            "orders": orders
        }

    except Exception as e:
        logging.error(f"Failed to fetch orders for worker {auth_user.user_id}: {e}")
        db.close()
        return {"status": "error", "message": str(e)}
    
"""
Fetching all users endpoint
"""
@app.get("/all_users/")
async def get_all_users(auth_user: AuthUser = Depends(verify_token)):
    # Only OWNER has access to user data
    if auth_user.user_role != "OWNER":
        raise HTTPException(status_code=403, detail="Insufficient permissions. Only owners can view user data.")

    db = DatabaseManager()
    try:
        rows = db.fetch_all("""
            SELECT id, username, first_name, last_name, email, role, city 
            FROM users
            ORDER BY last_name, first_name
        """)
        db.close()

        users = [
            {
                "id": str(row[0]),
                "username": row[1],
                "first_name": row[2],
                "last_name": row[3],
                "email": row[4],
                "role": row[5],
                "city": row[6]
            }
            for row in rows
        ]

        return {
            "status": "success",
            "message": f"Fetched {len(users)} users.",
            "users": users
        }

    except Exception as e:
        logging.error(f"Failed to fetch users: {e}")
        db.close()
        return {"status": "error", "message": str(e)}
    
"""
Promote user to WORKER role endpoint
"""
@app.put("/users/{username}/promote")
async def promote_user(username: str, auth_user: AuthUser = Depends(verify_token)):
    # Only OWNER can promote users
    if auth_user.user_role != "OWNER":
        raise HTTPException(status_code=403, detail="Insufficient permissions. Only owners can promote users.")
    
    db = DatabaseManager()
    try:
        # Check if user exists
        user = db.fetch_one("SELECT id, city FROM users WHERE username = %s", (username,))
        if not user:
            return {"status": "error", "message": f"User '{username}' not found."}
        
        user_id, city = user[0], user[1]
        
        # Update the user's role to WORKER
        db.execute_query("UPDATE users SET role = 'WORKER' WHERE id = %s", (user_id,))
        
        # Check if an employee record exists, create one if not
        employee = db.fetch_one("SELECT user_id FROM employees WHERE user_id = %s", (user_id,))
        if not employee:
            if not city:
                return {"status": "error", "message": "User has no city defined. Cannot promote."}
                
            # Create employee record
            db.execute_query(
                "INSERT INTO employees (user_id, worker_role, city) VALUES (%s, %s, %s)",
                (user_id, "WORKER", city)
            )
        else:
            # Update existing employee record
            db.execute_query(
                "UPDATE employees SET worker_role = 'WORKER' WHERE user_id = %s",
                (user_id,)
            )
        
        db.close()
        return {"status": "success", "message": f"User '{username}' promoted to WORKER."}
    
    except Exception as e:
        logging.error(f"Failed to promote user {username}: {e}")
        db.close()
        return {"status": "error", "message": str(e)}

"""
Demote user to USER role endpoint
"""
@app.put("/users/{username}/demote")
async def demote_user(username: str, auth_user: AuthUser = Depends(verify_token)):
    # Only OWNER can demote users
    if auth_user.user_role != "OWNER":
        raise HTTPException(status_code=403, detail="Insufficient permissions. Only owners can demote users.")
    
    db = DatabaseManager()
    try:
        # Check if user exists
        user = db.fetch_one("SELECT id FROM users WHERE username = %s", (username,))
        if not user:
            return {"status": "error", "message": f"User '{username}' not found."}
        
        user_id = user[0]
        
        # Update the user's role to USER
        db.execute_query("UPDATE users SET role = 'USER' WHERE id = %s", (user_id,))
        
        # Update employee record if it exists
        db.execute_query(
            "UPDATE employees SET worker_role = 'USER' WHERE user_id = %s",
            (user_id,)
        )
        
        db.close()
        return {"status": "success", "message": f"User '{username}' demoted to USER."}
    
    except Exception as e:
        logging.error(f"Failed to demote user {username}: {e}")
        db.close()
        return {"status": "error", "message": str(e)}

"""
Fetching all orders endpoint
"""   
@app.get("/all_orders/")
async def get_all_orders(auth_user: AuthUser = Depends(verify_token)):
    # Tylko OWNER ma dostęp do wszystkich zleceń
    if auth_user.user_role != "OWNER":
        raise HTTPException(status_code=403, detail="Insufficient permissions. Only owners can view all orders.")

    db = DatabaseManager()
    try:
        rows = db.fetch_all("""
            SELECT o.order_id, u.first_name || ' ' || u.last_name as worker_name, 
                   o.order_status, o.name, o.email, o.telephone, o.payment_method, 
                   o.sales_document, o.city, o.street, o.post_code, 
                   o.defect_difficulty, o.price 
            FROM orders o
            INNER JOIN employees e ON e.user_id::text = o.assigned_to
            INNER JOIN users u ON u.id = e.user_id
        """)
        db.close()

        orders = [
            {
                "order_id": row[0],
                "worker_name": row[1],
                "order_status": row[2],
                "client_name": row[3],
                "email": row[4],
                "telephone": row[5],
                "payment_method": row[6],
                "sales_document": row[7],
                "city": row[8],
                "street": row[9],
                "post_code": row[10],
                "defect_difficulty": row[11],
                "price": row[12]
            }
            for row in rows
        ]

        return {
            "status": "success",
            "message": f"Fetched {len(orders)} orders.",
            "orders": orders
        }

    except Exception as e:
        logging.error(f"Failed to fetch all orders: {e}")
        db.close()
        return {"status": "error", "message": str(e)}

"""
Pobieranie listy wszystkich pracowników dla menu rozwijanego
"""
@app.get("/get_all_employees/")
async def get_all_employees(auth_user: AuthUser = Depends(verify_token)):
    # Tylko OWNER ma dostęp
    if auth_user.user_role != "OWNER":
        raise HTTPException(status_code=403, detail="Insufficient permissions. Only owners can view employees.")

    db = DatabaseManager()
    try:
        rows = db.fetch_all("""
            SELECT u.id, u.first_name || ' ' || u.last_name as worker_name
            FROM users u
            JOIN employees e ON e.user_id = u.id
            ORDER BY worker_name
        """)
        db.close()

        employees = [
            {
                "id": str(row[0]),
                "name": row[1]
            }
            for row in rows
        ]

        return {
            "status": "success",
            "employees": employees
        }

    except Exception as e:
        logging.error(f"Failed to fetch employees: {e}")
        db.close()
        return {"status": "error", "message": str(e)}

"""
Updating an existing order endpoint
"""
@app.put("/update_order/")
async def update_order(
    order_id: str = Form(...),
    name: Optional[str] = Form(None),
    telephone: Optional[str] = Form(None),
    city: Optional[str] = Form(None),
    street: Optional[str] = Form(None),
    post_code: Optional[str] = Form(None),
    house_nr: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    urgency: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    payment_method: Optional[str] = Form(None),
    sales_document: Optional[str] = Form(None),
    billing_name: Optional[str] = Form(None),
    billing_address: Optional[str] = Form(None),
    billing_city: Optional[str] = Form(None),
    billing_postcode: Optional[str] = Form(None),
    billing_country: Optional[str] = Form(None),
    billing_phone: Optional[str] = Form(None),
    billing_tax_id: Optional[str] = Form(None),
    assigned_to: Optional[str] = Form(None),
    order_status: Optional[str] = Form(None),
    appointment_date: Optional[str] = Form(None),
    price: Optional[str] = Form(None),
    auth_user: AuthUser = Depends(verify_token)
):
    # Only OWNER can update orders
    if auth_user.user_role != "OWNER":
        raise HTTPException(status_code=403, detail="Insufficient permissions. Only owners can update orders.")
        
    # Validate data
    if billing_tax_id is not None and sales_document == 'Faktura' and not validate_nip(billing_tax_id):
        return {"status": "error", "message": "Nieprawidłowy NIP. Proszę podać poprawny numer NIP."}
    if billing_name is not None and sales_document == 'Faktura' and not validate_name_surname(billing_name):
        return {"status": "error", "message": "Nieprawidłowe imię i nazwisko na fakturze. Proszę podać poprawne imię i nazwisko."}
    if billing_address is not None and sales_document == 'Faktura' and not validate_address(billing_address):
        return {"status": "error", "message": "Nieprawidłowy adres na fakturze. Proszę podać poprawny adres."}
    if billing_address is not None and billing_city is not None and sales_document == 'Faktura' and not validate_address(billing_address+" "+billing_city):
        return {"status": "error", "message": "Nieprawidłowy adres na fakturze. Proszę podać poprawny adres."}
    if billing_postcode is not None and sales_document == 'Faktura' and not validate_postal_code(billing_postcode):
        return {"status": "error", "message": "Nieprawidłowy kod pocztowy na fakturze. Proszę podać poprawny kod pocztowy."}
    if billing_country is not None and sales_document == 'Faktura' and not validate_address(billing_country):
        return {"status": "error", "message": "Nieprawidłowy kraj na fakturze. Proszę podać poprawny kraj."}
    if billing_phone is not None and sales_document == 'Faktura' and not validate_phone(billing_phone):
        return {"status": "error", "message": "Nieprawidłowy numer telefonu na fakturze. Proszę podać poprawny numer telefonu."}
    if telephone is not None and not validate_phone(telephone):
        return {"status": "error", "message": "Nieprawidłowy numer telefonu. Proszę podać poprawny numer telefonu."}
    if name is not None and not validate_name_surname(name):
        return {"status": "error", "message": "Nieprawidłowe imię i nazwisko. Proszę podać poprawne imię i nazwisko."}
    if email is not None and not validate_email(email):
        return {"status": "error", "message": "Nieprawidłowy adres e-mail. Proszę podać poprawny adres e-mail."}
    if city is not None and street is not None and not validate_address(city+" "+street):
        return {"status": "error", "message": "Nieprawidłowy adres. Proszę podać poprawny adres."}
    if post_code is not None and not validate_postal_code(post_code):
        return {"status": "error", "message": "Nieprawidłowy kod pocztowy. Proszę podać poprawny kod pocztowy."}
    if house_nr is not None and not validate_house_number(house_nr):
        return {"status": "error", "message": "Nieprawidłowy numer budynku/lokalu. Proszę podać poprawny numer budynku/lokalu."}
    
    # Building dynamic SQL query based on provided fields
    update_fields = []
    params = []
    
    # Process each non-empty field for the update
    if name is not None and name.strip() != "":
        update_fields.append("name = %s")
        params.append(name.strip())
    if telephone is not None and telephone.strip() != "":
        update_fields.append("telephone = %s")
        params.append(telephone.strip())
    if city is not None and city.strip() != "":
        update_fields.append("city = %s")
        params.append(city.strip())
    if street is not None and street.strip() != "":
        update_fields.append("street = %s")
        params.append(street.strip())
    if post_code is not None and post_code.strip() != "":
        update_fields.append("post_code = %s")
        params.append(post_code.strip())
    if house_nr is not None and house_nr.strip() != "":
        update_fields.append("house_nr = %s")
        params.append(house_nr.strip())
    if description is not None and description.strip() != "":
        update_fields.append("description = %s")
        params.append(description.strip())
    if urgency is not None and urgency.strip() != "":
        update_fields.append("urgency = %s")
        params.append(urgency.strip())
    if email is not None and email.strip() != "":
        update_fields.append("email = %s")
        params.append(email.strip())
    if payment_method is not None and payment_method.strip() != "":
        update_fields.append("payment_method = %s")
        params.append(payment_method.strip())
    if sales_document is not None and sales_document.strip() != "":
        update_fields.append("sales_document = %s")
        params.append(sales_document.strip())
    if billing_name is not None and billing_name.strip() != "":
        update_fields.append("billing_name = %s")
        params.append(billing_name.strip())
    if billing_address is not None and billing_address.strip() != "":
        update_fields.append("billing_address = %s")
        params.append(billing_address.strip())
    if billing_city is not None and billing_city.strip() != "":
        update_fields.append("billing_city = %s")
        params.append(billing_city.strip())
    if billing_postcode is not None and billing_postcode.strip() != "":
        update_fields.append("billing_postcode = %s")
        params.append(billing_postcode.strip())
    if billing_country is not None and billing_country.strip() != "":
        update_fields.append("billing_country = %s")
        params.append(billing_country.strip())
    if billing_phone is not None and billing_phone.strip() != "":
        update_fields.append("billing_phone = %s")
        params.append(billing_phone.strip())
    if billing_tax_id is not None and billing_tax_id.strip() != "":
        update_fields.append("billing_tax_id = %s")
        params.append(billing_tax_id.strip())
    if assigned_to is not None and assigned_to.strip() != "":
        update_fields.append("assigned_to = %s")
        params.append(assigned_to.strip())
    if order_status is not None and order_status.strip() != "":
        update_fields.append("order_status = %s")
        params.append(order_status.strip())
    
    # Special handling for appointment_date
    if appointment_date is not None and appointment_date.strip() != "":
        try:
            # Try to parse the date to validate it
            datetime.strptime(appointment_date.strip(), "%Y-%m-%d")
            update_fields.append("appointment_date = %s")
            params.append(appointment_date.strip())
        except ValueError:
            return {"status": "error", "message": "Invalid appointment date format. Use YYYY-MM-DD."}
    
    # Special handling for price
    if price is not None and price.strip() != "":
        try:
            # Convert to float to validate
            price_float = float(price.strip())
            update_fields.append("price = %s")
            params.append(price_float)
        except ValueError:
            return {"status": "error", "message": "Invalid price format. Must be a number."}
    
    if not update_fields:
        return {"status": "error", "message": "No fields provided for update."}
    
    # Finalize query
    query = f"UPDATE orders SET {', '.join(update_fields)} WHERE order_id = %s"
    params.append(order_id)
    
    db = DatabaseManager()
    try:
        db.execute_query(query, tuple(params))
        db.close()
        return {"status": "success", "message": f"Order {order_id} updated successfully."}
    except Exception as e:
        logging.error(f"Failed to update order {order_id}: {e}")
        db.close()
        return {"status": "error", "message": str(e)}

"""
Getting details of a specific order for editing
"""
@app.get("/get_order/{order_id}")
async def get_order(order_id: str, auth_user: AuthUser = Depends(verify_token)):
    # Only OWNER can view order details for editing
    if auth_user.user_role != "OWNER":
        raise HTTPException(status_code=403, detail="Insufficient permissions. Only owners can view order details.")

    db = DatabaseManager()
    try:
        row = db.fetch_one("""
            SELECT name, telephone, city, street, post_code, house_nr, description, urgency,
                   email, payment_method, sales_document, billing_name, billing_address,
                   billing_city, billing_postcode, billing_country, billing_phone, billing_tax_id,
                   assigned_to, order_status, appointment_date, price, defect_difficulty, photo_url,
                   client_response, created_date
            FROM orders
            WHERE order_id = %s
        """, (order_id,))
        db.close()

        if not row:
            return {"status": "error", "message": "Order not found."}

        # Format date fields safely with type checking
        appointment_date = None
        if row[20]:
            if hasattr(row[20], 'strftime'):
                appointment_date = row[20].strftime("%Y-%m-%d")
            else:
                appointment_date = row[20]  # Keep as string if it's already a string
                
        created_date = None
        if row[25]:
            if hasattr(row[25], 'strftime'):
                created_date = row[25].strftime("%Y-%m-%d %H:%M:%S")
            else:
                created_date = row[25]  # Keep as string if it's already a string

        order = {
            "order_id": order_id,
            "name": row[0],
            "telephone": row[1],
            "city": row[2],
            "street": row[3],
            "post_code": row[4],
            "house_nr": row[5],
            "description": row[6],
            "urgency": row[7],
            "email": row[8],
            "payment_method": row[9],
            "sales_document": row[10],
            "billing_name": row[11],
            "billing_address": row[12],
            "billing_city": row[13],
            "billing_postcode": row[14],
            "billing_country": row[15],
            "billing_phone": row[16],
            "billing_tax_id": row[17],
            "assigned_to": row[18],
            "order_status": row[19],
            "appointment_date": appointment_date,
            "price": row[21],
            "defect_difficulty": row[22],
            "photo_url": row[23],
            "client_response": row[24],
            "created_date": created_date
        }

        return {
            "status": "success",
            "order": order
        }

    except Exception as e:
        logging.error(f"Failed to fetch order {order_id}: {e}")
        db.close()
        return {"status": "error", "message": str(e)}
    
"""
Fetching all working days for a worker in full availability
for leave requests endpoint
"""
@app.post("/fetch_working_days/")
async def fetch_working_days(
    auth_user: AuthUser = Depends(verify_token)
):
    # Tylko OWNER i WORKER mają dostęp
    if auth_user.user_role not in ["OWNER", "WORKER"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = DatabaseManager()
    try:
        rows = db.fetch_all(
            """SELECT work_date 
               FROM schedule 
              WHERE user_id = %s 
                AND available_slots = 6
                AND work_date >= %s
              ORDER BY work_date ASC;""",
            (auth_user.user_id, date.today())
        )
        db.close()

        working_days = [row[0].strftime("%Y-%m-%d") for row in rows]

        return {
            "status": "success",
            "message": f"Fetched {len(working_days)} working days.",
            "working_days": working_days
        }

    except Exception as e:
        logging.error(f"Failed to fetch working days for worker {auth_user.user_id}: {e}")
        db.close()
        return {"status": "error", "message": str(e)}
    
"""
Create a leave request endpoint
"""
@app.post("/create_leave_request/")
async def create_leave_request(
    work_date: date = Form(...),
    reason: str = Form(...),
    auth_user: AuthUser = Depends(verify_token)
):
    if auth_user.user_role not in ["OWNER", "WORKER"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
        
    db = DatabaseManager()
    try:
        db.execute_query(
            """INSERT INTO leave_requests (user_id, work_date, reason)
               VALUES (%s, %s, %s);""",
            (auth_user.user_id, work_date, reason)
        )
        db.close()
        return {"status": "success", "message": "Leave request submitted."}
    except Exception as e:
        db.close()
        return {"status": "error", "message": str(e)}
    
"""
Reviewing leave requests endpoint
"""
@app.post("/review_leave_request/")
async def review_leave_request(
    request_id: int = Form(...),
    action: str = Form(...),  # 'approve' or 'reject'
    auth_user: AuthUser = Depends(verify_token)
):
    if auth_user.user_role != "OWNER":
        raise HTTPException(status_code=403, detail="Only owners can review leave requests.")

    if action not in ["approve", "reject"]:
        raise HTTPException(status_code=400, detail="Invalid action")

    db = DatabaseManager()
    try:
        # Get request to verify it exists
        leave = db.fetch_one("SELECT user_id, work_date FROM leave_requests WHERE id = %s;", (request_id,))
        if not leave:
            raise HTTPException(status_code=404, detail="Leave request not found")

        status = "approved" if action == "approve" else "rejected"

        # Update leave request status
        db.execute_query("""
            UPDATE leave_requests
               SET status = %s,
                   reviewed_by = %s,
                   reviewed_at = CURRENT_TIMESTAMP
             WHERE id = %s;
        """, (status, auth_user.user_id, request_id))

        # If approved → update availability
        if status == "approved":
            db.execute_query("""
                UPDATE schedule
                   SET available_slots = 0
                 WHERE user_id = %s
                   AND work_date = %s;
            """, (leave[0], leave[1]))

        db.close()
        return {"status": "success", "message": f"Leave request {status}."}

    except Exception as e:
        db.close()
        return {"status": "error", "message": str(e)}

"""
Get pending leave requests endpoint
"""
@app.get("/pending_leave_requests/")
async def get_pending_leave_requests(auth_user: AuthUser = Depends(verify_token)):
    if auth_user.user_role != "OWNER":
        raise HTTPException(status_code=403, detail="Only owners can view leave requests.")

    db = DatabaseManager()
    try:
        results = db.fetch_all("""
           SELECT lr.id, u.first_name || ' ' || u.last_name as worker_name, 
            lr.work_date, lr.reason, lr.status
            FROM leave_requests lr
            inner JOIN users u ON lr.user_id = u.id
            WHERE lr.status = 'pending'
            ORDER BY lr.work_date ASC;
        """)
        db.close()
        return {
            "status": "success",
            "leave_requests": [{
                "id": row[0],
                "worker_name": row[1],
                "work_date": row[2].strftime("%Y-%m-%d"),
                "reason": row[3],
                "status": row[4]
            } for row in results]
        }

    except Exception as e:
        db.close()
        return {"status": "error", "message": str(e)}

@app.get("/check_role/")
async def check_role(auth_user: AuthUser = Depends(verify_token)):
    return {
        "status": "success",
        "user_id": auth_user.user_id,
        "role": auth_user.user_role
    }
    
"""
Fetching all orders for address endpoint
"""
@app.post("/fetch_orders_on_addr/")
async def fetch_orders_on_addr(
    city: str = Form(...),
    street: Optional[str] = Form(None),
    post_code: Optional[str] = Form(None),
    house_nr: Optional[str] = Form(None),
    auth_user: AuthUser = Depends(verify_token)
):
    # Tylko OWNER i WORKER mają dostęp
    if auth_user.user_role not in ["OWNER", "WORKER"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = DatabaseManager()
    try:
        query = """
            SELECT name, telephone, city, street, post_code, house_nr, defect_difficulty, description, photo_url
            FROM orders
            WHERE REPLACE(LOWER(city), ' ', '') = REPLACE(LOWER(%s), ' ', '')
            AND order_status NOT IN ('In progress', 'Ready to Assign')
        """
        params = [city]

        if street:
            query += " AND REPLACE(LOWER(street), ' ', '') = REPLACE(LOWER(%s), ' ', '')"
            params.append(street)
        if post_code:
            query += " AND post_code = %s"
            params.append(post_code)
        if house_nr:
            query += " AND house_nr = %s"
            params.append(house_nr)

        rows = db.fetch_all(query, tuple(params))
        db.close()

        orders = [
            {
                "name": row[0],
                "telephone": row[1],
                "city": row[2],
                "street": row[3],
                "post_code": row[4],
                "house_nr": row[5],
                "defect_difficulty": row[6],
                "description": row[7],
                "photo_url":  row[8]
            }
            for row in rows
        ]

        return {
            "status": "success",
            "message": f"Fetched {len(orders)} orders.",
            "orders": orders
        }

    except Exception as e:
        logging.error(f"Failed to fetch orders for worker {auth_user.user_id}: {e}")
        db.close()
        return {"status": "error", "message": str(e)}

"""
Fetching available cities endpoint
"""
@app.get("/fetch_cities/")
async def fetch_cities():
    db = DatabaseManager()
    try:
        rows = db.fetch_all("SELECT DISTINCT city FROM employees;")
        db.close()

        cities = [row[0] for row in rows]

        return {
            "status": "success",
            "message": f"Fetched {len(cities)} cities.",
            "cities": cities
        }

    except Exception as e:
        logging.error(f"Failed to fetch cities: {e}")
        db.close()
        return {"status": "error", "message": str(e)}

"""
Finishing order endpoint
"""       
@app.post("/finish_order/")
async def finish_order(
    request: FinishOrder,
    auth_user: AuthUser = Depends(verify_token)
):
    # Tylko OWNER i WORKER mają dostęp
    if auth_user.user_role not in ["OWNER", "WORKER"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    db = DatabaseManager()
    try:
        if request.order_status not in ["Completed", "Deleted"]:
            return {"status": "error", "message": "Invalid order status."}
        
        try:
            email_sender = GmailSender(EMAIL, EMAIL_PASSWORD)
            if request.order_status == "Completed":
                # Pobieramy email klienta
                row = db.fetch_one(
                    "SELECT email FROM orders WHERE order_id = %s;",
                    (request.order_id,)
                )
                if not row:
                    return {"status": "error", "message": "Order not found."}
                client_email = row[0]

                # Wysyłamy email z potwierdzeniem zakończenia
                email_sender.send_order_completed(client_email, request.order_id)
                
            elif request.order_status == "Deleted":
                # Pobieramy email klienta
                row = db.fetch_one(
                    "SELECT email FROM orders WHERE order_id = %s;",
                    (request.order_id,)
                )
                if not row:
                    return {"status": "error", "message": "Order not found."}
                client_email = row[0]

                # Wysyłamy email z informacją o usunięciu
                email_sender.send_order_rejection(client_email, request.order_id)
                
        except Exception as e:
            logging.error(f"Failed to send email for order {request.order_id}: {e}")
            return {"status": "error", "message": f"Email sending failed: {str(e)}"}

        # Zmieniamy tylko status zamówienia
        db.execute_query(
            "UPDATE orders SET order_status = %s WHERE order_id = %s;",
            (request.order_status, request.order_id)
        )

        logging.info(f"Order {request.order_id} status changed to {request.order_status}.")
        return {
            "status": "success",
            "message": f"Order {request.order_id} status changed to {request.order_status}."
        }

    except Exception as e:
        logging.error(f"Failed to finish order {request.order_id}: {e}")
        return {"status": "error", "message": str(e)}
    finally:
        db.close()
        
def is_valid_postal_code(post_code):
    return bool(re.fullmatch(r'\d{2}-\d{3}', post_code))


def ensure_worker_has_schedule(db, user_id, days_required=30, slots_per_day=6):
    today = date.today()
    user_id = str(user_id)

    # 1. Ile dni MA już pełne slots_per_day?
    row = db.fetch_one("""
        SELECT COUNT(*) 
          FROM schedule
         WHERE user_id = %s
           AND work_date >= %s
           AND available_slots = %s
    """, (user_id, today, slots_per_day))
    current_full = row[0] if row else 0

    # 2. Ile dni musimy DOPISAĆ, żeby mieć days_required pełnych dni?
    missing = max(0, days_required - current_full)
    if missing == 0:
        return

    # 3. Pobierz WSZYSTKIE istniejące daty od dziś (nie tylko pełne)
    existing = db.fetch_all("""
        SELECT work_date 
          FROM schedule
         WHERE user_id = %s
           AND work_date >= %s
    """, (user_id, today))
    existing_dates = {r[0] for r in existing}

    # 4. Generuj brakujące dni, pomijając te z existing_dates
    inserts = []
    i = 0
    while len(inserts) < missing:
        candidate = today + timedelta(days=i)
        # weekday(): 0=Poniedziałek, …, 4=Piątek, 5=Sobota, 6=Niedziela
        if candidate.weekday() < 5 and candidate not in existing_dates:
            inserts.append((user_id, candidate, slots_per_day))
        i += 1

    # 5. Wykonaj batch‐insert pojedynczo
    sql = """
        INSERT INTO schedule (user_id, work_date, available_slots)
         VALUES (%s, %s, %s)
    """
    for row in inserts:
        db.execute_query(sql, row)


def assign_order_to_worker(
    order_id: str,
    order_city: str,
    urgency: str,
    low_priority_delay: int = 1
) -> date | None:
    db = DatabaseManager()
    try:
        # 1. Pobranie pracowników z danego miasta
        rows = db.fetch_all("""
            SELECT user_id, worker_role
              FROM employees
             WHERE city = %s
        """, (order_city,))
        employees = [(str(uid), role) for uid, role in rows]
        if not employees:
            logging.info(f"Brak pracowników w {order_city}")
            return None

        # 2. Inicjalizacja harmonogramu dla wszystkich pracowników
        for uid, _ in employees:
            ensure_worker_has_schedule(db, uid, days_required=30, slots_per_day=6)

        # 3. Podział pracowników na OWNER i WORKER
        owners = [uid for uid, role in employees if role == "OWNER"]
        workers = [uid for uid, role in employees if role == "WORKER"]
        
        # 4. Sprawdzamy dostępność szefów (OWNER)
        is_urgent = urgency.lower().startswith("pilne")
        
        # Sprawdzamy zlecenia pilne - dla nich sprawdzamy najbliższy możliwy termin u szefa
        if is_urgent:
            owner_slots = {}
            for owner_id in owners:
                # Pobieramy najbliższy dostępny slot dla szefa
                nearest_slot = db.fetch_one("""
                    SELECT work_date, available_slots
                    FROM schedule
                    WHERE user_id = %s
                      AND work_date >= %s
                      AND available_slots > 0
                    ORDER BY work_date ASC
                    LIMIT 1
                """, (owner_id, date.today()))
                
                if nearest_slot:
                    owner_slots[owner_id] = nearest_slot
            
            # Jeśli jakiś szef ma dostępny slot - przydzielamy jemu
            if owner_slots:
                # Wybieramy szefa z najwcześniejszą datą
                best_owner = min(owner_slots.items(), key=lambda x: x[1][0])
                assigned_id = best_owner[0]
                work_date = best_owner[1][0]
            else:
                # Jeśli żaden szef nie ma slotów, szukamy wśród pracowników
                worker_slots = {}
                for worker_id in workers:
                    nearest_slot = db.fetch_one("""
                        SELECT work_date, available_slots
                        FROM schedule
                        WHERE user_id = %s
                          AND work_date >= %s
                          AND available_slots > 0
                        ORDER BY work_date ASC
                        LIMIT 1
                    """, (worker_id, date.today()))
                    
                    if nearest_slot:
                        worker_slots[worker_id] = nearest_slot
                
                if not worker_slots:
                    logging.info(f"Brak dostępnych slotów dla pilnego zlecenia {order_id}")
                    return None
                
                # Wybieramy pracownika z najwcześniejszą datą
                best_worker = min(worker_slots.items(), key=lambda x: x[1][0])
                assigned_id = best_worker[0]
                work_date = best_worker[1][0]
        
        # Dla zleceń niepilnych - najpierw próbujemy wypełnić dni szefa
        else:
            cutoff = date.today() + timedelta(days=low_priority_delay)
            
            # Sprawdzamy szefów - czy mają dostępne sloty po opóźnieniu
            owner_future_slots = {}
            for owner_id in owners:
                future_slot = db.fetch_one("""
                    SELECT work_date, available_slots
                    FROM schedule
                    WHERE user_id = %s
                      AND work_date >= %s
                      AND available_slots > 0
                    ORDER BY work_date ASC
                    LIMIT 1
                """, (owner_id, cutoff))
                
                if future_slot:
                    owner_future_slots[owner_id] = future_slot
            
            # Jeśli jakiś szef ma przyszłe sloty - przydzielamy jemu
            if owner_future_slots:
                # Wybieramy szefa z najwcześniejszą przyszłą datą
                best_owner = min(owner_future_slots.items(), key=lambda x: x[1][0])
                assigned_id = best_owner[0]
                work_date = best_owner[1][0]
            else:
                # Sprawdzamy pracowników, jeśli żaden szef nie ma slotów
                worker_future_slots = {}
                for worker_id in workers:
                    future_slot = db.fetch_one("""
                        SELECT work_date, available_slots
                        FROM schedule
                        WHERE user_id = %s
                          AND work_date >= %s
                          AND available_slots > 0
                        ORDER BY work_date ASC
                        LIMIT 1
                    """, (worker_id, cutoff))
                    
                    if future_slot:
                        worker_future_slots[worker_id] = future_slot
                
                # Jeśli nikt nie ma przyszłych slotów, szukamy jakichkolwiek
                if not worker_future_slots:
                    # Próbujemy szefów jeszcze raz - jakiekolwiek sloty
                    for owner_id in owners:
                        any_slot = db.fetch_one("""
                            SELECT work_date, available_slots
                            FROM schedule
                            WHERE user_id = %s
                              AND work_date >= %s
                              AND available_slots > 0
                            ORDER BY work_date ASC
                            LIMIT 1
                        """, (owner_id, date.today()))
                        
                        if any_slot:
                            assigned_id = owner_id
                            work_date = any_slot[0]
                            break
                    else:
                        # Jeśli nawet szefowie nie mają slotów, próbujemy pracowników
                        for worker_id in workers:
                            any_slot = db.fetch_one("""
                                SELECT work_date, available_slots
                                FROM schedule
                                WHERE user_id = %s
                                  AND work_date >= %s
                                  AND available_slots > 0
                                ORDER BY work_date ASC
                                LIMIT 1
                            """, (worker_id, date.today()))
                            
                            if any_slot:
                                assigned_id = worker_id
                                work_date = any_slot[0]
                                break
                        else:
                            logging.info(f"Brak dostępnych slotów dla niepilnego zlecenia {order_id}")
                            return None
                else:
                    # Wybieramy pracownika z najwcześniejszą przyszłą datą
                    best_worker = min(worker_future_slots.items(), key=lambda x: x[1][0])
                    assigned_id = best_worker[0]
                    work_date = best_worker[1][0]
        
        # 6. Rezerwacja slotu i aktualizacja zamówienia
        db.execute_query("""
            UPDATE schedule
               SET available_slots = available_slots - 1
             WHERE user_id = %s AND work_date = %s
        """, (assigned_id, work_date))
        
        db.execute_query("""
            UPDATE orders
               SET assigned_to = %s,
                   order_status = 'In progress',
                   appointment_date = %s
             WHERE order_id = %s
        """, (assigned_id, work_date, order_id))

        logging.info(f"{order_id} -> {assigned_id} na {work_date}")
        return work_date

    except Exception as e:
        logging.error(f"assign_order_to_worker error: {e}")
        return None
    finally:
        db.close()