from dataclasses import dataclass


@dataclass(frozen=True)
class Dataset:
    """A public dataset the tools read. The README's data table is generated from these."""

    id: str
    name: str  # the publisher's official name
    label: str  # short name for the chat page's sources footer
    publisher: str
    refreshed: str  # how often the publisher updates it
    used_for: str
    url: str = ""

    def __post_init__(self) -> None:
        if not self.url:
            object.__setattr__(self, "url", f"https://data.cityofnewyork.us/d/{self.id}")


GEOSEARCH = Dataset(
    id="geosearch",
    name="NYC GeoSearch",
    label="NYC GeoSearch",
    publisher="Department of City Planning",
    refreshed="Quarterly",
    used_for="Turning an address into a BBL, BIN and coordinates",
    url="https://geosearch.planninglabs.nyc/",
)
PLUTO = Dataset(
    id="64uk-42ks",
    name="Primary Land Use Tax Lot Output (PLUTO)",
    label="Tax lot records (PLUTO)",
    publisher="Department of City Planning",
    refreshed="Quarterly",
    used_for="Year built, floors, units and owner of record for each tax lot",
)
HPD_BUILDINGS = Dataset(
    id="kj4p-ruqc",
    name="Buildings Subject to HPD Jurisdiction",
    label="HPD building register",
    publisher="HPD",
    refreshed="Monthly",
    used_for="Legal apartment counts per building",
)
HPD_VIOLATIONS = Dataset(
    id="wvxf-dwi5",
    name="Housing Maintenance Code Violations",
    label="HPD violations",
    publisher="HPD",
    refreshed="Daily",
    used_for="Violations by severity class and type, and old lot numbers after renumbering",
)
SERVICE_REQUESTS = Dataset(
    id="erm2-nwe9",
    name="311 Service Requests from 2020 to Present",
    label="311 complaints",
    publisher="311",
    refreshed="Daily",
    used_for="Complaints about the building and its neighbors",
)
HPD_REGISTRATIONS = Dataset(
    id="tesw-yqqr",
    name="Multiple Dwelling Registrations",
    label="HPD registrations",
    publisher="HPD",
    refreshed="Monthly",
    used_for="Whether the building is registered, and the buildings in a landlord's portfolio",
)
HPD_CONTACTS = Dataset(
    id="feu5-w2e2",
    name="Registration Contacts",
    label="Owner and agent contacts",
    publisher="HPD",
    refreshed="Monthly",
    used_for="Owner, head officer and managing agent, used to link a landlord's buildings",
)
AEP = Dataset(
    id="hcir-3275",
    name="Buildings Selected for the Alternative Enforcement Program (AEP)",
    label="Worst-buildings program (AEP)",
    publisher="HPD",
    refreshed="Monthly",
    used_for="Which of a landlord's buildings are among the city's worst-maintained",
)
EVICTIONS = Dataset(
    id="6z8x-wfk4",
    name="Evictions",
    label="Marshal evictions",
    publisher="Department of Investigation",
    refreshed="Daily",
    used_for="Residential evictions carried out by city marshals",
)
BEDBUGS = Dataset(
    id="wz6d-d3jb",
    name="Bedbug Reporting",
    label="Bedbug reports",
    publisher="HPD",
    refreshed="Monthly",
    used_for="Owners' annual bedbug reports, and missing ones",
)
LITIGATIONS = Dataset(
    id="59kj-x8nc",
    name="Housing Litigations",
    label="Housing court cases",
    publisher="HPD",
    refreshed="Monthly",
    used_for="Housing court cases, harassment findings and court-appointed administrators",
)
VACATE_ORDERS = Dataset(
    id="tb8q-a3ar",
    name="Order to Repair/Vacate Orders",
    label="Vacate orders",
    publisher="HPD",
    refreshed="Daily",
    used_for="Orders forcing tenants out of unsafe apartments or buildings",
)

ALL = [
    GEOSEARCH,
    PLUTO,
    HPD_BUILDINGS,
    HPD_VIOLATIONS,
    SERVICE_REQUESTS,
    HPD_REGISTRATIONS,
    HPD_CONTACTS,
    AEP,
    EVICTIONS,
    BEDBUGS,
    LITIGATIONS,
    VACATE_ORDERS,
]
