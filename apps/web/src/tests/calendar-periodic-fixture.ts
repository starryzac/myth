/** TOOL_ONLY synthetic native-shaped source. Not an actual bank/PG/human result. */
import fixture from './calendar-periodic-fixture.json';
import type { CalendarPeriodicReport } from '../api/calendar-periodic-suggestions';
export const calendarPeriodicFixture = (): CalendarPeriodicReport => structuredClone(fixture) as unknown as CalendarPeriodicReport;
