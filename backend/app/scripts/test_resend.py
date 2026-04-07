"""Quick Resend delivery test."""
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s")

from dotenv import load_dotenv
load_dotenv()

from app.core.email import send_email

ok = send_email(
    to="tedialarana@gmail.com",
    subject="Resend test — Hedge Spark",
    html="<p>If you see this, Resend is working.</p>",
    text="If you see this, Resend is working.",
)

print(f"\nRESULT: {'SUCCESS' if ok else 'FAIL'}")
sys.exit(0 if ok else 1)
