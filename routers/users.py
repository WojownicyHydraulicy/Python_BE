"""!
@file users.py
@brief Moduł obsługujący routing dla operacji związanych z użytkownikami
@details Zapewnia endpointy API do zarządzania użytkownikami systemu, pobierania listy użytkowników
         i pracowników oraz ich danych.
@author Piotr
@date 2023
"""
from fastapi import APIRouter, Depends, HTTPException, Form, File, UploadFile
"""!
@brief Komponenty FastAPI do obsługi routingu, zależności i wyjątków
"""

from datetime import datetime
import uuid
import os
import logging
"""!
@brief Biblioteki standardowe do obsługi dat, identyfikatorów, systemu plików i logowania
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
@brief Funkcja weryfikująca token uwierzytelniający
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
@brief Klasyfikator zamówień
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
@brief Router FastAPI do obsługi endpointów związanych z użytkownikami
@details Router definiuje ścieżki API do zarządzania użytkownikami i pobierania informacji o nich
"""

"""!
@brief Pobieranie listy wszystkich użytkowników systemu
@details Endpoint dostępny tylko dla właścicieli (OWNER), zwraca pełną listę użytkowników 
         z podstawowymi informacjami o nich.
@param auth_user Uwierzytelniony użytkownik przekazywany przez Depends(verify_token)
@return JSON z listą wszystkich użytkowników zawierającą ich ID, nazwę użytkownika, imię, nazwisko, email, rolę i miasto
"""
@router.get("/all_users/")
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
    


"""!
@brief Pobieranie listy wszystkich pracowników dla menu rozwijanego
@details Endpoint zwraca uproszczoną listę pracowników (ID i nazwisko) do wykorzystania w komponentach interfejsu użytkownika,
         takich jak listy rozwijane. Dostępny tylko dla właścicieli (OWNER).
@param auth_user Uwierzytelniony użytkownik przekazywany przez Depends(verify_token)
@return JSON z listą pracowników zawierającą ich ID i pełne imię i nazwisko
"""
@router.get("/get_all_employees/")
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