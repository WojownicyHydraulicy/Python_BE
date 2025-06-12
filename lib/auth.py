from typing import Optional
from config import SECRET_KEY, ALGORITHM
from fastapi import Header, HTTPException
from lib.db_conn import DatabaseManager
from models.models import AuthUser
from jose import jwt, JWTError

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