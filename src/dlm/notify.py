"""Discord webhook delivery (stdlib only).

Batches embeds (Discord allows <=10 per message), retries 5xx with backoff, and
honors 429 rate-limit `retry_after`. A successful webhook returns 204.
"""
from __future__ import annotations

import json
import time
from urllib import error, request


class DiscordError(RuntimeError):
    pass


def _post(webhook_url: str, payload: dict, *, retries: int = 4) -> None:
    data = json.dumps(payload).encode("utf-8")
    for attempt in range(retries):
        req = request.Request(
            webhook_url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with request.urlopen(req, timeout=30):
                return
        except error.HTTPError as exc:
            if exc.code == 429:
                try:
                    wait = float(json.loads(exc.read()).get("retry_after", 1.0))
                except (ValueError, json.JSONDecodeError):
                    wait = 2.0
                time.sleep(min(wait, 30))
                continue
            if exc.code >= 500:
                time.sleep(2.0 * (2**attempt))
                continue
            body = exc.read()[:300].decode("utf-8", "replace")
            raise DiscordError(f"Discord {exc.code} {exc.reason}: {body}") from exc
        except (error.URLError, TimeoutError) as exc:
            if attempt == retries - 1:
                raise DiscordError(f"Discord request failed: {exc}") from exc
            time.sleep(2.0 * (2**attempt))
    raise DiscordError("Discord webhook failed after retries")


def send_embeds(
    webhook_url: str,
    embeds: list[dict],
    *,
    content: str | None = None,
    username: str | None = None,
) -> None:
    if not embeds and not content:
        return
    batches = [embeds[i : i + 10] for i in range(0, len(embeds), 10)] or [[]]
    for index, batch in enumerate(batches):
        payload: dict = {"embeds": batch}
        if username:
            payload["username"] = username
        if content and index == 0:
            payload["content"] = content[:2000]
        _post(webhook_url, payload)
        if len(batches) > 1:
            time.sleep(0.6)
