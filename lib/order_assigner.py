from datetime import date, timedelta
import logging

from lib.db_conn import DatabaseManager
from models.models import AuthUser

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