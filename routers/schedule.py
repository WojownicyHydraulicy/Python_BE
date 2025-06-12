from fastapi import APIRouter, Depends, HTTPException, Form, File, UploadFile
from datetime import datetime
import uuid
import os
from datetime import date
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
Fetching all working days for a worker in full availability
for leave requests endpoint
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

@router.get("/check_role/")
async def check_role(auth_user: AuthUser = Depends(verify_token)):
    return {
        "status": "success",
        "user_id": auth_user.user_id,
        "role": auth_user.user_role
    }
    



