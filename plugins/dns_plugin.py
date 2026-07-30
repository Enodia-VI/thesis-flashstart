import dns.query
import dns.asyncquery
import dns.message
import dns.rdatatype
import dns.exception
import dns.resolver

from api.dictionaries import PluginOutput, BasePlugin, PluginError, DnsInput


class DnsPlugin(BasePlugin):

    async def run(self, input_data: dict, resolver: str) -> PluginOutput:
        try:
            parsed = DnsInput(**input_data)
            query = self._build_query(
                parsed.qname,
                parsed.qtype
            )

            # Rimosso loop.run_in_executor!
            # Ora eseguiamo direttamente la coroutine in modo 100% asincrono.
            data = await self._query_dns(resolver, query)

            return PluginOutput(
                ok=True,
                data=data,
                error=None
            )

        # ------------------------- ERROR MAPPING -------------------------

        except dns.exception.Timeout:
            return self._error("DNS_TIMEOUT", "Timeout contacting DNS server")

        except dns.resolver.NXDOMAIN:
            return self._error("DNS_NXDOMAIN", "Domain does not exist")

        except dns.resolver.NoNameservers:
            return self._error("DNS_NO_NAMESERVERS", "No nameservers available")

        except OSError as e:
            # Aggiungo il messaggio originale dell'OSError per un debug più facile
            return self._error("DNS_NETWORK_ERROR", f"Network unreachable: {e}")

        except Exception as e:
            return self._error("DNS_UNKNOWN_ERROR", str(e))

    # ---------------- INTERNAL ----------------

    @staticmethod
    def _error(code: str, message: str) -> PluginOutput:
        # 1. Costruiamo prima l'oggetto interno tipizzato correttamente
        typed_error = PluginError(
            code=code,
            message=message
        )

        # 2. Passiamo l'oggetto tipizzato all'output principale
        return PluginOutput(
            ok=False,
            data=None,
            error=typed_error
        )

    @staticmethod
    def _build_query(domain, qtype):
        rdtype = dns.rdatatype.from_text(qtype)
        return dns.message.make_query(domain, rdtype)

    @staticmethod
    async def _query_dns(where, query):

        answer = await dns.asyncquery.udp(
            query,
            where,
            timeout=10
        )

        return [
            str(rdata)
            for rrset in answer.answer
            for rdata in rrset
        ]