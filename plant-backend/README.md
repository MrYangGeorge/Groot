# Talking Plant — Backend (Data Layer)

Python data layer for the Talking Plant hackathon project. Reads raw sensor
data from Viam Cloud (captured by a Pi-side custom sensor component built
separately), computes derived state (bucket transitions, watering events),
and stores it in a local SQLite database that downstream layers (LLM,
text-to-SQL, voice) can query.

This directory intentionally does **not** contain LLM code, voice pipeline,
or any HTTP surface — only the data plumbing.

## Files

| File | Purpose |
| --- | --- |
| `config.py` | Loads `.env` and exports constants. |
| `buckets.py` | Pure bucketing functions + `DIMENSIONS` map. |
| `db.py` | SQLite schema init + tiny helper API. |
| `processor.py` | Pure function: readings -> transitions + watering events. |
| `viam_client.py` | Async Viam Cloud read client. |
| `poller.py` | APScheduler loop: Viam -> processor -> SQLite. |
| `mock_data.py` | Standalone synthetic generator (no Viam needed). |
| `persona.py` | The plant's voice: single source of truth for the system prompt. |
| `llm_client.py` | Thin Anthropic SDK wrapper with JSON parsing + one-shot retry. |
| `context_builder.py` | Pure DB -> prompt-string helpers (current state, history, schema). |
| `text_to_sql.py` | Natural language -> validated read-only SQL over the DB. |
| `responder.py` | Public API: `answer_user_query` and `autonomous_message`. |
| `triggers.py` | Policy for when the poller should fire an autonomous message. |
| `cli.py` | Manual testing CLI for the LLM layer. |
| `elevenlabs_client.py` | STT (Scribe v1) + TTS (Flash v2.5) wrappers. |
| `audio_io.py` | Mic capture + speaker playback helpers. |
| `voice.py` | Spacebar push-to-talk entry point. |

## Setup

```bash
cd plant-backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

For local-only work you can leave the Viam keys as placeholders — the mock
generator doesn't read them.

## Local dev (no Viam needed)

Generate 6 simulated hours of data at 1000× speed and write derived state
straight to SQLite:

```bash
python mock_data.py --reset --duration-hours 6 --speed 1000
```

Then inspect:

```bash
sqlite3 plant.db "SELECT * FROM state_transitions ORDER BY id;"
```

Other useful mock-data invocations:

```bash
# Faster feedback loop (finishes in seconds).
python mock_data.py --reset --duration-hours 2 --speed 100000

# Pipe raw readings to stdout without touching the DB.
python mock_data.py --mode print --duration-hours 1 | head
```

## Live mode

Fill `.env` with real Viam credentials (`VIAM_API_KEY`, `VIAM_API_KEY_ID`,
`VIAM_ORG_ID`, and the `VIAM_COMPONENT_NAME` your teammate registered on the
Pi), then:

```bash
python poller.py
```

The poller does an initial fetch immediately and then every
`POLL_INTERVAL_SECONDS` (default 30s). It tracks progress in the
`poller_state` table, so restarting won't re-process old rows.

Each tick logs something like:

```
Processed 12 readings, 3 transitions, 1 waterings
```

Any individual poll failure is logged but will not stop the scheduler.

## Inspecting the DB

```bash
# List tables.
sqlite3 plant.db ".tables"

# Recent state transitions across all dimensions.
sqlite3 plant.db "SELECT timestamp, dimension, from_state, to_state, trigger_value
                   FROM state_transitions ORDER BY id DESC LIMIT 20;"

# Recent watering events.
sqlite3 plant.db "SELECT timestamp, moisture_before, moisture_after
                   FROM watering_events ORDER BY id DESC LIMIT 20;"

# Current (most recent) state per dimension.
sqlite3 plant.db "SELECT dimension, to_state, trigger_value, timestamp FROM (
                     SELECT dimension, to_state, trigger_value, timestamp,
                            ROW_NUMBER() OVER (PARTITION BY dimension ORDER BY id DESC) AS rn
                       FROM state_transitions
                   ) WHERE rn = 1;"

# Poller checkpoint.
sqlite3 plant.db "SELECT * FROM poller_state;"
```

## Talking to the plant (LLM layer)

Fill in `ANTHROPIC_API_KEY` in `.env` (plus optionally `CLAUDE_MODEL`,
`PLANT_NAME`, `PLANT_SPECIES`). Then:

```bash
# Conversational — runs text-to-SQL + persona under the hood.
python cli.py "how are you feeling?"
python cli.py "when did I last water you?"
python cli.py "what's the meaning of life?"     # in-character "not my department"

# Fire an autonomous message manually (bypasses the trigger logic).
python cli.py --autonomous '{"type":"watering","moisture_before":24.1,"moisture_after":78.3,"timestamp":"2026-04-18T15:22:00Z"}'

# Inspect the plant's last few utterances and the current sensor state.
python cli.py --recent 10
python cli.py --state

# Verbose mode shows per-call latency, tokens, and generated SQL.
python cli.py -v "am I a good plant parent?"
```

All plant utterances — both answers to questions and autonomous reactions —
are logged to the `plant_messages` table. The poller calls the trigger
policy in `triggers.py` after every tick, so leaving `python poller.py`
running against live (or mock) data will produce occasional in-character
outbursts.

## Voice mode (spacebar push-to-talk)

The fun one. `voice.py` wraps the LLM layer with mic input and speaker
output so you can just hold space and talk.

### Prerequisites

- Working microphone and speaker.
- An ElevenLabs account. Pick a voice from
  [voice-lab](https://elevenlabs.io/app/voice-lab) (a theatrical British
  male voice suits this persona well) and put its ID in
  `ELEVENLABS_VOICE_ID` in `.env`. Also set `ELEVENLABS_API_KEY`.
- `ANTHROPIC_API_KEY` already set (the voice layer calls the LLM layer).
- **macOS only:** grant your terminal app three permissions in
  System Settings → Privacy & Security:
  - **Microphone** (for `sounddevice`)
  - **Input Monitoring** and **Accessibility** (both for `pynput` — the
    spacebar listener needs them)
  First launch will silently do nothing if these aren't granted; quit the
  process, grant the permission, and re-run.
- **Linux:** run inside a graphical session (not bare SSH) so `pynput` can
  attach to the keyboard, and install PortAudio dev headers before
  `pip install -r requirements.txt`:
  `sudo apt install portaudio19-dev`.

### Run

```bash
python voice.py
```

Hold `SPACE`, speak, release. The plant transcribes your speech, thinks
about it, and answers aloud. Press `Q` to quit.

Each response is cached to `audio_cache/response_<id>.mp3` — if a judge
asks "can it say that again?", you have every line on disk.

### Troubleshooting

- **"no mic input"** — check System Settings → Privacy → Microphone.
- **"spacebar not detected" on macOS** — check Accessibility + Input
  Monitoring, and fully quit/relaunch the terminal after granting.
- **"voice sounds robotic"** — you're probably on a cheap default voice.
  Pick a different one in the ElevenLabs voice library and paste the new
  ID into `.env`.
- **MP3 playback fails** — on older libsndfile installs `soundfile` can't
  decode MP3. `audio_io.play_audio` falls back to `afplay` on macOS and
  `ffplay` elsewhere. If neither is on `PATH`, install `ffmpeg`
  (`brew install ffmpeg` / `apt install ffmpeg`).
- **"too short -- hold spacebar longer"** — minimum capture is 0.3s to
  avoid sending empty audio to the API.

## Demo script

For the judges' table, in order:

1. **Seed** the DB with realistic mock data right before the demo:
   ```bash
   python mock_data.py --reset --duration-hours 24 --speed 100000
   ```
   (optional) nudge a watering into recent history:
   ```bash
   sqlite3 plant.db "INSERT INTO watering_events (timestamp, moisture_before, moisture_after) VALUES (datetime('now','-2 hours'), 22.0, 76.0);"
   ```
2. **Start** `python voice.py` in a terminal the judges can see.
3. **Land these five questions in order** — don't improvise, these are
   tested to show off different parts of the stack:
   1. "How are you feeling?" — current state summary
   2. "When did I last water you?" — text-to-SQL over `watering_events`
   3. "Have I been a good plant parent?" — history + persona
   4. "What's the weather outside?" — graceful "I don't know" in character
   5. "Am I going to be okay?" — emotional flourish; the persona shines
4. **Stop after five.** 90 seconds of polished demo beats 3 minutes of
   rambling every time.

## Notes / gotchas

- **Viam APP-10891 workaround**: every SQL query to Viam's tabular data API
  includes a literal
  `time_received >= CAST('2000-01-01T00:00:00.000Z' AS TIMESTAMP)` clause in
  addition to the real lower bound. Do not remove it.
- **Field access is defensive**: the Pi-side sensor may nest readings as
  either `data.readings.<field>` or `data.<field>`. Both shapes are handled.
- **Purity**: `processor.py` is a pure function so both the poller and the
  mock generator share the exact same derivation logic.
- **SQLite WAL mode** is enabled so downstream readers (LLM, text-to-SQL,
  app) can query the DB concurrently with the poller writing to it.
