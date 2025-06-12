"""!
@file schedule.py
@brief Moduł obsługujący routing dla operacji związanych z harmonogramem pracy
@details Zapewnia endpointy API do zarządzania harmonogramem pracy pracowników, wnioskami urlopowymi 
         i dostępnością czasową dla realizacji zadań.
@author Piotr
@date 2023
"""
from fastapi import APIRouter, Depends, HTTPException, Form, File, UploadFile
"""!
@brief Komponenty FastAPI do obsługi routingu, autoryzacji i przesyłania formularzy
"""

from datetime import datetime
import uuid
import os
from datetime import date
import logging
"""!
@brief Biblioteki standardowe do obsługi dat, identyfikatorów i logowania
@details datetime - obsługa dat i czasu
        uuid - generowanie unikalnych identyfikatorów
        os - operacje na systemie plików
        date - praca z datami
        logging - rejestrowanie zdarzeń i błędów
"""

from google.cloud import storage
from typing import Optional
"""!
@brief Biblioteki do obsługi przechowywania plików i typowania zmiennych
"""

from config import EMAIL, EMAIL_PASSWORD, BUCKET_NAME, IMAGE_BASE_URL
"""!
@brief Konfiguracja systemu importowana z pliku config.py
"""

from lib.auth import verify_token
"""!
@brief Funkcje uwierzytelniania i weryfikacji tokenów dostępu
"""

from lib.db_conn import DatabaseManager
"""!
@brief Manager połączeń z bazą danych
"""

from lib.email_sender import GmailSender
"""!
@brief Moduł do wysyłania wiadomości email
"""

from lib.order_classifier import OrderClassifier
"""!
@brief Klasyfikator zamówień wykorzystujący AI
"""

from lib.order_assigner import assign_order_to_worker
"""!
@brief Funkcja przydzielająca zlecenia pracownikom
"""

from models.models import AuthUser, FinishOrder
"""!
@brief Modele danych używane w API
"""

from lib.validation_rules import (
    validate_nip,
    validate_name_surname,
    validate_address,
    validate_postal_code,
    validate_phone,
    validate_email,
    validate_house_number
)
"""!
@brief Funkcje walidacyjne dla danych wprowadzanych przez użytkowników
"""

router = APIRouter()
"""!
@brief Router FastAPI do obsługi endpointów związanych z harmonogramem
@details Router definiuje ścieżki API do zarządzania dniami pracy i wnioskami urlopowymi
"""


"""!
@brief Pobieranie dni pracy z pełną dostępnością dla pracownika
@details Endpoint zwraca listę dni, w których pracownik ma pełną dostępność (6 slotów),
         co umożliwia złożenie wniosku urlopowego na te dni.
@param auth_user Uwierzytelniony użytkownik przekazywany przez Depends(verify_token)
@return JSON z listą dostępnych dni pracy lub komunikatem błędu
"""
@router.post("/fetch_working_days/")
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
@router.post("/create_leave_request/")
async def create_leave_request(
    work_date: date = Form(...),
    reason: str = Form(...),
    auth_user: AuthUser = Depends(verify_token)
):
    """!
    @brief Tworzenie wniosku urlopowego
    @details Endpoint umożliwia pracownikom tworzenie wniosków o urlop na wybrany dzień roboczy
    @param work_date Data dnia, na który składany jest wniosek urlopowy
    @param reason Powód wniosku urlopowego
    @param auth_user Uwierzytelniony użytkownik przekazywany przez Depends(verify_token)
    @return JSON ze statusem operacji i komunikatem
    """
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
@router.post("/review_leave_request/")
async def review_leave_request(
    request_id: int = Form(...),
    action: str = Form(...),  # 'approve' or 'reject'
    auth_user: AuthUser = Depends(verify_token)
):
    """!
    @brief Rozpatrywanie wniosków urlopowych przez właściciela
    @details Endpoint umożliwia właścicielowi zaakceptowanie lub odrzucenie wniosku urlopowego pracownika.
             W przypadku zaakceptowania, dostępność pracownika w danym dniu jest ustawiana na 0.
    @param request_id Identyfikator wniosku urlopowego
    @param action Akcja do wykonania - 'approve' (zaakceptuj) lub 'reject' (odrzuć)
    @param auth_user Uwierzytelniony użytkownik przekazywany przez Depends(verify_token)
    @return JSON ze statusem operacji i komunikatem
    """
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
@router.get("/pending_leave_requests/")
async def get_pending_leave_requests(auth_user: AuthUser = Depends(verify_token)):
    """!
    @brief Pobieranie oczekujących wniosków urlopowych
    @details Endpoint zwraca listę wszystkich oczekujących wniosków urlopowych, które wymagają decyzji właściciela
    @param auth_user Uwierzytelniony użytkownik przekazywany przez Depends(verify_token)
    @return JSON z listą wniosków urlopowych lub komunikatem błędu
    """
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

"""!
@brief Sprawdzanie roli użytkownika
@details Endpoint zwraca rolę aktualnie zalogowanego użytkownika
@param auth_user Uwierzytelniony użytkownik przekazywany przez Depends(verify_token)
@return JSON z identyfikatorem użytkownika i jego rolą w systemie
"""
@router.get("/check_role/")
async def check_role(auth_user: AuthUser = Depends(verify_token)):
    return {
        "status": "success",
        "user_id": auth_user.user_id,
        "role": auth_user.user_role
    }




