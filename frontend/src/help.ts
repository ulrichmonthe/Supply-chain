/*
 * What each part of the tool is for, in one or two sentences a person who has never
 * seen a network design tool can act on. Shown by the "?" buttons (components/Help.tsx)
 * and as the tooltip on the tabs. Writing, not code: edit freely.
 */

export const TAB_HELP: Record<string, string> = {
  Scorecard:
    'Every scenario side by side on cost, service and equity, against today’s network. Start here to see which option wins on what.',
  Studies:
    'A question and the scenarios that answer it, kept together with a verdict and a diff map. Use it when you are comparing options rather than running one.',
  Equity:
    'Who carries each plan: facilities grouped from easiest to hardest to reach, and how well each group is supplied.',
  Facilities:
    'One facility at a time: its numbers, its lanes, its demand, and the editor that changes them with your name on the change.',
  Services:
    'The timetabled boats and planes: how often they run, how much they carry, and how many people depend on each.',
  Season:
    'What the wet season does: lanes that close, facilities cut off, and the twelve-month view with stock carried between months.',
  Data: 'Getting data in and out: the workbook round trip, CSV import with a column mapper, estimates for blank rows, adding a facility, and editing any table.',
  Onboard:
    'Bringing a ministry’s real files in properly: a country pack, reconciled facilities, checks, estimates, a review queue and a sign-off before anything loads.',
  Live: 'Connections to DHIS2, OpenLMIS or mSupply, tested step by step, synced through the same checks as a workbook.',
  Provenance:
    'The ledger: every value that entered the model, who put it there, how sure they were, and the undo for each.',
  Roadmap:
    'What it would take to get from today’s network to this scenario: the steps, their one-off costs, and the order.',
}

export const HELP = {
  // top bar
  countryPicker:
    'Switch between country workspaces. Each has its own facilities, lanes, scenarios and ledger.',
  statFacilities:
    'Health facilities in this workspace: hospitals, health centres and aid posts, whether or not they have demand loaded yet.',
  statStores: 'Stores currently operating as supply points. Scenarios can open or close others.',
  statTimetables:
    'Scheduled boat and plane services with a frequency and a hold size. Roads run on demand and are not counted here.',
  statPeople: 'People in the catchments of every facility, as the facility list records them.',
  view: 'Expert shows every lever and tab. Decision hides them and keeps the map, the options as cards and the three numbers a decision-maker reads.',
  signed: 'The name recorded on everything you change. Not a login: a signature, so the ledger can say who.',
  guide: 'A two-minute walk through the screen, one control at a time.',
  newCountry:
    'Open an empty workspace for another country. Load its facilities from a spreadsheet, a CSV or a connected system next.',
  report:
    'A self-contained document for people who will not open this tool: the decision, the figures, who carries it, how sure it is. Prints to PDF.',
  spreadsheet:
    'The results of this scenario as a workbook: scorecard, every facility, equity and the roadmap.',

  // scenario sidebar
  sessions:
    'A named, complete save of everything on screen. Open one later and the map, data and results are exactly as saved.',
  scenarios:
    'Each scenario is a question: close a store, change a timetable, spend less. Tick the ones to run together; the baseline is today’s network.',
  objective:
    'What the solver tries to do. Cost pulls towards the cheapest plan, service towards delivering everything, equity towards protecting the hardest-to-reach first.',
  constraints:
    'Lines the solver may not cross whatever the objective says: a minimum share of demand delivered, a floor for every vulnerability group, physical capacity.',
  levers:
    'The things a planner can change about the network: which transport modes are allowed, which stores are open, how often services run, how costs and demand move.',
  weightCost:
    'How much a unit of money matters. Raise it and the solver gives up service to save; lower it and cost stops being the point.',
  weightService:
    'How much delivering a cubic metre matters. At zero the solver will happily deliver nothing if that is cheaper.',
  weightEquity:
    'Raises the cost of failing a hard-to-reach facility, so the solver protects it before a cheap one.',
  minFill: 'A network-wide floor: at least this share of all demand must be delivered, whatever it costs.',
  equityFloor:
    'Every vulnerability group, easiest to hardest to reach, must be supplied to at least this level. The lever that decides who pays for a saving.',
  respectCapacity:
    'Keep vessel holds and store throughput as hard limits. Untick to see what the network could do if capacity were not the problem.',
  modes:
    'Which ways of moving stock the scenario may use. Untick air to see what the network costs without charters.',
  integration:
    'Whether programmes share one supply chain or run their own. Vertical programmes duplicate transport and storage.',
  services:
    'Change how often each timetabled service runs. Halve a sailing and the facilities on it get stock half as often.',
  month:
    'Hold one month’s conditions all year, or solve twelve linked months with stock carried between them.',
  runSet:
    'Solve every ticked scenario in parallel and refresh the scorecard. Nothing is written to the data; results are kept per scenario.',

  // map
  colourBy:
    'What the facility dots mean: how well each is supplied, how at risk it is of running out, or how hard it is to reach.',
  onlineTiles:
    'Draw streets and terrain from the internet behind the network. Off by default so the tool works offline; the land mask is built in.',
  conditions:
    'Annualised solves one year at average conditions. Pick a month to re-solve under that month’s road closures and sea conditions.',

  // results
  kpiCost:
    'Everything the plan costs in a year: transport plus running the stores, with capital spread over its life.',
  kpiFill: 'Share of all demand, by volume, that the plan delivers.',
  kpiWorst: 'Share of demand delivered to the hardest-to-reach fifth of facilities. The equity number.',
  kpiRisk:
    'How likely a facility is to run out between deliveries, averaged over facilities. Driven by how often it is reached and how much it can hold.',

  // data tab
  takeModel:
    'Download the whole model as a workbook, as CSV files, or an empty template with the same columns. Whatever comes out can go back in.',
  workbook:
    'Upload a workbook in the template’s columns. It is validated first and nothing is written until you review the changes and apply them.',
  csvImport:
    'Any CSV from another system. Map its columns to ours once; the mapping is kept under a name for next time. Merged in, never a wipe.',
  addFacility:
    'Add one facility by hand, or click a point on the map. It is checked the way an import would be, and recorded with your name.',
  estimates:
    'Fill blank rows by a named rule that shows its working: from population, like similar facilities, storage from cover days. Live until you type over them.',
  tables:
    'Every table as an editable grid. Each cell change is a ledger row with your name and how sure you were; select rows to change them together.',

  // elsewhere
  studies:
    'Create a study from a preset or a question of your own, add scenarios, run them, and read the verdict and the diff map.',
  greenfield:
    'Where new stores would go if placed by the demand itself. Adopt the proposal and the solver decides whether each is worth opening.',
  connections:
    'A link to a live system. Test it step by step, preview what a sync would change, then apply it through the same checks as a workbook.',
  provenance:
    'Every value that entered the model with who, when, how sure and why. Revert any row; a revert is itself a row.',
  roadmap: 'The steps from today’s network to this scenario, phased, with the one-off cost of each.',
  onboarding:
    'The six-step pipeline for a ministry’s real data. Nothing here writes to the model; loading is the last button, on its own.',
} as const
