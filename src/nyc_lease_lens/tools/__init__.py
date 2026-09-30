from nyc_lease_lens.opendata import OpenDataClient
from nyc_lease_lens.tools.base import Tool, ToolError, ToolRegistry
from nyc_lease_lens.tools.building import LookupBuilding

__all__ = ["Tool", "ToolError", "ToolRegistry", "build_registry"]


def build_registry(client: OpenDataClient) -> ToolRegistry:
    """To add a tool: write a Tool subclass in its own module and list it here."""
    return ToolRegistry([LookupBuilding(client)])
