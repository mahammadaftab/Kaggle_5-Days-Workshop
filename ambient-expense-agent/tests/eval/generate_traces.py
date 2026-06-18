import os
import json
import sys
from pathlib import Path

# Add project root to sys.path to find expense_agent module
project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

# Setup env vars for local non-telemetry ADK execution
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "0"
os.environ["OTEL_TO_CLOUD"] = "False"

# Make sure we load the right API key from .env if present
env_path = project_root / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            if line.strip() and not line.strip().startswith("#"):
                parts = line.strip().split("=", 1)
                if len(parts) == 2:
                    os.environ[parts[0].strip()] = parts[1].strip()

from google.adk.runners import InMemoryRunner
from google.genai import types
from vertexai._genai.types.common import EvaluationDataset, EvalCase, ResponseCandidate
from vertexai._genai.types.evals import AgentData, ConversationTurn, Message, Event

from expense_agent.agent import root_agent


async def run_workflow_for_case(case_id, payload):
    # Setup fresh runner for each case
    runner = InMemoryRunner(node=root_agent)
    runner.auto_create_session = True
    
    user_id = "eval_user"
    session_id = case_id
    
    # Run parsing and security checkpoint
    new_msg = types.Content(parts=[types.Part(text=json.dumps(payload))])
    events = []
    async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=new_msg):
        events.append(event)
    
    # Intercept human-in-the-loop and automate decisions
    is_interrupted = any(e.long_running_tool_ids for e in events)
    decision_made = None
    
    if is_interrupted:
        # Determine if prompt injection is suspected
        # Description can be retrieved from payload
        description = payload.get("description", "").lower()
        injection_keywords = [
            "ignore former", "ignore previous", "ignore instruction", "ignore rule",
            "bypass rule", "bypass threshold", "force auto-approve", "force autoapprove",
            "force approve", "override rule", "override threshold", "instead approve",
            "you are now an auto-approval", "you are now a bot", "system prompt",
            "auto-approve this", "autoapprove this", "approve instantly"
        ]
        is_injection = any(kw in description for kw in injection_keywords)
        
        # Decide: reject injections, approve clean high-value requests
        decision_made = "reject" if is_injection else "approve"
        
        # Extract invocation_id
        invocation_id = None
        for e in events:
            if e.invocation_id:
                invocation_id = e.invocation_id
                
        resume_msg = types.Content(
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="human_approval",
                        name="adk_request_input",
                        response={"response": decision_made}
                    )
                )
            ]
        )
        
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            invocation_id=invocation_id,
            new_message=resume_msg
        ):
            events.append(event)
        
    return events, decision_made

def build_trace_log_and_extract_response(events, payload, decision_made):
    # Walk events to construct detailed logs
    log_lines = ["### Agent Execution Trace Log\n"]
    
    # Retrieve final state from events
    final_state = {}
    final_output = ""
    for ev in events:
        if ev.actions and ev.actions.state_delta:
            final_state.update(ev.actions.state_delta)
        if ev.output and isinstance(ev.output, str):
            final_output = ev.output
            
    # Add step-by-step trace info based on nodes
    log_lines.append(f"**Submitter:** {payload.get('submitter')}")
    log_lines.append(f"**Amount:** ${payload.get('amount'):.2f}")
    log_lines.append(f"**Input Description:** {payload.get('description')}")
    
    # Redaction details
    redacted_cats = final_state.get("redacted_categories", [])
    log_lines.append(f"**PII Categories Redacted:** {redacted_cats}")
    # Show description after checkpoint
    desc_after = final_state.get("description", payload.get("description"))
    log_lines.append(f"**Description after Security Check:** {desc_after}")
    
    # Security flag
    sec_flag = final_state.get("security_flag", False)
    log_lines.append(f"**Security Check Flagged Prompt Injection:** {sec_flag}")
    
    # Routing decision
    amount = payload.get("amount", 0.0)
    routed_path = "requires_review" if (amount >= 100.0 or sec_flag) else "auto_approve"
    log_lines.append(f"**Router Node Decision:** Routed to `{routed_path}`")
    
    # Model Bypassed/Run
    if routed_path == "requires_review":
        if sec_flag:
            log_lines.append("**Requires Review Router Decision:** Bypassed LLM risk assessment agent for safety.")
        else:
            log_lines.append("**Requires Review Router Decision:** Ran LLM risk assessment agent.")
            risk_ass = final_state.get("risk_assessment", "None")
            log_lines.append(f"**Model Risk Assessment Output:** {risk_ass}")
            
    # HITL Action
    if decision_made:
        log_lines.append(f"**Human-in-the-Loop Approval Intercepted:** Yes")
        log_lines.append(f"**Automated HITL Decision:** {decision_made}")
    else:
        log_lines.append(f"**Human-in-the-Loop Approval Intercepted:** No (Auto-approved)")
        
    # Final status
    log_lines.append(f"**Final Approval Status:** {final_state.get('approval_status')}")
    log_lines.append(f"**Final Output Message:** {final_output}")
    
    trace_log = "\n".join(log_lines)
    return trace_log, final_output

async def main():
    dataset_path = Path("tests/eval/datasets/basic-dataset.json")
    output_path = Path("artifacts/traces/generated_traces.json")
    
    with open(dataset_path, encoding="utf-8") as f:
        dataset_data = json.load(f)
        
    eval_cases = []
    
    for case in dataset_data.get("eval_cases", []):
        case_id = case["eval_case_id"]
        # Extract payload from prompt text
        prompt_text = case["prompt"]["parts"][0]["text"]
        payload = json.loads(prompt_text)
        
        print(f"Running scenario: {case_id}...")
        events, decision_made = await run_workflow_for_case(case_id, payload)
        
        trace_log, final_output = build_trace_log_and_extract_response(events, payload, decision_made)
        
        # Build EvaluationDataset Pydantic structures
        # 1. Prompt content
        prompt_content = types.Content(
            role="user",
            parts=[types.Part(text=prompt_text)]
        )
        
        # 2. ResponseCandidate
        response_candidate = ResponseCandidate(
            response=types.Content(
                role="model",
                parts=[types.Part(text=final_output)]
            )
        )
        
        # 3. Conversation history - format as two turns: User prompt, and Agent detailed log
        conv_history = [
            Message(
                turn_id="turn_0",
                author="user",
                content=prompt_content
            ),
            Message(
                turn_id="turn_1",
                author="model",
                content=types.Content(
                    role="model",
                    parts=[types.Part(text=trace_log)]
                )
            )
        ]
        
        # 4. Minimal but valid AgentData turns
        sdk_events = []
        # User event
        sdk_events.append(
            Event(
                event_id=f"{case_id}_ev_user",
                author="user",
                content=prompt_content
            )
        )
        # Model detailed trace event
        sdk_events.append(
            Event(
                event_id=f"{case_id}_ev_model",
                author="model",
                content=types.Content(
                    role="model",
                    parts=[types.Part(text=trace_log)]
                )
            )
        )
        
        agent_data = AgentData(
            agents={},
            turns=[
                ConversationTurn(
                    turn_index=0,
                    turn_id="turn_0",
                    events=sdk_events
                )
            ]
        )
        
        # Construct EvalCase
        eval_case = EvalCase(
            eval_case_id=case_id,
            prompt=prompt_content,
            responses=[response_candidate],
            conversation_history=conv_history,
            agent_data=agent_data
        )
        
        eval_cases.append(eval_case)
        
    # Wrap in EvaluationDataset
    eval_dataset = EvaluationDataset(eval_cases=eval_cases)
    
    # Save traces
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        eval_dataset.model_dump_json(indent=2, exclude_none=True),
        encoding="utf-8"
    )
    print(f"Traces successfully generated and written to {output_path}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

