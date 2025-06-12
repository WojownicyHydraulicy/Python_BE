"""!
@file validation_rules.py
@brief Moduł zawierający funkcje walidacyjne dla danych wprowadzanych przez użytkowników
@details Zapewnia zestaw funkcji do weryfikacji poprawności danych, takich jak NIP,
         numery telefonów, adresy email, kody pocztowe i inne dane adresowe.
@author Piotr
@date 2023
"""
import re

def validate_nip(nip: str) -> bool:
    """!
    @brief Walidacja polskiego numeru NIP
    @details Sprawdza, czy podany ciąg znaków jest prawidłowym numerem NIP (10 cyfr)
             oraz czy cyfra kontrolna jest poprawna według algorytmu wyliczającego sumę kontrolną.
    @param nip Ciąg znaków reprezentujący numer NIP do sprawdzenia
    @return True jeśli numer NIP jest prawidłowy, False w przeciwnym wypadku
    """
    if not re.match(r'^\d{10}$', nip):
        return False

    digits = [int(d) for d in nip]
    checksum = (6 * digits[0] + 5 * digits[1] + 7 * digits[2] +
                2 * digits[3] + 3 * digits[4] + 4 * digits[5] +
                5 * digits[6] + 6 * digits[7] + 7 * digits[8]) % 11

    return checksum == digits[9]

def validate_phone(phone: str) -> bool:
    """!
    @brief Walidacja polskiego numeru telefonu
    @details Sprawdza, czy podany ciąg znaków jest prawidłowym numerem telefonu.
             Obsługuje różne formaty zapisu (z prefiksem +48, bez prefiksu, 
             z myślnikami, spacjami i nawiasami), ale ostatecznie numer musi 
             składać się z dokładnie 9 cyfr.
    @param phone Ciąg znaków reprezentujący numer telefonu do sprawdzenia
    @return True jeśli numer telefonu jest prawidłowy, False w przeciwnym wypadku
    """
    if phone.startswith('+48'):
        phone = phone[3:]
    elif phone.startswith('48'):
        phone = phone[2:]
    phone = phone.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
    # Check if the phone number is exactly 9 digits long
    return bool(re.match(r'^\d{9}$', phone))

def validate_name_surname(name: str) -> bool:
    """!
    @brief Walidacja imienia i nazwiska
    @details Sprawdza, czy podany ciąg znaków może być prawidłowym imieniem lub nazwiskiem.
             Dozwolone są litery (w tym polskie znaki diakrytyczne), spacje i myślniki.
    @param name Ciąg znaków reprezentujący imię lub nazwisko do sprawdzenia
    @return True jeśli imię/nazwisko jest prawidłowe, False w przeciwnym wypadku
    """
    return bool(re.match(r'^[A-Za-zżźćńółęąśŻŹĆĄŚĘŁÓŃ\s-]+$', name))

def validate_email(email: str) -> bool:
    """!
    @brief Walidacja adresu email
    @details Sprawdza, czy podany ciąg znaków jest prawidłowym adresem email.
             Używa prostego wyrażenia regularnego do weryfikacji formatu adresu email.
    @param email Ciąg znaków reprezentujący adres email do sprawdzenia
    @return True jeśli adres email jest prawidłowy, False w przeciwnym wypadku
    """
    return bool(re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email))

def validate_address(address: str) -> bool:
    """!
    @brief Walidacja adresu
    @details Sprawdza, czy podany ciąg znaków może być prawidłowym adresem.
             Używa prostego wyrażenia regularnego do weryfikacji formatu adresu.
             Pozwala na obecność podwójnych cudzysłowów (").
    @param address Ciąg znaków reprezentujący adres do sprawdzenia
    @return True jeśli adres jest prawidłowy, False w przeciwnym wypadku
    """
    return bool(re.match(r'^[A-Za-zżźćńółęąśŻŹĆĄŚĘŁÓŃ0-9\s,.\-"]+$', address))

def validate_postal_code(postal_code: str) -> bool:
    """!
    @brief Walidacja kodu pocztowego
    @details Sprawdza, czy podany ciąg znaków jest prawidłowym kodem pocztowym.
             Kody pocztowe mają format XX-XXX, gdzie X jest cyfrą.
    @param postal_code Ciąg znaków reprezentujący kod pocztowy do sprawdzenia
    @return True jeśli kod pocztowy jest prawidłowy, False w przeciwnym wypadku
    """
    return bool(re.match(r'^\d{2}-\d{3}$', postal_code))

def validate_house_number(house_number: str) -> bool:
    """!
    @brief Walidacja numeru domu
    @details Sprawdza, czy podany ciąg znaków może być prawidłowym numerem domu.
             Numery domów mogą być kombinacją cyfr i liter, np. '12A', '34B'.
    @param house_number Ciąg znaków reprezentujący numer domu do sprawdzenia
    @return True jeśli numer domu jest prawidłowy, False w przeciwnym wypadku
    """
    return bool(re.match(r'^[0-9]+[A-Za-z]?$|^[A-Za-z][0-9]+$', house_number))