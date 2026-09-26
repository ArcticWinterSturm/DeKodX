"""DeKodX - forensic recovery utility for Codex Desktop conversation data.

Reads a local ``.codex`` installation directory, parses every storage format
(JSONL transcripts, SQLite state/log databases, TOML config, JSON state),
reconstructs the full conversation history, redacts PII and secrets, and
exports a single navigable Markdown recovery report.

Runs entirely locally. No network access, ever.

v2: ChatGPT-integrated schema support, subagent linkage, version detection.
"""

__version__ = "2.1.0"
APP_NAME = "DeKodX"
APP_TAGLINE = "Codex Desktop Session Recovery v2"
