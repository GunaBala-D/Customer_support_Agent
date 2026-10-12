from app.services.orchestrator import handle_chat


class Chat:
    """Tiny conversation driver: one object = one conversation."""

    def __init__(self, container, session, customer="CUST-1001"):
        self.c, self.s, self.customer, self.conv = container, session, customer, None

    def say(self, message: str) -> dict:
        r = handle_chat(self.c, self.s, message=message, conversation_id=self.conv, customer_id=self.customer)
        self.conv = r["conversation_id"]
        return r
