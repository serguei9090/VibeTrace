"""Service module providing payload processing and metrics calculation."""


class DataProcessor:
    """Core processor for ingested batch payloads."""

    def __init__(self, prefix: str = "item"):
        self.prefix = prefix

    def process_payload(self, item_id: str, count: int) -> str:
        """Format and validate single payload item."""
        result = self.format_token(item_id, count)
        return result

    def format_token(self, item_id: str, count: int) -> str:
        """Format identifier with prefix and repetition count."""
        return f"{self.prefix}:{item_id}:{count}"
