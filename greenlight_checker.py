from datetime import datetime
from isaac_s3_reader import S3Reader
from dotenv import load_dotenv
import os
from time import time
import numpy as np
import json
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    handlers=[
        logging.FileHandler("greenlight_checker.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

N_MACHINES = 15
FS = 50
FS_TOLERANCE = 0.01

with open("range_fisici.json", encoding="utf-8") as fh:
    range_fisici = json.load(fh)

load_dotenv()

def unpack_sensors(group):
    """{sensor_id: {channel: value}} -> [value, ...]"""
    return [v for channels in (group or {}).values() for v in channels.values()]



def main():

    not_passed=False

    reader = S3Reader(
        env="internal",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY")
    )

    from_dt = datetime(2026, 9,17,9,10)
    to_dt = datetime(2026, 9, 17,9,20)

    machines = reader.read_diagnostic_machines(
        building_id="LAB_EP20",
        from_dt=from_dt,
        to_dt=to_dt,
    )

    sensors = reader.read_sensors(
        building_id="LAB_EP20",
        from_dt=from_dt,
        to_dt=to_dt,
    )

    errors = reader.read_errors(
        building_id="LAB_EP20",
        from_dt=from_dt,
        to_dt=to_dt,
    )

    warnings = reader.read_warnings(
        building_id="LAB_EP20",
        from_dt=from_dt,
        to_dt=to_dt,
    )

    # MACHINES
    machines = sorted(machines, key=lambda m: (m.plc_time, m.machine))

    machines_ranges = range_fisici.get("diagnostic_machines")

    log.info("===== Controlli tabella MACHINES =====")
    out = False
    counter = []
    datetimes_machine_0 = []
    for m in machines:
        counter.append(m.counter)
        if m.machine == 0:
            datetimes_machine_0.append(m.plc_time)

        # check valori nei loro range fisici

        for field, value in m:

            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue

            lo = machines_ranges.get(field + "_min")
            hi = machines_ranges.get(field + "_max")

            if lo is not None and value < lo:
                log.info(f"[{m.plc_time}] [machine {m.machine}] Valore fuori range fisico: {field} = {value} (minimo ammesso {lo})")
                not_passed = True

            if hi is not None and value > hi:
                log.info(f"[{m.plc_time}] [machine {m.machine}] Valore fuori range fisico: {field} = {value} (massimo ammesso {hi})")
                not_passed = True


        # buchi counter

    gaps_counter = np.diff(counter)

    gaps_count_values = np.unique_counts(gaps_counter)

    for value,count in zip(gaps_count_values.values,gaps_count_values.counts):
        log.info(f"Gap counter = {value}: {count} occorrenze")

    if not np.all(gaps_counter==1):
        log.info("Rilevati buchi nel counter delle macchine. Controllo NON superato \u274c")
        for i, gap in enumerate(gaps_counter):
            if gap != 1:
                m = machines[i+1]
                log.info(f"[{m.plc_time}] [machine {m.machine}] Salto nel counter: "
                         f"{counter[i]} -> {counter[i+1]} (gap = {gap})")
        not_passed=True
    else:
        log.info("Nessun buco nel counter delle macchine. Controllo superato \u2705")


        # fs th VS fs reale (timestamps)
    
    gaps_time = np.array([d.total_seconds() for d in np.diff(datetimes_machine_0)])

    ideal_count = round((datetimes_machine_0[-1] - datetimes_machine_0[0]).total_seconds()*FS)

    gaps_count_values = np.unique_counts(np.round(gaps_time,6))

    for value, count in zip(gaps_count_values.values, gaps_count_values.counts):
        log.info(f"Gap temporale di {value * 1000:.3f} ms: {count} occorrenze")

    for i, gap in enumerate(gaps_time):
        if abs(gap - 1/FS) > 1e-6:
            log.info(f"[{datetimes_machine_0[i+1]}] Gap temporale anomalo: {gap*1000:.3f} ms "
                     f"(atteso {1000/FS:.3f} ms)")

    if np.allclose(gaps_time, 1/FS, atol=1e-6):
        log.info("Timestamp delle macchine perfettamente regolari. Controllo superato \u2705")
        out = True

    media_hz = 1/np.mean(gaps_time)

    if len(gaps_time) == ideal_count and not out:

        if abs(media_hz-FS)<FS_TOLERANCE:

            log.info(f"Frequenza di campionamento media: {media_hz:.4f} Hz, numero di timestamp corretto. "
                     f"Controllo superato: {FS-FS_TOLERANCE} < {media_hz:.4f} < {FS+FS_TOLERANCE} \u2705")

        else:

            log.info(f"Frequenza di campionamento media fuori tolleranza: {media_hz:.4f} Hz "
                     f"(attesa {FS} \u00b1 {FS_TOLERANCE} Hz). Controllo NON superato \u274c")
            not_passed=True

    elif not out:

        log.info(f"Numero di timestamp non corretto: {len(gaps_time)} rilevati, {ideal_count} attesi. "
                 f"Controllo NON superato \u274c")
        not_passed=True



    #SENSORS
    sensors = sorted(sensors, key=lambda m: (m.plc_time))

    sensors_ranges = range_fisici.get("sensors")

    log.info("===== Controlli tabella SENSORS =====")

    counter_sensor = []
    datetimes_sensors=[]

    for sensor in sensors:

        counter_sensor.append(sensor.counter)
        datetimes_sensors.append(sensor.plc_time)

        lo = sensors_ranges.get("min_sensor")
        hi = sensors_ranges.get("max_sensor")

        for group in (sensor.ground, sensor.roof, sensor.mon, sensor.wind):
            for value in unpack_sensors(group):

                if lo is not None and value < lo:
                    log.info(f"[{sensor.plc_time}] Valore sensore fuori range fisico: {value} (minimo ammesso {lo})")
                    not_passed = True
                if hi is not None and value > hi:
                    log.info(f"[{sensor.plc_time}] Valore sensore fuori range fisico: {value} (massimo ammesso {hi})")
                    not_passed = True

    # buchi counter
    gaps_counter_sensor = np.diff(counter_sensor)

    gaps_count_values = np.unique_counts(gaps_counter_sensor)

    for value, count in zip(gaps_count_values.values, gaps_count_values.counts):
        log.info(f"Gap counter sensori = {value}: {count} occorrenze")

    if not np.all(gaps_counter_sensor == 1):
        log.info("Rilevati buchi nel counter dei sensori. Controllo NON superato \u274c")
        for i, gap in enumerate(gaps_counter_sensor):
            if gap != 1:
                s = sensors[i+1]
                log.info(f"[{s.plc_time}] Salto nel counter sensori: "
                         f"{counter_sensor[i]} -> {counter_sensor[i+1]} (gap = {gap})")
        not_passed = True
    else:
        log.info("Nessun buco nel counter dei sensori. Controllo superato \u2705")

    # fs th VS fs reale (timestamps)
    out_sensor = False

    gaps_time_sensor = np.array([d.total_seconds() for d in np.diff(datetimes_sensors)])

    ideal_count_sensor = round((datetimes_sensors[-1] - datetimes_sensors[0]).total_seconds()*FS)

    gaps_count_values = np.unique_counts(np.round(gaps_time_sensor, 6))

    for value, count in zip(gaps_count_values.values, gaps_count_values.counts):
        log.info(f"Gap temporale sensori di {value * 1000:.3f} ms: {count} occorrenze")

    for i, gap in enumerate(gaps_time_sensor):
        if abs(gap - 1/FS) > 1e-6:
            log.info(f"[{datetimes_sensors[i+1]}] Gap temporale sensori anomalo: {gap*1000:.3f} ms "
                     f"(atteso {1000/FS:.3f} ms)")

    if np.allclose(gaps_time_sensor, 1/FS, atol=1e-6):
        log.info("Timestamp dei sensori perfettamente regolari. Controllo superato \u2705")
        out_sensor = True

    media_hz_sensor = 1/np.mean(gaps_time_sensor)

    if len(gaps_time_sensor) == ideal_count_sensor and not out_sensor:

        if abs(media_hz_sensor - FS) < FS_TOLERANCE:

            log.info(f"Frequenza di campionamento media sensori: {media_hz_sensor:.4f} Hz, numero di timestamp corretto. "
                     f"Controllo superato: {FS-FS_TOLERANCE} < {media_hz_sensor:.4f} < {FS+FS_TOLERANCE} \u2705")

        else:

            log.info(f"Frequenza di campionamento media sensori fuori tolleranza: {media_hz_sensor:.4f} Hz "
                     f"(attesa {FS} \u00b1 {FS_TOLERANCE} Hz). Controllo NON superato \u274c")
            not_passed = True

    elif not out_sensor:

        log.info(f"Numero di timestamp sensori non corretto: {len(gaps_time_sensor)} rilevati, "
                 f"{ideal_count_sensor} attesi. Controllo NON superato \u274c")
        not_passed = True
    
if __name__=="__main__":
    main()