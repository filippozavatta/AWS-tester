from datetime import datetime, timedelta
from isaac_s3_reader import S3Reader
from dotenv import load_dotenv
import os
from time import time
import numpy as np
import json
import logging


class WindowFilter(logging.Filter):
    """Inietta in ogni record la finestra temporale dei dati analizzati."""
    window = "-"

    def filter(self, record):
        record.window = self.window
        return True


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(window)s] %(levelname)-7s %(message)s",
    handlers=[
        logging.FileHandler("greenlight_checker.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
for _h in logging.getLogger().handlers:
    _h.addFilter(WindowFilter())

log = logging.getLogger(__name__)


def append_jsonl(path, record):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        fh.flush()

N_MACHINES = 15
FS = 50
FS_TOLERANCE = 0.01

N_EXPECTED_OBSERVATIONS_BATTERIES = 16

with open("range_fisici.json", encoding="utf-8") as fh:
    range_fisici = json.load(fh)

load_dotenv()


def unpack_sensors(group):
    """{sensor_id: {channel: value}} -> [value, ...]"""
    return [v for channels in (group or {}).values() for v in channels.values()]


def counts_to_dict(values, counts, fmt=lambda v: str(int(v))):
    """np.unique_counts -> dict serializzabile in JSON (chiavi str, valori int)"""
    return {fmt(v): int(c) for v, c in zip(values, counts)}


def main():

    not_passed = False

    reader = S3Reader(
        env="internal",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY")
    )

    from_dt = datetime.now() - timedelta(minutes=120)-timedelta(minutes=15)
    to_dt = datetime.now() - timedelta(minutes=120)-timedelta(minutes=5)

    WindowFilter.window = f"{from_dt:%Y-%m-%d %H:%M:%S} -> {to_dt:%H:%M:%S}"

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

    batteries = reader.read_batteries(
        building_id="LAB_EP20",
        from_dt=from_dt,
        to_dt=to_dt
    )

    record = {
        "executed_at": datetime.now().isoformat(),
        "window_from": from_dt.isoformat(),
        "window_to": to_dt.isoformat(),
        "status": None,
        "machines": {},
        "sensors": {},
    }

    # MACHINES
    machines = sorted(machines, key=lambda m: (m.plc_time, m.machine))

    machines_ranges = range_fisici.get("diagnostic_machines")

    log.info("===== Controlli tabella MACHINES =====")
    out = False
    counter = []
    datetimes_machine_0 = []

    n_values = 0

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
                n_values += 1

            if hi is not None and value > hi:
                log.info(f"[{m.plc_time}] [machine {m.machine}] Valore fuori range fisico: {field} = {value} (massimo ammesso {hi})")
                not_passed = True
                n_values += 1

    record['machines']['rows'] = len(machines)
    record['machines']['check_ranges'] = (n_values == 0)
    record['machines']['n_values_oor'] = n_values

        # buchi counter

    gaps_counter = np.diff(counter)

    gaps_count_values = np.unique_counts(gaps_counter)

    record['machines']['counter_gaps'] = counts_to_dict(
        gaps_count_values.values, gaps_count_values.counts
    )

    for value, count in zip(gaps_count_values.values, gaps_count_values.counts):
        log.info(f"Gap counter = {value}: {count} occorrenze")

    if not np.all(gaps_counter == 1):
        log.info("Rilevati buchi nel counter delle macchine. Controllo NON superato \u274c")
        for i, gap in enumerate(gaps_counter):
            if gap != 1:
                m = machines[i+1]
                log.info(f"[{m.plc_time}] [machine {m.machine}] Salto nel counter: "
                         f"{counter[i]} -> {counter[i+1]} (gap = {gap})")
        not_passed = True
        record['machines']['check_counter'] = False
    else:
        log.info("Nessun buco nel counter delle macchine. Controllo superato \u2705")
        record['machines']['check_counter'] = True

        # fs th VS fs reale (timestamps)

    gaps_time = np.array([d.total_seconds() for d in np.diff(datetimes_machine_0)])

    ideal_count = round((datetimes_machine_0[-1] - datetimes_machine_0[0]).total_seconds()*FS)

    gaps_count_values = np.unique_counts(np.round(gaps_time, 6))

    record['machines']['time_gaps_ms'] = counts_to_dict(
        gaps_count_values.values, gaps_count_values.counts, fmt=lambda v: f"{v*1000:.3f}"
    )

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

    record['machines']['media_hz'] = float(media_hz)
    record['machines']['n_timestamps'] = len(gaps_time)
    record['machines']['n_timestamps_expected'] = ideal_count
    record['machines']['check_timestamp'] = out

    if len(gaps_time) == ideal_count and not out:

        if abs(media_hz - FS) < FS_TOLERANCE:

            log.info(f"Frequenza di campionamento media: {media_hz:.4f} Hz, numero di timestamp corretto. "
                     f"Controllo superato: {FS-FS_TOLERANCE} < {media_hz:.4f} < {FS+FS_TOLERANCE} \u2705")
            record['machines']['check_timestamp'] = True

        else:

            log.info(f"Frequenza di campionamento media fuori tolleranza: {media_hz:.4f} Hz "
                     f"(attesa {FS} \u00b1 {FS_TOLERANCE} Hz). Controllo NON superato \u274c")
            not_passed = True
            record['machines']['check_timestamp'] = False

    elif not out:

        log.info(f"Numero di timestamp non corretto: {len(gaps_time)} rilevati, {ideal_count} attesi. "
                 f"Controllo NON superato \u274c")
        not_passed = True
        record['machines']['check_timestamp'] = False


    # SENSORS
    sensors = sorted(sensors, key=lambda m: (m.plc_time))

    sensors_ranges = range_fisici.get("sensors")

    log.info("===== Controlli tabella SENSORS =====")

    counter_sensor = []
    datetimes_sensors = []

    n_values_sensor = 0

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
                    n_values_sensor += 1
                if hi is not None and value > hi:
                    log.info(f"[{sensor.plc_time}] Valore sensore fuori range fisico: {value} (massimo ammesso {hi})")
                    not_passed = True
                    n_values_sensor += 1

    record['sensors']['rows'] = len(sensors)
    record['sensors']['check_ranges'] = (n_values_sensor == 0)
    record['sensors']['n_values_oor'] = n_values_sensor

    # buchi counter
    gaps_counter_sensor = np.diff(counter_sensor)

    gaps_count_values = np.unique_counts(gaps_counter_sensor)

    record['sensors']['counter_gaps'] = counts_to_dict(
        gaps_count_values.values, gaps_count_values.counts
    )

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
        record['sensors']['check_counter'] = False
    else:
        log.info("Nessun buco nel counter dei sensori. Controllo superato \u2705")
        record['sensors']['check_counter'] = True

    # fs th VS fs reale (timestamps)
    out_sensor = False

    gaps_time_sensor = np.array([d.total_seconds() for d in np.diff(datetimes_sensors)])

    ideal_count_sensor = round((datetimes_sensors[-1] - datetimes_sensors[0]).total_seconds()*FS)

    gaps_count_values = np.unique_counts(np.round(gaps_time_sensor, 6))

    record['sensors']['time_gaps_ms'] = counts_to_dict(
        gaps_count_values.values, gaps_count_values.counts, fmt=lambda v: f"{v*1000:.3f}"
    )

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

    record['sensors']['media_hz'] = float(media_hz_sensor)
    record['sensors']['n_timestamps'] = len(gaps_time_sensor)
    record['sensors']['n_timestamps_expected'] = ideal_count_sensor
    record['sensors']['check_timestamp'] = out_sensor

    if len(gaps_time_sensor) == ideal_count_sensor and not out_sensor:

        if abs(media_hz_sensor - FS) < FS_TOLERANCE:

            log.info(f"Frequenza di campionamento media sensori: {media_hz_sensor:.4f} Hz, numero di timestamp corretto. "
                     f"Controllo superato: {FS-FS_TOLERANCE} < {media_hz_sensor:.4f} < {FS+FS_TOLERANCE} \u2705")
            record['sensors']['check_timestamp'] = True

        else:

            log.info(f"Frequenza di campionamento media sensori fuori tolleranza: {media_hz_sensor:.4f} Hz "
                     f"(attesa {FS} \u00b1 {FS_TOLERANCE} Hz). Controllo NON superato \u274c")
            not_passed = True
            record['sensors']['check_timestamp'] = False

    elif not out_sensor:

        log.info(f"Numero di timestamp sensori non corretto: {len(gaps_time_sensor)} rilevati, "
                 f"{ideal_count_sensor} attesi. Controllo NON superato \u274c")
        not_passed = True
        record['sensors']['check_timestamp'] = False


    # BATTERIES

    batteries_ranges = range_fisici['batteries']

    log.info("===== Controlli tabella BATTERIES =====")

    n_values_batt = 0
    not_passed_batt = False
    check_rows = len(batteries) >= N_EXPECTED_OBSERVATIONS_BATTERIES
    check_arrays = True

    if not check_rows:
        log.info(f"Righe batterie insufficienti: {len(batteries)} rilevate, "
                 f"{N_EXPECTED_OBSERVATIONS_BATTERIES} attese. Controllo NON superato \u274c")
        not_passed_batt = True

    for b in batteries:

        arrays_full = 0

        for field, array in b:

            if not isinstance(array, (list, tuple, np.ndarray)):
                continue

            if len(array) == 0:
                continue

            arrays_full += 1

            lo = batteries_ranges.get(field + "_min")
            hi = batteries_ranges.get(field + "_max")

            for value in array:

                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    continue

                if lo is not None and value < lo:
                    log.info(f"[{b.plc_time}] Valore batteria fuori range fisico: {field} = {value} (minimo ammesso {lo})")
                    not_passed_batt = True
                    n_values_batt += 1

                if hi is not None and value > hi:
                    log.info(f"[{b.plc_time}] Valore batteria fuori range fisico: {field} = {value} (massimo ammesso {hi})")
                    not_passed_batt = True
                    n_values_batt += 1

        if arrays_full != 1:
            log.info(f"[{b.plc_time}] {arrays_full} array pieni nella riga batterie "
                     f"(atteso 1, non coerente con logica PLC). Controllo NON superato \u274c")
            not_passed_batt = True
            check_arrays = False

    if n_values_batt == 0:
        log.info("Tutti i valori delle batterie nei range fisici. Controllo superato \u2705")
    else:
        log.info(f"{n_values_batt} valori delle batterie fuori range. Controllo NON superato \u274c")

    record['batteries'] = {
        'rows': len(batteries),
        'check_rows': check_rows,
        'check_arrays': check_arrays,
        'check_ranges': (n_values_batt == 0),
        'n_values_oor': n_values_batt,
        'status': "fail" if not_passed_batt else "ok",
    }


    # WARNINGS AND ERRORS

    record['warnings'] = []
    record['errors'] = []

    for warning in warnings:

        record['warnings'].append(warning.__dict__())

    for error in errors:

        record['errors'].append(error.__dict__())


    # scrittura record
    record['status'] = "fail" if not_passed else "ok"

    append_jsonl("greenlight_records.jsonl", record)

    log.info(f"Esito complessivo del run: {record['status'].upper()}")
    log.info(f"Esito batterie: {record['batteries']['status'].upper()}")


if __name__ == "__main__":
    main()