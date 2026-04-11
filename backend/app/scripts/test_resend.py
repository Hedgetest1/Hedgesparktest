"""Quick Resend delivery test — sends branded beta welcome email."""
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s")

from dotenv import load_dotenv
load_dotenv()

from app.services.email_templates import render_email
from app.core.email import send_email

subject, html, text = render_email("beta_welcome", {
    "shop_name": "your store",
    "merchant_name": "Andrea",
})

ok = send_email(
    to="tedialarana@gmail.com",
    subject=subject,
    html=html,
    text=text,
    from_address="HedgeSpark <dev@hedgesparkhq.com>",
)

print(f"\nRESULT: {'SUCCESS' if ok else 'FAIL'}")
sys.exit(0 if ok else 1)
