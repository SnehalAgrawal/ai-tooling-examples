// Eight interfaces a data assistant might answer with, from small to data-heavy. Written by hand; the
// numbers are synthetic (a fictional shipment-tracking fleet). Used to compare encodings, not shown to a model.
const el = (typeName, props) => ({ type: 'element', typeName, props, partial: false });
const stack = (...children) => el('Stack', { children });

const CITIES = ['Chennai', 'Delhi', 'Mumbai', 'Pune', 'Kolkata', 'Bengaluru', 'Hyderabad', 'Ahmedabad'];
const PACKAGES = [402, 399, 381, 370, 365, 352, 341, 330];

const COMPANIES = [
  ['Northwind Freight', 'IN', '312', '268'],
  ['Harbor Line', 'US', '287', '251'],
  ['Alpine Express', 'DE', '265', '230'],
  ['Coastal Carriers', 'IN', '259', '214'],
  ['Meridian Logistics', 'GB', '248', '219'],
  ['Sunrise Cargo', 'IN', '241', '199'],
  ['Pacific Haul', 'US', '236', '205'],
  ['Nordic Transit', 'SE', '229', '201'],
  ['Atlas Shipping', 'FR', '224', '190'],
  ['Redwood Movers', 'US', '219', '188'],
  ['Lotus Logistics', 'IN', '211', '176'],
  ['Danube Freight', 'AT', '206', '182'],
];

export const INTERFACES = [
  {
    id: 'one-metric',
    note: 'a single number',
    tree: stack(el('Metric', { label: 'Packages delivered', value: '2,543' })),
  },
  {
    id: 'three-metrics',
    note: 'a summary as three metrics',
    tree: stack(
      el('Heading', { text: 'October 2026' }),
      el('Metric', { label: 'Shipped', value: '3,000' }),
      el('Metric', { label: 'Delivered', value: '2,543' }),
      el('Metric', { label: 'In transit', value: '457' }),
    ),
  },
  {
    id: 'bar-chart',
    note: 'a bar chart of five values',
    tree: stack(
      el('Heading', { text: 'Packages by origin city' }),
      el('BarChart', { title: 'Packages shipped', labels: CITIES.slice(0, 5), values: PACKAGES.slice(0, 5) }),
    ),
  },
  {
    id: 'text-and-table',
    note: 'a sentence and a three-row table',
    tree: stack(
      el('Text', { text: 'Three companies shipped more than 260 packages in October.' }),
      el('Table', {
        columns: ['Company', 'Country', 'Packages'],
        rows: COMPANIES.slice(0, 3).map(([name, country, shipped]) => [name, country, shipped]),
      }),
    ),
  },
  {
    id: 'chart-and-table',
    note: 'the same eight values as a chart and a table',
    tree: stack(
      el('Heading', { text: 'Packages by origin city' }),
      el('BarChart', { title: 'Packages shipped', labels: CITIES, values: PACKAGES }),
      el('Table', { columns: ['City', 'Packages'], rows: CITIES.map((city, i) => [city, String(PACKAGES[i])]) }),
    ),
  },
  {
    id: 'dashboard',
    note: 'heading, three metrics, a chart, and a table',
    tree: stack(
      el('Heading', { text: 'Fleet overview, October 2026' }),
      el('Metric', { label: 'Shipped', value: '3,000' }),
      el('Metric', { label: 'Delivered', value: '2,543' }),
      el('Metric', { label: 'In transit', value: '457' }),
      el('BarChart', { title: 'Packages by origin city', labels: CITIES.slice(0, 5), values: PACKAGES.slice(0, 5) }),
      el('Table', {
        columns: ['Company', 'Country', 'Shipped', 'Delivered'],
        rows: COMPANIES.slice(0, 5),
      }),
    ),
  },
  {
    id: 'wide-table',
    note: 'a 12-row, 4-column table (data-heavy)',
    tree: stack(
      el('Heading', { text: 'Companies by packages shipped' }),
      el('Table', { columns: ['Company', 'Country', 'Shipped', 'Delivered'], rows: COMPANIES }),
    ),
  },
  {
    id: 'two-charts',
    note: 'two bar charts',
    tree: stack(
      el('BarChart', { title: 'Shipped by company', labels: COMPANIES.slice(0, 6).map((c) => c[0]), values: COMPANIES.slice(0, 6).map((c) => Number(c[2])) }),
      el('BarChart', { title: 'Delivered by company', labels: COMPANIES.slice(0, 6).map((c) => c[0]), values: COMPANIES.slice(0, 6).map((c) => Number(c[3])) }),
    ),
  },
];
