from fastapi import APIRouter, Depends, HTTPException, Form, File, UploadFile
from datetime import datetime
import uuid
import os
import logging
from google.cloud import storage
from typing import Optional

from config import EMAIL, EMAIL_PASSWORD, BUCKET_NAME, IMAGE_BASE_URL

from lib.auth import verify_token
from lib.db_conn import DatabaseManager
from lib.email_sender import GmailSender
from lib.order_classifier import OrderClassifier
from lib.order_assigner import assign_order_to_worker
from models.models import AuthUser, FinishOrder
from lib.validation_rules import (
    validate_nip,
    validate_name_surname,
    validate_address,
    validate_postal_code,
    validate_phone,
    validate_email,
    validate_house_number
)

router = APIRouter()

"""
Creating order endpoint -
"""
@router.post("/create_order/")
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
@router.post("/fetch_orders/")
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
Fetching all orders endpoint
"""   
@router.get("/all_orders/")
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
Updating an existing order endpoint
"""
@router.put("/update_order/")
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
@router.get("/get_order/{order_id}")
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
Fetching all orders for address endpoint
"""
@router.post("/fetch_orders_on_addr/")
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
@router.get("/fetch_cities/")
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
@router.post("/finish_order/")
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