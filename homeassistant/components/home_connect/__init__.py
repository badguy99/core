"""Support for BSH Home Connect appliances."""

import asyncio
from datetime import timedelta
import logging
from typing import Optional

from homeconnect.api import HomeConnectError
from requests import HTTPError
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_DEVICE,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow, config_validation as cv
from homeassistant.util import Throttle

from . import api, config_flow
from .const import (
    ATTR_KEY,
    ATTR_OPTION_KEY,
    ATTR_OPTION_UNIT,
    ATTR_OPTION_VALUE,
    ATTR_PROGRAM,
    ATTR_UNIT,
    ATTR_VALUE,
    BSH_PAUSE,
    BSH_RESUME,
    DOMAIN,
    OAUTH2_AUTHORIZE,
    OAUTH2_TOKEN,
    SERVICE_OPTION_ACTIVE,
    SERVICE_OPTION_SELECTED,
    SERVICE_PAUSE,
    SERVICE_RESUME,
    SERVICE_SELECT,
    SERVICE_SETTING,
    SERVICE_START,
)

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(minutes=1)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_CLIENT_ID): cv.string,
                vol.Required(CONF_CLIENT_SECRET): cv.string,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

SERVICE_SETTING_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_KEY): str,
        vol.Required(ATTR_VALUE): vol.Coerce(str),
    }
)

SERVICE_PROGRAM_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_PROGRAM): str,
        vol.Optional(ATTR_OPTION_KEY): str,
        vol.Optional(ATTR_OPTION_VALUE): vol.Any(int, str),
        vol.Optional(ATTR_OPTION_UNIT): str,
    }
)

SERVICE_COMMAND_SCHEMA = vol.Schema({vol.Required(ATTR_ENTITY_ID): cv.entity_id})

PLATFORMS = ["binary_sensor", "light", "sensor", "switch"]


def _get_appliance_by_entity_id(
    hass: HomeAssistant, entity_id: str
) -> Optional[api.HomeConnectDevice]:
    """Return a Home Connect appliance instance given an entity_id."""
    for hc in hass.data[DOMAIN].values():
        for dev_dict in hc.devices:
            device = dev_dict[CONF_DEVICE]
            for entity in device.entities:
                if entity.entity_id == entity_id:
                    return device.appliance
    _LOGGER.error("Appliance for %s not found.", entity_id)
    return None


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up Home Connect component."""
    hass.data[DOMAIN] = {}

    if DOMAIN not in config:
        return True

    config_flow.OAuth2FlowHandler.async_register_implementation(
        hass,
        config_entry_oauth2_flow.LocalOAuth2Implementation(
            hass,
            DOMAIN,
            config[DOMAIN][CONF_CLIENT_ID],
            config[DOMAIN][CONF_CLIENT_SECRET],
            OAUTH2_AUTHORIZE,
            OAUTH2_TOKEN,
        ),
    )

    async def _async_service_program(call, method):
        """Generic callback for services taking a program."""
        program = call.data[ATTR_PROGRAM]
        entity_id = call.data[ATTR_ENTITY_ID]
        option_key = call.data.get(ATTR_OPTION_KEY, None)
        option_value = call.data.get(ATTR_OPTION_VALUE, None)
        option_unit = call.data.get(ATTR_OPTION_UNIT, None)
        if option_key is not None and option_value is not None:
            _options = {ATTR_KEY: option_key, ATTR_VALUE: option_value}
            if option_unit is not None:
                _options[ATTR_UNIT] = option_unit
            options = [_options]
        else:
            options = None
        _LOGGER.debug("Options: %s", options)
        appliance = _get_appliance_by_entity_id(hass, entity_id)
        if appliance is not None:
            await hass.async_add_executor_job(
                getattr(appliance, method), program, options
            )

    async def _async_service_command(call, command):
        """Generic callback for services executing a command."""
        entity_id = call.data[ATTR_ENTITY_ID]
        appliance = _get_appliance_by_entity_id(hass, entity_id)
        if appliance is not None:
            await hass.async_add_executor_job(appliance.execute_command, command)

    async def _async_service_key_value(call, method):
        """Generic callback for services taking a key and value."""
        key = call.data[ATTR_KEY]
        value = call.data[ATTR_VALUE]
        entity_id = call.data[ATTR_ENTITY_ID]
        appliance = _get_appliance_by_entity_id(hass, entity_id)
        if appliance is not None:
            await hass.async_add_executor_job(
                getattr(appliance, method),
                key,
                value,
            )

    async def async_service_option_active(call):
        """Service for setting an option for an active program."""
        await _async_service_key_value(call, "set_options_active_program")

    async def async_service_option_selected(call):
        """Service for setting an option for a selected program."""
        await _async_service_key_value(call, "set_options_selected_program")

    async def async_service_pause(call):
        """Service for pausing a program."""
        await _async_service_command(call, BSH_PAUSE)

    async def async_service_resume(call):
        """Service for resuming a paused program."""
        await _async_service_command(call, BSH_RESUME)

    async def async_service_select(call):
        """Service for selecting a program."""
        await _async_service_program(call, SERVICE_SELECT)

    async def async_service_setting(call):
        """Service for changing a setting."""
        await _async_service_key_value(call, "set_setting")

    async def async_service_start(call):
        """Service for starting a program."""
        await _async_service_program(call, SERVICE_START)

    hass.services.async_register(
        DOMAIN,
        SERVICE_OPTION_ACTIVE,
        async_service_option_active,
        schema=SERVICE_SETTING_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_OPTION_SELECTED,
        async_service_option_selected,
        schema=SERVICE_SETTING_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SETTING, async_service_setting, schema=SERVICE_SETTING_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_PAUSE, async_service_pause, schema=SERVICE_COMMAND_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RESUME, async_service_resume, schema=SERVICE_COMMAND_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SELECT, async_service_select, schema=SERVICE_PROGRAM_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_START, async_service_start, schema=SERVICE_PROGRAM_SCHEMA
    )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Home Connect from a config entry."""
    implementation = (
        await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )
    )

    hc_api = api.ConfigEntryAuth(hass, entry, implementation)

    hass.data[DOMAIN][entry.entry_id] = hc_api

    await update_all_devices(hass, entry)

    for component in PLATFORMS:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(entry, component)
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, component)
                for component in PLATFORMS
            ]
        )
    )
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


@Throttle(SCAN_INTERVAL)
async def update_all_devices(hass, entry):
    """Update all the devices."""
    data = hass.data[DOMAIN]
    hc_api = data[entry.entry_id]
    try:
        await hass.async_add_executor_job(hc_api.get_devices)
        for device_dict in hc_api.devices:
            await hass.async_add_executor_job(device_dict["device"].initialize)
    except HTTPError as err:
        _LOGGER.warning("Cannot update devices: %s", err.response.status_code)
