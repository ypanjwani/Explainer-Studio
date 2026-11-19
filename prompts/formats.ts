// Structured-output formats and tool definitions the server sends to Claude.
// Like prompts.ts, this is source only: `npm run export:server` writes the
// built JSON into server/explainer/generated/, which the Flask server reads.
// Keep key order stable: tools and formats are part of the cached request.

import { zodOutputFormat } from "@anthropic-ai/sdk/helpers/zod";
import { z } from "zod";
import { Design, PLAN_OPS } from "../shared/spec";

const StepsOut = z.object({ examples: z.array(z.object({ key: z.string(), steps: z.array(z.string()) })) });
/** json_schema output formats (pipeline: design, worked-example steps, briefs to text). */
export const FORMATS = {
  design: zodOutputFormat(Design).schema,
  steps: zodOutputFormat(StepsOut).schema,
  /** Follow-ups v3 (UNDERSTAND_SYSTEM, server/explainer/understand.py): sections + intents and structural steps, a question, a refusal, an answer. */
  followupUnderstand: (() => {
    const step = {
      type: "object",
      properties: {
        op: { type: "string", enum: [...PLAN_OPS] },
        section: { type: "string" },
        other: { type: "string" },
        after: { type: "string" },
        level: { type: "string", enum: ["", "beginner", "intermediate", "advanced"] },
        what: { type: "string" },
        quote: { type: "string" },
      },
      required: ["op", "section", "other", "after", "level", "what", "quote"],
      additionalProperties: false,
    };
    const intent = {
      type: "object",
      properties: { section: { type: "string" }, intent: { type: "string" }, quote: { type: "string" } },
      required: ["section", "intent", "quote"],
      additionalProperties: false,
    };
    const option = {
      type: "object",
      properties: { label: { type: "string" }, sections: { type: "array", items: intent }, steps: { type: "array", items: step } },
      required: ["label", "sections", "steps"],
      additionalProperties: false,
    };
    return {
      type: "object",
      properties: {
        kind: { type: "string", enum: ["change", "ask", "cannot", "answer", "new_page", "revert"] },
        sections: { type: "array", items: intent },
        steps: { type: "array", items: step },
        question: {
          type: "object",
          properties: { text: { type: "string" }, options: { type: "array", items: option } },
          required: ["text", "options"],
          additionalProperties: false,
        },
        reason: { type: "string" },
        alternative: option,
      },
      required: ["kind", "sections", "steps", "question", "reason", "alternative"],
      additionalProperties: false,
    };
  })(),
  /** Follow-ups v4 (FOLLOWUP_TALK_SYSTEM, server/explainer/modes/followup/v4/talk.py): change (a brief), ask, answer, cannot, new page, revert. */
  followupTalk: {
    type: "object",
    properties: {
      kind: { type: "string", enum: ["change", "ask", "answer", "cannot", "new_page", "revert"] },
      brief: { type: "string" },
      sections: { type: "array", items: { type: "string" } },
      reply: { type: "string" },
      question: {
        type: "object",
        properties: {
          text: { type: "string" },
          options: { type: "array", items: { type: "object", properties: { label: { type: "string" } }, required: ["label"], additionalProperties: false } },
        },
        required: ["text", "options"],
        additionalProperties: false,
      },
      alternative: { type: "object", properties: { label: { type: "string" } }, required: ["label"], additionalProperties: false },
    },
    required: ["kind", "brief", "sections", "reply", "question", "alternative"],
    additionalProperties: false,
  },
  /** Follow-ups v3 (INDEX_SYSTEM, server/explainer/page_index.py): the page map's AI part. */
  pageIndex: {
    type: "object",
    properties: {
      sections: {
        type: "array",
        items: {
          type: "object",
          properties: {
            id: { type: "string" },
            introduces: { type: "array", items: { type: "string" } },
            uses: { type: "array", items: { type: "string" } },
            refersTo: { type: "array", items: { type: "string" } },
            claims: { type: "array", items: { type: "string" } },
            playgroundLevel: { type: "string", enum: ["", "beginner", "intermediate", "advanced"] },
          },
          required: ["id", "introduces", "uses", "refersTo", "claims", "playgroundLevel"],
          additionalProperties: false,
        },
      },
    },
    required: ["sections"],
    additionalProperties: false,
  },
  /** Follow-ups v5 (FOLLOWUP_V5_REVIEW_SYSTEM, v5/review.py): the report tool's input. */
  followupV5Review: {
    type: "object",
    properties: {
      problems: {
        type: "array",
        items: {
          type: "object",
          properties: { section: { type: "string" }, cause: { type: "string" }, what: { type: "string" } },
          required: ["section", "cause", "what"],
          additionalProperties: false,
        },
      },
    },
    required: ["problems"],
    additionalProperties: false,
  },
  /** Follow-ups v5 (MAIN_FILE_SYSTEM, server/explainer/modes/followup/main_file.py): a main file's AI part, per section. */
  mainFileNotes: {
    type: "object",
    properties: {
      sections: {
        type: "array",
        items: {
          type: "object",
          properties: {
            id: { type: "string" },
            introduces: { type: "array", items: { type: "string" } },
            uses: { type: "array", items: { type: "string" } },
            claims: { type: "array", items: { type: "string" } },
          },
          required: ["id", "introduces", "uses", "claims"],
          additionalProperties: false,
        },
      },
    },
    required: ["sections"],
    additionalProperties: false,
  },
  /** Fact check: Haiku's verdict and fixes for flagged claims. */
  repair: {
    type: "object", additionalProperties: false, required: ["results"],
    properties: {
      results: {
        type: "array",
        items: {
          type: "object", additionalProperties: false, required: ["id", "verdict", "edits", "answer"],
          properties: {
            id: { type: "string" },
            verdict: { type: "string", enum: ["correct", "fix"] },
            edits: { type: "array", items: { type: "object", additionalProperties: false, required: ["find", "replace"], properties: { find: { type: "string" }, replace: { type: "string" } } } },
            answer: { type: "integer" },
          },
        },
      },
    },
  },
  /** Clarity check (Quality+, Freeform+): the ideas each stage relies on (CLARITY_TOPICS_SYSTEM). */
  clarityTopics: {
    type: "object", additionalProperties: false, required: ["stages"],
    properties: {
      stages: {
        type: "array",
        items: {
          type: "object", additionalProperties: false, required: ["index", "relies_on"],
          properties: { index: { type: "integer" }, relies_on: { type: "array", items: { type: "string" } } },
        },
      },
    },
  },
  /** Code Lab 7.0's look check: what a reader would see is wrong in the widget's screenshots (CODE_LAB_LOOK_SYSTEM). */
  codeLabLook: {
    type: "object", additionalProperties: false, required: ["problems"],
    properties: { problems: { type: "array", items: { type: "string" } } },
  },
  /** "Add to each section": a deeper explanation and the same explanation in other words, per stage (SECTION_NOTES_SYSTEM). */
  /** "Go deeper" on a formula (FORMULA_DEEPER_SYSTEM; shared/spec.ts FormulaNote). */
  formulaDeeper: {
    type: "object", additionalProperties: false, required: ["idea", "terms", "origin", "shape", "example", "pitfall"],
    properties: {
      idea: { type: "string" },
      terms: {
        type: "array",
        items: {
          type: "object", additionalProperties: false, required: ["symbol", "name", "meaning", "role"],
          properties: { symbol: { type: "string" }, name: { type: "string" }, meaning: { type: "string" }, role: { type: "string" } },
        },
      },
      origin: { type: "array", items: { type: "string" } },
      shape: { type: "string" },
      example: { type: "array", items: { type: "string" } },
      pitfall: { type: "string" },
    },
  },
  sectionNotes: {
    type: "object", additionalProperties: false, required: ["stages"],
    properties: {
      stages: {
        type: "array",
        items: {
          type: "object", additionalProperties: false, required: ["index", "deeper", "another"],
          properties: { index: { type: "integer" }, deeper: { type: "string" }, another: { type: "string" } },
        },
      },
    },
  },
  /** Haiku's inserts and edits for the stages Jev flagged (CLARITY_SYSTEM). */
  clarify: {
    type: "object", additionalProperties: false, required: ["results"],
    properties: {
      results: {
        type: "array",
        items: {
          type: "object", additionalProperties: false, required: ["index", "inserts", "edits"],
          properties: {
            index: { type: "integer" },
            inserts: {
              type: "array",
              items: {
                type: "object", additionalProperties: false, required: ["before", "type", "variant", "title", "md"],
                properties: {
                  before: { type: "integer" },
                  type: { type: "string", enum: ["text", "callout"] },
                  variant: { type: "string", enum: ["info", "key", "analogy"] },
                  title: { type: "string" },
                  md: { type: "string" },
                },
              },
            },
            edits: { type: "array", items: { type: "object", additionalProperties: false, required: ["find", "replace"], properties: { find: { type: "string" }, replace: { type: "string" } } } },
          },
        },
      },
    },
  },
};

/** The planner's "compute" tool: it checks numbers with the page's own math engine. */
export const COMPUTE_TOOL = {
  name: "compute",
  description: "Evaluate formula-language expressions exactly as the explainer page will. For each expression give the variable values to use (for example the slider defaults you chose). Returns each value, or the error.",
  input_schema: {
    type: "object",
    properties: {
      expressions: {
        type: "array",
        items: {
          type: "object",
          properties: {
            expr: { type: "string" },
            vars: { type: "array", items: { type: "object", properties: { name: { type: "string" }, value: { type: "number" } }, required: ["name", "value"] } },
          },
          required: ["expr"],
        },
      },
    },
    required: ["expressions"],
  },
  // No eager_input_streaming: the input is tiny, and buffering lets the API
  // check it, so a model's JSON slip can't reach (and crash) the SDK.
};
