from typing import Any


class OrderProcessor:
    """Manages order lifecycles and notifications."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    def process(self, order_id: str, items: list[Any]) -> bool:
        if not items:
            return False
        return self._notify(order_id)

    def _notify(self, order_id: str) -> bool:
        print(f"Notified order: {order_id}")
        return True
