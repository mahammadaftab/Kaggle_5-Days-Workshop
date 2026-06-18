import base64
import json
import re
from typing import Any
from pydantic import BaseModel
from google.adk import Agent, Workflow
from google.adk.agents.context import Context
from google.adk.events import Event, RequestInput
from google.adk.workflow import node
from google.genai import types

from .config import THRESHOLD_USD, MODEL_NAME


# ==============================================================================
# 1. State Schema Definition
# ==============================================================================
class ExpenseState(BaseModel):
    """Workflow state schema for validating and tracking expense data."""
    amount: float = 0.0
    submitter: str = ""
    category: str = ""
    description: str = ""
    date: str = ""
    risk_assessment: str | None = None
    approval_status: str | None = None
    redacted_categories: list[str] = []
    security_flag: bool = False


# ==============================================================================
# 2. Workflow Nodes (Function Nodes & Agents)
# ==============================================================================

@node
def parse_expense_node(ctx: Context, node_input: Any) -> Any:
    """Parses incoming JSON/PubSub payload and populates the workflow state."""
    payload = {}
    if isinstance(node_input, dict):
        payload = node_input
    elif isinstance(node_input, str):
        try:
            payload = json.loads(node_input)
        except Exception:
            pass
    else:
        # Handle types.Content or other objects
        from google.adk.utils.content_utils import extract_text_from_content
        text = extract_text_from_content(node_input)
        if text:
            try:
                payload = json.loads(text)
            except Exception:
                try:
                    payload = json.loads(str(node_input))
                except Exception:
                    pass

    # Extract Pub/Sub message data if present
    message_data = None
    if "message" in payload and isinstance(payload["message"], dict):
        message_data = payload["message"].get("data")
    else:
        message_data = payload.get("data")

    # If message_data is base64 or direct JSON
    expense_data = None
    if message_data is not None:
        if isinstance(message_data, dict):
            expense_data = message_data
        elif isinstance(message_data, str):
            # Try base64 decoding first
            try:
                decoded = base64.b64decode(message_data).decode("utf-8")
                expense_data = json.loads(decoded)
            except Exception:
                # Fallback: parse direct JSON string
                try:
                    expense_data = json.loads(message_data)
                except Exception:
                    pass

    # Fallback to entire payload if no explicit data/message envelope
    if expense_data is None:
        expense_data = payload

    # Extract fields with fallbacks
    amount = float(expense_data.get("amount", 0.0))
    submitter = str(expense_data.get("submitter", "Unknown"))
    category = str(expense_data.get("category", "General"))
    description = str(expense_data.get("description", "No description"))
    date = str(expense_data.get("date", ""))

    # Save to workflow context state
    ctx.state["amount"] = amount
    ctx.state["submitter"] = submitter
    ctx.state["category"] = category
    ctx.state["description"] = description
    ctx.state["date"] = date
    ctx.state["redacted_categories"] = []
    ctx.state["security_flag"] = False

    return expense_data


@node
def security_checkpoint(ctx: Context, node_input: Any) -> Any:
    """Scrubs SSNs and Credit Cards from description, and checks for prompt injection."""
    description = ctx.state.get("description", "")
    redacted_categories = []

    # 1. Scrub SSNs (e.g. 123-45-6789)
    ssn_pattern = r"\b\d{3}-\d{2}-\d{4}\b"
    if re.search(ssn_pattern, description):
        description = re.sub(ssn_pattern, "[REDACTED SSN]", description)
        redacted_categories.append("SSN")

    # 2. Scrub Credit Cards (matches sequences of 13 to 16 digits with spaces or hyphens)
    cc_pattern = r"\b(?:\d[ -]*?){13,16}\b"
    if re.search(cc_pattern, description):
        description = re.sub(cc_pattern, "[REDACTED CREDIT CARD]", description)
        redacted_categories.append("Credit Card")

    # Save redacted fields to context state
    ctx.state["description"] = description
    ctx.state["redacted_categories"] = redacted_categories

    # 3. Prompt Injection Detection Heuristics
    injection_keywords = [
        "ignore former", "ignore previous", "ignore instruction", "ignore rule",
        "bypass rule", "bypass threshold", "force auto-approve", "force autoapprove",
        "force approve", "override rule", "override threshold", "instead approve",
        "you are now an auto-approval", "you are now a bot", "system prompt",
        "auto-approve this", "autoapprove this", "approve instantly"
    ]

    security_flag = False
    desc_lower = description.lower()
    for kw in injection_keywords:
        if kw in desc_lower:
            security_flag = True
            break

    ctx.state["security_flag"] = security_flag

    return node_input


@node
def router_node(ctx: Context, node_input: Any) -> Event:
    """Determines whether an expense requires LLM/human review or auto-approval."""
    # Prompts that trigger security warnings are automatically routed to requires_review
    if ctx.state.get("security_flag"):
        return Event(route="requires_review")

    amount = ctx.state.get("amount", 0.0)
    if amount < THRESHOLD_USD:
        return Event(route="auto_approve")
    else:
        return Event(route="requires_review")


@node
def auto_approve_node(ctx: Context, node_input: Any) -> Event:
    """Sets approval status directly for low-value expenses."""
    ctx.state["approval_status"] = "Approved (Auto)"
    output_str = f"Expense of ${ctx.state.get('amount')} submitted by {ctx.state.get('submitter')} was automatically approved."
    return Event(
        content=types.Content(parts=[types.Part(text=output_str)]),
        output=output_str
    )


# Specialist agent for evaluating risk on large expenses
risk_assessment_agent = Agent(
    model=MODEL_NAME,
    name="risk_assessment_agent",
    description="Compliance LLM that assesses policy risk for high-value expenses.",
    instruction=(
        "You are an expense compliance risk auditor. Assess the following expense details for policy risk factors:\n"
        "- Submitter: {submitter}\n"
        "- Amount: ${amount}\n"
        "- Category: {category}\n"
        "- Description: {description}\n"
        "- Date: {date}\n\n"
        "Analyze this expense and look for potential compliance issues (e.g. category mismatch, unusually high amount, split receipts, or generic description).\n"
        "Provide a concise summary highlighting any high or low risk factors. If there are no concerns, state that clearly."
    ),
)


@node
def prepare_audit_prompt(ctx: Context, node_input: Any) -> str:
    """Constructs the prompt for the risk assessment agent using state variables."""
    amount = ctx.state.get("amount")
    submitter = ctx.state.get("submitter")
    category = ctx.state.get("category")
    description = ctx.state.get("description")
    date = ctx.state.get("date")

    prompt = (
        f"Please analyze this expense report for compliance and risk:\n"
        f"- Submitter: {submitter}\n"
        f"- Amount: ${amount}\n"
        f"- Category: {category}\n"
        f"- Description: {description}\n"
        f"- Date: {date}\n"
        f"Assess potential policy violations or suspicious activity."
    )
    return prompt


@node
def save_audit_result(ctx: Context, node_input: Any) -> Any:
    """Saves the risk auditor's assessment text back into the context state."""
    if isinstance(node_input, str):
        assessment = node_input
    else:
        from google.adk.utils.content_utils import extract_text_from_content
        try:
            assessment = extract_text_from_content(node_input)
        except Exception:
            assessment = str(node_input)
    ctx.state["risk_assessment"] = assessment
    return node_input


@node
def requires_review_router(ctx: Context, node_input: Any) -> Event:
    """Routes the workflow either to the LLM reviewer or directly to human review (bypassing LLM) if security flag is active."""
    if ctx.state.get("security_flag"):
        ctx.state["risk_assessment"] = "SECURITY ALERT: Potential prompt injection detected in description. Model evaluation bypassed for safety."
        return Event(route="bypass_llm")
    else:
        return Event(route="run_llm")


@node(rerun_on_resume=True)
def human_review_node(ctx: Context, node_input: Any) -> Any:
    """Pauses the workflow for human approval and processes the decision on resume."""
    interrupt_id = "human_approval"
    response = ctx.resume_inputs.get(interrupt_id)

    if response is None:
        amount = ctx.state.get("amount")
        submitter = ctx.state.get("submitter")
        risk_assessment = ctx.state.get("risk_assessment")
        security_flag = ctx.state.get("security_flag", False)
        redacted = ctx.state.get("redacted_categories", [])

        # Construct alert headers for privacy and security warnings
        alert_header = ""
        if security_flag:
            alert_header += "🚨 SECURITY ALERT: Potential Prompt Injection Detected! LLM review was bypassed.\n"
        if redacted:
            alert_header += f"🔒 PRIVACY NOTICE: Personal data redacted: {', '.join(redacted)}\n"

        message = (
            f"\n⚠️ ACTION REQUIRED: Human Review Needed\n"
            f"----------------------------------------\n"
            f"{alert_header}"
            f"Submitter: {submitter}\n"
            f"Amount: ${amount}\n"
            f"Risk Assessment:\n{risk_assessment}\n"
            f"----------------------------------------\n"
            f"Please enter your decision ('approve' or 'reject'):"
        )
        return RequestInput(interrupt_id=interrupt_id, message=message)

    # Process human decision when resuming
    if isinstance(response, dict):
        decision = response.get("response", response.get("decision", next(iter(response.values())) if response else ""))
    else:
        decision = response
    decision = str(decision).strip().lower()
    security_flag = ctx.state.get("security_flag", False)
    status_suffix = " (Security Audit Required)" if security_flag else ""

    if "approve" in decision:
        ctx.state["approval_status"] = f"Approved{status_suffix}"
        output_str = f"Expense of ${ctx.state.get('amount')} by {ctx.state.get('submitter')} was approved by human reviewer."
    else:
        ctx.state["approval_status"] = f"Rejected{status_suffix}"
        output_str = f"Expense of ${ctx.state.get('amount')} by {ctx.state.get('submitter')} was rejected by human reviewer."

    return Event(
        content=types.Content(parts=[types.Part(text=output_str)]),
        output=output_str
    )


# ==============================================================================
# 3. Graph Workflow Definition
# ==============================================================================
root_agent = Workflow(
    name="root_agent",
    description="Ambient expense-approval workflow agent.",
    state_schema=ExpenseState,
    edges=[
        # 1. Parse expense -> run security checkpoint -> determine routing path
        ("START", parse_expense_node, security_checkpoint, router_node),

        # 2. Amount-based routing (auto-approve vs requires review)
        (
            router_node,
            {
                "auto_approve": auto_approve_node,
                "requires_review": requires_review_router,
            },
        ),

        # 3. Requires review routing (bypasses LLM if prompt injection was flagged)
        (
            requires_review_router,
            {
                "bypass_llm": human_review_node,
                "run_llm": prepare_audit_prompt,
            },
        ),

        # 4. Large expense flow: audit prompt -> risk review -> save results -> HITL
        (
            prepare_audit_prompt,
            risk_assessment_agent,
            save_audit_result,
            human_review_node,
        ),
    ],
)
