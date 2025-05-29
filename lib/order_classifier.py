import requests
from io import BytesIO
from vertexai.vision_models import Image
from vertexai.generative_models import GenerativeModel
import vertexai
from dotenv import load_dotenv
import os
import json

class OrderClassifier:
    """Klasa do wyceny zgłoszeń klienckich."""

    def __init__(self, project_id, location, model_name):
        self.project_id = project_id
        self.location = location
        self.model_name = model_name

    def initialize(self):
        try:
            vertexai.init(project=self.project_id, location=self.location)
            self.model = GenerativeModel(self.model_name)

        except Exception as e:
            print("❌ Błąd podczas inicjalizacji lub wywołania modelu:", e)
            raise e  # Podbij błąd dalej jeśli chcesz go obsłużyć wyżej

    def evaluate_difficulty(self, prompt_text: str, image_url: str = None) -> dict:
        """
        Ocena trudności usterki hydraulicznej na podstawie opisu i ewentualnego zdjęcia.

        Args:
            prompt_text: Opis usterki podany przez klienta.
            image_url: (Opcjonalnie) URL do obrazu usterki.

        Returns:
            dict: JSON zawierający flaw_category i client_response.
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