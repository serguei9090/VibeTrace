"""Client module orchestrating data pipeline execution."""

from service import DataProcessor


class ServiceClient:
    """Client consuming DataProcessor to run batch ingestion."""

    def __init__(self):
        self.processor = DataProcessor(prefix="prod")

    def run_pipeline(self, batch_id: str, items: int) -> str:
        """Run batch processing pipeline."""
        output = self.processor.process_payload(batch_id, items)
        return output


def execute_job() -> str:
    """Top-level job entrypoint."""
    client = ServiceClient()
    return client.run_pipeline("batch-101", 5)
