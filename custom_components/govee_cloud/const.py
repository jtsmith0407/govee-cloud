"""Constants for the Govee Cloud integration."""

DOMAIN = "govee_cloud"

# API
API_BASE_URL = "https://openapi.api.govee.com"
API_DEVICES_URL = f"{API_BASE_URL}/router/api/v1/user/devices"
API_STATE_URL = f"{API_BASE_URL}/router/api/v1/device/state"
API_CONTROL_URL = f"{API_BASE_URL}/router/api/v1/device/control"

# Rate limits
DAILY_REQUEST_LIMIT = 10000
RATE_LIMIT_BUFFER = 500  # reserve for control commands

# Capability types
CAP_ON_OFF = "devices.capabilities.on_off"
CAP_RANGE = "devices.capabilities.range"
CAP_COLOR_SETTING = "devices.capabilities.color_setting"
CAP_TOGGLE = "devices.capabilities.toggle"
CAP_SEGMENT_COLOR = "devices.capabilities.segment_color_setting"
CAP_WORK_MODE = "devices.capabilities.work_mode"

# Capability instances
INST_POWER = "powerSwitch"
INST_BRIGHTNESS = "brightness"
INST_COLOR_RGB = "colorRgb"
INST_COLOR_TEMP = "colorTemperatureK"

# Polling
DEFAULT_POLL_INTERVAL = 15  # seconds, base interval
ACTIVE_POLL_INTERVAL = 5  # seconds, after recent command
IDLE_POLL_INTERVAL = 60  # seconds, no activity for a while
ACTIVE_WINDOW = 30  # seconds to stay in active polling after a command
IDLE_THRESHOLD = 300  # seconds of no commands before idle mode

# Color temp
MIN_COLOR_TEMP_KELVIN = 2000
MAX_COLOR_TEMP_KELVIN = 9000

# Debounce
COMMAND_DEBOUNCE_SECONDS = 0.3

# Optimistic state window: how long to hold the assumed state after a command
# before letting a poll overwrite it.  Cloud devices can take 5-10+ seconds to
# physically respond after the API accepts the command, so this must be longer
# than ACTIVE_POLL_INTERVAL to prevent the entity from flipping back to the old
# state while the device is still processing the command.
OPTIMISTIC_SECONDS = 12

# Config keys
CONF_API_KEY = "api_key"
CONF_POLL_INTERVAL = "poll_interval"
