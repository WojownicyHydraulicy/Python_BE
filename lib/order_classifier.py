"""!
@file order_classifier.py
@brief Moduł klasyfikacji i wyceny zleceń hydraulicznych
@details Zawiera klasę OrderClassifier, która wykorzystuje model AI Google Vertex do oceny trudności,
         wyceny i weryfikacji zleceń hydraulicznych na podstawie opisu i zdjęcia.
@author Piotr
@date 2023
"""
import requests
from io import BytesIO
from vertexai.vision_models import Image
from vertexai.generative_models import GenerativeModel
import vertexai
from dotenv import load_dotenv
import os
import json

class OrderClassifier:
    """!
    @brief Klasa do wyceny i oceny trudności zgłoszeń klienckich dotyczących usług hydraulicznych
    @details Wykorzystuje model AI z Google Vertex AI do analizy opisów i zdjęć zgłoszeń hydraulicznych.
             Zwraca kategorię trudności, przedział cenowy, odpowiedź dla klienta 
             oraz informację o poprawności zgłoszenia.
    """

    def __init__(self, project_id, location, model_name):
        """!
        @brief Konstruktor klasy OrderClassifier
        @param project_id Identyfikator projektu Google Cloud
        @param location Region, w którym znajduje się model AI
        @param model_name Nazwa modelu Gemini do wykorzystania
        """
        self.project_id = project_id
        self.location = location
        self.model_name = model_name

    def initialize(self):
        """!
        @brief Inicjalizuje połączenie z Google Vertex AI i ładuje model
        @details Tworzy instancję modelu generatywnego Gemini dostępną do użycia
        @exception Exception Wyrzuca wyjątek w przypadku błędu inicjalizacji modelu
        """
        try:
            vertexai.init(project=self.project_id, location=self.location)
            self.model = GenerativeModel(self.model_name)

        except Exception as e:
            print("❌ Błąd podczas inicjalizacji lub wywołania modelu:", e)
            raise e  # Podbij błąd dalej jeśli chcesz go obsłużyć wyżej

    def evaluate_difficulty(self, prompt_text: str, image_url: str = None) -> dict:
        """!
        @brief Ocenia trudność usterki hydraulicznej i proponuje wycenę
        @details Analizuje opis usterki podany przez klienta oraz opcjonalne zdjęcie.
                 Na podstawie tych danych określa poziom trudności naprawy, szacunkową cenę
                 i generuje odpowiedź dla klienta. Dodatkowo weryfikuje, czy zgłoszenie
                 jest poprawne i dotyczy faktycznie hydrauliki.
        
        @param prompt_text Tekstowy opis usterki podany przez klienta
        @param image_url Opcjonalny URL do zdjęcia usterki
        
        @return Słownik zawierający:
                - flaw_category: kategoria trudności (NISKI, ŚREDNI, WYSOKI, BARDZO WYSOKI, WYCENA NIEMOŻLIWA)
                - price: przedział cenowy naprawy
                - client_response: tekst odpowiedzi dla klienta
                - is_valid_request: wartość logiczna określająca, czy zgłoszenie jest prawidłowe
        """
        inputs = []

        prompt = f"""
        Jesteś doświadczonym polskim hydraulikiem i ekspertem w wycenie usług hydraulicznych na rynku w Polsce w 2025 roku.
        Twoim zadaniem jest ocenić poziom trudności zgłoszenia hydraulicznego na podstawie opisu klienta i ewentualnego zdjęcia.
        Weź pod uwagę typowe ceny usług hydraulicznych w Polsce, w tym koszt dojazdu, robocizny i materiałów.
        Przyjmij następujące widełki cenowe:
        - NISKI: 150-250 zł (np. wymiana uszczelki, naprawa cieknącego kranu, drobne naprawy)
        - ŚREDNI: 250-500 zł (np. wymiana baterii, montaż WC, naprawa spłuczki, udrażnianie odpływu)
        - WYSOKI: 500-1200 zł (np. poważniejsze awarie, wymiana rur, montaż kabiny prysznicowej, usuwanie poważnych przecieków)
        - BARDZO WYSOKI: powyżej 1200 zł (np. generalny remont instalacji, rozległe uszkodzenia, prace wymagające wielu dni)
        Jeśli nie jesteś w stanie wycenić zgłoszenia na podstawie opisu i zdjęcia, zwróć 'WYCENA NIEMOŻLIWA' jako flaw_category
        oraz w polu client_response wyjaśnij czemu nie jesteś stanie wycenić zgłoszenia.
        Nie proś o kontakt z klientem. Skontaktujemy się z nim sami mailowo.
        Oceń, czy zgłoszenie jest sensowne i zawiera wystarczająco dużo informacji, aby można było je przyjąć. 
        Jeśli opis jest nie na temat, ktoś sobie jawnie żartuje lub nie dotyczy hydrauliki, ustaw is_valid_request na false. 
        W przeciwnym razie ustaw is_valid_request na true.

        Zwróć wynik w formacie JSON o polach:
            - flaw_category: jeden z ['NISKI', 'ŚREDNI', 'WYSOKI', 'BARDZO WYSOKI', 'WYCENA NIEMOŻLIWA']
            - price: przedział cenowy w formacie 'od - do' (np. '150-250 zł') lub 'powyżej 1200 zł' dla bardzo wysokiego poziomu
            - client_response: krótka, uprzejma wiadomość do klienta wyjaśniająca decyzję i orientacyjną cenę
            - is_valid_request: true jeśli zgłoszenie jest sensowne i kompletne, false jeśli nie

        Opis zgłoszenia: {prompt_text}
        """

        
        inputs.append(prompt)

        if image_url:
            try:
                response = requests.get(image_url)
                response.raise_for_status()
                image_bytes = BytesIO(response.content)
                image = Image.load_from_file(image_bytes)
                inputs.append(image)
            except Exception as e:
                # Dodaj uwagę do promptu, jeśli obraz się nie ładuje
                prompt += "\n(Uwaga: Obraz nie mógł zostać załadowany, oceń tylko na podstawie opisu.)"

        # Wymuszamy format JSON
        response = self.model.generate_content(
            inputs,
            generation_config={
                "response_mime_type": "application/json"
            }
        )

        try:
            json_str = response.candidates[0].content.parts[0].text
            return json.loads(json_str)
        except Exception as e:
            return {
                "flaw_category": "WYCENA NIEMOŻLIWA",
                "price": "",
                "client_response": f"Nie udało się przetworzyć zgłoszenia: {str(e)}",
                "is_valid_request": False
            }

if __name__ == "__main__":
    # Przykładowe użycie
    load_dotenv('../.env')
    project_id = os.getenv("PROJECT_ID")
    location = os.getenv("GCLOUD_REGION")
    model_name = os.getenv("GEMINI_MODEL")
    
    print(project_id, location, model_name)
    
    classifier = OrderClassifier(
        project_id=project_id,
        location=location,
        model_name=model_name
    )
    classifier.initialize()
    url = "https://storage.googleapis.com/arch_oprog_photos/168fa710-5dea-4717-9c98-a6ff8acd3872.jpeg"
    result = classifier.evaluate_difficulty("Kran mi cieknie", url)
    print(result)