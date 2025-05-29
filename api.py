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

# 5. Funkcja pomocnicza do walidacji kodu pocztowego
def is_valid_postal_code(post_code: str) -> bool:
    return bool(re.fullmatch(r'\d{2}-\d{3}', post_code))
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
    
    # 3) Walidacja numeru telefonu i kodu pocztowego
    if not telephone.isdigit() or len(telephone) != 9:
        return {"status": "error", "message": "Nieprawidłowy numer telefonu."}
    if not is_valid_postal_code(post_code):
        return {"status": "error", "message": "Nieprawidłowy kod pocztowy."}

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
        payment_method = None
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
def fetch_orders(auth_user: AuthUser = Depends(verify_token)):
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
                appointment_date
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
                "appointment_date": row[10].strftime("%Y-%m-%d") if row[10] else None
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
Fetching all orders for address endpoint
"""
@app.post("/fetch_orders_on_addr/")
def fetch_orders_on_addr(
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
def fetch_cities():
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
def finish_order(
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
    low_priority_delay: int = 3
) -> date | None:
    db = DatabaseManager()
    try:
        # 1. Pobranie pracowników...
        rows = db.fetch_all("""
            SELECT user_id, worker_role
              FROM employees
             WHERE city = %s
        """, (order_city,))
        employees = [(str(uid), role) for uid, role in rows]
        if not employees:
            logging.info(f"Brak pracowników w {order_city}")
            return None

        # 2. Inicjalizacja harmonogramu: nawet nowi pracownicy dostają 30 dni roboczych
        for uid, _ in employees:
            ensure_worker_has_schedule(db, uid, days_required=30, slots_per_day=6)

        # 3. Pobranie wolnych slotów
        user_ids = tuple(uid for uid, _ in employees)
        schedules = db.fetch_all("""
            SELECT user_id, work_date
              FROM schedule
             WHERE work_date >= %s
               AND available_slots > 0
               AND user_id IN %s
             ORDER BY work_date ASC
        """, (date.today(), user_ids))

        worker_av = defaultdict(list)
        for uid, wd in schedules:
            worker_av[uid].append(wd)

        # 4. Wybór pracownika wg roli (OWNER > WORKER)
        by_role = {"OWNER": [], "WORKER": []}
        for uid, role in employees:
            if uid in worker_av:
                by_role[role].append(uid)

        assigned = next((by_role[r][0] for r in ("OWNER","WORKER") if by_role[r]), None)
        if not assigned:
            logging.info(f"Brak slotów dla {order_id}")
            return None

        # 5. Wybór daty wg priorytetu
        dates = sorted(worker_av[assigned])
        if urgency.lower().startswith("pilne"):
            work_date = dates[0]
        else:
            cutoff = date.today() + timedelta(days=low_priority_delay)
            future = [d for d in dates if d >= cutoff]
            work_date = future[0] if future else dates[-1]

        # 6. Rezerwacja slotu i aktualizacja zamówienia
        db.execute_query("""
            UPDATE schedule
               SET available_slots = available_slots - 1
             WHERE user_id = %s AND work_date = %s
        """, (assigned, work_date))
        db.execute_query("""
            UPDATE orders
               SET assigned_to = %s,
                   order_status = 'In progress',
                   appointment_date = %s
             WHERE order_id = %s
        """, (assigned, work_date, order_id))

        logging.info(f"{order_id} -> {assigned} na {work_date}")
        return work_date

    except Exception as e:
        logging.error(f"assign_order_to_worker error: {e}")
        return None
    finally:
        db.close()