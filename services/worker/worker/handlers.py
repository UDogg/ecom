"""What each consumer does with an event.

Handlers raise PermanentError for messages that can never succeed (they go
straight to the dead-letter queue) and any other exception for transient
failures (they are retried with backoff).
"""

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage


class PermanentError(Exception):
    """Retrying will not help: malformed or unsupported message."""


def money(minor, currency):
    return f"{minor / 100:,.2f} {currency}"


class SmtpSender:
    def __init__(self, host, port, sender):
        self.host, self.port, self.sender = host, port, sender

    def send(self, to, subject, body, message_id=None):
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        if message_id:
            # Stable per event, so a duplicate delivery is recognisable as such.
            msg["Message-ID"] = f"<{message_id}@shop.local>"
        msg.set_content(body)
        with smtplib.SMTP(self.host, self.port, timeout=10) as smtp:
            smtp.send_message(msg)


def require(data, *keys):
    missing = [k for k in keys if k not in data]
    if missing:
        raise PermanentError(f"missing fields: {', '.join(missing)}")


def order_confirmation(envelope, ctx):
    data = envelope["data"]
    require(data, "id", "items", "total_minor", "currency")
    to = data.get("customer_email")
    if not to:
        return "skipped: no customer email"
    short = data["id"][:8]
    lines = [f"  {item['quantity']} x {item['name']} ({item['sku']})  "
             f"{money(item['unit_price_minor'] * item['quantity'], data['currency'])}" for item in data["items"]]
    body = "\n".join([
        "Thanks for your order!",
        "",
        f"Order {short} is waiting for payment. We have reserved your items.",
        "",
        *lines,
        "",
        f"Total: {money(data['total_minor'], data['currency'])}",
        "",
        f"View it any time: {ctx.config.public_web_url}/orders/{data['id']}",
    ])
    ctx.mailer.send(to, f"Order {short} received", body, message_id=f"order-placed-{envelope['id']}")
    return "sent"


def payment_receipt(envelope, ctx):
    data = envelope["data"]
    require(data, "order_id", "amount_minor", "currency")
    to = data.get("customer_email")
    if not to:
        return "skipped: no customer email"
    short = data["order_id"][:8]
    body = "\n".join([
        "Payment received, thank you!",
        "",
        f"We have charged {money(data['amount_minor'], data['currency'])} for order {short}. It is being prepared now.",
        "",
        f"Track it: {ctx.config.public_web_url}/orders/{data['order_id']}",
    ])
    ctx.mailer.send(to, f"Payment received for order {short}", body, message_id=f"payment-captured-{envelope['id']}")
    return "sent"


def low_stock_alert(envelope, ctx):
    data = envelope["data"]
    require(data, "product_id", "available_quantity", "delta")
    quantity, threshold = data["available_quantity"], ctx.config.low_stock_threshold
    # Alert only when stock goes down into the low zone, not on every restock.
    if data["delta"] >= 0 or quantity > threshold:
        return "skipped: not low"
    body = (f"Product {data['product_id']} is down to {quantity} unit(s) "
            f"(threshold {threshold}) after: {data.get('reason', 'a stock change')}.\n\n"
            f"Restock it from the admin panel: {ctx.config.public_web_url}/admin/inventory")
    ctx.mailer.send(ctx.config.owner_alert_email, f"Low stock: {quantity} left", body,
                    message_id=f"low-stock-{envelope['id']}")
    return "sent"


@dataclass(frozen=True)
class Consumer:
    name: str
    routing_keys: tuple
    handle: object


CONSUMERS = {
    "order-email": Consumer("order-email", ("order.placed.v1",), order_confirmation),
    "low-stock": Consumer("low-stock", ("inventory.changed.v1",), low_stock_alert),
    "payment-email": Consumer("payment-email", ("payment.captured.v1",), payment_receipt),
}
