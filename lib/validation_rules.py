import re

def validate_nip(nip: str) -> bool:
    """
    Validate a Polish NIP (Numer Identyfikacji Podatkowej).
    NIP is a 10-digit number with specific rules for the last digit.
    """
    if not re.match(r'^\d{10}$', nip):
        return False

    digits = [int(d) for d in nip]
    checksum = (6 * digits[0] + 5 * digits[1] + 7 * digits[2] +
                2 * digits[3] + 3 * digits[4] + 4 * digits[5] +
                5 * digits[6] + 6 * digits[7] + 7 * digits[8]) % 11

    return checksum == digits[9]

def validate_phone(phone: str) -> bool:
    """
    Validate a Polish phone number.
    Phone numbers can be in various formats, but generally they should be 9 digits long.
    """
    if phone.startswith('+48'):
        phone = phone[3:]
    elif phone.startswith('48'):
        phone = phone[2:]
    phone = phone.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
    # Check if the phone number is exactly 9 digits long
    return bool(re.match(r'^\d{9}$', phone))

def validate_name_surname(name: str) -> bool:
    """
    Validate a name or surname.
    Names and surnames should only contain letters, hyphens, and spaces.
    """
    return bool(re.match(r'^[A-Za-zżźćńółęąśŻŹĆĄŚĘŁÓŃ\s-]+$', name))

def validate_email(email: str) -> bool:
    """
    Validate a Polish email address.
    A simple regex to check for a valid email format.
    """
    return bool(re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email))

def validate_address(address: str) -> bool:
    """
    Validate a Polish address.
    A simple regex to check for a valid address format.
    This is a basic validation and may need to be adjusted for more complex cases.
    Allows for double quotes (").
    """
    return bool(re.match(r'^[A-Za-zżźćńółęąśŻŹĆĄŚĘŁÓŃ0-9\s,.\-"]+$', address))

def validate_postal_code(postal_code: str) -> bool:
    """
    Validate a Polish postal code.
    Postal codes are in the format XX-XXX, where X is a digit.
    """
    return bool(re.match(r'^\d{2}-\d{3}$', postal_code))

def validate_house_number(house_number: str) -> bool:
    """
    Validate a Polish house number.
    House numbers can be a combination of digits and letters, e.g., '12A', '34B'.
    """
    return bool(re.match(r'^[0-9]+[A-Za-z]?$|^[A-Za-z][0-9]+$', house_number))