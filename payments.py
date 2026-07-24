import time
import requests
from config import GPL_API_KEY, GPL_ACCOUNT_NAME

GPL_BASE_URL = f"https://{GPL_ACCOUNT_NAME}.getplatinum.ru/api/public/pay/init-payment-url"

PACKAGE = {
    "requests": 5,
    "price_rub": 500,
    "title": "Пакет 5 запросов",
}

NOTIFICATION_URL = "https://worker-production-6e6d.up.railway.app/gpl-webhook"
SUCCESS_URL = "https://t.me/"


def create_payment_link(user_id: int) -> str:
    deal_id = f"user{user_id}-{int(time.time())}"
    headers = {
        "Authorization": f"Bearer {GPL_API_KEY}",
        "Content-Type": "application/json",
    }
    amount_kopecks = PACKAGE["price_rub"] * 100

    payload = {
        "dealId": deal_id,
        "currency": "RUB",
        "amount": amount_kopecks,
        "positions": [
            {
                "prefix": 1,
                "name": PACKAGE["title"],
                "price": amount_kopecks,
                "quantity": 1,
                "vat": "none",
            }
        ],
        "clientParams": {
            "clientId": str(user_id),
        },
        "notificationUrl": NOTIFICATION_URL,
        "successUrl": SUCCESS_URL,
        "customParams": {
            "user_id": str(user_id),
            "requests_count": str(PACKAGE["requests"]),
        },
    }

    resp = requests.post(GPL_BASE_URL, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if data.get("errorCode", 0) != 0:
        raise Exception(data.get("errorMessage") or "Unknown GetPlatinum error")

    return data["formUrl"]

