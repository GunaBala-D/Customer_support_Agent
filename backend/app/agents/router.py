"""Intent routing.

Stage 1 - deterministic rules for high-precision cases (order IDs, cancel, human request...).
         Cheap, fast, testable, and does not depend on a small model's mood.
Stage 2 - LLM classification only when the rules are inconclusive (see agents/graph.py).

Identifiers (order IDs) are always extracted by regex, never by the model.
"""
import re

INTENTS = (
    "knowledge_question", "order_status", "customer_information", "refund_question",
    "cancellation_request", "complaint", "unknown", "escalation",
)

ORDER_ID_RE = re.compile(r"\bORD-\d{3,8}\b", re.I)
YES_RE = re.compile(
    r"^\s*(yes|yep|yeah|yup|sure|ok|okay|confirm(ed)?|go ahead|proceed|do it|please do|"
    r"yes[, ]+(please|proceed|cancel it|go ahead|confirm))\W*$", re.I)
NO_RE = re.compile(r"^\s*(no|nope|nah|don'?t|do not|never ?mind|stop|keep it|no thanks?|not now)\W*$", re.I)

HUMAN_RE = re.compile(
    r"\b(human|real person|live agent|representative|supervisor|manager|escalate|talk to (an? )?(agent|person)|"
    r"speak (to|with) (an? )?(agent|person|someone))\b", re.I)
PAYMENT_ISSUE_RE = re.compile(
    r"\b(payment|paid|charged?|money)\b.*\b(wasn'?t|was not|not|never|didn'?t|no)\b.*\b(order|created|confirm\w*|received)\b|"
    r"\b(payment|charge)\b.*\b(successful|went through|deducted)\b.*\b(but|yet|however)\b", re.I)
COMPLAINT_RE = re.compile(
    r"\b(terrible|awful|angry|unacceptable|worst|furious|disappointed|complain(t)?|fed up|ridiculous|"
    r"scam|disgusting|useless|horrible)\b", re.I)
CANCEL_RE = re.compile(r"\bcancel\w*\b", re.I)
IMPERATIVE_CANCEL_RE = re.compile(
    r"\b(please cancel|cancel (my|the|this|that|it|order|ord-)|(i )?(want|need|would like|wish) to cancel)\b", re.I)
INTERROGATIVE_RE = re.compile(
    r"^\s*(how|what|when|where can|can i|is it possible|do you|does|are|is)\b|\b(policy|policies|fee|deadline|window)\b", re.I)
ITEMS_RE = re.compile(
    r"\b(what('?s| is| was| are)?\s+(in|inside)|which items|what items|what did i (order|buy)|items? (in|of)|contents? of)\b", re.I)
ITEMS_PRONOUN_RE = re.compile(r"\b(it|them)\b", re.I)
LOST_RE = re.compile(
    r"\b(can'?t|cannot|could not|couldn'?t|unable to) (find|locate)\b|\b(lost|missing|never (arrived|came|showed up)|"
    r"(didn'?t|did not|hasn'?t|has not) (arrive|come|show up|arrived))\b", re.I)
STATUS_RE = re.compile(
    r"\b(where|status|track(ing)?|shipped|deliver(ed)?|arrive|arrival|eta|dispatch(ed)?|on its way|when (will|is|does))\b", re.I)
MY_ORDER_RE = re.compile(r"\b(my|the|this|that) (order|package|parcel|shipment|purchase)\b", re.I)
PRONOUN_RE = re.compile(r"\b(it|this|that|they)\b", re.I)
REFUND_RE = re.compile(r"\b(refund\w*|return\w*|money back|exchange|send (it|this|that|them) back|replacement)\b", re.I)
PERSONAL_REFUND_RE = re.compile(
    r"\b(can i|could i|am i|is (it|my order)|i want to|i'd like to|i would like to|i need to)\b.*\b(return|refund|send back)\b|"
    r"\b(eligible|eligibility)\b", re.I)
INFO_RE = re.compile(
    r"\b(show|what'?s|what is|tell me|check|view|see|get|display)\b.*\bmy (account|profile|email|address|phone|"
    r"details|info(rmation)?|membership|tier)\b|\bwho am i\b", re.I)
GREETING_RE = re.compile(r"^\s*(hi|hello|hey|good (morning|afternoon|evening)|thanks?|thank you|thx|bye|ok(ay)?)\b\W*$", re.I)
KNOWLEDGE_HINT_RE = re.compile(
    r"\b(policy|policies|shipping|delivery|warranty|payment|pay|hours|open|contact|password|account|privacy|"
    r"return|refund|cancel\w*|international|track|invoice|support|price|cost|fee|data|delete|methods?)\b", re.I)


def extract_order_ids(text: str) -> list[str]:
    seen: list[str] = []
    for m in ORDER_ID_RE.findall(text):
        oid = m.upper()
        if oid not in seen:
            seen.append(oid)
    return seen


def classify_rules(message: str, memory_has_order: bool = False) -> tuple[str, str] | None:
    """Return (intent, reason) or None when the rules cannot decide."""
    m = message.strip()
    ids = extract_order_ids(m)

    if HUMAN_RE.search(m):
        return "escalation", "customer asked for a human agent"
    if PAYMENT_ISSUE_RE.search(m):
        return "escalation", "payment/order mismatch needs manual investigation"
    if CANCEL_RE.search(m):
        if ids or IMPERATIVE_CANCEL_RE.search(m) and not re.match(r"^\s*(how|what|when)\b", m, re.I):
            return "cancellation_request", "cancel request with action wording"
        if INTERROGATIVE_RE.search(m):
            return "knowledge_question", "question about cancellation rules"
        return "cancellation_request", "cancel keyword"
    if COMPLAINT_RE.search(m):
        return "complaint", "complaint wording"
    if ITEMS_RE.search(m) and (ids or MY_ORDER_RE.search(m) or (memory_has_order and ITEMS_PRONOUN_RE.search(m))):
        return "order_status", "asks what an order contains"  # the order summary lists the items
    if LOST_RE.search(m):
        # "says delivered but I can't find it": a lost-package case, not a status lookup.
        if ids:
            return "escalation", "lost or missing package needs a carrier investigation"
        return "knowledge_question", "question about a missing package"
    if STATUS_RE.search(m) and (ids or MY_ORDER_RE.search(m) or (memory_has_order and PRONOUN_RE.search(m))):
        return "order_status", "status wording with an order reference"
    if REFUND_RE.search(m):
        return "refund_question", "refund/return wording"
    if INFO_RE.search(m):
        return "customer_information", "asks about own account details"
    if ids:
        return "order_status", "order ID provided"
    if GREETING_RE.match(m):
        return "unknown", "greeting / small talk"
    return None


def looks_like_knowledge(message: str) -> bool:
    return "?" in message or bool(KNOWLEDGE_HINT_RE.search(message))


def wants_personal_refund_check(message: str, has_order: bool) -> bool:
    """Refund question about *their* order (use the tool) vs. a general policy question (use RAG)."""
    if not has_order:
        return False
    ids = extract_order_ids(message)
    if not ids and re.match(r"^\s*(how|what)\b", message, re.I):
        return False  # "How can I return...?" is a how-to question for the knowledge base
    return bool(ids) or bool(PERSONAL_REFUND_RE.search(message)) or bool(MY_ORDER_RE.search(message))
