"""OPTIONAL rephrasing of the template insight by a free-tier LLM (Gemini) - off unless GEMINI_API_KEY is set.
Safety: the reply is accepted only if it contains exactly the same set of numbers as the template text;
otherwise the deterministic text is used. All financial figures are computed in Python, never by the model."""
import json
import os
import re
import urllib.request


def _nums(s: str) -> list[str]:
    return sorted(re.findall(r"\d+(?:\.\d+)?", s))


def polish(text: str) -> str:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return text
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{os.getenv('GEMINI_MODEL', 'gemini-2.0-flash')}:generateContent?key={key}"
        prompt = ("Rewrite this fulfilment-centre P&L note for a busy operations manager in 3 short sentences. "
                  "Keep EVERY number and line-item name exactly as given; do not add facts.\n\n" + text)
        req = urllib.request.Request(url, data=json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            out = json.loads(resp.read())["candidates"][0]["content"]["parts"][0]["text"].strip()
        return out if _nums(out) == _nums(text) else text
    except Exception:
        return text
