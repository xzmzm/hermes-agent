"""Quota lookup helpers for the /quota slash command.

This helper intentionally talks to the local aichatproxy instance instead of
provider dashboards directly.  aichatproxy owns the model -> upstream route
mapping and knows which provider-specific quota endpoint to call.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx


DEFAULT_AICHATPROXY_QUOTA_URL = "http://localhost:8000/api/quota"


def _provider_label(provider: str | None) -> str:
    p = (provider or "").strip()
    return p or "auto"


def _human_duration(seconds: Any) -> str:
    if seconds in (None, ""):
        return ""
    try:
        total = int(float(seconds))
    except Exception:
        return str(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def _human_timestamp(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        ts = float(value)
        if ts > 10_000_000_000:  # milliseconds
            ts /= 1000.0
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return str(value)


def _fmt_percent(value: Any) -> str:
    try:
        n = float(value)
        return f"{n:g}%"
    except Exception:
        return f"{value}%"


def build_quota_payload(*, model: str, provider: str | None = None) -> dict[str, Any]:
    return {
        "model": (model or "").strip(),
        "provider": _provider_label(provider),
    }


def render_quota_response(data: dict[str, Any]) -> str:
    """Render a compact human-readable quota response."""
    provider = data.get("provider") or "unknown"
    model = data.get("model") or "unknown"
    upstream = data.get("upstream_url") or ""
    status = data.get("status") or "unknown"

    lines: list[str] = [f"Quota for `{model}` ({provider})"]
    if upstream:
        lines.append(f"Upstream: `{upstream}`")

    normalized = data.get("normalized")
    if isinstance(normalized, dict) and normalized:
        balance = normalized.get("balance")
        total = normalized.get("total")
        used = normalized.get("used")
        remaining = normalized.get("remaining")
        reset_at = normalized.get("reset_at")
        unit = normalized.get("unit") or ""
        plan = normalized.get("plan")
        if plan:
            lines.append(f"Plan: `{plan}`")
        if "allowed" in normalized:
            lines.append(f"Allowed: `{normalized.get('allowed')}`")
        if "limit_reached" in normalized:
            lines.append(f"Limit reached: `{normalized.get('limit_reached')}`")
        if balance is not None:
            lines.append(f"Balance: `{balance}`{(' ' + unit) if unit else ''}")
        balances = normalized.get("balances")
        if isinstance(balances, list) and balances:
            for item in balances:
                if not isinstance(item, dict):
                    continue
                currency = item.get("currency") or ""
                total_balance = item.get("total_balance")
                granted = item.get("granted_balance")
                topped_up = item.get("topped_up_balance")
                parts = [f"total `{total_balance}`"]
                if granted is not None:
                    parts.append(f"granted `{granted}`")
                if topped_up is not None:
                    parts.append(f"top-up `{topped_up}`")
                lines.append(f"Balance {currency}: " + ", ".join(parts))
        if remaining is not None:
            if unit == "%":
                lines.append(f"Remaining: `{_fmt_percent(remaining)}`")
            else:
                lines.append(f"Remaining: `{remaining}`{(' ' + unit) if unit else ''}")
        if used is not None:
            if unit == "%":
                lines.append(f"Used: `{_fmt_percent(used)}`")
            else:
                lines.append(f"Used: `{used}`{(' ' + unit) if unit else ''}")
        used_percent = normalized.get("used_percent")
        if used_percent is not None:
            lines.append(f"Used percent: `{used_percent}`%")
        if total is not None:
            lines.append(f"Total: `{total}`{(' ' + unit) if unit else ''}")
        if reset_at:
            reset_text = normalized.get("reset_at_local") or _human_timestamp(reset_at)
            reset_after = normalized.get("reset_after_human") or _human_duration(normalized.get("reset_after_seconds"))
            if reset_after:
                lines.append(f"Reset: `{reset_text}` (in `{reset_after}`)")
            else:
                lines.append(f"Reset: `{reset_text}`")
        windows = normalized.get("windows")
        if isinstance(windows, list) and windows:
            lines.append("Rate limit windows:")
            for item in windows:
                if not isinstance(item, dict):
                    continue
                name = item.get("name") or "window"
                window_human = item.get("limit_window_human") or _human_duration(item.get("limit_window_seconds"))
                reset_after = item.get("reset_after_human") or _human_duration(item.get("reset_after_seconds"))
                reset_at_text = item.get("reset_at_local") or _human_timestamp(item.get("reset_at"))
                used_text = _fmt_percent(item.get("used_percent"))
                remaining = item.get("remaining_percent")
                parts = [f"window `{window_human}`", f"used `{used_text}`"]
                if remaining is not None:
                    parts.append(f"remaining `{_fmt_percent(remaining)}`")
                if reset_after:
                    parts.append(f"resets in `{reset_after}`")
                if reset_at_text:
                    parts.append(f"at `{reset_at_text}`")
                lines.append(f"- {name}: " + ", ".join(parts))
        credits = normalized.get("credits")
        if isinstance(credits, dict):
            credit_bits = []
            for key in ("has_credits", "unlimited", "balance"):
                if key in credits:
                    credit_bits.append(f"{key} `{credits[key]}`")
            if credit_bits:
                lines.append("Credits: " + ", ".join(credit_bits))
        limits = normalized.get("limits")
        if isinstance(limits, list) and limits:
            lines.append("Limits:")
            for item in limits:
                if not isinstance(item, dict):
                    continue
                label = item.get("type") or "limit"
                details = []
                for key in ("remaining", "used", "total", "used_percent", "reset_at"):
                    if key in item:
                        suffix = "%" if key == "used_percent" else ""
                        details.append(f"{key} `{item[key]}`{suffix}")
                lines.append(f"- {label}: " + ", ".join(details))

    raw = data.get("raw")
    show_raw = raw is not None and (not isinstance(normalized, dict) or not normalized or status != "ok")
    if show_raw:
        try:
            pretty = json.dumps(raw, ensure_ascii=False, indent=2)
        except Exception:
            pretty = str(raw)
        if len(pretty) > 2200:
            pretty = pretty[:2200] + "…"
        lines.extend(["", "Raw:", f"```json\n{pretty}\n```"])
    elif data.get("message"):
        lines.append(str(data.get("message")))

    if status != "ok":
        http_status = data.get("http_status")
        if http_status is not None:
            lines.append(f"Upstream HTTP: `{http_status}`")
        lines.append(f"Status: `{status}`")
    return "\n".join(lines)


def _codex_account_id(provider: str | None) -> str:
    p = (provider or "").strip().lower()
    if p not in {"codex", "openai-codex"}:
        return ""
    try:
        from hermes_cli.auth import _read_codex_tokens

        token_data = _read_codex_tokens()
        tokens = token_data.get("tokens") or {}
        return str(tokens.get("account_id", "") or "").strip()
    except Exception:
        return ""


def _quota_headers(provider: str | None, api_key: str | None) -> dict[str, str]:
    headers = {"content-type": "application/json"}
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"
    account_id = _codex_account_id(provider)
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    return headers


def fetch_quota(
    *,
    model: str,
    provider: str | None = None,
    api_key: str | None = None,
    endpoint: str = DEFAULT_AICHATPROXY_QUOTA_URL,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Query local aichatproxy quota endpoint synchronously."""
    headers = _quota_headers(provider, api_key)
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        resp = client.post(
            endpoint,
            headers=headers,
            json=build_quota_payload(model=model, provider=provider),
        )
    try:
        data = resp.json()
    except Exception:
        data = {"status": "error", "message": resp.text}
    if resp.status_code >= 400:
        if isinstance(data, dict):
            data.setdefault("status", "error")
            data.setdefault("http_status", resp.status_code)
        resp.raise_for_status()
    if not isinstance(data, dict):
        return {"status": "error", "raw": data}
    return data


async def fetch_quota_async(
    *,
    model: str,
    provider: str | None = None,
    api_key: str | None = None,
    endpoint: str = DEFAULT_AICHATPROXY_QUOTA_URL,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Query local aichatproxy quota endpoint asynchronously."""
    headers = _quota_headers(provider, api_key)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        resp = await client.post(
            endpoint,
            headers=headers,
            json=build_quota_payload(model=model, provider=provider),
        )
    try:
        data = resp.json()
    except Exception:
        data = {"status": "error", "message": resp.text}
    if resp.status_code >= 400:
        if isinstance(data, dict):
            data.setdefault("status", "error")
            data.setdefault("http_status", resp.status_code)
        resp.raise_for_status()
    if not isinstance(data, dict):
        return {"status": "error", "raw": data}
    return data
