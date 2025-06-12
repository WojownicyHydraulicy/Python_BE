"""!
@file auth.py
@brief Moduł uwierzytelniania i autoryzacji użytkowników
@details Zapewnia funkcje do weryfikacji tokenów JWT i zarządzania uprawnieniami użytkowników w systemie.
@author Piotr
@date 2023
"""
from typing import Optional
"""!
@brief Typ do oznaczenia opcjonalnych parametrów
"""

from config import SECRET_KEY, ALGORITHM
"""!
@brief Konfiguracja klucza i algorytmu dla JWT
"""

from fastapi import Header, HTTPException
"""!
@brief Komponenty FastAPI do obsługi nagłówków HTTP i wyjątków
"""

from lib.db_conn import DatabaseManager
"""!
@brief Manager połączeń z bazą danych
"""

from models.models import AuthUser
"""!
@brief Model danych uwierzytelnionego użytkownika
"""

from jose import jwt, JWTError
"""!
@brief Biblioteka do obsługi tokenów JWT
"""

"""!
@brief Weryfikuje token JWT i zwraca dane uwierzytelnionego użytkownika
@details Funkcja analizuje token przesłany w nagłówku 'Authorization', dekoduje go
         przy użyciu SECRET_KEY i sprawdza uprawnienia użytkownika w bazie danych.
@param authorization Nagłówek Authorization zawierający token JWT (format: "Bearer [token]")
@return Obiekt AuthUser zawierający ID użytkownika i jego rolę w systemie
@exception HTTPException(401) Gdy token jest nieprawidłowy, brakuje go, lub użytkownik nie istnieje
"""
def verify_token(authorization: Optional[str] = Header(None)) -> AuthUser:
    # Sprawdzenie czy token istnieje i ma poprawny format
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token missing or invalid")
    
    # Wyodrębnienie tokenu z nagłówka
    token = authorization.split(" ", 1)[1]
    
    try:
        # Dekodowanie tokenu JWT
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        # Pobranie emaila z tokenu
        user_email = payload.get("email")
        if not user_email:
            raise HTTPException(status_code=401, detail="Invalid token payload")

        # Sprawdzenie użytkownika w bazie danych
        db = DatabaseManager()
        query = """
            SELECT e.user_id, e.worker_role
              FROM users u
              JOIN employees e ON e.user_id = u.id
             WHERE u.email = %s
        """
        result = db.fetch_all(query, (user_email,))
        db.close()

        # Weryfikacja czy użytkownik istnieje w systemie
        if not result:
            raise HTTPException(status_code=401, detail="Invalid token payload")
        
        # Utworzenie i zwrócenie obiektu uwierzytelnionego użytkownika
        uid, role = result[0]
        return AuthUser(user_id=str(uid), user_role=role)

    except JWTError:
        # Obsługa błędów związanych z tokenem JWT
        raise HTTPException(status_code=401, detail="Token verification failed")