import logging
import os
import sys
from typing import Any, Literal

# Load .env manually if it exists to retrieve GOOGLE_API_KEY
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            if line.strip() and not line.strip().startswith("#"):
                parts = line.strip().split("=", 1)
                if len(parts) == 2:
                    os.environ[parts[0].strip()] = parts[1].strip()
from fastapi import FastAPI, Request, HTTPException
from google.adk.runners import InMemoryRunner
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import types

# 1. Logging Setup: Use standard Python logging for console logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("expense_agent.service")

# Ensure environment uses local settings and doesn't export telemetry to cloud
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "0"
# Explicitly turn off ADK cloud telemetry/traces
os.environ["OTEL_TO_CLOUD"] = "False"

# Import root agent from agent.py
from .agent import root_agent

# 2. SQLite session service for persistent storage
db_dir = os.path.join(os.path.dirname(__file__), ".adk")
os.makedirs(db_dir, exist_ok=True)
db_path = os.path.join(db_dir, "session.db")
logger.info(f"Using SQLite database for session storage: {db_path}")

session_service = SqliteSessionService(db_path=db_path)
runner = InMemoryRunner(node=root_agent)
runner.session_service = session_service
runner.auto_create_session = True

# Create FastAPI app
app = FastAPI(title="Ambient Expense Approval Service")

@app.post("/")
@app.post("/apps/expense_agent/trigger/pubsub")
async def handle_pubsub(request: Request):
    """POST endpoint to accept Pub/Sub trigger messages and feed them into the workflow."""
    try:
        payload = await request.json()
    except Exception:
        logger.error("Failed to parse JSON body from request")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Extract subscription path and normalize to short name
    fq_subscription = payload.get("subscription", "projects/local/subscriptions/default-sub")
    session_id = fq_subscription.split("/")[-1]
    
    logger.info(f"Received trigger for subscription: '{fq_subscription}' -> normalized session_id: '{session_id}'")
    
    user_id = "pubsub_trigger"
    body_str = json_dumps_payload = json_dumps_payload_str(payload)
    new_msg = types.Content(parts=[types.Part(text=body_str)])
    
    events = []
    invocation_id = None
    
    try:
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=new_msg
        ):
            events.append(event)
            if event.invocation_id:
                invocation_id = event.invocation_id
    except Exception as e:
        logger.exception(f"Exception encountered during workflow execution for session_id '{session_id}'")
        raise HTTPException(status_code=500, detail=f"Workflow error: {str(e)}")

    # Check if the workflow was interrupted for human approval
    is_interrupted = any(e.long_running_tool_ids for e in events)
    if is_interrupted:
        logger.info(f"Workflow suspended for human approval. session_id: '{session_id}', invocation_id: '{invocation_id}'")
        # Extract the human approval message to display to the user
        hitl_message = ""
        for e in events:
            if e.actions:
                for act in e.actions:
                    if hasattr(act, "type") and act.type == "request_input" and hasattr(act, "content"):
                        hitl_message = act.content
                    elif hasattr(e, "content") and e.content and hasattr(e.content, "parts"):
                        # Extract message from first request_input event structure
                        for part in e.content.parts:
                            if part.function_call and part.function_call.name == "adk_request_input":
                                hitl_message = part.function_call.args.get("message", "")
        
        return {
            "status": "suspended",
            "session_id": session_id,
            "invocation_id": invocation_id,
            "action_required": "Human Review Needed",
            "message": hitl_message
        }
    else:
        logger.info(f"Workflow completed successfully. session_id: '{session_id}'")
        # Retrieve final state
        session = await runner.session_service.get_session(
            app_name=runner.app_name,
            user_id=user_id,
            session_id=session_id
        )
        return {
            "status": "completed",
            "session_id": session_id,
            "state": session.state if session else None
        }

@app.post("/resume")
@app.post("/apps/expense_agent/resume")
async def resume_workflow(request: Request):
    """POST endpoint to resume a suspended workflow with a human decision."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
        
    session_id = body.get("session_id")
    invocation_id = body.get("invocation_id")
    decision = body.get("decision")
    
    if not session_id or not invocation_id or not decision:
        raise HTTPException(
            status_code=400, 
            detail="Missing required fields. Payload must include: session_id, invocation_id, decision"
        )
        
    logger.info(f"Resuming workflow session_id: '{session_id}', invocation_id: '{invocation_id}' with decision: '{decision}'")
    
    # Construct standard FunctionResponse part to resume human approval
    resume_msg = types.Content(
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id="human_approval",
                    name="adk_request_input",
                    response={"response": decision}
                )
            )
        ]
    )
    
    user_id = "pubsub_trigger"
    events = []
    try:
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            invocation_id=invocation_id,
            new_message=resume_msg
        ):
            events.append(event)
    except Exception as e:
        logger.exception(f"Exception encountered while resuming workflow session_id '{session_id}'")
        raise HTTPException(status_code=500, detail=f"Workflow resume error: {str(e)}")
        
    session = await runner.session_service.get_session(
        app_name=runner.app_name,
        user_id=user_id,
        session_id=session_id
    )
    return {
        "status": "completed",
        "session_id": session_id,
        "state": session.state if session else None
    }

@app.get("/session/{session_id}")
@app.get("/apps/expense_agent/session/{session_id}")
async def get_session_state(session_id: str):
    """GET endpoint to fetch the current state of any active or completed session."""
    user_id = "pubsub_trigger"
    session = await runner.session_service.get_session(
        app_name=runner.app_name,
        user_id=user_id,
        session_id=session_id
    )
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    return {"session_id": session_id, "state": session.state}

def json_dumps_payload_str(payload: Any) -> str:
    import json
    return json.dumps(payload)
