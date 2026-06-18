from typing import Any
from google.adk import Agent, Workflow
from google.adk.workflow import node
from google.adk.events.event import Event

# Specialist Agents
classifier_agent = Agent(
    model="gemini-2.5-flash",
    name="classifier_agent",
    description="Classifies whether a user query is related to shipping or unrelated.",
    instruction=(
        "Analyze the user query. Determine if it is related to shipping (rates, tracking, delivery, returns) "
        "or completely unrelated.\n"
        'Output exactly "shipping" if it is related to shipping, '
        'or "unrelated" if it is unrelated. Do not output any other text or punctuation.'
    ),
)

shipping_faq_agent = Agent(
    model="gemini-2.5-flash",
    name="shipping_faq_agent",
    description="Answers questions about shipping rates, tracking, delivery, and returns.",
    instruction=(
        "You are a super friendly, playful, and enthusiastic customer support representative for a shipping company! 🚚✨\n"
        "Answer the user's shipping-related questions (rates, tracking, delivery, returns) helpfully, with lots of positive energy and appropriate emojis! 🎉\n"
        "Our standard shipping is a flat rate of $5.99, but make sure to enthusiastically highlight that we offer **FREE shipping on all orders over $50!** 🥳🎁"
    ),
)

decline_agent = Agent(
    model="gemini-2.5-flash",
    name="decline_agent",
    description="Politely declines to answer non-shipping questions.",
    instruction=(
        "You are a customer support representative for a shipping company.\n"
        "Politely decline to answer the user's query because it is unrelated to shipping (rates, tracking, delivery, returns).\n"
        "Explain that you can only help with shipping-related inquiries."
    ),
)


# Helper function to extract text from content objects (e.g. types.Content)
def get_text_from_content(content: Any) -> str:
    if not content:
        return ""
    if isinstance(content, str):
        return content
    # Handle types.Content / Event / dict structures
    if hasattr(content, "parts") and content.parts:
        return "".join(part.text for part in content.parts if part.text)
    if isinstance(content, dict):
        if "parts" in content:
            parts = content["parts"]
            if isinstance(parts, list):
                return "".join(
                    part.get("text", "")
                    for part in parts
                    if isinstance(part, dict) and "text" in part
                )
        if "text" in content:
            return content["text"]
    return str(content)


# Custom Node to save the original user query in session state
@node
def save_query(ctx: Any, node_input: Any) -> Any:
    query_text = get_text_from_content(node_input)
    ctx.state["original_query"] = query_text
    return node_input


# Custom Node to inspect the classification output and set the route
@node
def router(ctx: Any, node_input: Any) -> Event:
    classification = get_text_from_content(node_input).strip().lower()
    original_query = ctx.state.get("original_query", "")

    if "shipping" in classification:
        return Event(route="shipping", output=original_query)
    else:
        return Event(route="unrelated", output=original_query)


# Workflow Graph Definition
root_agent = Workflow(
    name="root_agent",
    description="Shipping company customer support workflow agent.",
    edges=[
        ("START", save_query, classifier_agent, router),
        (
            router,
            {
                "shipping": shipping_faq_agent,
                "unrelated": decline_agent,
            },
        ),
    ],
)
