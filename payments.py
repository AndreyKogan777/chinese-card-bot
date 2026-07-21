import time
import requests
from config import GPL_API_KEY, GPL_ACCOUNT_NAME

GPL_BASE_URL = f"https://{GPL_ACCOUNT_NAME}.getplatinum.ru/api/public/pay/init-payment-url"

PACKAGE = {
    "requests": 5,
    "price_rub": 500,
    "title": "Пакет 5 запросов",
    "offer_id": 2,
}


def create_payment_link(user_id: int) -> str:
    deal_id = f"user{user_id}_{int(time.time())}"
    headers = {"Authorization": f"Bearer {GPL_API_KEY}"}
    payload = {
        "offerId": PACKAGE["offer_id"],
        "amount": PACKAGE["price_rub"],
        "currency": "RUB",
        "description": PACKAGE["title"],
        "dealId": deal_id,
        "customParams": {
            "user_id": str(user_id),
            "requests_count": str(PACKAGE["requests"]),
        },
    }
    resp = requests.post(GPL_BASE_URL, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data["formUrl"]

