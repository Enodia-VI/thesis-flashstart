import asyncio
import logging

from api.dictionaries import DnsInput

logger = logging.getLogger("engine.runner")


class Runner:

    def __init__(self, registry, policy, max_concurrent_tests: int = 10):
        self.registry = registry
        self.policy = policy
        # Limita i test simultanei per non saturare la macchina o i DNS target
        self.semaphore = asyncio.Semaphore(max_concurrent_tests)

    async def run_suite(self, suite_tests: list, resolver: str, suite_name: str = "unknown"):
        logger.info(f"Avvio esecuzione suite [{suite_name}] contenente {len(suite_tests)} test.")

        # Funzione worker interna che rispetta il semaforo
        async def worker(test):
            async with self.semaphore:
                return await self.run_test(test, resolver)

        # Creazione dei task concorrenti
        tasks = [worker(test) for test in suite_tests]

        # Esecuzione parallela controllata
        results = await asyncio.gather(*tasks)

        logger.info(f"Suite [{suite_name}] completata con successo.")
        return results

    async def run_test(self, test, resolver):
        plugin_type = test["type"]

        if plugin_type not in self.registry or not self.registry[plugin_type]:
            logger.error(f"Test {test.get('id')}: plugin di tipo '{plugin_type}' assente nel Registry.")
            return {
                "id": test.get("id"),
                "name": test.get("name"),
                "status": "ERROR",
                "error": "PLUGIN_NOT_CONFIGURED",
                "message": f"Il plugin per {plugin_type} non è censito nel sistema."
            }

        plugin = self.registry[plugin_type]
        logger.debug(f"Esecuzione test {test['id']} tramite plugin {plugin_type}")

        try:
            result = await plugin.run(test["input"], resolver)

            return await self.policy.handle(test, result)

        except Exception as e:
            logger.exception(f"Errore critico non gestito durante il test {test.get('id')}")
            return {
                "id": test.get("id"),
                "name": test.get("name"),
                "status": "ERROR",
                "error": "UNEXPECTED_RUNNER_EXCEPTION",
                "message": str(e)
            }