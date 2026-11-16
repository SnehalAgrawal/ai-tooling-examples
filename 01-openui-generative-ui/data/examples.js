// Two examples for the prompt's `examples` option, about unrelated data (rainfall, store sales) so a model
// cannot copy values from them into the answers. Checked at startup: both must parse with no errors.
export const EXAMPLES = [
  `root = Stack([title, chart])
title = Heading("Rainfall by month")
chart = BarChart("Rainfall (mm)", ["Jan", "Feb", "Mar"], [12, 30, 45])`,
  `root = Stack([summary, total, table])
summary = Text("Two stores sold more than 100 units.")
total = Metric("Units sold", "245")
table = Table(["Store", "Units"], [["North", "130"], ["South", "115"]])`,
];
