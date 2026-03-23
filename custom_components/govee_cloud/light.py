"""Govee Cloud light platform."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CAP_COLOR_SETTING,
    CAP_ON_OFF,
    CAP_RANGE,
    DOMAIN,
    INST_BRIGHTNESS,
    INST_COLOR_RGB,
    INST_COLOR_TEMP,
    INST_POWER,
    MAX_COLOR_TEMP_KELVIN,
    MIN_COLOR_TEMP_KELVIN,
)
from .coordinator import GoveeCloudCoordinator, GoveeDeviceState

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Govee Cloud lights from a config entry."""
    coordinator: GoveeCloudCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for device in coordinator.devices.values():
        # Only create light entities for devices with on/off capability
        if device.has_capability(CAP_ON_OFF, INST_POWER):
            entities.append(GoveeCloudLight(coordinator, device))

    async_add_entities(entities)


class GoveeCloudLight(CoordinatorEntity, LightEntity):
    """Representation of a Govee cloud-controlled light."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(
        self, coordinator: GoveeCloudCoordinator, device: GoveeDeviceState
    ) -> None:
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"govee_cloud_{device.unique_id}"

        # Build supported color modes based on capabilities
        modes: set[ColorMode] = set()
        if device.supports_color:
            modes.add(ColorMode.RGB)
        if device.supports_color_temp:
            modes.add(ColorMode.COLOR_TEMP)
        if not modes and device.supports_brightness:
            modes.add(ColorMode.BRIGHTNESS)
        if not modes:
            modes.add(ColorMode.ONOFF)

        self._attr_supported_color_modes = modes

        # Use per-device range from capabilities; fall back to global defaults.
        # This prevents out-of-range errors for devices like H6004 whose minimum
        # is higher than the global MIN_COLOR_TEMP_KELVIN (e.g. 2700K vs 2000K).
        temp_range = device.color_temp_range
        self._attr_min_color_temp_kelvin = temp_range[0] if temp_range else MIN_COLOR_TEMP_KELVIN
        self._attr_max_color_temp_kelvin = temp_range[1] if temp_range else MAX_COLOR_TEMP_KELVIN

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._device.device_id)},
            name=self._device.name,
            manufacturer="Govee",
            model=self._device.sku,
        )

    @property
    def available(self) -> bool:
        return self._device.online

    @property
    def is_on(self) -> bool | None:
        return self._device.on

    @property
    def brightness(self) -> int | None:
        if self._device.brightness is None:
            return None
        # Govee uses 1-100, HA uses 1-255
        return round(self._device.brightness * 255 / 100)

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        if self._device.color_rgb is not None:
            return (self._device.color_r, self._device.color_g, self._device.color_b)
        return None

    @property
    def color_temp_kelvin(self) -> int | None:
        return self._device.color_temp_kelvin

    @property
    def color_mode(self) -> ColorMode | None:
        modes = self._attr_supported_color_modes
        if ColorMode.COLOR_TEMP in modes and self._device.color_temp_kelvin is not None:
            return ColorMode.COLOR_TEMP
        if ColorMode.RGB in modes and self._device.color_rgb is not None:
            return ColorMode.RGB
        # State not yet known — prefer COLOR_TEMP over RGB since color_temp_kelvin=None is
        # valid but rgb_color=None triggers an HA warning. Only fall back to RGB if the
        # device has no COLOR_TEMP capability at all.
        if ColorMode.COLOR_TEMP in modes:
            return ColorMode.COLOR_TEMP
        if ColorMode.RGB in modes:
            return ColorMode.RGB
        if ColorMode.BRIGHTNESS in modes:
            return ColorMode.BRIGHTNESS
        if ColorMode.COLOR_TEMP in modes:
            return ColorMode.COLOR_TEMP
        return ColorMode.ONOFF

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on with optional attributes."""
        coordinator: GoveeCloudCoordinator = self.coordinator

        # Turn on first if the light is off
        if self._device.on is not True:
            coordinator.send_command(
                self._device,
                CAP_ON_OFF,
                INST_POWER,
                1,
                optimistic_state={"on": True},
            )

        if ATTR_BRIGHTNESS in kwargs:
            brightness = max(1, round(kwargs[ATTR_BRIGHTNESS] * 100 / 255))
            coordinator.send_command(
                self._device,
                CAP_RANGE,
                INST_BRIGHTNESS,
                brightness,
                optimistic_state={"brightness": brightness},
            )

        if ATTR_RGB_COLOR in kwargs:
            r, g, b = kwargs[ATTR_RGB_COLOR]
            packed = (r << 16) | (g << 8) | b
            coordinator.send_command(
                self._device,
                CAP_COLOR_SETTING,
                INST_COLOR_RGB,
                packed,
                optimistic_state={
                    "color_rgb": packed,
                    "color_temp_kelvin": None,
                },
            )

        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            kelvin = max(self.min_color_temp_kelvin, min(self.max_color_temp_kelvin, kwargs[ATTR_COLOR_TEMP_KELVIN]))
            coordinator.send_command(
                self._device,
                CAP_COLOR_SETTING,
                INST_COLOR_TEMP,
                kelvin,
                optimistic_state={
                    "color_temp_kelvin": kelvin,
                    "color_rgb": None,
                },
            )

        # Simple turn on with no extras
        if not any(
            k in kwargs
            for k in (ATTR_BRIGHTNESS, ATTR_RGB_COLOR, ATTR_COLOR_TEMP_KELVIN)
        ):
            coordinator.send_command(
                self._device,
                CAP_ON_OFF,
                INST_POWER,
                1,
                optimistic_state={"on": True},
            )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        self.coordinator.send_command(
            self._device,
            CAP_ON_OFF,
            INST_POWER,
            0,
            optimistic_state={"on": False},
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose rate limit info for monitoring."""
        api = self.coordinator.api
        return {
            "rate_limit_remaining": api.rate_limit_remaining,
            "rate_limit_total": api.rate_limit_total,
        }
