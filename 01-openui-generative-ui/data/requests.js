// Twelve requests a data assistant might get. Each carries its data, so the model only has to choose components
// and lay the values out; `values` are what must appear somewhere in the interface (the "all values present" check).
// The numbers are synthetic (a fictional shipment-tracking fleet).
export const REQUESTS = [
  {
    id: 'r01-one-metric',
    request: 'In October 2026, 2,543 packages were delivered. Show this as a single metric.',
    values: ['2,543'],
  },
  {
    id: 'r02-three-metrics',
    request:
      'October 2026 summary: 3,000 packages shipped, 2,543 delivered, 457 still in transit. Show a heading "October 2026" and the three numbers as metrics.',
    values: ['October 2026', '3,000', '2,543', '457'],
  },
  {
    id: 'r03-bar-chart',
    request:
      'Packages shipped by origin city: Chennai 402, Delhi 399, Mumbai 381, Pune 370, Kolkata 365. Show them as a bar chart titled "Packages by origin city".',
    values: ['Chennai', 'Delhi', 'Mumbai', 'Pune', 'Kolkata', 402, 399, 381, 370, 365],
  },
  {
    id: 'r04-table',
    request:
      'Show a table of these companies and their countries: Northwind Freight (IN), Harbor Line (US), Alpine Express (DE).',
    values: ['Northwind Freight', 'Harbor Line', 'Alpine Express', 'IN', 'US', 'DE'],
  },
  {
    id: 'r05-text-and-table',
    request:
      'Packages shipped in October: Northwind Freight 312, Harbor Line 287, Alpine Express 265. Write one sentence saying which company shipped the most, then show a table of all three.',
    values: ['Northwind Freight', 'Harbor Line', 'Alpine Express', 312, 287, 265],
  },
  {
    id: 'r06-chart-and-table',
    request:
      'Packages shipped by origin city: Chennai 402, Delhi 399, Mumbai 381, Pune 370, Kolkata 365, Bengaluru 352, Hyderabad 341, Ahmedabad 330. Show a bar chart and a table of the same numbers.',
    values: ['Chennai', 'Bengaluru', 'Hyderabad', 'Ahmedabad', 402, 352, 341, 330],
  },
  {
    id: 'r07-dashboard',
    request:
      'Build a small dashboard titled "Fleet overview": metrics for 3,000 shipped, 2,543 delivered, and 457 in transit, and a bar chart of packages by origin city: Chennai 402, Delhi 399, Mumbai 381.',
    values: ['Fleet overview', '3,000', '2,543', '457', 'Chennai', 'Delhi', 'Mumbai', 402, 399, 381],
  },
  {
    id: 'r08-wide-table',
    request:
      'Show a table with columns Company, Country, Shipped, Delivered for: Northwind Freight IN 312 268; Harbor Line US 287 251; Alpine Express DE 265 230; Coastal Carriers IN 259 214; Meridian Logistics GB 248 219; Sunrise Cargo IN 241 199.',
    values: ['Northwind Freight', 'Meridian Logistics', 'Sunrise Cargo', 'GB', 268, 251, 230, 214, 219, 199],
  },
  {
    id: 'r09-pick-components',
    request:
      'Average delivery time by company, in hours: Northwind Freight 41.5, Harbor Line 38.2, Alpine Express 44.9, Coastal Carriers 39.7. Show this in whatever way is clearest.',
    values: ['Northwind Freight', 'Harbor Line', 'Alpine Express', 'Coastal Carriers', 41.5, 38.2, 44.9, 39.7],
  },
  {
    id: 'r10-two-charts',
    request:
      'Show two bar charts. "Shipped" with Northwind Freight 312, Harbor Line 287, Alpine Express 265. "Delivered" with Northwind Freight 268, Harbor Line 251, Alpine Express 230.',
    values: ['Shipped', 'Delivered', 'Northwind Freight', 312, 287, 265, 268, 251, 230],
  },
  {
    id: 'r11-text-only',
    request: 'Show a heading "Fleet report" and one sentence saying the report covers October 2026.',
    values: ['Fleet report', 'October 2026'],
  },
  {
    id: 'r12-metric-and-text',
    request:
      'The on-time delivery rate in October was 84.8%. Show it as a metric labelled "On-time delivery", with a sentence explaining that it counts packages delivered within 48 hours.',
    values: ['On-time delivery', '84.8%', '48 hours'],
  },
];
