import argparse
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from api.dictionaries import RunSuiteRequest, PushDnsServersRequest
from engine.runner import Runner
from engine.registry import registry
from engine.DefaultTestPolicy import DefaultTestPolicy
from engine.evaluator import Evaluator
from engine.TestResultBuilder import TestResultBuilder

# Crea la cartella dei log se non esiste
os.makedirs("logs", exist_ok=True)

# 1. CENTRALIZZAZIONE DEL LOGGING DI SISTEMA (Console + File)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        # Scrive i log fisicamente in questo file
        logging.FileHandler("logs/engine.log", encoding="utf-8"),
        # Continua a stamparli anche nel terminale
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("api_gateway")

# Carica le variabili d'ambiente da .env (credenziali dei test, mai in config.yml)
load_dotenv()

_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


def _substitute_env_vars(value):
    """Sostituisce ricorsivamente i placeholder ${VAR_NAME} con le variabili d'ambiente."""
    if isinstance(value, str):
        def _replace(match):
            var_name = match.group(1)
            env_value = os.environ.get(var_name)
            if env_value is None:
                raise RuntimeError(f"Variabile d'ambiente richiesta ma non impostata: {var_name}")
            return env_value

        return _ENV_VAR_PATTERN.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _substitute_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env_vars(v) for v in value]
    return value


app = FastAPI(title="DNS Enterprise Testing Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


try:
    with open("config.yml") as f:
        CONFIG = yaml.safe_load(f)
    CONFIG = _substitute_env_vars(CONFIG)
    logger.info("Configurazione dei test YAML caricata correttamente.")
except Exception as e:
    logger.critical(f"Impossibile caricare config/tests.yml: {e}")
    CONFIG = {}

# logic injection
policy = DefaultTestPolicy(evaluator=Evaluator(), builder=TestResultBuilder())
runner = Runner(registry=registry, policy=policy, max_concurrent_tests=50)

# =================================================================
#   LISTA NODI DNS (ricevuta dalla macchina Ansible, letta dal Frontend)
# =================================================================
DNS_SERVERS_FILE = os.path.join("data", "dns_servers.json")
DNS_SERVERS_STATE = {"updated_at": None, "servers": []}

if os.path.exists(DNS_SERVERS_FILE):
    try:
        with open(DNS_SERVERS_FILE, encoding="utf-8") as f:
            DNS_SERVERS_STATE.update(json.load(f))
        logger.info(f"Lista DNS server ripristinata da disco ({len(DNS_SERVERS_STATE['servers'])} nodi).")
    except Exception as e:
        logger.warning(f"Impossibile leggere {DNS_SERVERS_FILE}: {e}")

from fastapi import Query

"""
    Endpoint utilizzato per i comandi diretti a riga di comando
"""
# @app.get("/run")
# async def run_suite_via_get(
#         suite: str = Query("", description="La suite da eseguire (es. from_ipv4, from_ipv6, all)"),
#         dns4: Optional[str] = Query(None, alias="dns4_server", description="Indirizzo IPv4 del DNS"),
#         dns6: Optional[str] = Query(None, alias="dns6_server", description="Indirizzo IPv6 del DNS")
# ):
#
#     # Incapsuliamo i parametri nel modello Pydantic esistente
#     request_data = RunSuiteRequest(
#         suite=suite,
#         dns4_server=dns4 if dns4 else "",
#         dns6_server=dns6
#     )
#
#     # Chiamiamo direttamente la funzione POST riutilizzando tutta la logica asincrona multi-stack
#     return await run_suite(request_data)

# API per il check di connessione frontend.

@app.get("/api/health")
async def health_check():
    return {"status": "ok", "message": "Backend connesso e raggiungibile"}


@app.post("/api/internal/dns-servers")
async def push_dns_servers(request: PushDnsServersRequest, x_internal_token: Optional[str] = Header(None)):
    """
    Chiamato SOLO dalla macchina Ansible (sync-dns-servers.yml) dopo ogni
    refresh dell'inventario dinamico. Non e' pensato per essere raggiungibile
    dal Frontend: richiede il segreto condiviso INVENTORY_PUSH_TOKEN.
    """
    expected_token = os.environ.get("INVENTORY_PUSH_TOKEN")
    if not expected_token or x_internal_token != expected_token:
        raise HTTPException(status_code=401, detail="Token interno mancante o non valido.")

    DNS_SERVERS_STATE["servers"] = [s.model_dump() for s in request.servers]
    DNS_SERVERS_STATE["updated_at"] = datetime.now(timezone.utc).isoformat()

    os.makedirs("data", exist_ok=True)
    with open(DNS_SERVERS_FILE, "w", encoding="utf-8") as f:
        json.dump(DNS_SERVERS_STATE, f)

    logger.info(f"Lista DNS server aggiornata dalla macchina Ansible: {len(DNS_SERVERS_STATE['servers'])} nodi.")
    return {
        "status": "ok",
        "count": len(DNS_SERVERS_STATE["servers"]),
        "updated_at": DNS_SERVERS_STATE["updated_at"],
    }


@app.get("/api/dns-servers")
async def get_dns_servers(continent: Optional[str] = None, country: Optional[str] = None):
    """Letto dal Frontend per popolare la select e i filtri per continente/paese."""
    servers = DNS_SERVERS_STATE["servers"]

    if continent:
        servers = [s for s in servers if (s.get("continent") or "").lower() == continent.lower()]
    if country:
        servers = [s for s in servers if (s.get("country") or "").lower() == country.lower()]

    return {"updated_at": DNS_SERVERS_STATE["updated_at"], "servers": servers}


@app.post("/api/run-suite")
async def run_suite(request: RunSuiteRequest):
    if not CONFIG:
        raise HTTPException(status_code=500, detail="Configurazione assente.")

    tasks = []

    esegui_ipv4 = request.suite == "from_ipv4" or (
                request.suite not in ["from_ipv4", "from_ipv6"] and bool(request.dns4_server))
    esegui_ipv6 = request.suite == "from_ipv6" or (
                request.suite not in ["from_ipv4", "from_ipv6"] and bool(request.dns6_server))

    # Validazione di sicurezza preventiva
    if esegui_ipv4 and not request.dns4_server:
        raise HTTPException(status_code=400,
                            detail="È stata richiesta o rilevata l'esecuzione IPv4, ma dns4_server è assente.")
    if esegui_ipv6 and not request.dns6_server:
        raise HTTPException(status_code=400,
                            detail="È stata richiesta o rilevata l'esecuzione IPv6, ma dns6_server è assente.")

    # 2. PREPARAZIONE RUNNER IPv4
    if esegui_ipv4:
        ipv4_config = CONFIG.get("dns", {}).get("from_ipv4")
        if ipv4_config:
            suite_tests = ipv4_config.get("tests", [])
            concurrency = ipv4_config.get("execution", {}).get("concurrency", 10)

            # Creiamo il runner dedicato all'IPv4 con il suo semaforo specifico
            runner_v4 = Runner(registry=registry, policy=policy, max_concurrent_tests=concurrency)
            # Aggiungiamo la coroutine alla lista dei task senza attenderla (niente await qui!)
            tasks.append(runner_v4.run_suite(suite_tests, request.dns4_server, "from_ipv4"))

    # 3. PREPARAZIONE RUNNER IPv6
    if esegui_ipv6:
        ipv6_config = CONFIG.get("dns", {}).get("from_ipv6")
        if ipv6_config:
            suite_tests = ipv6_config.get("tests", [])
            concurrency = ipv6_config.get("execution", {}).get("concurrency", 10)

            # Creiamo il runner dedicato all'IPv6 (gestisce la sua coda parallela in autonomia)
            runner_v6 = Runner(registry=registry, policy=policy, max_concurrent_tests=concurrency)
            # Aggiungiamo la coroutine alla lista
            tasks.append(runner_v6.run_suite(suite_tests, request.dns6_server, "from_ipv6"))

    if not tasks:
        raise HTTPException(status_code=404,
                            detail="Nessuna suite di test configurata o eseguibile con i parametri passati.")

    logger.info(f"Avvio esecuzione simultanea multi-stack. Task attivi in parallelo: {len(tasks)}")
    liste_risultati = await asyncio.gather(*tasks)

    # Concateneremo tutti i risultati (IPv4 e IPv6) in un unico array piatto ("flat") da restituire
    flat_results = []
    for risultati_suite in liste_risultati:
        flat_results.extend(risultati_suite)

    return {
        "suite_richiesta": request.suite,
        "dns4_server_utilizzato": request.dns4_server if esegui_ipv4 else None,
        "dns6_server_utilizzato": request.dns6_server if esegui_ipv6 else None,
        "results": flat_results
    }


# =================================================================
#                   SERVER WEB vs CLI API
# =================================================================
# if __name__ == "__main__":
#     # Se il file viene lanciato senza argomenti aggiuntivi, avviamo Uvicorn (Server Web)
#     if len(sys.argv) == 1:
#         import uvicorn
#
#         logger.info("Nessun argomento CLI rilevato. Avvio in modalità SERVER WEB API...")
#         uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
#
#     # Altrimenti, se sono presenti argomenti passiamo ad API CLI nativa
#     else:
#         parser = argparse.ArgumentParser(description="DNS Engine CLI API - Esecuzione locale e nativa")
#         parser.add_argument("--suite", type=str, default="all", help="Suite (from_ipv4, from_ipv6, all)")
#         parser.add_argument("--dns4", type=str, required=True, help="Indirizzo IPv4 del server DNS target")
#         parser.add_argument("--dns6", type=str, default=None, help="Indirizzo IPv6 del server DNS target (opzionale)")
#
#         args = parser.parse_args()
#         logger.info(f"Inizializzazione esecuzione da terminale (CLI Mode). Suite: {args.suite}")
#
#         # Incapsuliamo i dati nel modello Pydantic condiviso
#         request_data = RunSuiteRequest(
#             suite=args.suite,
#             dns4_server=args.dns4,
#             dns6_server=args.dns6
#         )
#
#         try:
#             # Eseguiamo il motore asincrono all'interno del flusso sincrono della CLI
#             results = asyncio.run(run_suite(request_data))
#             # Stampiamo il dizionario risultante in formato JSON pulito nello standard output
#             print(json.dumps(results, indent=2))
#             sys.exit(0)
#
#         except HTTPException as exc:
#             error_output = {
#                 "status": "ERROR",
#                 "http_code": exc.status_code,
#                 "message": exc.detail
#             }
#             print(json.dumps(error_output, indent=2), file=sys.stderr)
#             sys.exit(1)
#         except Exception as e:
#             error_output = {
#                 "status": "CRITICAL_ERROR",
#                 "message": str(e)
#             }
#             print(json.dumps(error_output, indent=2), file=sys.stderr)
#             sys.exit(1)