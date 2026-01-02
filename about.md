🧠 Nexus — Local macOS AI Assistant

Nexus is a local-first, safety-focused AI assistant for macOS, built in Python.
It is not a Siri clone and not an always-listening agent.

Nexus is a command router + planner + execution system that:

understands natural language
plans actions using AI
asks for confirmation
executes actions safely through deterministic code
records everything in a database for auditing and memory

This project is designed to be portfolio-grade, auditable, and extensible.

✨ Core Principles

Nexus is built on a few strict rules:

AI never executes system commands
AI plans, code executes
All destructive actions require confirmation
macOS security is never bypassed
Everything is logged
Failures are reported, not hidden

This makes Nexus safe to run on a real machine.

🏗️ High-Level Architecture

User Input
   ↓
AI Planner (Intent + Steps)
   ↓
Safety Gate
   ↓
Plan Confirmation (Yes / No)
   ↓
Router
   ↓
Skills (Deterministic Execution)
   ↓
Postgres Memory Log

Two Lanes of Operation

Lane A — Chat

Used for:
greetings
questions
explanations
conversation

Handled entirely by the chat skill.

Lane B — Action

Used for:
opening apps
closing apps
batch operations (open many / close many)
close all apps safely

Actions are planned by AI but executed by code.

🧩 Key Components

core/intent.py

Defines:
Mode → CHAT or ACTION
Intent → OPEN_APP, CLOSE_APP, CLOSE_ALL_APPS, etc.
This file contains no execution logic.

core/planner.py

Uses OpenAI to:
classify user input
generate a human-readable plan
output structured steps

Example AI output:

{
  "mode": "ACTION",
  "plan": "I will close Chrome and open Safari.",
  "steps": [
    { "intent": "CLOSE_APP", "args": { "app_name": "Google Chrome" } },
    { "intent": "OPEN_APP",  "args": { "app_name": "Safari" } }
  ]
}

core/safety.py

Central safety gate.

Responsibilities:
block unknown intents
validate arguments
mark risky actions
enforce business rules

If safety fails → execution stops.

core/router.py

Routes actions to skills.
No guessing
No fallback
No dynamic execution
Each intent must be explicitly registered.

skills/system.py

Deterministic OS execution.

Currently supports:
OPEN_APP → open -a "App Name"
CLOSE_APP → AppleScript quit (graceful, safe)
No shell injection.
No force kills.

utils/macos.py

macOS-specific helpers.

Used for:
listing running applications
excluding protected apps (Finder, Terminal, Nexus itself)

AI never sees this data.

core/memory.py

Persistent memory layer backed by PostgreSQL.

Stores:
raw user input
planned steps
execution results
timestamps

This enables:
debugging
auditing
“What did you do earlier?” queries
future analytics

🔐 Safety Features

One confirmation per plan
Graceful app quitting (no kill -9)
Nexus never closes itself
Finder and Terminal are excluded by default
Partial failures are reported clearly

Example result:
✅ Closed Google Chrome
❌ Failed to close Xcode (unsaved changes)
✅ Opened Safari

🗄️ Memory & Database

Nexus uses PostgreSQL for memory storage.
Runs in Docker
Uses JSONB for steps and results
Designed for future querying and dashboards

Environment variable:
DATABASE_URL=postgresql://user:password@localhost:5432/nexus

▶️ Running Nexus

1. Install dependencies
pip install -r requirements.txt

2. Set up environment
cp .env.example .env
Add your OpenAI key and Postgres connection string.

3. Start Nexus
python3 nexus.py

🧪 Example Commands

hello
open safari
close chrome and open firefox
open chrome safari mail
close all apps

Nexus will:
show a plan
ask for confirmation
execute step-by-step
report results
log everything

🚧 What This Is NOT

❌ Not autonomous
❌ Not always-listening
❌ Not self-executing AI
❌ Not a security bypass
❌ Not a background daemon (yet)

🚀 Future Extensions

Calendar & reminders
File management
Semantic search over memory
Activity summaries (“What did you do today?”)
Voice interface (optional)
Plugin system for new skills

📌 Why This Project Matters

This project demonstrates:
real system design
AI used responsibly
OS-level automation knowledge
safety-first engineering
clean separation of concerns

This is not a toy project.
It is a foundation for a real assistant.