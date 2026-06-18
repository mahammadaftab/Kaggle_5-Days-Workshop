@echo off
if "%1"=="playground" (
    echo Starting FastAPI web service on port 8080...
    "..\First Agent\.venv\Scripts\uvicorn" expense_agent.service:app --host 127.0.0.1 --port 8080 --reload
) else if "%1"=="generate-traces" (
    echo Running trace generator...
    "..\First Agent\.venv\Scripts\python" tests/eval/generate_traces.py
) else if "%1"=="grade" (
    echo Grading traces...
    set "PATH=C:\Users\mdaft\OneDrive\Desktop\Kaggle\First Agent\.venv\Scripts;%PATH%"
    "..\First Agent\.venv\Scripts\agents-cli" eval grade --traces artifacts/traces/generated_traces.json --config tests/eval/eval_config.yaml --output artifacts/grade_results
) else (
    echo Unknown target. Usage: make [playground ^| generate-traces ^| grade]
)

