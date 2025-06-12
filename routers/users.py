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
Fetching all users endpoint
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
    


"""
Pobieranie listy wszystkich pracowników dla menu rozwijanego
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