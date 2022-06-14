"""Moving average sensor implementation."""
from __future__ import annotations

import logging
import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from collections import deque
from datetime import (
    datetime, 
    timedelta
)
from typing import Tuple

from . import (
    DOMAIN, 
    PLATFORMS
)
from homeassistant.components.sensor import (
    ATTR_STATE_CLASS as SENSOR_ATTR_STATE_CLASS,
    DEVICE_CLASSES as SENSOR_DEVICE_CLASSES,
    DOMAIN as SENSOR_DOMAIN,
    PLATFORM_SCHEMA,
    STATE_CLASSES as SENSOR_STATE_CLASSES,
    SensorEntity
)
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_ENTITY_ID,
    ATTR_ICON,
    ATTR_UNIT_OF_MEASUREMENT,
    CONF_ENTITY_ID,
    CONF_NAME,
    CONF_UNIQUE_ID,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN
)
from homeassistant.core import (
    HomeAssistant,
    callback
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.reload import async_setup_reload_service
from homeassistant.helpers.typing import (
    ConfigType, 
    DiscoveryInfoType
)


_LOGGER = logging.getLogger(__name__)

DEFAULT_ICON = "mdi:chart-line-variant"
DEFAULT_PRECISION = 2

CONF_FILTER_WINDOW_SIZE = "window_size"
CONF_FILTER_UPDATE_INTERVAL = "update_interval"
CONF_FILTER_PRECISION = "precision"
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_ENTITY_ID): cv.entity_domain(SENSOR_DOMAIN),
        vol.Optional(CONF_NAME): cv.string,
        vol.Optional(CONF_UNIQUE_ID): cv.string,
        vol.Required(CONF_FILTER_WINDOW_SIZE): vol.All(cv.time_period, cv.positive_timedelta),
        vol.Required(CONF_FILTER_UPDATE_INTERVAL): vol.All(cv.time_period, cv.positive_timedelta),
        vol.Optional(CONF_FILTER_PRECISION, default=DEFAULT_PRECISION): vol.Coerce(int)
    }
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Setup the sensor."""
    await async_setup_reload_service(hass, DOMAIN, PLATFORMS)

    name = config.get(CONF_NAME)
    unique_id = config.get(CONF_UNIQUE_ID)
    entity_id = config.get(CONF_ENTITY_ID)
    avg = MovingAvg(name, config.get(CONF_FILTER_WINDOW_SIZE), config.get(CONF_FILTER_PRECISION))

    async_add_entities([SensorMovingAvg(name, unique_id, entity_id, avg)])


class SensorMovingAvg(SensorEntity):
    """Representation of moving average sensor."""

    def __init__(self, name, unique_id, entity_id, avg) -> None:
        """Initialize sensor."""
        self._name = name
        self._attr_unique_id = unique_id
        self._entity = entity_id
        self._avg = avg
        self._unit_of_measurement = None
        self._state = None
        self._icon = None
        self._device_class = None
        self._attr_state_class = None

    @callback
    def _update_filter_sensor_state_event(self, event):
        """Handle device state changes."""
        _LOGGER.debug("Updating %s on event: %s", self._name, event)
        self._update_filter_sensor_state(event.data.get("new_state"))

    @callback
    def _update_filter_sensor_state(self, new_state):
        """Process device state changes."""
        if new_state is None:
            _LOGGER.debug("Updating %s, new_state is None", self._name)
            self._state = None
            self.async_write_ha_state()
            return

        if new_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            self._state = new_state.state
            self.async_write_ha_state()
            return

        if new_state.last_changed == new_state.last_updated:
            # get attributes from entitity
            if self._icon is None:
                self._icon = new_state.attributes.get(ATTR_ICON, DEFAULT_ICON)
            if (self._device_class is None) and (new_state.attributes.get(ATTR_DEVICE_CLASS) in SENSOR_DEVICE_CLASSES):
                self._device_class = new_state.attributes.get(ATTR_DEVICE_CLASS)
            if (self._attr_state_class is None) and (new_state.attributes.get(SENSOR_ATTR_STATE_CLASS) in SENSOR_STATE_CLASSES):
                self._attr_state_class = new_state.attributes.get(SENSOR_ATTR_STATE_CLASS)
            if self._unit_of_measurement is None:
                self._unit_of_measurement = new_state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
            # update moving average
            new_val = float(new_state.state)
            _LOGGER.debug(f"{self._name}: Updating with {new_val}")
            self._state = self._avg.update_value(new_val, new_state.last_changed)
            self.async_write_ha_state()
        else:
            _LOGGER.debug(f"{self._name}: Not updating, last_changed != last_updated")


    async def async_added_to_hass(self):
        """Register callbacks."""
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._entity], self._update_filter_sensor_state_event
            )
        )

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def icon(self):
        """Return the icon to use in the frontend, if any."""
        return self._icon

    @property
    def native_unit_of_measurement(self):
        """Return the unit_of_measurement of the device."""
        return self._unit_of_measurement

    @property
    def should_poll(self):
        """No polling needed."""
        return False

    @property
    def extra_state_attributes(self):
        """Return the state attributes of the sensor."""
        return {ATTR_ENTITY_ID: self._entity, "data_points": self._avg.data_points()}

    @property
    def device_class(self):
        """Return device class."""
        return self._device_class


class MovingAvg:
    """Moving average computation."""

    def __init__(self, name, window: timedelta, precision: int) -> None:
        """Initialize moving average."""
        self._name = name
        self._window = window
        self._precision = precision
        self._data = deque()

    def update_value(self, val: float, timestamp: datetime) -> float | None:
        """Update moving average with value and timestamp."""
        self._data.append(MovingAvg._tuple(val, timestamp))
        return self.update(timestamp)

    def update(self, timestamp: datetime) -> float | None:
        """Update moving average for timestamp."""
        ret_val = None
        size = len(self._data)
        if size == 1:
            ret_val = MovingAvg._value(self._data[0])
        if size > 1:
            # move window
            start = timestamp - self._window
            _LOGGER.debug(f"{self._name}: Window starts {start}")
            removed = None
            while MovingAvg._timestamp(self._data[0]) < start:
                removed = self._data.popleft()
                _LOGGER.debug(f"{self._name}: Removing {MovingAvg._value(removed)},{MovingAvg._timestamp(removed)}")
            if ((removed is not None) and 
               ((len(self._data) == 0) or (MovingAvg._timestamp(self._data[0]) > start))):
                self._data.appendleft(MovingAvg._tuple(MovingAvg._value(removed), start))
                _LOGGER.debug(f"{self._name}: Adding back {MovingAvg._value(removed)}")
            # compute avg
            if len(self._data) == 1:
                ret_val = MovingAvg._value(self._data[0])
            else:
                duration = (timestamp - MovingAvg._timestamp(self._data[0])).total_seconds()
                _LOGGER.debug(f"{self._name}: Duration is {duration}")
                if duration > self._window.total_seconds():
                    _LOGGER.error(f"{self._name}: Invalid duration - {duration} > {self._window.total_seconds()}")
                ret_val = 0.0
                prev = None
                for cur in self._data:
                    if prev is not None:
                        weighted = MovingAvg._weighted(prev, MovingAvg._timestamp(cur), duration)
                        _LOGGER.debug(f"{self._name}: Adding {weighted} - {MovingAvg._value(prev)},{MovingAvg._timestamp(prev)}, {MovingAvg._timestamp(cur)}")
                        ret_val = ret_val + weighted
                    prev = cur
                if timestamp > MovingAvg._timestamp(cur):
                    weighted = MovingAvg._weighted(cur, timestamp, duration)
                    _LOGGER.debug(f"{self._name}: Adding {weighted} - {MovingAvg._value(cur)},{MovingAvg._timestamp(cur)}, {timestamp}")
                    ret_val = ret_val + weighted
        return (None if ret_val is None else round(ret_val, self._precision))

    def data_points(self) -> int:
        """Number of data points currently in window."""
        return len(self._data)

    def _timestamp(tuple: Tuple[float, datetime]) -> datetime:
        return tuple[1]

    def _value(tuple: Tuple[float, datetime]) -> float:
        return tuple[0]

    def _tuple(val: float, timestamp: datetime) -> Tuple[float, datetime]:
        return [val, timestamp]

    def _weighted(cur: Tuple[float, datetime], next: datetime, duration: float) -> float:
        delta = (next - MovingAvg._timestamp(cur)).total_seconds()
        return (MovingAvg._value(cur) * delta / duration)
