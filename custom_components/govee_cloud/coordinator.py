"""Govee Cloud data coordinator with adaptive polling and optimistic state."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GoveeApiClient, GoveeApiError, GoveeRateLimitError
from .const import (
    ACTIVE_POLL_INTERVAL,
    ACTIVE_WINDOW,
    CAP_COLOR_SETTING,
    CAP_ON_OFF,
    CAP_RANGE,
    COMMAND_DEBOUNCE_SECONDS,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    IDLE_POLL_INTERVAL,
    IDLE_THRESHOLD,
    INST_BRIGHTNESS,
    INST_COLOR_RGB,
    INST_COLOR_TEMP,
    INST_POWER,
    OPTIMISTIC_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


class GoveeDeviceState:
    """Tracked state for a single Govee device."""

    def __init__(self, device_data: dict[str, Any]) -> None:
        self.sku: str = device_data.get("sku", "")
        self.device_id: str = device_data.get("device", "")
        self.name: str = device_data.get("deviceName", f"Govee {self.sku}")
        self.capabilities: list[dict] = device_data.get("capabilities", [])

        # State
        self.online: bool = True
        self.on: bool | None = None
        self.brightness: int | None = None
        self.color_rgb: int | None = None  # packed integer
        self.color_temp_kelvin: int | None = None

        # Tracking
        self._optimistic_until: float = 0
        self._pending_state: dict[str, Any] = {}
        self.consecutive_failures: int = 0

    @property
    def unique_id(self) -> str:
        return self.device_id.replace(":", "").lower()

    @property
    def color_r(self) -> int:
        if self.color_rgb is None:
            return 0
        return (self.color_rgb >> 16) & 0xFF

    @property
    def color_g(self) -> int:
        if self.color_rgb is None:
            return 0
        return (self.color_rgb >> 8) & 0xFF

    @property
    def color_b(self) -> int:
        if self.color_rgb is None:
            return 0
        return self.color_rgb & 0xFF

    @property
    def is_optimistic(self) -> bool:
        return time.monotonic() < self._optimistic_until

    def has_capability(self, cap_type: str, instance: str) -> bool:
        """Check if device supports a given capability."""
        for cap in self.capabilities:
            if cap.get("type") == cap_type and cap.get("instance") == instance:
                return True
        return False

    @property
    def supports_brightness(self) -> bool:
        return self.has_capability(CAP_RANGE, INST_BRIGHTNESS)

    @property
    def supports_color(self) -> bool:
        return self.has_capability(CAP_COLOR_SETTING, INST_COLOR_RGB)

    @property
    def supports_color_temp(self) -> bool:
        return self.has_capability(CAP_COLOR_SETTING, INST_COLOR_TEMP)

    @property
    def color_temp_range(self) -> tuple[int, int] | None:
        """Return (min_kelvin, max_kelvin) from device capabilities, or None if unavailable."""
        for cap in self.capabilities:
            if cap.get("type") == CAP_COLOR_SETTING and cap.get("instance") == INST_COLOR_TEMP:
                r = cap.get("parameters", {}).get("range", {})
                if "min" in r and "max" in r:
                    return (int(r["min"]), int(r["max"]))
        return None

    def apply_optimistic(self, **kwargs: Any) -> None:
        """Apply optimistic state after sending a command."""
        self._optimistic_until = time.monotonic() + OPTIMISTIC_SECONDS
        for key, value in kwargs.items():
            setattr(self, key, value)

    def update_from_api(self, capabilities: list[dict]) -> bool:
        """Update state from API response. Returns True if state changed."""
        if self.is_optimistic:
            return False  # Don't overwrite optimistic state

        changed = False
        for cap in capabilities:
            cap_type = cap.get("type", "")
            instance = cap.get("instance", "")
            value = cap.get("value")

            if instance == INST_POWER:
                new_on = value == 1
                if self.on != new_on:
                    self.on = new_on
                    changed = True
            elif instance == INST_BRIGHTNESS:
                if self.brightness != value:
                    self.brightness = value
                    changed = True
            elif instance == INST_COLOR_RGB:
                # Govee v2 API returns color as {"r": int, "g": int, "b": int}
                if isinstance(value, dict):
                    packed = (
                        (value.get("r", 0) << 16)
                        | (value.get("g", 0) << 8)
                        | value.get("b", 0)
                    )
                else:
                    packed = value
                if self.color_rgb != packed:
                    self.color_rgb = packed
                    self.color_temp_kelvin = None
                    changed = True
            elif instance == INST_COLOR_TEMP:
                if self.color_temp_kelvin != value:
                    self.color_temp_kelvin = value
                    self.color_rgb = None
                    changed = True
            elif instance == "online":
                new_online = bool(value)
                if self.online != new_online:
                    self.online = new_online
                    changed = True

        return changed


class GoveeCloudCoordinator(DataUpdateCoordinator):
    """Coordinator with adaptive polling, rate budgeting, and command debouncing."""

    def __init__(
        self, hass: HomeAssistant, api: GoveeApiClient, poll_interval: int
    ) -> None:
        self._api = api
        self._base_interval = poll_interval
        self._last_command_time: float = 0
        self._debounce_timers: dict[str, asyncio.TimerHandle] = {}
        self.devices: dict[str, GoveeDeviceState] = {}

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=None,  # We manage timing ourselves
        )

        self._poll_task: asyncio.Task | None = None
        self._running = False

    @property
    def api(self) -> GoveeApiClient:
        return self._api

    @property
    def _current_interval(self) -> int:
        """Calculate polling interval based on recent activity."""
        now = time.monotonic()
        elapsed = now - self._last_command_time

        if self._last_command_time == 0:
            return self._base_interval
        if elapsed < ACTIVE_WINDOW:
            return ACTIVE_POLL_INTERVAL
        if elapsed > IDLE_THRESHOLD:
            return IDLE_POLL_INTERVAL
        return self._base_interval

    async def async_start(self) -> None:
        """Start by fetching devices, then poll state in the background.

        We deliberately do not await the initial state poll here — the Govee API
        takes several seconds per batch even with concurrency, and blocking HA
        startup for that long delays all other integrations.  Entities will come
        up with is_on=None (unknown) for one poll cycle (~3-15 s) and then
        populate normally.
        """
        await self._fetch_devices()
        self._running = True
        # Poll loop does an immediate first fetch before entering its sleep cycle
        self._poll_task = self.hass.async_create_background_task(
            self._adaptive_poll_loop(), "govee_cloud_poll"
        )

    async def async_stop(self) -> None:
        """Stop polling."""
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
        for handle in self._debounce_timers.values():
            handle.cancel()
        self._debounce_timers.clear()

    async def _fetch_devices(self) -> None:
        """Fetch the device list from the API. Raises on failure."""
        device_list = await self._api.get_devices()
        for dev_data in device_list:
            device_id = dev_data.get("device", "")
            if device_id not in self.devices:
                self.devices[device_id] = GoveeDeviceState(dev_data)
                _LOGGER.info(
                    "Found Govee device: %s (%s)",
                    dev_data.get("deviceName", "Unknown"),
                    dev_data.get("sku", "Unknown"),
                )

    async def _adaptive_poll_loop(self) -> None:
        """Poll devices with adaptive intervals based on activity.

        Polls immediately on first run so entities populate state quickly after
        setup, then sleeps between subsequent cycles.
        """
        first = True
        while self._running:
            try:
                if not first:
                    await asyncio.sleep(self._current_interval)
                first = False
                await self._poll_all_devices()
            except asyncio.CancelledError:
                break
            except Exception:
                _LOGGER.exception("Error in poll loop")
                await asyncio.sleep(30)

    async def _poll_device(self, device: GoveeDeviceState) -> bool:
        """Poll a single device. Returns True if state changed."""
        if device.is_optimistic:
            return False

        try:
            payload = await self._api.get_device_state(device.sku, device.device_id)
            capabilities = payload.get("capabilities", [])
            changed = device.update_from_api(capabilities)
            device.consecutive_failures = 0
            if not device.online:
                device.online = True
                changed = True
            return changed
        except GoveeRateLimitError:
            _LOGGER.debug("Rate limited while polling %s", device.name)
            return False
        except GoveeApiError as err:
            device.consecutive_failures = getattr(device, "consecutive_failures", 0) + 1
            if device.consecutive_failures >= 3 and device.online:
                device.online = False
                _LOGGER.warning(
                    "Marking %s offline after %d consecutive failures",
                    device.name,
                    device.consecutive_failures,
                )
                return True
            _LOGGER.debug("Failed to poll %s: %s", device.name, err)
            return False

    async def _poll_all_devices(self) -> None:
        """Poll state for all devices concurrently."""
        if not self._api.budget_available:
            _LOGGER.debug(
                "Rate budget low (%d remaining), skipping poll cycle",
                self._api.rate_limit_remaining,
            )
            return

        results = await asyncio.gather(
            *[self._poll_device(d) for d in self.devices.values()],
            return_exceptions=True,
        )

        if any(r is True for r in results):
            self.async_set_updated_data(self.devices)

    async def _async_update_data(self) -> dict[str, GoveeDeviceState]:
        """Called by DataUpdateCoordinator if update_interval is set."""
        await self._poll_all_devices()
        return self.devices

    def send_command(
        self,
        device: GoveeDeviceState,
        capability_type: str,
        instance: str,
        value: Any,
        optimistic_state: dict[str, Any] | None = None,
    ) -> None:
        """Send a command with debouncing and optimistic state."""
        self._last_command_time = time.monotonic()

        # Apply optimistic state immediately
        if optimistic_state:
            device.apply_optimistic(**optimistic_state)
            self.async_set_updated_data(self.devices)

        # Debounce: cancel any pending command for same device+instance
        debounce_key = f"{device.device_id}:{instance}"
        if debounce_key in self._debounce_timers:
            self._debounce_timers[debounce_key].cancel()

        # Schedule the actual API call after debounce window
        self._debounce_timers[debounce_key] = self.hass.loop.call_later(
            COMMAND_DEBOUNCE_SECONDS,
            lambda: self.hass.async_create_task(
                self._execute_command(
                    device, capability_type, instance, value, debounce_key
                )
            ),
        )

    async def _execute_command(
        self,
        device: GoveeDeviceState,
        capability_type: str,
        instance: str,
        value: Any,
        debounce_key: str,
    ) -> None:
        """Execute a command against the API."""
        self._debounce_timers.pop(debounce_key, None)
        try:
            await self._api.control_device(
                device.sku, device.device_id, capability_type, instance, value
            )
            _LOGGER.debug(
                "Command OK: %s → %s=%s", device.name, instance, value
            )
        except GoveeApiError as err:
            _LOGGER.error(
                "Command FAILED: %s → %s=%s: %s", device.name, instance, value, err
            )
