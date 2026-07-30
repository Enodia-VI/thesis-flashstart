from pydantic import BaseModel
from typing import Any, Optional, Literal, List, Union
from abc import ABC, abstractmethod

# Richiesta API
class RunSuiteRequest(BaseModel):
    suite: str
    dns4_server: str
    dns6_server: Optional[str] = None

# Modelli per l'output dei Plugin
class PluginError(BaseModel):
    code: str
    message: str

class PluginOutput(BaseModel):
    ok: bool
    data: Optional[Any] = None
    error: Optional[PluginError] = None

class AssertionModel(BaseModel):
    op: Literal["equals", "contains", "not_contains"]
    value: Any

class DnsInput(BaseModel):
    qname: str
    qtype: str

class TestCase(BaseModel):
    id: str
    name: str
    type: Literal["dns_query", "nic_update", "dot", "doh"]
    input: DnsInput
    assertions: List[AssertionModel]

# Interfaccia Base astratta aggiornata con la tipizzazione corretta
class BasePlugin(ABC):
    @abstractmethod
    async def run(self, input_data: DnsInput, resolver: str) -> PluginOutput:
        pass

class NicUpdateInput(BaseModel):
    hostname: str
    username: str
    password: str
    url: str

# Modelli per la lista dei nodi DNS (inviata dalla macchina Ansible, letta dal Frontend)
class DnsServerEntry(BaseModel):
    name: str
    ip: str
    ipv6: Optional[str] = None
    continent: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    cluster: Optional[str] = None

class PushDnsServersRequest(BaseModel):
    servers: List[DnsServerEntry]

class TestCase(BaseModel):
    id: str
    name: str
    type: Literal["dns_query", "nic_update", "dot", "doh"]
    input: Union[DnsInput, NicUpdateInput]
    assertions: List[AssertionModel]