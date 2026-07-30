class TestResultBuilder:

    @staticmethod
    def from_error(test, plugin_result):
        # Formato in caso di fallimenti hard di rete (timeout, irraggiungibile)
        return {
            "test_id": test.get("id"),
            #"test_name": test.get("name"),
            "test_type": test.get("type"),
            "status": "ERROR",
            "summary": "Il test non ha potuto completare l'ispezione di rete.",
            "details": {
                "error_code": plugin_result.error.code if plugin_result.error else "UNKNOWN",
                "error_message": plugin_result.error.message if plugin_result.error else "Unknown Error",
                # Inviamo comunque i criteri attesi per debug
                "expected_assertions": test.get("assertions", {})
            }
        }

    @staticmethod
    def from_success(test, plugin_result, evaluation):
        # Formato standardizzato in caso di risposta ricevuta (sia PASS che FAIL logico)
        return {
            "test_id": test.get("id"),
            #"test_name": test.get("name"),
            "test_type": test.get("type"),
            "status": "PASS" if evaluation.get("passed") else "FAIL",
            "summary": "OK",
            "details": {
                # 1. DATI OSSERVATI: L'output effettivo del comando DNS (es. ['185.236.106.107'])
                "observed_data": plugin_result.data,

                # 2. DATI ATTESI: Cosa c'era scritto nel config.yml (es. op: 'equals', value: '...')
                "expected_assertions": test.get("assertions", {}),

                # 3. REPORT DI VALUTAZIONE: L'esito calcolato dall'evaluator per ogni regola
                "evaluation_report": evaluation.get("groups", {})
            }
        }