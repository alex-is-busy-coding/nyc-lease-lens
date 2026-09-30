from nyc_lease_lens.opendata import OpenDataClient
from nyc_lease_lens.tools.base import Tool, ToolError, ToolRegistry
from nyc_lease_lens.tools.building import LookupBuilding
from nyc_lease_lens.tools.complaints import Get311Complaints
from nyc_lease_lens.tools.landlord import GetLandlordProfile
from nyc_lease_lens.tools.violations import GetHpdViolations

__all__ = ["TOOL_CLASSES", "Tool", "ToolError", "ToolRegistry", "build_registry"]

# To add a tool: write a Tool subclass in its own module and list it here.
TOOL_CLASSES: list[type[Tool]] = [LookupBuilding, GetHpdViolations, Get311Complaints, GetLandlordProfile]


def build_registry(client: OpenDataClient) -> ToolRegistry:
    return ToolRegistry([cls(client) for cls in TOOL_CLASSES])
