"""Govee Cloud API client with rate limit tracking."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import aiohttp

from .const import (
    API_CONTROL_URL,
    API_DEVICES_URL,
    API_STATE_URL,
    DAILY_REQUEST_LIMIT,
    RATE_LIMIT_BUFFER,
)

_LOGGER = logging.getLogger(__name__)


class GoveeApiError(Exception):
    """Base exception for Govee API errors."""


class GoveeRateLimitError(GoveeApiError):
    """Raised when rate limit is exceeded."""


class GoveeAuthError(GoveeApiError):
    """Raised on authentication failure."""


class GoveeApiClient:
    """Async client for the Govee Cloud API v2."""

    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        self._session = session
        self._api_key = api_key
        self.rate_limit_remaining: int = DAILY_REQUEST_LIMIT
        self.rate_limit_total: int = DAILY_REQUEST_LIMIT
        self.rate_limit_reset: float = 0
        self._request_count: int = 0

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Govee-API-Key": self._api_key,
        }

    @property
    def budget_available(self) -> bool:
        """Check if we have enough rate budget for a polling request."""
        return self.rate_limit_remaining > RATE_LIMIT_BUFFER

    @property
    def budget_critical(self) -> bool:
        """Check if rate budget is critically low (control commands only)."""
        return self.rate_limit_remaining < 100

    def _update_rate_limits(self, headers: dict) -> None:
        """Extract rate limit info from response headers."""
        if "X-RateLimit-Remaining" in headers:
            self.rate_limit_remaining = int(headers["X-RateLimit-Remaining"])
        if "X-RateLimit-Limit" in headers:
            self.rate_limit_total = int(headers["X-RateLimit-Limit"])
        if "X-RateLimit-Reset" in headers:
            self.rate_limit_reset = float(headers["X-RateLimit-Reset"])

    async def _request(
        self,
        method: str,
        url: str,
        json_data: dict | None = None,
        is_control: bool = False,
    ) -> dict[str, Any]:
        """Make an API request with rate limit handling."""
        if not is_control and not self.budget_available:
            _LOGGER.debug(
                "Skipping poll request, rate budget low: %d remaining",
                self.rate_limit_remaining,
            )
            raise GoveeRateLimitError("Rate budget too low for polling")

        if is_control and self.budget_critical:
            _LOGGER.warning(
                "Rate budget critical: %d remaining. Command may fail.",
                self.rate_limit_remaining,
            )

        try:
            async with self._session.request(
                method, url, headers=self._headers, json=json_data, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                self._update_rate_limits(resp.headers)
                self._request_count += 1

                if resp.status == 401:
                    raise GoveeAuthError("Invalid API key")
                if resp.status == 429:
                    _LOGGER.warning("Govee API rate limit hit")
                    raise GoveeRateLimitError("Rate limit exceeded")
                if resp.status != 200:
                    text = await resp.text()
                    raise GoveeApiError(
                        f"API error {resp.status}: {text}"
                    )

                return await resp.json()
        except TimeoutError as err:
            raise GoveeApiError(f"Request timed out: {url}") from err
        except aiohttp.ClientError as err:
            raise GoveeApiError(f"Connection error: {err}") from err

    async def get_devices(self) -> list[dict[str, Any]]:
        """Fetch the list of all devices."""
        result = await self._request("GET", API_DEVICES_URL)
        return result.get("data", [])

    async def get_device_state(self, sku: str, device: str) -> dict[str, Any]:
        """Get the current state of a device."""
        payload = {
            "requestId": str(uuid.uuid4()),
            "payload": {"sku": sku, "device": device},
        }
        result = await self._request("POST", API_STATE_URL, json_data=payload)
        return result.get("payload", {})

    async def control_device(
        self,
        sku: str,
        device: str,
        capability_type: str,
        instance: str,
        value: Any,
    ) -> bool:
        """Send a control command to a device."""
        payload = {
            "requestId": str(uuid.uuid4()),
            "payload": {
                "sku": sku,
                "device": device,
                "capability": {
                    "type": capability_type,
                    "instance": instance,
                    "value": value,
                },
            },
        }
        result = await self._request(
            "POST", API_CONTROL_URL, json_data=payload, is_control=True
        )
        # Govee API occasionally returns HTTP 200 with an error code in the body.
        code = result.get("code") if isinstance(result, dict) else None
        if code is not None and code != 200:
            msg = result.get("msg") or result.get("message") or "unknown error"
            raise GoveeApiError(f"API rejected command (code {code}): {msg}")
        return True

    async def validate_key(self) -> bool:
        """Validate the API key by fetching devices."""
        try:
            await self.get_devices()
            return True
        except GoveeAuthError:
            return False
        except GoveeApiError:
            return True  # API reachable, key accepted (other error)
