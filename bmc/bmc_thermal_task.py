"""
Bare-metal telemetry: parse a Redfish Thermal payload.

Real BMCs expose a `Thermal` resource (Redfish's
`/redfish/v1/Chassis/{id}/Thermal`) listing every temperature and fan
sensor on the box. `redfish_thermal_sample.json` next to this file is a
trimmed, realistic example of that payload -- the same shape a
redfish_exporter would poll on a schedule, out-of-band, with no agent
running on the host OS.

TODO: implement parse_redfish_thermal() so it returns one dict per
temperature sensor, shaped like:

    {
        "sensor": <Name>,
        "reading_celsius": <ReadingCelsius>,
        "critical_threshold": <UpperThresholdCritical>,
        "over_critical_threshold": <bool -- reading >= threshold>,
        "health": <Status.Health>,
    }

This is the shape you'd hand to a Prometheus gauge per sensor, with
`over_critical_threshold` being exactly what a
"critical above vendor UpperThresholdCritical" alert rule would fire on.

Run this file directly to see your output against the sample payload.
"""

import json
from pathlib import Path


def parse_redfish_thermal(payload: dict) -> list[dict]:
    readings = []
    for sensor in payload["Temperatures"]:
        reading = sensor["ReadingCelsius"]
        threshold = sensor["UpperThresholdCritical"]
        readings.append({
            "sensor": sensor["Name"],
            "reading_celsius": reading,
            "critical_threshold": threshold,
            "over_critical_threshold": reading >= threshold,
            "health": sensor["Status"]["Health"],
        })
    return readings


if __name__ == "__main__":
    sample = json.loads((Path(__file__).parent / "redfish_thermal_sample.json").read_text())
    for reading in parse_redfish_thermal(sample):
        print(reading)