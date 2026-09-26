"""PII / secret redaction engine.

Three levels:
  * standard - the specification's redaction matrix (default)
  * paranoid - standard + absolute paths, URLs, hostnames and tighter ID trimming
  * none     - keeps everything EXCEPT hard secrets (auth tokens, API keys,
               installation ids, SIDs, account ids) which are never emitted.
"""

from __future__ import annotations

import re
from typing import Iterable

REDACTED = "[REDACTED]"
ENCRYPTED_REASONING = "[ENCRYPTED REASONING - UNRECOVERABLE]"

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SID = re.compile(r"\bS-1-\d+(?:-\d+)+\b")
_PROCESS_UUID = re.compile(r"\bpid:\d+:[0-9a-fA-F-]{6,}\b")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_API_KEY = re.compile(r"\b(?:sk|pk|rk)-(?:proj-)?[A-Za-z0-9_-]{10,}\b")
_WIN_USER = re.compile(r"([A-Za-z]:\\+(?:Users|Documents and Settings)\\+)[^\\/\s:\"'<>|]+")
_POSIX_USER = re.compile(r"(/(?:home|Users)/)[^/\\\s:\"'<>|]+")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_URL_CREDS = re.compile(r"(https?://)[^/@\s:\"']+@")
_URL = re.compile(r"\bhttps?://[^\s\"'<>|)]+")
_WIN_PATH = re.compile(r"[A-Za-z]:\\[^\s\"'<>|]*")
_POSIX_PATH = re.compile(r"(?<![\w.])/(?:[\w.-]+/)+[\w.-]*")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


class Redactor:
    def __init__(
        self,
        level: str = "standard",
        home_dirs: Iterable[str] = (),
        secrets: Iterable[str] = (),
    ):
        self.level = (level or "standard").lower()
        if self.level not in ("standard", "paranoid", "none"):
            self.level = "standard"
        self.homes = [str(h).rstrip("/\\") for h in home_dirs if h]
        banned = {"users", "user", "home", "desktop", "documents", "admin", "administrator", "owner", "profile", "name"}
        self.usernames = sorted(
            {
                re.escape(p) for h in self.homes
                for p in ([h.rsplit("/", 1)[-1], h.rsplit("\\", 1)[-1]])
                if len(p) >= 3 and p.lower() not in banned
            },
            key=len, reverse=True,
        )
        self.secrets = sorted({s.strip() for s in secrets if s and len(s.strip()) >= 6},
                              key=len, reverse=True)

    # -- identifiers (partial redaction) ------------------------------------ #
    def session_id(self, sid: str) -> str:
        sid = sid or ""
        if self.level == "none":
            return sid
        if self.level == "paranoid":
            return sid[:4] + "-…" if sid else sid
        return sid[:8] + "-…" if sid else sid

    def full_session_id(self, sid: str) -> str:
        """Used only in the per-session detail header at standard level."""
        if self.level == "paranoid":
            return self.session_id(sid)
        return sid or ""

    def call_id(self, cid: str) -> str:
        cid = cid or ""
        if self.level == "none":
            return cid
        if self.level == "paranoid":
            return cid[:6] + "…" if cid else cid
        return cid[:12] + "…" if cid else cid

    # -- free text ----------------------------------------------------------- #
    def apply(self, text) -> str:
        if text is None:
            return ""
        text = str(text)
        if not text:
            return text
        # 0. binary/control bytes -> hex escapes (logs may carry raw binary)
        text = _CONTROL.sub(lambda m: f"\\x{ord(m.group(0)):02x}", text)

        # 1. hard secrets: never emitted at any level
        for secret in self.secrets:
            if secret in text:
                text = text.replace(secret, REDACTED)
        text = _JWT.sub(REDACTED, text)
        text = _API_KEY.sub(REDACTED, text)

        if self.level == "none":
            return text

        # 2. standard matrix
        keep = 4 if self.level == "paranoid" else 8
        text = _UUID_RE.sub(lambda m: m.group(0)[:keep] + "-…", text)
        text = _EMAIL.sub(REDACTED, text)
        text = _SID.sub(REDACTED, text)
        text = _PROCESS_UUID.sub(REDACTED, text)
        text = _URL_CREDS.sub(r"\1" + REDACTED + "@", text)
        text = _WIN_USER.sub(r"\1[USER]", text)
        text = _POSIX_USER.sub(r"\1[USER]", text)
        for name in self.usernames:
            text = re.sub(rf"\b{name}\b", "[USER]", text, flags=re.IGNORECASE)
        for home in self.homes:
            if home in text:
                if re.match(r"[A-Za-z]:\\", home):
                    repl = home.rsplit("\\", 1)[0] + "\\[USER]"
                else:
                    repl = home.rsplit("/", 1)[0] + "/[USER]"
                text = text.replace(home, repl)

        if self.level == "paranoid":
            text = _URL.sub("[URL]", text)
            text = _WIN_PATH.sub("[PATH]", text)
            text = _POSIX_PATH.sub("[PATH]", text)
            text = _IPV4.sub("[IP]", text)
        return text

    def path(self, value: str) -> str:
        return self.apply(value)
