import os

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from edi_triage import EdiTriageResult, FailedTransaction, run_edi_triage

BEDROCK_MOCK = os.getenv("BEDROCK_MOCK", "true").lower() == "true"

# The SDK auto-enables Host/Origin allowlisting for DNS-rebinding protection, but
# only when it thinks it's bound to localhost. Since this is mounted behind a real
# public domain, that allowlist must be configured explicitly here,
# otherwise every request in production 421s once the default localhost-only
# allowlist doesn't match the deployed Host header.
_ALLOWED_HOSTS = [
    h.strip()
    for h in os.getenv(
        "MCP_ALLOWED_HOSTS",
        "127.0.0.1:*,localhost:*,[::1]:*",
    ).split(",")
    if h.strip()
]
_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "MCP_ALLOWED_ORIGINS",
        "http://127.0.0.1:*,http://localhost:*,http://[::1]:*",
    ).split(",")
    if o.strip()
]

class EdiTriageToolResult(EdiTriageResult):
    transaction_id: str
    tier: str
    classification: str
    mode: str


mcp_server = MCPServer(
    name="edi-failure-triage",
    title="EDI Failure Triage",
    instructions=(
        "Classify one failed EDI transaction, rank the likely causes with evidence, "
        "draft the customer reply, and propose the ticket-two fix that prevents recurrence."
    ),
)


@mcp_server.tool()
async def triage_edi_transaction(tx: FailedTransaction) -> EdiTriageToolResult:
    """Classify a failed EDI transaction, rank causes, draft the reply, propose the ticket-two fix."""
    return EdiTriageToolResult.model_validate(run_edi_triage(tx, mock=BEDROCK_MOCK))


def streamable_http_app():
    return mcp_server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=_ALLOWED_HOSTS,
            allowed_origins=_ALLOWED_ORIGINS,
        ),
    )


if __name__ == "__main__":
    mcp_server.run(transport="stdio")
