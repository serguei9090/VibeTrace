# Antigravity Code Reviewer Plugin

Automated pre-finish code review hook for Antigravity, powered by Google AI Studio's **Antigravity Agent** (`antigravity-preview-09-2026`) defaulting to **Gemini 3.8 Flash** (`gemini-3.8-flash`).

## How It Works

1. **Trigger:** Listens to the `Stop` event before the turn finishes (`terminationReason: "NO_TOOL_CALL"`).
2. **Analysis:** Captures uncommitted changes (`git diff HEAD` plus newly created untracked files).
3. **Review:** Invokes the Google AI Studio Interactions API with `agent: "antigravity-preview-09-2026"` and `environment: "remote"`. By omitting `agent_config`, it defaults directly to **Gemini 3.8 Flash**.
4. **Action:**
   - If clean: returns `{"decision": "allow"}`.
   - If issues found: returns `{"decision": "continue", "reason": "..."}`, keeping the agent in the loop to fix the findings.
   - If quota exceeded (HTTP 429), timeout, or offline: **fails open** (`{"decision": "allow"}`) so your work is never blocked.
   - Automatically prevents infinite loops via diff hashing and max retry counters.

## Setup & Configuration

### 1. API Key
Set your Gemini API key from [Google AI Studio](https://aistudio.google.com/):
- Via environment variable: `GEMINI_API_KEY=your_key` or `GOOGLE_API_KEY=your_key`
- Or via a `.env` file in the workspace root or inside this plugin folder:
  ```env
  GEMINI_API_KEY=AIzaSy...
  ```

### 2. Optional Agent Override
By default, it uses `antigravity-preview-09-2026` (Gemini 3.8 Flash). You can set an environment variable to override:
```env
ANTIGRAVITY_AGENT=antigravity-preview-09-2026
```

## Enable / Disable

- **Disable via hooks.json:** Set `"enabled": false` in `hooks.json`.
- **Disable via plugin.json:** Set `"disabled": true` in `plugin.json`.
- **Disable via CLI:**
  ```powershell
  agy plugin disable code-reviewer
  ```
- **Enable via CLI:**
  ```powershell
  agy plugin enable code-reviewer
  ```
