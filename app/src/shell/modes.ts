// The modes on the composer menu, and what the app needs to know about each. Reader and Explorer only; the server
// runs them on their own pipelines (server/explainer/modes/). Old pages made with other modes still open and show
// their mode's name (LEGACY_MODE_NAMES): delete that map with those modes.

import type { Quality } from "../../../shared/spec";

export interface ModeInfo {
  id: Quality;
  /** The menu name. */
  name: string;
  /** The menu's one-line description. */
  description: string;
  /** How long a new page usually takes (dev runs, Nov 2026: Explorer median 97 s, 50-193 s; Reader median 27 s). */
  howLong: string;
  /** The Thinking option applies (Haiku thinks before planning and building each playground). */
  thinking: boolean;
  /** Review rounds apply (hidden in v1, MORE_OPTIONS). */
  reviewRounds: boolean;
}

export const MODES: readonly ModeInfo[] = [
  { id: "lite-plus", name: "Reader", description: "A short page to read, with charts at fixed values. The fastest.",
    howLong: "under a minute", thinking: false, reviewRounds: false },
  { id: "playground", name: "Explorer", description: "A deep page where up to 4 sections get a hands-on playground: drag, click, play and watch it react. Tested and reviewed.",
    howLong: "1–3 minutes", thinking: true, reviewRounds: true },
];

/** The menu's choice the first time (and when a remembered choice is no longer on the menu). */
export const DEFAULT_MODE: Quality = "playground";

export const modeInfo = (q: string | undefined): ModeInfo | undefined => MODES.find((m) => m.id === q);

/** Names of modes no longer on the menu, for old pages and their follow-ups. */
const LEGACY_MODE_NAMES: Record<string, string> = {
  "quality-plus": "Interactive", "quality-deep": "Interactive", "freeform-plus": "Creative", smart: "Smart", "scene-plus": "Lab",
  "code-lab": "Code Lab", "code-lab-2": "Code Lab 2.0", "code-lab-3": "Code Lab 3.0", "code-lab-4": "Code Lab 4.0", "code-lab-5": "Code Lab 5.0",
  "code-lab-6": "Code Lab 6.0", "code-lab-7": "Code Lab 7.0", "playground-2": "Explorer 2.0",
  quality: "Interactive", "quality-2": "Interactive", "quality-fixed": "Interactive", custom: "Interactive", lab: "Interactive",
  economy: "Interactive", cheap: "Interactive", "compact-plus": "Interactive", freeform: "Creative",
};
const LEGACY_HOW_LONG: Record<string, string> = { "playground-2": "1–3 minutes" };

/** A mode's name for display: a menu mode, an old one, or undefined. */
export const modeName = (q: string | undefined): string | undefined => (q ? modeInfo(q)?.name ?? LEGACY_MODE_NAMES[q] : undefined);

/** How long a new page in this mode usually takes, or undefined. */
export const howLong = (q: string | undefined): string | undefined => (q ? modeInfo(q)?.howLong ?? LEGACY_HOW_LONG[q] : undefined);
