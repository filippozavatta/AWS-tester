from greenlight_checker import analyze

import pandas as pd

def main():

    inizio = pd.Timestamp("2026-09-28 11:00")
    fine = pd.Timestamp("2026-10-28 13:00")
    passo = pd.Timedelta("5min")     # distanza tra un "da" e il successivo
    durata = pd.Timedelta("10min")    # lunghezza di ogni analisi

    for da in pd.date_range(inizio, fine - durata, freq=passo):
        a = da + durata
        analyze(da, a)


if __name__=="__main__":
    main()