"""Sends email notifications via Gmail SMTP."""
import os
import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv

load_dotenv()

SENDER = os.getenv("SENDER_EMAIL")
PASSWORD = os.getenv("SENDER_APP_PASSWORD")
RECIPIENT = os.getenv("RECIPIENT_EMAIL")


def send_email(subject, body):
    """Send one email with the given subject and body text."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SENDER
    msg["To"] = RECIPIENT
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(SENDER, PASSWORD)
        server.send_message(msg)
    print(f"Email sent to {RECIPIENT}")


if __name__ == "__main__":
    send_email(
        subject="MovieBot test",
        body="If you're reading this, your email setup works!",
    )
