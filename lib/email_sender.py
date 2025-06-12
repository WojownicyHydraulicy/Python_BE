import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import logging

class GmailSender:
    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self.smtp_server = "smtp.gmail.com"
        self.smtp_port = 587

    def authenticate(self):
        try:
            self.server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            self.server.starttls()
            self.server.login(self.email, self.password)
            return True
        except Exception as e:
            print(f"Authentication failed: {e}")
            return False

    def send_email(self, recipient: str, subject: str, body: str):
        try:
            # Nawiązanie połączenia i logowanie
            self.server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            self.server.starttls()
            self.server.login(self.email, self.password)

            # Utworzenie wiadomości
            msg = MIMEMultipart("alternative")
            msg['From'] = self.email
            msg['To'] = recipient
            msg['Subject'] = subject

            # Dodanie treści (HTML)
            msg.attach(MIMEText(body, 'html'))

            # Wysyłka
            self.server.sendmail(self.email, recipient, msg.as_string())
            return {"status": "success", "message": "Email sent successfully"}

        except Exception as e:
            logging.error(f"Failed to send email to {recipient}: {e}")
            return {"status": "error", "message": f"Failed to send email: {e}"}

        finally:
            if hasattr(self, 'server'):
                try:
                    self.server.quit()
                except Exception as e:
                    logging.warning(f"Failed to close SMTP connection: {e}")
                
    """!
    @brief Wysyła email z potwierdzeniem złożenia zamówienia na naprawę hydrauliczną
    @details Tworzy i wysyła wiadomość email z szablonu HTML zawierającą potwierdzenie
             przyjęcia zamówienia na naprawę hydrauliczną.
    @param recipient Adres email odbiorcy wiadomości
    @param order_id Unikalny identyfikator zamówienia
    @return Słownik zawierający status wysyłki ('success' lub 'error') oraz wiadomość
    @exception Exception Wszelkie wyjątki obsługiwane są wewnątrz metody send_email
    """
    def send_order_confirmation(self, recipient: str, order_id: str):
        subject = f"Potwierdzenie złożenia na naprawę hydrauliczną"
        body = f"""
        <html>
            <body>
            <p>Dzień dobry,</p>
            <p>Dziękujemy za zgłoszenie <strong>naprawy hydraulicznej</strong> o numerze <strong>{order_id}</strong>.</p>
            <p>W razie dodatkowych pytań prosimy o odpowiedź na tę wiadomość.</p>
            <br/>
            <p>Z pozdrowieniami,<br/>
            <strong>Zespół Hydropol</strong></p>
            </body>
        </html>
        """
        return self.send_email(recipient, subject, body)
    
    """!
    @brief Wysyła email z informacją o zakończeniu naprawy hydraulicznej
    @details Tworzy i wysyła wiadomość email z szablonu HTML zawierającą informację
             o pomyślnym zakończeniu zlecenia naprawy hydraulicznej wraz z prośbą o opinię.
    @param recipient Adres email odbiorcy wiadomości
    @param order_id Unikalny identyfikator zakończonego zamówienia
    @return Słownik zawierający status wysyłki ('success' lub 'error') oraz wiadomość
    @exception Exception Wszelkie wyjątki obsługiwane są wewnątrz metody send_email
    """
    def send_order_completed(self, recipient: str, order_id: str):
        subject = f"Zakończenie naprawy hydraulicznej – {order_id}"
        body = f"""
        <html>
            <body>
            <p>Dzień dobry,</p>
            <p>Z przyjemnością informujemy, że zlecenie numer <strong>{order_id}</strong> zostało pomyślnie zakończone.</p>
            <p>Bardzo dziękujemy za skorzystanie z naszych usług. Jeśli byli Państwo zadowoleni z przebiegu naprawy, bylibyśmy wdzięczni za wystawienie pozytywnej opinii.</p>
            <br/>
            <p>Z pozdrowieniami,<br/>
            <strong>Zespół Hydropol</strong></p>
            </body>
        </html>
        """
        return self.send_email(recipient, subject, body)

    def send_order_rejection(self, recipient: str, order_id: str):
        subject = f"Odrzucenie zlecenia hydraulicznego – {order_id}"
        body = f"""
        <html>
            <body>
            <p>Dzień dobry,</p>
            <p>Z przykrością informujemy, że zlecenie numer <strong>{order_id}</strong> zostało odrzucone przez technika.</p>
            <p>Aby poznać przyczynę odrzucenia lub uzyskać więcej informacji, prosimy o kontakt z naszą infolinią.</p>
            <br/>
            <p>Z pozdrowieniami,<br/>
            Zespół <strong>Hydropol</strong></p>
            </body>
        </html>
        """
        return self.send_email(recipient, subject, body)