/*
 * What the guided tour says.
 *
 * Kept apart from the machinery in components/Tour.tsx because this is writing, not
 * code, and it gets edited for different reasons. Each step explains one thing and
 * says what it is *for* — a reader who has never seen a network design tool does not
 * need "this is the objective weight slider", they need to know that moving it changes
 * which question the solver is answering.
 */

import type { TourStep } from './components/Tour'
import type { ColourBy } from './components/MapView'

export type TourActions = {
  setTab: (tab: string) => void
  setColourBy: (colour: ColourBy) => void
}

export const TOUR_STORAGE_KEY = 'hscn.tour.seen.v1'

export function buildTour({ setTab, setColourBy }: TourActions): TourStep[] {
  return [
    {
      id: 'welcome',
      placement: 'centre',
      title: 'This is a model of a country’s health supply chain',
      body: (
        <>
          <p>
            Every clinic, every warehouse and every way of moving a box between them is in
            here. You change something — close a store, add a boat, spend less — and it
            works out what that would do to the medicines actually arriving.
          </p>
          <p>
            Two minutes, fourteen stops. You can leave at any point and reopen the guide
            from <b>Guide</b> in the top right.
          </p>
        </>
      ),
    },

    {
      id: 'map',
      target: '.map-wrap',
      placement: 'left',
      padding: 0,
      title: 'The map is the network',
      body: (
        <>
          <p>
            Each dot is a facility or a store. Each line is a lane something can travel
            along — <b>road</b>, <b>sea</b>, <b>air</b> or <b>river</b>, coloured by mode in
            the key at the bottom left.
          </p>
          <p>
            The counts in the top bar tell you the size of what you are looking at:
            facilities, open stores, timetabled boat and air services, and the population
            they serve. Click any dot to see that facility’s numbers.
          </p>
        </>
      ),
    },

    {
      id: 'colour',
      target: '.map-mode',
      placement: 'left',
      title: 'Ask the map a different question',
      body: (
        <>
          <p>
            The dots are coloured by how well each facility is supplied. Switch this and
            the same map answers something else: where the <b>stockout risk</b> is, which
            places are most <b>vulnerable</b> to begin with, or which <b>transport mode</b>
            each one depends on.
          </p>
          <p className="tour-aside">
            Online tiles are optional. Turned off, you get a coarse land outline — the same
            geometry the tool uses to catch a facility that has been typed into the sea.
          </p>
        </>
      ),
    },

    {
      id: 'scenarios',
      target: '[data-tour="scenarios"]',
      placement: 'right',
      title: 'A scenario is a question',
      body: (
        <>
          <p>
            The one marked <b>base</b> is the network as it stands today — every comparison
            is made against it. The others are questions somebody wanted answered: what if
            we closed a store, what if the budget were cut, what if the monsoon lasted
            longer.
          </p>
          <p>
            Click one to work on it. Tick the boxes to choose which ones get compared side
            by side. <b>Duplicate</b> is how you ask a variation without losing the original.
          </p>
          <p>
            Tag them — <i>board pack</i>, <i>seasonal</i>, <i>cost</i> — and the chips at the
            top filter the list. Two tags narrow rather than widen, and the search box also
            looks inside descriptions. Filtering only changes what you can see: whatever is
            ticked still runs.
          </p>
        </>
      ),
    },

    {
      id: 'levers',
      target: '[data-tour="levers"]',
      placement: 'right',
      title: 'The levers are what you change',
      body: (
        <>
          <p>
            Scroll this panel and you can weight <b>cost</b> against <b>service</b> against{' '}
            <b>equity</b>, set floors the plan must respect, switch transport modes off, or
            change how often a boat runs.
          </p>
          <p>
            Raising <b>equity</b> makes failing a hard-to-reach facility expensive to the
            solver, so it protects those places before it protects convenient ones. That is
            the lever that changes who a plan is for.
          </p>
        </>
      ),
    },

    {
      id: 'run',
      target: '[data-tour="run"]',
      placement: 'right',
      title: 'Then you solve it',
      body: (
        <>
          <p>
            This is a real optimiser, not a spreadsheet recalculation: it searches for the
            cheapest way to meet the demand you have described, under the constraints you
            have set. A country-sized network takes well under a second.
          </p>
          <p>
            This button runs every ticked scenario at once, so you can compare them. The{' '}
            <b>Run</b> button inside a selected scenario re-solves just that one.
          </p>
        </>
      ),
    },

    {
      id: 'results',
      target: '.inspector',
      placement: 'left',
      title: 'The answer arrives here',
      body: (
        <>
          <p>
            Four headline numbers appear at the top once a scenario has been run — total
            cost, how much of the demand is met, how the worst-served fifth of the
            population fares, and the average stockout risk. Each carries its change
            against the baseline.
          </p>
          <p>
            The tabs below open the same result nine different ways. The guide visits four
            of them next.
          </p>
        </>
      ),
    },

    {
      id: 'equity',
      target: '.tab-body',
      placement: 'left',
      before: () => setTab('Equity'),
      title: 'Equity: who pays for the saving',
      body: (
        <>
          <p>
            Facilities are ranked by how hard they are to reach and split into five groups
            of equal population. <b>Q1</b> is the easiest fifth to serve; <b>Q5</b> the
            hardest.
          </p>
          <p>
            A cheaper network is nearly always cheaper because it stopped reaching Q5. This
            tab says so in people rather than percentages, which is the difference between a
            saving and a decision.
          </p>
        </>
      ),
    },

    {
      id: 'season',
      target: '.season-bar',
      placement: 'top',
      title: 'Seasons are not a footnote',
      body: (
        <>
          <p>
            Click a month. The tool holds that month’s conditions for a whole year and
            re-solves: monsoon roads close, rough seas cut how much a boat can carry, and
            the cost and the reach both move.
          </p>
          <p>
            <b>Annualised</b> is the average across twelve months — useful for budgeting,
            and useless for knowing whether February is survivable. Check both.
          </p>
        </>
      ),
    },

    {
      id: 'data',
      target: '.tab-body',
      placement: 'left',
      before: () => setTab('Data'),
      title: 'Data: bring your own country',
      body: (
        <>
          <p>
            Download the blank template, fill it in, upload it. Nothing is saved until you
            say so — the tool checks the file first and lists what is wrong, and you can
            correct a flagged row right here without reopening the spreadsheet.
          </p>
          <p>
            Export gives you back a file the same shape as the one that went in, so your
            work outlives this tool.
          </p>
        </>
      ),
    },

    {
      id: 'live',
      target: '.tab-body',
      placement: 'left',
      before: () => setTab('Live'),
      title: 'Live: pull from the systems you already run',
      body: (
        <>
          <p>
            Connect a <b>DHIS2</b>, <b>OpenLMIS</b> or <b>Open mSupply</b> instance and the
            facility list comes from there instead of a spreadsheet. Test the connection,
            preview exactly what would change, then apply it.
          </p>
          <p>
            Matching is by system id first, then code, then name and proximity. A facility
            whose coordinates have moved more than two kilometres is flagged rather than
            silently relocated.
          </p>
        </>
      ),
    },

    {
      id: 'provenance',
      target: '.tab-body',
      placement: 'left',
      before: () => setTab('Provenance'),
      title: 'Provenance: how much to trust this',
      body: (
        <>
          <p>
            Every distance records how it was worked out — measured on a real road network,
            estimated with a terrain factor, or drawn straight through the air — and carries
            a confidence score. Anything that was changed by hand is listed with who changed
            it and why.
          </p>
          <p>
            Read this tab before you quote a number in a meeting. It is where the model
            admits what it does not know.
          </p>
        </>
      ),
    },

    {
      id: 'deliverable',
      target: '.topbar-right',
      placement: 'bottom',
      before: () => setTab('Scorecard'),
      title: 'Taking the answer out',
      body: (
        <>
          <p>
            <b>Report</b> opens a plain-language document for people who will never open
            this tool: what the option does, who carries it, and which facilities would stop
            being fully supplied — by name. It prints to PDF from your browser.
          </p>
          <p>
            <b>Spreadsheet</b> is the same result as numbers, for anyone who wants to check
            the work. Both appear once the selected scenario has been run.
          </p>
        </>
      ),
    },

    {
      id: 'finish',
      placement: 'centre',
      before: () => {
        setTab('Scorecard')
        setColourBy('fill')
      },
      title: 'That is the whole tool',
      body: (
        <>
          <p>
            A reasonable first hour: run the baseline, run the cheapest option beside it,
            open <b>Equity</b> to see who paid for the difference, then hold February and
            watch both fall apart.
          </p>
          <p className="tour-aside">
            One caution worth carrying: unless your own data is loaded, the demand, costs
            and timetables here are illustrative placeholders. The banner at the top says
            which parts are real.
          </p>
        </>
      ),
    },
  ]
}
