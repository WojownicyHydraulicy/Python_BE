FROM python:3.11-slim

# Ustawiamy working dir
WORKDIR /app

# Kopiujemy zależności i instalujemy
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopiujemy wszystko
COPY . .

# Uruchamiamy aplikację FastAPI przez uvicorn
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8080"]
