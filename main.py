from greenlight_checker import analyze, log
from config import CONFIG

import time
import pandas as pd


def run_window(da, a, plants):
    for plant in plants:
        try:
            analyze(da, a, plant)
        except Exception:
            # un impianto senza dati o con errori non deve fermare gli altri
            log.exception(f"[{plant}] Analisi fallita per la finestra {da} -> {a}")


def main():

    plants = CONFIG["plants"]
    analysis = CONFIG["analysis"]

    passo = pd.Timedelta(analysis["step"])      # distanza tra un "da" e il successivo
    durata = pd.Timedelta(analysis["window"])   # lunghezza di ogni analisi

    if analysis["mode"] == "interval":

        inizio = pd.Timestamp(analysis["interval_start"])
        fine = pd.Timestamp(analysis["interval_end"])

        for da in pd.date_range(inizio, fine - durata, freq=passo):
            run_window(da, da + durata, plants)

    elif analysis["mode"] == "realtime":

        # ritardo rispetto ad adesso, per dare tempo ai dati di arrivare su S3
        ritardo = pd.Timedelta(analysis.get("realtime_delay", "0min"))
        now = lambda: pd.Timestamp.now(tz="UTC").tz_localize(None) if analysis.get("realtime_utc") else pd.Timestamp.now()

        # finestre allineate al passo: a = ultimo multiplo di "passo" disponibile
        a = (now() - ritardo).floor(passo)

        while True:
            attesa = (a + ritardo - now()).total_seconds()
            if attesa > 0:
                time.sleep(attesa)
            run_window(a - durata, a, plants)
            a += passo

    else:
        raise ValueError(f"analysis.mode non valido: {analysis['mode']!r} (usare 'realtime' o 'interval')")


if __name__=="__main__":
    main()
