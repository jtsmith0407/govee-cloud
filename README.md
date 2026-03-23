# Govee Cloud Control for Home Assistant

A custom Home Assistant integration for controlling Govee smart devices via the Govee Cloud API (v2). Optimized for responsiveness with adaptive polling, command debouncing, and optimistic state updates.

## Features

- **Govee Cloud API v2** — supports the full capabilities-based device model
- **Adaptive polling** — polls faster (5s) during active use, backs off (60s) when idle
- **Optimistic state** — UI updates instantly on command, reconciles on next poll
- **Command debouncing** — rapid slider adjustments send only the final value (300ms window)
- **Rate budget tracking** — monitors the 10,000 req/day limit, reserves budget for commands
- **Auto-discovery** — all devices linked to your Govee account appear automatically
- **HACS compatible**

## Prerequisites

You need a Govee API key:

1. Open the **Govee Home** app
2. Go to **Account → Settings → About Us → Apply for API Key**
3. The key will be emailed to you

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots menu → **Custom repositories**
3. Add this repository URL and select **Integration** as the category
4. Search for "Govee Cloud Control" and install
5. Restart Home Assistant

### Manual

1. Copy the `custom_components/govee_cloud` folder into your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Govee Cloud Control**
3. Enter your Govee API key
4. Adjust the base polling interval if desired (default: 15 seconds)
5. Devices are fetched automatically from your Govee account

## Adaptive Polling

The integration automatically adjusts polling frequency based on activity:

| State | Interval | Trigger |
|-------|----------|---------|
| Active | 5s | Within 30s of last command |
| Normal | 15s (configurable) | Default |
| Idle | 60s | No commands for 5+ minutes |

When the daily rate limit budget runs low, polling is further throttled to preserve budget for control commands.

## Rate Limit Management

The Govee API allows **10,000 requests per day**. The integration:

- Tracks remaining budget via API response headers
- Reserves 500 requests for control commands
- Skips polling when budget is low
- Exposes `rate_limit_remaining` and `rate_limit_total` as entity attributes for monitoring

## Supported Devices

Any device linked to your Govee account that supports the Cloud API, including:

- LED strip lights and bulbs
- Air purifiers and humidifiers
- Smart plugs and switches
- Thermometers and sensors
- Heaters, fans, and more

Light entities are created for any device with on/off + optional brightness/color capabilities.

## Options

| Option | Default | Range | Description |
|--------|---------|-------|-------------|
| Base polling interval | 15s | 5–120s | Normal polling rate (adaptive system adjusts from here) |

## Troubleshooting

- **Invalid API key**: Double-check the key from your email. Keys do not expire but are one per account.
- **Devices not appearing**: Ensure devices are set up in the Govee Home app and online.
- **Rate limit warnings**: Reduce polling interval or reduce the number of automations polling state. Check entity attributes for current budget.
- **Slow response**: The adaptive system polls faster during active use. Cloud latency is typically 1-3 seconds.

## License

MIT
