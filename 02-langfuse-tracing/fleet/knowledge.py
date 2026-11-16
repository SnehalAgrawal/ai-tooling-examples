"""The fleet assistant's knowledge base: short policy and runbook snippets for a fictional delivery fleet.

Everything here is invented for the demo (company, numbers, procedures).
"""

SNIPPETS: list[dict[str, str]] = [
    {
        "id": "pol-breaks",
        "title": "Driver breaks",
        "text": "Drivers must take a 30-minute break after 4.5 hours of driving. "
        "A break can be split into two parts of 15 minutes each.",
    },
    {
        "id": "pol-speed",
        "title": "Speed limits for vans",
        "text": "Company vans are limited to 90 km/h on motorways by a speed governor. "
        "Inside depots the limit is 10 km/h.",
    },
    {
        "id": "rb-breakdown",
        "title": "Vehicle breakdown runbook",
        "text": "If a van breaks down, the driver turns on the hazard lights, moves to a safe place, "
        "and calls the fleet desk on extension 4410. The fleet desk dispatches roadside assistance "
        "and reassigns undelivered parcels to the nearest van.",
    },
    {
        "id": "rb-ev-charging",
        "title": "Electric van charging",
        "text": "Electric vans must be plugged in at the depot when the battery is below 40 percent at the end "
        "of a shift. A full charge on a depot charger takes about 6 hours.",
    },
    {
        "id": "pol-cold-chain",
        "title": "Cold-chain parcels",
        "text": "Refrigerated parcels must stay between 2 and 8 degrees Celsius. "
        "If the van's cooler shows a temperature outside that range for more than 20 minutes, "
        "the driver reports a cold-chain incident in the app.",
    },
    {
        "id": "pol-failed-delivery",
        "title": "Failed deliveries",
        "text": "After a failed delivery the driver leaves a card and tries again the next working day. "
        "After 3 failed attempts the parcel goes back to the depot and is held for 7 days.",
    },
    {
        "id": "rb-accident",
        "title": "Accident runbook",
        "text": "After an accident the driver checks for injuries, calls emergency services if anyone is hurt, "
        "takes photos of the scene, and files an accident report in the app within 24 hours.",
    },
    {
        "id": "pol-maintenance",
        "title": "Scheduled maintenance",
        "text": "Every van gets a service every 20,000 km or every 6 months, whichever comes first. "
        "Tyres are replaced when tread depth falls below 3 mm.",
    },
    {
        "id": "pol-fuel-card",
        "title": "Fuel cards",
        "text": "Fuel cards may only be used for the van they are assigned to. "
        "A lost fuel card must be reported to the fleet desk the same day so it can be blocked.",
    },
    {
        "id": "pol-loading",
        "title": "Loading limits",
        "text": "The maximum payload of a standard van is 1,200 kg. Parcels over 30 kg need two people to load.",
    },
]
