"""The Weatherlink integration."""

from __future__ import annotations

from datetime import timedelta
from email.utils import mktime_tz, parsedate_tz
import logging

from aiohttp import ClientResponseError
import async_timeout

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_API_KEY_V2,
    CONF_API_SECRET,
    CONF_API_TOKEN,
    CONF_API_VERSION,
    CONF_STATION_ID,
    DOMAIN,
    ApiVersion,
    DataKey,
)
from .pyweatherlink import WLHub, WLHubV2

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]
SENSOR_TYPE_VUE_AND_VANTAGE_PRO = (
    23,
    24,
    27,
    28,
    33,
    34,
    37,
    43,
    44,
    45,
    46,
    48,
    49,
    50,
    51,
    76,
    77,
    78,
    79,
    80,
    81,
    82,
    83,
    84,
    85,
    87,
)

SENSOR_TYPE_AIRLINK = (
    323,
    326,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Weatherlink from a config entry."""

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {}
    if entry.data[CONF_API_VERSION] == ApiVersion.API_V1:
        hass.data[DOMAIN][entry.entry_id]["api"] = WLHub(
            websession=async_get_clientsession(hass),
            username=entry.data[CONF_USERNAME],
            password=entry.data[CONF_PASSWORD],
            apitoken=entry.data[CONF_API_TOKEN],
        )
        hass.data[DOMAIN][entry.entry_id]["primary_tx_id"] = 1
        tx_ids = [1]

    if entry.data[CONF_API_VERSION] == ApiVersion.API_V2:
        hass.data[DOMAIN][entry.entry_id]["api"] = WLHubV2(
            websession=async_get_clientsession(hass),
            station_id=entry.data[CONF_STATION_ID],
            api_key_v2=entry.data[CONF_API_KEY_V2],
            api_secret=entry.data[CONF_API_SECRET],
        )
        hass.data[DOMAIN][entry.entry_id]["station_data"] = await hass.data[DOMAIN][
            entry.entry_id
        ]["api"].get_station()

        all_sensors = await hass.data[DOMAIN][entry.entry_id]["api"].get_all_sensors()

        sensors = []
        tx_ids = []
        for sensor in all_sensors["sensors"]:
            if (
                sensor["station_id"]
                == hass.data[DOMAIN][entry.entry_id]["station_data"]["stations"][0][
                    "station_id"
                ]
            ):
                sensors.append(sensor)
                if (
                    sensor["sensor_type"] in SENSOR_TYPE_VUE_AND_VANTAGE_PRO
                    and sensor["tx_id"] is not None
                    and sensor["tx_id"] not in tx_ids
                ):
                    tx_ids.append(sensor["tx_id"])
        hass.data[DOMAIN][entry.entry_id]["sensors_metadata"] = sensors
        # todo Make primary_tx_id configurable by user - perhaps in config flow.
        if len(tx_ids) == 0:
            tx_ids = [1]
        hass.data[DOMAIN][entry.entry_id]["primary_tx_id"] = min(tx_ids)
    _LOGGER.debug("Primary tx_ids: %s", tx_ids)
    coordinator = await get_coordinator(hass, entry)
    if not coordinator.last_update_success:
        await coordinator.async_config_entry_first_refresh()
    _LOGGER.debug("First data: %s", coordinator.data)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


DCO = "davis_current_observation"


async def get_coordinator(  # noqa: C901
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> DataUpdateCoordinator:
    """Get the data update coordinator."""
    if "coordinator" in hass.data[DOMAIN][entry.entry_id]:
        return hass.data[DOMAIN][entry.entry_id]["coordinator"]

    def _preprocess(indata: str):  # noqa: C901
        outdata = {}
        # _LOGGER.debug("Received data: %s", indata)
        if entry.data[CONF_API_VERSION] == ApiVersion.API_V1:
            tx_id = 1
            outdata.setdefault(tx_id, {})
            outdata[tx_id]["DID"] = indata[DCO].get("DID")
            outdata[tx_id]["station_name"] = indata[DCO].get("station_name")
            outdata[tx_id][DataKey.TEMP_OUT] = indata.get("temp_f")
            outdata[tx_id][DataKey.HEAT_INDEX] = indata.get("heat_index_f")
            outdata[tx_id][DataKey.WIND_CHILL] = indata.get("wind_chill_f")
            outdata[tx_id][DataKey.TEMP_IN] = indata[DCO].get("temp_in_f")
            outdata[tx_id][DataKey.HUM_IN] = indata[DCO].get("relative_humidity_in")
            outdata[tx_id][DataKey.HUM_OUT] = indata.get("relative_humidity")
            outdata[tx_id][DataKey.BAR_SEA_LEVEL] = indata.get("pressure_in")
            outdata[tx_id][DataKey.WIND_MPH] = indata.get("wind_mph")
            outdata[tx_id][DataKey.WIND_GUST_MPH] = indata[DCO].get(
                "wind_ten_min_gust_mph"
            )
            outdata[tx_id][DataKey.WIND_DIR] = indata.get("wind_degrees")
            outdata[tx_id][DataKey.DEWPOINT] = indata.get("dewpoint_f")
            outdata[tx_id][DataKey.RAIN_DAY] = indata[DCO].get("rain_day_in")
            outdata[tx_id][DataKey.RAIN_STORM] = indata[DCO].get("rain_storm_in", 0.0)
            outdata[tx_id][DataKey.RAIN_RATE] = indata[DCO].get("rain_rate_in_per_hr")
            outdata[tx_id][DataKey.RAIN_MONTH] = indata[DCO].get("rain_month_in")
            outdata[tx_id][DataKey.RAIN_YEAR] = indata[DCO].get("rain_year_in")
            outdata[tx_id][DataKey.BAR_TREND] = indata[DCO].get(
                "pressure_tendency_string"
            )
            outdata[tx_id][DataKey.SOLAR_RADIATION] = indata[DCO].get("solar_radiation")
            outdata[tx_id][DataKey.UV_INDEX] = indata[DCO].get("uv_index")
            outdata[tx_id][DataKey.ET_DAY] = indata[DCO].get("et_day")
            outdata[tx_id][DataKey.ET_MONTH] = indata[DCO].get("et_month")
            outdata[tx_id][DataKey.ET_YEAR] = indata[DCO].get("et_year")

            outdata[tx_id][DataKey.TIMESTAMP] = mktime_tz(
                parsedate_tz(indata["observation_time_rfc822"])
            )

        if entry.data[CONF_API_VERSION] == ApiVersion.API_V2:
            primary_tx_id = tx_id = hass.data[DOMAIN][entry.entry_id]["primary_tx_id"]
            outdata.setdefault(tx_id, {})
            outdata[DataKey.UUID] = indata["station_id_uuid"]
            for sensor in indata["sensors"]:
                # Vue
                if (
                    sensor["sensor_type"] in SENSOR_TYPE_VUE_AND_VANTAGE_PRO
                    or sensor["sensor_type"] in [55]
                ) and sensor["data_structure_type"] == 10:
                    # _LOGGER.debug("Sensor: %s | %s", sensor["sensor_type"], sensor)
                    tx_id = sensor["data"][0]["tx_id"]
                    outdata.setdefault(tx_id, {})
                    outdata[tx_id][DataKey.SENSOR_TYPE] = sensor["sensor_type"]
                    outdata[tx_id][DataKey.DATA_STRUCTURE] = sensor[
                        "data_structure_type"
                    ]
                    outdata[tx_id][DataKey.TIMESTAMP] = sensor["data"][0]["ts"]
                    outdata[tx_id][DataKey.TEMP_OUT] = sensor["data"][0]["temp"]
                    outdata[tx_id][DataKey.HUM_OUT] = sensor["data"][0]["hum"]
                    outdata[tx_id][DataKey.WIND_MPH] = sensor["data"][0][
                        "wind_speed_last"
                    ]
                    outdata[tx_id][DataKey.WIND_MPH_1M]= sensor["data"][0]["wind_speed_avg_last_1_min"]
                    outdata[tx_id][DataKey.WIND_MPH_2M] = sensor["data"][0]["wind_speed_avg_last_2_min"]
                    outdata[tx_id][DataKey.WIND_MPH_10M] = sensor["data"][0]["wind_speed_avg_last_10_min"]
                    outdata[tx_id][DataKey.WIND_GUST_MPH] = sensor["data"][0][
                        "wind_speed_hi_last_10_min"
                    ]
                    outdata[tx_id][DataKey.WIND_GUST_MPH_2M] = sensor["data"][0]["wind_speed_hi_last_2_min"]
                    outdata[tx_id][DataKey.WIND_DIR] = sensor["data"][0][
                        "wind_dir_last"
                    ]
                    outdata[tx_id][DataKey.WIND_DIR_2M] = sensor["data"][0]["wind_dir_scalar_avg_last_2_min"]
                    outdata[tx_id][DataKey.WIND_DIR_10M] = sensor["data"][0]["wind_dir_scalar_avg_last_10_min"]
                    outdata[tx_id][DataKey.WIND_GUST_DIR] = sensor["data"][0]["wind_dir_at_hi_speed_last_10_min"]
                    outdata[tx_id][DataKey.WIND_GUST_DIR_2M] = sensor["data"][0]["wind_dir_at_hi_speed_last_2_min"]

                    outdata[tx_id][DataKey.DEWPOINT] = sensor["data"][0]["dew_point"]
                    outdata[tx_id][DataKey.HEAT_INDEX] = sensor["data"][0]["heat_index"]
                    outdata[tx_id][DataKey.THW_INDEX] = sensor["data"][0]["thw_index"]
                    outdata[tx_id][DataKey.THSW_INDEX] = sensor["data"][0]["thsw_index"]
                    outdata[tx_id][DataKey.WET_BULB] = sensor["data"][0]["wet_bulb"]
                    outdata[tx_id][DataKey.WIND_CHILL] = sensor["data"][0]["wind_chill"]
                    outdata[tx_id][DataKey.RAIN_DAY] = sensor["data"][0].get(
                        "rainfall_daily_in", 0.0
                    )
                    if (xx := sensor["data"][0].get("rainfall_last_15_min_in", 0.0)) is None:
                        xx = 0.0
                    outdata[tx_id][DataKey.RAIN_DAY_15M] = xx
                    if (xx := sensor["data"][0].get("rainfall_last_60_min_in", 0.0)) is None:
                        xx = 0.0
                    outdata[tx_id][DataKey.RAIN_DAY_60M] = xx
                    if (xx := sensor["data"][0].get("rainfall_last_24_hr_in", 0.0)) is None:
                        xx = 0.0
                    outdata[tx_id][DataKey.RAIN_DAY_24H] = xx

                    if (xx := sensor["data"][0].get("rain_storm_in", 0.0)) is None:
                        xx = 0.0
                    outdata[tx_id][DataKey.RAIN_STORM] = xx
                    outdata[tx_id][DataKey.RAIN_STORM_START] = sensor["data"][0].get(
                        "rain_storm_start_time"
                    )
                    if (xx := sensor["data"][0].get("rain_storm_last_in", 0.0)) is None:
                        xx = 0.0
                    outdata[tx_id][DataKey.RAIN_STORM_LAST] = xx
                    outdata[tx_id][DataKey.RAIN_STORM_LAST_START] = sensor["data"][
                        0
                    ].get("rain_storm_last_start_at")
                    outdata[tx_id][DataKey.RAIN_STORM_LAST_END] = sensor["data"][0].get(
                        "rain_storm_last_end_at"
                    )

                    outdata[tx_id][DataKey.RAIN_RATE] = sensor["data"][0][
                        "rain_rate_last_in"
                    ]
                    outdata[tx_id][DataKey.RAIN_RATE_HI] = sensor["data"][0]["rain_rate_hi_in"]
                    outdata[tx_id][DataKey.RAIN_RATE_HI_15M] = sensor["data"][0]["rain_rate_hi_last_15_min_in"]

                    outdata[tx_id][DataKey.RAIN_MONTH] = sensor["data"][0][
                        "rainfall_monthly_in"
                    ]
                    outdata[tx_id][DataKey.RAIN_YEAR] = sensor["data"][0][
                        "rainfall_year_in"
                    ]
                    outdata[tx_id][DataKey.TRANS_BATTERY_FLAG] = sensor["data"][0][
                        "trans_battery_flag"
                    ]
                    outdata[tx_id][DataKey.UV_INDEX] = sensor["data"][0]["uv_index"]
                    outdata[tx_id][DataKey.SOLAR_RADIATION] = sensor["data"][0][
                        "solar_rad"
                    ]
                    outdata[tx_id][DataKey.ET_DAY] = sensor["data"][0].get("et_day")
                    outdata[tx_id][DataKey.ET_MONTH] = sensor["data"][0].get("et_month")
                    outdata[tx_id][DataKey.ET_YEAR] = sensor["data"][0].get("et_year")

                    outdata[tx_id][DataKey.RX_STATE] = sensor["data"][0].get("rx_state")

                # ----------- Data structure 2
                if (
                    sensor["sensor_type"] in SENSOR_TYPE_VUE_AND_VANTAGE_PRO
                    and sensor["data_structure_type"] == 2
                ):
                    tx_id = sensor["data"][0].get("tx_id", 1)
                    outdata.setdefault(tx_id, {})
                    outdata[tx_id][DataKey.SENSOR_TYPE] = sensor["sensor_type"]
                    outdata[tx_id][DataKey.DATA_STRUCTURE] = sensor[
                        "data_structure_type"
                    ]
                    outdata[tx_id][DataKey.TIMESTAMP] = sensor["data"][0]["ts"]
                    outdata[tx_id][DataKey.TEMP_OUT] = sensor["data"][0]["temp_out"]
                    outdata[tx_id][DataKey.TEMP_IN] = sensor["data"][0]["temp_in"]
                    for numb in range(1, 7 + 1):
                        outdata[tx_id][f"{DataKey.TEMP_EXTRA}_{numb}"] = sensor["data"][
                            0
                        ][f"temp_extra_{numb}"]
                    for numb in range(1, 4 + 1):
                        outdata[tx_id][f"{DataKey.TEMP_LEAF}_{numb}"] = sensor["data"][
                            0
                        ][f"temp_leaf_{numb}"]
                    for numb in range(1, 4 + 1):
                        outdata[tx_id][f"{DataKey.TEMP_SOIL}_{numb}"] = sensor["data"][
                            0
                        ][f"temp_soil_{numb}"]
                    for numb in range(1, 7 + 1):
                        outdata[tx_id][f"{DataKey.HUM_EXTRA}_{numb}"] = sensor["data"][
                            0
                        ][f"hum_extra_{numb}"]
                    for numb in range(1, 4 + 1):
                        outdata[tx_id][f"{DataKey.MOIST_SOIL}_{numb}"] = sensor["data"][
                            0
                        ][f"moist_soil_{numb}"]
                    for numb in range(1, 4 + 1):
                        outdata[tx_id][f"{DataKey.WET_LEAF}_{numb}"] = sensor["data"][
                            0
                        ][f"wet_leaf_{numb}"]
                    outdata[tx_id][DataKey.BAR_SEA_LEVEL] = sensor["data"][0]["bar"]
                    if (xx := sensor["data"][0].get("bar_trend", 0)) is not None:
                        xx = xx / 1000
                    outdata[tx_id][DataKey.BAR_TREND] = xx
                    outdata[tx_id][DataKey.HUM_OUT] = sensor["data"][0]["hum_out"]
                    outdata[tx_id][DataKey.HUM_IN] = sensor["data"][0]["hum_in"]
                    outdata[tx_id][DataKey.WIND_MPH] = sensor["data"][0]["wind_speed"]
                    outdata[tx_id][DataKey.WIND_GUST_MPH] = sensor["data"][0][
                        "wind_gust_10_min"
                    ]
                    outdata[tx_id][DataKey.WIND_DIR] = sensor["data"][0]["wind_dir"]
                    outdata[tx_id][DataKey.DEWPOINT] = sensor["data"][0]["dew_point"]
                    outdata[tx_id][DataKey.HEAT_INDEX] = sensor["data"][0]["heat_index"]
                    outdata[tx_id][DataKey.WIND_CHILL] = sensor["data"][0]["wind_chill"]
                    outdata[tx_id][DataKey.RAIN_DAY] = sensor["data"][0].get(
                        "rain_day_in"
                    )
                    if (xx := sensor["data"][0].get("rain_storm_in", 0.0)) is None:
                        xx = 0.0
                    outdata[tx_id][DataKey.RAIN_STORM] = xx
                    outdata[tx_id][DataKey.RAIN_STORM_START] = sensor["data"][0].get(
                        "rain_storm_start_time"
                    )
                    outdata[tx_id][DataKey.RAIN_RATE] = sensor["data"][0][
                        "rain_rate_in"
                    ]
                    outdata[tx_id][DataKey.RAIN_MONTH] = sensor["data"][0][
                        "rain_month_in"
                    ]
                    outdata[tx_id][DataKey.RAIN_YEAR] = sensor["data"][0][
                        "rain_year_in"
                    ]
                    outdata[tx_id][DataKey.SOLAR_RADIATION] = sensor["data"][0][
                        "solar_rad"
                    ]
                    outdata[tx_id][DataKey.UV_INDEX] = sensor["data"][0]["uv"]
                    outdata[tx_id][DataKey.ET_DAY] = sensor["data"][0]["et_day"]
                    outdata[tx_id][DataKey.ET_MONTH] = sensor["data"][0]["et_month"]
                    outdata[tx_id][DataKey.ET_YEAR] = sensor["data"][0]["et_year"]

                # Console Vantage or VUE:
                if (
                    sensor["sensor_type"] in SENSOR_TYPE_VUE_AND_VANTAGE_PRO
                    or sensor["sensor_type"] in [55]
                ) and sensor["data_structure_type"] == 23:
                    # _LOGGER.debug("Sensor: %s | %s", sensor["sensor_type"], sensor)
                    tx_id = sensor["data"][0]["tx_id"]
                    outdata.setdefault(tx_id, {})
                    outdata[tx_id][DataKey.SENSOR_TYPE] = sensor["sensor_type"]
                    outdata[tx_id][DataKey.DATA_STRUCTURE] = sensor[
                        "data_structure_type"
                    ]
                    outdata[tx_id][DataKey.TIMESTAMP] = sensor["data"][0]["ts"]
                    outdata[tx_id][DataKey.TEMP_OUT] = sensor["data"][0]["temp"]
                    outdata[tx_id][DataKey.HUM_OUT] = sensor["data"][0]["hum"]
                    outdata[tx_id][DataKey.WIND_MPH] = sensor["data"][0][
                        "wind_speed_last"
                    ]
                    if (xx := sensor["data"][0].get("wind_speed_hi_last_10_min", 0.0)) is None:
                        xx = 0.0
                    outdata[tx_id][DataKey.WIND_GUST_MPH] = xx

                    outdata[tx_id][DataKey.WIND_DIR] = sensor["data"][0][
                        "wind_dir_last"
                    ]

                    if (xx := sensor["data"][0].get("wind_speed_avg_last_1_min", 0.0)) is None:
                        xx = 0.0
                    outdata[tx_id][DataKey.WIND_MPH_1M] = xx

                    if (xx := sensor["data"][0].get("wind_speed_avg_last_2_min", 0.0)) is None:
                        xx = 0.0
                    outdata[tx_id][DataKey.WIND_MPH_2M] = xx

                    if (xx := sensor["data"][0].get("wind_speed_avg_last_10_min", 0.0)) is None:
                        xx = 0.0
                    outdata[tx_id][DataKey.WIND_MPH_10M] = xx

                    if (xx := sensor["data"][0].get("wind_speed_hi_last_2_min", 0.0)) is None:
                        xx = 0.0
                    outdata[tx_id][DataKey.WIND_GUST_MPH_2M] = xx

                    if (xx := sensor["data"][0].get("wind_dir_scalar_avg_last_2_min", 0)) is None:
                        xx = 0
                    outdata[tx_id][DataKey.WIND_DIR_2M] = xx
                    if (xx := sensor["data"][0].get("wind_dir_scalar_avg_last_10_min", 0)) is None:
                        xx = 0
                    outdata[tx_id][DataKey.WIND_DIR_10M] = xx

                    if (xx := sensor["data"][0].get("wind_dir_at_hi_speed_last_10_min", 0)) is None:
                        xx = 0
                    outdata[tx_id][DataKey.WIND_GUST_DIR] = xx 

                    if (xx := sensor["data"][0].get("wind_dir_at_hi_speed_last_2_min", 0)) is None:
                        xx = 0
                    outdata[tx_id][DataKey.WIND_GUST_DIR_2M] = xx

                    outdata[tx_id][DataKey.DEWPOINT] = sensor["data"][0]["dew_point"]
                    outdata[tx_id][DataKey.HEAT_INDEX] = sensor["data"][0]["heat_index"]
                    outdata[tx_id][DataKey.THW_INDEX] = sensor["data"][0]["thw_index"]
                    outdata[tx_id][DataKey.THSW_INDEX] = sensor["data"][0]["thsw_index"]
                    outdata[tx_id][DataKey.WET_BULB] = sensor["data"][0]["wet_bulb"]
                    outdata[tx_id][DataKey.WIND_CHILL] = sensor["data"][0]["wind_chill"]

                    outdata[tx_id][DataKey.RAIN_DAY] = sensor["data"][0].get(
                        "rainfall_day_in", 0.0
                    )
                    if (xx := sensor["data"][0].get("rainfall_last_15_min_in", 0.0)) is None:
                        xx = 0.0
                    outdata[tx_id][DataKey.RAIN_DAY_15M] = xx
                    if (xx := sensor["data"][0].get("rainfall_last_60_min_in", 0.0)) is None:
                        xx = 0.0
                    outdata[tx_id][DataKey.RAIN_DAY_60M] = xx
                    if (xx := sensor["data"][0].get("rainfall_last_24_hr_in", 0.0)) is None:
                        xx = 0.0
                    outdata[tx_id][DataKey.RAIN_DAY_24H] = xx

                    if (
                        xx := sensor["data"][0].get("rain_storm_current_in", 0.0)
                    ) is None:
                        xx = 0.0
                    outdata[tx_id][DataKey.RAIN_STORM] = xx
                    outdata[tx_id][DataKey.RAIN_STORM_START] = sensor["data"][0].get(
                        "rain_storm_current_start_at"
                    )
                    if (xx := sensor["data"][0].get("rain_storm_last_in", 0.0)) is None:
                        xx = 0.0
                    outdata[tx_id][DataKey.RAIN_STORM_LAST] = xx
                    outdata[tx_id][DataKey.RAIN_STORM_LAST_START] = sensor["data"][
                        0
                    ].get("rain_storm_last_start_at")
                    outdata[tx_id][DataKey.RAIN_STORM_LAST_END] = sensor["data"][0].get(
                        "rain_storm_last_end_at"
                    )
                    if (xx := sensor["data"][0].get("rain_rate_last_in", 0.0)) is None:
                        xx = 0
                    outdata[tx_id][DataKey.RAIN_RATE] = xx

                    if (xx := sensor["data"][0].get("rain_rate_hi_in", 0.0)) is None:
                        xx = 0
                    outdata[tx_id][DataKey.RAIN_RATE_HI] = xx

                    if (xx := sensor["data"][0].get("rain_rate_hi_last_15_min_in", 0.0)) is None:
                        xx = 0
                    outdata[tx_id][DataKey.RAIN_RATE_HI_15M] = xx

                    if (xx := sensor["data"][0].get("rainfall_month_in", 0.0)) is None:
                        xx = 0
                    outdata[tx_id][DataKey.RAIN_MONTH] = xx

                    if (xx := sensor["data"][0].get("rainfall_year_in", 0.0)) is None:
                        xx = 0
                    outdata[tx_id][DataKey.RAIN_YEAR] = xx

                    if ( xx := sensor["data"][0].get("trans_battery_flag", 0.0)) is not None:
                        outdata[tx_id][DataKey.TRANS_BATTERY_FLAG] = xx

                    if ( xx := sensor["data"][0].get("trans_battery_volt", 0.0)) is not None:
                        outdata[tx_id][DataKey.TRANS_BATTERY_VOLT] = xx

                    if ( xx := sensor["data"][0].get("supercap_volt", 0.0)) is not None:
                        outdata[tx_id][DataKey.SUPERCAP_VOLT] = xx

                    if ( xx := sensor["data"][0].get("solar_panel_volt", 0.0)) is not None:
                        outdata[tx_id][DataKey.SOLAR_PANEL_VOLT] = xx

                    if ( xx := sensor["data"][0].get("solar_rad", 0.0)) is not None:
                        outdata[tx_id][DataKey.SOLAR_RADIATION] = xx

                    if ( xx := sensor["data"][0].get("uv_index", 0.0)) is not None:
                        outdata[tx_id][DataKey.UV_INDEX] = xx

                    if ( xx := sensor["data"][0].get("et_day", 0.0)) is not None:
                        outdata[tx_id][DataKey.ET_DAY] = xx

                    if ( xx := sensor["data"][0].get("et_month", 0.0)) is not None:
                        outdata[tx_id][DataKey.ET_MONTH] = xx

                    if ( xx := sensor["data"][0].get("et_year", 0.0)) is not None:
                        outdata[tx_id][DataKey.ET_YEAR] = xx


                    if ( xx := sensor["data"][0].get("solar_energy_day", 0.0)) is not None:
                        xx = xx * 11.622		#1 Langley  = 11.622 Watt-hours per square meter
                        outdata[tx_id][DataKey.SOLAR_ENERGY] = xx

                    if ( xx := sensor["data"][0].get("hdd_day", 0.0)) is not None:
                        outdata[tx_id][DataKey.HDDF_DAY] = xx
                        #heating degree days in degrees Fahrenheit, to convert to an equivalent Celsius value use C = F x 5 / 9
                        outdata[tx_id][DataKey.HDDC_DAY] = xx = xx * 5 / 9

                    if ( xx := sensor["data"][0].get("cdd_day", 0.0)) is not None:
                        outdata[tx_id][DataKey.CDDF_DAY] = xx
                        outdata[tx_id][DataKey.CDDC_DAY] = xx = xx * 5 / 9

                    if ( xx := sensor["data"][0].get("uv_dose_day", 0.0)) is not None:
                        outdata[tx_id][DataKey.UV_DOSE] = xx

                    if ( xx := sensor["data"][0].get("wind_run_day", 0.0)) is not None:
                        outdata[tx_id][DataKey.WIND_RUN] = xx

                    if ( xx := sensor["data"][0].get("rssi_last", 0.0)) is not None:
                        outdata[tx_id][DataKey.RSSI] = xx

                    if ( xx := sensor["data"][0].get("freq_error_current", 0.0)) is not None:
                        outdata[tx_id][DataKey.FREQ_ERROR] = xx

                    if ( xx := sensor["data"][0].get("packets_missed_day", 0.0)) is not None:
                        outdata[tx_id][DataKey.PACKETS_MISSED] = xx

                    if ( xx := sensor["data"][0].get("packets_received_day", 0.0)) is not None:
                        outdata[tx_id][DataKey.PACKETS_RECEIVED] = xx

                    if ( xx := sensor["data"][0].get("reception_day", 0.0)) is not None:
                        outdata[tx_id][DataKey.RECEPTION] = xx

                    if ( xx := sensor["data"][0].get("resyncs_day", 0.0)) is not None:
                        outdata[tx_id][DataKey.RESYNCS] = xx

                    if ( xx := sensor["data"][0].get("crc_errors_day", 0.0)) is not None:
                        outdata[tx_id][DataKey.CRC_ERRORS] = xx

                    outdata[tx_id][DataKey.RX_STATE] = sensor["data"][0].get("rx_state")


                # Console Leaf or Soil
                if sensor["sensor_type"] == 56 and sensor["data_structure_type"] == 12:
                    tx_id = sensor["data"][0]["tx_id"]
                    outdata.setdefault(tx_id, {})
                    outdata[tx_id][DataKey.SENSOR_TYPE] = sensor["sensor_type"]
                    outdata[tx_id][DataKey.DATA_STRUCTURE] = sensor[
                        "data_structure_type"
                    ]
                    outdata[tx_id][DataKey.TIMESTAMP] = sensor["data"][0]["ts"]
                    for numb in range(1, 4 + 1):
                        outdata[tx_id][f"{DataKey.TEMP}_{numb}"] = sensor["data"][0][
                            f"temp_{numb}"
                        ]
                    for numb in range(1, 4 + 1):
                        outdata[tx_id][f"{DataKey.MOIST_SOIL}_{numb}"] = sensor["data"][
                            0
                        ][f"moist_soil_{numb}"]
                    for numb in range(1, 2 + 1):
                        outdata[tx_id][f"{DataKey.WET_LEAF}_{numb}"] = sensor["data"][
                            0
                        ][f"wet_leaf_{numb}"]
                    outdata[tx_id][DataKey.TRANS_BATTERY_FLAG] = sensor["data"][0][
                        "trans_battery_flag"
                    ]
                    outdata[tx_id][DataKey.RX_STATE] = sensor["data"][0].get("rx_state")

                if sensor["sensor_type"] == 56 and sensor["data_structure_type"] == 25:
                    tx_id = sensor["data"][0]["tx_id"]
                    outdata.setdefault(tx_id, {})
                    outdata[tx_id][DataKey.SENSOR_TYPE] = sensor["sensor_type"]
                    outdata[tx_id][DataKey.DATA_STRUCTURE] = sensor[
                        "data_structure_type"
                    ]
                    outdata[tx_id][DataKey.TIMESTAMP] = sensor["data"][0]["ts"]
                    for numb in range(1, 4 + 1):
                        outdata[tx_id][f"{DataKey.TEMP}_{numb}"] = sensor["data"][0][
                            f"temp_{numb}"
                        ]
                    for numb in range(1, 4 + 1):
                        outdata[tx_id][f"{DataKey.MOIST_SOIL}_{numb}"] = sensor["data"][
                            0
                        ][f"moist_soil_{numb}"]
                    for numb in range(1, 2 + 1):
                        outdata[tx_id][f"{DataKey.WET_LEAF}_{numb}"] = sensor["data"][
                            0
                        ][f"wet_leaf_{numb}"]
                    outdata[tx_id][DataKey.TRANS_BATTERY_FLAG] = sensor["data"][0][
                        "trans_battery_flag"
                    ]
                    outdata[tx_id][DataKey.RX_STATE] = sensor["data"][0].get("rx_state")

                if sensor["sensor_type"] == 365 and sensor["data_structure_type"] == 21:
                    tx_id = primary_tx_id
                    outdata[tx_id][DataKey.TEMP_IN] = sensor["data"][0]["temp_in"]
                    outdata[tx_id][DataKey.HUM_IN] = sensor["data"][0]["hum_in"]
                if sensor["sensor_type"] == 243 and sensor["data_structure_type"] == 12:
                    tx_id = primary_tx_id
                    outdata[tx_id][DataKey.TEMP_IN] = sensor["data"][0]["temp_in"]
                    outdata[tx_id][DataKey.HUM_IN] = sensor["data"][0]["hum_in"]
                if sensor["sensor_type"] == 242 and sensor["data_structure_type"] == 12:
                    tx_id = primary_tx_id
                    outdata[tx_id][DataKey.BAR_SEA_LEVEL] = sensor["data"][0][
                        "bar_sea_level"
                    ]
                    outdata[tx_id][DataKey.BAR_TREND] = sensor["data"][0]["bar_trend"]
                if sensor["sensor_type"] == 242 and sensor["data_structure_type"] == 19:
                    tx_id = primary_tx_id
                    outdata[tx_id][DataKey.BAR_SEA_LEVEL] = sensor["data"][0][
                        "bar_sea_level"
                    ]
                    outdata[tx_id][DataKey.BAR_TREND] = sensor["data"][0]["bar_trend"]

                if (
                    sensor["sensor_type"] in SENSOR_TYPE_AIRLINK
                    and sensor["data_structure_type"] == 16
                ):
                    tx_id = primary_tx_id
                    tx_id = sensor["lsid"]
                    outdata.setdefault(tx_id, {})
                    outdata[tx_id][DataKey.SENSOR_TYPE] = sensor["sensor_type"]
                    outdata[tx_id][DataKey.DATA_STRUCTURE] = sensor[
                        "data_structure_type"
                    ]
                    outdata[tx_id][DataKey.TIMESTAMP] = sensor["data"][0]["ts"]
                    outdata[tx_id][DataKey.TEMP] = sensor["data"][0]["temp"]
                    outdata[tx_id][DataKey.HUM] = sensor["data"][0]["hum"]
                    outdata[tx_id][DataKey.DEWPOINT] = sensor["data"][0]["dew_point"]
                    outdata[tx_id][DataKey.HEAT_INDEX] = sensor["data"][0]["heat_index"]
                    outdata[tx_id][DataKey.WET_BULB] = sensor["data"][0]["wet_bulb"]
                    outdata[tx_id][DataKey.PM_1] = sensor["data"][0]["pm_1"]
                    outdata[tx_id][DataKey.PM_2P5] = sensor["data"][0]["pm_2p5"]
                    outdata[tx_id][DataKey.PM_2P5_24H] = sensor["data"][0][
                        "pm_2p5_24_hour"
                    ]
                    outdata[tx_id][DataKey.PM_2P5_1H] = sensor["data"][0]["pm_2p5_1_hour"]
                    outdata[tx_id][DataKey.PM_2P5_3H] = sensor["data"][0]["pm_2p5_3_hour"]
                    outdata[tx_id][DataKey.PM_2P5_NOWCAST] = sensor["data"][0]["pm_2p5_nowcast"]

                    outdata[tx_id][DataKey.PM_10] = sensor["data"][0]["pm_10"]
                    outdata[tx_id][DataKey.PM_10_24H] = sensor["data"][0][
                        "pm_10_24_hour"
                    ]
                    outdata[tx_id][DataKey.PM_10_1H] = sensor["data"][0]["pm_10_1_hour"]
                    outdata[tx_id][DataKey.PM_10_3H] = sensor["data"][0]["pm_10_3_hour"]
                    outdata[tx_id][DataKey.PM_10_NOWCAST] = sensor["data"][0]["pm_10_nowcast"]
                    outdata[tx_id][DataKey.PM_PCT_DATA] = sensor["data"][0]["pct_pm_data_nowcast"]
                    outdata[tx_id][DataKey.PM_PCT_DATA_1H] = sensor["data"][0]["pct_pm_data_1_hour"]
                    outdata[tx_id][DataKey.PM_PCT_DATA_3H] = sensor["data"][0]["pct_pm_data_3_hour"]
                    outdata[tx_id][DataKey.PM_PCT_DATA_24H] = sensor["data"][0]["pct_pm_data_24_hour"]
                    #outdata[tx_id][DataKey.PM_PCT_DATA_NOWCAST] = sensor["data"][0]["pct_pm_data_nowcast"]

                    outdata[tx_id][DataKey.AQI_VAL] = sensor["data"][0]["aqi_val"]
                    outdata[tx_id][DataKey.AQI_NOWCAST_VAL] = sensor["data"][0][
                        "aqi_nowcast_val"
                    ]

            # Test data can be injected here

            # tx_id = primary_tx_id
            # outdata[tx_id][DataKey.PM_1] = 10
            # outdata[tx_id][DataKey.PM_2P5] = 20
            # outdata[tx_id][DataKey.PM_10] = 50
            # outdata[tx_id][DataKey.AQI_VAL] = 101
            # outdata[tx_id][DataKey.AQI_NOWCAST_VAL] = 102

        return outdata

    async def async_fetch():
        api = hass.data[DOMAIN][entry.entry_id]["api"]
        try:
            async with async_timeout.timeout(10):
                res = await api.request("GET")
                json_data = await res.json()
                hass.data[DOMAIN][entry.entry_id]["current"] = json_data
                return _preprocess(json_data)
        except ClientResponseError as exc:
            _LOGGER.warning("API fetch failed. Status: %s, - %s", exc.code, exc.message)
            raise UpdateFailed(exc) from exc

    hass.data[DOMAIN][entry.entry_id]["coordinator"] = DataUpdateCoordinator(
        hass,
        logging.getLogger(__name__),
        name=DOMAIN,
        update_method=async_fetch,
        update_interval=timedelta(minutes=5),
    )
    await hass.data[DOMAIN][entry.entry_id]["coordinator"].async_refresh()
    return hass.data[DOMAIN][entry.entry_id]["coordinator"]


async def async_migrate_entry(hass, config_entry: ConfigEntry):
    """Migrate old entry."""
    _LOGGER.info("Migrating from version %s", config_entry.version)

    if config_entry.version == 1:
        new_data = {**config_entry.data}

        new_data[CONF_API_VERSION] = ApiVersion.API_V1

        config_entry.version = 2
        hass.config_entries.async_update_entry(config_entry, data=new_data)

    _LOGGER.info("Migration to version %s successful", config_entry.version)

    return True
