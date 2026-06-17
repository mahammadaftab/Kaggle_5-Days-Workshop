# Kaggle Shipping Customer Support Agent 🚚✨

This repository contains a shipping company customer support agent built using **Google Agent Development Kit (ADK) 2.0**. 

The project uses a **Graph Workflow** to classify user queries and route them to specialist nodes:
1. **Classify:** Analyzes the query to see if it is related to shipping (rates, tracking, delivery, returns).
2. **Route:** 
   * If related to shipping, it routes to `shipping_faq_agent` (configured to be playful, enthusiastic, and quote rates/free shipping thresholds).
   * If unrelated, it routes to `decline_agent` which politely declines to answer non-shipping questions.

---

## 🛠️ Prerequisites

* **Python 3.10+**
* A **Google AI API Key** (Gemini API Key)

---

## 🚀 Getting Started

### 1. Set Up the Virtual Environment

Initialize a virtual environment and install the Agent Development Kit:

```bash
# Create virtual environment
python -m venv .venv

# Activate virtual environment (Windows PowerShell)
.venv\Scripts\Activate.ps1

# Activate virtual environment (macOS/Linux)
source .venv/bin/activate

# Install google-adk
pip install google-adk
```

### 2. Configure Environment Variables

Create or update the `.env` file inside the `customer_support_agent` folder:

```env
GOOGLE_GENAI_USE_VERTEXAI=0
GOOGLE_API_KEY=your_actual_gemini_api_key_here
```

---

## 💻 Running the Agent Manually

Make sure your virtual environment is active before running the commands.

### Option 1: Run via CLI (Interactive Mode)

Run the agent in an interactive chat session in your terminal:

```bash
adk run customer_support_agent
```

### Option 2: Run via CLI (Single Query)

Send a single query directly from the command line:

```bash
adk run customer_support_agent "How long does standard delivery take?"
```

*(Note: On Windows, if you encounter emoji-encoding issues in your terminal, run `$env:PYTHONUTF8=1` in PowerShell first to enable UTF-8 mode).*

### Option 3: Run the Web Playground

Launch the interactive local web development server and playground:

```bash
adk web customer_support_agent
```

Once started, open your browser and navigate to:
👉 **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

---

## 📂 Project Structure

* **`customer_support_agent/`**
  * `agent.py`: Graph workflow, agents, and custom routing node definitions.
  * `__init__.py`: Package entrypoint exposing `root_agent`.
  * `.env`: Local environment configuration (contains model backends and API keys).
* **`.gitignore`**: Excludes virtual environments, environment secrets, and caches.
* **`README.md`**: Guide to running and managing the agent project.