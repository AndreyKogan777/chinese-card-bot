import hashlib
import hmac
import json

from flask import Flask, request, jsonify

from config import GPL_API_KEY
from database import (
    add_paid_requests,
    init_db,
    payment_already_processed,
    save_processed_payment,
)

app = Flask(__name__)
init_db()


def build_checksum_string(data: dict) -> str:
    params = dict(data)
    params.pop("checksum", None)
    params.pop("customParams", None)

    sorted_keys = sorted(params.keys(), key=lambda k: k.lower())

    sign_parts = []
    for key in sorted_keys:
        value = params[key]
        if isinstance(value, bool):
            value = int(value)
        elif isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        elif value is None:
            value = ""
        sign_parts.append(f"{key};{value};")
    return "".join(sign_parts)


def calculate_checksum(data: dict) -> str:
    sign_string = build_checksum_string(data)
    signature = hmac.new(
        GPL_API_KEY.encode("utf-8"),
        sign_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest().upper()
    return signature


@app.route("/gpl-webhook", methods=["POST"])
def gpl_webhook():
    data = request.get_json(force=True)

    received_checksum = data.get("checksum", "")
    calculated_checksum = calculate_checksum(data)

    if received_checksum != calculated_checksum:
        return jsonify({"status": "error", "reason": "invalid checksum"}), 400

    notification_type = data.get("notificationType")
    is_success = data.get("isSuccess")
    deal_id = data.get("dealId")
    custom_params = data.get("customParams", {}) or {}

    user_id = custom_params.get("user_id")
    requests_count = custom_params.get("requests_count")

    if notification_type != 1 or is_success is not True:
        return jsonify({"status": "ignored", "reason": "not successful payment"}), 200

    if not deal_id or not user_id or not requests_count:
        return jsonify({"status": "error", "reason": "missing required fields"}), 400

    if payment_already_processed(deal_id):
        return jsonify({"status": "ok", "reason": "already processed"}), 200

    add_paid_requests(int(user_id), int(requests_count))
    save_processed_payment(deal_id, int(user_id), int(requests_count), "success")

    return jsonify({"status": "ok"}), 200


@app.route("/health", methods=["GET"])
def health():
    return "OK", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

