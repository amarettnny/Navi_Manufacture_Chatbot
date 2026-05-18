# Navi_Manufacture_Chatbot

## 1 Set up your API keys
cp .env.example .env
   edit .env and paste in at least one of the following LLM API key:
- ANTHROPIC_API_KEY (from https://console.anthropic.com/settings/keys)
- OPENROUTER_API_KEY (from https://openrouter.ai/keys)

## 2 Backend
run following commands in terminal (environment interpretor set to backend/.venv/bin/python):
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python load_data.py ../data/seed_data_parameters.json
uvicorn server:app --reload --port 8000
```

## 3 Frontend (separate terminal)
```bash
cd frontend
python -m http.server 5173
```
**The chatbot frontend is deployed at http://localhost:5173**
