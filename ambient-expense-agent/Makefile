.PHONY: playground generate-traces grade

playground:
	"../First Agent/.venv/Scripts/uvicorn" expense_agent.service:app --host 127.0.0.1 --port 8080 --reload

generate-traces:
	"../First Agent/.venv/Scripts/python" tests/eval/generate_traces.py

grade:
	@powershell -Command "$$env:PATH = 'C:\Users\mdaft\OneDrive\Desktop\Kaggle\First Agent\.venv\Scripts;' + $$env:PATH; & '../First Agent/.venv/Scripts/agents-cli' eval grade --traces artifacts/traces/generated_traces.json --config tests/eval/eval_config.yaml --output artifacts/grade_results"

