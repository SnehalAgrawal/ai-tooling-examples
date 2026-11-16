"""The one example used throughout: a fleet-operations assistant.

A short, fixed context about a delivery fleet; two tools; and a delivery-exception schema with
fifteen messages to extract, each with the values a correct extraction must return.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

FLEET_CONTEXT = """\
Fleet status, depot North, 16 November, 09:00.
- 412 packages out for delivery on 23 vans.
- Delayed: PKG-10432 (Leeds, van breakdown, about 4 hours), PKG-10433 (York, flooded road, about 6 hours),
  PKG-10440 (Hull, recipient not home, rescheduled to tomorrow).
- Exceptions open: 3. Vans in workshop: 1 (VAN-07).
"""

SYSTEM_PROMPT = "You are a fleet-operations assistant. Answer in one or two sentences, using only the fleet status."

QUESTION = "Which packages are delayed, and why?"


def chat_messages() -> list[dict[str, str]]:
    """The plain chat request, as Ollama's /api/chat messages."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + FLEET_CONTEXT},
        {"role": "user", "content": QUESTION},
    ]


# --------------------------------------------------------------------------- tools


def get_package_status(package_id: str) -> str:
    """Look up the current status of one package.

    Args:
        package_id: The package ID, for example PKG-10432.
    """
    return f"{package_id}: delayed, van breakdown near Leeds, new estimate 14:00."


def list_delayed_packages(depot: str, min_delay_hours: int = 0) -> str:
    """List the delayed packages at a depot.

    Args:
        depot: The depot name, for example North.
        min_delay_hours: Only list packages delayed at least this many hours.
    """
    return "PKG-10432, PKG-10433"


TOOL_QUESTION = "What is the status of package PKG-10432?"

# The same two tools written by hand, as Ollama's /api/chat expects them (OpenAI function format).
PLAIN_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_package_status",
            "description": "Look up the current status of one package.",
            "parameters": {
                "type": "object",
                "properties": {
                    "package_id": {"type": "string", "description": "The package ID, for example PKG-10432."}
                },
                "required": ["package_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_delayed_packages",
            "description": "List the delayed packages at a depot.",
            "parameters": {
                "type": "object",
                "properties": {
                    "depot": {"type": "string", "description": "The depot name, for example North."},
                    "min_delay_hours": {
                        "type": "integer",
                        "description": "Only list packages delayed at least this many hours.",
                        "default": 0,
                    },
                },
                "required": ["depot"],
            },
        },
    },
]


# --------------------------------------------------------------------------- extraction


class Reason(StrEnum):
    vehicle_breakdown = "vehicle_breakdown"
    weather = "weather"
    road_closure = "road_closure"
    recipient_absent = "recipient_absent"
    address_problem = "address_problem"
    damaged = "damaged"
    customs_hold = "customs_hold"


class DeliveryException(BaseModel):
    """A delivery exception reported by a driver or a depot."""

    package_id: str = Field(description="The package ID, in the form PKG-12345.")
    city: str = Field(description="The city where the exception happened.")
    reason: Reason = Field(description="Why the package is delayed.")
    delay_hours: int | None = Field(
        default=None, description="Expected delay in whole hours; days count as 24 hours. Null if not stated."
    )


EXTRACTION_PROMPT = """\
Extract the delivery exception from the message. Reply with JSON only, with these fields:
- package_id: the package ID, in the form PKG-12345
- city: the city where it happened
- reason: one of vehicle_breakdown, weather, road_closure, recipient_absent, address_problem, damaged, customs_hold
- delay_hours: the expected delay in whole hours (days count as 24 hours), or null if not stated"""


def extraction_messages(text: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": EXTRACTION_PROMPT}, {"role": "user", "content": text}]


# (id, message, expected values). Some are easy; some test units, distractors, and a missing delay.
CASES: list[tuple[str, str, dict]] = [
    (
        "x01",
        "Van broke down outside Leeds with PKG-10432 on board. Expect about 4 hours delay.",
        {"package_id": "PKG-10432", "city": "Leeds", "reason": "vehicle_breakdown", "delay_hours": 4},
    ),
    (
        "x02",
        "PKG-20117 stuck in York: the A64 is flooded after the storm. Six hours late at least.",
        {"package_id": "PKG-20117", "city": "York", "reason": "weather", "delay_hours": 6},
    ),
    (
        "x03",
        "Nobody home at the Hull address for PKG-30801, card left, retry tomorrow (24h).",
        {"package_id": "PKG-30801", "city": "Hull", "reason": "recipient_absent", "delay_hours": 24},
    ),
    (
        "x04",
        "Customs is holding PKG-44120 at Manchester airport. They said two days.",
        {"package_id": "PKG-44120", "city": "Manchester", "reason": "customs_hold", "delay_hours": 48},
    ),
    (
        "x05",
        "Box for PKG-51002 arrived crushed at the Sheffield depot; sending a replacement, 3 hours.",
        {"package_id": "PKG-51002", "city": "Sheffield", "reason": "damaged", "delay_hours": 3},
    ),
    (
        "x06",
        "Bradford: road closed for a marathon, PKG-60233 can't get through until it reopens in 5 hours.",
        {"package_id": "PKG-60233", "city": "Bradford", "reason": "road_closure", "delay_hours": 5},
    ),
    (
        "x07",
        "PKG-70045, Durham. Street number doesn't exist, waiting for the customer to confirm the address.",
        {"package_id": "PKG-70045", "city": "Durham", "reason": "address_problem", "delay_hours": None},
    ),
    (
        "x08",
        "Snow on the Pennines. PKG-80310 to Halifax is held, roughly 1.5 days.",
        {"package_id": "PKG-80310", "city": "Halifax", "reason": "weather", "delay_hours": 36},
    ),
    (
        "x09",
        "Driver reports clutch failure near Wakefield. PKG-90007 and the rest of van 12 wait for a tow; ~2 hours.",
        {"package_id": "PKG-90007", "city": "Wakefield", "reason": "vehicle_breakdown", "delay_hours": 2},
    ),
    (
        "x10",
        "Re PKG-10432 (delivered yesterday): new problem with PKG-11876 in Leeds, the recipient was out. 24 hours.",
        {"package_id": "PKG-11876", "city": "Leeds", "reason": "recipient_absent", "delay_hours": 24},
    ),
    (
        "x11",
        "PKG-12990 left Newcastle late, police closed the bridge. New ETA is 3 hours later than planned.",
        {"package_id": "PKG-12990", "city": "Newcastle", "reason": "road_closure", "delay_hours": 3},
    ),
    (
        "x12",
        "Water damage on PKG-13404 after the leak at the Doncaster hub. No new date yet.",
        {"package_id": "PKG-13404", "city": "Doncaster", "reason": "damaged", "delay_hours": None},
    ),
    (
        "x13",
        "pkg-14555 york customs paperwork missing, held 12h",
        {"package_id": "PKG-14555", "city": "York", "reason": "customs_hold", "delay_hours": 12},
    ),
    (
        "x14",
        "Flat tyre on the M62 just outside Huddersfield. PKG-15020 will be about half a day late.",
        {"package_id": "PKG-15020", "city": "Huddersfield", "reason": "vehicle_breakdown", "delay_hours": 12},
    ),
    (
        "x15",
        "The postcode for PKG-16008 points to Leeds but the street is in Harrogate. Sorting it out, 8 hours.",
        {"package_id": "PKG-16008", "city": "Harrogate", "reason": "address_problem", "delay_hours": 8},
    ),
]
