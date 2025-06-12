"""!
@file api.py
@brief Główny plik aplikacji API zbudowanej przy użyciu FastAPI.
@details Plik zawiera konfigurację głównej aplikacji FastAPI oraz podłącza wszystkie routery.
@author Piotr
@date 2023
"""

from fastapi import FastAPI
from routers import orders, users, schedule

# Inicjalizacja aplikacji FastAPI
app = FastAPI(
    title="System API",
    description="API do zarządzania zamówieniami, użytkownikami i harmonogramami",
    version="1.0.0"
)

"""!
@brief Konfiguracja głównej aplikacji FastAPI.
@details Aplikacja zawiera trzy główne routery: zamówienia, użytkownicy i harmonogram.
"""

# Dodanie routera obsługującego zamówienia
app.include_router(orders.router)
"""!
@brief Router zamówień.
@details Odpowiada za zarządzanie zamówieniami, ich tworzenie, aktualizację i usuwanie.
"""

# Dodanie routera obsługującego użytkowników
app.include_router(users.router)
"""!
@brief Router użytkowników.
@details Odpowiada za zarządzanie kontami użytkowników, uwierzytelnianie i autoryzację.
"""

# Dodanie routera obsługującego harmonogram
app.include_router(schedule.router)
"""!
@brief Router harmonogramu.
@details Odpowiada za zarządzanie harmonogramami, planowanie i zarządzanie czasem.
"""









