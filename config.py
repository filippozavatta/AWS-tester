import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# configuration.json (locale, non committato) ha la precedenza su configuration_example.json
CONFIG_PATH = os.getenv("GREENLIGHT_CONFIG") or next(
    (p for p in (os.path.join(BASE_DIR, "configuration.json"),
                 os.path.join(BASE_DIR, "configuration_example.json")) if os.path.exists(p)),
    None,
)

if CONFIG_PATH is None:
    raise FileNotFoundError("Nessun file di configurazione trovato (configuration.json / configuration_example.json)")

with open(CONFIG_PATH, encoding="utf-8") as fh:
    CONFIG = json.load(fh)


def path(key):
    """Restituisce il path configurato in CONFIG['paths'][key]; i path relativi sono risolti rispetto alla cartella del config."""
    return os.path.join(os.path.dirname(os.path.abspath(CONFIG_PATH)), CONFIG["paths"][key])
