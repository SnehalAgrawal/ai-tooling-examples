"""The requests every condition sends: one fleet-operations assistant, eight writing tasks.

Replies are a paragraph or two (up to 256 tokens), long enough for small differences to show.
"""

SYSTEM = (
    "You are the operations assistant of a parcel delivery company. Write clearly and concisely for the "
    "operations team. Use only the facts given in the request."
)

REQUESTS = [
    ("p1", "Package 1042 left Pune at 09:10 on 14 October and was due in Delhi on 16 October. Its last scan was at "
           "the Nagpur hub on 15 October at 22:40, and it has not been scanned since. Write a short update for the "
           "customer support team."),
    ("p2", "Summarise this week's delays for the morning meeting: 41 packages late out of 3,120; 18 because of a "
           "truck breakdown on the Mumbai-Pune route on Tuesday, 15 because of a sorting backlog in Delhi on "
           "Thursday, and 8 for weather in Chennai."),
    ("p3", "Explain to a new dispatcher, in plain language, why a tracking device's battery level matters for "
           "packages that travel for more than three days."),
    ("p4", "Draft a polite message to a business customer whose shipment of 12 parcels was split across two trucks, "
           "so 9 arrived on Monday and the remaining 3 will arrive on Wednesday."),
    ("p5", "List the checks a hub supervisor should do when a package shows status 'exception' with the note "
           "'temperature above threshold', and say why each check matters."),
    ("p6", "Compare two options for the night shift in Kolkata: adding one more sorter (cost 28,000 a month) or "
           "starting the shift an hour earlier (no extra cost, but drivers arrive later). Give the trade-offs."),
    ("p7", "A customer asks why their package went from Chennai to Bengaluru and then back to Chennai before "
           "delivery. The route log shows it was mis-sorted at the Chennai hub. Write the reply."),
    ("p8", "Write three short tips for drivers on scanning packages correctly at handover, with one sentence on "
           "what goes wrong when a scan is missed."),
]


def messages(prompt: str) -> list[dict]:
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
